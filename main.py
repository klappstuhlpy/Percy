import asyncio
import contextlib
import logging
import traceback
from collections.abc import Awaitable, Callable, Generator
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, ClassVar

import aiohttp
import asyncpg
import click
import discord

import config
from app.clients.base import HTTPClientError
from app.clients.news import NewsClient
from app.clients.tmdb import TMDBClient
from app.core import Bot
from app.database import Database, MigrationRunner
from app.database.migrations import MIGRATIONS_TABLE, Migration, MigrationError
from app.services.comics import ComicIngest
from app.services.news import NewsIngest, load_vocabulary, seed_sources
from app.services.watchlist import SeedError, UniverseSeed, WatchlistIngest, load_seed
from config import DatabaseConfig, locg_api_url, logs_path

try:
    import uvloop  # type: ignore[import-not-found]
except ImportError:
    pass
else:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


__all__ = (
    'RemoveNoise',
    'comics',
    'comics_backfill',
    'db',
    'history',
    'init',
    'main',
    'migrate',
    'news',
    'news_poll',
    'news_sources',
    'run_bot',
    'setup_logging',
    'status',
    'upgrade',
    'verify',
    'watchlist',
    'watchlist_lint',
    'watchlist_search',
    'watchlist_sync',
)


class RemoveNoise(logging.Filter):
    """Suppresses discord.state warnings about unknown references."""

    def __init__(self) -> None:
        super().__init__(name='discord.state')

    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.levelname == 'WARNING' and 'referencing an unknown' in record.msg)


class _ColourFormatter(logging.Formatter):
    LEVEL_COLOURS: ClassVar[list[tuple[int, str, int]]] = [
        (logging.DEBUG, '\x1b[40;1m', 5),
        (logging.INFO, '\x1b[34;1m', 4),
        (logging.WARNING, '\x1b[33;1m', 7),
        (logging.ERROR, '\x1b[31m', 5),
        (logging.CRITICAL, '\x1b[41m', 8),
    ]

    FORMATS: ClassVar[dict[int, logging.Formatter]] = {
        level: logging.Formatter(
            f'%(asctime)s\x1b[0m | {colour}%(levelname)-{length}s\x1b[0m \x1b[35m%(name)s\x1b[0m: %(message)s',
            '%Y-%m-%d %H:%M:%S',
        )
        for level, colour, length in LEVEL_COLOURS
    }

    def format(self, record: logging.LogRecord) -> str:
        formatter = self.FORMATS.get(record.levelno, self.FORMATS[logging.DEBUG])

        if record.exc_info:
            text = formatter.formatException(record.exc_info)
            record.exc_text = f'\x1b[31m{text}\x1b[0m'

        output = formatter.format(record)
        record.exc_text = None
        return output


@contextlib.contextmanager
def setup_logging() -> Generator[None, Any, None]:
    root_log = logging.getLogger()

    try:
        dt_fmt = '%Y-%m-%d %H:%M:%S'
        fmt = logging.Formatter(fmt='[{asctime}] | {levelname:<7} - {name}: {message}', datefmt=dt_fmt, style='{')

        discord.utils.setup_logging(formatter=_ColourFormatter())

        max_bytes = 32 * 1024 * 1024  # 32 MiB
        logging.getLogger('discord').setLevel(logging.INFO)
        logging.getLogger('discord.http').setLevel(logging.WARNING)
        logging.getLogger('discord.state').addFilter(RemoveNoise())
        logging.getLogger('charset_normalizer').setLevel(logging.ERROR)
        logging.getLogger('TrackException').setLevel(logging.CRITICAL)

        root_log.setLevel(logging.INFO)
        handler = RotatingFileHandler(
            filename=Path(logs_path, 'percy.log'),
            encoding='utf-8',
            mode='w',
            maxBytes=max_bytes,
            backupCount=5,
        )
        handler.setFormatter(fmt)
        root_log.addHandler(handler)

        from app.utils.logging import JSONFormatter
        json_handler = RotatingFileHandler(
            filename=Path(logs_path, 'percy.json.log'),
            encoding='utf-8',
            mode='w',
            maxBytes=max_bytes,
            backupCount=3,
        )
        json_handler.setFormatter(JSONFormatter())
        root_log.addHandler(json_handler)

        yield
    finally:
        for hdlr in root_log.handlers[:]:
            hdlr.close()
            root_log.removeHandler(hdlr)


