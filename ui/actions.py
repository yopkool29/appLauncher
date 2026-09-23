"""Actions sur les processus : start/stop/restart, toggle, navigateur."""
# mypy: disable-error-code="attr-defined,has-type"
from __future__ import annotations

import configparser
import logging
import re
import shlex
import shutil
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Callable, Dict, List, Optional, Set, Tuple

from core import ports as portscan
from core import tmux
from core.config import App, Process
from ui.dialogs import L, confirm
from core.manager import (
	STATUS_EXTERNAL,
	STATUS_RUNNING,
	ProcStatus,
)

log = logging.getLogger(__name__)

def _detect_browsers() -> Dict[str, List[str]]:
	"""Navigateurs installes : label -> argv, detectes via les fichiers
	.desktop ayant 'WebBrowser' dans Categories (paquets, flatpak, snap)."""
	dirs = (
		Path.home() / ".local/share/applications",
		Path("/usr/local/share/applications"),
		Path("/usr/share/applications"),
		Path("/var/lib/snapd/desktop/applications"),
		Path("/var/lib/flatpak/exports/share/applications"),
		Path.home() / ".local/share/flatpak/exports/share/applications",
	)
	browsers: Dict[str, List[str]] = {}
	for d in dirs:
		for f in sorted(d.glob("*.desktop")) if d.is_dir() else ():
			try:
				p = configparser.ConfigParser(
					interpolation=None, strict=False
				)
				# les cles .desktop sont sensibles a la casse
				p.optionxform = str  # type: ignore[assignment,method-assign]
				p.read(f, encoding="utf-8")
				e = p["Desktop Entry"]
				if (
					e.get("NoDisplay", "false").lower() == "true"
					or e.get("Hidden", "false").lower() == "true"
					or "webbrowser"
					not in e.get("Categories", "").lower().split(";")
				):
					continue
				# strip les field codes freedesktop (%u, %U, %f...)
				argv = shlex.split(
					re.sub(
						r"%[a-zA-Z]", "", e.get("Exec", "")
					).replace("%%", "%")
				)
				if e.get("Name") and argv:
					browsers.setdefault(e["Name"], argv)
			except (OSError, configparser.Error, KeyError, ValueError):
				continue
	return browsers


BROWSERS = _detect_browsers()

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def app_urls(app: App) -> List[Tuple[str, bool]]:
	"""URLs ouvertes par 'Open URLs' / Launch : url + launch_port de
	l'app en tete (fenetre), puis chaque port des procs dont le mode
	browser n'est pas 'none' (dans leur propre mode). Dedoublonne."""
	urls: List[Tuple[str, bool]] = []
	seen = set()

	def add(u: str, tab: bool) -> None:
		if u and u not in seen:
			seen.add(u)
			urls.append((u, tab))

	# ports des procs 'none'/'manual' : bloque aussi url/launch_port
	# app-level qui pointeraient dessus — 'manual' ne s'ouvre que via
	# Open :port sur le processus, jamais avec Open URLs
	blocked = {
		p for proc in app.processes
		if proc.browser_mode in ("none", "manual")
		for p in proc.ports
	}

	def port_tab(port: int) -> bool:
		"""Mode d'ouverture d'un port : celui du proc qui le declare
		(une url app-level pointant sur un proc 'tab' s'ouvre en
		onglet, sinon fenetre par defaut)."""
		return any(
			port in proc.ports and proc.browser_mode == "tab"
			for proc in app.processes
		)

	m = re.search(r":(\d+)(?:[/:?#]|$)", app.url)
	url_port = int(m.group(1)) if m else 0
	if app.url and url_port not in blocked:
		add(app.url, port_tab(url_port))
	if app.launch_port and app.launch_port not in blocked:
		add(f"http://localhost:{app.launch_port}",
		    port_tab(app.launch_port))
	for proc in app.processes:
		if proc.browser_mode in ("none", "manual"):
			continue
		for p in proc.ports:
			add(f"http://localhost:{p}", proc.browser_mode == "tab")
	return urls


