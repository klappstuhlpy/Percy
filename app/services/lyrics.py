"""Lyrics service: pure parsing/formatting logic for synced + plain lyrics.

Like the rest of ``app/services``, this module is Discord-free and unit-testable.
It knows how to turn an LRC string (``[mm:ss.xx] line``) into an ordered list of
timed lines, locate the active line for a playback position, and render a small
"karaoke" window around it. The HTTP fetching lives in
``app/clients/lyrics.py`` (LRCLIB); the orchestration/UI lives in the music cog.
"""

from __future__ import annotations

import bisect
import re
from dataclasses import dataclass, field
from typing import NamedTuple

__all__ = (
    "LyricLine",
    "LyricsResult",
    "SyncedLyrics",
    "clean_track_title",
    "parse_lrc",
)

import discord

# Matches a single LRC time tag, e.g. ``[01:23.45]`` or ``[01:23]`` (also ``:``
# as the fractional separator that some providers emit). Metadata tags such as
# ``[ar:Artist]`` never match because the minute group requires digits.
_TIME_TAG = re.compile(r"\[(\d{1,2}):(\d{2})(?:[.:](\d{1,3}))?\]")

# Bracketed segments that are noise for a lyrics lookup (improves LRCLIB matching).
_TITLE_NOISE = re.compile(
    r"[(\[][^)\]]*\b("
    r"official|video|audio|lyrics?|lyric video|visuali[sz]er|m/?v|hd|hq|4k|8k|"
    r"remaster(?:ed)?|explicit|clean|colou?r coded|sped up|slowed|reverb"
    r")\b[^)\]]*[)\]]",
    re.IGNORECASE,
)


class LyricLine(NamedTuple):
    """A single timed lyric line."""

    timestamp: int  # milliseconds from the start of the track
    text: str


def _fraction_to_ms(fraction: str | None) -> int:
    """Convert an LRC fractional component (cs/ms) into milliseconds."""
    if not fraction:
        return 0
    # Pad/truncate to exactly three digits: ``34`` -> 340ms, ``5`` -> 500ms.
    return int(fraction.ljust(3, "0")[:3])


def parse_lrc(raw: str | None) -> list[LyricLine]:
    """Parse an LRC string into a timestamp-sorted list of :class:`LyricLine`.

    Lines may carry multiple time tags (``[00:10.00][01:20.00] chorus``); each tag
    yields its own entry. Lines without a time tag (metadata, blanks) are ignored.
    Empty lyric text is preserved so instrumental gaps still advance the highlight.
    """
    if not raw:
        return []

    lines: list[LyricLine] = []
    for raw_line in raw.splitlines():
        tags = list(_TIME_TAG.finditer(raw_line))
        if not tags:
            continue
        text = raw_line[tags[-1].end():].strip()
        for tag in tags:
            minutes, seconds = int(tag.group(1)), int(tag.group(2))
            total_ms = (minutes * 60 + seconds) * 1000 + _fraction_to_ms(tag.group(3))
            lines.append(LyricLine(total_ms, discord.utils.escape_markdown(text)))

    lines.sort(key=lambda line: line.timestamp)
    return lines


def clean_track_title(title: str) -> str:
    """Strip common YouTube/upload noise from a track title for lyric matching.

    Removes bracketed junk like ``(Official Video)`` / ``[Lyrics]`` and trims
    trailing separators, leaving something closer to the bare song name.
    """
    cleaned = _TITLE_NOISE.sub("", title)
    cleaned = re.sub(r"\s{2,}", " ", cleaned)
    return cleaned.strip(" -–—·|")


@dataclass(slots=True)
class SyncedLyrics:
    """Time-synced lyrics with helpers to track the active line during playback."""

    lines: list[LyricLine]
    _timestamps: list[int] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._timestamps = [line.timestamp for line in self.lines]

    def __bool__(self) -> bool:
        return bool(self.lines)

    def active_index(self, position_ms: int) -> int:
        """Index of the line that should be highlighted at ``position_ms``.

        Returns ``-1`` before the first line begins (intro/instrumental).
        """
        return bisect.bisect_right(self._timestamps, position_ms) - 1

    def next_timestamp(self, index: int) -> int | None:
        """Start time (ms) of the line after ``index``, or ``None`` if it's the last."""
        nxt = index + 1
        if 0 <= nxt < len(self._timestamps):
            return self._timestamps[nxt]
        return None

    def plain_text(self) -> str:
        """Flatten to plain text (used when a synced view isn't wanted)."""
        return "\n".join(line.text for line in self.lines)

    def render(
        self,
        position_ms: int,
        *,
        before: int = 3,
        after: int = 5,
        highlight: int = 2,
        max_chars: int = 3500,
    ) -> str:
        """Render a karaoke window around the active line.

        ``highlight`` lines starting at the active one are bold (the current line
        plus the next, by default, which reads better and tolerates small position
        drift); surrounding context is rendered as Discord subtext (``-#``). Empty
        lines become a ``♪`` so instrumental gaps read clearly. The result is
        clamped to ``max_chars`` to stay embed-safe.
        """
        if not self.lines:
            return ""

        idx = self.active_index(position_ms)
        anchor = idx if idx >= 0 else 0
        start = max(0, anchor - before)
        end = min(len(self.lines), anchor + after + 1)
        bold_last = idx + max(highlight, 1) - 1  # inclusive index of the last bold line

        rows: list[str] = []
        for i in range(start, end):
            text = self.lines[i].text or "♪"
            rows.append(f"**{text}**" if idx <= i <= bold_last else f"-# {text}")

        return "\n".join(rows)[:max_chars]


@dataclass(slots=True)
class LyricsResult:
    """The outcome of a lyrics lookup, carrying whichever form was found."""

    title: str
    source: str
    synced: SyncedLyrics | None = None
    plain: str | None = None
    url: str | None = None
    thumbnail: str | None = None

    @property
    def has_synced(self) -> bool:
        return self.synced is not None and bool(self.synced.lines)

    def best_text(self) -> str:
        """Plain text for a static (non-live) display."""
        if self.plain:
            return self.plain
        if self.synced is not None:
            return self.synced.plain_text()
        return ""
