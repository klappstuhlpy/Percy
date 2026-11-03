"""Response models of the news/comics/releases/watchlist routers must not drop or add a wire key.

FastAPI filters a handler's return through its response model, so a key the model does not
declare silently vanishes from the JSON. Every model is round-tripped against a realistic sample
(built from the pure payload functions where one exists) and must come back identical.
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

from app.internal_api.routers.models.comics import (
    ComicBrowsePage,
    ComicDetail,
    ComicNamesResponse,
    ComicSeriesList,
    ComicSeriesPage,
)
from app.internal_api.routers.models.news import NewsPage, NewsRelated, NewsSourcesResponse
from app.internal_api.routers.models.releases import (
    ProgressResponse,
    ReleaseProgressDeleted,
    ReleasesOverview,
    SubjectsResponse,
    Subscription,
    SubscriptionDeleted,
    SubscriptionsResponse,
)
from app.internal_api.routers.models.watchlist import (
    WatchPeopleResponse,
    WatchPersonPage,
    WatchPrefs,
    WatchProgressDeleted,
    WatchProgressResponse,
    WatchStats,
    WatchTitleDetail,
    WatchUniversePage,
    WatchUniversesResponse,
)
from app.services.comics.payload import (
    browse_payload as comic_browse,
)
from app.services.comics.payload import (
    names_payload,
    release_payload,
    series_context,
    series_detail_payload,
    series_summary_payload,
)
from app.services.news.payload import browse_payload as news_browse
from app.services.news.payload import item_payload, source_payload
from app.services.releases.progress import progress_payload
from app.services.watchlist.payload import (
    credit_payloads,
    person_payload,
    person_summary,
    universe_payload,
    universe_stats,
    universe_summary,
)

if TYPE_CHECKING:
    from pydantic import BaseModel

NOW = datetime.datetime(2026, 11, 1, 12, 0, 0)
TODAY = datetime.date(2026, 11, 1)


def roundtrip(model: type[BaseModel], sample: Any) -> None:
    assert model.model_validate(sample).model_dump(mode='json', by_alias=True) == sample


# -- news ---------------------------------------------------------------------

_SOURCES = {'cbr': {'name': 'CBR', 'homepage': 'https://cbr.com'}}
_NEWS_ROW = {
    'id': 7, 'url': 'https://cbr.com/a', 'headline': 'Big news', 'excerpt': 'short', 'image_url': 'https://i/x.jpg',
    'author': 'Jane', 'published_at': NOW, 'fetched_at': NOW, 'source_slug': 'cbr',
}
_SUBJECTS = [{'media': 'comic', 'subject_type': 'series', 'subject_value': 'x-men'}]


def _news_item(**over: Any) -> dict[str, Any]:
    return item_payload({**_NEWS_ROW, **over}, sources=_SOURCES, subjects=_SUBJECTS)


def test_news_page() -> None:
    filters = {'media': 'comic', 'subject_type': None, 'subject_value': None, 'source': None, 'since': None, 'search': 'x'}
    roundtrip(NewsPage, news_browse([_news_item(), _news_item(id=8, excerpt=None, published_at=None)],
                                    total=2, page=1, per_page=25, filters=filters, followed=[_news_item(id=9)]))


def test_news_related_and_sources() -> None:
    roundtrip(NewsRelated, {'items': [_news_item()]})
    # unknown source: slug fallback with no homepage
    roundtrip(NewsRelated, {'items': [item_payload(_NEWS_ROW, sources={}, subjects=())]})
    row = {'slug': 'cbr', 'name': 'CBR', 'homepage': None, 'feed_url': 'https://f', 'media': 'both',
           'enabled': True, 'last_fetched': NOW, 'last_status': 'ok'}
    roundtrip(NewsSourcesResponse, {'sources': [source_payload(row), source_payload({**row, 'last_fetched': None, 'last_status': None})]})


# -- comics -------------------------------------------------------------------

_COMIC_ROW = {
    'id': 1, 'brand': 'marvel', 'slug': 'x-men-1', 'title': 'X-Men #1', 'series_slug': 'x-men', 'series_name': 'X-Men',
    'issue_number': '1', 'release_date': TODAY, 'cover_url': 'https://c', 'description': 'd', 'price': 3.99,
    'page_count': 32, 'format': 'Comic', 'publisher': 'Marvel', 'url': 'https://u', 'first_seen': NOW, 'updated_at': NOW,
}


def test_comics_models() -> None:
    other = {**_COMIC_ROW, 'id': 2, 'slug': 'x-men-2', 'issue_number': '2', 'price': None, 'release_date': None}
    credits = [{'name': 'Stan', 'role': 'writer'}]
    chars = [{'name': 'Cyclops', 'kind': None}]
    filters = {'brand': None, 'series': 'x-men', 'creator': None, 'character': None, 'since': None, 'until': None, 'search': None}
    roundtrip(ComicBrowsePage, comic_browse([_COMIC_ROW, other], total=2, page=1, per_page=24, filters=filters))
    detail = release_payload(_COMIC_ROW, credits=credits, characters=chars)
    detail['series'] = series_context([_COMIC_ROW, other], 1)
    roundtrip(ComicDetail, detail)
    detail['series'] = {'previous': None, 'next': None, 'more_from_series': []}
    roundtrip(ComicDetail, detail)
    summary = series_summary_payload({'series_slug': 'x-men', 'series_name': 'X-Men', 'brand': 'marvel',
                                      'issue_count': 2, 'latest': TODAY})
    roundtrip(ComicSeriesList, {'series': [summary]})
    roundtrip(ComicSeriesPage, series_detail_payload([_COMIC_ROW, other]))
    roundtrip(ComicNamesResponse, names_payload('creators', [{'name': 'Stan', 'appearances': 4}]))


# -- releases -----------------------------------------------------------------

_CARD = {
    'media': 'comic', 'key': 'marvel/x-men-1', 'name': 'X-Men #1', 'subtitle': 'X-Men #1', 'release_date': '2026-11-01',
    'cover_url': 'https://c', 'url': 'https://u',
    'subjects': [{'subject_type': 'series', 'subject_value': 'x-men', 'label': 'X-Men'}],
}
_PROGRESS_ROWS = [
    {'media': 'comic', 'release_key': 'marvel/x-men-1', 'status': 'read', 'updated_at': NOW},
    {'media': 'title', 'release_key': 'mcu/iron-man', 'status': 'watched', 'updated_at': None},
]


def test_releases_models() -> None:
    sub = {'media': 'comic', 'subject_type': 'series', 'subject_value': 'x-men', 'subject_label': None,
           'streams': 'releases', 'delivery': 'dm', 'guild_id': '123456789012345678', 'created_at': NOW.isoformat()}
    roundtrip(Subscription, sub)
    roundtrip(SubscriptionsResponse, {'subscriptions': [sub]})
    roundtrip(SubscriptionDeleted, {'deleted': True})
    roundtrip(ReleaseProgressDeleted, {'deleted': 3})
    roundtrip(SubjectsResponse, {'subjects': [
        {'media': 'comic', 'subject_type': 'brand', 'subject_value': 'dc', 'label': 'DC', 'hint': 'every release'}]})
    progress = progress_payload(_PROGRESS_ROWS)
    roundtrip(ProgressResponse, {'merged': 2, 'entries': progress})
    roundtrip(ProgressResponse, {'merged': 0, 'entries': progress_payload([])})
    roundtrip(ReleasesOverview, {
        'window': {'start': '2026-10-25', 'end': '2026-11-08'},
        'comics': [_CARD], 'titles': [{**_CARD, 'media': 'title', 'subtitle': None, 'url': None}],
        'followed': [_CARD], 'progress': progress,
    })


# -- watchlist ----------------------------------------------------------------

_TITLE_ROW = {
    'id': 1, 'universe': 'mcu', 'tmdb_type': 'movie', 'tmdb_id': 1726, 'slug': 'iron-man', 'title': 'Iron Man',
    'kind': 'movie', 'runtime': 126, 'episodes': None, 'poster_path': '/p.jpg', 'backdrop_path': None,
    'overview': 'o', 'tmdb_votes': 100, 'era': 'phase-1', 'era_order': 1, 'story_order': 1, 'release_order': 1,
    'milestone': True, 'instruction': None, 'context': None, 'spoiler': None, 'credits_of': None,
    'sub_universe': 'Iron Man', 'season_number': None, 'season_name': None, 'parent_id': None,
    'release_date': TODAY, 'tmdb_rating': 7.9, 'importance': 'essential',
    'trailer_site': 'youtube', 'trailer_key': 'abc123', 'trailer_name': 'Trailer',
}
_PROVIDER = {'provider_id': 8, 'provider_name': 'Netflix', 'logo_path': '/n.jpg', 'offer': 'flatrate', 'link': None}
_UNIVERSE = {'slug': 'mcu', 'name': 'Marvel Cinematic Universe', 'short_name': 'MCU', 'tagline': None,
             'accent': 0xE62429, 'logo_url': None, 'backdrop_url': None, 'published': True, 'synced_at': NOW}
_PATH = {'slug': 'story', 'name': 'Story', 'description': None, 'is_default': True, 'sort_order': 0}


def _page(**over: Any) -> dict[str, Any]:
    titles = [_TITLE_ROW, {**_TITLE_ROW, 'id': 2, 'slug': 'hulk', 'trailer_site': None, 'importance': None}]
    return universe_payload(_UNIVERSE, [_PATH], titles, [{**_PROVIDER, 'title_id': 1}],
                            path_slug='story', region='US', sort='story', **over)


def test_watch_universe_models() -> None:
    summary = universe_summary({**_UNIVERSE, 'sort_order': 1, 'title_count': 30, 'total_runtime': 4000})
    roundtrip(WatchUniversesResponse, {'universes': [summary]})
    page = _page()
    roundtrip(WatchUniversePage, page)
    stats = {'universe': 'mcu', 'path': None, **universe_stats([_TITLE_ROW])}
    roundtrip(WatchStats, stats)


def test_watch_title_and_people_models() -> None:
    page = _page()
    first, second = page['titles']
    credit = {'tmdb_person_id': 1, 'name': 'RDJ', 'slug': 'rdj', 'profile_path': None, 'role': 'cast',
              'character_name': 'Tony', 'billing_order': 0}
    roundtrip(WatchTitleDetail, {
        'title': first, 'credits_of': None, 'parent': None, 'seasons': [], 'credits': credit_payloads([credit]),
        'previous': None, 'next': second, 'position': 1, 'total': 2, 'path': 'story', 'sort': 'story',
        'universe': page['universe'],
    })
    summary = {'tmdb_person_id': 1, 'name': 'RDJ', 'slug': 'rdj', 'profile_path': None, 'credit_count': 5}
    roundtrip(WatchPeopleResponse, {'people': [person_summary(summary)]})
    person = {'tmdb_person_id': 1, 'name': 'RDJ', 'slug': 'rdj', 'profile_path': '/r.jpg', 'updated_at': NOW}
    credit_row = {**_TITLE_ROW, 'role': 'cast', 'character_name': 'Tony', 'billing_order': 0,
                  'universe_name': 'MCU', 'universe_accent': 0xE62429}
    roundtrip(WatchPersonPage, person_payload(person, [credit_row]))


def test_watch_progress_and_prefs_models() -> None:
    roundtrip(WatchProgressResponse, {'entries': {'1': {'status': 'watched', 'updated_at': NOW.isoformat()}}})
    roundtrip(WatchProgressDeleted, {'deleted': 2})
    prefs = {'region': 'US', 'path_slug': None, 'sort_mode': 'story', 'show_spoiler': False,
             'with_credits': True, 'hide_sub': False, 'weekly_hours': None, 'target_date': None}
    roundtrip(WatchPrefs, prefs)
    roundtrip(WatchPrefs, {**prefs, 'path_slug': 'story', 'weekly_hours': {'mon': 2}, 'target_date': '2026-12-01'})
