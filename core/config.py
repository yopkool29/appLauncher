"""Chargement et sauvegarde du catalogue d'applications (YAML)."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import yaml


def safe_name(name: str) -> str:
	return re.sub(r"[^A-Za-z0-9_.-]+", "_", name)


@dataclass
class Process:
	name: str
	cmd: str
	workdir: str = ""
	stop: str = ""
	ports: List[int] = field(default_factory=list)
	docker: bool = False
	tmux: bool = False
	tmux_window: int = 0
	browser_mode: str = "window"  # window | tab | none (Open masque)

	def __post_init__(self) -> None:
		self.workdir = os.path.expandvars(os.path.expanduser(self.workdir))

	@property
	def is_docker(self) -> bool:
		return self.docker or "docker" in self.cmd

	@staticmethod
	def from_dict(data: dict) -> "Process":
		# migration : l'ancien same_window: true devient browser_mode: tab
		bmode = str(data.get("browser_mode", "") or "")
		if bmode not in ("window", "tab", "none", "manual"):
			bmode = "tab" if data.get("same_window") else "window"
		return Process(
			name=str(data.get("name", "processus")),
			cmd=str(data.get("cmd", "")),
			workdir=str(data.get("workdir", "")),
			stop=str(data.get("stop", "")),
			ports=[int(p) for p in data.get("ports", []) or []],
			docker=bool(data.get("docker", False)),
			tmux=bool(data.get("tmux", False)),
			tmux_window=int(data.get("tmux_window", 0) or 0),
			browser_mode=bmode,
		)

	def to_dict(self) -> dict:
		data: dict = {"name": self.name, "cmd": self.cmd}
		for key, val in {
			"workdir": self.workdir,
			"stop": self.stop,
			"ports": self.ports,
			"docker": self.docker,
			"tmux": self.tmux,
			"tmux_window": self.tmux_window,
			"browser_mode": (
				self.browser_mode if self.browser_mode != "window" else ""
			),
		}.items():
			if val:
				data[key] = val
		return data


@dataclass
class App:
	name: str
	alias: str = ""  # nom affiche dans le tree (name = identite interne)
	url: str = ""
	processes: List[Process] = field(default_factory=list)
	tmux_layout: str = ""  # "" = tiled | horizontal | vertical
	launch_port: int = 0  # port ouvert par Launch au niveau app (0 = url)
	tmux_attach: bool = True  # ouvrir un terminal quand un proc tmux demarre
	tmux_shared: bool = False  # True: onglet dans la session partagee 'al'
	                           # False: session dediee 'al-<app>'
	exclude_all: bool = False  # True: ignoree par Start all / Stop all
	color: str = ""  # hex "#rrggbb" -> petit carre colore dans le tree

	@property
	def label(self) -> str:
		"""Nom d'affichage : alias s'il est pose, sinon name."""
		return self.alias or self.name

	@staticmethod
	def from_dict(data: dict) -> "App":
		return App(
			name=str(data.get("name", "app")),
			alias=str(data.get("alias", "") or ""),
			url=str(data.get("url", "")),
			processes=[Process.from_dict(p) for p in data.get("processes", []) or []],
			tmux_layout=str(data.get("tmux_layout", "")),
			launch_port=int(data.get("launch_port", 0) or 0),
			tmux_attach=bool(data.get("tmux_attach", True)),
			tmux_shared=bool(data.get("tmux_shared", False)),
			exclude_all=bool(data.get("exclude_all", False)),
			color=str(data.get("color", "") or ""),
		)

	def to_dict(self) -> dict:
		data: dict = {"name": self.name}
		for key, val in {
			"alias": self.alias,
			"url": self.url,
			"tmux_layout": self.tmux_layout,
			"launch_port": self.launch_port,
			"tmux_shared": self.tmux_shared,
			"exclude_all": self.exclude_all,
			"color": self.color,
		}.items():
			if val:
				data[key] = val
		if not self.tmux_attach:
			data["tmux_attach"] = False
		data["processes"] = [p.to_dict() for p in self.processes]
		return data


TEMPLATE = """# App Launcher catalog
# An app = a name + a list of processes, each with its command line.
#
# apps:
#   - name: "My app"
#     alias: ""                           # optional: display name in the
#                                          # tree (name stays the identity)
#     url: "http://localhost:8000"        # optional (Launch button fallback)
#     launch_port: 5173                   # optional: port opened by Launch
#     tmux_layout: "tiled"                # tmux pane layout:
#                                          # tiled (grid) | horizontal
#                                          # (side by side) | vertical (stacked)
#     tmux_attach: true                   # open a terminal when a tmux
#                                          # process starts (default: true)
#     tmux_shared: false                  # true: app runs as a window (tab)
#                                          # in the shared 'al' session;
#                                          # false: dedicated 'al-<app>'
#                                          # session
#     exclude_all: false                  # true: ignored by the global
#                                          # Start all / Stop all buttons
#                                          # (manual start/stop only)
#     color: "#1e90ff"                    # optional: colored square shown
#                                          # next to the app in the tree
#     processes:
#       - name: "api"
#         cmd: "python3 -m http.server 8000"
#         workdir: "/tmp"                  # optional
#         stop: ""                         # custom stop command (optional,
#                                          # default: kill process group)
#         ports: [8000]                    # expected ports (optional)
#         docker: false                    # true if cmd spawns containers
#         tmux: false                      # true to run in a tmux pane
#                                          # (`tmux attach -t al-<app>`)
#         tmux_window: 0                   # window index inside the app
#                                          # session; same index = panes
#                                          # in the same window
#         browser_mode: window|tab|none|manual # optional (default window):
#                                          # how Open URLs / Open :port opens
#                                          # 'none' hides the Open entries;
#                                          # 'manual' opens only via Open :port
#                                          # on the process (never Open URLs)
#
#       - name: "compose stack"
#         cmd: "docker compose up -d"
#         stop: "docker compose down"
#         workdir: "/path/to/project"
#         ports: [8080, 5432]
#         docker: true
apps: []
"""


def _xdg(var: str, default: Path) -> Path:
	return Path(os.getenv(var) or default)


def default_config_path() -> Path:
	env = os.getenv("APPLAUNCHER_CONFIG")
	if env:
		return Path(env)
	repo = Path(__file__).resolve().parent.parent / "apps.yaml"
	if repo.exists():
		return repo  # checkout dev : config locale au projet
	xdg = _xdg("XDG_CONFIG_HOME", Path.home() / ".config")
	return xdg / "applauncher" / "apps.yaml"


def default_logs_dir() -> Path:
	"""logs/<app>/<proc>.log : env APPLAUNCHER_LOGS, sinon a cote du
	repo en dev, sinon XDG_STATE_HOME/applauncher/logs (installe)."""
	env = os.getenv("APPLAUNCHER_LOGS")
	if env:
		return Path(env)
	repo = Path(__file__).resolve().parent.parent
	if (repo / "apps.yaml").exists():
		return repo / "logs"  # checkout dev : logs locaux au projet
	xdg = _xdg("XDG_STATE_HOME", Path.home() / ".local/state")
	return xdg / "applauncher" / "logs"


def load_config(path: Path) -> List[App]:
	if not path.exists():
		path.parent.mkdir(parents=True, exist_ok=True)
		path.write_text(TEMPLATE)
		return []
	data = yaml.safe_load(path.read_text()) or {}
	return [App.from_dict(a) for a in data.get("apps", []) or []]


def load_window_config(path: Path) -> dict:
	"""Section 'window:' (legacy) : min_width, min_height — lue au
	demarrage uniquement pour migrer vers les prefs win_min_*."""
	try:
		data = yaml.safe_load(path.read_text()) or {}
	except (OSError, yaml.YAMLError):
		return {}
	w = data.get("window") if isinstance(data, dict) else None
	return w if isinstance(w, dict) else {}


def save_config(path: Path, apps: List[App]) -> None:
	header = "# App Launcher catalog - editable here or via the GUI\n"
	try:
		data = yaml.safe_load(path.read_text()) or {}
	except (OSError, yaml.YAMLError):
		data = {}
	if not isinstance(data, dict):
		data = {}
	data["apps"] = [a.to_dict() for a in apps]  # garde 'window:' etc.
	body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
	tmp = path.with_name(path.name + ".tmp")
	tmp.write_text(header + body)
	tmp.replace(path)  # rename atomique : pas de yaml tronque si crash


PREFS_PATH = (
	_xdg("XDG_CONFIG_HOME", Path.home() / ".config")
	/ "applauncher" / "prefs.json"
)


def _load_prefs() -> dict:
	try:
		data = json.loads(PREFS_PATH.read_text())
		return data if isinstance(data, dict) else {}
	except (OSError, json.JSONDecodeError):
		return {}


def _save_prefs(data: dict) -> None:
	try:
		PREFS_PATH.parent.mkdir(parents=True, exist_ok=True)
		PREFS_PATH.write_text(json.dumps(data))
	except OSError:
		pass


def load_pref(key: str, default: str) -> str:
	return str(_load_prefs().get(key, default))


def save_pref(key: str, value: str) -> None:
	data = _load_prefs()
	data[key] = value
	_save_prefs(data)
