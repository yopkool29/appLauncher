"""Icone de tray (pystray) et minimisation/restauration de la fenetre."""
# mypy: disable-error-code="attr-defined,has-type"
from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
import tkinter as tk
from pathlib import Path

from core.config import load_pref

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
