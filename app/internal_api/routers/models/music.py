"""Response models for the music router — the source of truth for percy-client's generated types.

Class names are the Rust type names (see dashboard/crates/percy-client/GENERATION.md). Keys the
dashboard's browser JS reads are *required-nullable* (``X | None`` without a default) so they are
never dropped when the BFF re-serialises. ``MusicHistoryItem`` is shared with the user library.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class TrackLink(BaseModel):
    name: str
    url: str | None


class TrackRequester(BaseModel):
    id: str
    name: str | None
    avatar: str | None


class _TrackBase(BaseModel):
    title: str
    author: str
    uri: str | None
    artwork: str | None
    duration: int
    is_stream: bool
    source: str | None
    requester: TrackRequester | None
    artist_url: str | None
    album: TrackLink | None


class QueueTrack(_TrackBase):
    """A track in the queue / history / search results (``_serialize_track``)."""

    autoplay: bool


class MusicTrackFull(_TrackBase):
    """``_serialize_track(full=True)`` for artist/album pages."""

    autoplay: bool
    playlist: TrackLink | None
    recommended: bool
    isrc: str | None


class NowPlaying(_TrackBase):
    """The live player's current track: the full track plus transport state."""

    model_config = ConfigDict(populate_by_name=True)

    autoplay: int
    playlist: TrackLink | None
    recommended: bool
    isrc: str | None
    position: int
    paused: bool
    volume: int
    loop: int  # 0 = off, 1 = track, 2 = queue
    shuffle: bool


class LyricLine(BaseModel):
    time: int
    text: str


class MusicLyrics(BaseModel):
    ok: bool
    has_synced: bool
    title: str | None
    source: str | None
    lines: list[LyricLine]
    plain: str | None


class MusicFiltersState(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    nightcore: bool
    eight_d: bool = Field(alias='8d')
    lowpass: float | None


class MusicSetup(BaseModel):
    channel_id: str | None
    message_id: str | None
    use_panel: bool
    dj_mode: int


class AlwaysOnState(BaseModel):
    enabled: bool
    mode: str | None
    source: str | None


class MusicInfo(BaseModel):
    """``GET /music``: the live player snapshot (inactive guilds get empty queue/history)."""

    active: bool
    equalizer: list[float]
    filters: MusicFiltersState
    presets: list[str]
    now_playing: NowPlaying | None
    queue: list[QueueTrack]
    history: list[QueueTrack]
    channel: str | None
    channel_name: str | None
    setup: MusicSetup | None  # always sent by Percy; nullable so dashboard templates keep their None branch
    always_on: AlwaysOnState
    listeners: list[str]


class MusicSetupResponse(BaseModel):
    ok: bool
    channel_id: str
    channel_name: str


class MusicAck(BaseModel):
    ok: bool


class MusicEqualizerApplied(BaseModel):
    ok: bool
    equalizer: list[float]


class MusicAlwaysOnApplied(BaseModel):
    ok: bool
    always_on: AlwaysOnState


class MusicDjModeApplied(BaseModel):
    ok: bool
    dj_mode: int


class MusicControlResult(BaseModel):
    ok: bool
    action: str
    paused: bool


class MusicEqualizer(BaseModel):
    preset: str | None
    bands: list[float]


class MusicAudio(BaseModel):
    """Equalizer + filters. ``filters``/``presets`` stay free-form: the sound panel reads them directly."""

    active: bool
    equalizer: MusicEqualizer
    filters: dict[str, Any]
    effects: list[str]
    presets: dict[str, Any]


class MusicAudioApplied(MusicAudio):
    ok: bool


class MusicChannelRef(BaseModel):
    id: str
    name: str


class MusicViewer(BaseModel):
    role: str
    can_start: bool
    can_control: bool
    can_destroy: bool
    is_manager: bool
    dj_mode: int
    voice_channel: MusicChannelRef | None
    bot_channel: MusicChannelRef | None


class MusicSearch(BaseModel):
    results: list[QueueTrack]


class MusicArtistInfo(BaseModel):
    name: str
    artwork: str | None
    url: str | None


class MusicRelease(BaseModel):
    title: str
    kind: str
    year: int | None
    artwork: str | None
    url: str | None
    track_count: int | None


class MusicArtist(BaseModel):
    artist: MusicArtistInfo
    top_tracks: list[MusicTrackFull]
    releases: list[MusicRelease]


class MusicAlbumInfo(BaseModel):
    title: str
    kind: str
    year: int | None
    artists: list[str]
    artwork: str | None
    url: str | None
    total_ms: int


class MusicAlbum(BaseModel):
    album: MusicAlbumInfo
    tracks: list[MusicTrackFull]


class MusicPlayResult(BaseModel):
    queued: list[QueueTrack]
    started: bool


class MusicHistoryItem(BaseModel):
    """One ``music_plays`` row (guild history and a user's recent plays)."""

    title: str
    author: str | None
    uri: str | None
    artwork: str | None
    length_ms: int | None
    user_id: str | None
    played_at: str  # naive-UTC ISO text


class MusicHistory(BaseModel):
    items: list[MusicHistoryItem]


class TrendingTrack(BaseModel):
    title: str
    author: str | None
    uri: str | None
    artwork: str | None
    length_ms: int | None
    listeners: int
    plays: int


class ChartTrack(BaseModel):
    title: str
    author: str | None
    artwork: str | None
    length_ms: int | None
    url: str | None


class MusicTrending(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    server: list[TrendingTrack]
    global_: list[TrendingTrack] = Field(alias='global')
    #: Deezer's worldwide chart; empty when Deezer is unreachable or the Music cog is not loaded.
    charts: list[ChartTrack] = []
