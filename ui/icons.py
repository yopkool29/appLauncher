"""Icones couleur : emoji Noto Color Emoji rasterises via Pillow.

Rendu a la taille bitmap native CBDT (109px) puis downscale LANCZOS ;
fallback glyphe unicode gere par le caller quand None est rendu."""
import io
import tkinter as tk
from pathlib import Path
from typing import Optional

_SIZE = 109  # taille bitmap native de Noto Color Emoji (CBDT)
_FONT_CANDIDATES = (
	"/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf",
	"/usr/share/fonts/google-noto-emoji/NotoColorEmoji.ttf",
	"/usr/share/fonts/noto-color-emoji/NotoColorEmoji.ttf",
)


def _emoji_font() -> Optional[str]:
	for p in _FONT_CANDIDATES:
		if Path(p).exists():
			return p
	return None


_cache: dict = {}


def emoji_image(char: str, px: int = 14) -> Optional[tk.PhotoImage]:
	"""PhotoImage couleur d'un emoji ; None si Pillow/font absent.
	Cachee : garde la reference en vie (GC des PhotoImage)."""
	key = (char, px)
	if key in _cache:
		return _cache[key]
	path = _emoji_font()
	if path is None:
		return None
	try:
		from PIL import Image, ImageDraw, ImageFont  # type: ignore

		f = ImageFont.truetype(path, _SIZE)
		img = Image.new("RGBA", (_SIZE + 8, _SIZE + 8), (0, 0, 0, 0))
		ImageDraw.Draw(img).text(
			(0, 0), char, font=f, embedded_color=True
		)
		# crop sur la bbox alpha : le glyphe remplit tout le cadre
		# (sinon marges transparentes autour -> aspect "padded")
		bbox = img.getchannel("A").getbbox()
		if bbox is None:
			return None  # emoji absent de la font -> bitmap vide
		img = img.crop(bbox)
		img.thumbnail((px, px), Image.Resampling.LANCZOS)
		out = Image.new("RGBA", (px, px), (0, 0, 0, 0))
		out.paste(
			img,
			((px - img.width) // 2, (px - img.height) // 2),
			img,
		)
		img = out
		buf = io.BytesIO()
		img.save(buf, "PNG")
		_cache[key] = tk.PhotoImage(data=buf.getvalue())
		return _cache[key]
	except Exception:
		return None
