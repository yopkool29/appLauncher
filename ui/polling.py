"""Polling des statuts en arriere-plan et dispatch de la queue Tk."""
# mypy: disable-error-code="attr-defined,has-type"
from __future__ import annotations

import tkinter as tk

import logging
import queue
import threading
import time

from core import ports as portscan

log = logging.getLogger(__name__)

POLL_INTERVAL_S = 2.0


class PollMixin(tk.Tk):
	def _start_poller(self) -> None:
		def loop() -> None:
			while self._alive:
				self._collect_snapshot()
				for _ in range(int(POLL_INTERVAL_S * 10)):
					if not self._alive:
						return
					time.sleep(0.1)
		threading.Thread(target=loop, daemon=True).start()

	def _collect_snapshot(self) -> None:
		apps = list(self.apps)
		try:
			statuses = {
				(app.name, proc.name): self.manager.status(app, proc)
				for app in apps
				for proc in app.processes
			}
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
			self._queue.put(
				("snapshot", (statuses, port_owner, portscan.all_listening()))
			)
		except Exception as exc:
			self._queue.put(("error", f"scan: {exc}"))

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
