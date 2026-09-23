"""Onglet Logs : sous-onglets par processus (fichier / docker)."""
# mypy: disable-error-code="attr-defined,has-type"
from __future__ import annotations

import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk
from typing import Optional

from core import ports as portscan
from core.config import App, Process
from core.logging_helpers import tail_file
from ui.ansi_text import write_ansi

LOG_TAIL_BYTES = 65536


class LogsMixin(tk.Tk):
	_logs_app: Optional[App]

	def _build_log_tabs(self, app: App) -> None:
		"""(Re)cree un sous-onglet par processus de l'app."""
		for tab in self._logs_nb.tabs():
			self._logs_nb.forget(tab)
		self._log_tabs = {}
		self._logs_app = app
		for proc in app.processes:
			frame = ttk.Frame(self._logs_nb)
			text = tk.Text(frame, wrap=tk.NONE, state=tk.DISABLED)
			ys = ttk.Scrollbar(
				frame, orient=tk.VERTICAL, command=text.yview
			)
			xs = ttk.Scrollbar(
				frame, orient=tk.HORIZONTAL, command=text.xview
			)
			text.configure(yscrollcommand=ys.set, xscrollcommand=xs.set)
			text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
			ys.pack(side=tk.RIGHT, fill=tk.Y)
			xs.pack(side=tk.BOTTOM, fill=tk.X)
			self._logs_nb.add(frame, text=proc.name)
			self._log_tabs[proc.name] = {
				"frame": frame, "text": text, "proc": proc, "size": -1,
			}
			self._style_log_widget(text)
		# contenu charge a la selection (_on_log_tab/_reload_logs) : pas
		# de docker logs pour les onglets jamais affiches

	def _cur_log_tab(self) -> Optional[dict]:
		"""Sous-onglet de log actuellement visible."""
		sel = self._logs_nb.select()
		for tab in self._log_tabs.values():
			if str(tab["frame"]) == sel:
				return tab
		return None

	def _on_log_tab(self) -> None:
		tab = self._cur_log_tab()
		if tab and self._logs_app is not None:
			self._logs_target.config(
				text=f"{self._logs_app.label} › {tab['proc'].name}"
			)
			self._load_tab(tab)

	def _show_logs(
		self, app: App, proc: Optional[Process] = None, focus: bool = True
	) -> None:
		"""Sous-onglets des procs de l'app ; selectionne celui de proc."""
		if self._logs_app is not app:
			self._build_log_tabs(app)
		target = proc or (app.processes[0] if app.processes else None)
		if target is not None:
			tab = self._log_tabs.get(target.name)
			if tab:
				self._logs_nb.select(tab["frame"])
		self._logs_target.config(
			text=app.label
			if proc is None
			else f"{app.label} › {proc.name}"
		)
		if focus:
			self.notebook.select(self._logs_frame)
		self._reload_logs()

	def _load_tab(self, tab: dict) -> None:
		"""(Re)charge le contenu d'un sous-onglet donne."""
		app = self._logs_app
		if app is None:
			return
		proc: Process = tab["proc"]
		tab["ts"] = time.time()
		if proc.is_docker:
			name = proc.name

			def fetch() -> None:
				names = [
					c["name"]
					for c in portscan.containers_for_proc(
						proc.name, proc.workdir or "", proc.cmd
					)
				]
				parts = []
				for cname in names:
					try:
						out = subprocess.run(
							["docker", "logs", "--tail", "150", cname],
							capture_output=True, text=True, timeout=15,
						)
						parts.append(
							f"===== {cname} =====\n{out.stdout}{out.stderr}"
						)
					except (OSError, subprocess.TimeoutExpired) as exc:
						parts.append(f"===== {cname} =====\n(error: {exc})")
				text = "\n".join(parts) if parts else "(no running container)"
				self._queue.put(("logtext", (name, text)))

			threading.Thread(target=fetch, daemon=True).start()
			return
		path = self.manager.log_path(app, proc)
		res = tail_file(path, LOG_TAIL_BYTES)
		if res is None:
			text = "(no log yet)"
		else:
			text, tab["size"] = res
		self._set_log_text(tab, text)

	def _reload_logs(self) -> None:
		tab = self._cur_log_tab()
		if tab is not None:
			self._load_tab(tab)

	def _maybe_refresh_logs(self) -> None:
		# recharge uniquement le sous-onglet visible ET seulement si
		# l'onglet principal Logs est affiche (docker logs est couteux)
		if self.notebook.select() != str(self._logs_frame):
			return
		tab = self._cur_log_tab()
		if tab is None or self._logs_app is None:
			return
		proc: Process = tab["proc"]
		if proc.is_docker:
			# docker logs = subprocess couteux : 4s minimum entre reloads
			if time.time() - tab.get("ts", 0.0) >= 4.0:
				self._load_tab(tab)
			return
		path = self.manager.log_path(self._logs_app, proc)
		if path.exists() and path.stat().st_size != tab["size"]:
			self._load_tab(tab)

	def _set_log_text(self, tab: dict, text: str) -> None:
		w = tab["text"]
		w.configure(state=tk.NORMAL)
		w.delete("1.0", tk.END)
		write_ansi(w, text)
		w.see(tk.END)
		w.configure(state=tk.DISABLED)

	def _clear_tab(self, tab: dict) -> None:
		"""Vide le log d'un sous-onglet : fichier tronque + affichage
		(docker : l'affichage se re-remplit au prochain refresh)."""
		proc: Process = tab["proc"]
		path = self.manager.log_path(self._logs_app, proc)
		try:
			if path.exists():
				path.write_bytes(b"")
		except OSError as exc:
			self._queue.put(("error", f"clear logs: {exc}"))
			return
		tab["size"] = 0
		self._set_log_text(tab, "")

	def _select_all_log(self) -> None:
		"""Selectionne tout le texte du sous-onglet visible."""
		tab = self._cur_log_tab()
		if tab is None:
			return
		w = tab["text"]
		w.tag_add("sel", "1.0", "end-1c")
		w.mark_set("insert", "1.0")
		w.focus_set()

	def _copy_log(self) -> None:
		"""Copie la selection s'il y en a une, sinon tout le log."""
		tab = self._cur_log_tab()
		if tab is None:
			return
		w = tab["text"]
		try:
			text = w.get("sel.first", "sel.last")
		except tk.TclError:
			text = w.get("1.0", "end-1c")
		if text:
			self.clipboard_clear()
			self.clipboard_append(text)

	def _clear_logs(self) -> None:
		"""Vide le log du sous-onglet visible."""
		tab = self._cur_log_tab()
		if tab is not None:
			self._clear_tab(tab)

	def _clear_all_logs(self) -> None:
		"""Vide les logs de tous les procs de l'app affichee."""
		if self._logs_app is None:
			return
		for tab in self._log_tabs.values():
			self._clear_tab(tab)
