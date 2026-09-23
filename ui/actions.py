"""Actions sur les processus : start/stop/restart, toggle, navigateur."""
# mypy: disable-error-code="attr-defined,has-type"
from __future__ import annotations

import shutil
import subprocess
import threading
import time
import tkinter as tk
from typing import Callable, List, Optional, Set, Tuple

from core import ports as portscan
from core import tmux
from core.browser import app_urls, browsers, open_url, port_url
from core.config import App, Process
from ui.dialogs import L, confirm
from core.manager import (
	STATUS_EXTERNAL,
	STATUS_RUNNING,
	ProcStatus,
)

_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


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
			for i, proc in enumerate(procs):
				ok, msg = self.manager.start(app, proc)
				if not ok:
					self._queue.put(("error", msg))
				elif proc.tmux and attach is None and app.tmux_attach:
					# session tmux detachee : ouvrir un terminal dessus
					attach = (app, proc)
				if i < len(procs) - 1:
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
		elif ch == "r":
			self._restart_selection()

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
			self._open_port(app, proc, proc.ports[0])
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
		"""App : reorder interdit tant qu'un proc tourne (l'ordre
		pilote les groupes tmux — le changer a chaud donnerait des
		sessions hybrides). Proc : verrou limite a son app."""
		t = self._resolve(self.tree.focus()) or self._selection()
		if t and t[0] != "app":
			app = t[1]
			locked = any(
				self._state_of(app, p)
				in (STATUS_RUNNING, STATUS_EXTERNAL)
				for p in app.processes
			)
		else:
			locked = self._any_running()
		state = tk.DISABLED if locked else tk.NORMAL
		self._up_btn.config(state=state)
		self._down_btn.config(state=state)

	def _refresh_toggle_btn(self) -> None:
		# bouton avec icone couleur : pas de glyphe dans le texte
		has_img = bool(self._toggle_btn.cget("image"))

		def lbl(running: bool) -> str:
			if has_img:
				return " Stop" if running else " Start"
			return "⏹ Stop" if running else "▶ Start"

		target = self._sel_procs()
		if not target:
			t = self._selection()
			if t and t[0] == "app":
				self._toggle_btn.config(
					text=lbl(self._app_running(t[1])),
					state=tk.NORMAL,
				)
				return
			self._toggle_btn.config(
				text=lbl(False), state=tk.DISABLED
			)
			return
		app, procs = target
		running = all(
			self._state_of(app, p) in (STATUS_RUNNING, STATUS_EXTERNAL)
			for p in procs
		)
		self._toggle_btn.config(
			text=lbl(running),
			state=tk.NORMAL,
		)

	# ---------------- Browser ----------------

	def _browser_for(self, app: App) -> str:
		"""Navigateur de l'app si override, sinon le global toolbar."""
		return app.browser or self._browser_var.get()

	def _launch_proc_browser(self) -> None:
		target = self._selection()
		if not target:
			self._status_lbl.config(text="select an app or a process")
			return
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
			url = port_url(port)
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
				url, self._browser_for(app), proc.browser_mode == "tab",
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
			self._open_in_browser(url, self._browser_for(app), tab)

	def _open_port(self, app: App, proc: Process, port: int) -> None:
		self._open_in_browser(
			port_url(port),
			self._browser_for(app),
			proc.browser_mode == "tab",
		)

	def _open_in_browser(
		self, url: str, browser: str, same_window: bool = False
	) -> None:
		argv = browsers().get(browser)
		if not argv:
			self._queue.put(("error", f"browser not found: {browser}"))
			return
		open_url(url, argv, same_window)
