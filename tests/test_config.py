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


class ProfileConfigTest(unittest.TestCase):
	"""Profiles nommes : for_profile/eff/effective_procs + round-trip."""

	def test_proc_override_fields_and_inherit(self):
		from core.config import Process
		p = Process(
			name="fe", cmd="dev", workdir="$HOME/x", ports=[5173],
			tmux=True,
			configs={"prod": {
				"cmd": "serve", "ports": ["4173"], "tmux": False,
			}},
		)
		e = p.for_profile("prod")
		self.assertEqual(e.cmd, "serve")
		self.assertEqual(e.ports, [4173])       # strings -> ints
		self.assertFalse(e.tmux)
		self.assertTrue(e.workdir.endswith("/x"))  # herite + expande
		self.assertEqual(e.configs, {})          # pas de recursion
		self.assertIs(p.for_profile(""), p)
		self.assertIs(p.for_profile("default"), p)
		self.assertIs(p.for_profile("absent"), p)
		self.assertEqual(p.ports, [5173])        # base intacte

	def test_proc_override_workdir_expands(self):
		import os
		from core.config import Process
		p = Process(
			name="x", cmd="c",
			configs={"prod": {"workdir": "~/w"}},
		)
		self.assertEqual(
			p.for_profile("prod").workdir,
			os.path.expanduser("~/w"),
		)

	def test_app_eff_suffix_profiles(self):
		from core.config import App, Process
		a = App(
			name="cp", url="http://dev", launch_port=5173,
			profile="prod",
			configs={"prod": {"url": "http://prod", "launch_port": "443"}},
			processes=[
				Process(name="fe", cmd="c", configs={"prod": {}}),
				Process(name="be", cmd="c"),
			],
		)
		self.assertEqual(a.eff("url"), "http://prod")
		self.assertEqual(a.eff("launch_port"), "443")
		self.assertEqual(a.suffix(), "[prod]")
		self.assertEqual(a.profiles(), ["prod"])
		self.assertEqual(len(a.effective_procs()), 2)
		a.profile = ""
		self.assertEqual(a.eff("url"), "http://dev")
		self.assertEqual(a.suffix(), "")

	def test_roundtrip(self):
		from core.config import App, Process
		a = App(
			name="cp", profile="prod",
			configs={"prod": {"url": "u"}},
			processes=[Process(
				name="p", cmd="c",
				configs={"prod": {"cmd": "c2", "ports": [1]}},
			)],
		)
		a2 = App.from_dict(a.to_dict())
		self.assertEqual(a2.profile, "prod")
		self.assertEqual(a2.configs, a.configs)
		self.assertEqual(a2.processes[0].configs, a.processes[0].configs)
		# 'default' jamais serialise comme profile
		a.profile = "default"
		self.assertNotIn("profile", a.to_dict())

	def test_valid_profile_max_len(self):
		from core.config import valid_profile
		self.assertTrue(valid_profile("prod"))
		self.assertTrue(valid_profile("a" * 10))
		self.assertFalse(valid_profile("a" * 11))
		self.assertFalse(valid_profile(""))
		self.assertFalse(valid_profile("a b"))

	def test_configs_filter_invalid_names(self):
		from core.config import Process
		p = Process.from_dict({
			"name": "x", "cmd": "c",
			"configs": {
				"ok": {"cmd": "y"},
				"default": {"cmd": "z"},   # reserve : ignore
				"bad name": {"cmd": "w"},  # invalide : ignore
			},
		})
		self.assertEqual(list(p.configs), ["ok"])


if __name__ == "__main__":
	unittest.main()
