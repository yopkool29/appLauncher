"""Polling des statuts en arriere-plan et dispatch de la queue Tk."""
# mypy: disable-error-code="attr-defined,has-type"
from __future__ import annotations

import tkinter as tk

import logging
import queue
import threading

from core import ports as portscan
from core.manager import STATUS_EXTERNAL, STATUS_RUNNING

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 2.0
POLL_IDLE_S = 5.0   # aucun proc running/external : poll ralenti
POLL_TRAY_S = 6.0   # fenetre minimisee/tray : encore plus lent


class PollMixin(tk.Tk):
	def _start_poller(self) -> None:
		def loop() -> None:
			last = 0.0
			while self._alive:
				idle = self._collect_snapshot()
				interval = POLL_INTERVAL_S
				if idle:
					interval = POLL_IDLE_S
				if self._minimized:
					interval = max(interval, POLL_TRAY_S)
				if interval != last:
					what = (
						"hidden" if self._minimized
						else "idle" if idle else "active"
					)
					log.info("poll interval %.0fs (%s)", interval, what)
					last = interval
				self._wake.clear()
				self._wake.wait(interval)  # _destroy_now le pose aussi
		threading.Thread(target=loop, daemon=True).start()

	def _on_main_tab(self) -> None:
		# scan all_listening() seulement quand l'onglet Ports est visible
		self._ports_visible = (
			self.notebook.select() == str(self._ports_frame)
		)
		self._wake.set()

	def _collect_snapshot(self) -> bool:
		"""Snapshot statuts -> queue Tk. True si aucun proc actif
		(running/external) : le poller peut alors ralentir."""
		apps = list(self.apps)
		try:
			statuses = {
				(app.name, proc.name): self.manager.status(app, proc)
				for app in apps
				for proc in app.processes
			}
			idle = not any(
				s.state in (STATUS_RUNNING, STATUS_EXTERNAL)
				for s in statuses.values()
			)
			port_owner = {}
			for app in apps:
				for proc in app.processes:
					for p in set(proc.ports) | set(
						statuses[(app.name, proc.name)].ports
					):
						port_owner[p] = app.name
					if proc.is_docker:
						for c in portscan.containers_for_proc(
							proc.name, proc.workdir or "", proc.cmd
						):
							for hp in c["host_ports"]:
								port_owner[hp] = app.name
			ports = (
				portscan.all_listening() if self._ports_visible else None
			)
			self._queue.put(("snapshot", (statuses, port_owner, ports)))
			return idle
		except Exception as exc:
			self._queue.put(("error", f"scan: {exc}"))
			return False

	def _drain_queue(self) -> None:
		try:
			while True:
				kind, payload = self._queue.get_nowait()
				if kind == "quit":
					log.info("quit signal received -> destroy")
					self._destroy_now(idle=True)
					return
				try:
					if kind == "snapshot":
						statuses, port_owner, all_ports = payload
						self._snapshot = statuses
						self._port_owner = port_owner
						self._reload_tree()
						if all_ports is not None:
							self._fill_ports(all_ports)
						self._refresh_statusbar()
						self._refresh_move_btns()
						self._maybe_refresh_logs()
					elif kind.startswith("busy"):
						self._on_busy(payload, kind == "busy+")
					elif kind == "logtext":
						name, text = payload
						tab = self._log_tabs.get(name)
						if tab is not None:
							self._set_log_text(tab, text)
					elif kind == "attach":
						app, proc = payload
						self._tmux_attach(app, proc)
					elif kind == "tray_show":
						self._toggle_window()
					elif kind == "tray_quit":
						self._on_close()
					elif kind == "error":
						self._status_lbl.config(text=payload)
						log.warning(payload)
				except Exception:
					log.exception("drain_queue error")
		except queue.Empty:
			pass
		if self._alive:
			self.after(150, self._drain_queue)
