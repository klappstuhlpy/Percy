import pytest

from app.services.music_access import resolve_viewer


@pytest.mark.parametrize(
    ('has_dj', 'user_ch', 'bot_ch', 'mode', 'expected'),
    [
        (True, None, 5, 0, ('manager', False, True, True)),
        (True, 7, None, 1, ('manager', True, True, True)),
        (False, 5, 5, 0, ('listener', False, True, True)),
        (False, 5, 5, 2, ('listener', False, True, False)),
        (False, 5, 5, 1, ('visitor', False, False, False)),
        (False, 6, 5, 0, ('visitor', False, False, False)),
        (False, None, None, 0, ('visitor', False, False, False)),
        (False, 7, None, 0, ('listener', True, False, False)),
        (False, 7, None, 1, ('visitor', False, False, False)),
    ],
)
def test_resolve_viewer(has_dj, user_ch, bot_ch, mode, expected) -> None:
    v = resolve_viewer(has_dj=has_dj, user_channel_id=user_ch, bot_channel_id=bot_ch, dj_mode=mode)
    assert (v.role, v.can_start, v.can_control, v.can_destroy) == expected
    assert v.is_manager is has_dj
