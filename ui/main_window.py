"""Main window: apps/processes tree, logs, ports."""
from __future__ import annotations

import json
import logging
import queue
import threading
import tkinter as tk
from functools import partial
from pathlib import Path
from tkinter import ttk
from typing import Any, Dict, List, Optional, Set, Tuple

from core import tmux
from core.config import (
	App,
	Process,
	default_logs_dir,
	load_config,
	load_pref,
	load_window_config,
	save_pref,
)
from core.browser import browsers
from core.manager import ProcessManager, ProcStatus
from ui.actions import ActionsMixin
from ui.dialogs import LauncherLogDialog, PrefsDialog
from ui.editing import EditMixin
from ui.lifecycle import LifecycleMixin
from ui.logs import LogsMixin
from ui.menus import MenuMixin
from ui.polling import PollMixin
from ui.theme import ThemeMixin
from ui.tray import ICON_PATH, TrayMixin
from ui.tree import TreeMixin

log = logging.getLogger(__name__)

DEFAULT_SIZE = "1150x680"
MIN_W, MIN_H = 800, 450


def _tooltip(w: tk.Widget, text: str) -> None:
	"""Info-bulle legere : Toplevel sans deco sous le widget, ~500ms."""
	tip: Optional[tk.Toplevel] = None
	aid = ""

	def hide(_e=None) -> None:
		nonlocal tip, aid
		if aid:
			w.after_cancel(aid)
			aid = ""
		if tip is not None:
			tip.destroy()
			tip = None

	def show(_e=None) -> None:
		nonlocal tip
		if tip is not None:
			return
		tip = tk.Toplevel(w)
		tip.wm_overrideredirect(True)
		tip.wm_geometry(
			f"+{w.winfo_rootx()}"
			f"+{w.winfo_rooty() + w.winfo_height() + 2}"
		)
		tk.Label(
			tip, text=text, relief=tk.SOLID, borderwidth=1,
			bg="#ffffe0", fg="#000000", padx=4, pady=2,
		).pack()

	def schedule(_e=None) -> None:
		nonlocal aid
		aid = w.after(500, show)

	w.bind("<Enter>", schedule, add="+")
	w.bind("<Leave>", hide, add="+")
	w.bind("<ButtonPress>", hide, add="+")


