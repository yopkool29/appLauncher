"""Arbre apps/processus : iids, selection, rafraichissement, ports."""
# mypy: disable-error-code="attr-defined,has-type"
from __future__ import annotations

import json
import time
import tkinter as tk
from typing import Dict, List, Optional, Tuple

from core import ports as portscan
from core import tmux
from core.config import App, Process, save_pref
from core.manager import (
	STATUS_EXTERNAL,
	STATUS_FINISHED,
	STATUS_RUNNING,
	STATUS_STOPPED,
	ProcStatus,
)


STATE_META = {
	STATUS_RUNNING: ("● running", "green"),
	STATUS_EXTERNAL: ("◉ external", "orange"),
	STATUS_FINISHED: ("■ finished", "blue"),
	STATUS_STOPPED: ("○ stopped", "gray"),
}


def _strike(text: str) -> str:
	"""Barre le texte via le combining long stroke (U+0336) —
	le treeview Tk ne permet pas de font par cellule."""
	return "\u0336".join(text) + "\u0336"


def _browser_cell(proc: Process) -> str:
	"""Colonne browser : mode du proc (window/tab/manual) uniquement
	la ou une ouverture navigateur existe (ports + mode != none).
	Lignes app / procs sans port / 'none' -> cellule vide."""
	if proc.browser_mode == "none" or not proc.ports:
		return ""
	return proc.browser_mode


def _fmt_uptime(seconds: float) -> str:
	s = int(seconds)
	if s <= 0:
		return ""
	if s < 60:
		return f"{s}s"
	if s < 3600:
		return f"{s // 60}m{s % 60:02d}s"
	return f"{s // 3600}h{(s % 3600) // 60:02d}m"


