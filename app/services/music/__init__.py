"""Music: access gating, audio filters, lyrics and playlist import/export."""

from app.services.music.access import (
    MusicViewer,
    resolve_viewer,
)
from app.services.music.audio import (
    EFFECTS,
    EQ_PRESETS,
    audio_state,
    match_eq_preset,
)
from app.services.music.lyrics import (
    LyricLine,
    LyricsResult,
    SyncedLyrics,
    clean_track_title,
    display_title,
    parse_lrc,
    pick_genius_hit,
    track_artist,
)
from app.services.music.playlist_io import (
    MAX_BYTES,
    MAX_NAME,
    MAX_TRACKS,
    ImportedPlaylist,
    ImportedTrack,
    PlaylistImportError,
    parse_playlist,
    serialize_playlist,
)

__all__ = (
    "EFFECTS",
    "EQ_PRESETS",
    "MAX_BYTES",
    "MAX_NAME",
    "MAX_TRACKS",
    "ImportedPlaylist",
    "ImportedTrack",
    "LyricLine",
    "LyricsResult",
    "MusicViewer",
    "PlaylistImportError",
    "SyncedLyrics",
    "audio_state",
    "clean_track_title",
    "display_title",
    "match_eq_preset",
    "parse_lrc",
    "parse_playlist",
    "pick_genius_hit",
    "resolve_viewer",
    "serialize_playlist",
    "track_artist",
)
