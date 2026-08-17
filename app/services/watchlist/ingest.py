"""TMDB ingest for the universe watchlist: seed + TMDB payload -> ``watch_*`` table rows.

Split in the same spirit as ``app/services/ai/service.py``: the mapping functions
(:func:`slugify`, :func:`movie_row`, :func:`tv_row`, :func:`provider_rows`,
:func:`discovered_titles`) are pure --
no network, no database, unit-tested directly with hand-built payloads. The only impure
part is :class:`WatchlistIngest`, which is the sole caller of the TMDB client and the
watchlist repository, mirroring how ``AIService`` is the sole caller of ``OllamaClient``.

**Rule enforced throughout the row builders (see their docstrings):** curation columns
(``era``, ``era_order``, ``story_order``, ``release_order``, ``milestone``, ``instruction``,
``context``, ``spoiler``, ``sub_universe``, ``kind``) come only from the seed; factual
columns (``title``, ``runtime``, ``episodes``, ``release_date``, ``poster_path``,
``backdrop_path``, ``overview``, ``tmdb_rating``, ``tmdb_votes``) come only from TMDB. No
column is ever written from both sources -- keeps a re-sync from letting a curated field
drift back to whatever TMDB happens to say, and vice versa.
"""

from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import asyncpg

# Matches the AIService pattern: the *orchestrator* below is allowed to touch the resilient
# HTTP-client error hierarchy and asyncpg's exception hierarchy (it does real I/O); the pure
# mapping functions above it never import discord/asyncpg/aiohttp and never raise or catch these.
from app.clients.base import HTTPClientError
from app.services.watchlist.people import credit_rows
from app.services.watchlist.seeds import TitleSeed, accent_to_int
from app.services.watchlist.trailers import select_trailer
from app.utils.text import slugify

if TYPE_CHECKING:
    from collections.abc import Container, Iterable, Mapping, Sequence

    from app.clients.tmdb import TMDBClient
    from app.database.repositories.watchlist import WatchlistRepository
    from app.services.watchlist.seeds import DiscoverSeed, PathSeed, UniverseSeed

__all__ = (
    'DISCOVER_ORDER_BASE',
    'DISCOVER_ORDER_STEP',
    'DISCOVER_PAGE_CAP',
    'IngestReport',
    'WatchlistIngest',
    'discovered_titles',
    'movie_row',
    'provider_rows',
    'slugify',
    'tv_row',
)

log = logging.getLogger(__name__)

#: TMDB's ``watch/providers`` result buckets, in the order rows are emitted.
_PROVIDER_BUCKETS = ('flatrate', 'rent', 'buy')

#: Hard cap on pages fetched per ``[[discover]]`` block (TMDB pages hold 20 results, so 200
#: titles). A mis-specified query -- a company id that owns a back catalogue, say -- must cost
#: one bounded crawl, not a 400-page one.
DISCOVER_PAGE_CAP = 10

#: ``story``/``release`` order of the first discovered title, and the gap between two.
#: The base sits far above the hundreds hand-written seeds use, so every discovered title
#: sorts after every curated one; the step leaves room to slot a title in during a later
#: curation pass without renumbering its neighbours.
DISCOVER_ORDER_BASE = 10000
DISCOVER_ORDER_STEP = 10


def _parse_date(value: str | None) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _curation_columns(seed_title: TitleSeed, era_order: int) -> dict[str, Any]:
    """Columns that come only from seed curation -- never touched by a TMDB payload."""
    return {
        'kind': seed_title.resolved_kind,
        'era': seed_title.era,
        'era_order': era_order,
        'story_order': seed_title.story,
        'release_order': seed_title.release,
        'milestone': seed_title.milestone,
        'instruction': seed_title.instruction,
        'context': seed_title.context,
        'spoiler': seed_title.spoiler,
        'sub_universe': seed_title.sub_universe,
    }


