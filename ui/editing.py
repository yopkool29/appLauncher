"""CRUD du catalogue : add/edit/delete d'apps et de processus."""
# mypy: disable-error-code="attr-defined,has-type"
from __future__ import annotations

import logging
import tkinter as tk

import threading
from tkinter import messagebox
from typing import List, Optional

from core.config import App, Process, load_config, save_config
from core.manager import STATUS_EXTERNAL, STATUS_RUNNING
from ui.dialogs import AppDialog, L, ProcessDialog, confirm

log = logging.getLogger(__name__)


class EditMixin(tk.Tk):
	def _add_app(self) -> None:
		dialog = AppDialog(self)
		if dialog.result:
			self.apps.append(dialog.result)
			self._save()

	def _add_process(
		self, app: Optional[App] = None
	) -> None:
		if app is None:
			target = self._selection()
			app = target[1] if target else None
		if app is None:
			messagebox.showinfo("Process", "Select an app first.")
			return
		dialog = ProcessDialog(self)
		if dialog.result:
			app.processes.append(dialog.result)
			self._save()

	def _edit_app(self, app: App) -> None:
		dialog = AppDialog(self, app)
		if dialog.result:
			r = dialog.result
			# tout sauf processes (edites a part)
			for f in (
				"name", "alias", "url", "tmux_layout", "launch_port",
				"tmux_attach", "tmux_shared", "exclude_all", "color",
			):
				setattr(app, f, getattr(r, f))
			self._save()

	def _edit_proc(self, app: App, proc: Process) -> None:
		dialog = ProcessDialog(self, proc)
		if dialog.result:
			idx = app.processes.index(proc)
			app.processes[idx] = dialog.result
			self._save()

	def _edit_selection(self) -> None:
		target = self._selection()
		if target and target[0] == "proc":
			_, app, proc = target
			self._edit_proc(app, proc)
		elif target and target[0] == "app":
			self._edit_app(target[1])
		else:
			self._status_lbl.config(
				text="select an app or a process"
			)

	def _del_app(self, app: App) -> None:
		running = len([
			p
			for p in app.processes
			if self._state_of(app, p) in (STATUS_RUNNING, STATUS_EXTERNAL)
		])
		text = L("del_app", name=app.name, count=len(app.processes))
		if running:
			text += L("del_app_up", running=running)
		if confirm(self, L("del_title"), text, bitmap="warning") != "yes":
			return
		self._run_action(self.manager.stop_app, app)
		self.apps.remove(app)
		self._save()

	def _del_procs(
		self, app: App, procs: List[Process]
	) -> None:
		text = (
			L("del_one", name=procs[0].name)
			if len(procs) == 1
			else L("del_many", n=len(procs))
		)
		if confirm(self, L("del_title"), text, bitmap="warning") != "yes":
			return
		self._run_each(app, procs, self.manager.stop)
		for proc in procs:
			app.processes.remove(proc)
		self._save()

	def _move_selection(self, delta: int) -> None:
		"""Deplace l'item selectionne (app ou proc) de +/-1 —
		l'ordre determine aussi celui de Start all et les groupes
		tmux : interdit tant qu'un proc tourne."""
		if self._any_running():
			self._status_lbl.config(
				text="reorder locked while processes are running"
			)
			return
		t = self._resolve(self.tree.focus()) or self._selection()
		if not t:
			return
		if t[0] == "app":
			lst, obj, iid = self.apps, t[1], self._iid_app(t[1])
		else:
			lst, obj = t[1].processes, t[2]
			iid = self._iid_proc(t[1], t[2])
		i, j = lst.index(obj), lst.index(obj) + delta
		if not (0 <= j < len(lst)):
			return
		lst[i], lst[j] = lst[j], lst[i]
		self._save()
		self.tree.see(iid)

	def _save(self) -> None:
		try:
			save_config(self.config_path, self.apps)
		except OSError as exc:
			messagebox.showerror("Save", f"Cannot write config: {exc}")
		self._reload_tree()
		threading.Thread(
			target=self._collect_snapshot, daemon=True
		).start()

	def _refresh_all(self) -> None:
		"""F5 : recharge apps.yaml depuis le disque + refresh statuts."""
		try:
			# mutation en place : manager._apps partage cette liste
			# (resolution des groupes tmux) — un rebind le rendrait stale
			self.apps[:] = load_config(self.config_path)
			log.info(
				f"config reloaded from {self.config_path} "
				f"({len(self.apps)} app(s))"
			)
		except Exception as exc:
			log.warning(f"config reload failed: {exc}")
			messagebox.showerror(
				"Reload", f"Cannot reload config: {exc}"
			)
		self._reload_tree()
		threading.Thread(
			target=self._collect_snapshot, daemon=True
		).start()

	def _delete_selection(self) -> None:
		t = self._selection()
		if not t:
			return
		if t[0] == "app":
			self._del_app(t[1])
		else:
			sel = self._sel_procs()
			if sel:
				self._del_procs(sel[0], sel[1])

	def _restart_selection(self) -> None:
		sel = self._sel_procs()
		if sel:
			self._run_each(sel[0], sel[1], self.manager.restart)
