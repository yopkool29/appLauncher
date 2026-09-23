#!/usr/bin/env python3
"""App Launcher - centralized multi-process application manager.

Launches "apps" made of several processes (bash commands,
docker compose, docker run...), with status, ports and central logs.
"""
import argparse
import logging
import os
import socket
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import default_config_path, default_logs_dir
from core.logging_helpers import setup_logging
from ui.main_window import MainWindow


def _instance_socket() -> Optional[socket.socket]:
	"""Single instance : si une instance ecoute deja, on lui envoie
	'show' et on rend None (le caller quitte) ; sinon socket serveur
	pret a recevoir les prochains relances."""
	path = Path(
		os.environ.get("XDG_RUNTIME_DIR", "/tmp")
	) / f"applauncher-{os.getuid()}.sock"
	try:
		s = socket.socket(socket.AF_UNIX)
		s.connect(str(path))
		s.sendall(b"show")
		s.close()
		print("already running — raised existing window")
		return None
	except OSError:
		pass
	try:
		path.unlink()  # socket stale d'un crash precedent
	except OSError:
		pass
	srv = socket.socket(socket.AF_UNIX)
	srv.bind(str(path))
	srv.listen(2)
	return srv


def main() -> None:
	parser = argparse.ArgumentParser(description="App Launcher")
	parser.add_argument(
		"--config",
		type=Path,
		default=default_config_path(),
		help="YAML file of the app catalog",
	)
	parser.add_argument(
		"--logs-dir",
		type=Path,
		default=default_logs_dir(),
		help="directory for process logs and applauncher.log",
	)
	args = parser.parse_args()
	setup_logging(args.logs_dir / "applauncher.log")
	srv = _instance_socket()
	if srv is None:
		return
	win = MainWindow(args.config, srv, args.logs_dir)
	win.mainloop()
	logging.getLogger(__name__).info("mainloop exited")
	# os._exit : saute la finalisation de l'interpreteur, qui peut bloquer
	# de facon intermittente (threads natifs gdbus/gtk du backend tray).
	os._exit(0)


if __name__ == "__main__":
	main()
