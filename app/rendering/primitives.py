"""Low-level Pillow toolkit shared by the templates: fonts, masks and colour helpers."""

from __future__ import annotations

import io
from enum import Enum
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageSequence

from config import path

__all__ = (
    "ASSETS",
    "Font",
    "FontManager",
    "get_dominant_color",
    "mask_to_circle",
    "resize_to_limit",
    "rounded_mask",
)


ASSETS = path / "assets"


class Font(Enum):
    """A bundled font; the value is its path under :data:`ASSETS`."""

    RUBIK = "fonts/rubik.ttf"
    ARIAL_UNICODE = "fonts/arial-unicode-ms.ttf"
    MENLO = "fonts/menlo.ttf"
    WHITNEY = "fonts/whitney.otf"
    KARLA = "fonts/karla.ttf"
    POPPINS = "fonts/poppins.ttf"
    INTER = "fonts/inter.ttf"
    GINTO_BOLD = "fonts/ginto-bold.otf"
    GINTO_NORD_HEAVY = "fonts/ginto-nord-heavy.otf"

    def __str__(self) -> str:
        return self.name.replace("_", " ").title()

    @property
    def path(self) -> str:
        return str(ASSETS / self.value)


class FontManager:
    """Opens each (font, size) once and keeps it in memory."""

    def __init__(self) -> None:
        self._cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def get(self, font: Font | str, size: int) -> ImageFont.FreeTypeFont:
        key = (font.path if isinstance(font, Font) else font), size
        try:
            return self._cache[key]
        except KeyError:
            self._cache[key] = loaded = ImageFont.truetype(key[0], size=size)
            return loaded


def mask_to_circle(image: Image.Image, *, quality: int = 3) -> Image.Image:
    """Masks an RGBA image into a circle. A higher quality will result in a smoother circle."""
    width, height = size = image.size
    big = width * quality, height * quality

    with Image.new("L", big, 0) as mask:
        ImageDraw.Draw(mask).ellipse((0, 0, *big), fill=255)
        mask = mask.resize(size, Image.Resampling.LANCZOS)
        mask = ImageChops.darker(mask, image.getchannel("A"))
        image.putalpha(mask)
        return image


@lru_cache(maxsize=128)
def rounded_mask(size: tuple[int, int], radius: int, *, quality: int = 4) -> Image.Image:
    """An anti-aliased rounded-rectangle ``L`` mask. Cached: treat the result as read-only."""
    width, height = size[0] * quality, size[1] * quality
    mask = Image.new("L", (width, height), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius * quality, fill=255)
    return mask.resize(size, Image.Resampling.LANCZOS)


def get_dominant_color(image: Image.Image | io.BytesIO, palette_size: int = 16) -> tuple[int, int, int]:
    """The most common colour of an image, ignoring (mostly) transparent pixels."""
    if isinstance(image, io.BytesIO):
        with Image.open(image) as opened:
            rgba = opened.convert("RGBA")
    else:
        rgba = image.convert("RGBA")

    rgba.thumbnail((100, 100))
    pixels = list(rgba.getdata())
    # Transparent pixels are usually (0, 0, 0, 0) and would otherwise win on any emoji/PNG.
    visible = [p[:3] for p in pixels if p[3] >= 128] or [p[:3] for p in pixels]

    strip = Image.new("RGB", (len(visible), 1))
    strip.putdata(visible)
    paletted = strip.convert("P", palette=Image.Palette.ADAPTIVE, colors=palette_size)

    palette = paletted.getpalette() or [0, 0, 0]
    index = max(paletted.getcolors() or [(0, 0)])[1]  # type: ignore[type-var]
    r, g, b = palette[index * 3 : index * 3 + 3]
    return r, g, b


def resize_to_limit(image: io.BytesIO, limit: int = 26_214_400) -> io.BytesIO:
    """Halve an image's dimensions until its encoded size fits under ``limit`` bytes.

    Formats that cannot be re-encoded are returned unchanged.
    """
    current_size: int = image.getbuffer().nbytes

    while current_size > limit:
        with Image.open(image) as canvas:
            fmt = canvas.format  # a resized copy loses its ``format``, so read it first
            if fmt not in ("PNG", "JPEG", "WEBP", "GIF"):
                return image  # cannot shrink it; looping would never terminate
            image = io.BytesIO()
            if fmt == "GIF":
                durations, frames = [], []
                for frame in ImageSequence.Iterator(canvas):
                    durations.append(frame.info.get("duration", 0))
                    frames.append(frame.resize([i // 2 for i in frame.size], resample=Image.Resampling.BICUBIC))

                frames[0].save(
                    image,
                    save_all=True,
                    append_images=frames[1:],
                    format="gif",
                    version=canvas.info.get("version", "GIF89a"),
                    duration=durations,
                    loop=0,
                    background=canvas.info.get("background", 0),
                    palette=canvas.getpalette(),
                )
            else:
                canvas.resize([i // 2 for i in canvas.size], resample=Image.Resampling.BICUBIC).save(image, format=fmt)

            image.seek(0)
            current_size = image.getbuffer().nbytes

    return image
