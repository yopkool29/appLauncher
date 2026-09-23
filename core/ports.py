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
from typing import Dict, List, Optional, Set, Tuple

import psutil


@dataclass
class PortInfo:
	port: int
	proto: str
	addr: str
	pid: Optional[int]
	process: str


_caches: Dict[str, tuple] = {}  # nom -> (ts, valeur)
_docker_cache_ts: float = 0.0
_docker_cache: List[dict] = []

CACHE_TTL = 1.0
LISTEN_TTL = 4.0  # all_listening : partage onglet Ports + Prefs


def _cached(key: str, ttl: float, fn):
	"""Cache TTL generique : fn() n'est re-evalue que si perime."""
	ts, val = _caches.get(key, (0.0, None))
	if time.time() - ts >= ttl:
		val = fn()
		_caches[key] = (time.time(), val)
	return val


def _net_scan() -> list:
	try:
		return psutil.net_connections(kind="all")
	except psutil.Error:
		return []


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


def _scan_listening() -> List[PortInfo]:
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


def _parse_socket_ports() -> Dict[str, Tuple[int, str]]:
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
	global _docker_cache_ts, _docker_cache
	now = time.time()
	if now - _docker_cache_ts < CACHE_TTL:
		return _docker_cache
	try:
		out = subprocess.run(
			["docker", "ps", "--format", "{{json .}}"],
			capture_output=True,
			text=True,
			timeout=8,
		)
	except (OSError, subprocess.TimeoutExpired):
		return _docker_cache
	if out.returncode != 0:
		_docker_cache = []
		_docker_cache_ts = now
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
	_docker_cache = containers
	_docker_cache_ts = now
	return containers


def _same_dir(a: str, b: str) -> bool:
	if not a or not b:
		return False
	try:
		return os.path.realpath(a) == os.path.realpath(b)
	except OSError:
		return a == b


_svc_cache: Dict[tuple, tuple] = {}
_SVC_TTL = 30.0


def compose_services(workdir: str, cmd: str) -> Set[str]:
	"""Noms de services declares dans les compose files de la commande
	(fait foi meme sans conteneur running)."""
	key = (os.path.realpath(workdir or "."), cmd)
	ts, cached = _svc_cache.get(key, (0.0, set()))
	if time.time() - ts < _SVC_TTL:
		return cached
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
	_svc_cache[key] = (time.time(), services or cached)
	return services or cached


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
