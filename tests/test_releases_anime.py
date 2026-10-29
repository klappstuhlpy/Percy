from __future__ import annotations

import datetime

from app.cogs.releases.ui import decode_subject
from app.services.releases import Subject, anime_releasable, match

AIRED = datetime.datetime(2026, 10, 29, 15, 30)
SCHEDULE = {
    'airingAt': int(AIRED.replace(tzinfo=datetime.UTC).timestamp()),
    'episode': 7,
    'media': {
        'id': 154587,
        'title': {'romaji': 'Sousou no Frieren', 'english': 'Frieren'},
        'siteUrl': 'https://anilist.co/anime/154587',
        'coverImage': {'large': 'https://img/cover.png'},
        'studios': {'nodes': [{'id': 11, 'name': 'Madhouse'}]},
    },
}


def _sub(subject_type: str, value: str, created_at: datetime.datetime) -> dict:
    return {
        'user_id': 1, 'media': 'anime', 'subject_type': subject_type, 'subject_value': value,
        'streams': 'releases', 'delivery': 'dm', 'created_at': created_at,
    }


def test_episode_maps_to_show_and_studio_subjects() -> None:
    item = anime_releasable(SCHEDULE)
    assert item.media == 'anime'
    assert item.key == '154587/7'
    assert item.name == 'Sousou no Frieren — Episode 7'
    assert item.first_seen == AIRED
    assert item.release_date == AIRED.date()
    assert item.subjects == {Subject('anime', 'series', '154587'), Subject('anime', 'studio', '11')}


def test_followers_get_episodes_that_aired_after_they_followed() -> None:
    item = anime_releasable(SCHEDULE)
    before, after = AIRED - datetime.timedelta(days=1), AIRED + datetime.timedelta(hours=1)
    assert match([item], [_sub('series', '154587', before)]) == {1: [item]}
    assert match([item], [_sub('studio', '11', before)]) == {1: [item]}
    assert match([item], [_sub('series', '154587', after)]) == {}


def test_anime_subject_keys_decode() -> None:
    assert decode_subject('anime:series:154587') == ('anime', 'series', '154587')
    assert decode_subject('anime:person:x') is None