def _focus_latest_browser_window(argv0: str) -> None:
	"""Focus la fenetre la plus recente du navigateur pour que
	--new-tab / l'URL s'y ouvre. _NET_CLIENT_LIST_STACKING est ordonne
	bas->haut : la derniere fenetre du navigateur est la plus
	recemment active. No-op silencieux sans wmctrl/xprop."""
	try:
		out = subprocess.run(
			["xprop", "-root", "_NET_CLIENT_LIST_STACKING"],
			capture_output=True, text=True, timeout=3,
		).stdout
		# ids en int : xprop '0x2200004' vs wmctrl '0x02200004'
		ids = [int(w, 16) for w in re.findall(r"0x[0-9a-fA-F]+", out)]
		if not ids:
			return
		cls_of = {}
		for line in subprocess.run(
			["wmctrl", "-lx"], capture_output=True, text=True, timeout=3
		).stdout.splitlines():
			parts = line.split(None, 3)
			if len(parts) >= 3:
				try:
					cls_of[int(parts[0], 16)] = parts[2].lower()
				except ValueError:
					continue
		key = Path(argv0).name.lower()
		stem = key.split("-")[0]  # 'google-chrome' -> 'google'
		for wid in reversed(ids):
			cls = cls_of.get(wid, "")
			if key in cls or stem in cls:
				subprocess.run(
					["wmctrl", "-i", "-a", hex(wid)],
					stdout=subprocess.DEVNULL,
					stderr=subprocess.DEVNULL,
					timeout=3,
				)
				return
	except (OSError, subprocess.TimeoutExpired):
		pass


def _fn_name(fn: Callable) -> str:
	"""Nom lisible de l'action (fonction ou functools.partial)."""
	return getattr(fn, "__name__", None) or getattr(
		getattr(fn, "func", None), "__name__", "task"
	) or "task"


