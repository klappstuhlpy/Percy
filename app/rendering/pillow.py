from __future__ import annotations

import io
import re
from enum import Enum
from functools import partial
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias, Final

from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageSequence
from pilmoji import EMOJI_REGEX, Node, NodeType, getsize

from config import path

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from PIL.ImageDraw import Draw
    from pilmoji.core import ColorT, FontT

    ImageSize: TypeAlias = tuple[int, int]

__all__ = (
    'ASSETS',
    'Font',
    'FONT_MAPPING',
    'wrap_text',
    'alpha_paste',
    'rounded_mask',
    'FontManager',
    'FallbackFont',
    'FallbackFontSession',
    'get_text_dimensions',
    'get_dominant_color',
    'resize_to_limit',
)


ASSETS = path / 'assets'


FONT_MAPPING: Final[tuple[str, ...]] = (
    'fonts/rubik.ttf',
    'fonts/arial.ttf',
    'fonts/arial-unicode-ms.ttf',
    'fonts/menlo.ttf',
    'fonts/whitney.otf',
    'fonts/karla.ttf',
    'fonts/poppins.ttf',
    'fonts/inter.ttf',
    'fonts/ginto-bold.otf',
    'fonts/ginto-nord-heavy.otf',
    'fonts/helvetica.ttf',
)


class Font(Enum):
    """Represents a rank card's font."""
    RUBIK = 0
    ARIAL = 1
    ARIAL_UNICODE = 2
    MENLO = 3
    WHITNEY = 4
    KARLA = 5
    POPPINS = 6
    INTER = 7
    GINTO_BOLD = 8
    GINTO_NORD_HEAVY = 9
    HELVETICA = 10

    def __str__(self) -> str:
        return self.name.replace('_', ' ').title()


def _pilmoji_parse_line(line: str, /) -> list[Node]:
    nodes = []

    for i, chunk in enumerate(EMOJI_REGEX.split(line)):
        if not chunk:
            continue

        if not i % 2:
            nodes.append(Node(NodeType.text, chunk))
            continue

        node = Node(NodeType.discord_emoji, chunk) if len(chunk) > 18 else Node(NodeType.emoji, chunk)
        nodes.append(node)

    return nodes


def _to_emoji_aware_chars(text: str) -> list[str]:
    nodes = _pilmoji_parse_line(text)
    result = []

    for node in nodes:
        if node.type is NodeType.text:
            result.extend(node.content)
            continue

        result.append(node.content)

    return result


def _strip_split_text(text: list[str]) -> list[str]:
    """Note that this modifies in place"""
    if not text:
        return text

    text[0] = text[0].lstrip()
    text[-1] = text[-1].rstrip()

    if not text[0]:
        text.pop(0)

    if text and not text[-1]:
        text.pop(-1)

    return text


def _wrap_text_by_chars(text: str, max_width: int, to_getsize: Callable[[str], tuple[int, int]]) -> list[str]:
    result = []
    buffer = ''

    for char in _to_emoji_aware_chars(text):
        new = buffer + char

        width, _ = to_getsize(new)
        if width > max_width:
            result.append(buffer)
            buffer = char

            continue

        buffer += char

    if buffer:
        result.append(buffer)

    return result


def _wrap_line(text: str, font: FontT, max_width: int, **pilmoji_kwargs: Any) -> list[str]:
    result = []
    buffer = []

    _getsize = partial(getsize, font=font, **pilmoji_kwargs)

    for word in text.split():
        new = ' '.join(buffer) + ' ' + word

        width, _ = _getsize(new)
        if width >= max_width:
            new = ' '.join(buffer)
            width, _ = _getsize(new)

            if width >= max_width:
                wrapped = _wrap_text_by_chars(new, max_width, _getsize)
                last = wrapped.pop()

                result += wrapped
                buffer = [last, word]

            else:
                result.append(new)
                buffer = [word]

            continue

        buffer.append(word)

    if buffer:
        new = ' '.join(buffer)
        try:
            width, _ = getsize(new, font=font, **pilmoji_kwargs)
        except AttributeError:
            width = font.getlength(new)

        if width >= max_width:
            result += _wrap_text_by_chars(new, max_width, _getsize)
        else:
            result.append(new)

    return _strip_split_text(result)


def wrap_text(text: str, font: FontT, max_width: int) -> list[str]:
    lines = text.split('\n')
    result = []

    for line in lines:
        result += _wrap_line(line, font, max_width)

    return result