async def run_bot() -> None:
    discord.VoiceClient.warn_nacl = False

    async with Bot() as bot:
        with contextlib.suppress(asyncio.CancelledError):
            await bot.start()


@click.group(invoke_without_command=True, options_metavar='[options]')
@click.pass_context
def main(ctx: click.Context) -> None:
    """Launches the bot."""
    if ctx.invoked_subcommand is None:
        with setup_logging():
            asyncio.run(run_bot())


@main.group(short_help='Database configuration', options_metavar='[options]')
def db() -> None:
    """Manages forward-only SQL migrations.

    Available migrations are the ``migrations/V<n>__<name>.sql`` files; applied state lives
    in the ``schema_migrations`` table. A database created by the old ``revisions.json``
    system is backfilled automatically on the first ``upgrade``/``init``/``status``.
    """


async def _with_connection[T](action: Callable[[asyncpg.Connection], Awaitable[T]]) -> T:
    """Opens a short-lived connection from the configured DSN, runs ``action``, closes it."""
    connection: asyncpg.Connection = await asyncpg.connect(**DatabaseConfig.to_kwargs())
    try:
        return await action(connection)
    finally:
        await connection.close()


def _fail(message: str) -> None:
    traceback.print_exc()
    click.secho(message, fg='red')


@db.command()
def init() -> None:
    """Creates the tracking table (backfilling legacy state) and applies all pending migrations."""
    runner = MigrationRunner()

    async def _action(conn: asyncpg.Connection) -> tuple[int, list[Migration]]:
        backfilled = await runner.bootstrap(conn)
        applied = await runner.upgrade(conn)
        return backfilled, applied

    try:
        backfilled, applied = asyncio.run(_with_connection(_action))
    except (MigrationError, asyncpg.PostgresError, OSError):
        _fail('Failed to initialize the database. Check your configuration and migration scripts.')
        return

    if backfilled:
        click.secho(f'Backfilled {backfilled} previously-applied migration(s) into {MIGRATIONS_TABLE}.', fg='cyan')
    click.secho(f'Initialized the database and applied {len(applied)} migration(s).', fg='green')


@db.command()
@click.option('--reason', '-r', help='Short description of the migration.', required=True)
def migrate(reason: str) -> None:
    """Creates a new, empty migration stub one version above the latest file."""
    runner = MigrationRunner()
    migration = runner.create(reason)
    click.secho(f'Created {migration.label} at {migration.path.as_posix()}.', fg='green')


@db.command()
@click.option('--target', '-t', type=int, default=None, help='Highest version to apply (default: latest).')
@click.option('--sql', 'show_sql', is_flag=True, help='Print the pending SQL instead of executing it.')
@click.option('--dry-run', is_flag=True, help='List what would be applied without executing.')
def upgrade(target: int | None, show_sql: bool, dry_run: bool) -> None:
    """Applies every pending migration, optionally only up to ``--target``."""
    runner = MigrationRunner()

    async def _pending(conn: asyncpg.Connection) -> list[Migration]:
        await runner.bootstrap(conn)
        return await runner.pending(conn, target=target)

    if show_sql or dry_run:
        try:
            pending = asyncio.run(_with_connection(_pending))
        except (MigrationError, asyncpg.PostgresError, OSError):
            _fail('Could not determine pending migrations.')
            return
        if not pending:
            click.secho('Database is up to date — nothing pending.', fg='green')
            return
        for migration in pending:
            if show_sql:
                click.secho(f'-- {migration.label} {migration.title}', fg='yellow')
                click.echo(migration.sql.rstrip())
                click.echo()
            else:
                click.echo(f'{click.style(migration.label, fg="yellow")} {migration.title}')
        return

    try:
        applied = asyncio.run(_with_connection(lambda conn: runner.upgrade(conn, target=target)))
    except (MigrationError, asyncpg.PostgresError, OSError):
        _fail('An error occurred while applying migrations. Check your migration scripts.')
        return

    if not applied:
        click.secho('Database is already up to date.', fg='green')
    else:
        for migration in applied:
            click.echo(f'{click.style("✓ " + migration.label, fg="green")} {migration.title}')
        click.secho(f'Applied {len(applied)} migration(s).', fg='green', bold=True)