class ActionsMixin(tk.Tk):
	_busy_names: List[str]
	_spin_i: int
	_spinning: bool
	_attach_pending: Set[str]

	def _run_action(self, fn: Callable, *args) -> None:
		name = _fn_name(fn)

		def wrap() -> None:
			try:
				fn(*args)
			except Exception as exc:
				self._queue.put(("error", str(exc)))
			finally:
				self._queue.put(("busy-", name))
				self._collect_snapshot()
		self._queue.put(("busy+", name))
		threading.Thread(target=wrap, daemon=True).start()

	def _on_busy(self, name: str, start: bool) -> None:
		"""Met a jour la liste des actions en cours + le spinner."""
		if start:
			self._busy_names.append(name)
		elif name in self._busy_names:
			self._busy_names.remove(name)
		if self._busy_names and not self._spinning:
			self._spinning = True
			self._spin()

	def _spin(self) -> None:
		if not self._busy_names:
			self._spinning = False
			self._activity_lbl.config(text="")
			return
		ch = _SPINNER[self._spin_i % len(_SPINNER)]
		self._spin_i += 1
		label = self._busy_names[-1]
		if len(self._busy_names) > 1:
			label += f" (+{len(self._busy_names) - 1})"
		self._activity_lbl.config(text=f"{ch} {label}…")
		self.after(120, self._spin)

	def _state_of(self, app: App, proc: Process) -> str:
		return self._snapshot.get((app.name, proc.name), ProcStatus()).state

	def _start_procs(
		self, app: App, procs: List[Process]
	) -> None:
		def run() -> None:
			attach: Optional[Tuple[App, Process]] = None
			for proc in procs:
				ok, msg = self.manager.start(app, proc)
				if not ok:
					self._queue.put(("error", msg))
				elif proc.tmux and attach is None and app.tmux_attach:
					# session tmux detachee : ouvrir un terminal dessus
					attach = (app, proc)
				time.sleep(0.3)
			if attach:
				self._queue_attach(attach[0], attach[1])
		self._run_action(run)

	def _run_each(
		self, app: App, procs: List[Process], fn: Callable
	) -> None:
		def run() -> None:
			for proc in procs:
				ok, msg = fn(app, proc)
				if not ok:
					self._queue.put(("error", msg))
		self._run_action(run)

	def _confirm_stop_procs(
		self, app: App, procs: List[Process]
	) -> None:
		external = [
			p for p in procs
			if self._state_of(app, p) == STATUS_EXTERNAL
		]
		if external:
			text = (
				L("ext_one", name=procs[0].name)
				if len(procs) == 1
				else L("ext_many", n=len(external))
			)
			if confirm(
				self, L("ext_title"), text, bitmap="warning"
			) != "yes":
				return
		def run() -> None:
			for proc in procs:
				fn = (
					self.manager.stop_external if proc in external
					else self.manager.stop
				)
				ok, msg = fn(app, proc)
				if not ok:
					self._queue.put(("error", msg))
		self._run_action(run)

	def _queue_attach(self, app: App, proc: Process) -> None:
		"""Demande un terminal sur la session de l'app, une seule fois
		par session : le client tmux met ~1s a attacher — sans ce
		verrou, un start_all ouvrirait des fenetres miroir avant que
		session_attached devienne vrai."""
		session = tmux.session_name(app, self.apps)
		if session in self._attach_pending:
			return
		if tmux.session_attached(session):
			return
		self._attach_pending.add(session)
		self._queue.put(("attach", (app, proc)))
		# grace : si le terminal n'a jamais ouvert, un nouvel attach
		# redevient possible apres ce delai
		self.after(5000, self._attach_pending.discard, session)

	def _maybe_attach(self, app: App) -> None:
		"""Attache un terminal sur la session tmux de l'app si elle a
		des procs tmux et que tmux_attach est actif. Pas de re-attach
		si un client est deja dessus : le nouveau pane s'affiche dans
		le terminal existant."""
		if not app.tmux_attach or not tmux.TMUX_OK:
			return
		proc = next((p for p in app.processes if p.tmux), None)
		if proc:
			self._queue_attach(app, proc)

	def _start_app(self, app: App, restart: bool = False) -> None:
		"""Start/restart toute l'app + attache le terminal tmux."""
		def run() -> None:
			fn = self.manager.restart_app if restart else self.manager.start_app
			fn(app)
			self._maybe_attach(app)
		self._run_action(run)

	def _start_all(self) -> None:
		def run() -> None:
			self.manager.start_all(self.apps)
			for app in self.apps:
				if not app.exclude_all:
					self._maybe_attach(app)
		self._run_action(run)

	def _tmux_attach(self, app: App, proc: Process) -> None:
		session = tmux.session_name(app, self.apps)
		if not tmux.session_exists(session):
			return  # jamais d'attach sur une session exterieure
		tmux_cmd = ["tmux", "attach", "-t", tmux.tgt(session)]
		if app.tmux_shared:
			# affiche l'onglet de l'app dans la session partagee
			tmux_cmd += [
				";", "select-window",
				"-t", f"{tmux.tgt(session)}:{tmux.window_key(app, proc)}",
			]
		for term in ("x-terminal-emulator", "xfce4-terminal",
					 "gnome-terminal", "konsole", "xterm"):
			if shutil.which(term):
				subprocess.Popen(
					[term, "-e", *tmux_cmd],
					stdin=subprocess.DEVNULL,
					stdout=subprocess.DEVNULL,
					stderr=subprocess.DEVNULL,
					start_new_session=True,
				)
				return
		self._status_lbl.config(text="no terminal emulator found")

	def _on_keypress(self, event) -> None:
		# shortcuts: e=edit, s=start/stop toggle (outside text inputs)
		if event.state & 0xC:  # Ctrl/Alt
			return
		w = self.focus_get()
		if w is not None and w.winfo_class() in (
			"Entry", "TEntry", "Text", "TCombobox", "Spinbox", "TSpinbox",
		):
			return
		ch = (event.char or "").lower()
		if ch == "e":
			self._edit_selection()
		elif ch == "s":
			self._toggle_proc()
		elif ch == "u":
			self._open_selection()

	def _open_selection(self) -> None:
		"""Raccourci 'u' : 'Open URLs' pour une app, premier port du
		proc (menu contextuel : pas de demarrage implicite)."""
		t = self._selection()
		if not t:
			return
		if t[0] == "app":
			if app_urls(t[1]):
				self._open_app_urls(t[1])
			else:
				self._status_lbl.config(
					text=f"{t[1].name}: no urls to open"
				)
			return
		_, app, proc = t
		if proc.browser_mode != "none" and proc.ports:
			self._open_port(proc, proc.ports[0])
		else:
			self._status_lbl.config(
				text=f"{proc.name}: no urls to open"
			)

	def _toggle_proc(self) -> None:
		target = self._sel_procs()
		if not target:
			t = self._selection()
			if t and t[0] == "app":
				app = t[1]
				if self._app_running(app):
					self._run_action(self.manager.stop_app, app)
				else:
					self._start_app(app)
			return
		app, procs = target
		stopped = [
			p for p in procs
			if self._state_of(app, p) not in (STATUS_RUNNING, STATUS_EXTERNAL)
		]
		if stopped:
			self._start_procs(app, stopped)
		else:
			self._confirm_stop_procs(app, procs)

	def _app_running(self, app: App) -> bool:
		return all(
			self._state_of(app, p) in (STATUS_RUNNING, STATUS_EXTERNAL)
			for p in app.processes
		)

	def _any_running(self) -> bool:
		"""Au moins un proc est actif (lance par nous ou externe)."""
		return any(
			s.state in (STATUS_RUNNING, STATUS_EXTERNAL)
			for s in self._snapshot.values()
		)

	def _refresh_move_btns(self) -> None:
		"""Reorder interdit tant qu'un proc tourne : l'ordre pilote
		les groupes tmux — le changer a chaud donnerait des sessions
		hybrides (anciens panes dans l'ancien groupe)."""
		state = tk.DISABLED if self._any_running() else tk.NORMAL
		self._up_btn.config(state=state)
		self._down_btn.config(state=state)

	def _refresh_toggle_btn(self) -> None:
		target = self._sel_procs()
		if not target:
			t = self._selection()
			if t and t[0] == "app":
				self._toggle_btn.config(
					text="⏹ Stop" if self._app_running(t[1])
					else "▶ Start",
					state=tk.NORMAL,
				)
				return
			self._toggle_btn.config(text="▶ Start", state=tk.DISABLED)
			return
		app, procs = target
		running = all(
			self._state_of(app, p) in (STATUS_RUNNING, STATUS_EXTERNAL)
			for p in procs
		)
		self._toggle_btn.config(
			text="⏹ Stop" if running else "▶ Start",
			state=tk.NORMAL,
		)

	# ---------------- Browser ----------------

	def _launch_proc_browser(self) -> None:
		target = self._selection()
		if not target:
			self._status_lbl.config(text="select an app or a process")
			return
		browser = self._browser_var.get()
		if target[0] == "app":
			app = target[1]
			urls = app_urls(app)
			if not urls:
				self._status_lbl.config(
					text=f"{app.name}: no urls to open"
				)
				return

			def run_app() -> None:
				self.manager.start_app(app)
				self._maybe_attach(app)
				if app.launch_port:
					self._wait_port(app.launch_port)
				self._open_app_urls(app)

			self._run_action(run_app)
			return
		_, app, proc = target
		if proc.browser_mode == "none":
			self._status_lbl.config(
				text=f"{proc.name}: browser disabled"
			)
			return
		port = proc.ports[0] if proc.ports else app.launch_port
		if port:
			url = f"http://localhost:{port}"
		elif app.url:
			url = app.url
		else:
			self._status_lbl.config(
				text=f"{proc.name}: no port configured"
			)
			return

		def run() -> None:
			if not self.manager.is_tracked(app, proc):
				ok, msg = self.manager.start(app, proc)
				if not ok:
					self._queue.put(("error", msg))
					return
			if port:
				self._wait_port(port)
			self._open_in_browser(
				url, browser, proc.browser_mode == "tab",
			)

		self._run_action(run)

	@staticmethod
	def _wait_port(port: int, timeout: float = 15) -> None:
		deadline = time.time() + timeout
		while time.time() < deadline and not portscan.port_listening(port):
			time.sleep(0.3)

	def _open_app_urls(self, app: App) -> None:
		"""Ouvre toutes les URLs de l'app (menu 'Open URLs', Launch)."""
		for url, tab in app_urls(app):
			self._open_in_browser(url, self._browser_var.get(), tab)

	def _open_port(self, proc: Process, port: int) -> None:
		self._open_in_browser(
			f"http://localhost:{port}",
			self._browser_var.get(),
			proc.browser_mode == "tab",
		)

	def _open_in_browser(
		self, url: str, browser: str, same_window: bool = False
	) -> None:
		argv = BROWSERS.get(browser)
		if not argv:
			self._queue.put(("error", f"browser not found: {browser}"))
			return
		if same_window:
			# focus d'abord la fenetre la plus recente : l'onglet
			# s'ouvre dans la fenetre active. --new-tab est explicite
			# pour Gecko ET Chromium (kNewTab ; les vieux Chromium
			# l'ignorent -> URL nue = tab). Sans fenetre ouverte,
			# le navigateur demarre simplement.
			_focus_latest_browser_window(argv[0])
			args = argv + ["--new-tab", url]
		else:
			args = argv + ["--new-window", url]
		log.info("opening %s -> %s", " ".join(args[:-1]), url)
		subprocess.Popen(
			args,
			stdin=subprocess.DEVNULL,
			stdout=subprocess.DEVNULL,
			stderr=subprocess.DEVNULL,
			start_new_session=True,
		)
