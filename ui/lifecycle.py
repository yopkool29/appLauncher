"""Fermeture de l'app (confirmations, arret des procs, watchdogs)
et listener single-instance (socket unix -> 'raise')."""
# mypy: disable-error-code="attr-defined,has-type"
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
import tkinter as tk

from core import ports as portscan
from core.config import load_pref
from core.manager import STATUS_RUNNING
from ui.dialogs import L, confirm

log = logging.getLogger(__name__)


class LifecycleMixin(tk.Tk):
	# ---------------- Single instance ----------------

	def _start_instance_listener(self) -> None:
		"""Un second lancement envoie 'show' sur le socket unix :
		on remontre la fenetre existante au lieu d'ouvrir un doublon."""
		srv = self._instance_sock
		if srv is None:
			return

		def loop() -> None:
			while self._alive:
				try:
					conn, _ = srv.accept()
				except OSError:
					return
				conn.close()
				self._queue.put(("raise", None))

		threading.Thread(target=loop, daemon=True).start()

	def _raise_existing(self) -> None:
		"""Relance detectee : restore + focus la fenetre existante."""
		self._restore()
		self.focus_force()

	# ---------------- Fermeture ----------------

	def _on_close(self) -> None:
		if self._closing or self._confirming:
			return
		# snapshot (statuts live) plutot que running_count : celui-ci
		# ne compte que les runtimes traces — les panes adoptes et les
		# conteneurs docker (popen deja sorti) passeraient a cote
		n = sum(
			s.state == STATUS_RUNNING
			for s in self._snapshot.values()
		)
		# ports des processus suivis encore en ecoute (procs externes,
		# conteneurs docker survivants...)
		busy = sorted(
			{p for st in self._snapshot.values() for p in st.ports}
		)
		log.info(f"close requested ({n} running, ports busy: {busy})")
		action = load_pref("quit_action", "")
		if not action:  # migration de l'ancien bool confirm_quit
			action = (
				"ask" if load_pref("confirm_quit", "1") == "1" else "leave"
			)
		if action == "stop" and n > 0:
			# stop tout (panes tmux compris) puis quitte, sans dialogue
			self._stop_and_quit()
			return
		if action == "ask":
			if self.state() != "normal":
				self._restore()
			self.lift()
			self.focus_force()
			self.update_idletasks()
			self._confirming = True
			try:
				if n > 0:
					answer = confirm(
						self, L("quit"),
						L("quit_running", n=n),
						("yes", "no", "cancel"), "question",
					)
					log.info(f"close confirmation: {answer}")
					if answer == "cancel":
						return
					if answer == "yes":
						self._stop_and_quit()
						return
				else:
					msg = L(
						"quit_busy",
						ports=", ".join(str(p) for p in busy),
					) if busy else L("quit_ask")
					if confirm(
						self, L("quit"), msg,
						("ok", "cancel"), "question",
					) != "ok":
						return
			finally:
				self._confirming = False
		log.info("destroying window")
		self._destroy_now()

	def _stop_and_quit(self) -> None:
		# la fenetre reste normale : le user voit les statuts passer
		# a stopped pendant le polling de _wait_all_down, mais toute
		# l'interface est gelee (tk busy) pour eviter les interactions
		self._closing = True
		try:
			self.tk.call("tk", "busy", "hold", self._w)
		except tk.TclError:
			pass  # tk busy indispo -> degrade gracieux
		self._run_action(self._stop_apps_then_quit)
		self.after(35000, self._force_quit)

	def _stop_apps_then_quit(self) -> None:
		log.info("shutdown: stopping processes")
		try:
			threads = [
				threading.Thread(
					target=self.manager.stop,
					args=(app, proc),
					name=f"stop:{app.name}/{proc.name}",
					daemon=True,
				)
				for app in self.apps
				for proc in app.processes
			]
			for t in threads:
				t.start()
			deadline = time.time() + 30.0
			for t in threads:
				t.join(timeout=max(0.0, deadline - time.time()))
			stuck = [t.name for t in threads if t.is_alive()]
			if stuck:
				log.warning(f"stops still running after deadline: {stuck}")
			self._wait_all_down()
		finally:
			self._queue.put(("quit", None))
			log.info("shutdown: quit signal sent")

	def _wait_all_down(self, timeout: float = 20.0) -> None:
		"""Poll jusqu'a ce que tout soit vraiment ferme : stop() rend
		la main des que sa commande a tourne, pas quand le process
		meurt et libere ses ports. A l'echeance, SIGKILL sur les pid
		encore sur les ports declares (SIGTERM ignore)."""
		def busy() -> list:
			return [
				f"{app.name}/{proc.name}"
				for app in self.apps
				for proc in app.processes
				if self.manager.is_tracked(app, proc)
				or any(portscan.port_listening(p) for p in proc.ports)
			]
		deadline = time.time() + timeout
		left = busy()
		while left and time.time() < deadline:
			time.sleep(0.5)
			left = busy()
		if not left:
			return
		for app in self.apps:
			for proc in app.processes:
				for pid in portscan.pids_on_ports(proc.ports):
					try:
						os.kill(pid, signal.SIGKILL)
					except (ProcessLookupError, PermissionError):
						pass
		log.warning(f"shutdown: force-killed stragglers: {left}")

	def _destroy_now(self, idle: bool = False) -> None:
		"""Chemin unique de destruction : watchdog externe + destroy."""
		self._closing = True
		self._alive = False
		self._wake.set()  # reveille le poller pour sortie immediate
		if self._instance_sock is not None:
			try:
				path = self._instance_sock.getsockname()
				self._instance_sock.close()  # debloque accept()
				os.unlink(path)
			except OSError:
				pass
		self._hard_exit_watchdog()
		if idle:
			self.after_idle(self.destroy)
			return
		try:
			self.destroy()
		except tk.TclError:
			pass

	def _force_quit(self) -> None:
		log.warning("35s watchdog: forced destroy")
		self._destroy_now()

	def _hard_exit_watchdog(self, delay: float = 5.0) -> None:
		"""os._exit sur thread + kill -9 par processus externe : destroy()
		peut bloquer de facon intermittente (verrous X11 entre Tk et le
		backend tray GTK) en gardant le GIL, ce qui empecherait le
		Timer de s'executer. Le processus externe est immunise."""
		pid = os.getpid()
		try:
			subprocess.Popen(
				[
					"sh", "-c",
					f"sleep {delay}; "
					f"grep -q applauncher /proc/{pid}/cmdline "
					f"2>/dev/null && kill -9 {pid}",
				],
				stdin=subprocess.DEVNULL,
				stdout=subprocess.DEVNULL,
				stderr=subprocess.DEVNULL,
				start_new_session=True,
			)
		except OSError:
			pass

		def _kill() -> None:
			log.warning("destroy blocked: os._exit(0)")
			os._exit(0)

		t = threading.Timer(delay, _kill)
		t.daemon = True
		t.start()