@db.command()
def status() -> None:
    """Shows the current version, pending migrations and any integrity problems."""
    runner = MigrationRunner()

    async def _action(conn: asyncpg.Connection) -> tuple[int, list[Migration], list[str]]:
        await runner.bootstrap(conn)
        return await runner.current_version(conn), await runner.pending(conn), await runner.check_integrity(conn)

    try:
        current, pending, problems = asyncio.run(_with_connection(_action))
    except (MigrationError, asyncpg.PostgresError, OSError):
        _fail('Could not read migration status.')
        return

    click.echo(f'Current version : {click.style(f"V{current:03d}", fg="cyan")}')
    click.echo(f'Latest available: {click.style(f"V{runner.latest_version:03d}", fg="cyan")}')
    click.echo(f'Pending         : {click.style(str(len(pending)), fg="yellow" if pending else "green")}')
    for migration in pending:
        click.echo(f'  - {migration.label} {migration.title}')

    file_problems = runner.validate() + problems
    if file_problems:
        click.secho(f'Problems ({len(file_problems)}):', fg='red', bold=True)
        for problem in file_problems:
            click.secho(f'  ! {problem}', fg='red')
    else:
        click.secho('Integrity       : OK', fg='green')


@db.command(name='history')
@click.option('--reverse', is_flag=True, help='Oldest first.')
def history(reverse: bool) -> None:
    """Lists applied migrations (with apply time) followed by any pending ones."""
    runner = MigrationRunner()

    try:
        applied = asyncio.run(_with_connection(runner.fetch_applied))
    except (asyncpg.PostgresError, OSError):
        _fail('Could not read migration history.')
        return

    records = sorted(applied.values(), key=lambda a: a.version, reverse=not reverse)
    if not records:
        click.secho('No migrations have been applied yet.', fg='yellow')
    for record in records:
        when = record.applied_at.strftime('%Y-%m-%d %H:%M')
        label = click.style(f'V{record.version:03d}', fg='green')
        click.echo(f'{label} {record.description.replace("_", " "):<45} {click.style(when, fg="bright_black")}')

    pending = [m for m in runner.migrations if m.version not in applied]
    for migration in pending:
        click.echo(f'{click.style(migration.label, fg="yellow")} {migration.title:<45} {click.style("pending", fg="yellow")}')


@db.command()
def verify() -> None:
    """Validates the migration files and checks applied rows for drift; exits non-zero on problems."""
    runner = MigrationRunner()
    problems = runner.validate()

    try:
        problems += asyncio.run(_with_connection(lambda conn: _verify_db(runner, conn)))
    except (asyncpg.PostgresError, OSError):
        _fail('Could not verify migrations against the database.')
        raise SystemExit(1) from None

    if problems:
        click.secho(f'Found {len(problems)} problem(s):', fg='red', bold=True)
        for problem in problems:
            click.secho(f'  ! {problem}', fg='red')
        raise SystemExit(1)
    click.secho('All migrations are valid and consistent with the database.', fg='green')


async def _verify_db(runner: MigrationRunner, conn: asyncpg.Connection) -> list[str]:
    await runner.bootstrap(conn)
    return await runner.check_integrity(conn)


