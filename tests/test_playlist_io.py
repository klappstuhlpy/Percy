import json

import pytest

from app.services.music.playlist_io import MAX_TRACKS, PlaylistImportError, parse_playlist, serialize_playlist


def test_roundtrip() -> None:
    text = serialize_playlist("Mix", [("A", "Art", "https://x.test/a", 1000), ("B", None, "http://x.test/b", 0)])
    p = parse_playlist(text)
    assert p.name == "Mix"
    assert [(t.title, t.author, t.uri) for t in p.tracks] == [
        ("A", "Art", "https://x.test/a"),
        ("B", None, "http://x.test/b"),
    ]


def test_red_shape() -> None:
    raw = json.dumps(
        {"playlist_name": "R", "tracks": [{"info": {"uri": "https://y.test", "title": "T", "author": "Z", "length": 5}}]}
    )
    p = parse_playlist(raw)
    assert p.name == "R" and p.tracks[0].title == "T"


@pytest.mark.parametrize(
    "raw",
    [
        "nope",
        "[]",
        "{}",
        '{"tracks": [1]}',
        '{"tracks": [{"uri": "ftp://x"}]}',
        '{"tracks": [{"title": "x"}]}',
        '{"tracks": [{"uri": "https://x", "title": 3}]}',
        json.dumps({"tracks": [{"uri": "https://x"}] * (MAX_TRACKS + 1)}),
    ],
)
def test_rejects(raw) -> None:
    with pytest.raises(PlaylistImportError):
        parse_playlist(raw)


def test_max_tracks_ok_and_title_fallback() -> None:
    p = parse_playlist(json.dumps({"tracks": [{"uri": "https://x"}] * MAX_TRACKS}))
    assert len(p.tracks) == MAX_TRACKS and p.tracks[0].title == "https://x" and p.name is None


def test_rejects_deep_and_oversize() -> None:
    with pytest.raises(PlaylistImportError):
        parse_playlist("[" * 100000)
    with pytest.raises(PlaylistImportError):
        parse_playlist(" " * (512 * 1024 + 1))
