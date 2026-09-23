"""Modal dialogs: app and process editing."""
from __future__ import annotations

import locale
import os
import tkinter as tk
from functools import partial
from tkinter import filedialog, messagebox, ttk
from typing import Generic, Optional, TypeVar

from core import manager
from core import ports as portscan
from core import tmux
from core.config import App, Process, load_pref, save_pref

T = TypeVar("T")


class _Modal(tk.Toplevel, Generic[T]):
	def __init__(
		self, parent: tk.Misc, title: str, modal: bool = True
	) -> None:
		super().__init__(parent)
		self.title(title)
		# non modal = fenetre autonome : resizable (modal = taille fixe)
		self.resizable(not modal, not modal)
		self.result: Optional[T] = None
		self._modal = modal
		self.wait_visibility()
		# transient = le WM gere l'empilement (au-dessus du parent) et
		# la minimisation/restauration groupee, modal ou pas
		self.transient(parent.winfo_toplevel())
		if modal:
			try:
				self.grab_set()
			except tk.TclError:
				pass
		else:
			# taille minimum = taille naturelle du contenu (mesuree
			# apres la construction du corps)
			self.after_idle(
				lambda: self.minsize(
					self.winfo_reqwidth(), self.winfo_reqheight()
				)
			)
		self.bind("<Escape>", lambda _e: self.destroy())

	def _ok_cancel(self, body: tk.Widget, row: int, colspan: int = 2) -> None:
		btns = ttk.Frame(body)
		btns.grid(
			row=row, column=0, columnspan=colspan,
			pady=(10, 0), sticky=tk.E,
		)
		ttk.Button(btns, text="Cancel", command=self.destroy).pack(
			side=tk.RIGHT, padx=4
		)
		ttk.Button(btns, text="OK", command=self._ok).pack(side=tk.RIGHT)

	def _ok(self) -> None:
		raise NotImplementedError


def _key_of(modes: dict, label: str, default: str) -> str:
	"""Cle d'un dict {key: label} depuis le label affiche."""
	return next((k for k, v in modes.items() if v == label), default)


# libelles localises : la langue vient de l'OS (LANGUAGE/LC_ALL/LANG),
# anglais par defaut
_L = {
	"en": {
		"yes": "✓ Yes", "no": "✗ No",
		"cancel": "↩ Cancel", "ok": "✓ OK",
		"quit": "Quit",
		"quit_running": "{n} process(es) are still running.\n"
		                "Stop them before quitting?",
		"quit_ask": "Quit App Launcher?",
		"quit_busy": "Nothing is running, but these ports are "
		             "still in use:\n{ports}\n\nQuit anyway?",
		"ext_title": "External process",
		"ext_one": "'{name}' seems to run outside the launcher.\n"
		           "Stop the processes on its ports?",
		"ext_many": "{n} selected process(es) run outside the "
		            "launcher.\nStop the processes on their ports?",
		"del_title": "Delete",
		"del_app": "Delete app '{name}' and its {count} process(es)?",
		"del_app_up": "\n{running} still running — they will be stopped.",
		"del_one": "Delete process '{name}'?",
		"del_many": "Delete {n} processes?",
	},
	"fr": {
		"yes": "✓ Oui", "no": "✗ Non",
		"cancel": "↩ Annuler", "ok": "✓ OK",
		"quit": "Quitter",
		"quit_running": "{n} processus tournent encore.\n"
		                "Les arrêter avant de quitter ?",
		"quit_ask": "Quitter App Launcher ?",
		"quit_busy": "Rien ne tourne, mais ces ports sont encore "
		             "utilisés :\n{ports}\n\nQuitter quand même ?",
		"ext_title": "Processus externe",
		"ext_one": "'{name}' semble tourner hors du launcher.\n"
		           "Arrêter les processus sur ses ports ?",
		"ext_many": "{n} processus sélectionnés tournent hors du "
		            "launcher.\nArrêter les processus sur leurs ports ?",
		"del_title": "Supprimer",
		"del_app": "Supprimer l'app '{name}' et ses {count} processus ?",
		"del_app_up": "\n{running} encore actif(s) — ils seront arrêtés.",
		"del_one": "Supprimer le processus '{name}' ?",
		"del_many": "Supprimer {n} processus ?",
	},
}