@db.command()
@click.argument('version', type=int, required=False)
@click.option('--all', 'reseal_all', is_flag=True, help='Reseal every drifted applied migration.')
@click.option('--dry-run', is_flag=True, help='Show what would change without writing.')
def reseal(version: int | None, reseal_all: bool, dry_run: bool) -> None:
    """Re-sync an applied migration's stored checksum to its file WITHOUT re-running it.

    Use after a deliberate, safe edit to an already-applied migration whose change only affects
    how a *fresh* database is built (e.g. a corrected idempotency guard). The live schema already
    matches the intended result, so re-running is unnecessary — this just updates the recorded
    checksum so ``db verify`` stops reporting drift. It never executes migration SQL and preserves
    each migration's original apply time. Pass a VERSION (e.g. ``10``) or ``--all``.
    """
    if version is None and not reseal_all:
        click.secho('Provide a migration VERSION (e.g. `db reseal 10`) or use --all.', fg='red')
        raise SystemExit(2)
    if version is not None and reseal_all:
        click.secho('Use either a VERSION or --all, not both.', fg='red')
        raise SystemExit(2)

    runner = MigrationRunner()

    async def _action(conn: asyncpg.Connection) -> list[tuple[int, str, str]]:
        await runner.bootstrap(conn)
        targets = [m.version for m in await runner.drifted(conn)] if reseal_all else [version]  # type: ignore[list-item]
        results: list[tuple[int, str, str]] = []
        for target in targets:
            old, new = await runner.reseal(conn, target, dry_run=dry_run)
            results.append((target, old, new))
        return results

    try:
        results = asyncio.run(_with_connection(_action))
    except MigrationError as exc:
        click.secho(str(exc), fg='red')
        raise SystemExit(1) from None
    except (asyncpg.PostgresError, OSError):
        _fail('Failed to reseal migration(s). Check your database connection.')
        raise SystemExit(1) from None

    if not results:
        click.secho('No drifted applied migrations — nothing to reseal.', fg='green')
        return

    changed = 0
    for target, old, new in results:
        label = f'V{target:03d}'
        if old == new:
            click.echo(f'{click.style(label, fg="cyan")} already in sync ({old[:12]}).')
        else:
            changed += 1
            verb = 'Would reseal' if dry_run else 'Resealed'
            arrow = f'{click.style(old[:12], fg="red")} → {click.style(new[:12], fg="green")}'
            click.echo(f'{click.style(label, fg="green")} {verb}: {arrow}')

    if dry_run:
        click.secho(f'Dry run — {changed} migration(s) would be resealed. Re-run without --dry-run to apply.', fg='yellow')
    else:
        click.secho(f'Resealed {changed} migration(s). `db verify` should now be clean.', fg='green', bold=True)


class _NullBot:
    """Minimal stand-in so :class:`Database` can be constructed outside a running bot.

    ``Database`` only ever touches ``self.bot`` to call ``.close()`` if the connection pool
    fails to build; the watchlist repository never touches ``bot`` at all. Building a real
    ``app.core.Bot`` here would pull in the Discord gateway/cog loader for no reason.
    """

    async def close(self) -> None:
        pass


@main.group('comics', short_help='Comic catalogue archive', options_metavar='[options]')
def comics() -> None:
    """Fills the ``comic_*`` archive (V40) from the same sources the bot's 6 h refresh uses.

    The bot ingests on every refresh, so this is only needed to populate the archive without
    waiting for one — a fresh database, or a backfill after downtime.
    """


@comics.command('backfill')
@click.option(
    '--brand', '-b', type=click.Choice(('marvel', 'dc', 'manga', 'all')), default='all',
    help='Only this brand. Defaults to all three.',
)
def comics_backfill(brand: str) -> None:
    """Fetches the current release list for each brand and upserts it into the archive."""
    try:
        asyncio.run(_run_backfill(brand))
    except (RuntimeError, asyncpg.PostgresError, OSError):
        _fail('An error occurred while backfilling comics. Check your database connection.')
        raise SystemExit(1) from None


async def _run_backfill(brand: str) -> None:
    """Opens the same DB pool the bot uses plus one aiohttp session, ingests, closes both.

    Deliberately mirrors :func:`_run_sync`. The fetch is the fragile part (a self-hosted
    locg-api, a scraped manga page), so a brand that fails is reported and skipped rather than
    aborting the other two.
    """
    # Imported here rather than at module scope: this pulls in the comic cog's Discord-facing
    # models, which no other CLI command needs.
    from app.cogs.comic.client import LOCGClient, Parser

    session = aiohttp.ClientSession()
    db: Database | None = None
    try:
        db = await Database(_NullBot(), loop=asyncio.get_running_loop()).wait()  # type: ignore[arg-type]
        ingest = ComicIngest(db.releases)
        client = LOCGClient(session, base_url=locg_api_url)

        sources: list[tuple[str, Callable[[], Awaitable[list[Any]]]]] = [
            ('MARVEL', lambda: client.fetch_comics('marvel')),
            ('DC', lambda: client.fetch_comics('dc')),
            ('MANGA', Parser.bs4_viz),
        ]
        for name, fetch in sources:
            if brand != 'all' and name != brand.upper():
                continue
            try:
                data = await fetch()
            except (HTTPClientError, aiohttp.ClientError, OSError) as exc:
                click.secho(f'{name}: fetch failed ({exc}).', fg='red')
                continue
            if not data:
                click.secho(f'{name}: nothing returned.', fg='yellow')
                continue
            click.echo(str(await ingest.ingest(name, data)))
    finally:
        if db is not None:
            await db.close()
        await session.close()


