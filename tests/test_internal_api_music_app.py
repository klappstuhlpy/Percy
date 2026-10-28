"""Music app routes: viewer, play, history and queue remove, with fake bot/guild/member objects."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import wavelink
from fastapi import HTTPException

from app.cogs.music.player import Player
from app.internal_api.routers import music as api


def _voice(channel_id: int | None) -> SimpleNamespace | None:
    return SimpleNamespace(channel=SimpleNamespace(id=channel_id, name=f'vc{channel_id}')) if channel_id else None


def _guild(*, member_vc: int | None, bot_vc: int | None, manage_guild: bool = False) -> MagicMock:
    member = MagicMock()
    member.id = 5
    member.voice = _voice(member_vc)
    member.roles = []
    member.guild_permissions.manage_guild = manage_guild
    guild = MagicMock()
    guild.id = 1
    guild.roles = []
    guild.me.voice = _voice(bot_vc)
    guild.get_member = MagicMock(side_effect=lambda uid: member if uid == 5 else None)
    member.guild = guild
    return guild


def _bot(dj_mode: int = 0) -> MagicMock:
    bot = MagicMock()
    bot.db.get_guild_config = AsyncMock(return_value=SimpleNamespace(music_dj_mode=dj_mode))
    return bot


class _Queue:
    def __init__(self, n: int = 0) -> None:
        self._items = list(range(n))

    async def put_wait(self, item: object) -> None:
        self._items.append(item)

    def get(self) -> object:
        return self._items.pop(0)


def _player(queued: int = 0) -> MagicMock:
    player = MagicMock()
    player.queue = _Queue(queued)
    player.playing = True
    player.always_on = False
    player.connected = True
    player.refresh_panel = AsyncMock()
    # The real queue edits, so the API is tested against what the player does.
    player.move_queued = lambda src, dst: Player.move_queued(player, src, dst)
    player.remove_queued = lambda i: Player.remove_queued(player, i)
    return player


async def test_viewer_shapes_listener() -> None:
    out = await api.get_music_viewer(_guild(member_vc=10, bot_vc=10), _bot(0), 5)
    assert out['role'] == 'listener'
    assert out['can_control'] and not out['is_manager']
    assert out['voice_channel'] == {'id': '10', 'name': 'vc10'}
    assert out['bot_channel'] == {'id': '10', 'name': 'vc10'}
    assert out['dj_mode'] == 0


async def test_play_requires_voice() -> None:
    with patch.object(api, '_get_guild_player', return_value=(None, None)), pytest.raises(HTTPException) as exc:
        await api.post_music_play(_guild(member_vc=None, bot_vc=None), _bot(), api.PlayBody(user_id=5, query='x'))
    assert exc.value.status_code == 400
    assert exc.value.detail == 'Join a voice channel first.'


async def test_play_enqueues_when_connected() -> None:
    guild, player = _guild(member_vc=10, bot_vc=10), _player(queued=1)
    track = MagicMock(spec=wavelink.Playable)
    track.extras = SimpleNamespace()
    with (
        patch.object(api, '_get_guild_player', return_value=(guild, player)),
        patch.object(api, '_serialize_track', return_value={'title': 't'}),
        patch('app.cogs.music.player.Player.search', AsyncMock(return_value=track)),
    ):
        out = await api.post_music_play(guild, _bot(), api.PlayBody(user_id=5, query='x'))
    assert out == {'queued': [{'title': 't'}], 'started': False}
    assert len(player.queue._items) == 2
    assert track.extras.requester_id == 5


async def test_history_paginates() -> None:
    bot = _bot()
    row = {'title': 'a', 'author': 'b', 'uri': 'u', 'artwork': None, 'length_ms': 9, 'user_id': 5,
           'played_at': datetime.datetime(2026, 1, 1, 12, 0)}
    bot.db.music_plays.guild_history = AsyncMock(return_value=[row])
    out = await api.get_music_history(MagicMock(id=1), bot, 10, '2026-01-01T14:00:00+02:00')
    bot.db.music_plays.guild_history.assert_awaited_once_with(1, limit=10, before=datetime.datetime(2026, 1, 1, 12, 0))
    assert out['items'][0]['user_id'] == '5'
    assert out['items'][0]['played_at'] == '2026-01-01T12:00:00'


async def test_control_remove_requires_destroy() -> None:
    guild, player = _guild(member_vc=10, bot_vc=10), _player(queued=3)
    body = api.ControlBody(action='remove', user_id=5, index=1)
    with patch.object(api, '_get_guild_player', return_value=(guild, player)):
        with pytest.raises(HTTPException) as exc:
            await api.post_music_control(guild, _bot(2), body)  # hybrid, non-DJ
        assert exc.value.status_code == 403
        await api.post_music_control(guild, _bot(0), body)
    assert player.queue._items == [0, 2]


async def test_control_move_accepts_from_key() -> None:
    guild, player = _guild(member_vc=10, bot_vc=10), _player(queued=3)
    body = api.ControlBody.model_validate({'action': 'move', 'user_id': 5, 'from': 0, 'to': 1})
    with patch.object(api, '_get_guild_player', return_value=(guild, player)):
        await api.post_music_control(guild, _bot(0), body)
    assert player.queue._items == [1, 0, 2]


def _tracks(n: int) -> list[MagicMock]:
    out = []
    for i in range(n):
        t = MagicMock(spec=wavelink.Playable)
        t.uri = f'u{i % 18}'  # last two repeat earlier uris
        t.extras = SimpleNamespace()
        out.append(t)
    return out


async def test_play_limit_queues_distinct_mix() -> None:
    guild, player = _guild(member_vc=10, bot_vc=10), _player(queued=0)
    with (
        patch.object(api, '_get_guild_player', return_value=(guild, player)),
        patch.object(api, '_serialize_track', return_value={'title': 't'}),
        patch('app.cogs.music.player.Player.search', AsyncMock(return_value=_tracks(20))),
    ):
        out = await api.post_music_play(guild, _bot(), api.PlayBody(user_id=5, query='x', limit=15))
        assert len(out['queued']) == 15 and len(player.queue._items) == 15
        out = await api.post_music_play(guild, _bot(), api.PlayBody(user_id=5, query='x', limit=25))
        assert len(out['queued']) == 18  # deduped by uri


async def test_play_default_limit_queues_one_of_list() -> None:
    guild, player = _guild(member_vc=10, bot_vc=10), _player(queued=0)
    with (
        patch.object(api, '_get_guild_player', return_value=(guild, player)),
        patch.object(api, '_serialize_track', return_value={'title': 't'}),
        patch('app.cogs.music.player.Player.search', AsyncMock(return_value=_tracks(1)[0])),
    ):
        out = await api.post_music_play(guild, _bot(), api.PlayBody(user_id=5, query='x'))
    assert len(out['queued']) == 1


@pytest.mark.parametrize('n', [0, 26])
def test_play_limit_bounds(n: int) -> None:
    with pytest.raises(ValueError):
        api.PlayBody(user_id=5, query='x', limit=n)


async def test_search_is_cached() -> None:
    api._search_cache.clear()
    node = AsyncMock(return_value=_tracks(3))
    with patch.object(api, '_serialize_track', return_value={'title': 't'}), patch('app.cogs.music.player.Player.search', node):
        a = await api.get_music_search(MagicMock(), ' Lofi ', 10)
        b = await api.get_music_search(MagicMock(), 'lofi', 10)
    assert a == b and node.await_count == 1
    node.return_value = []
    with patch('app.cogs.music.player.Player.search', node):
        await api.get_music_search(MagicMock(), 'nothing', 10)
        await api.get_music_search(MagicMock(), 'nothing', 10)
    assert node.await_count == 3  # empties are not cached


class _SkipQueue(_Queue):
    def put_at(self, i: int, item: object) -> None:
        self._items.insert(i, item)


async def test_play_now_keeps_queue_and_skips() -> None:
    guild, player = _guild(member_vc=10, bot_vc=10), _player()
    player.queue = _SkipQueue()
    player.queue._items = ['A', 'B', 'C']
    player.skip = AsyncMock()
    x = _tracks(1)[0]
    with (
        patch.object(api, '_get_guild_player', return_value=(guild, player)),
        patch.object(api, '_serialize_track', return_value={'title': 'x'}),
        patch('app.cogs.music.player.Player.search', AsyncMock(return_value=x)),
    ):
        await api.post_music_play(guild, _bot(0), api.PlayBody(user_id=5, query='x', play_now=True))
    assert player.queue._items == [x, 'A', 'B', 'C']
    player.skip.assert_awaited_once()


async def test_play_now_needs_destroy() -> None:
    guild, player = _guild(member_vc=10, bot_vc=10), _player()
    with patch.object(api, '_get_guild_player', return_value=(guild, player)), pytest.raises(HTTPException) as exc:
        await api.post_music_play(guild, _bot(2), api.PlayBody(user_id=5, query='x', play_now=True))
    assert exc.value.status_code == 403


def _fx_player() -> MagicMock:
    """A player whose wavelink filters are real, so presets round-trip through ``payload``."""
    player = _player()
    player.filters = wavelink.Filters()
    player.set_filters = AsyncMock()
    return player


async def test_audio_state_defaults_without_player() -> None:
    with patch.object(api, '_get_guild_player', return_value=(None, None)):
        out = await api.get_music_audio(_guild(member_vc=10, bot_vc=10), _bot())
    assert out['active'] is False
    assert out['equalizer'] == {'preset': 'flat', 'bands': [0.0] * 15}
    assert out['filters']['timescale']['speed'] == 1.0
    assert out['filters']['channel_mix'] == {'preset': None}
    assert out['presets']['equalizer_bands']['pop'] and 'pop' in out['presets']['equalizer'] and 'vaporwave' in out['presets']['filters']


async def test_audio_effect_preset_applies_and_reports() -> None:
    guild, player = _guild(member_vc=10, bot_vc=10), _fx_player()
    with patch.object(api, '_get_guild_player', return_value=(guild, player)):
        out = await api.post_music_audio(guild, _bot(0), api.AudioBody(user_id=5, effect='nightcore'))
        assert out['effects'] == ['nightcore'] and out['filters']['timescale']['speed'] == 1.25
        out = await api.post_music_audio(guild, _bot(0), api.AudioBody(user_id=5, effect='mono'))
        assert out['filters']['channel_mix']['preset'] == 'mono'
        out = await api.post_music_audio(guild, _bot(0), api.AudioBody(user_id=5, effect='nightcore', enabled=False))
        assert 'nightcore' not in out['effects']
    assert player.set_filters.await_count == 3


async def test_audio_fine_values_and_reset() -> None:
    guild, player = _guild(member_vc=10, bot_vc=10), _fx_player()
    body = api.AudioBody(user_id=5, speed=1.5, rotation=0.4, low_pass=10, tremolo={'frequency': 3, 'depth': 0.4})
    with patch.object(api, '_get_guild_player', return_value=(guild, player)):
        out = await api.post_music_audio(guild, _bot(0), body)
        f = out['filters']
        assert f['timescale']['speed'] == 1.5 and f['rotation']['rotation_hz'] == 0.4
        assert f['low_pass']['smoothing'] == 10 and f['tremolo']['depth'] == 0.4
        out = await api.post_music_audio(guild, _bot(0), api.AudioBody(user_id=5, reset=True))
    assert out['effects'] == [] and out['filters']['timescale']['speed'] == 1.0


def test_audio_body_rejects_out_of_range() -> None:
    from pydantic import ValidationError

    for bad in ({'speed': 3.0}, {'pitch': 0.1}, {'rotation': 2}, {'low_pass': 99}, {'tremolo': {'frequency': 50, 'depth': 0.5}}):
        with pytest.raises(ValidationError):
            api.AudioBody(user_id=5, **bad)


async def test_audio_requires_voice_presence() -> None:
    guild, player = _guild(member_vc=None, bot_vc=10), _fx_player()
    with patch.object(api, '_get_guild_player', return_value=(guild, player)):
        with pytest.raises(HTTPException) as exc:
            await api.post_music_audio(guild, _bot(0), api.AudioBody(user_id=5, effect='8d'))
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException) as exc:
            await api.post_music_equalizer(guild, _bot(0), api.EqualizerBody(preset="pop", user_id=5))
        assert exc.value.status_code == 403
        with pytest.raises(HTTPException) as exc:
            await api.post_music_audio(_guild(member_vc=10, bot_vc=10), _bot(1), api.AudioBody(user_id=5, effect='8d'))
        assert exc.value.status_code == 403  # DJ-only mode, no DJ role
    player.set_filters.assert_not_awaited()


async def test_equalizer_preset_with_user() -> None:
    guild, player = _guild(member_vc=10, bot_vc=10), _fx_player()
    with patch.object(api, '_get_guild_player', return_value=(guild, player)):
        out = await api.post_music_equalizer(guild, _bot(0), api.EqualizerBody(preset='rock', user_id=5))
    assert out['equalizer'] == api.EQ_PRESETS['rock']


def test_audio_writes_require_user_id() -> None:
    from pydantic import ValidationError

    for model, kw in ((api.EqualizerBody, {'preset': 'pop'}), (api.FiltersBody, {'action': 'reset'}), (api.AudioBody, {})):
        with pytest.raises(ValidationError):
            model(**kw)


async def test_legacy_filters_are_gated_too() -> None:
    guild, player = _guild(member_vc=None, bot_vc=10), _fx_player()
    with patch.object(api, '_get_guild_player', return_value=(guild, player)), pytest.raises(HTTPException) as exc:
        await api.post_music_filters(guild, _bot(0), api.FiltersBody(action='reset', user_id=5))
    assert exc.value.status_code == 403


def _t(title: str, author: str, album: str | None, *, art: str | None = 'a.png', ms: int = 1000) -> SimpleNamespace:
    """A playable-shaped stand-in with invented metadata."""
    return SimpleNamespace(
        title=title, author=author, uri=f'https://x.test/{title}', artwork=art, length=ms, is_stream=False,
        source='spotify', extras=SimpleNamespace(), isrc=None, recommended=False, playlist=None,
        artist=SimpleNamespace(url='https://x.test/artist/1', artwork='face.png'),
        album=SimpleNamespace(name=album, url=f'https://x.test/album/{album}') if album else None,
    )


def _playlist(tracks: list, name: str = 'Glass Tides') -> wavelink.Playlist:
    pl = MagicMock(spec=wavelink.Playlist)
    pl.tracks, pl.name, pl.artwork, pl.url, pl.author = tracks, name, 'cover.png', 'https://x.test/album/1', None
    return pl


async def test_artist_groups_releases_and_caches() -> None:
    api._detail_cache.clear()
    tracks = [_t('One', 'Nova Quill', 'Glass Tides'), _t('Two', 'Nova Quill', 'Glass Tides'), _t('Solo', 'Nova Quill', 'Solo'),
              _t('Else', 'Someone Else', 'Other'), _t('Bare', 'Nova Quill', None)]
    node = AsyncMock(return_value=tracks)
    with patch('app.cogs.music.player.Player.search', node):
        out = await api.get_music_artist(MagicMock(), 'Nova Quill', None)
        again = await api.get_music_artist(MagicMock(), 'Nova Quill', None)
    assert node.await_count == 1 and again is out
    assert [t['title'] for t in out['top_tracks']] == ['One', 'Two', 'Solo', 'Bare']
    assert out['top_tracks'][3]['album'] is None  # missing album: no link, still listed
    assert [(r['title'], r['kind']) for r in out['releases']] == [('Glass Tides', 'album'), ('Solo', 'single')]
    assert out['artist'] == {'name': 'Nova Quill', 'artwork': 'face.png', 'url': 'https://x.test/artist/1'}


async def test_artist_url_uses_top_track_playlist() -> None:
    api._detail_cache.clear()
    pl = _playlist([_t('One', 'Nova Quill', 'Glass Tides')], name="Nova Quill's Top Tracks")
    with patch('app.cogs.music.player.Player.search', AsyncMock(return_value=pl)) as node:
        out = await api.get_music_artist(MagicMock(), 'Nova Quill', 'https://x.test/artist/1')
    node.assert_awaited_once_with('https://x.test/artist/1')
    assert out['artist']['artwork'] == 'cover.png'


async def test_artist_rejects_bad_input_and_empty() -> None:
    api._detail_cache.clear()
    for args in (('', None), ('x', 'javascript:alert(1)')):
        with pytest.raises(HTTPException) as exc:
            await api.get_music_artist(MagicMock(), *args)
        assert exc.value.status_code == 400
    with patch('app.cogs.music.player.Player.search', AsyncMock(return_value=[])), pytest.raises(HTTPException) as exc:
        await api.get_music_artist(MagicMock(), 'Nobody', None)
    assert exc.value.status_code == 404


async def test_album_loads_playlist_and_caches() -> None:
    api._detail_cache.clear()
    pl = _playlist([_t('One', 'Nova Quill, Guest', 'Glass Tides', ms=60_000), _t('Two', 'Nova Quill', 'Glass Tides', ms=30_000)])
    node = AsyncMock(return_value=pl)
    with patch('app.cogs.music.player.Player.search', node):
        out = await api.get_music_album(MagicMock(), 'https://x.test/album/1')
        await api.get_music_album(MagicMock(), 'https://x.test/album/1')
    assert node.await_count == 1
    assert out['album'] == {'title': 'Glass Tides', 'kind': 'album', 'year': None, 'artists': ['Nova Quill'],
                            'artwork': 'cover.png', 'url': 'https://x.test/album/1', 'total_ms': 90_000}
    assert len(out['tracks']) == 2 and out['tracks'][0]['album']['name'] == 'Glass Tides'


async def test_album_not_a_playlist_is_404() -> None:
    api._detail_cache.clear()
    with patch('app.cogs.music.player.Player.search', AsyncMock(return_value=[_t('x', 'y', None)])), pytest.raises(HTTPException) as exc:
        await api.get_music_album(MagicMock(), 'https://x.test/a')
    assert exc.value.status_code == 404


async def test_control_back_without_history_and_bad_loop_mode_are_400() -> None:
    guild, player = _guild(member_vc=10, bot_vc=10), _player()
    player.back = AsyncMock(return_value=False)
    with patch.object(api, '_get_guild_player', return_value=(guild, player)):
        for body in (api.ControlBody(action='back', user_id=5), api.ControlBody(action='loop', user_id=5, mode=7)):
            with pytest.raises(HTTPException) as exc:
                await api.post_music_control(guild, _bot(0), body)
            assert exc.value.status_code == 400
