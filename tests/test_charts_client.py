from __future__ import annotations

from app.clients.charts import chart_tracks


def test_chart_tracks_normalises_and_skips_bad_rows() -> None:
    payload = {'data': [
        {'title': 'Song', 'duration': 201, 'link': 'https://deezer.com/track/1',
         'artist': {'name': 'Band'}, 'album': {'cover_medium': 'https://img/1.jpg'}},
        {'title': ''},
        'junk',
        {'title': 'Bare'},
    ]}
    assert chart_tracks(payload) == [
        {'title': 'Song', 'author': 'Band', 'artwork': 'https://img/1.jpg', 'length_ms': 201000,
         'url': 'https://deezer.com/track/1'},
        {'title': 'Bare', 'author': None, 'artwork': None, 'length_ms': None, 'url': None},
    ]


def test_chart_tracks_tolerates_error_payloads() -> None:
    assert chart_tracks({'error': {'code': 4}}) == []
    assert chart_tracks('<html>') == []
