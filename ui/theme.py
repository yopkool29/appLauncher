"""Themes (sv_ttk / ttk natifs) et couleurs des tags."""
# mypy: disable-error-code="attr-defined,has-type"
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Dict, List, Tuple

from core.config import load_pref, save_pref
from ui.ansi_text import config_ansi_tags

try:
	import sv_ttk  # type: ignore
	_SV_TTK_OK = True
except ImportError:
	_SV_TTK_OK = False


TAG_COLORS = {
	"light": {
		"green": "#2e7d32",
		"orange": "#e65100",
		"blue": "#1565c0",
		"gray": "#757575",
		"red": "#c62828",
	},
	"dark": {
		"green": "#66bb6a",
		"orange": "#ffb74d",
		"blue": "#64b5f6",
		"gray": "#9e9e9e",
		"red": "#ef5350",
	},
}


class ThemeMixin(tk.Tk):
	def _init_themes(self) -> None:
		self._style = ttk.Style()
		self._themes: List[Tuple[str, str]] = []
		if _SV_TTK_OK:
			self._themes += [("Dark", "sv:dark"), ("Light", "sv:light")]
		native = self._style.theme_use()
		self._native_bg = self.cget("bg")
		names = [native] + [
			n for n in self._style.theme_names() if n != native
		]
		self._themes += [
			("Default" if n == native else n.capitalize(), f"ttk:{n}")
			for n in names
		]
		theme = load_pref("theme", "sv:dark")
		if theme in ("dark", "light"):
			theme = f"sv:{theme}"
		ids = [tid for _, tid in self._themes]
		self._theme = theme if theme in ids else ids[0]
		self._theme_var = tk.StringVar(
			value=dict((tid, lbl) for lbl, tid in self._themes)[self._theme]
		)

	def _tag_colors(self) -> Dict[str, str]:
		return TAG_COLORS["dark" if self._theme == "sv:dark" else "light"]

	def _on_theme_select(self) -> None:
		tid = dict(self._themes).get(self._theme_var.get())
		if tid:
			self._set_theme(tid)
			save_pref("theme", tid)

	def _set_theme(self, theme_id: str) -> None:
		self._theme = theme_id
		kind, _, name = theme_id.partition(":")
		if kind == "sv" and _SV_TTK_OK:
			sv_ttk.set_theme(name)
		elif kind == "ttk":
			self._style.theme_use(name)
			self.tk.call("tk_setPalette", self._native_bg)
		self._apply_tk_colors()

	def _apply_tk_colors(self) -> None:
		dark = self._theme == "sv:dark"
		self.configure(
			bg={"sv:dark": "#1c1c1c", "sv:light": "#fafafa"}.get(
				self._theme, self._native_bg
			)
		)
		for tab in self._log_tabs.values():
			self._style_log_widget(tab["text"])
		for opt, color in (
			("*Menu.background", "#2b2b2b" if dark else "#f0f0f0"),
			("*Menu.foreground", "#fafafa" if dark else "#000000"),
			("*Menu.activeBackground", "#3f3f46" if dark else "#cce4ff"),
			("*Menu.activeForeground", "#ffffff" if dark else "#000000"),
			("*Menu.disabledForeground", "#6d6d6d" if dark else "#a3a3a3"),
		):
			self.option_add(opt, color)
		for tag, color in self._tag_colors().items():
			self.tree.tag_configure(tag, foreground=color)
		self.ports_tree.tag_configure(
			"managed", foreground=self._tag_colors()["green"]
		)

	def _style_log_widget(self, w: tk.Text) -> None:
		"""Couleurs + tags ANSI d'un widget log (sous-onglet proc)."""
		dark = self._theme == "sv:dark"
		w.configure({
			"bg": "#1c1c1c" if dark else "#ffffff",
			"fg": "#fafafa" if dark else "#000000",
			"insertbackground": "#fafafa" if dark else "#000000",
			"selectbackground": "#264f78" if dark else "#cfe8ff",
		})
		config_ansi_tags(w, dark)
