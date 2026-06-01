"""Tests for the time parsing and formatting helpers in :mod:`app.utils.timetools`.

All tests inject a fixed ``now``/``source`` so they are deterministic and do not
depend on the wall clock.
"""

import datetime

import pytest
from discord.ext import commands

from app.utils import timetools

NOW = datetime.datetime(2024, 1, 1, 12, 0, 0, tzinfo=datetime.UTC)


class TestShortTime:
    def test_parses_hours(self) -> None:
        st = timetools.ShortTime('2h', now=NOW)
        assert st.dt == NOW + datetime.timedelta(hours=2)

    def test_parses_minutes(self) -> None:
        st = timetools.ShortTime('30m', now=NOW)
        assert st.dt == NOW + datetime.timedelta(minutes=30)

    def test_parses_combined(self) -> None:
        st = timetools.ShortTime('1h30m', now=NOW)
        assert st.dt == NOW + datetime.timedelta(hours=1, minutes=30)

    def test_parses_days(self) -> None:
        st = timetools.ShortTime('3d', now=NOW)
        assert st.dt == NOW + datetime.timedelta(days=3)

    def test_as_timedelta(self) -> None:
        st = timetools.ShortTime('2h', now=NOW, as_timedelta=True)
        assert st.dt == datetime.timedelta(hours=2)

    def test_discord_timestamp(self) -> None:
        ts = int(NOW.timestamp())
        st = timetools.ShortTime(f'<t:{ts}:f>', now=NOW)
        assert st.dt == NOW

    def test_invalid_raises(self) -> None:
        with pytest.raises(commands.BadArgument):
            timetools.ShortTime('not a time', now=NOW)


class TestConvertDuration:
    @pytest.mark.parametrize(('ms', 'expected'), [
        (0, '00:00'),
        (30_000, '00:30'),
        (90_000, '01:30'),
        (3_600_000, '01:00:00'),
        (3_661_000, '01:01:01'),
    ])
    def test_convert(self, ms: int, expected: str) -> None:
        assert timetools.convert_duration(ms) == expected


class TestHumanizeDuration:
    def test_sub_second(self) -> None:
        assert timetools.humanize_duration(0.5) == '<1 second'

    def test_minutes_and_seconds(self) -> None:
        assert timetools.humanize_duration(90) == '1 minute and 30 seconds'

    def test_single_unit(self) -> None:
        assert timetools.humanize_duration(60) == '1 minute'

    def test_depth_limits_output(self) -> None:
        # 1h 1m 1s, limited to a depth of 2 units.
        result = timetools.humanize_duration(3661, depth=2)
        assert result == '1 hour and 1 minute'

    def test_accepts_timedelta(self) -> None:
        assert timetools.humanize_duration(datetime.timedelta(minutes=2)) == '2 minutes'

    def test_caps_huge_durations(self) -> None:
        assert timetools.humanize_duration(60 * 60 * 24 * 365 * 200) == '>100 years'


class TestHumanTimedelta:
    def test_future(self) -> None:
        dt = NOW + datetime.timedelta(days=2)
        assert timetools.human_timedelta(dt, source=NOW) == '2 days'

    def test_past_has_ago_suffix(self) -> None:
        dt = NOW - datetime.timedelta(hours=1)
        assert timetools.human_timedelta(dt, source=NOW) == '1 hour ago'

    def test_no_suffix_when_disabled(self) -> None:
        dt = NOW - datetime.timedelta(hours=1)
        assert timetools.human_timedelta(dt, source=NOW, suffix=False) == '1 hour'

    def test_now(self) -> None:
        assert timetools.human_timedelta(NOW, source=NOW) == 'now'

    def test_brief_format(self) -> None:
        dt = NOW + datetime.timedelta(hours=2, minutes=30)
        assert timetools.human_timedelta(dt, source=NOW, brief=True) == '2h 30m'

    def test_accuracy_limits_units(self) -> None:
        dt = NOW + datetime.timedelta(days=1, hours=2, minutes=3, seconds=4)
        result = timetools.human_timedelta(dt, source=NOW, accuracy=2)
        # Only the two largest units are kept.
        assert result == '1 day and 2 hours'


class TestGetTimezoneOffset:
    def test_utc(self) -> None:
        assert timetools.get_timezone_offset(NOW) == '+00:00'

    def test_positive_offset(self) -> None:
        tz = datetime.timezone(datetime.timedelta(hours=2))
        dt = datetime.datetime(2024, 1, 1, tzinfo=tz)
        assert timetools.get_timezone_offset(dt) == '+02:00'

    def test_negative_offset(self) -> None:
        tz = datetime.timezone(datetime.timedelta(hours=-5))
        dt = datetime.datetime(2024, 1, 1, tzinfo=tz)
        assert timetools.get_timezone_offset(dt) == '-05:00'


class TestEnsureFutureTime:
    def test_valid_future(self) -> None:
        result = timetools.ensure_future_time('2h', NOW)
        assert result > NOW.replace(tzinfo=None)

    def test_too_soon_raises(self) -> None:
        with pytest.raises(commands.BadArgument):
            timetools.ensure_future_time('1s', NOW)


class TestHumanizeSmallDuration:
    def test_returns_string_with_unit(self) -> None:
        result = timetools.humanize_small_duration(0.5)
        assert result.endswith('ms')

    def test_tiny_value(self) -> None:
        assert timetools.humanize_small_duration(0.0) == '<1 ps'
