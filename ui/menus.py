"""Barre de menus et menu contextuel du tree."""
# mypy: disable-error-code="attr-defined,has-type"
from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
import webbrowser
from functools import partial
from tkinter import ttk

from core.browser import app_urls, port_url
from ui.tray import ICON_PATH

from core import tmux

GITHUB_URL = "https://github.com/yopkool29/applauncher"


class MenuMixin(tk.Tk):
	def _build_menu(self) -> None:
		menubar = tk.Menu(self, tearoff=0)
		self.config(menu=menubar)

		def _menu(label: str, spec: list) -> tk.Menu:
			m = tk.Menu(menubar, tearoff=0)
			for item in spec:
				if item is None:
					m.add_separator()
				else:
					lbl, accel, cmd = item
					m.add_command(
						label=lbl, command=cmd, accelerator=accel
					)
			menubar.add_cascade(label=label, menu=m)
			return m

		_menu("File", [
			("New App…", "", self._add_app),
			("New Process…", "", self._add_process),
			None,
			("Minimize to Tray", "", self.iconify),
			("Quit", "Ctrl+Q", self._on_close),
		])
		_menu("Edit", [
			("Edit…", "E", self._edit_selection),
			("Delete", "Del", self._delete_selection),
			None,
			("Preferences…", "Ctrl+,", self._open_prefs),
		])
		_menu("Process", [
			("Start / Stop", "S", self._toggle_proc),
			("Restart", "", self._restart_selection),
			None,
			("Launch in Browser", "", self._launch_proc_browser),
			None,
			("Start All", "", self._start_all),
			("Stop All", "", partial(
				self._run_action, self.manager.stop_all, self.apps
			)),
		])
		m_view = _menu("View", [
			("Logs", "", lambda: self.notebook.select(self._logs_frame)),
			("Ports", "", lambda: (
				str(self._ports_frame) in self.notebook.tabs()
				and self.notebook.select(self._ports_frame)
			)),
			("Launcher log", "", self._open_log_viewer),
			None,
			("Refresh", "F5", self._refresh_all),
		])
		m_view.insert_checkbutton(
			0, label="Simple mode", accelerator="F11",
			variable=self._simple_var,
			command=lambda: self._set_simple(self._simple_var.get()),
		)
		if len(self._themes) > 1:
			m_theme = tk.Menu(m_view, tearoff=0)
			for lbl, _tid in self._themes:
				m_theme.add_radiobutton(
					label=lbl, variable=self._theme_var,
					command=self._on_theme_select,
				)
			m_view.insert_cascade(0, label="Theme", menu=m_theme)
		_menu("Help", [
			("About", "", self._show_about),
			(
				"GitHub page", "",
				lambda: webbrowser.open(GITHUB_URL),
			),
		])

		self.bind("<Control-q>", lambda _e: self._on_close())
		self.bind("<Control-comma>", lambda _e: self._open_prefs())
		self.bind("<F5>", lambda _e: self._refresh_all())
		self.bind(
			"<F11>",
			lambda _e: self._set_simple(not self._simple_var.get()),
		)

	def _show_about(self) -> None:
		d = tk.Toplevel(self)
		d.title("About")
		d.resizable(False, False)
		d.transient(self)
		if ICON_PATH.exists():
			# subsample() fait du nearest-neighbor (pixelise) ->
			# vrai resize lisse via Pillow (pas d'ImageTk : PNG en
			# memoire passe a PhotoImage en base64)
			import base64
			import io
			from PIL import Image  # type: ignore
			buf = io.BytesIO()
			Image.open(ICON_PATH).resize(
				(64, 64), Image.LANCZOS
			).save(buf, "PNG")
			img = tk.PhotoImage(
				data=base64.b64encode(buf.getvalue())
			)
			lbl = ttk.Label(d, image=img)
			lbl.image = img  # garde la ref : Tk GC les images sinon
			lbl.pack(pady=(14, 0))
		ttk.Label(
			d,
			text="App Launcher\nCentralized launcher for multi-process "
				 "apps (shell, docker, tmux).",
			justify=tk.CENTER,
		).pack(padx=20, pady=(14, 4))
		link = tk.Label(d, text=GITHUB_URL, fg="#4a9eff", cursor="hand2")
		f = tkfont.Font(link, link.cget("font"))
		f.configure(underline=True)
		link.configure(font=f)
		link.pack(padx=20, pady=(0, 8))
		link.bind("<Button-1>", lambda _e: webbrowser.open(GITHUB_URL))
		ttk.Button(d, text="Close", command=d.destroy).pack(pady=(0, 12))
		d.bind("<Escape>", lambda _e: d.destroy())
		# centre sur la fenetre principale (taille reelle apres mapping)
		d.update_idletasks()
		x = self.winfo_rootx() + (self.winfo_width() - d.winfo_width()) // 2
		y = self.winfo_rooty() + (self.winfo_height() - d.winfo_height()) // 2
		d.geometry(f"+{x}+{y}")

	@staticmethod
	def _pad_menu(menu: tk.Menu, min_chars: int = 28) -> None:
		"""Elargit un menu au min_chars + marge a gauche/droite :
		labels paddes d'espaces (pas de padx sur les entrees Tk)."""
		end = menu.index("end")
		if end is None:
			return
		for i in range(end + 1):
			try:
				label = str(menu.entrycget(i, "label"))
			except tk.TclError:
				continue
			if label:
				menu.entryconfigure(
					i, label="  " + label.ljust(min_chars) + "  "
				)

	def _on_right_click(self, event) -> None:
		iid = self.tree.identify_row(event.y)
		# clic droit sur un item selectionne garde la multi-selection
		if iid and iid not in self.tree.selection():
			self.tree.selection_set(iid)
		target = self._selection()
		procs_sel = self._sel_procs()
		if target is None:
			return
		menu = tk.Menu(self, tearoff=0)

		def item(lbl: str, fn, accel: str = "") -> None:
			menu.add_command(label=lbl, command=fn, accelerator=accel)

		if procs_sel and len(procs_sel[1]) > 1:
			app, procs = procs_sel
			n = len(procs)
			item(f"Start all ({n})", partial(self._start_procs, app, procs), "s")
			item(f"Stop all ({n})",
			     partial(self._confirm_stop_procs, app, procs), "s")
			item(f"Restart all ({n})",
			     partial(self._run_each, app, procs, self.manager.restart))
			if (
				tmux.TMUX_OK
				and any(p.tmux for p in procs)
				and tmux.session_exists(
					tmux.session_name(app, self.apps)
				)
			):
				menu.add_separator()
				item("Attach (tmux)",
				     partial(self._tmux_attach, app, procs[0]))
			menu.add_separator()
			item(f"Delete {n} processes",
			     partial(self._del_procs, app, procs), "Del")
		elif target[0] == "app":
			app = target[1]
			item("Start all", partial(self._start_app, app), "s")
			item("Stop all",
			     partial(self._run_action, self.manager.stop_app, app), "s")
			item("Restart all", partial(self._start_app, app, True))
			menu.add_separator()
			if app_urls(app):
				item("Open URLs", partial(self._open_app_urls, app), "u")
			item("Add process", partial(self._add_process, app))
			item("Edit app", partial(self._edit_app, app), "e")
			menu.add_separator()
			item("Delete app", partial(self._del_app, app), "Del")
		else:
			_, app, proc = target
			item("Start", partial(self._start_procs, app, [proc]), "s")
			item("Stop", partial(self._confirm_stop_procs, app, [proc]), "s")
			item("Restart",
			     partial(self._run_each, app, [proc], self.manager.restart))
			if proc.browser_mode != "none":
				if len(proc.ports) == 1:
					item(f"Open :{proc.ports[0]}",
					     partial(self._open_port, proc, proc.ports[0]), "u")
				elif len(proc.ports) > 1:
					open_menu = tk.Menu(menu, tearoff=0)
					for p in proc.ports:
						open_menu.add_command(
							label=port_url(p).split("://", 1)[-1],
							command=partial(self._open_port, proc, p),
						)
					self._pad_menu(open_menu)
					menu.add_cascade(label="Open port", menu=open_menu)
			if proc.tmux and tmux.TMUX_OK and tmux.session_exists(
				tmux.session_name(app, self.apps)
			):
				menu.add_separator()
				item("Attach (tmux)",
				     partial(self._tmux_attach, app, proc))
			menu.add_separator()
			item("Edit process", partial(self._edit_proc, app, proc), "e")
			item("Delete process", partial(self._del_procs, app, [proc]), "Del")
		self._pad_menu(menu)
		# poste en differe : tk_popup prend un grab ; pendant le handler
		# Button-3 le bouton est encore enfonce -> le release peut fermer
		# le menu ou le grab echouer si un dialogue modal le detient.
		x, y = event.x_root, event.y_root

		def _post() -> None:
			try:
				menu.tk_popup(x, y)
			except tk.TclError:
				pass

		self.after_idle(_post)
