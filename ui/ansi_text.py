"""Rendu ANSI -> tk.Text : couleurs, gras, dim, souligne, \r."""
from __future__ import annotations

import re
import tkinter as tk
import tkinter.font as tkfont
from typing import Set

_ANSI_RE = re.compile(
	r"\x1b(?:\[([0-9;?]*)([A-Za-z])|\[[0-9;?]*$|"
	r"\][^\x07\x1b]*(?:\x07|\x1b\\)|[()][0-9A-B]|.)",
	re.DOTALL,
)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

ANSI_DARK = (
	"#6e7681", "#ff7b72", "#3fb950", "#d29922",
	"#58a6ff", "#bc8cff", "#39c5cf", "#b1bac4",
	"#8b949e", "#ffa198", "#56d364", "#e3b341",
	"#79c0ff", "#d2a8ff", "#56d4dd", "#f0f6fc",
)
ANSI_LIGHT = (
	"#24292f", "#cf222e", "#116329", "#4d2d00",
	"#0969da", "#8250df", "#1b7c83", "#57606a",
	"#6e7781", "#a40e26", "#1a7f37", "#9a6700",
	"#218bff", "#a475f9", "#3192aa", "#8c959f",
)


def config_ansi_tags(w: tk.Text, dark: bool) -> None:
	"""Configure les tags statiques (16 couleurs + b/d/u) selon le theme."""
	pal = ANSI_DARK if dark else ANSI_LIGHT
	for i, c in enumerate(pal):
		w.tag_configure(f"af{i}", foreground=c)
		w.tag_configure(f"ag{i}", background=c)
	w.tag_configure("ad", foreground="#8b949e" if dark else "#9a9a9a")
	w.tag_configure("au", underline=True)
	bold = tkfont.Font(font=w.cget("font") or "TkFixedFont")
	bold.configure(weight="bold")
	w.tag_configure("ab", font=bold)


def _drop(st: Set[str], *prefixes: str) -> None:
	st.difference_update({t for t in st if t.startswith(prefixes)})


def _sgr(params: str, st: Set[str]) -> None:
	"""Applique une sequence SGR a l'ensemble de tags actifs."""
	codes = params.split(";") if params else ["0"]
	i = 0
	while i < len(codes):
		try:
			n = int(codes[i])
		except ValueError:
			n = 0
		if n == 0:
			st.clear()
		elif n in (1, 2, 4):
			st.add({1: "ab", 2: "ad", 4: "au"}[n])
		elif n == 22:
			_drop(st, "ab", "ad")
		elif n == 24:
			st.discard("au")
		elif n in (39, 49):
			_drop(st, *(("af", "ax", "ay") if n == 39 else ("ag", "aX", "az")))
		elif 30 <= n <= 37 or 90 <= n <= 97:
			_drop(st, "af", "ax", "ay")
			st.add(f"af{n - 30 if n < 40 else n - 82}")
		elif 40 <= n <= 47 or 100 <= n <= 107:
			_drop(st, "ag", "aX", "az")
			st.add(f"ag{n - 40 if n < 50 else n - 92}")
		elif n in (38, 48):
			fg = n == 38
			tag = ""
			if i + 2 < len(codes) and codes[i + 1] == "5":
				try:
					v = int(codes[i + 2])
				except ValueError:
					v = -1
				if 0 <= v <= 255:
					tag = (f"a{'f' if fg else 'g'}{v}" if v < 16
						   else f"a{'x' if fg else 'X'}{v}")
				i += 2
			elif i + 4 < len(codes) and codes[i + 1] == "2":
				try:
					rgb = "".join(
						f"{int(codes[i + k]):02x}" for k in (2, 3, 4)
					)
					tag = f"a{'y' if fg else 'z'}{rgb}"
				except ValueError:
					pass
				i += 4
			if tag:
				_drop(st, *(("af", "ax", "ay") if fg else ("ag", "aX", "az")))
				st.add(tag)
		i += 1


def _xcolor(n: int) -> str:
	"""Couleur hex pour une entree de la palette 256."""
	if n >= 232:
		v = 8 + (n - 232) * 10
		return f"#{v:02x}{v:02x}{v:02x}"
	lv = (0, 95, 135, 175, 215, 255)
	n -= 16
	return f"#{lv[n // 36]:02x}{lv[(n % 36) // 6]:02x}{lv[n % 6]:02x}"


def _ensure_tag(w: tk.Text, tag: str) -> None:
	"""Configure a la volee les tags couleur 256/24bit (independants du theme)."""
	cfg: Set[str] = getattr(w, "_ansi_cfg", set())
	if tag in cfg:
		return
	color = _xcolor(int(tag[2:])) if tag[1] in "xX" else f"#{tag[2:]}"
	w.tag_configure(
		tag, **{"foreground" if tag[1] in "xy" else "background": color}
	)
	cfg.add(tag)
	w._ansi_cfg = cfg  # type: ignore[attr-defined]


def _emit(w: tk.Text, seg: str, tags: Set[str]) -> None:
	for i, part in enumerate(_CTRL_RE.sub("", seg).split("\r")):
		if i and not part.startswith("\n"):
			w.delete("end-1c linestart", "end-1c")
		if part:
			for t in tags:
				if len(t) > 2 and t[1] in "xXyz":
					_ensure_tag(w, t)
			w.insert("end-1c", part, tuple(sorted(tags)))


def write_ansi(w: tk.Text, data: str) -> None:
	"""Insere du texte ANSI dans un Text: couleurs -> tags, \\r -> reecriture de ligne."""
	st: Set[str] = set()
	pos = 0
	for m in _ANSI_RE.finditer(data):
		_emit(w, data[pos : m.start()], st)
		if m.group(2) == "m":
			_sgr(m.group(1) or "", st)
		pos = m.end()
	_emit(w, data[pos:], st)