def movie_row(seed_title: TitleSeed, payload: Mapping[str, Any], *, universe: str, era_order: int) -> dict[str, Any]:
    """Maps a TMDB ``movie/{id}`` payload + seed curation onto the ``watch_titles`` column dict.

    ``universe`` is accepted for signature symmetry with :func:`tv_row` (which needs it to
    scope a missing-runtime warning) but movies have no equivalent fallback chain, so it is
    unused here.
    """
    del universe  # see docstring
    title = payload.get('title') or ''
    return {
        **_curation_columns(seed_title, era_order),
        'tmdb_type': seed_title.tmdb_type,
        'tmdb_id': seed_title.tmdb_id,
        'slug': slugify(title) or str(seed_title.tmdb_id),
        'title': title,
        'runtime': payload.get('runtime') or 0,
        'episodes': None,
        'release_date': _parse_date(payload.get('release_date')),
        'poster_path': payload.get('poster_path'),
        'backdrop_path': payload.get('backdrop_path'),
        'overview': payload.get('overview'),
        'tmdb_rating': payload.get('vote_average'),
        'tmdb_votes': payload.get('vote_count'),
    }


def _wanted_seasons(seed_title: TitleSeed, available: Iterable[int]) -> set[int]:
    """Season numbers that count toward runtime/episodes: pinned seasons, else all but 0."""
    if seed_title.seasons is not None:
        return set(seed_title.seasons)
    return {number for number in available if number != 0}


def tv_row(
    seed_title: TitleSeed,
    payload: Mapping[str, Any],
    season_payloads: Mapping[int, Mapping[str, Any]],
    *,
    universe: str,
    era_order: int,
) -> dict[str, Any]:
    """Maps a TMDB ``tv/{id}`` payload + seed curation onto the ``watch_titles`` column dict.

    ``season_payloads`` should carry every season TMDB lists for the show, keyed by season
    number -- this function itself selects which of them count toward ``runtime``/``episodes``
    (:func:`_wanted_seasons`: ``seed_title.seasons`` if pinned, else every season except 0).

    Runtime is the sum of every counted episode's ``runtime``; a missing per-episode value
    falls back to the show's ``episode_run_time[0]``, and a title missing *both* gets a 0 for
    that episode plus a warning recorded under the returned dict's ``'_warnings'`` key -- an
    extra key the orchestrator pops off and folds into :class:`IngestReport.warnings`, since
    this function itself must stay side-effect-free.

    ``'_created_by'`` is carried out the same way: a show's creators live on this payload and
    *not* on TMDB's ``credits`` response, so the orchestrator pops them off here and hands
    them to :func:`~app.services.watchlist.people.credit_rows` rather than re-fetching the
    show. Movies have no equivalent and :func:`movie_row` emits no such key.
    """
    wanted = _wanted_seasons(seed_title, season_payloads.keys())
    fallback_runtime = next(iter(payload.get('episode_run_time') or []), None)

    runtime = 0
    episodes = 0
    missing_runtime = False
    for number in wanted:
        season = season_payloads.get(number) or {}
        for episode in season.get('episodes') or []:
            episodes += 1
            episode_runtime = episode.get('runtime')
            if episode_runtime is None:
                episode_runtime = fallback_runtime
            if episode_runtime is None:
                episode_runtime = 0
                missing_runtime = True
            runtime += episode_runtime

    title = payload.get('name') or ''
    row: dict[str, Any] = {
        **_curation_columns(seed_title, era_order),
        'tmdb_type': seed_title.tmdb_type,
        'tmdb_id': seed_title.tmdb_id,
        'slug': slugify(title) or str(seed_title.tmdb_id),
        'title': title,
        'runtime': runtime,
        'episodes': episodes,
        'release_date': _parse_date(payload.get('first_air_date')),
        'poster_path': payload.get('poster_path'),
        'backdrop_path': payload.get('backdrop_path'),
        'overview': payload.get('overview'),
        'tmdb_rating': payload.get('vote_average'),
        'tmdb_votes': payload.get('vote_count'),
    }
    created_by = payload.get('created_by')
    if created_by:
        row['_created_by'] = created_by
    if missing_runtime:
        row['_warnings'] = [f'{universe}/{row["slug"]}: at least one episode has no runtime data, counted as 0']
    return row


