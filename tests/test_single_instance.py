"""Tests du single-instance : applauncher._instance_socket + listener.

Run : python3 -m unittest discover -s tests -v   (depuis la racine du repo)
Le socket vit dans un XDG_RUNTIME_DIR temporaire -> pas d'interference
avec une instance reelle.
"""
import os
import queue
import socket
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import applauncher
from ui.tray import TrayMixin


class SingleInstanceTest(unittest.TestCase):
	def setUp(self):
		self._dir = tempfile.mkdtemp()
		self._old = os.environ.get("XDG_RUNTIME_DIR")
		os.environ["XDG_RUNTIME_DIR"] = self._dir
		self._path = Path(self._dir) / f"applauncher-{os.getuid()}.sock"
		self._socks: list = []

	def tearDown(self):
		for s in self._socks:
			s.close()
		try:
			self._path.unlink()
		except OSError:
			pass
		if self._old is None:
			del os.environ["XDG_RUNTIME_DIR"]
		else:
			os.environ["XDG_RUNTIME_DIR"] = self._old

	def _sock(self):
		s = applauncher._instance_socket()
		if s is not None:
			self._socks.append(s)
		return s

	def test_first_instance_binds(self):
		srv = self._sock()
		self.assertIsNotNone(srv)
		self.assertTrue(self._path.exists())

	def test_second_instance_signals_and_exits(self):
		srv = self._sock()
		srv.settimeout(2)
		# second lancement : rend None et envoie 'show' au premier
		self.assertIsNone(applauncher._instance_socket())
		conn, _ = srv.accept()
		self.assertEqual(conn.recv(16), b"show")
		conn.close()

	def test_stale_socket_file_rebound(self):
		# fichier socket reste apres un crash -> rien n'ecoute dessus
		self._path.touch()
		srv = self._sock()
		self.assertIsNotNone(srv)  # unlinked puis rebinde

	def test_listener_queues_raise(self):
		srv = self._sock()
		stub = SimpleNamespace(
			_instance_sock=srv, _alive=True, _queue=queue.Queue()
		)
		TrayMixin._start_instance_listener(stub)
		# simule le 'show' d'un second lancement
		s = socket.socket(socket.AF_UNIX)
		s.connect(str(self._path))
		s.sendall(b"show")
		s.close()
		self.assertEqual(stub._queue.get(timeout=2), ("raise", None))


if __name__ == "__main__":
	unittest.main()
