"""
Logging configuration with emoji support for Windows console.
"""

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional, Tuple


def tail_file(path: Path, max_bytes: int) -> Optional[Tuple[str, int]]:
	"""(contenu des derniers max_bytes, taille reelle) d'un fichier ;
	None s'il est absent ou illisible."""
	try:
		size = path.stat().st_size
		with open(path, "rb") as f:
			f.seek(max(0, size - max_bytes))
			return f.read().decode("utf-8", errors="replace"), size
	except OSError:
		return None


class CleanFormatter(logging.Formatter):
	"""
	Formatter that removes emojis and ANSI color codes from messages.

	Used for Windows console (cp1252) and clean log files.
	"""

	EMOJI_PATTERN = re.compile(
		"["
		"\U0001f600-\U0001f64f"
		"\U0001f300-\U0001f5ff"
		"\U0001f680-\U0001f6ff"
		"\U0001f1e0-\U0001f1ff"
		"\U00002702-\U000027b0"
		"\U000024c2-\U0001f251"
		"]+",
		flags=re.UNICODE,
	)
	ANSI_PATTERN = re.compile(r"\x1b\[[0-9;]*m")

	def format(self, record):
		"""Removes emojis and ANSI codes from message before formatting."""
		record.msg = self.EMOJI_PATTERN.sub("", str(record.msg))
		record.msg = self.ANSI_PATTERN.sub("", record.msg)
		return super().format(record)


def setup_logging(
	log_file: Optional[Path] = None,
	log_level: int = logging.INFO,
	logger_name: Optional[str] = None,
	console: bool = True,
	stream=None,
) -> logging.Logger:
	"""
	Configures logging with emoji support.

	- Log file: UTF-8, rotation 2MB x 5 files
	- Console: sans emojis (évite UnicodeEncodeError sur Windows)

	Args:
		log_file: Path to log file — n'importe quel chemin absolu, ou
			relatif (résolu depuis le cwd). None → ./logs/app.log du
			répertoire courant (= logs local à l'app qui lance).
			Ex. depuis Azur : Path(__file__).parent / "logs" / "azur.log".
		log_level: Log level (default: INFO)
		logger_name: Logger name (default: root logger)
		console: Ajouter un handler console (default: True)
		stream: Stream console — None → stdout. Passer sys.stderr (ou
			console=False) pour un serveur MCP stdio : stdout = protocole.

	Returns:
		Configured logger
	"""
	if log_file is None:
		log_file = Path.cwd() / "logs" / "app.log"
	log_file.parent.mkdir(parents=True, exist_ok=True)

	log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

	# File handler with rotation (2MB, 5 backups)
	file_handler = RotatingFileHandler(
		log_file, mode="a", maxBytes=2 * 1024 * 1024, backupCount=5, encoding="utf-8"
	)
	file_handler.setLevel(log_level)
	file_handler.setFormatter(CleanFormatter(log_format))

	# Configuration
	logger = logging.getLogger(logger_name)
	logger.setLevel(log_level)
	logger.handlers = []  # Clear existing handlers
	logger.addHandler(file_handler)

	if console:
		stream = stream if stream is not None else sys.stdout
		if hasattr(stream, "reconfigure"):
			stream.reconfigure(encoding="utf-8", errors="replace")
		console_handler = logging.StreamHandler(stream)
		console_handler.setLevel(log_level)
		console_handler.setFormatter(CleanFormatter(log_format))
		logger.addHandler(console_handler)

	logger.propagate = False

	return logger