class TreeMixin(tk.Tk):
	apps: List[App]
	_snapshot: Dict[Tuple[str, str], ProcStatus]

	def _iid_app(self, app: App) -> str:
		return f"app::{app.name}"

	def _iid_proc(self, app: App, proc: Process) -> str:
		return f"proc::{app.name}::{proc.name}"

	def _upsert(self, iid: str, parent: str, idx: int, **kw) -> None:
		"""Item ou insert+move : garde l'iid (selection) entre refresh.
		'open' n'est applique qu'a l'insert : un collapse manuel ne
		doit pas etre re-ouvert par le refresh periodique."""
		if self.tree.exists(iid):
			kw.pop("open", None)
			self.tree.item(iid, **kw)
			self.tree.move(iid, parent, idx)
		else:
			self.tree.insert(parent, idx, iid=iid, **kw)

	def _resolve(self, iid: str) -> Optional[tuple]:
		if iid.startswith("app::"):
			app = next((a for a in self.apps if a.name == iid[5:]), None)
			return ("app", app) if app else None
		if iid.startswith("proc::"):
			_, app_name, proc_name = iid.split("::", 2)
			app = next((a for a in self.apps if a.name == app_name), None)
			if not app:
				return None
			proc = next(
				(p for p in app.processes if p.name == proc_name), None
			)
			return ("proc", app, proc) if proc else None
		return None

	def _selection(self) -> Optional[tuple]:
		sel = self.tree.selection()
		return self._resolve(sel[0]) if sel else None

	def _sel_procs(
		self,
	) -> Optional[Tuple[App, List[Process]]]:
		"""Processus selectionnes (meme app, garanti par _on_select)."""
		procs = [
			(t[1], t[2])
			for iid in self.tree.selection()
			if (t := self._resolve(iid)) and t[0] == "proc"
		]
		return (procs[0][0], [p for _, p in procs]) if procs else None

	def _dot(self, color: str) -> tk.PhotoImage:
		"""Petit carre colore (cache) affiche devant le nom d'app."""
		img = self._dots.get(color)
		if img is None:
			img = tk.PhotoImage(width=12, height=12)
			try:
				r, g, b = (v // 256 * 2 // 3 for v in self.winfo_rgb(color))
				img.put(f"#{r:02x}{g:02x}{b:02x}", to=(0, 0, 12, 12))
				img.put(color, to=(1, 1, 11, 11))
			except tk.TclError:
				pass  # couleur invalide -> carre transparent
			self._dots[color] = img
		return img

	def _app_state(self, app: App) -> Tuple[str, str]:
		states = [
			self._snapshot.get((app.name, p.name), ProcStatus()).state
			for p in app.processes
		]
		if not states:
			return ("○ empty", "gray")
		running = states.count(STATUS_RUNNING)
		external = states.count(STATUS_EXTERNAL)
		if running == len(states):
			return ("● running", "green")
		if external == len(states):
			return ("◉ external", "orange")
		if running > 0 or external > 0:
			return (f"◐ {running + external}/{len(states)}", "orange")
		return ("○ stopped", "gray")

	def _reload_tree(self) -> None:
		"""Mise a jour en place : les iids persistent entre refresh,
		la selection/focus ne sont jamais detruits par le polling."""
		filt = self._filter_var.get().lower().strip()
		keep = set()
		row = 0
		for app in self.apps:
			if filt and filt not in app.name.lower():
				continue
			app_iid = self._iid_app(app)
			keep.add(app_iid)
			label, color = self._app_state(app)
			all_ports = sorted({
				port
				for p in app.processes
				for port in self._snapshot.get(
					(app.name, p.name), ProcStatus()
				).ports
			})
			n = len(app.processes)
			values = (
				label, "", "", "",
				", ".join(map(str, all_ports)), ""
			)
			disp = _strike(app.label) if app.exclude_all else app.label
			self._upsert(
				app_iid, "", row,
				text=f" {disp} ({n})",
				image=self._dot(app.color) if app.color else "",
				values=values, tags=(color,),
				open=app.name not in self._collapsed,
			)
			row += 1
			for i, proc in enumerate(app.processes):
				proc_iid = self._iid_proc(app, proc)
				keep.add(proc_iid)
				st = self._snapshot.get(
					(app.name, proc.name), ProcStatus()
				)
				plabel, pcolor = STATE_META.get(st.state, ("?", "gray"))
				pids = ", ".join(map(str, st.pids[:3]))
				if len(st.pids) > 3:
					pids += ",…"
				self._upsert(
					proc_iid, app_iid, i,
					text=f"   {proc.name}",
					values=(
						plabel,
						"☑" if proc.tmux else "☐",
						_browser_cell(proc),
						pids,
						", ".join(map(str, st.ports)),
						_fmt_uptime(st.uptime),
					),
					tags=(pcolor,),
				)
		# supprime uniquement ce qui a disparu (filtre, edition)
		for iid in self.tree.get_children(""):
			if iid not in keep:
				self.tree.delete(iid)
				continue
			for cid in self.tree.get_children(iid):
				if cid not in keep:
					self.tree.delete(cid)
		self._save_expand_state()
		self._refresh_toggle_btn()

	def _tree_set_open(self, open_: bool) -> None:
		"""Expand/collapse toutes les lignes app du tree."""
		for iid in self.tree.get_children(""):
			self.tree.item(iid, open=open_)
		self._save_expand_state()

	def _save_expand_state(self) -> None:
		"""Persiste les apps repliees (pref 'collapsed_apps') : le
		set, pas un bool global — chaque node garde son etat.
		Les apps masquees par le filtre conservent leur etat,
		les apps supprimees sont oubliees. Appelee au refresh et
		sur <<TreeviewOpen/Close>> — n'ecrit que sur changement."""
		rows = self.tree.get_children("")
		visible = {
			iid[5:] for iid in rows if not self.tree.item(iid, "open")
		}
		hidden = {a.name for a in self.apps} - {iid[5:] for iid in rows}
		new = (self._collapsed & hidden) | visible
		if new != self._collapsed:
			self._collapsed = new
			save_pref("collapsed_apps", json.dumps(sorted(new)))

	def _on_select(self, _event) -> None:
		sel = self.tree.selection()
		if len(sel) > 1:
			# multi-select : procs d'une meme app uniquement.
			# selection_remove (pas selection_set) pour ne pas reset
			# l'ancre Tk pendant un shift-gesture -> plage correcte.
			anchor = self.tree.focus()
			if anchor not in sel:
				anchor = sel[0]
			if anchor.startswith("proc::"):
				prefix = anchor.rsplit("::", 1)[0] + "::"
				keep = {i for i in sel if i.startswith(prefix)}
			else:
				keep = {anchor}
			extra = [i for i in sel if i not in keep]
			if extra:
				self.tree.selection_remove(*extra)
		target = self._sel_procs()
		if target:
			app, procs = target
			proc = next(
				(
					p
					for p in procs
					if self._iid_proc(app, p) == self.tree.focus()
				),
				procs[0],
			)
			self._show_logs(app, proc, focus=False)
		else:
			# ligne app : sous-onglets de tous ses processus
			t = self._selection()
			if t and t[0] == "app":
				self._show_logs(t[1], focus=False)
		self._refresh_toggle_btn()

	def _on_cell_click(self, event) -> Optional[str]:
		"""Clic sur la cellule tmux -> toggle 'Run in tmux' (colonne #2)."""
		if not tmux.TMUX_OK:
			return None  # colonne masquee : #2 = browser, pas de toggle
		if (
			self.tree.identify_region(event.x, event.y) != "cell"
			or self.tree.identify_column(event.x) != "#2"
		):
			return None
		t = self._resolve(self.tree.identify_row(event.y))
		if not t or t[0] != "proc":
			return None
		t[2].tmux = not t[2].tmux
		self._save()
		return "break"

	def _on_double_click(self, event) -> Optional[str]:
		# edit uniquement sur le nom (colonne #0, element texte/image) ;
		# l'indicateur expand/collapse et les autres colonnes gardent
		# le comportement par defaut (collapse, selection).
		if (
			self.tree.identify_region(event.x, event.y) != "tree"
			or self.tree.identify_element(event.x, event.y)
			not in ("text", "image")
		):
			return None
		iid = self.tree.identify_row(event.y)
		if not iid:
			return None
		self.tree.selection_set(iid)
		self._edit_selection()
		return "break"

	def _refresh_statusbar(self) -> None:
		n_proc = sum(len(a.processes) for a in self.apps)
		n_up = sum(
			1
			for s in self._snapshot.values()
			if s.state == STATUS_RUNNING
		)
		self._status_lbl.config(
			text=f"{len(self.apps)} apps • {n_up}/{n_proc} running "
			f"• updated {time.strftime('%H:%M:%S')}"
		)

	def _fill_ports(
		self, all_ports: List[portscan.PortInfo]
	) -> None:
		prev = self.ports_tree.selection()
		self.ports_tree.delete(*self.ports_tree.get_children(""))
		for info in all_ports:
			app = self._port_owner.get(info.port, "")
			tags = ("managed",) if app else ()
			self.ports_tree.insert(
				"", tk.END,
				values=(
					info.port, info.proto, info.pid or "",
					info.process, app, info.addr,
				),
				tags=tags,
			)
		if prev:
			self.ports_tree.selection_set(
				[i for i in prev if self.ports_tree.exists(i)]
			)