class MainWindow(
	MenuMixin,
	EditMixin,
	ActionsMixin,
	TreeMixin,
	LogsMixin,
	PollMixin,
	ThemeMixin,
	TrayMixin,
	LifecycleMixin,
	tk.Tk,
):
	def __init__(
		self, config_path: Path, instance_sock=None,
		logs_dir: Optional[Path] = None,
	) -> None:
		super().__init__(className="applauncher")
		self.title("App Launcher")
		self.geometry(DEFAULT_SIZE)
		# migration : l'ancienne section 'window:' du yaml -> prefs
		# (les prefs font ensuite foi, la section est ignoree)
		legacy = load_window_config(config_path)
		for pref_key, yaml_key in (
			("win_min_width", "min_width"),
			("win_min_height", "min_height"),
		):
			if legacy.get(yaml_key) and not load_pref(pref_key, ""):
				save_pref(pref_key, str(legacy[yaml_key]))
		try:
			self.minsize(
				int(load_pref("win_min_width", "")),
				int(load_pref("win_min_height", "")),
			)
		except (TypeError, ValueError):
			self.minsize(MIN_W, MIN_H)

		self.config_path = config_path
		self.logs_dir = logs_dir or default_logs_dir()
		self.apps: List[App] = load_config(config_path)
		self.manager = ProcessManager(self.logs_dir)
		self.manager.set_apps(self.apps)
		self._queue: queue.Queue = queue.Queue()
		self._snapshot: Dict[Tuple[str, str], ProcStatus] = {}
		self._port_owner: Dict[int, str] = {}
		self._alive: bool = True
		self._minimized: bool = False
		self._ports_visible: bool = True  # onglet Ports selectionne au depart
		self._wake = threading.Event()  # reveil anticipe du poller
		self._instance_sock = instance_sock  # single-instance (None = tests)
		self._cpu_last = None
		self._cpu_ema = 0.0
		self._closing: bool = False
		self._confirming: bool = False
		self._logs_app: Optional[App] = None
		self._log_tabs: Dict[str, dict] = {}
		self._dots: Dict[str, tk.PhotoImage] = {}
		try:
			self._collapsed: Set[str] = set(
				json.loads(load_pref("collapsed_apps", "[]"))
			)
		except json.JSONDecodeError:
			self._collapsed = set()
		self._tray = None
		self._tray_live: bool = False
		self._busy_names: List[str] = []
		self._spin_i = 0
		self._spinning = False
		self._attach_pending: Set[str] = set()
		self._filter_var = tk.StringVar()
		self._prefs_dialog: Optional[tk.Toplevel] = None
		self._log_dialog: Optional[tk.Toplevel] = None
		self._simple_var = tk.BooleanVar(
			value=load_pref("simple_mode", "0") == "1"
		)
		self._sash_pos = 580  # position sash du paned hors mode simple
		self._init_themes()
		browser = load_pref("browser", "")
		# compat ancienne pref ("firefox") -> nouvelle cle ("Firefox ...")
		if browser not in browsers():
			browser = next(
				(k for k in browsers() if browser.lower() in k.lower()),
				next(iter(browsers()), ""),
			)
		self._browser_var = tk.StringVar(value=browser)

		self._build_ui()
		if self._simple_var.get():
			self._set_simple(True)
		self._set_theme(self._theme)
		self._reload_tree()
		self.protocol("WM_DELETE_WINDOW", self._on_close)
		self._setup_tray()
		self._start_poller()
		self._start_instance_listener()
		self.after(150, self._drain_queue)

	# ---------------- UI ----------------

	def _open_dialog(self, attr: str, cls) -> None:
		"""Fenetre non modale a instance unique : deiconify+lift si
		deja ouverte (lift seul ne restaure pas une fenetre iconic)."""
		d = getattr(self, attr)
		if d is not None and d.winfo_exists():
			d.deiconify()
			d.lift()
			return
		setattr(self, attr, cls(self))

	def _open_prefs(self) -> None:
		self._open_dialog("_prefs_dialog", PrefsDialog)

	def _open_log_viewer(self) -> None:
		self._open_dialog("_log_dialog", LauncherLogDialog)

	def _set_simple(self, on: bool) -> None:
		"""Mode simple : ne garde que le panneau des logs (tree,
		toolbar et onglet Ports masques). Toggle : F11 / menu View."""
		self._simple_var.set(on)
		if on:
			if self.winfo_viewable():
				self._sash_pos = self._paned.sashpos(0)
			self.notebook.select(self._logs_frame)
			self.notebook.forget(self._ports_frame)
			self._paned.forget(self._left)
			self._toolbar.pack_forget()
		else:
			self._toolbar.pack(
				side=tk.TOP, fill=tk.X, padx=6, pady=4,
				before=self._paned,
			)
			self._paned.insert(0, self._left, weight=3)
			self.notebook.insert(0, self._ports_frame, text="Ports")
			pos = self._sash_pos
			self.after_idle(lambda: self._paned.sashpos(0, pos))
		save_pref("simple_mode", "1" if on else "0")

	def _build_ui(self) -> None:
		self._build_menu()
		toolbar = ttk.Frame(self)
		self._toolbar = toolbar
		toolbar.pack(side=tk.TOP, fill=tk.X, padx=6, pady=4)

		def _btn(text: str, cmd, px=0, tip: str = "", **kw) -> ttk.Button:
			b = ttk.Button(toolbar, text=text, command=cmd, **kw)
			b.pack(side=tk.LEFT, padx=px)
			if tip:
				_tooltip(b, tip)
			return b

		_btn("+ App", self._add_app, tip="New app")
		_btn("+ Process", self._add_process, 4,
		     tip="New process (selected app)")
		_btn("Edit", self._edit_selection, (0, 14), underline=0,
		     tip="Edit selection (e)")
		self._up_btn = _btn(
			"↑", partial(self._move_selection, -1), (4, 0), width=3,
			tip="Move up (locked while running)",
		)
		self._down_btn = _btn(
			"↓", partial(self._move_selection, 1), width=3,
			tip="Move down (locked while running)",
		)
		_btn("⇩", partial(self._tree_set_open, True), (4, 0), width=3,
		     tip="Expand all")
		_btn("⇧", partial(self._tree_set_open, False), (0, 14), width=3,
		     tip="Collapse all")
		self._toggle_btn = _btn(
			"▶ Start", self._toggle_proc, underline=2,
			tip="Start / Stop selection (s)",
		)
		_btn("▶ Start all", self._start_all, 4, tip="Start all apps")
		_btn(
			"⏹ Stop all",
			partial(self._run_action, self.manager.stop_all, self.apps),
			(4, 14), tip="Stop all apps",
		)
		ttk.Label(toolbar, text="Filter:").pack(side=tk.LEFT)
		entry = ttk.Entry(toolbar, textvariable=self._filter_var, width=18)
		entry.pack(side=tk.LEFT, padx=4)
		_tooltip(entry, "Filter apps by name")
		self._filter_var.trace_add("write", lambda *_: self._reload_tree())
		browser_combo = ttk.Combobox(
			toolbar,
			textvariable=self._browser_var,
			values=tuple(browsers()),
			state="readonly",
			width=14,
		)
		browser_combo.pack(side=tk.LEFT, padx=(4, 0))
		_tooltip(browser_combo, "Browser used by Open/Launch")
		browser_combo.bind(
			"<<ComboboxSelected>>",
			lambda _e: save_pref("browser", self._browser_var.get()),
		)
		_btn("Launch", self._launch_proc_browser, 4,
		     tip="Start and open in browser")
		_btn("⚙", self._open_prefs, width=3, tip="Preferences (Ctrl+,)")
		_btn(
			"◧", lambda: self._set_simple(not self._simple_var.get()),
			width=3, tip="Simple mode: logs only (F11)",
		)
		if len(self._themes) > 1:
			theme_combo = ttk.Combobox(
				toolbar,
				textvariable=self._theme_var,
				values=[lbl for lbl, _ in self._themes],
				state="readonly",
				width=9,
			)
			theme_combo.pack(side=tk.LEFT, padx=(4, 0))
			_tooltip(theme_combo, "Theme")
			theme_combo.bind(
				"<<ComboboxSelected>>", lambda _e: self._on_theme_select()
			)
		if ICON_PATH.exists():
			self._icon_img = tk.PhotoImage(file=str(ICON_PATH))
			self.iconphoto(True, self._icon_img)

		# barre de statut en bas (pack avant le paned qui expand)
		statusbar = ttk.Frame(self)
		statusbar.pack(side=tk.BOTTOM, fill=tk.X)
		self._activity_lbl = ttk.Label(statusbar, text="", width=34)
		self._activity_lbl.pack(side=tk.LEFT, padx=6)
		self._status_lbl = ttk.Label(statusbar, text="")
		self._status_lbl.pack(side=tk.RIGHT, padx=6)

		paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
		self._paned = paned
		paned.pack(fill=tk.BOTH, expand=True, padx=6, pady=(0, 6))

		left = ttk.Frame(paned)
		self._left = left
		self.tree = ttk.Treeview(
			left,
			columns=("state", "tmux", "browser", "pid", "ports", "uptime"),
			show="tree headings",
			selectmode="extended",
		)
		cols: List[Tuple[str, str, int, int, Any]] = [
			("#0", "Nom", 200, 100, tk.W),
			("state", "State", 75, 55, tk.CENTER),
			("tmux", "tmux", 45, 40, tk.CENTER),
			("browser", "browser", 60, 45, tk.CENTER),
			("pid", "PID", 90, 70, tk.W),
			("ports", "Ports", 120, 70, tk.W),
			("uptime", "Uptime", 65, 50, tk.CENTER),
		]
		for col, label, w, mw, anchor in cols:
			self.tree.heading(col, text=label)
			self.tree.column(
				col, width=w, minwidth=mw, anchor=anchor, stretch=True
			)
		if not tmux.TMUX_OK:
			# pas de tmux : colonne et interactions tmux masquees
			self.tree.configure(
				displaycolumns=(
					"state", "browser", "pid", "ports", "uptime"
				)
			)
		scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self.tree.yview)
		self.tree.configure(yscrollcommand=scroll.set)
		self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
		scroll.pack(side=tk.RIGHT, fill=tk.Y)
		for tag, color in self._tag_colors().items():
			self.tree.tag_configure(tag, foreground=color)
		paned.add(left, weight=3)

		self.notebook = ttk.Notebook(paned)
		paned.add(self.notebook, weight=2)
		# le paned garde assez de place pour toutes les colonnes de l'arbre
		# (sinon la derniere, uptime, est clippee au demarrage) ; on pose
		# le sash au premier Configure avec une taille reelle
		def _place_sash(_e=None, p=paned):
			if p.winfo_width() > 600:
				p.sashpos(0, 580)
				p.unbind("<Configure>")

		paned.bind("<Configure>", _place_sash)

		logs_frame = ttk.Frame(self.notebook)
		self._logs_frame = logs_frame
		logs_top = ttk.Frame(logs_frame)
		logs_top.pack(fill=tk.X)
		self._logs_target = ttk.Label(logs_top, text="Select a process")
		self._logs_target.pack(side=tk.LEFT, padx=4, pady=2)
		ttk.Button(logs_top, text="Refresh", command=self._reload_logs).pack(
			side=tk.RIGHT, padx=4
		)
		ttk.Button(logs_top, text="Clear", command=self._clear_logs).pack(
			side=tk.RIGHT
		)
		ttk.Button(
			logs_top, text="Clear all", command=self._clear_all_logs
		).pack(side=tk.RIGHT, padx=(0, 4))
		# sous-onglets : un Text par processus de l'app selectionnee
		self._logs_nb = ttk.Notebook(logs_frame)
		self._logs_nb.pack(fill=tk.BOTH, expand=True)
		self._logs_nb.bind(
			"<<NotebookTabChanged>>", lambda _e: self._on_log_tab()
		)
		self.notebook.add(logs_frame, text="Logs")

		ports_frame = ttk.Frame(self.notebook)
		self._ports_frame = ports_frame
		self.ports_tree = ttk.Treeview(
			ports_frame,
			columns=("port", "proto", "pid", "proc", "app", "addr"),
			show="headings",
		)
		for col, label, w in (
			("port", "Port", 70),
			("proto", "Proto", 50),
			("pid", "PID", 80),
			("proc", "Process", 130),
			("app", "App", 140),
			("addr", "Address", 110),
		):
			self.ports_tree.heading(col, text=label)
			self.ports_tree.column(col, width=w)
		pscroll = ttk.Scrollbar(ports_frame, orient=tk.VERTICAL, command=self.ports_tree.yview)
		self.ports_tree.configure(yscrollcommand=pscroll.set)
		self.ports_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
		pscroll.pack(side=tk.RIGHT, fill=tk.Y)
		self.ports_tree.tag_configure(
			"managed", foreground=self._tag_colors()["green"]
		)
		# Ports en premier onglet (avant Logs)
		self.notebook.insert(0, ports_frame, text="Ports")
		self.notebook.bind(
			"<<NotebookTabChanged>>", lambda _e: self._on_main_tab()
		)

		self.tree.bind("<Delete>", lambda _e: self._delete_selection())
		self.tree.bind("<Button-1>", self._on_cell_click)
		self.tree.bind("<Button-3>", self._on_right_click)
		self.tree.bind("<<TreeviewSelect>>", self._on_select)
		self.tree.bind(
			"<<TreeviewOpen>>", lambda _e: self._save_expand_state()
		)
		self.tree.bind(
			"<<TreeviewClose>>", lambda _e: self._save_expand_state()
		)
		self.tree.bind("<Double-1>", self._on_double_click)
		self.bind("<Unmap>", self._on_unmap)
		self.bind("<Map>", self._on_map)
		self.bind("<Destroy>", self._on_destroy)
		self.bind("<KeyPress>", self._on_keypress)

	def report_callback_exception(self, exc, val, tb) -> None:
		log.exception("Tk callback exception", exc_info=(exc, val, tb))