@main.group('watchlist', short_help='Universe watchlist seed ingest', options_metavar='[options]')
def watchlist() -> None:
    """Turns hand-curated TOML seed files (``data/universes/*.toml``) plus TMDB metadata into
    ``watch_*`` table rows. See ``app/services/watchlist/`` for the seed format and ingest logic.
    """


def _seed_paths(universe: str | None) -> list[Path]:
    """Every seed file in ``data/universes/``, optionally filtered to one filename stem."""
    directory = config.data_path / 'universes'
    paths = sorted(directory.glob('*.toml'))
    if universe is not None:
        paths = [p for p in paths if p.stem == universe]
    return paths


_universe_option = click.option(
    '--universe', '-u', default=None, help='Only this universe (matches the seed filename stem, e.g. "mcu").',
)


@watchlist.command('lint')
@_universe_option
def watchlist_lint(universe: str | None) -> None:
    """Validates every seed file. No network, no database."""
    paths = _seed_paths(universe)
    if not paths:
        click.secho('No seed files found in data/universes/.', fg='green')
        return

    any_invalid = False
    for path in paths:
        try:
            seed = load_seed(path)
        except SeedError as exc:
            any_invalid = True
            click.secho(f'{path.name}: {len(exc.problems)} problem(s):', fg='red', bold=True)
            for problem in exc.problems:
                click.secho(f'  ! {problem}', fg='red')
        else:
            click.secho(f'{path.name}: OK ({seed.slug}, {len(seed.titles)} title(s)).', fg='green')

    if any_invalid:
        raise SystemExit(1)


@watchlist.command('sync')
@_universe_option
@click.option('--dry-run', is_flag=True, help='Fetch from TMDB but write nothing; report what would change.')
def watchlist_sync(universe: str | None, dry_run: bool) -> None:
    """Fetches TMDB metadata for every seed and upserts it into the watchlist tables."""
    if config.tmdb.token is None:
        click.secho('TMDB_API_TOKEN is not set — refusing to sync.', fg='red')
        raise SystemExit(1)

    paths = _seed_paths(universe)
    if not paths:
        click.secho('No seed files found in data/universes/.', fg='green')
        return

    seeds: list[UniverseSeed] = []
    for path in paths:
        try:
            seeds.append(load_seed(path))
        except SeedError as exc:
            click.secho(f'{path.name}: {len(exc.problems)} problem(s):', fg='red', bold=True)
            for problem in exc.problems:
                click.secho(f'  ! {problem}', fg='red')
            raise SystemExit(1) from None

    try:
        asyncio.run(_run_sync(seeds, dry_run=dry_run))
    except (RuntimeError, asyncpg.PostgresError, OSError):
        _fail('An error occurred while syncing the watchlist. Check your database connection.')
        raise SystemExit(1) from None


async def _run_sync(seeds: list[UniverseSeed], *, dry_run: bool) -> None:
    """Opens the same DB pool the bot uses plus one aiohttp session, syncs, closes both."""
    session = aiohttp.ClientSession()
    db: Database | None = None
    try:
        db = await Database(_NullBot(), loop=asyncio.get_running_loop()).wait()  # type: ignore[arg-type]
        client = TMDBClient(session)
        ingest = WatchlistIngest(client, db.watchlist)
        for seed in seeds:
            report = await ingest.sync(seed, regions=config.tmdb.regions, dry_run=dry_run)
            click.echo(report.summary())
    finally:
        if db is not None:
            await db.close()
        await session.close()


