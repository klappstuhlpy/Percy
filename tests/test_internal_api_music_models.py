"""Music / profile / gallery response models neither drop nor rename a key of the pre-typing JSON.

FastAPI drops keys a response model does not declare, so each model round-trips a realistic
payload (``model_validate(x).model_dump(mode='json', by_alias=True) == x``) and the handlers
that build the big payloads are called with mocks.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import wavelink

from app.internal_api.routers import gallery, music, profile
from app.internal_api.routers.models import music as mm

REQUESTER = {'id': '5', 'name': 'Ann', 'avatar': 'https://cdn/a.png'}
LINK = {'name': 'Glass Tides', 'url': 'https://x.test/album/1'}
TRACK = {
    'title': 'One', 'author': 'Nova', 'uri': 'https://x.test/1', 'artwork': None, 'duration': 61000,
    'is_stream': False, 'source': 'youtube', 'requester': REQUESTER, 'autoplay': False,
    'artist_url': None, 'album': LINK,
}
FULL = {**TRACK, 'playlist': {'name': 'Mix', 'url': None}, 'recommended': False, 'isrc': None}
NOW = {
    **{k: v for k, v in FULL.items() if k != 'autoplay'}, 'autoplay': 1, 'position': 1200, 'paused': False,
    'volume': 70, 'loop': 2, 'shuffle': True,
}
HIST = {'title': 'a', 'author': None, 'uri': 'u', 'artwork': None, 'length_ms': 9, 'user_id': '5',
        'played_at': '2026-01-01T12:00:00'}
TREND = {'title': 'a', 'author': 'b', 'uri': None, 'artwork': None, 'length_ms': None, 'listeners': 2, 'plays': 3}
ALWAYS = {'enabled': True, 'mode': 'radio', 'source': 'lofi'}
SETUP = {'channel_id': '1', 'message_id': None, 'use_panel': True, 'dj_mode': 2}
FILTERS = {'nightcore': False, '8d': True, 'lowpass': 20.0}
CHAN = {'id': '10', 'name': 'vc'}

SAMPLES: list[tuple[type, object]] = [
    (mm.QueueTrack, TRACK),
    (mm.MusicTrackFull, FULL),
    (mm.NowPlaying, NOW),
    (mm.MusicLyrics, {'ok': True, 'has_synced': True, 'title': None, 'source': 'lrclib',
                      'lines': [{'time': 500, 'text': 'hi'}], 'plain': None}),
    (mm.MusicInfo, {
        'active': True, 'equalizer': [0.0] * 15, 'filters': FILTERS, 'presets': ['flat'], 'now_playing': NOW,
        'queue': [TRACK], 'history': [TRACK], 'channel': '10', 'channel_name': 'vc', 'setup': SETUP,
        'always_on': ALWAYS, 'listeners': ['5'],
    }),
    (mm.MusicInfo, {
        'active': False, 'equalizer': [0.0] * 15, 'filters': {**FILTERS, 'lowpass': None}, 'presets': [],
        'now_playing': None, 'queue': [], 'history': [], 'channel': None, 'channel_name': None,
        'setup': SETUP, 'always_on': {'enabled': False, 'mode': None, 'source': None}, 'listeners': [],
    }),
    (mm.MusicSetupResponse, {'ok': True, 'channel_id': '1', 'channel_name': 'music'}),
    (mm.MusicEqualizerApplied, {'ok': True, 'equalizer': [0.1] * 15}),
    (mm.MusicAlwaysOnApplied, {'ok': True, 'always_on': ALWAYS}),
    (mm.MusicDjModeApplied, {'ok': True, 'dj_mode': 1}),
    (mm.MusicControlResult, {'ok': True, 'action': 'skip', 'paused': False}),
    (mm.MusicAudio, {
        'active': True, 'equalizer': {'preset': 'pop', 'bands': [0.0] * 15},
        'filters': {'timescale': {'speed': 1.0, 'pitch': 1.0, 'rate': 1.0}, 'channel_mix': {'preset': None}},
        'effects': ['nightcore'], 'presets': {'equalizer': ['pop'], 'filters': ['8d'], 'equalizer_bands': {'pop': [0.0]}},
    }),
    (mm.MusicViewer, {'role': 'dj', 'can_start': True, 'can_control': True, 'can_destroy': False, 'is_manager': False,
                      'dj_mode': 1, 'voice_channel': CHAN, 'bot_channel': None}),
    (mm.MusicSearch, {'results': [TRACK]}),
    (mm.MusicArtist, {
        'artist': {'name': 'Nova', 'artwork': None, 'url': None}, 'top_tracks': [FULL],
        'releases': [{'title': 'T', 'kind': 'single', 'year': None, 'artwork': None, 'url': None, 'track_count': None}],
    }),
    (mm.MusicAlbum, {
        'album': {'title': 'T', 'kind': 'album', 'year': None, 'artists': ['Nova'], 'artwork': None,
                  'url': 'https://x.test', 'total_ms': 5},
        'tracks': [FULL],
    }),
    (mm.MusicPlayResult, {'queued': [TRACK], 'started': True}),
    (mm.MusicHistory, {'items': [HIST]}),
    (mm.MusicTrending, {'server': [TREND], 'global': [TREND], 'charts': [{'title': 't', 'author': None, 'artwork': None, 'length_ms': 1000, 'url': 'u'}]}),
    (profile.CustomBotProfile, {'name': None, 'avatar_url': 'u', 'banner_url': None, 'accent_color': None, 'about_me': None}),
    (gallery.GalleryResponse, {'images': [{'id': 'a', 'ext': 'png', 'mimetype': 'image/png', 'size': 3,
                                           'original_name': None, 'uploaded_at': 't', 'url': 'u', 'raw_url': 'r'}],
                               'total': 1}),
    (gallery.GalleryUploadResult, {'total': 1, 'errors': 0, 'skipped': 0, 'infected': 0, 'links': ['l'], 'raw_links': ['r']}),
    (gallery.GalleryDeleteResult, {'file': 'a', 'failed': False}),
]


@pytest.mark.parametrize(('model', 'sample'), SAMPLES, ids=[f'{m.__name__}-{i}' for i, (m, _) in enumerate(SAMPLES)])
def test_model_round_trips(model: type[mm.BaseModel], sample: object) -> None:
    assert model.model_validate(sample).model_dump(mode='json', by_alias=True) == sample


def _track(title: str = 'One') -> MagicMock:
    t = MagicMock(spec=wavelink.Playable)
    t.title, t.author, t.uri, t.artwork, t.length, t.is_stream = title, 'Nova', 'u', None, 1000, False
    t.source, t.extras, t.artist, t.album, t.playlist = 'youtube', SimpleNamespace(), None, None, None
    t.recommended, t.isrc = False, None
    return t


class _Queue(list):
    mode = wavelink.QueueMode.loop
    shuffle = False


async def test_get_music_wire_shape() -> None:
    guild = MagicMock(id=1)
    cfg = SimpleNamespace(music_panel_channel_id=7, music_panel_message_id=None, use_music_panel=True, music_dj_mode=1)
    bot = MagicMock()
    bot.db.get_guild_config = AsyncMock(return_value=cfg)
    bot.db.music_sessions.get_session = AsyncMock(return_value=None)

    # Inactive: keys the old handler omitted now arrive as null/empty (additive; the dashboard defaulted them anyway).
    with patch.object(music, '_get_guild_player', return_value=(guild, None)):
        out = (await music.get_music(guild, bot)).model_dump(mode='json', by_alias=True)
    assert out['active'] is False and out['now_playing'] is None and out['queue'] == [] and out['channel'] is None
    assert out['setup'] == {'channel_id': '7', 'message_id': None, 'use_panel': True, 'dj_mode': 1}
    assert out['filters'] == {'nightcore': False, '8d': False, 'lowpass': None}

    cur = _track('Now')
    player = MagicMock()
    player.current = cur
    player.queue = _Queue([_track('Q1')])
    player.autoplay = wavelink.AutoPlayMode.partial
    player.played_history = [cur, _track('H1')]
    player.position, player.paused, player.volume = 5, False, 70
    player.always_on = False
    player.channel = SimpleNamespace(id=10, name='vc', members=[SimpleNamespace(id=5, bot=False)])
    player.filters.equalizer.payload = {0: {'gain': 0.25}}
    player.filters.timescale.payload = {'speed': 1.25}
    player.filters.rotation.payload = {}
    player.filters.low_pass.payload = {}
    with patch.object(music, '_get_guild_player', return_value=(guild, player)):
        out = (await music.get_music(guild, bot)).model_dump(mode='json', by_alias=True)
    assert out['now_playing']['loop'] == 1 and out['now_playing']['title'] == 'Now'
    assert [t['title'] for t in out['queue']] == ['Q1'] and [t['title'] for t in out['history']] == ['H1']
    assert out['filters'] == {'nightcore': True, '8d': False, 'lowpass': None}
    assert out['listeners'] == ['5'] and out['channel'] == '10' and out['equalizer'][0] == 0.25


async def test_profile_and_gallery_wire_shape() -> None:
    me = MagicMock(nick=None, display_name='Percy', accent_color=None)
    me.display_avatar.url = 'https://cdn/p.png'
    me.banner = None
    guild = MagicMock(me=me)
    bot = MagicMock()
    bot.wait_until_ready = AsyncMock()
    bot.fetch_user = AsyncMock(return_value=MagicMock(banner=None))
    out = (await profile.get_custom_bot(bot, guild)).model_dump(mode='json')
    assert out['name'] == 'Percy' and set(out) == {'name', 'avatar_url', 'banner_url', 'accent_color', 'about_me'}

    @dataclass
    class Img:
        id: str = 'a'
        ext: str = 'png'
        mimetype: str = 'image/png'
        size: int = 3
        uploaded_at: str = 't'
        url: str = 'u'
        raw_url: str = 'r'
        original_name: str | None = None

    @dataclass
    class Listing:
        images: list
        total: int

    client = MagicMock(available=True)
    client.list_guild_images = AsyncMock(return_value=Listing([Img()], 1))
    gbot = MagicMock(klappstuhlme_internal_client=client)
    listing = (await gallery.list_gallery(MagicMock(id=1), gbot)).model_dump(mode='json')
    assert listing['total'] == 1 and set(listing['images'][0]) == set(SAMPLES[-3][1]['images'][0])  # type: ignore[index]
