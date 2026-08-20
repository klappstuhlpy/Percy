"""Personal release subscriptions: the Discord surface, and the DM dispatcher behind it.

Three commands (``/subscribe``, ``/subscriptions``, ``/unsubscribe``) and one hourly loop.
The loop is the whole feature: it turns the persisted catalogue into
:class:`~app.services.releases.Releasable`\\s, fans them out to the users who follow their
subjects, claims each ``(user, media, key)`` delivery so nobody is told twice, and DMs one
digest per user.

All of the *policy* lives in ``app/services/releases/`` -- what a subject is, who matches, what
goes in a digest -- and all of the SQL in ``app/database/repositories/releases.py``. This module
is the controller between them plus the autocomplete that resolves a typed name to a subject key.

**The autocomplete is load-bearing.** ``series``, ``universe`` and ``person`` subject values are
*slugs*; ``brand``, ``creator`` and ``character`` values are names. A user who could type a
subject value freehand could create a subscription the fan-out can never match, so the picker is
what resolves a name to the right key -- mirroring ``app/internal_api/routers/releases.py``'s
``_subject``/``_catalogue_subjects``, so the two surfaces cannot drift apart.
"""

from __future__ import annotations

import datetime
import itertools
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import asyncpg
import discord
from discord import app_commands
from discord.ext import tasks

from app.clients.news import NewsClient
from app.clients.tmdb import TMDBClient
from app.core import Bot, Cog, Context, command, describe
from app.services.news import (
    NEWS_MEDIA,
    NewsIngest,
    due_sources,
    load_vocabulary,
    news_releasable,
    seed_sources,
)
from app.services.releases import (
    NEWS_LIMIT,
    Releasable,
    build_digest,
    comic_releasable,
    match,
    title_releasable,
)
from app.services.watchlist.ingest import WatchlistIngest
from app.utils import cache, fuzzy
from app.utils.lock import lock
from config import Emojis