@watchlist.command('search')
@click.argument('kind', type=click.Choice(('movie', 'tv')))
@click.argument('query')
def watchlist_search(kind: str, query: str) -> None:
    """Searches TMDB for a title — a curation helper for finding ``tmdb_id`` values."""
    if config.tmdb.token is None:
        click.secho('TMDB_API_TOKEN is not set — refusing to search.', fg='red')
        raise SystemExit(1)

    asyncio.run(_run_search(kind, query))


async def _run_search(kind: str, query: str) -> None:
    async with aiohttp.ClientSession() as session:
        client = TMDBClient(session)
        try:
            data = await client.search(kind, query)
        except HTTPClientError:
            _fail('TMDB search failed.')
            raise SystemExit(1) from None

    results = data.get('results', [])
    if not results:
        click.secho('No results.', fg='yellow')
        return

    for result in results:
        title = result.get('title') or result.get('name') or '?'
        date = result.get('release_date') or result.get('first_air_date') or ''
        year = date[:4] if date else '?'
        click.echo(f'{result.get("id")} · {title} · {year}')


@main.group('news', short_help='News feed polling', options_metavar='[options]')
def news() -> None:
    """Polls the RSS/Atom sources in ``news_sources`` (V43) into ``news_items``/``news_subjects``.

    The bot polls on its own 15-minute loop, so this exists for the same reason
    ``comics backfill`` does: a fresh database stays empty until a background task has run, and
    an operator debugging a source should not have to wait for the next tick.
    """


@news.command('poll')
@click.option('--source', '-s', default=None, help='Only this source slug. Defaults to every enabled source.')
def news_poll(source: str | None) -> None:
    """Runs one polling pass now: fetch, parse, upsert and tag every story."""
    try:
        asyncio.run(_run_news_poll(source))
    except (RuntimeError, asyncpg.PostgresError, OSError):
        _fail('An error occurred while polling news. Check your database connection.')
        raise SystemExit(1) from None


async def _run_news_poll(source: str | None) -> None:
    """Opens the same DB pool the bot uses plus one aiohttp session, polls, closes both.

    The cadence rule (``due_sources``) is deliberately **not** applied: an operator asking for a
    run is the cadence, and a CLI that silently did nothing because a feed was polled twenty
    minutes ago is a CLI nobody can debug with. The per-source politeness budget still holds in
    the loop that actually runs unattended.
    """
    session = aiohttp.ClientSession()
    db: Database | None = None
    try:
        db = await Database(_NullBot(), loop=asyncio.get_running_loop()).wait()  # type: ignore[arg-type]
        added = await seed_sources(db.news)
        if added:
            click.secho(f'Seeded {len(added)} source(s): {", ".join(added)}.', fg='green')

        if source is None:
            sources = await db.news.list_sources()
        else:
            row = await db.news.get_source(source)
            if row is None:
                _fail(f'No such news source: {source!r}.')
                raise SystemExit(1)
            sources = [row]

        if not sources:
            click.secho('No enabled news sources.', fg='yellow')
            return

        index = await load_vocabulary(db.releases, db.watchlist)
        report = await NewsIngest(NewsClient(session), db.news, index).run(sources)
        click.echo(str(report))
    finally:
        if db is not None:
            await db.close()
        await session.close()


@news.command('sources')
def news_sources() -> None:
    """Lists every configured source with the outcome of its last poll."""
    asyncio.run(_run_news_sources())


async def _run_news_sources() -> None:
    """Prints the sources table, disabled rows included -- a dead feed has to be *visible*."""
    db: Database | None = None
    try:
        db = await Database(_NullBot(), loop=asyncio.get_running_loop()).wait()  # type: ignore[arg-type]
        rows = await db.news.list_sources(enabled_only=False)
    finally:
        if db is not None:
            await db.close()

    if not rows:
        click.secho('No news sources — run `news poll` once to seed the built-in list.', fg='yellow')
        return

    for row in rows:
        state = click.style('enabled', fg='green') if row['enabled'] else click.style('disabled', fg='red')
        fetched = row['last_fetched'].strftime('%Y-%m-%d %H:%M') if row['last_fetched'] else 'never'
        status = row['last_status'] or '—'
        colour = 'green' if status == '200' else 'yellow'
        click.echo(f'{row["slug"]:<24} {state} · {fetched} · {click.style(status, fg=colour)}')


if __name__ == '__main__':
    main()
