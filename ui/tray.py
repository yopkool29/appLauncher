"""Icone de tray (pystray), minimisation/restauration et fermeture."""
# mypy: disable-error-code="attr-defined,has-type"
from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path

from core import ports as portscan
from core.config import load_pref
from core.manager import STATUS_RUNNING
from ui.dialogs import L, confirm

try:
	import pystray  # type: ignore
	_TRAY_OK = True
except ImportError:
	_TRAY_OK = False

log = logging.getLogger(__name__)

ICON_PATH = Path(__file__).resolve().parent.parent / "icon.png"


def _xembed_tray_present() -> bool:
	"""Un tray XEmbed tourne si _NET_SYSTEM_TRAY_S<screen> a un owner."""
	try:
		from Xlib import display  # type: ignore
		d = display.Display()
		try:
			atom = d.intern_atom(
				f"_NET_SYSTEM_TRAY_S{d.get_default_screen()}"
			)
			return bool(d.get_selection_owner(atom))
		finally:
			d.close()
	except Exception:
		return False


def _sni_watcher_present() -> bool:
	"""org.kde.StatusNotifierWatcher sur le bus session = un tray
	StatusNotifier/appindicator est actif."""
	try:
		out = subprocess.run(
			[
				"gdbus", "call", "--session",
				"--dest", "org.freedesktop.DBus",
				"--object-path", "/org/freedesktop/DBus",
				"--method", "org.freedesktop.DBus.NameHasOwner",
				"org.kde.StatusNotifierWatcher",
			],
			capture_output=True, text=True, timeout=3,
		).stdout
		return "true" in out
	except (OSError, subprocess.SubprocessError):
		return False


def _icon_cls():
	"""Backend gtk (GtkStatusIcon) si un tray XEmbed existe : clic gauche
	= activation de l'item par defaut (Show), clic droit = menu.
	Sinon backend par defaut de pystray si un watcher SNI tourne
	(appindicator : menu sur clic, les deux boutons identiques).
	None si aucun tray n'est disponible."""
	if os.getenv("PYSTRAY_BACKEND"):
		return pystray.Icon
	if _xembed_tray_present():
		try:
			from pystray import _gtk
			return _gtk.Icon
		except ImportError:
			pass
	if _sni_watcher_present():
		return pystray.Icon
	return None


class TrayMixin(tk.Tk):
	def _setup_tray(self) -> None:
		if not _TRAY_OK or not ICON_PATH.exists():
			return
		try:
			from PIL import Image  # type: ignore
			image = Image.open(ICON_PATH)
		except (ImportError, OSError):
			return
		cls = _icon_cls()
		if cls is None:
			return
		menu = pystray.Menu(
			pystray.MenuItem(
				"Show / Hide",
				lambda *_: self._queue.put(("tray_show", None)),
				default=True,
			),
			pystray.MenuItem(
				"Quit", lambda *_: self._queue.put(("tray_quit", None))
			),
		)
		self._tray = cls(
			"applauncher", image, "App Launcher", menu
		)
		threading.Thread(target=self._run_tray, daemon=True).start()

	def _run_tray(self) -> None:
		tray = self._tray
		if tray is None:
			return

		def _setup(icon) -> None:
			icon.visible = True
			si = getattr(icon, "_status_icon", None)
			if si is None:
				# appindicator : pas de verif d'ancrage possible,
				# le watcher SNI a deja ete detecte
				self._tray_live = True
				return
			for _ in range(30):  # ~3s pour l'ancrage XEmbed
				if si.is_embedded():
					self._tray_live = True
					return
				time.sleep(0.1)
			# jamais ancre : _tray_live reste False -> minimise
			# classique, icone invisible nettoyee au destroy

		try:
			tray.run(_setup)
		except Exception:
			self._tray = None
			# evite une fenetre iconic + invisible sans icone pour la
			# restaurer : revient a une minimisation classique
			self._queue.put(("tray_show", None))
		finally:
			self._tray_live = False

	def _on_unmap(self, event) -> None:
		if event.widget is not self or not self._alive or self._closing:
			return
		self._minimized = True  # fenetre cachee -> polling ralenti
		if (
			self.state() == "iconic"
			and self._tray_live
			and load_pref("minimize_to_tray", "1") == "1"
		):
			# reste 'iconic' mais invisible dans la taskbar : pas
			# de withdraw, la geometrie est conservee par le WM.
			# Les toplevels 'transient' suivent la minimisation
			# nativement.
			self._set_skip_taskbar(True)

	def _toggle_window(self) -> None:
		"""Clic sur l'icone : masque la fenetre si elle est visible
		(iconify -> _on_unmap pose SKIP_TASKBAR quand le tray est
		live), la restaure sinon (iconic/withdrawn)."""
		if self.state() in ("normal", "zoomed"):
			self.iconify()
		else:
			self._restore()

	def _on_map(self, event) -> None:
		if event.widget is self:
			self._minimized = False
			self._wake.set()  # reveille le poller : refresh immediat

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

	def _restore(self) -> None:
		self._set_skip_taskbar(False)
		self.deiconify()
		self.lift()

	def _set_skip_taskbar(self, enable: bool) -> None:
		"""_NET_WM_STATE_SKIP_TASKBAR via ClientMessage EWMH au root :
		la fenetre disparait de la barre des taches sans withdraw."""
		try:
			from Xlib import X, display, protocol  # type: ignore
			d = display.Display()
			# le client vu par le WM/taskbar est le toplevel X : le
			# parent de la fenetre widget renvoyee par winfo_id()
			win = d.create_resource_object(
				"window", self.winfo_id()
			).query_tree().parent
			ev = protocol.event.ClientMessage(
				window=win,
				client_type=d.intern_atom("_NET_WM_STATE"),
				data=(32, [
					1 if enable else 0,  # _NET_WM_STATE_ADD / REMOVE
					d.intern_atom("_NET_WM_STATE_SKIP_TASKBAR"),
					0, 1, 0,
				]),
			)
			d.screen().root.send_event(
				ev,
				event_mask=X.SubstructureRedirectMask
				| X.SubstructureNotifyMask,
			)
			d.sync()
		except Exception:
			# pas de Xlib / pas de X11 -> minimise classique, degrade propre
			pass

	def _on_destroy(self, event) -> None:
		if event.widget is self and self._tray is not None:
			tray, self._tray = self._tray, None
			# pystray (backend appindicator/gtk) ecrit l'icone dans
			# tempfile.mktemp() ; _finalize est saute par os._exit ->
			# on supprime le fichier nous-memes pour eviter les fuites.
			icon_path = getattr(tray, "_icon_path", None)
			if icon_path:
				try:
					os.unlink(icon_path)
				except OSError:
					pass

			def _stop_tray() -> None:
				try:
					tray.stop()
					log.info("tray stopped")
				except Exception:
					log.exception("tray.stop failed")

			threading.Thread(target=_stop_tray, daemon=True).start()

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

	# ---------------- Fermeture ----------------

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
