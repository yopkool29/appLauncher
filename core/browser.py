"""Navigateur : detection des .desktop, focus de fenetre, ouverture d'URL."""
from __future__ import annotations

import configparser
import logging
import re
import shlex
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import App

log = logging.getLogger(__name__)


def detect_browsers() -> Dict[str, List[str]]:
	"""Navigateurs installes : label -> argv, detectes via les fichiers
	.desktop ayant 'WebBrowser' dans Categories (paquets, flatpak, snap)."""
	dirs = (
		Path.home() / ".local/share/applications",
		Path("/usr/local/share/applications"),
		Path("/usr/share/applications"),
		Path("/var/lib/snapd/desktop/applications"),
		Path("/var/lib/flatpak/exports/share/applications"),
		Path.home() / ".local/share/flatpak/exports/share/applications",
	)
	browsers: Dict[str, List[str]] = {}
	for d in dirs:
		for f in sorted(d.glob("*.desktop")) if d.is_dir() else ():
			try:
				p = configparser.ConfigParser(
					interpolation=None, strict=False
				)
				# les cles .desktop sont sensibles a la casse
				p.optionxform = str  # type: ignore[assignment,method-assign]
				p.read(f, encoding="utf-8")
				e = p["Desktop Entry"]
				if (
					e.get("NoDisplay", "false").lower() == "true"
					or e.get("Hidden", "false").lower() == "true"
					or "webbrowser"
					not in e.get("Categories", "").lower().split(";")
				):
					continue
				# strip les field codes freedesktop (%u, %U, %f...)
				argv = shlex.split(
					re.sub(
						r"%[a-zA-Z]", "", e.get("Exec", "")
					).replace("%%", "%")
				)
				if e.get("Name") and argv:
					browsers.setdefault(e["Name"], argv)
			except (OSError, configparser.Error, KeyError, ValueError):
				continue
	return browsers


_BROWSERS: Optional[Dict[str, List[str]]] = None


def browsers() -> Dict[str, List[str]]:
	"""Acces lazy au scan .desktop : pas d'IO a l'import du module."""
	global _BROWSERS
	if _BROWSERS is None:
		_BROWSERS = detect_browsers()
	return _BROWSERS


def app_urls(app: App) -> List[Tuple[str, bool]]:
	"""URLs ouvertes par 'Open URLs' / Launch : url + launch_port de
	l'app en tete (fenetre), puis chaque port des procs dont le mode
	browser n'est pas 'none' (dans leur propre mode). Dedoublonne."""
	urls: List[Tuple[str, bool]] = []
	seen = set()

	def add(u: str, tab: bool) -> None:
		if u and u not in seen:
			seen.add(u)
			urls.append((u, tab))

	# ports des procs 'none'/'manual' : bloque aussi url/launch_port
	# app-level qui pointeraient dessus — 'manual' ne s'ouvre que via
	# Open :port sur le processus, jamais avec Open URLs
	blocked = {
		p for proc in app.processes
		if proc.browser_mode in ("none", "manual")
		for p in proc.ports
	}

	def port_tab(port: int) -> bool:
		"""Mode d'ouverture d'un port : celui du proc qui le declare
		(une url app-level pointant sur un proc 'tab' s'ouvre en
		onglet, sinon fenetre par defaut)."""
		return any(
			port in proc.ports and proc.browser_mode == "tab"
			for proc in app.processes
		)

	m = re.search(r":(\d+)(?:[/:?#]|$)", app.url)
	url_port = int(m.group(1)) if m else 0
	if app.url and url_port not in blocked:
		add(app.url, port_tab(url_port))
	if app.launch_port and app.launch_port not in blocked:
		add(f"http://localhost:{app.launch_port}",
		    port_tab(app.launch_port))
	for proc in app.processes:
		if proc.browser_mode in ("none", "manual"):
			continue
		for p in proc.ports:
			add(f"http://localhost:{p}", proc.browser_mode == "tab")
	return urls


def _focus_latest_window(argv0: str) -> None:
	"""Focus la fenetre la plus recente du navigateur pour que
	--new-tab / l'URL s'y ouvre. _NET_CLIENT_LIST_STACKING est ordonne
	bas->haut : la derniere fenetre du navigateur est la plus
	recemment active. No-op silencieux sans wmctrl/xprop."""
	try:
		out = subprocess.run(
			["xprop", "-root", "_NET_CLIENT_LIST_STACKING"],
			capture_output=True, text=True, timeout=3,
		).stdout
		# ids en int : xprop '0x2200004' vs wmctrl '0x02200004'
		ids = [int(w, 16) for w in re.findall(r"0x[0-9a-fA-F]+", out)]
		if not ids:
			return
		cls_of = {}
		for line in subprocess.run(
			["wmctrl", "-lx"], capture_output=True, text=True, timeout=3
		).stdout.splitlines():
			parts = line.split(None, 3)
			if len(parts) >= 3:
				try:
					cls_of[int(parts[0], 16)] = parts[2].lower()
				except ValueError:
					continue
		key = Path(argv0).name.lower()
		stem = key.split("-")[0]  # 'google-chrome' -> 'google'
		for wid in reversed(ids):
			cls = cls_of.get(wid, "")
			if key in cls or stem in cls:
				subprocess.run(
					["wmctrl", "-i", "-a", hex(wid)],
					stdout=subprocess.DEVNULL,
					stderr=subprocess.DEVNULL,
					timeout=3,
				)
				return
	except (OSError, subprocess.TimeoutExpired):
		pass


def open_url(url: str, argv: List[str], tab: bool = False) -> None:
	"""Lance le navigateur (argv detecte) sur url, nouvelle fenetre ou
	nouvel onglet. En mode tab : focus d'abord la fenetre la plus
	recente — l'onglet s'ouvre dans la fenetre active. --new-tab est
	explicite pour Gecko ET Chromium (kNewTab ; les vieux Chromium
	l'ignorent -> URL nue = tab). Sans fenetre ouverte, le navigateur
	demarre simplement."""
	args = argv + (["--new-tab", url] if tab else ["--new-window", url])
	if tab:
		_focus_latest_window(argv[0])
	log.info("opening %s -> %s", " ".join(args[:-1]), url)
	subprocess.Popen(
		args,
		stdin=subprocess.DEVNULL,
		stdout=subprocess.DEVNULL,
		stderr=subprocess.DEVNULL,
		start_new_session=True,
	)