def provider_rows(payload: Mapping[str, Any], region: str) -> list[dict[str, Any]]:
    """Flattens TMDB's ``watch/providers`` ``results[region]`` into ``watch_providers`` rows.

    Emits one row per provider per bucket (``flatrate``, ``rent``, ``buy``); a region absent
    from the payload (not subscribed to by anyone TMDB tracks there) yields ``[]``.
    """
    region_data = (payload.get('results') or {}).get(region)
    if not region_data:
        return []

    rows: list[dict[str, Any]] = []
    for offer in _PROVIDER_BUCKETS:
        rows.extend(
            {
                'provider_id': provider['provider_id'],
                'provider_name': provider['provider_name'],
                'logo_path': provider.get('logo_path'),
                'offer': offer,
            }
            for provider in region_data.get(offer) or []
        )
    return rows


def discovered_titles(
    found: Sequence[tuple[DiscoverSeed, Sequence[Mapping[str, Any]]]],
    *,
    curated: Container[tuple[str, int]],
    path_slugs: Sequence[str],
) -> list[TitleSeed]:
    """Turns fetched ``[[discover]]`` results into synthetic :class:`TitleSeed`\\ s.

    Pure: ``found`` pairs each block with the raw TMDB results
    :meth:`WatchlistIngest._discover_block` fetched for it, so every rule below is testable
    without a network. A result is dropped when it is

    * already curated -- ``curated`` holds the ``(tmdb_type, tmdb_id)`` of every
      hand-written ``[[titles]]`` block. **Curation always wins**: a discovered title never
      overwrites one, and contributes no ``era``/``story``/``context``/``importance`` to it.
      Keying on the *pair* rather than the bare id is deliberate -- TMDB numbers movies and
      shows in separate spaces, so movie ``1403`` and TV ``1403`` are unrelated titles and
      curating one must not silently swallow the other;
    * listed in the block's ``exclude``;
    * undated -- an announced-but-undated entry has nothing to order by and is usually a
      placeholder;
    * outside the block's ``min_date``/``max_date`` window;
    * a duplicate of something an earlier block already contributed (two blocks legitimately
      overlap: a keyword and a company query cover much of the same franchise).

    Survivors are ordered by release date across *all* blocks -- one shared sequence, so two
    blocks can never claim the same order -- and numbered ``DISCOVER_ORDER_BASE + index *
    DISCOVER_ORDER_STEP``, with ``story`` equal to ``release``. A discovered title's story
    order is honestly just its release order: that is what "not curated yet" looks like, and
    it beats inventing a chronology. Ties break on ``(tmdb_type, tmdb_id)`` so a re-sync of
    two same-day releases never churns the numbering.
    """
    accepted: dict[tuple[str, int], tuple[datetime.date, DiscoverSeed]] = {}

    for block, results in found:
        date_field = 'release_date' if block.kind == 'movie' else 'first_air_date'
        for result in results:
            tmdb_id = result.get('id')
            if not isinstance(tmdb_id, int):
                continue
            key = (block.kind, tmdb_id)
            if key in curated or key in accepted or tmdb_id in block.exclude:
                continue
            released = _parse_date(result.get(date_field))
            if released is None:
                continue
            if (block.min_date and released < block.min_date) or (block.max_date and released > block.max_date):
                continue
            accepted[key] = (released, block)

    ordered = sorted(accepted.items(), key=lambda item: (item[1][0], item[0]))
    return [
        TitleSeed(
            tmdb_type=tmdb_type,
            tmdb_id=tmdb_id,
            era=block.era,
            story=DISCOVER_ORDER_BASE + index * DISCOVER_ORDER_STEP,
            release=DISCOVER_ORDER_BASE + index * DISCOVER_ORDER_STEP,
            importance=dict.fromkeys(path_slugs, block.importance),
        )
        for index, ((tmdb_type, tmdb_id), (_, block)) in enumerate(ordered)
    ]


