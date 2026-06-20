from __future__ import annotations

from app.services.lyrics import LyricsResult, SyncedLyrics, clean_track_title, parse_lrc

SAMPLE_LRC = """[ar:Some Artist]
[ti:Some Song]
[length:02:00.00]
[00:01.00]First line
[00:03.50]Second line
[00:05.00]
[00:07.20]Last line
"""


def test_parse_lrc_skips_metadata_and_orders_by_time() -> None:
    lines = parse_lrc(SAMPLE_LRC)
    assert [line.timestamp for line in lines] == [1000, 3500, 5000, 7200]
    assert [line.text for line in lines] == ["First line", "Second line", "", "Last line"]


def test_parse_lrc_handles_multiple_tags_per_line() -> None:
    lines = parse_lrc("[00:10.00][01:20.00]Chorus")
    assert lines == [(10000, "Chorus"), (80000, "Chorus")]


def test_parse_lrc_fraction_normalisation() -> None:
    # ``.5`` is 500ms (centi/deci), ``.34`` is 340ms, ``.345`` is 345ms.
    assert parse_lrc("[00:00.5]a")[0].timestamp == 500
    assert parse_lrc("[00:00.34]a")[0].timestamp == 340
    assert parse_lrc("[00:00.345]a")[0].timestamp == 345


def test_parse_lrc_empty_or_none() -> None:
    assert parse_lrc(None) == []
    assert parse_lrc("") == []
    assert parse_lrc("no timestamps here") == []


def test_active_index_boundaries() -> None:
    synced = SyncedLyrics(parse_lrc(SAMPLE_LRC))
    assert synced.active_index(0) == -1       # before the first line
    assert synced.active_index(1000) == 0     # exactly on the first line
    assert synced.active_index(4000) == 1     # between line 1 and 2
    assert synced.active_index(7200) == 3     # on the last line
    assert synced.active_index(999_999) == 3  # past the end -> stays on last


def test_next_timestamp() -> None:
    synced = SyncedLyrics(parse_lrc(SAMPLE_LRC))
    assert synced.next_timestamp(-1) == 1000   # next after "before first" is line 0
    assert synced.next_timestamp(0) == 3500
    assert synced.next_timestamp(3) is None    # nothing after the last line


def test_render_marks_current_line_bold() -> None:
    synced = SyncedLyrics(parse_lrc(SAMPLE_LRC))
    out = synced.render(3600, before=1, after=2)
    # The active line at 3600ms is "Second line"; the current line plus the next
    # one are both bold (the empty next line renders as a bold ♪).
    assert "**Second line**" in out
    assert "**♪**" in out
    # Context above the active line stays as subtext.
    assert "-# First line" in out


def test_render_highlight_count_is_configurable() -> None:
    synced = SyncedLyrics(parse_lrc(SAMPLE_LRC))
    # highlight=1 -> only the active line ("First line") is bold; the next is context.
    out = synced.render(1000, before=0, after=2, highlight=1)
    assert "**First line**" in out
    assert "-# Second line" in out


def test_render_empty_when_no_lines() -> None:
    assert SyncedLyrics([]).render(1000) == ""


def test_clean_track_title_strips_noise() -> None:
    assert clean_track_title("Song Name (Official Music Video)") == "Song Name"
    assert clean_track_title("Song Name [Lyrics]") == "Song Name"
    assert clean_track_title("Song Name (Remastered 2011)") == "Song Name"
    # A legitimate parenthetical (no noise keyword) is preserved.
    assert clean_track_title("Song Name (Acoustic)") == "Song Name (Acoustic)"


def test_lyrics_result_helpers() -> None:
    synced = SyncedLyrics(parse_lrc(SAMPLE_LRC))
    result = LyricsResult(title="x", source="LRCLIB", synced=synced)
    assert result.has_synced is True
    # best_text falls back to flattened synced text when no plain form exists.
    assert "First line" in result.best_text()

    plain_only = LyricsResult(title="x", source="Genius", plain="hello\nworld")
    assert plain_only.has_synced is False
    assert plain_only.best_text() == "hello\nworld"
