"""Scan des ports en ecoute et correspondance avec les processus et Docker."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Set

import psutil


@dataclass
class PortInfo:
	port: int
	proto: str
	addr: str
	pid: Optional[int]
	process: str


_tcp_cache_ts: float = 0.0
_tcp_cache: Set[int] = set()
_docker_cache_ts: float = 0.0
_docker_cache: List[dict] = []

CACHE_TTL = 1.0


def _conns(kind: str) -> list:
	try:
		return psutil.net_connections(kind=kind)
	except psutil.Error:
		return []


def all_listening() -> List[PortInfo]:
	"""Tous les ports TCP/UDP en ecoute ou lies sur la machine."""
	result: Dict[tuple, PortInfo] = {}
	for kind in ("tcp", "udp"):
		for c in _conns(kind):
			if kind == "tcp" and c.status != "LISTEN":
				continue
			if not c.laddr:
				continue
			key = (c.laddr.port, kind, c.pid)
			if key in result:
				continue
			name = ""
			if c.pid:
				try:
					name = psutil.Process(c.pid).name()
				except psutil.Error:
					name = "?"
			result[key] = PortInfo(
				port=c.laddr.port,
				proto=kind,
				addr=str(c.laddr.ip),
				pid=c.pid,
				process=name,
			)
	return sorted(result.values(), key=lambda p: (p.port, p.proto))


def listening_tcp_ports() -> Set[int]:
	"""Set des ports TCP en ecoute, avec cache court pour les refresh rapides."""
	global _tcp_cache_ts, _tcp_cache
	now = time.time()
	if now - _tcp_cache_ts < CACHE_TTL:
		return _tcp_cache
	_tcp_cache = {
		c.laddr.port
		for c in _conns("tcp")
		if c.status == "LISTEN" and c.laddr
	}
	_tcp_cache_ts = now
	return _tcp_cache


def port_listening(port: int) -> bool:
	return port in listening_tcp_ports()


def ports_for_pids(pids: List[int]) -> List[int]:
	"""Ports en ecoute appartenant a un arbre de PIDs."""
	pidset = set(pids)
	found: Set[int] = set()
	for kind in ("tcp", "udp"):
		for c in _conns(kind):
			if c.pid in pidset and c.laddr:
				if kind == "tcp" and c.status != "LISTEN":
					continue
				found.add(c.laddr.port)
	return sorted(found)


def pids_on_ports(ports: List[int]) -> List[int]:
	"""PIDs ecoutant sur les ports donnes (pour arreter un processus externe)."""
	wanted = set(ports)
	return sorted({
		c.pid
		for c in _conns("tcp")
		if c.status == "LISTEN" and c.laddr and c.laddr.port in wanted and c.pid
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
