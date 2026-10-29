from __future__ import annotations

from app.cogs.anilist.ui import follow_target, format_date, month_to_season, related_media, sanitize_description


def test_cut_inside_spoiler_closes_it() -> None:
    text = sanitize_description('Intro ~!the big twist is here!~ end', 20)
    assert text.endswith('...||')
    assert text.count('||') == 2


def test_description_strips_html_and_markdown() -> None:
    assert sanitize_description('<i>Hi</i> **there**', 100) == 'Hi there'
    assert sanitize_description(None, 10) == 'N/A'


def test_partial_dates_stay_text() -> None:
    assert format_date(year=2024) == '2024'
    assert format_date(year=2024, month=4) == 'April 2024'
    assert format_date() == 'N/A'
    assert format_date(year=2024, month=4, day=7).startswith('<t:')


def test_seasons() -> None:
    assert [month_to_season(m) for m in (1, 3, 4, 7, 10, 12)] == ['WINTER', 'WINTER', 'SPRING', 'SUMMER', 'FALL', 'FALL']


def test_related_media_dedupes_and_drops_adult_and_self() -> None:
    node = {'id': 2, 'format': 'TV', 'title': {'romaji': 'Season 2'}}
    data = {
        'id': 1,
        'relations': {'edges': [
            {'relationType': 'SEQUEL', 'node': node},
            {'relationType': 'SIDE_STORY', 'node': {'id': 3, 'isAdult': True, 'title': {'romaji': 'NSFW'}}},
            {'relationType': 'ALTERNATIVE', 'node': {'id': 1, 'title': {'romaji': 'Itself'}}},
        ]},
        'recommendations': {'nodes': [{'mediaRecommendation': node}, {'mediaRecommendation': None}]},
    }
    assert related_media('media', data) == [('Season 2', 'Sequel · TV', 2)]


def test_people_link_their_works() -> None:
    staff = {'id': 2, 'staffMedia': {'nodes': [{'id': 2, 'format': 'MOVIE', 'title': {'romaji': 'Film'}}]}}
    assert related_media('staff', staff) == [('Film', 'Known for · Movie', 2)]


def test_only_airing_anime_and_studios_are_followable() -> None:
    show = {'id': 5, 'type': 'ANIME', 'status': 'RELEASING', 'title': {'romaji': 'Show'}}
    assert follow_target('media', show) == ('series', '5', 'Show')
    assert follow_target('media', {**show, 'status': 'FINISHED'}) is None
    assert follow_target('media', {**show, 'type': 'MANGA'}) is None
    assert follow_target('studio', {'id': 9, 'name': 'Madhouse'}) == ('studio', '9', 'Madhouse')
    assert follow_target('character', {'id': 1}) is None