class FontManager:
    """Manages fonts by opening then storing them in memory."""

    def __init__(self) -> None:
        self._internal_cache: dict[tuple[str, int], ImageFont.FreeTypeFont] = {}

    def get(self, path: str, size: int) -> ImageFont.FreeTypeFont:
        key = path, size
        try:
            return self._internal_cache[key]
        except KeyError:
            pass

        fp = Path(path).open('rb')
        self._internal_cache[key] = font = ImageFont.truetype(fp, size=size)
        return font

    def clear(self) -> None:
        self._internal_cache.clear()

    def __del__(self) -> None:
        self.clear()


class FallbackFontSession:
    def __init__(self, font: FallbackFont, draw: Draw) -> None:
        self._font = font
        self._draw = draw

    def __enter__(self) -> FallbackFont:
        self._font.inject(self._draw)
        return self._font

    def __exit__(self, *args: Any) -> None:
        self._font.eject(self._draw)


class FallbackFont:
    """A font that falls back to another font when it cannot render a character."""

    def __init__(
        self,
        font: FontT,
        fallback_loader: Callable[[], FontT],
        *,
        fallback_scale: float = 1,
        fallback_offset: tuple[int, int] = (0, 0),
    ) -> None:
        self._prepare(font, fallback_loader, fallback_scale, fallback_offset)
        self._load_font_regex()

    def _prepare(self, font: FontT, fallback_loader: Callable[[], FontT], fallback_scale: float, fallback_offset: tuple[int, int]) -> None:
        self.font: FontT = font
        self.fallback_loader = fallback_loader
        self.fallback_scale: float = fallback_scale
        self.fallback_offset: tuple[int, int] = fallback_offset

        self._size: int | float = font.size
        self._fallback_size: int = round(self._size * fallback_scale)

        self._fallback: FontT | None = None
        self._regex: re.Pattern[str] | None = None

    @property
    def fallback(self) -> FontT:
        if self._fallback is None:
            self._fallback = self.fallback_loader()

        return self._fallback

    @property
    def path(self) -> str:
        return self.font.path

    @property
    def size(self) -> int:
        return self._size

    def _load_font_regex(self) -> None:
        with TTFont(self.font.path) as font:
            characters = (chr(code) for table in font["cmap"].tables for code, _ in table.cmap.items())

        self._regex = re.compile('([^%s]+)' % ''.join(map(re.escape, characters)))

    def _split_text(self, text: str) -> Iterator[list[str]]:
        yield from (self._regex.split(line) for line in text.split('\n'))

    def variant(self, *, font: FontT = None, size: int | None = None) -> FallbackFont:
        if font is not None:
            font = font.path
            size = size or getattr(font, 'size', self._size)

        new = self.__class__.__new__(self.__class__)
        new._prepare(
            self.font.font_variant(font=font, size=size), self.fallback_loader, self.fallback_scale, self.fallback_offset
        )

        new._fallback = self.fallback and self.fallback.font_variant(size=round(size * self.fallback_scale))
        new._regex = self._regex

        if font is not None or not new._regex:
            new._load_font_regex()

        return new

    def inject(self, draw: Draw) -> None:
        self.font.getsize, self.__font_getsize = self.getsize, get_text_dimensions
        draw.text, self.__draw_text = partial(self.text, draw), draw.text

    def eject(self, draw: Draw) -> None:
        self.font.getsize = self.__font_getsize
        del self.__font_getsize

        draw.text = self.__draw_text
        del self.__draw_text

    def session(self, draw: Draw) -> FallbackFontSession:
        return FallbackFontSession(self, draw)

    def getsize(self, text: str) -> tuple[int, int]:
        width = height = 0

        for line in self._split_text(text):
            current = 0

            for i, chunk in enumerate(line):
                if not chunk:
                    continue

                font = self.fallback if i % 2 else self.font
                current += get_text_dimensions(chunk, font)[0]

            if current > width:
                width = current

            height += 4 + self._size

        return width, height - 4

    def text(self, draw: Draw, xy: tuple[int, int], text: str, fill: ColorT = None, font: FontT = None, *args: Any, **kwargs: Any) -> None:
        if font is not None and (font.path, font.size) != (self.font.path, self.font.size) and font is not self:
            return self.variant(font=font).text(draw, xy, text, fill, *args, **kwargs)

        x, y = xy
        draw_text = self.__draw_text if isinstance(draw.text, partial) else draw.text

        for line in self._split_text(text):
            for i, chunk in enumerate(line):
                if not chunk:
                    continue

                if i % 2:
                    font = self.fallback
                    offset_x, offset_y = self.fallback_offset
                    position = x + offset_x, y + offset_y
                else:
                    font = self.font
                    position = x, y
                draw_text(position, chunk, fill, font, *args, **kwargs)

                width, _ = get_text_dimensions(chunk, font)
                x += width

            y += 4 + self._size
            x = xy[0]


