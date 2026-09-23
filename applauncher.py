#!/usr/bin/env python3
"""App Launcher - centralized multi-process application manager.

Launches "apps" made of several processes (bash commands,
docker compose, docker run...), with status, ports and central logs.
"""
import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import default_config_path, default_logs_dir
from core.logging_helpers import setup_logging
import core.manager as manager
from ui.main_window import MainWindow


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
	manager.LOGS_DIR = args.logs_dir
	setup_logging(args.logs_dir / "applauncher.log")
	win = MainWindow(args.config)
	win.mainloop()
	logging.getLogger(__name__).info("mainloop exited")
	# os._exit : saute la finalisation de l'interpreteur, qui peut bloquer
	# de facon intermittente (threads natifs gdbus/gtk du backend tray).
	os._exit(0)


if __name__ == "__main__":
	main()