def L(key: str, **kw) -> str:
	"""Texte localise (langue de l'OS, en par defaut)."""
	loc = (
		os.getenv("LANGUAGE") or os.getenv("LC_ALL")
		or os.getenv("LANG") or (locale.getlocale()[0] or "")
	).lower()
	d = _L["fr"] if loc.startswith("fr") else _L["en"]
	return d[key].format(**kw)


class ConfirmDialog(_Modal[str]):
	"""Confirmation localisee avec boutons icones (remplace les
	messagebox.askyesno*/askokcancel : boutons en langue de l'OS)."""

	def __init__(
		self, parent: tk.Misc, title: str, text: str,
		buttons=("yes", "no"), bitmap: str = "question",
	) -> None:
		super().__init__(parent, title)
		body = ttk.Frame(self, padding=12)
		body.pack(fill=tk.BOTH, expand=True)
		row = ttk.Frame(body)
		row.pack(fill=tk.X)
		tk.Label(row, bitmap=bitmap).pack(side=tk.LEFT, padx=(0, 10))
		ttk.Label(row, text=text, justify=tk.LEFT).pack(side=tk.LEFT)
		btns = ttk.Frame(body)
		btns.pack(pady=(10, 0), anchor=tk.E)
		for b in buttons:
			ttk.Button(
				btns, text=L(b),
				command=partial(self._answer, b),
			).pack(side=tk.LEFT, padx=4)
		self.bind("<Return>", lambda _e: self._answer(buttons[0]))
		self.bind(
			"<Escape>",
			lambda _e: self._answer(
				"cancel" if "cancel" in buttons else "no"
			),
		)
		self.wait_window(self)

	def _answer(self, v: str) -> None:
		self.result = v
		self.destroy()


def confirm(
	parent: tk.Misc, title: str, text: str,
	buttons=("yes", "no"), bitmap: str = "question",
) -> str:
	"""Resultat : 'yes'/'no'/'ok'/'cancel' (fermeture = dernier choix)."""
	res = ConfirmDialog(parent, title, text, buttons, bitmap).result
	return res or ("cancel" if "cancel" in buttons else buttons[-1])


TMUX_LAYOUT_LABELS = {
	"tiled": "Grid",
	"horizontal": "Side by side",
	"vertical": "Stacked",
}

# mode tmux de l'app (remplace le duo tmux_attach/tmux_shared)
# ouverture navigateur d'un proc (Open :port / Open URLs / Launch)
BROWSER_MODES = {
	"window": "New window",
	"tab": "New tab",
	"manual": "Manual (Open :port only)",
	"none": "Nothing (hidden)",
}

# palette de couleurs HTML standard ("" = aucune)
APP_COLORS = (
	"",
	"#ff6347",  # tomato
	"#ffa500",  # orange
	"#ffd700",  # gold
	"#9acd32",  # yellowgreen
	"#2e8b57",  # seagreen
	"#20b2aa",  # lightseagreen
	"#00bfff",  # deepskyblue
	"#1e90ff",  # dodgerblue
	"#6a5acd",  # slateblue
	"#9370db",  # mediumpurple
	"#ff69b4",  # hotpink
	"#a52a2a",  # brown
	"#708090",  # slategray
)