def get_text_dimensions(text_string: str, font: ImageFont) -> tuple[int, int]:
    # https://stackoverflow.com/a/46220683/9263761
    ascent, descent = font.getmetrics()

    text_width = font.getmask(text_string).getbbox()[2]
    text_height = font.getmask(text_string).getbbox()[3] + descent

    return text_width, text_height


def mask_to_circle(image: Image.Image, *, quality: int = 3) -> Image.Image:
    """Masks an image into a circle. A higher quality will result in a smoother circle."""
    width, height = size = image.size
    big = width * quality, height * quality

    with Image.new('L', big, 0) as mask:
        ImageDraw.Draw(mask).ellipse((0, 0, *big), fill=255)
        mask = mask.resize(size, Image.Resampling.LANCZOS)
        mask = ImageChops.darker(mask, image.split()[-1])
        image.putalpha(mask)
        return image


def rounded_mask(size: ImageSize, radius: int, *, alpha: int = 255, quality: int = 5) -> Image.Image:
    """Create a rounded rectangle mask with the given size and border radius."""
    radius *= quality
    image = Image.new('RGBA', (size[0] * quality, size[1] * quality), (0, 0, 0, 0))

    with Image.new('RGBA', (radius, radius), (0, 0, 0, 0)) as corner:
        draw = ImageDraw.Draw(corner)

        draw.pieslice((0, 0, radius * 2, radius * 2), 180, 270, fill=(50, 50, 50, alpha + 55))  # type: ignore
        mx, my = (size[0] * quality, size[1] * quality)

        image.paste(corner, (0, 0), corner)
        image.paste(corner.rotate(90), (0, my - radius), corner.rotate(90))
        image.paste(corner.rotate(180), (mx - radius, my - radius), corner.rotate(180))
        image.paste(corner.rotate(270), (mx - radius, 0), corner.rotate(270))

    draw = ImageDraw.Draw(image)
    draw.rectangle(((radius, 0), (mx - radius, my)), fill=(50, 50, 50, alpha))
    draw.rectangle(((0, radius), (mx, my - radius)), fill=(50, 50, 50, alpha))

    return image.resize(size, Image.Resampling.LANCZOS)


def alpha_paste(background: Image.Image, foreground: Image.Image, box: ImageSize, mask: Image.Image) -> Image.Image:
    """Paste an image with alpha on top of another image additively, rather than overwriting the alpha."""
    background = background.convert('RGBA')
    foreground = foreground.convert('RGBA')

    with Image.new('RGBA', background.size) as overlay:
        overlay.paste(foreground, box, mask)
        return Image.alpha_composite(background, overlay)


def get_dominant_color(image: Image.Image | io.BytesIO, palette_size: int = 16) -> tuple:
    if isinstance(image, io.BytesIO):
        image = Image.open(image)

    img = image.copy()
    img.thumbnail((100, 100))

    paletted = img.convert('P', palette=Image.Palette.ADAPTIVE, colors=palette_size)

    palette = paletted.getpalette()
    color_counts = sorted(paletted.getcolors(), reverse=True)
    palette_index = color_counts[0][1]
    dominant_color = palette[palette_index * 3: palette_index * 3 + 3]  # type: ignore

    return tuple(dominant_color)


def resize_to_limit(image: io.BytesIO, limit: int = 26_214_400) -> io.BytesIO:
    """Resizes an image to a given limit.

    Parameters
    ----------
    image: `BytesIO`
        The image to resize.
    limit: `int`
        The limit to resize the image to.

    Returns
    -------
    `BytesIO`
        The resized image.
    """
    current_size: int = image.getbuffer().nbytes

    while current_size > limit:
        with Image.open(image) as canvas:
            image = io.BytesIO()
            if canvas.format in ('PNG', 'JPEG', 'JPG', 'WEBP'):
                canvas = canvas.resize([i // 2 for i in canvas.size], resample=Image.BICUBIC)  # type: ignore
                canvas.save(image, format=canvas.format)
            elif canvas.format == 'GIF':
                durations, frames = [], []
                for frame in ImageSequence.Iterator(canvas):
                    durations.append(frame.info.get('duration', 0))
                    frames.append(
                        frame.resize(
                            [i // 2 for i in frame.size],
                            resample=Image.Resampling.BICUBIC
                        )
                    )

                frames[0].save(
                    image,
                    save_all=True,
                    append_images=frames[1:],
                    format='gif',
                    version=canvas.info.get('version', 'GIF89a'),
                    duration=durations,
                    loop=0,
                    background=canvas.info.get('background', 0),
                    palette=canvas.getpalette(),
                )

            image.seek(0)
            current_size = image.getbuffer().nbytes

    return image
