"""Scan des ports en ecoute et correspondance avec les processus et Docker."""
from __future__ import annotations

import http.client
import json
import os
import re
import shlex
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import psutil
import yaml


@dataclass
class PortInfo:
	port: int
	proto: str
	addr: str
	pid: Optional[int]
	process: str


_caches: Dict[Any, tuple] = {}  # cle -> (ts, valeur)

CACHE_TTL = 1.0
LISTEN_TTL = 4.0  # all_listening : partage onglet Ports + Prefs
_SVC_TTL = 30.0   # compose_services : docker compose config est cher


def _cached(key: Any, ttl: float, fn):
	"""Cache TTL generique : fn(old) n'est re-evalue que si perime.
	'old' = derniere valeur connue — fn peut la renvoyer en cas
	d'echec (resultat d'erreur mis en cache comme un succes)."""
	ts, val = _caches.get(key, (0.0, None))
	if time.time() - ts >= ttl:
		val = fn(val)
		_caches[key] = (time.time(), val)
	return val


def _net_scan(_old=None) -> list:
	try:
		return psutil.net_connections(kind="all")
	except psutil.Error:
		return _old or []


def _conns() -> list:
	"""net_connections('all') mis en cache — hors polling : onglet
	Ports et kill externe uniquement (le poller vit sur /proc/net)."""
	return _cached("conns", CACHE_TTL, _net_scan)


def _proto(c) -> Optional[str]:
	"""'tcp'/'udp' pour les sockets IPv4/IPv6 ; None sinon (unix,
	raw — leur laddr est un chemin, pas un couple ip:port)."""
	if c.family not in (socket.AF_INET, socket.AF_INET6):
		return None
	if c.type == socket.SOCK_STREAM:
		return "tcp"
	if c.type == socket.SOCK_DGRAM:
		return "udp"
	return None


def _proc_name(pid: int) -> str:
	"""/proc/<pid>/comm : un syscall, vs psutil.Process().name()
	qui relit stat+status a chaque appel."""
	try:
		with open(f"/proc/{pid}/comm") as f:
			return f.read().strip()
	except OSError:
		return "?"


def _scan_listening(_old=None) -> List[PortInfo]:
	result: Dict[tuple, PortInfo] = {}
	for c in _conns():
		proto = _proto(c)
		if proto is None or not c.laddr:
			continue
		if proto == "tcp" and c.status != "LISTEN":
			continue
		key = (c.laddr.port, proto, c.pid)
		if key in result:
			continue
		result[key] = PortInfo(
			port=c.laddr.port,
			proto=proto,
			addr=str(c.laddr.ip),
			pid=c.pid,
			process=_proc_name(c.pid) if c.pid else "",
		)
	return sorted(result.values(), key=lambda p: (p.port, p.proto))


def all_listening() -> List[PortInfo]:
	"""Tous les ports TCP/UDP en ecoute ou lies sur la machine."""
	return _cached("listen", LISTEN_TTL, _scan_listening)


_SOCK_RE = re.compile(r"socket:\[(\d+)\]")


def _parse_socket_ports(_old=None) -> Dict[str, Tuple[int, str]]:
	out: Dict[str, Tuple[int, str]] = {}
	for f, proto, listen_only in (
		("/proc/net/tcp", "tcp", True),
		("/proc/net/tcp6", "tcp", True),
		("/proc/net/udp", "udp", False),
		("/proc/net/udp6", "udp", False),
	):
		try:
			with open(f) as fh:
				next(fh)  # header
				for line in fh:
					p = line.split()
					if len(p) <= 9 or (listen_only and p[3] != "0A"):
						continue
					out[p[9]] = (int(p[1].rsplit(":", 1)[1], 16), proto)
		except OSError:
			pass
	return out


def _socket_ports() -> Dict[str, Tuple[int, str]]:
	"""inode -> (port, 'tcp'|'udp') pour TCP LISTEN et UDP lie, lu dans
	/proc/net — sans resolution de PID : net_connections perd ~70ms
	a parcourir /proc/*/fd de tous les processus."""
	return _cached("sock", CACHE_TTL, _parse_socket_ports)


def listening_tcp_ports() -> Set[int]:
	"""Set des ports TCP en ecoute (/proc/net, pas de resolution PID)."""
	return {
		port for port, proto in _socket_ports().values() if proto == "tcp"
	}


def port_listening(port: int) -> bool:
	return port in listening_tcp_ports()


