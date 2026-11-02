"""Playlist import/export: a pure parser and serializer (no discord import).

Accepts Percy's own shape ``{name, tracks: [{title, author, uri, length}]}`` and Red's audio
export ``{playlist_name, tracks: [{info: {uri, title, author, length}}]}``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

MAX_TRACKS = 500
MAX_BYTES = 512 * 1024
MAX_NAME = 100
_MAX_TEXT = 500
_MAX_URI = 2000


class PlaylistImportError(ValueError):
    """The file is not an importable playlist; the message says why."""


@dataclass(slots=True)
class ImportedTrack:
    title: str
    author: str | None
    uri: str


@dataclass(slots=True)
class ImportedPlaylist:
    name: str | None
    tracks: list[ImportedTrack]


def serialize_playlist(name: str, tracks: list[tuple[str, str | None, str, int]]) -> str:
    """JSON text for ``(title, author, uri, length_ms)`` rows."""
    return json.dumps(
        {"name": name, "tracks": [{"title": t, "author": a, "uri": u, "length": n} for t, a, u, n in tracks]},
        indent=2,
        ensure_ascii=False,
    )


def _text(value: Any, field: str, index: int, *, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise PlaylistImportError(f"Track {index}: `{field}` must be text.")
    return value.strip()[:limit] or None


def parse_playlist(raw: bytes | str) -> ImportedPlaylist:
    """Validate an exported playlist file. Raises :class:`PlaylistImportError`."""
    size = len(raw.encode() if isinstance(raw, str) else raw)
    if size > MAX_BYTES:
        raise PlaylistImportError(f"The file is larger than {MAX_BYTES // 1024} KB.")
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):
        raise PlaylistImportError("The file is not valid JSON.") from None
    if not isinstance(data, dict):
        raise PlaylistImportError("Expected a JSON object with a `tracks` list.")

    name = data.get("name", data.get("playlist_name"))
    if name is not None and not isinstance(name, str):
        raise PlaylistImportError("`name` must be text.")
    name = (name.strip()[:MAX_NAME] or None) if name else None

    items = data.get("tracks")
    if not isinstance(items, list):
        raise PlaylistImportError("Expected a `tracks` list.")
    if len(items) > MAX_TRACKS:
        raise PlaylistImportError(f"A playlist can hold at most {MAX_TRACKS} tracks (the file has {len(items)}).")

    tracks: list[ImportedTrack] = []
    for i, item in enumerate(items, 1):
        if not isinstance(item, dict):
            raise PlaylistImportError(f"Track {i} is not an object.")
        info = item.get("info")
        src = info if isinstance(info, dict) else item
        uri = _text(src.get("uri"), "uri", i, limit=_MAX_URI + 1)
        if uri is None or len(uri) > _MAX_URI:
            raise PlaylistImportError(f"Track {i}: `uri` is missing or too long.")
        if not uri.lower().startswith(("http://", "https://")):
            raise PlaylistImportError(f"Track {i}: `uri` must be an http(s) link.")
        title = _text(src.get("title"), "title", i, limit=_MAX_TEXT) or uri
        tracks.append(ImportedTrack(title, _text(src.get("author"), "author", i, limit=_MAX_TEXT), uri))
    return ImportedPlaylist(name, tracks)