def _universe_row(seed: UniverseSeed) -> dict[str, Any]:
    return {
        'slug': seed.slug,
        'name': seed.name,
        'short_name': seed.short_name,
        'tagline': seed.tagline,
        'accent': accent_to_int(seed.accent),
        'logo_url': seed.logo_url,
        'backdrop_url': seed.backdrop_url,
        'sort_order': seed.sort_order,
        'published': seed.published,
    }


def _path_row(path: PathSeed, sort_order: int) -> dict[str, Any]:
    """``sort_order`` is the path's declaration index -- ``PathSeed`` has no such field, and
    ``list_paths``' ``ORDER BY sort_order`` needs a deterministic value rather than the column
    default of 0 for every path.
    """
    return {
        'slug': path.slug, 'name': path.name, 'description': path.description,
        'is_default': path.default, 'sort_order': sort_order,
    }


@dataclass(slots=True)
class IngestReport:
    """Outcome of one :meth:`WatchlistIngest.sync` call, printed by the ``watchlist sync`` CLI."""

    universe: str
    titles_seen: int = 0
    titles_written: int = 0
    discovered: int = 0
    titles_pruned: int = 0
    providers_written: int = 0
    credits_written: int = 0
    trailers_written: int = 0
    people_pruned: int = 0
    warnings: list[str] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        """A short, human-readable line (plus one indented line per warning)."""
        verb = 'Would sync' if self.dry_run else 'Synced'
        header = (
            f'{self.universe}: {verb} {self.titles_written}/{self.titles_seen} title(s) '
            f'({self.discovered} discovered), '
            f'{self.providers_written} provider row(s), {self.credits_written} credit(s), '
            f'{self.trailers_written} trailer(s), '
            f'pruned {self.titles_pruned} title(s) and {self.people_pruned} orphaned person(s).'
        )
        if not self.warnings:
            return header
        return '\n'.join([header, *(f'  ! {warning}' for warning in self.warnings)])


