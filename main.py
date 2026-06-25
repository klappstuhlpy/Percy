import asyncio
import contextlib
import logging
import traceback
from collections.abc import Awaitable, Callable, Generator
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, ClassVar

import asyncpg
import click
import discord

from app.core import Bot
from app.database import MigrationRunner
from app.database.migrations import MIGRATIONS_TABLE, Migration, MigrationError
from config import DatabaseConfig, logs_path

try:
    import uvloop  # type: ignore[import-not-found]
except ImportError:
    pass
else:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


__all__ = (
    'RemoveNoise',
    'db',
    'history',
    'init',
    'main',
    'migrate',
    'run_bot',
    'setup_logging',
    'status',
    'upgrade',
    'verify',
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


if __name__ == '__main__':
    main()