def ports_for_pids(pids: List[int]) -> List[int]:
	"""Ports en ecoute appartenant a un arbre de PIDs — inodes de
	/proc/net matches contre /proc/<pid>/fd : seuls les fds de nos
	processus sont lus, pas ceux de tout le systeme."""
	want = _socket_ports()
	found: Set[int] = set()
	for pid in pids:
		try:
			fds = os.listdir(f"/proc/{pid}/fd")
		except OSError:
			continue
		for fd in fds:
			try:
				link = os.readlink(f"/proc/{pid}/fd/{fd}")
			except OSError:
				continue
			m = _SOCK_RE.match(link)
			if m and m.group(1) in want:
				found.add(want[m.group(1)][0])
	return sorted(found)


def pids_on_ports(ports: List[int]) -> List[int]:
	"""PIDs ecoutant sur les ports donnes (pour arreter un processus externe)."""
	wanted = set(ports)
	return sorted({
		c.pid
		for c in _conns()
		if _proto(c) == "tcp" and c.status == "LISTEN"
		and c.laddr and c.laddr.port in wanted and c.pid
	})


def docker_containers() -> List[dict]:
	"""Conteneurs en cours : name, image, host_ports, workdir (label compose)."""
	return _cached("docker", CACHE_TTL, _scan_docker)


def _scan_docker(old: Optional[List[dict]]) -> List[dict]:
	"""API Engine /containers/json (~5ms sur socket unix, vs ~50ms
	pour le CLI) ; repli sur `docker ps` si l'API est injoignable —
	'old' garde la derniere liste connue si tout echoue."""
	data = _docker_api("/containers/json")
	if data is not None:
		return [
			_api_container(c) for c in data if isinstance(c, dict)
		]
	return _scan_docker_cli(old)


class _UnixHTTPConnection(http.client.HTTPConnection):
	"""HTTP sur socket unix : dockerd n'ecoute pas en TCP par defaut."""

	def __init__(self, sock_path: str, timeout: float = 3.0) -> None:
		super().__init__("localhost", timeout=timeout)
		self._sock_path = sock_path

	def connect(self) -> None:
		sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
		sock.settimeout(self.timeout)
		sock.connect(self._sock_path)
		self.sock = sock


def _docker_context() -> str:
	"""Contexte courant : DOCKER_CONTEXT, sinon currentContext de
	~/.docker/config.json."""
	ctx = os.getenv("DOCKER_CONTEXT", "")
	if ctx:
		return ctx
	try:
		cfg = json.loads(
			Path("~/.docker/config.json").expanduser().read_text()
		)
	except (OSError, json.JSONDecodeError):
		return ""
	return str(cfg.get("currentContext") or "")


def _docker_conn() -> Optional[http.client.HTTPConnection]:
	"""Endpoint resolu comme le CLI : contexte non-'default' ou
	DOCKER_HOST non-http (ssh://, npipe://, https) -> None, le
	caller retombe sur le CLI (endpoint reel inconnu : mieux vaut
	un `docker ps` lent que des conteneurs d'un autre daemon)."""
	ctx = _docker_context()
	if ctx and ctx != "default":
		return None
	host = os.getenv("DOCKER_HOST", "")
	if host.startswith(("tcp://", "http://")):
		return http.client.HTTPConnection(
			host.split("://", 1)[1], timeout=3.0
		)
	if host and not host.startswith("unix://"):
		return None
	return _UnixHTTPConnection(host[7:] or "/var/run/docker.sock")


def _docker_api(path: str) -> Optional[list]:
	"""GET sur l'API Engine ; None si le daemon ne repond pas en
	HTTP local."""
	conn = _docker_conn()
	if conn is None:
		return None
	try:
		conn.request("GET", path)
		resp = conn.getresponse()
		if resp.status != 200:
			return None
		data = json.loads(resp.read())
		return data if isinstance(data, list) else None
	except (OSError, http.client.HTTPException, json.JSONDecodeError):
		return None
	finally:
		conn.close()


def _api_container(c: dict) -> dict:
	"""Entree /containers/json -> format interne : 'Names' porte un
	'/' prefixe ("/web-1") ; 'PublicPort' absent = port expose non
	publie ; 'Labels' arrive en dict (les valeurs avec virgule
	cassaient le parsing de la chaine CLI)."""
	labels = c.get("Labels") or {}
	names = c.get("Names") or []
	return {
		"name": str(names[0]).lstrip("/") if names else "",
		"image": str(c.get("Image", "")),
		"host_ports": [
			int(p["PublicPort"])
			for p in c.get("Ports") or []
			if isinstance(p, dict) and p.get("PublicPort") is not None
		],
		"workdir": labels.get("com.docker.compose.project.working_dir", ""),
		"project": labels.get("com.docker.compose.project", ""),
		"service": labels.get("com.docker.compose.service", ""),
	}