class WatchlistIngest:
    """Turns a validated :class:`UniverseSeed` into ``watch_*`` rows via TMDB + the repository.

    The only part of the watchlist ingest pipeline that touches network/DB -- every mapping
    function above it is Discord-free and independently testable. Constructed with a
    :class:`TMDBClient` and a :class:`WatchlistRepository` (reached in the bot as
    ``bot.db.watchlist``); see ``main.py``'s ``watchlist sync`` command for how the CLI wires
    both up outside a running bot.
    """

    def __init__(self, client: TMDBClient, repository: WatchlistRepository) -> None:
        self._client = client
        self._repo = repository

    async def sync(self, seed: UniverseSeed, *, regions: Sequence[str], dry_run: bool = False) -> IngestReport:
        """Sync one universe. See the module docstring for the curation/factual column split.

        Order of operations: upsert the universe -> upsert paths (collecting slug -> path_id)
        -> per title fetch/build/upsert + importance + providers + credits -> a second pass resolving
        ``credits_of`` (the referenced title may not have existed yet on first insert, and
        clearing it for any synced title whose seed no longer sets one) -> ``prune_titles``
        with every id seen this run.

        A seed's ``[[discover]]`` blocks (if any) are expanded into synthetic titles up front
        by :meth:`_discover` and appended *after* the curated ones, so the loop below -- and
        everything downstream of it -- cannot tell the two apart. A seed with no such block
        behaves exactly as it did before discovery existed.

        Curation authority: a seed is authoritative for every column it owns, including the
        *absence* of a value -- dropping a title's ``importance`` entry for a path, or its
        ``credits_of``, must clear the stale row/column on re-sync rather than leaving it
        forever. See :meth:`~app.database.repositories.watchlist.WatchlistRepository
        .prune_importance` and the ``credits_of`` clearing pass below.

        Credits (cast/crew, ``V41``) and the trailer (``V44``) are both fetched for every seeded
        title *including unreleased ones* -- a person subscription that only knew about released
        films would be useless, and an unreleased title is precisely the one whose trailer people
        come looking for. People left with no credit at all are swept once at the end.

        Failure policy: a TMDB failure fetching one title's metadata, or a Postgres unique-
        constraint violation upserting it (two seeded titles whose TMDB titles slugify
        identically, or a TMDB rename colliding with another title's stored slug -- see
        ``watch_titles``' ``UNIQUE (universe, slug)``), is recorded as a warning and that
        title is skipped rather than aborting the run. Its *existing* row (if any, from a
        prior successful sync) is preserved by keeping its id in the prune keep-list -- a
        transient failure must never delete data a previous run already wrote. Only a
        seed/validation error (raised before :meth:`sync` is even called) or an exception
        outside :class:`~app.clients.base.HTTPClientError` / ``asyncpg.UniqueViolationError``
        (a genuinely broken client/database) aborts.

        ``dry_run=True`` performs every read (including TMDB fetches, so the report reflects
        what *would* change) but issues no repository write. Because pruning is the one
        destructive thing a sync does -- it cascades to ``watch_progress``, i.e. user data --
        a dry run still computes ``titles_pruned`` (from the titles already in the database
        that this run's seed no longer keeps) and warns about each one by name, rather than
        always reporting 0. Credits and trailers are the reads a dry run skips: each costs an
        extra TMDB request per title and reports a number nobody acts on, so ``credits_written``
        and ``trailers_written`` stay 0.
        """
        report = IngestReport(universe=seed.slug, dry_run=dry_run)

        existing_rows = await self._repo.list_titles(seed.slug)
        existing_ids = {(row['tmdb_type'], row['tmdb_id']): row['id'] for row in existing_rows}

        if not dry_run:
            await self._repo.upsert_universe(_universe_row(seed))

        path_ids: dict[str, int] = {}
        for index, path in enumerate(seed.paths):
            if not dry_run:
                path_ids[path.slug] = await self._repo.upsert_path(seed.slug, _path_row(path, index))

        era_order = {era.key: era.order for era in seed.eras}
        tmdb_id_to_title_id: dict[int, int] = {}
        pending_credits_of: list[tuple[int, int]] = []
        credits_of_clear: list[int] = []
        keep_ids: list[int] = []

        discovered, discovery_failed = await self._discover(seed)
        report.discovered = len(discovered)

        for seed_title in (*seed.titles, *discovered):
            report.titles_seen += 1
            key = (seed_title.tmdb_type, seed_title.tmdb_id)

            try:
                row = await self._build_row(seed_title, universe=seed.slug, era_order=era_order.get(seed_title.era, 0))
            except HTTPClientError as exc:
                report.warnings.append(f'{seed.slug}: TMDB fetch failed for {key[0]}:{key[1]} -- {exc}. Skipped.')
                if key in existing_ids:
                    keep_ids.append(existing_ids[key])
                continue

            warnings = row.pop('_warnings', None)
            created_by = row.pop('_created_by', ())
            if warnings:
                report.warnings.extend(warnings)
            report.titles_written += 1

            region_rows: dict[str, list[dict[str, Any]]] = {}
            try:
                providers = await self._client.watch_providers(seed_title.tmdb_type, seed_title.tmdb_id)
            except HTTPClientError as exc:
                report.warnings.append(f'{seed.slug}: watch_providers failed for {key[0]}:{key[1]} -- {exc}.')
                providers = None
            for region in regions:
                region_rows[region] = provider_rows(providers, region) if providers is not None else []
            written_provider_rows = sum(len(rows) for rows in region_rows.values())
            report.providers_written += written_provider_rows

            if dry_run:
                if key in existing_ids:
                    keep_ids.append(existing_ids[key])
                continue

            try:
                title_id = await self._repo.upsert_title(seed.slug, row)
            except asyncpg.UniqueViolationError as exc:
                report.titles_written -= 1
                report.providers_written -= written_provider_rows
                report.warnings.append(
                    f'{seed.slug}: slug conflict upserting {key[0]}:{key[1]} -- {exc}. Skipped.'
                )
                if key in existing_ids:
                    keep_ids.append(existing_ids[key])
                continue

            tmdb_id_to_title_id[seed_title.tmdb_id] = title_id
            keep_ids.append(title_id)

            try:
                report.credits_written += await self._sync_credits(title_id, seed_title, created_by=created_by)
            except HTTPClientError as exc:
                report.warnings.append(f'{seed.slug}: credits fetch failed for {key[0]}:{key[1]} -- {exc}.')

            try:
                report.trailers_written += await self._sync_trailer(title_id, seed_title)
            except HTTPClientError as exc:
                report.warnings.append(f'{seed.slug}: videos fetch failed for {key[0]}:{key[1]} -- {exc}.')

            kept_path_ids = [
                path_ids[path_slug] for path_slug in seed_title.importance if path_slug in path_ids
            ]
            for path_slug, importance in seed_title.importance.items():
                path_id = path_ids.get(path_slug)
                if path_id is not None:
                    await self._repo.set_importance(title_id, path_id, importance)
            await self._repo.prune_importance(title_id, kept_path_ids)

            for region, rows in region_rows.items():
                await self._repo.replace_providers(title_id, region, rows)

            if seed_title.credits_of is not None:
                pending_credits_of.append((title_id, seed_title.credits_of))
            else:
                credits_of_clear.append(title_id)

        if discovery_failed:
            # A failed discovery block means `discovered` (and therefore `keep_ids`) is
            # missing whatever that block previously contributed -- pruning against it would
            # delete those titles, cascading to every user's watch_progress for them, over a
            # transient TMDB error. Same precedent as prune_titles' empty-keep_ids guard: an
            # optional expansion failing must never wipe the catalogue. See _discover.
            report.warnings.append(
                f'{seed.slug}: a discovery block failed this run -- skipping the title prune '
                f'entirely so a transient TMDB error cannot delete previously discovered titles.'
            )
        elif dry_run:
            keep_id_set = set(keep_ids)
            would_prune = [key for key, row_id in existing_ids.items() if row_id not in keep_id_set]
            report.titles_pruned = len(would_prune)
            report.warnings.extend(
                f'{seed.slug}: {tmdb_type}:{tmdb_id} exists in the database but not in this seed -- '
                f'would be pruned (cascades to watch_progress).'
                for tmdb_type, tmdb_id in would_prune
            )

        if not dry_run:
            for title_id, credits_of_tmdb_id in pending_credits_of:
                credits_of_id = tmdb_id_to_title_id.get(credits_of_tmdb_id)
                if credits_of_id is not None:
                    await self._repo.set_credits_of(title_id, credits_of_id)
            for title_id in credits_of_clear:
                await self._repo.set_credits_of(title_id, None)

            if not discovery_failed:
                report.titles_pruned = await self._repo.prune_titles(seed.slug, keep_ids)
            report.people_pruned = await self._repo.prune_people()

        return report

    async def _discover(self, seed: UniverseSeed) -> tuple[list[TitleSeed], bool]:
        """Expands every ``[[discover]]`` block into synthetic titles. See :func:`discovered_titles`.

        Returns ``(titles, any_block_failed)``. Failure policy: a block that errors (TMDB down,
        an id nobody ever created, :class:`~app.clients.base.HTTPClientError`) logs a warning
        naming the universe and the block, contributes zero titles, and lets the rest of the
        sync proceed with the curated ones plus whatever other blocks produced. A sync must
        never be all-or-nothing on an *optional* expansion -- the hand-written titles are the
        part someone actually curated, and losing them because a keyword query timed out would
        be the worse outcome by a distance.

        ``any_block_failed`` is why :meth:`sync` skips :meth:`~app.database.repositories.
        watchlist.WatchlistRepository.prune_titles` entirely for this run: a failed block means
        this call's ``titles`` is missing whatever that block previously contributed, so
        pruning against it would delete those titles and cascade to every user's watch
        progress for them -- over a transient TMDB error rather than a deliberate curation
        change. Same precedent as ``prune_titles``' own empty-``keep_ids`` guard.

        Runs in a dry run too: the report would otherwise claim every discovered title is about
        to be pruned.
        """
        if not seed.discover:
            return [], False

        found: list[tuple[DiscoverSeed, Sequence[Mapping[str, Any]]]] = []
        any_failed = False
        for block in seed.discover:
            source, source_id = block.source
            try:
                found.append((block, await self._discover_block(seed.slug, block)))
            except HTTPClientError as exc:
                any_failed = True
                log.warning(
                    '%s: discovery block %s (%s=%s) failed -- %s. Contributed no titles.',
                    seed.slug, block.kind, source, source_id, exc,
                )

        titles = discovered_titles(
            found,
            curated={(title.tmdb_type, title.tmdb_id) for title in seed.titles},
            path_slugs=[path.slug for path in seed.paths],
        )
        return titles, any_failed

    async def _discover_block(self, universe: str, block: DiscoverSeed) -> list[Mapping[str, Any]]:
        """Fetches one block's raw TMDB results, stopping at the last page or :data:`DISCOVER_PAGE_CAP`.

        A collection is one request whose ``parts`` array *is* the result list -- TMDB does not
        page it, and a collection is small by construction.

        A block whose ``total_pages`` exceeds the cap is truncated silently otherwise -- and
        since :func:`discovered_titles` applies ``min_date``/``max_date`` only after the crawl
        while TMDB sorts results release-date ascending, a capped block with a ``min_date`` set
        would fetch only the oldest pages and then discard nearly all of them. Not reachable by
        today's seeds (the largest block is 6 pages), but a single ``log.warning`` here is what
        lets an operator reading sync output notice it before a franchise grows into it.
        """
        source, source_id = block.source
        if source == 'collection':
            payload = await self._client.collection(source_id)
            return list(payload.get('parts') or [])

        results: list[Mapping[str, Any]] = []
        total_pages = 0
        for page in range(1, DISCOVER_PAGE_CAP + 1):
            payload = await self._client.discover(
                block.kind,
                keyword=source_id if source == 'keyword' else None,
                company=source_id if source == 'company' else None,
                page=page,
            )
            results.extend(payload.get('results') or [])
            total_pages = payload.get('total_pages') or 0
            if page >= total_pages:
                break
        else:
            log.warning(
                '%s: discovery block %s (%s=%s) has %d pages, past the %d-page cap -- only the '
                'oldest %d pages were fetched, the rest were dropped.',
                universe, block.kind, source, source_id, total_pages, DISCOVER_PAGE_CAP, DISCOVER_PAGE_CAP,
            )
        return results

    async def refresh_release_dates(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Re-checks stored release dates against TMDB, writing only the ones that moved.

        The bounded counterpart to :meth:`sync`: a sync is seed-driven and whole-universe, but
        TMDB dates *move* after a sync has run, and a date that slipped is a subscriber DMed on
        the wrong day. So the ``Releases`` cog hands this the titles due in the next 30 days
        each night. It reuses the same client and the same :func:`_parse_date` as the ingest --
        there is deliberately no second path from a TMDB payload to a ``watch_titles`` row.

        Each row must carry ``id``, ``tmdb_type``, ``tmdb_id`` and ``release_date`` (what
        :meth:`~app.database.repositories.watchlist.WatchlistRepository.titles_releasing_between`
        selects). Returns how many dates were written.

        Two deliberate refusals to act:

        * a title TMDB no longer publishes a date for keeps the date it has, rather than being
          nulled out of the catalogue by one bad payload;
        * a TMDB failure on one title is logged and skipped, exactly as in :meth:`sync` -- an
          outage must never rewrite dates, and one dead ``tmdb_id`` must not cost the run.
        """
        moved = 0
        for row in rows:
            try:
                if row['tmdb_type'] == 'movie':
                    payload = await self._client.movie(row['tmdb_id'])
                    fetched = _parse_date(payload.get('release_date'))
                else:
                    payload = await self._client.tv(row['tmdb_id'])
                    fetched = _parse_date(payload.get('first_air_date'))
            except HTTPClientError as exc:
                log.warning('Release-date re-check failed for %s:%s -- %s.', row['tmdb_type'], row['tmdb_id'], exc)
                continue

            if fetched is None or fetched == row['release_date']:
                continue

            await self._repo.set_release_date(row['id'], fetched)
            log.info('Release date moved: %s:%s %s -> %s.',
                     row['tmdb_type'], row['tmdb_id'], row['release_date'], fetched)
            moved += 1
        return moved

    async def _sync_credits(
        self, title_id: int, seed_title: TitleSeed, *, created_by: Sequence[Mapping[str, Any]],
    ) -> int:
        """Fetches one title's TMDB credits and writes the people + credit rows. Returns the row count.

        People are upserted one at a time *before* :meth:`~app.database.repositories.watchlist
        .WatchlistRepository.replace_credits` opens its transaction, because a slug collision
        is resolved by retrying the failed insert -- inside a transaction that first failure
        would abort the whole batch.

        A TMDB failure propagates to the caller, which records it as a warning: the existing
        credit rows are then left untouched rather than deleted, so an outage never empties a
        title's cast.
        """
        payload = await self._client.credits(seed_title.tmdb_type, seed_title.tmdb_id)
        rows = credit_rows(payload, created_by=created_by)
        for row in rows:
            await self._repo.upsert_person(row)
        await self._repo.replace_credits(title_id, rows)
        return len(rows)

    async def _sync_trailer(self, title_id: int, seed_title: TitleSeed) -> int:
        """Fetches one title's TMDB videos and stores the winning trailer. Returns 1 if one was written.

        Run for **every** seeded title, unreleased ones included -- the title whose trailer
        people actually want is the one that is not out yet. It costs one extra request per
        title (38 for the current seed set, against TMDB's ~40 req/s ceiling), which is why
        there is no filter here to justify.

        A payload with no videos, or none matching
        :func:`~app.services.watchlist.trailers.select_trailer`'s precedence, writes nothing and
        returns 0: the stored trailer (if any) survives, and "this title has no trailer" is a
        normal state rather than a warning. A TMDB failure propagates to the caller, which
        records it as a warning, with the same effect.
        """
        trailer = select_trailer(await self._client.videos(seed_title.tmdb_type, seed_title.tmdb_id))
        if trailer is None:
            return 0
        await self._repo.set_trailer(title_id, trailer)
        return 1

    async def _build_row(self, seed_title: TitleSeed, *, universe: str, era_order: int) -> dict[str, Any]:
        """Fetches TMDB metadata for one title and maps it via :func:`movie_row`/:func:`tv_row`."""
        if seed_title.tmdb_type == 'movie':
            payload = await self._client.movie(seed_title.tmdb_id)
            return movie_row(seed_title, payload, universe=universe, era_order=era_order)

        payload = await self._client.tv(seed_title.tmdb_id)
        available = [
            season['season_number'] for season in (payload.get('seasons') or []) if 'season_number' in season
        ]
        season_payloads = {
            number: await self._client.tv_season(seed_title.tmdb_id, number)
            for number in _wanted_seasons(seed_title, available)
        }
        return tv_row(seed_title, payload, season_payloads, universe=universe, era_order=era_order)