class AppDialog(_Modal[App]):
	def __init__(self, parent: tk.Misc, app: Optional[App] = None) -> None:
		super().__init__(parent, "Edit app" if app else "New app")
		self._name_var = tk.StringVar(value=app.name if app else "")
		self._alias_var = tk.StringVar(value=app.alias if app else "")
		self._url = tk.StringVar(value=app.url if app else "")
		self._launch_port = tk.StringVar(
			value=str(app.launch_port) if app and app.launch_port else ""
		)
		self._layout = tk.StringVar(
			value=TMUX_LAYOUT_LABELS.get(
				app.tmux_layout if app else "", "Grid"
			)
		)
		# deux reglages orthogonaux : ou vont les panes (shared) et
		# faut-il ouvrir une fenetre terminal dessus (attach)
		self._tmux_shared = tk.BooleanVar(
			value=app.tmux_shared if app else False
		)
		self._tmux_attach = tk.BooleanVar(
			value=app.tmux_attach if app else True
		)
		self._exclude_all = tk.BooleanVar(
			value=app.exclude_all if app else False
		)
		self._color = app.color if app else ""

		body = ttk.Frame(self, padding=12)
		body.pack(fill=tk.BOTH, expand=True)
		rows = [
			("Name:", self._name_var),
			("Alias (display):", self._alias_var),
			("URL (optional):", self._url),
			("Launch port (optional):", self._launch_port),
		]
		for row, (label, var) in enumerate(rows):
			ttk.Label(body, text=label).grid(row=row, column=0, sticky=tk.W, pady=3)
			ttk.Entry(body, textvariable=var, width=42).grid(row=row, column=1, pady=3)
		row = len(rows)
		if tmux.TMUX_OK:
			ttk.Label(body, text="tmux:").grid(
				row=row, column=0, sticky=tk.W, pady=3
			)
			tm = ttk.Frame(body)
			tm.grid(row=row, column=1, sticky=tk.W)
			ttk.Checkbutton(
				tm, text="Join previous session (shared)",
				variable=self._tmux_shared,
			).pack(side=tk.LEFT)
			ttk.Checkbutton(
				tm, text="Attach new terminal window",
				variable=self._tmux_attach,
			).pack(side=tk.LEFT, padx=(10, 0))
			row += 1
			ttk.Label(body, text="tmux layout:").grid(
				row=row, column=0, sticky=tk.W, pady=3
			)
			ttk.Combobox(
				body, textvariable=self._layout, state="readonly", width=40,
				values=list(dict.fromkeys(TMUX_LAYOUT_LABELS.values())),
			).grid(row=row, column=1, pady=3)
			row += 1
		ttk.Checkbutton(
			body,
			text="Exclude from Start all / Stop all (manual only)",
			variable=self._exclude_all,
		).grid(row=row, column=1, sticky=tk.W, pady=3)
		row += 1
		ttk.Label(body, text="Color:").grid(row=row, column=0, sticky=tk.W, pady=3)
		colors = list(APP_COLORS)
		if self._color and self._color not in colors:
			colors.append(self._color)
		cv = tk.Canvas(
			body, width=22 * len(colors), height=24, highlightthickness=0
		)
		cv.grid(row=row, column=1, sticky=tk.W, pady=3)
		for i, c in enumerate(colors):
			x = 3 + i * 22
			cv.create_rectangle(
				x, 3, x + 18, 21, fill=c or "#ffffff", outline="#999999"
			)
			if not c:
				cv.create_line(x, 3, x + 18, 21, fill="#cc0000")
		self._colors = colors
		cv.bind(
			"<Button-1>",
			lambda e: self._pick_color(cv, int(cv.canvasx(e.x) // 22)),
		)
		self._mark_color(cv)

		self._ok_cancel(body, row + 1)
		self.bind("<Return>", lambda _e: self._ok())
		self.wait_window(self)

	def _pick_color(self, cv: tk.Canvas, idx: int) -> None:
		if 0 <= idx < len(self._colors):
			self._color = self._colors[idx]
			self._mark_color(cv)

	def _mark_color(self, cv: tk.Canvas) -> None:
		cv.delete("hl")
		if self._color in self._colors:
			x = 3 + self._colors.index(self._color) * 22
			cv.create_rectangle(
				x - 2, 1, x + 20, 23, outline="#000000", width=2, tags="hl"
			)

	def _ok(self) -> None:
		name = self._name_var.get().strip()
		if not name:
			messagebox.showwarning("App", "Name is required.", parent=self)
			return
		layout = _key_of(TMUX_LAYOUT_LABELS, self._layout.get(), "")
		port_txt = self._launch_port.get().strip()
		try:
			launch_port = int(port_txt) if port_txt else 0
		except ValueError:
			messagebox.showwarning(
				"App", f"Invalid launch port: {port_txt}", parent=self
			)
			return
		self.result = App(
			name=name,
			alias=self._alias_var.get().strip(),
			url=self._url.get().strip(),
			tmux_layout=layout,
			launch_port=launch_port,
			tmux_attach=self._tmux_attach.get(),
			tmux_shared=self._tmux_shared.get(),
			exclude_all=self._exclude_all.get(),
			color=self._color,
		)
		self.destroy()


class ProcessDialog(_Modal[Process]):
	def __init__(self, parent: tk.Misc, proc: Optional[Process] = None) -> None:
		super().__init__(parent, "Edit process" if proc else "New process")
		self._name_var = tk.StringVar(value=proc.name if proc else "")
		self._cmd = tk.StringVar(value=proc.cmd if proc else "")
		self._stop = tk.StringVar(value=proc.stop if proc else "")
		self._workdir = tk.StringVar(value=proc.workdir if proc else "")
		self._ports = tk.StringVar(
			value=", ".join(str(p) for p in proc.ports) if proc else ""
		)
		self._docker = tk.BooleanVar(value=proc.docker if proc else False)
		self._tmux = tk.BooleanVar(value=proc.tmux if proc else False)
		self._tmux_window = tk.IntVar(
			value=proc.tmux_window if proc else 0
		)
		self._browser_mode = tk.StringVar(
			value=BROWSER_MODES[
				proc.browser_mode
				if proc and proc.browser_mode in BROWSER_MODES
				else "window"
			]
		)

		body = ttk.Frame(self, padding=12)
		body.pack(fill=tk.BOTH, expand=True)

		rows = [
			("Name:", self._name_var),
			("Command:", self._cmd),
			("Stop (optional):", self._stop),
			("Directory:", self._workdir),
			("Ports (comma-sep):", self._ports),
		]
		for row, (label, var) in enumerate(rows):
			ttk.Label(body, text=label).grid(row=row, column=0, sticky=tk.W, pady=3)
			entry = ttk.Entry(body, textvariable=var, width=48)
			entry.grid(row=row, column=1, pady=3, sticky=tk.EW)
			if label == "Directory:":
				ttk.Button(
					body, text="...", width=3, command=self._browse
				).grid(row=row, column=2, padx=(4, 0))

		ttk.Checkbutton(
			body,
			text="Docker process (docker run / docker compose)",
			variable=self._docker,
		).grid(row=len(rows), column=1, sticky=tk.W, pady=3)
		if tmux.TMUX_OK:
			tmux_row = ttk.Frame(body)
			tmux_row.grid(
				row=len(rows) + 1, column=1, sticky=tk.W, pady=3
			)
			ttk.Checkbutton(
				tmux_row,
				text="Run in tmux (attachable via `tmux attach`)",
				variable=self._tmux,
			).pack(side=tk.LEFT)
			ttk.Label(tmux_row, text="window:").pack(
				side=tk.LEFT, padx=(10, 2)
			)
			ttk.Spinbox(
				tmux_row, from_=0, to=19, width=3,
				textvariable=self._tmux_window,
			).pack(side=tk.LEFT)
		browser_row = ttk.Frame(body)
		browser_row.grid(
			row=len(rows) + (2 if tmux.TMUX_OK else 1),
			column=1, sticky=tk.W, pady=3,
		)
		ttk.Label(browser_row, text="browser:").pack(side=tk.LEFT, padx=(0, 4))
		ttk.Combobox(
			browser_row,
			textvariable=self._browser_mode,
			values=list(BROWSER_MODES.values()),
			state="readonly",
			width=18,
		).pack(side=tk.LEFT)

		self._ok_cancel(body, len(rows) + 3, colspan=3)
		self.bind("<Return>", lambda _e: self._ok())
		self.wait_window(self)

	def _browse(self) -> None:
		path = filedialog.askdirectory(parent=self)
		if path:
			self._workdir.set(path)

	def _ok(self) -> None:
		name = self._name_var.get().strip()
		cmd = self._cmd.get().strip()
		if not name or not cmd:
			messagebox.showwarning(
				"Process", "Name and command are required.", parent=self
			)
			return
		chunks = [c.strip() for c in self._ports.get().split(",") if c.strip()]
		try:
			ports = [int(c) for c in chunks]
		except ValueError:
			bad = next((c for c in chunks if not c.isdecimal()), chunks[0])
			messagebox.showwarning(
				"Process", f"Invalid port: {bad}", parent=self
			)
			return
		self.result = Process(
			name=name,
			cmd=cmd,
			workdir=self._workdir.get().strip(),
			stop=self._stop.get().strip(),
			ports=ports,
			docker=self._docker.get(),
			tmux=self._tmux.get(),
			tmux_window=self._tmux_window.get(),
			browser_mode=_key_of(
				BROWSER_MODES, self._browser_mode.get(), "window"
			),
		)
		self.destroy()


class LauncherLogDialog(_Modal[None]):
	"""Visionneuse non modale de applauncher.log (refresh auto 2s)."""

	_TAIL = 262144  # derniers 256KB

	def __init__(self, parent: tk.Misc) -> None:
		super().__init__(parent, "Launcher log", modal=False)
		self._size = -1

		top = ttk.Frame(self, padding=(6, 6, 6, 0))
		top.pack(fill=tk.X)
		ttk.Button(top, text="Refresh", command=self._load).pack(
			side=tk.RIGHT, padx=(4, 0)
		)
		ttk.Button(top, text="Clear", command=self._clear).pack(
			side=tk.RIGHT
		)
		body = ttk.Frame(self, padding=6)
		body.pack(fill=tk.BOTH, expand=True)
		self._text = tk.Text(
			body, wrap=tk.NONE, state=tk.DISABLED, width=100, height=32
		)
		ys = ttk.Scrollbar(
			body, orient=tk.VERTICAL, command=self._text.yview
		)
		self._text.configure(yscrollcommand=ys.set)
		self._text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
		ys.pack(side=tk.RIGHT, fill=tk.Y)
		style = getattr(parent, "_style_log_widget", None)
		if style:
			style(self._text)
		self._refresh()

	def _set_text(self, text: str) -> None:
		self._text.configure(state=tk.NORMAL)
		self._text.delete("1.0", tk.END)
		self._text.insert("1.0", text)
		self._text.see(tk.END)
		self._text.configure(state=tk.DISABLED)

	def _load(self) -> None:
		try:
			path = manager.LOGS_DIR / "applauncher.log"
			size = path.stat().st_size
			if size == self._size:
				return
			with open(path, "rb") as f:
				f.seek(max(0, size - self._TAIL))
				text = f.read().decode("utf-8", errors="replace")
			self._set_text(text)
			self._size = size
		except OSError:
			pass

	def _clear(self) -> None:
		try:
			(manager.LOGS_DIR / "applauncher.log").write_bytes(b"")
		except OSError:
			pass
		self._size = 0
		self._set_text("")

	def _refresh(self) -> None:
		if not self.winfo_exists():
			return
		self._load()
		self.after(2000, self._refresh)


class PrefsDialog(_Modal[None]):
	"""Preferences globales (prefs.json)."""

	_QUIT_LABELS = {
		"ask": "Ask confirmation",
		"stop": "Stop processes, then quit",
		"leave": "Quit without stopping",
	}

	def __init__(self, parent: tk.Misc) -> None:
		super().__init__(parent, "Preferences", modal=False)
		self._win = parent  # MainWindow (acces au snapshot des ports)
		self._wrapper = tk.StringVar(value=load_pref("cmd_wrapper", ""))
		self._to_tray = tk.BooleanVar(
			value=load_pref("minimize_to_tray", "1") == "1"
		)
		# migration de l'ancien bool confirm_quit
		saved = load_pref("quit_action", "")
		if not saved:
			saved = (
				"ask"
				if load_pref("confirm_quit", "1") == "1"
				else "leave"
			)
		self._quit_action = tk.StringVar(
			value=self._QUIT_LABELS.get(saved, self._QUIT_LABELS["ask"])
		)

		body = ttk.Frame(self, padding=12)
		body.pack(fill=tk.BOTH, expand=True)
		# resize : la colonne contenu + la ligne du tree s'etendent
		body.columnconfigure(1, weight=1)
		body.rowconfigure(5, weight=1)
		ttk.Label(body, text="Command wrapper:").grid(
			row=0, column=0, sticky=tk.W, pady=3
		)
		ttk.Entry(body, textvariable=self._wrapper, width=52).grid(
			row=0, column=1, sticky=tk.EW, pady=3
		)
		ttk.Label(
			body,
			text=(
				"Prepended to every process command at launch.\n"
				"Use {cmd} as placeholder, e.g.:\n"
				"bash -c '. \"$HOME/.nvm/nvm.sh\" && exec {cmd}'"
			),
			justify=tk.LEFT,
		).grid(row=1, column=0, columnspan=2, sticky=tk.W, pady=(0, 4))

		# grise si aucune icone de tray n'est active (ancrage confirme)
		tray_ok = bool(getattr(self._win, "_tray_live", False))
		ttk.Checkbutton(
			body,
			text="Minimize to system tray",
			variable=self._to_tray,
			state=tk.NORMAL if tray_ok else tk.DISABLED,
		).grid(row=2, column=0, columnspan=2, sticky=tk.W, pady=(4, 0))

		ttk.Label(body, text="On quit:").grid(row=3, column=0, sticky=tk.W)
		ttk.Combobox(
			body,
			textvariable=self._quit_action,
			values=tuple(self._QUIT_LABELS.values()),
			state="readonly",
			width=26,
		).grid(row=3, column=1, sticky=tk.W)

		ttk.Label(body, text="Ports in use (live):").grid(
			row=4, column=0, columnspan=2, sticky=tk.W, pady=(8, 2)
		)
		self._ports = ttk.Treeview(
			body,
			columns=("port", "proc", "name", "pid"),
			show="headings",
			height=7,
		)
		for col, label, wd in (
			("port", "Port", 70),
			("proc", "Process", 170),
			("name", "Name", 110),
			("pid", "PID", 70),
		):
			self._ports.heading(col, text=label)
			self._ports.column(col, width=wd)
		sb = ttk.Scrollbar(body, orient=tk.VERTICAL, command=self._ports.yview)
		self._ports.configure(yscrollcommand=sb.set)
		self._ports.grid(
			row=5, column=0, columnspan=2, sticky=tk.NSEW
		)
		sb.grid(row=5, column=2, sticky=tk.NS)
		self._refresh_ports()

		self._ok_cancel(body, 6)
		self.bind("<Return>", lambda _e: self._ok())

	def _refresh_ports(self) -> None:
		if not self.winfo_exists():
			return
		snap = getattr(self._win, "_snapshot", {})
		# port -> processus systeme qui ecoute dessus (node, postgres…)
		owners = {
			p.port: p for p in portscan.all_listening() if p.proto == "tcp"
		}
		rows = []
		for (app_name, proc_name), st in snap.items():
			for port in st.ports:
				info = owners.get(port)
				pid = (info.pid if info else None) or (
					st.pids[0] if st.pids else ""
				)
				rows.append((
					port,
					f"{app_name}/{proc_name}",
					info.process if info else "",
					pid,
				))
		self._ports.delete(*self._ports.get_children())
		for row in sorted(rows):
			self._ports.insert("", tk.END, values=row)
		self.after(4000, self._refresh_ports)

	def _ok(self) -> None:
		save_pref("cmd_wrapper", self._wrapper.get().strip())
		save_pref(
			"minimize_to_tray", "1" if self._to_tray.get() else "0"
		)
		save_pref(
			"quit_action",
			_key_of(self._QUIT_LABELS, self._quit_action.get(), "ask"),
		)
		self.destroy()