def _scan_docker_cli(old: Optional[List[dict]]) -> List[dict]:
	"""`docker ps` parse ; 'old' garde la derniere liste connue si
	la commande echoue (binaire absent, timeout)."""
	try:
		out = subprocess.run(
			["docker", "ps", "--format", "{{json .}}"],
			capture_output=True,
			text=True,
			timeout=8,
		)
	except (OSError, subprocess.TimeoutExpired):
		return old or []
	if out.returncode != 0:
		return []
	containers: List[dict] = []
	for line in out.stdout.splitlines():
		line = line.strip()
		if not line:
			continue
		try:
			d = json.loads(line)
		except json.JSONDecodeError:
			continue
		labels: Dict[str, str] = {}
		for part in str(d.get("Labels", "")).split(","):
			if "=" in part:
				k, v = part.split("=", 1)
				labels[k.strip()] = v.strip()
		containers.append({
			"name": str(d.get("Names", "")),
			"image": str(d.get("Image", "")),
			"host_ports": [
				int(m.group(1))
				for m in re.finditer(r":(\d+)->", str(d.get("Ports", "")))
			],
			"workdir": labels.get("com.docker.compose.project.working_dir", ""),
			"project": labels.get("com.docker.compose.project", ""),
			"service": labels.get("com.docker.compose.service", ""),
		})
	return containers


def _same_dir(a: str, b: str) -> bool:
	if not a or not b:
		return False
	try:
		return os.path.realpath(a) == os.path.realpath(b)
	except OSError:
		return a == b


_COMPOSE_NAMES = (
	"compose.yaml", "compose.yml",
	"docker-compose.yaml", "docker-compose.yml",
)
_COMPOSE_OVERRIDES = (
	"compose.override.yaml", "compose.override.yml",
	"docker-compose.override.yaml", "docker-compose.override.yml",
)


def _compose_files(workdir: str, cmd: str) -> List[str]:
	"""Fichiers compose de la commande : -f/--file explicites (ils
	desactivent la detection auto), sinon COMPOSE_FILE, sinon le
	fichier par defaut du workdir + les overrides presents —
	comme `docker compose` (un seul fichier de base)."""
	base = workdir or "."
	try:
		args = shlex.split(cmd)
	except ValueError:
		args = []
	files: List[str] = []
	i = 0
	while i < len(args):
		a = args[i]
		if a in ("-f", "--file") and i + 1 < len(args):
			files.append(args[i + 1])
			i += 1
		elif a.startswith(("--file=", "-f=")):
			files.append(a.split("=", 1)[1])
		i += 1
	if not files:
		env = os.getenv("COMPOSE_FILE", "")
		files = [f for f in env.split(os.pathsep) if f]
	if files:
		return [
			os.path.normpath(
				os.path.expandvars(os.path.expanduser(
					f if os.path.isabs(f) else os.path.join(base, f)
				))
			)
			for f in files
		]
	found = next(
		(
			n for n in _COMPOSE_NAMES
			if os.path.isfile(os.path.join(base, n))
		),
		None,
	)
	if found is None:
		return []
	return [os.path.join(base, found)] + [
		os.path.join(base, n)
		for n in _COMPOSE_OVERRIDES
		if os.path.isfile(os.path.join(base, n))
	]


class _ComposeLoader(yaml.SafeLoader):
	"""SafeLoader tolerant aux tags locaux inconnus (!reset,
	!override... de la spec compose) : le tag est ignore, la
	valeur construite normalement — safe_load jetterait sinon
	un ConstructorError et le fichier entier serait saute."""


