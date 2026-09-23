"""Tests config : section 'window:' + preservation par save_config.

Run : python3 -m unittest discover -s tests -v   (depuis la racine du repo)
"""
import tempfile
import unittest
from pathlib import Path

import yaml  # type: ignore

from core.config import load_window_config, save_config


class WindowConfigTest(unittest.TestCase):
	def setUp(self):
		self._path = Path(tempfile.mkdtemp()) / "apps.yaml"

	def _write(self, text: str) -> None:
		self._path.write_text(text)

	def test_load_window_config(self):
		self._write("window:\n  min_width: 1000\n  min_height: 600\napps: []\n")
		w = load_window_config(self._path)
		self.assertEqual(w["min_width"], 1000)
		self.assertEqual(w["min_height"], 600)

	def test_missing_section(self):
		self._write("apps: []\n")
		self.assertEqual(load_window_config(self._path), {})

	def test_invalid_yaml(self):
		self._write("{{{ not yaml")
		self.assertEqual(load_window_config(self._path), {})

	def test_save_config_preserves_window(self):
		self._write(
			"window:\n  min_width: 999\napps:\n"
			"  - name: x\n    processes: []\n"
		)
		from core.config import load_config
		save_config(self._path, load_config(self._path))
		data = yaml.safe_load(self._path.read_text())
		self.assertEqual(data["window"]["min_width"], 999)
		self.assertEqual(data["apps"][0]["name"], "x")


if __name__ == "__main__":
	unittest.main()
