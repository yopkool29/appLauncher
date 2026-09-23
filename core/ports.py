"""Scan des ports en ecoute et correspondance avec les processus et Docker."""
from __future__ import annotations

import json
import os
import re
import shlex
import socket
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

import psutil


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


def compose_services(workdir: str, cmd: str) -> Set[str]:
	"""Noms de services declares dans les compose files de la commande
	(fait foi meme sans conteneur running)."""
	key = ("svc", os.path.realpath(workdir or "."), cmd)
	return _cached(
		key, _SVC_TTL, lambda old: _scan_services(workdir, cmd, old)
	)


def _scan_services(workdir: str, cmd: str, old: Optional[Set[str]]) -> Set[str]:
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
