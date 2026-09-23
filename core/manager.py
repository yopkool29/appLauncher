"""Cycle de vie des processus : start/stop/restart, statut, logs."""
from __future__ import annotations

import logging
import os
import shlex
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Dict, List, Optional, Tuple

import psutil

from . import tmux
from .config import (
	App,
	Process,
	default_logs_dir,
	load_pref,
	safe_name,
)
from . import ports as portscan

log = logging.getLogger(__name__)


LOGS_DIR = default_logs_dir()

STATUS_RUNNING = "running"
STATUS_STOPPED = "stopped"
STATUS_EXTERNAL = "external"   # ports declares occupes, mais pas lance par l'outil
STATUS_FINISHED = "finished"   # commande terminee (one-shot ou docker compose up -d)


@dataclass
class ProcStatus:
	state: str = STATUS_STOPPED
	pids: List[int] = field(default_factory=list)
	ports: List[int] = field(default_factory=list)
	uptime: float = 0.0


@dataclass
class _ProcRuntime:
	popen: Optional[subprocess.Popen]
	started_at: float
	log_handle: Optional[IO[bytes]]
	tmux: str = ""
	pane_id: str = ""
	pane_pid: int = 0


class ProcessManager:
	def __init__(self) -> None:
		self._runtimes: Dict[Tuple[str, str], _ProcRuntime] = {}
		# liste ordonnee des apps (mutee en place par l'UI) :
		# sert a resoudre le groupe tmux des apps 'shared'
		self._apps: List[App] = []

	def set_apps(self, apps: List[App]) -> None:
		self._apps = apps

	def log_path(self, app: App, proc: Process) -> Path:
		return LOGS_DIR / safe_name(app.name) / f"{safe_name(proc.name)}.log"

	@staticmethod
	def _rt_alive(rt: _ProcRuntime) -> bool:
		if rt.tmux:
			pane = next(
				(
					p
					for p in tmux.all_panes()
					if p["id"] == rt.pane_id
				),
				None,
			)
			return pane is not None and not pane["dead"]
		return rt.popen is not None and rt.popen.poll() is None

	@staticmethod
	def _adopted_pane(app: App, proc: Process) -> Optional[dict]:
		"""Pane tmux survivant a une instance precedente du launcher
		— cherche dans toutes les sessions a nous (l'ordre des apps,
		donc le nom du groupe, a pu changer entre-temps)."""
		if not proc.tmux:
			return None
		return tmux.find_pane_anywhere(app.name, proc.name)

	def is_tracked(self, app: App, proc: Process) -> bool:
		rt = self._runtimes.get((app.name, proc.name))
		if rt is not None:
			return self._rt_alive(rt)
		pane = self._adopted_pane(app, proc)
		return pane is not None and not pane["dead"]

	def pid_tree(self, app: App, proc: Process) -> List[int]:
		"""PID racine + tous les enfants (recursif) du processus lance."""
		rt = self._runtimes.get((app.name, proc.name))
		pid = 0
		if rt is not None and self._rt_alive(rt):
			pid = rt.pane_pid if rt.tmux else rt.popen.pid if rt.popen else 0
		elif proc.tmux:
			# pane adoptee apres redemarrage du launcher
			pane = self._adopted_pane(app, proc)
			if pane is not None and not pane["dead"]:
				pid = pane["pid"]
				if not pane["pipe"]:
					tmux.arm_pipe(self.log_path(app, proc), pane["id"])
		if not pid:
			return []
		try:
			root = psutil.Process(pid)
			return [root.pid] + [c.pid for c in root.children(recursive=True)]
		except (psutil.Error, AttributeError):
			return []

	@staticmethod
	def _launch_error(log_path: Path, msg: str) -> Tuple[bool, str]:
		log.warning(msg)
		try:
			with open(log_path, "ab") as f:
				stamp = time.strftime("%Y-%m-%d %H:%M:%S")
				f.write(f"!!! [{stamp}] {msg}\n".encode("utf-8", "replace"))
		except OSError:
			pass
		return False, msg

	def _start_tmux(
		self, app: App, proc: Process, key, log_path: Path, cmd: str
	) -> Tuple[bool, str]:
		session = tmux.session_name(app, self._apps)
		try:
			pane_id, pane_pid = tmux.spawn(app, proc, cmd, log_path, session)
		except Exception as exc:
			return self._launch_error(
				log_path, f"{proc.name} : launch failed ({exc})"
			)
		self._runtimes[key] = _ProcRuntime(
			popen=None,
			started_at=time.time(),
			log_handle=None,
			tmux=session,
			pane_id=pane_id,
			pane_pid=pane_pid,
		)
		msg = f"{proc.name} started (tmux {session} pane {pane_id})"
		log.info(msg)
		return True, msg

	def start(self, app: App, proc: Process) -> Tuple[bool, str]:
		key = (app.name, proc.name)
		if self.is_tracked(app, proc):
			return False, f"{proc.name}: already running"
		log_path = self.log_path(app, proc)
		try:
			log_path.parent.mkdir(parents=True, exist_ok=True)
		except OSError as exc:
			return self._launch_error(
				log_path, f"{proc.name}: log dir ({exc})"
			)
		# wrapper global (pref "cmd_wrapper") : {cmd} = placeholder,
		# sinon simple prefixe (ex. chargement nvm avant npm)
		cmd = proc.cmd
		wrapper = load_pref("cmd_wrapper", "").strip()
		if wrapper:
			cmd = (
				wrapper.replace("{cmd}", proc.cmd)
				if "{cmd}" in wrapper
				else f"{wrapper} {proc.cmd}"
			)
		if proc.workdir:
			# cd dans la commande : un workdir invalide apparait
			# dans le log/pane au lieu d'une erreur de lancement
			# (~/$VAR deja expandes a la lecture du yaml)
			cmd = f"cd {shlex.quote(proc.workdir)} && {cmd}"
		if proc.tmux:
			if tmux.TMUX_OK:
				return self._start_tmux(app, proc, key, log_path, cmd)
			log.warning("tmux not found: %s spawned directly", proc.name)
		try:
			handle = open(log_path, "ab")
			try:
				popen = subprocess.Popen(
					cmd,
					shell=True,
					stdin=subprocess.DEVNULL,
					stdout=handle,
					stderr=subprocess.STDOUT,
					start_new_session=True,
				)
			except Exception:
				handle.close()
				raise
		except Exception as exc:
			msg = f"{proc.name} : launch failed ({exc})"
			return self._launch_error(log_path, msg)
		self._runtimes[key] = _ProcRuntime(
			popen=popen,
			started_at=time.time(),
			log_handle=handle,
		)
		msg = f"{proc.name} started (pid {popen.pid})"
		log.info(msg)
		return True, msg

	def stop(self, app: App, proc: Process) -> Tuple[bool, str]:
		key = (app.name, proc.name)
		rt = self._runtimes.get(key)
		log.info(f"stop requested: {app.name}/{proc.name}")
		msg = ""

		if proc.stop:
			try:
				log.info(f"{proc.name}: stop cmd: {proc.stop}")
				res = subprocess.run(
					proc.stop,
					shell=True,
					cwd=proc.workdir or None,
					stdout=subprocess.DEVNULL,
					stderr=subprocess.DEVNULL,
					timeout=60,
				)
				msg = f"{proc.name}: stop command (rc={res.returncode})"
			except subprocess.TimeoutExpired:
				msg = f"{proc.name}: stop command timed out"
			except OSError as exc:
				msg = f"{proc.name}: stop command failed ({exc})"
		elif proc.is_docker:
			names = [
				c["name"]
				for c in portscan.containers_for_proc(
					proc.name, proc.workdir or "", proc.cmd
				)
			]
			if names:
				try:
					subprocess.run(
						["docker", "stop", *names],
						stdout=subprocess.DEVNULL,
						stderr=subprocess.DEVNULL,
						timeout=90,
					)
					msg = f"{proc.name}: {len(names)} container(s) stopped"
				except subprocess.TimeoutExpired:
					msg = f"{proc.name}: docker stop timed out"

		if proc.tmux:
			# pane tracke, ou pane survivant d'une instance precedente
			pane_id = rt.pane_id if rt is not None and rt.tmux else ""
			pid = rt.pane_pid if rt is not None and rt.tmux else 0
			if not pane_id:
				pane = self._adopted_pane(app, proc)
				if pane is not None:
					pane_id, pid = pane["id"], pane["pid"]
			if pane_id:
				tmux.kill_pane(pane_id)
				msg = msg or f"{proc.name} stopped"
			if pid:
				self._kill_tree(pid)
			if rt is not None and rt.tmux and tmux.session_exists(rt.tmux):
				tmux.retile(
					rt.tmux, tmux.window_key(app, proc), app.tmux_layout
				)
		# independent du bloc tmux : un proc declare tmux lance en
		# fallback Popen (tmux absent) doit quand meme etre tue
		if rt is not None and rt.popen is not None and rt.popen.poll() is None:
			self._kill_tree(rt.popen.pid)
			msg = msg or f"{proc.name} stopped"

		if (
			rt is None and not proc.tmux and not proc.is_docker
			and proc.ports
		):
			# proc externe/orphelin (autre instance, process manuel) :
			# libere aussi les ports occupes, sinon le stop ne fait rien
			self.stop_external(app, proc)

		if rt is not None:
			if rt.log_handle is not None:
				try:
					rt.log_handle.close()
				except OSError:
					pass
			self._runtimes.pop(key, None)
		msg = msg or f"{proc.name}: nothing to stop"
		log.info(msg)
		return True, msg

	def restart(self, app: App, proc: Process) -> Tuple[bool, str]:
		self.stop(app, proc)
		return self.start(app, proc)

	def stop_external(self, app: App, proc: Process) -> Tuple[bool, str]:
		"""Tue les processus externes ecoutant sur les ports declares."""
		pids = portscan.pids_on_ports(proc.ports)
		log.info(
			f"stop_external {app.name}/{proc.name}: "
			f"ports {proc.ports} -> pids {pids}"
		)
		errors = []
		for pid in pids:
			try:
				os.kill(pid, signal.SIGTERM)
			except (ProcessLookupError, PermissionError) as exc:
				errors.append(f"pid {pid} : {exc}")
		if errors:
			msg = f"{proc.name} : " + "; ".join(errors)
			log.warning(msg)
			return False, msg
		msg = f"{proc.name}: {len(pids)} external process(es) killed"
		log.info(msg)
		return True, msg

	def _each(self, app: App, fn, delay: float = 0.0) -> None:
		for proc in app.processes:
			try:
				fn(app, proc)
			except Exception as exc:
				log.warning(
					"%s/%s: %s error (%s)",
					app.name, proc.name, fn.__name__, exc,
				)
			time.sleep(delay)

	def stop_app(self, app: App) -> None:
		self._each(app, self.stop)

	def start_app(self, app: App) -> None:
		self._each(app, self.start, 0.3)

	def restart_app(self, app: App) -> None:
		self.stop_app(app)
		self.start_app(app)

	def start_all(self, apps: List[App]) -> None:
		for app in apps:
			if not app.exclude_all:
				self.start_app(app)

	def stop_all(self, apps: List[App]) -> None:
		for app in apps:
			if not app.exclude_all:
				self.stop_app(app)

	def status(self, app: App, proc: Process) -> ProcStatus:
		rt = self._runtimes.get((app.name, proc.name))
		tree = self.pid_tree(app, proc)
		detected: List[int] = portscan.ports_for_pids(tree) if tree else []
		uptime = 0.0

		if tree:
			state = STATUS_RUNNING
			uptime = time.time() - rt.started_at if rt else 0.0
		elif proc.is_docker:
			containers = portscan.containers_for_proc(
				proc.name, proc.workdir or "", proc.cmd
			)
			detected = [p for c in containers for p in c["host_ports"]]
			state = (
				STATUS_RUNNING if containers
				else STATUS_FINISHED if rt is not None
				else STATUS_STOPPED
			)
		else:
			# le cas RUNNING tmux est deja couvert par pid_tree ci-dessus
			pane = self._adopted_pane(app, proc)
			live = [p for p in proc.ports if portscan.port_listening(p)]
			if pane is not None and not pane["dead"]:
				state = STATUS_RUNNING
			elif pane is not None or (proc.tmux and rt is not None):
				state = STATUS_FINISHED
			elif live:
				state, detected = STATUS_EXTERNAL, live
			elif rt is not None:
				state = STATUS_FINISHED
			else:
				state = STATUS_STOPPED

		for p in proc.ports:
			if p not in detected and portscan.port_listening(p):
				detected.append(p)
		return ProcStatus(
			state=state,
			pids=tree,
			ports=sorted(set(detected)),
			uptime=uptime,
		)

	@staticmethod
	def _kill_tree(pid: int, timeout: float = 4.0) -> None:
		try:
			pgid = os.getpgid(pid)
			os.killpg(pgid, signal.SIGTERM)
		except (ProcessLookupError, PermissionError):
			return
		deadline = time.time() + timeout
		while time.time() < deadline:
			try:
				os.killpg(pgid, 0)
			except (ProcessLookupError, PermissionError):
				return
			time.sleep(0.1)
		try:
			log.info("pgid %s: SIGKILL", pgid)
			os.killpg(pgid, signal.SIGKILL)
		except (ProcessLookupError, PermissionError):
			pass