from .ui import (
    STREAM_LABELS,
    SubscriptionsView,
    build_digest_embed,
    build_subscriptions_embed,
    decode_subject,
    subject_choice,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from discord.app_commands import Choice

log = logging.getLogger(__name__)

#: How long the subject catalogue is served from memory. Ingest runs on its own schedule in
#: another process, so a few minutes of staleness is cheap next to four queries per keystroke.
CATALOGUE_TTL = 300

#: How many rows each catalogue source contributes to the candidate pool.
POOL_SIZE = 200

#: Minimum fuzzy score a candidate needs to show up in the picker.
SCORE_CUTOFF = 60

#: Display forms for the closed brand set -- ``str.title()`` turns ``'dc'`` into ``'Dc'``.
BRAND_LABELS = {'marvel': 'Marvel', 'dc': 'DC', 'manga': 'Manga'}

#: How far back a dispatch run looks for newly seen comics. Deliberately far wider than the
#: loop's interval: a few hours (or a day) of downtime must not silently drop a week of books.
#: What stops a user hearing about the same issue twice is ``release_deliveries`` -- the claim
#: in :meth:`Releases.deliver_to` -- never the width of this window.
COMIC_WINDOW = datetime.timedelta(days=7)

#: How long a delivery claim is kept. Long enough that a re-dispatch cannot double-notify.
DELIVERY_RETENTION = datetime.timedelta(days=90)

#: How far ahead the nightly TMDB re-check looks. Matches the overview endpoint's ``MAX_DAYS``:
#: a month is exactly as far as the film/TV dates are kept honest, so nothing is dispatched --
#: or shown as imminent -- on a date nothing re-verified.
REFRESH_HORIZON = datetime.timedelta(days=30)

#: How far back a dispatch run looks for newly *fetched* stories. Much narrower than
#: :data:`COMIC_WINDOW` because news is much higher-volume: two days of six feeds is a few
#: hundred rows, a week would be several thousand for no gain. As with comics, what stops a
#: double-notify is the delivery claim, never the width of this window.
NEWS_WINDOW = datetime.timedelta(days=2)

#: How many sources one polling tick may fetch. The repository returns sources oldest-polled
#: first, so truncating is fair by construction -- whatever is dropped leads the next tick.
#: Eight covers the shipped list several times over and still bounds one run's HTTP work if an
#: operator adds thirty feeds.
MAX_SOURCES_PER_RUN = 8

#: How often the polling loop wakes. Deliberately finer than
#: :data:`~app.services.news.MIN_POLL_INTERVAL` (30 min), which is what actually decides how
#: often a given feed is *asked*: the tick only decides how quickly a newly seeded, re-enabled
#: or previously-failing source is picked up, since ``due_sources`` filters the rest out. Four
#: ticks an hour against an hourly digest also means a story is rarely more than one tick old
#: when the digest that should carry it is built.
POLL_INTERVAL = datetime.timedelta(minutes=15)

Streams = Literal['releases', 'news', 'both']


@dataclass(frozen=True, slots=True)
class Candidate:
    """One thing a user could follow, with the popularity weight that breaks fuzzy ties."""

    weight: int
    media: str
    subject_type: str
    value: str
    label: str


@cache.cache(maxsize=CATALOGUE_TTL, strategy=cache.Strategy.TIMED)
async def catalogue_subjects(bot: Bot) -> list[Candidate]:
    """Every subject derivable from the persisted catalogue, each with a weight.

    Cached for :data:`CATALOGUE_TTL` seconds and keyed only on the bot, so the pool is built at
    most once per TTL no matter how much anybody types. People are deliberately *not* in here:
    person search is a trigram query per keystroke, and caching it would fill the shared cache
    with one entry per prefix anybody ever typed.

    Note which column becomes the ``value`` in each branch -- slugs for series, universes and
    people, names for brands, creators and characters. That is the rule the fan-out matches on.
    """
    candidates: list[Candidate] = [
        # A whole-publisher follow. Weightless on purpose: three brands outranking every series
        # would bury the subjects a user actually came looking for.
        Candidate(0, 'comic', 'brand', key, label)
        for key, label in BRAND_LABELS.items()
    ]

    candidates.extend(
        Candidate(row['issue_count'], 'comic', 'series', row['series_slug'],
                  row['series_name'] or row['series_slug'])
        for row in await bot.db.releases.list_series(limit=POOL_SIZE)
    )

    for kind, table in (('creator', 'comic_creators'), ('character', 'comic_characters')):
        candidates.extend(
            Candidate(row['appearances'], 'comic', kind, row['name'], row['name'])
            for row in await bot.db.releases.list_names(table, limit=POOL_SIZE)
        )

    candidates.extend(
        Candidate(row['title_count'], 'title', 'universe', row['slug'], row['name'])
        for row in await bot.db.watchlist.list_universes()
    )
    return candidates


def rank(candidates: Iterable[Candidate], query: str, limit: int) -> list[Candidate]:
    """Ranks candidates against ``query`` with the project's own fuzzy scorer.

    :func:`app.utils.fuzzy.partial_ratio` is substring-tolerant, so "iron" scores 100 against
    "Iron Man" and ties are the common case rather than the exception. Popularity breaks them,
    then the label, so two identical requests rank identically.
    """
    needle = query.lower()
    scored = [(fuzzy.partial_ratio(needle, candidate.label.lower()), candidate) for candidate in candidates]
    ranked = sorted(
        (row for row in scored if row[0] >= SCORE_CUTOFF),
        key=lambda row: (-row[0], -row[1].weight, row[1].label),
    )
    return [candidate for _, candidate in ranked[:limit]]


def popular(candidates: Iterable[Candidate], limit: int) -> list[Candidate]:
    """The empty-query case: the head of each source, interleaved.

    Round-robin rather than concatenation, or series alone would fill every slot and nobody
    would learn the picker also knows about actors.
    """
    groups: dict[tuple[str, str], list[Candidate]] = {}
    for candidate in candidates:
        groups.setdefault((candidate.media, candidate.subject_type), []).append(candidate)
    interleaved = itertools.chain.from_iterable(itertools.zip_longest(*groups.values()))
    return [candidate for candidate in interleaved if candidate is not None][:limit]


def _group_by(rows: Iterable[asyncpg.Record], column: str) -> dict[int, list[asyncpg.Record]]:
    """Buckets batched rows by their owning id, so one query serves many releasables."""
    grouped: dict[int, list[asyncpg.Record]] = {}
    for row in rows:
        grouped.setdefault(row[column], []).append(row)
    return grouped


class Releases(Cog):
    """Follow the comics, films, shows and people you care about, and get a DM when they land."""

    emoji = Emojis.fire

    if TYPE_CHECKING:
        bot: Bot

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.dispatch_releases.add_exception_type(asyncpg.PostgresConnectionError)
        self.dispatch_releases.start()
        self.refresh_release_dates.start()
        self.poll_news.start()

    async def cog_unload(self) -> None:
        self.dispatch_releases.cancel()
        self.refresh_release_dates.cancel()
        self.poll_news.cancel()

    # -- Autocomplete ------------------------------------------------------

    async def subject_autocomplete(self, _: discord.Interaction, current: str) -> list[Choice[str]]:
        """Resolves what the user typed into subject keys they can actually be matched on."""
        candidates = list(await catalogue_subjects(self.bot))
        if not current:
            chosen = popular(candidates, 25)
        else:
            # Person search is a trigram query, so it joins the pool per keystroke rather than
            # being cached into it -- one cache entry per prefix anybody ever typed is a leak.
            candidates.extend(
                Candidate(row['credit_count'], 'title', 'person', row['slug'], row['name'])
                for row in await self.bot.db.watchlist.search_people(current, limit=25)
            )
            chosen = rank(candidates, current, 25)

        choices = (
            subject_choice(candidate.media, candidate.subject_type, candidate.value, candidate.label)
            for candidate in chosen
        )
        return [choice for choice in choices if choice is not None]

    async def subscription_autocomplete(self, interaction: discord.Interaction, current: str) -> list[Choice[str]]:
        """Autocomplete over the caller's own subscriptions -- you cannot unfollow someone else's."""
        labelled = [
            (row, row['subject_label'] or row['subject_value'])
            for row in await self.bot.db.releases.list_subscriptions(interaction.user.id)
        ]
        choices = (
            subject_choice(row['media'], row['subject_type'], row['subject_value'], label)
            for row, label in labelled
            if not current or fuzzy.partial_ratio(current.lower(), label.lower()) >= SCORE_CUTOFF
        )
        return [choice for choice in choices if choice is not None][:25]

    async def resolve_label(self, media: str, subject_type: str, value: str) -> str:
        """The display name for a subject key, falling back to the key's own value.

        People are looked up directly (they are not in the cached pool); everything else comes
        out of the pool the picker already built, so confirming a follow costs no extra query.
        """
        if subject_type == 'person':
            row = await self.bot.db.watchlist.get_person(value)
            return row['name'] if row else value
        for candidate in await catalogue_subjects(self.bot):
            if (candidate.media, candidate.subject_type, candidate.value) == (media, subject_type, value):
                return candidate.label
        return value

    # -- Commands ----------------------------------------------------------

    @command(
        'subscribe',
        description='Follow a comic series, creator, character, film universe or person.',
        hybrid=True,
    )
    @describe(
        subject='What to follow — pick one of the suggestions.',
        streams='Which updates you want. Releases only by default; news is far higher-frequency.',
    )
    @app_commands.autocomplete(subject=subject_autocomplete)  # type: ignore[arg-type]
    async def subscribe(self, ctx: Context, subject: str, streams: Streams = 'releases') -> None:
        """Follow something and get a DM when it has news."""
        decoded = decode_subject(subject)
        if decoded is None:
            await ctx.send_error('Pick one of the suggestions — I could not read that subject.')
            return

        media, subject_type, value = decoded
        label = await self.resolve_label(media, subject_type, value)
        await self.bot.db.releases.subscribe(
            ctx.author.id, media, subject_type, value,
            label=label, streams=streams, guild_id=ctx.guild.id if ctx.guild else None,
        )
        await ctx.send_success(
            f'Now following **{label}** — {STREAM_LABELS[streams]}. I will DM you.', ephemeral=True)

    @command(
        'subscriptions',
        alias='subs',
        description='Show everything you follow, and change or remove any of it.',
        hybrid=True,
    )
    async def subscriptions(self, ctx: Context) -> None:
        """List your subscriptions."""
        rows = await self.bot.db.releases.list_subscriptions(ctx.author.id)
        embed = build_subscriptions_embed(ctx.author, rows)
        if not rows:
            await ctx.send(embed=embed, ephemeral=True)
            return

        view = SubscriptionsView(self, ctx, rows)
        view.message = await ctx.send(embed=embed, view=view, ephemeral=True)

    @command('unsubscribe', description='Stop following something.', hybrid=True)
    @describe(subject='One of your subscriptions.')
    @app_commands.autocomplete(subject=subscription_autocomplete)  # type: ignore[arg-type]
    async def unsubscribe(self, ctx: Context, *, subject: str) -> None:
        """Unfollow something you subscribed to."""
        decoded = decode_subject(subject)
        if decoded is None:
            await ctx.send_error('Pick one of the suggestions — I could not read that subject.')
            return

        label = await self.resolve_label(*decoded)
        if await self.bot.db.releases.unsubscribe(ctx.author.id, *decoded):
            await ctx.send_success(f'Unfollowed **{label}**.', ephemeral=True)
        else:
            await ctx.send_error(f'You were not following **{label}**.')

    # -- Dispatch ----------------------------------------------------------

    async def gather_releasables(self, now: datetime.datetime) -> list[Releasable]:
        """Everything that could be dispatched this run, as media-agnostic releasables.

        Comics come from ``first_seen`` (what we learned about recently, which is not the same
        question as what comes out recently); titles from today's release dates, because TMDB
        dates move and a title is only news on the day it is actually out.
        """
        repo = self.bot.db.releases
        rows = await repo.recent_comic_releases(now - COMIC_WINDOW)
        release_ids = [row['id'] for row in rows]
        creators = _group_by(await repo.credits_for_releases(release_ids), 'release_id')
        characters = _group_by(await repo.characters_for_releases(release_ids), 'release_id')
        items = [
            comic_releasable(row, creators.get(row['id'], ()), characters.get(row['id'], ()))
            for row in rows
        ]

        today = now.date()
        titles = await self.bot.db.watchlist.titles_releasing_between(today, today)
        # A season carries no credits of its own (TMDB's ``credits`` is series-level, so the
        # ingest writes it against the series row), and a season is the only row of a
        # multi-season show the dispatcher ever sees -- so credits are looked up on the series,
        # exactly as ``routers/watchlist.py`` does. Without it a ``person``/``character``
        # subscriber is silently never told about any multi-season show.
        credits = _group_by(
            await self.bot.db.watchlist.list_credits([row['parent_id'] or row['id'] for row in titles]), 'title_id')
        items += [title_releasable(row, credits.get(row['parent_id'] or row['id'], ())) for row in titles]
        return items

    async def gather_news(self, now: datetime.datetime) -> list[Releasable]:
        """Recently *fetched* stories, as releasables the same matcher understands.

        ``recent_news`` is keyed on ``fetched_at`` for the same reason the comic half uses
        ``first_seen``: a feed that back-fills a week-old story is new to us today.

        The subject rows are grouped by story before the releasable is built, and that grouping
        is load-bearing rather than tidiness. A story tagged in both catalogues (a comic *and*
        its adaptation) has several ``news_subjects`` rows; one releasable per row would take
        one delivery claim each and appear twice in the same DM. One releasable carrying every
        subject is claimed once -- :func:`~app.services.releases.match` dedupes by
        :attr:`Releasable.key`, and ``news_releasable`` keys every story under the single
        ``news`` medium so two runs cannot claim it under two names.

        The source name is joined in here because §9 makes it non-optional: the DM has to name
        the publisher. Disabled sources are included in that lookup -- a source switched off
        today must not strip the attribution from the stories it already delivered.
        """
        repo = self.bot.db.news
        rows = await repo.recent_news(now - NEWS_WINDOW)
        if not rows:
            return []

        subjects = _group_by(await repo.subjects_for_items([row['id'] for row in rows]), 'news_id')
        names = {row['slug']: row['name'] for row in await repo.list_sources(enabled_only=False)}
        return [
            news_releasable(row, subjects.get(row['id'], ()), source=names.get(row['source_slug']))
            for row in rows
        ]

    async def deliver_to(
        self, user: discord.abc.User, items: Sequence[Releasable], stories: Sequence[Releasable] = (),
    ) -> bool:
        """DMs one user their digest, returning whether it actually went out.

        The delivery claim is taken **before** the send and is optimistic: only what
        :meth:`~app.database.repositories.releases.ReleasesRepository.claim_deliveries` hands
        back is new to this user, and anything not sent is handed straight back, or a DM that
        never arrived would count as delivered and the release would be lost for good.

        ``stories`` is the news half, and it is capped at :data:`NEWS_LIMIT` *before* anything is
        claimed -- so what does not fit is still unclaimed and leads the next run's digest rather
        than being dropped (§4.4: a busy news week must never crowd out the release someone
        actually subscribed for). ``match`` hands the stories back oldest-first, so the slice is
        FIFO and a backlog drains in order; the digest re-sorts them newest-first for display.
        Both halves render into **one** embed, so a subscriber still gets one DM per run.

        A blocked DM (:class:`discord.Forbidden`) mutes *every* subscription this user has, per
        the plan's D8: Discord will never tell us they are reachable again, so retrying forever
        is how a bot gets reported. The subscriptions survive -- the user turns delivery back on
        and loses nothing.
        """
        repo = self.bot.db.releases
        offered = [*items, *stories[:NEWS_LIMIT]]
        claimed = await repo.claim_deliveries([(user.id, item.media, item.key) for item in offered])
        if not claimed:
            return False

        claims = sorted(claimed)
        kept = [item for item in offered if (user.id, item.media, item.key) in claimed]
        digest = build_digest(user.id, [item for item in kept if item.media != NEWS_MEDIA])
        news = build_digest(user.id, [item for item in kept if item.media == NEWS_MEDIA], limit=NEWS_LIMIT)
        if not digest.groups and not news.groups:
            await repo.release_claims(claims)
            return False

        try:
            await user.send(embed=build_digest_embed(digest, news))
        except discord.Forbidden:
            await repo.release_claims(claims)
            muted = await repo.disable_all_delivery(user.id)
            log.info('Muted %d release subscription(s) for %s: their DMs are closed.', muted, user.id)
            return False
        except Exception:
            await repo.release_claims(claims)
            log.exception('Failed to deliver a release digest to %s.', user.id)
            return False
        return True

    @tasks.loop(hours=1)
    @lock('Releases.dispatch', 'release dispatch task')
    async def dispatch_releases(self) -> None:
        """One fan-out run: catalogue → subscribers → per-user digest → DM.

        Both streams run through here, and deliberately through **one** subscription query and
        **one** DM per user: the subjects of the releases and of the stories are unioned before
        the lookup, then matched twice -- once per stream, because ``streams`` is what decides
        which of the two a subscriber wanted (§4.4). A user following one subject for news and
        another for releases is one digest, not two.

        Locked rather than queued: if a run is still going when the next hour comes round, the
        second one is dropped. It has nothing to catch up on -- the windows are days wide and
        the claims are idempotent.
        """
        repo = self.bot.db.releases
        now = discord.utils.utcnow().replace(tzinfo=None)

        releasables = await self.gather_releasables(now)
        stories = await self.gather_news(now)
        subjects = sorted({(subject.media, subject.type, subject.value)
                           for item in (*releasables, *stories) for subject in item.subjects})
        subscriptions = await repo.subscribers_for_subjects(subjects)

        by_user = match(releasables, subscriptions)
        news_by_user = match(stories, subscriptions, stream='news')

        for user_id in sorted(by_user.keys() | news_by_user.keys()):
            user = self.bot.get_user(user_id)
            if user is None:
                try:
                    user = await self.bot.fetch_user(user_id)
                except discord.HTTPException:
                    log.warning('Skipping a release digest for unresolvable user %s.', user_id)
                    continue
            try:
                await self.deliver_to(user, by_user.get(user_id, []), news_by_user.get(user_id, []))
            except Exception:
                # One user's database blip must not cost everyone else their digest.
                log.exception('Release dispatch failed for %s.', user_id)

        await repo.prune_deliveries(now - DELIVERY_RETENTION)

    @dispatch_releases.before_loop
    async def _before_dispatch_releases(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(hours=24)
    @lock('Releases.refresh_dates', 'release-date refresh task')
    async def refresh_release_dates(self) -> None:
        """Nightly: re-check the next 30 days of film/TV dates against TMDB and write what moved.

        A watchlist sync is a one-shot, and TMDB dates slip constantly -- so without this the
        hourly dispatch above eventually DMs people on the wrong day, and the overview's forward
        half advertises a date nothing has re-verified. This is the smallest thing that keeps
        both honest: the same client and the same ingest service the CLI sync uses, pointed at a
        bounded set of rows (:data:`REFRESH_HORIZON`) instead of a whole universe.

        Degrades like every other optional external service: no TMDB token, an unreachable TMDB
        or a database blip logs and returns, because a task loop that raises stops running and a
        stopped loop is a silent one.

        Locked like the dispatch loop, so a slow run cannot overlap the next night's. The lock is
        process-local, so a manual ``main.py watchlist sync`` is *not* excluded -- both write the
        same TMDB value for the same title, so the cost of an overlap is a duplicate write rather
        than a wrong date.
        """
        client = TMDBClient(self.bot.session)
        if not client.available:
            log.info('Skipping the release-date refresh: TMDB is not configured.')
            return

        today = discord.utils.utcnow().date()
        try:
            rows = await self.bot.db.watchlist.titles_releasing_between(today, today + REFRESH_HORIZON)
            moved = await WatchlistIngest(client, self.bot.db.watchlist).refresh_release_dates(rows)
        except Exception:
            log.exception('The nightly release-date refresh failed.')
            return

        log.info('Release-date refresh: checked %d title(s), %d date(s) moved.', len(rows), moved)

    @refresh_release_dates.before_loop
    async def _before_refresh_release_dates(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(seconds=POLL_INTERVAL.total_seconds())
    @lock('Releases.poll_news', 'news polling task')
    async def poll_news(self) -> None:
        """Polls the feeds that are due and writes their stories through to the catalogue.

        fetch → parse → upsert → tag → ``replace_subjects`` → ``mark_fetched``, all of it inside
        :class:`~app.services.news.NewsIngest`, which never raises: a dead feed's status string
        lands in ``news_sources.last_status`` and the run continues to the next source. A story
        that matched no term is still stored -- it is catalogue content for ``GET /news`` and can
        simply never be *delivered*, which is the correct direction to fail in (§8: a false tag
        notifies the wrong people, a missing tag notifies nobody).

        Only :data:`MAX_SOURCES_PER_RUN` sources are polled per tick. ``list_sources`` returns
        them oldest-polled first, so truncating cannot starve anyone: whatever is dropped leads
        the next tick.

        Seeding runs here rather than once at startup because the tables may not exist yet (a
        deploy that has not applied ``V43`` yet) and a ``before_loop`` that raises kills the loop
        for the process's lifetime. It is insert-if-absent, so it never re-enables a source an
        operator switched off, and after the first successful tick it is six cheap lookups.

        Everything is wrapped: a task loop that raises stops running, and a stopped loop is a
        silent one -- exactly the failure mode ``last_status`` exists to make visible.
        """
        try:
            repo = self.bot.db.news
            await seed_sources(repo)
            sources = due_sources(
                await repo.list_sources(),
                discord.utils.utcnow().replace(tzinfo=None),
                limit=MAX_SOURCES_PER_RUN,
            )
            if not sources:
                # Nothing is due. Logged at DEBUG rather than INFO: at four ticks an hour
                # against a 30-minute cadence, most ticks are this one.
                log.debug('News poll: no source is due.')
                return

            index = await load_vocabulary(self.bot.db.releases, self.bot.db.watchlist)
            report = await NewsIngest(NewsClient(self.bot.session), repo, index).run(sources)
        except Exception:
            log.exception('The news polling run failed.')
            return

        log.info('News poll: %s.', report)

    @poll_news.before_loop
    async def _before_poll_news(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: Bot) -> None:
    await bot.add_cog(Releases(bot))