def _unknown_tag(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> Any:
	if isinstance(node, yaml.MappingNode):
		return loader.construct_mapping(node, deep=True)
	if isinstance(node, yaml.SequenceNode):
		return loader.construct_sequence(node, deep=True)
	if isinstance(node, yaml.ScalarNode):
		return loader.construct_scalar(node)
	return None


_ComposeLoader.add_multi_constructor("!", _unknown_tag)


def _include_paths(inc: Any) -> Any:
	"""'include:' accepte str, liste de str, ou dicts avec 'path'
	(lui-meme str ou liste)."""
	if isinstance(inc, str):
		yield inc
	elif isinstance(inc, dict):
		yield from _include_paths(inc.get("path"))
	elif isinstance(inc, list):
		for item in inc:
			yield from _include_paths(item)


def _services_from_file(path: str, seen: Set[str]) -> Set[str]:
	"""Cles de 'services:' + recursion sur 'include:' (chemins
	relatifs au fichier qui les declare ; un dossier vise son
	fichier compose par defaut)."""
	if path in seen or len(seen) > 16:
		return set()
	seen.add(path)
	data = yaml.load(Path(path).read_text(), Loader=_ComposeLoader)
	if not isinstance(data, dict):
		return set()
	svc = data.get("services")
	services = {str(k) for k in svc} if isinstance(svc, dict) else set()
	directory = os.path.dirname(path)
	for inc in _include_paths(data.get("include")):
		inc = os.path.expandvars(os.path.expanduser(inc))
		p = inc if os.path.isabs(inc) else os.path.join(directory, inc)
		targets = (
			[os.path.join(p, n) for n in _COMPOSE_NAMES]
			if os.path.isdir(p)
			else [p]
		)
		for t in targets:
			if os.path.isfile(t):
				try:
					services |= _services_from_file(
						os.path.normpath(t), seen
					)
				except (OSError, yaml.YAMLError):
					pass
				break
	return services


_svc_cache: Dict[Any, Tuple[Tuple, Set[str]]] = {}


def compose_services(workdir: str, cmd: str) -> Set[str]:
	"""Noms de services declares dans les compose files de la
	commande (fait foi meme sans conteneur running). Parsing YAML
	direct (~1ms vs ~100ms pour `docker compose config`), revalide
	a chaque appel par fingerprint (fichiers + mtimes) : reparse
	uniquement si un fichier a change. Repli CLI quand aucun
	fichier n'est resolu (contexte distant, projet sans compose
	file local)."""
	key = (os.path.realpath(workdir or "."), cmd)
	files = _compose_files(workdir or ".", cmd)
	if not files:
		return _cached(
			("svc-cli", key), _SVC_TTL,
			lambda old: _scan_services_cli(workdir, cmd, old),
		)
	fp = (
		tuple(files),
		tuple(
			os.path.getmtime(f) if os.path.isfile(f) else -1.0
			for f in files
		),
	)
	got = _svc_cache.get(key)
	if got is not None and got[0] == fp:
		return got[1]
	old = got[1] if got is not None else set()
	services: Set[str] = set()
	for f in files:
		try:
			services |= _services_from_file(f, set())
		except (OSError, yaml.YAMLError):
			pass
	services = services or old
	_svc_cache[key] = (fp, services)
	return services


def _scan_services_cli(
	workdir: str, cmd: str, old: Optional[Set[str]]
) -> Set[str]:
	try:
		args = shlex.split(cmd)
	except ValueError:
		args = []
	command = ["docker", "compose"]
	for i, a in enumerate(args):
		if a in ("-f", "--file") and i + 1 < len(args):
			command += ["-f", args[i + 1]]
	command += ["config", "--services"]
	services: Set[str] = set()
	try:
		out = subprocess.run(
			command,
			cwd=workdir or None,
			capture_output=True,
			text=True,
			timeout=10,
		)
		if out.returncode == 0:
			services = set(out.stdout.split())
	except (OSError, subprocess.TimeoutExpired):
		pass
	return services or (old or set())


def containers_for_proc(name: str, workdir: str, cmd: str = "") -> List[dict]:
	"""Conteneurs lies a un processus docker : label compose workdir,
	restreint au label service quand le nom du processus est un service
	connu du projet ('frontend' -> uniquement son conteneur, meme vide
	si le service ne tourne pas) ; un nom hors services garde tous les
	conteneurs du projet ; a defaut de projet, nom du processus comme
	segment du nom de conteneur ('db' match 'proj-db-1' ou 'db')."""
	containers = docker_containers()
	services = compose_services(workdir, cmd) if workdir else set()
	matched = [
		c
		for c in containers
		if c["workdir"] and _same_dir(c["workdir"], workdir)
	]
	if name and name in services:
		return [c for c in matched if c["service"] == name]
	if matched:
		byname = [
			c
			for c in matched
			if name and name in re.split(r"[-_.]+", c["name"])
		]
		return byname if byname else matched
	return [
		c
		for c in containers
		if name and name in re.split(r"[-_.]+", c["name"])
	]
