import asyncio
import contextlib
import logging
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import ClassVar

import asyncpg
import click
import discord

from app.core import Bot
from app.database import Migrations
from config import DatabaseConfig, path

try:
    import uvloop
except ImportError:
    pass
else:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


__all__ = (
    'RemoveNoise',
    'setup_logging',
    'run_bot',
    'main',
    'db',
    'init',
    'migrate',
    'upgrade',
    'log',
)


class RemoveNoise(logging.Filter):
    """A filter to remove noise from the logs."""
    def __init__(self) -> None:
        super().__init__(name='discord.state')

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelname == 'WARNING' and 'referencing an unknown' in record.msg:
            return False
        return True


class _ColourFormatter(logging.Formatter):
    LEVEL_COLOURS: ClassVar[tuple[int, str, int]] = [
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
        formatter = self.FORMATS.get(record.levelno)
        if formatter is None:
            formatter = self.FORMATS[logging.DEBUG]

        if record.exc_info:
            text = formatter.formatException(record.exc_info)
            record.exc_text = f'\x1b[31m{text}\x1b[0m'

        output = formatter.format(record)

        record.exc_text = None
        return output


@contextlib.contextmanager
def setup_logging() -> None:
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

        root_log.setLevel(logging.INFO)
        handler = RotatingFileHandler(
            filename=Path(path, 'percy.log'),
            encoding='utf-8', mode='w', maxBytes=max_bytes, backupCount=5
        )
        handler.setFormatter(fmt)
        root_log.addHandler(handler)

        yield
    finally:
        handlers = root_log.handlers[:]
        for hdlr in handlers:
            hdlr.close()
            root_log.removeHandler(hdlr)


async def run_bot() -> None:
    discord.VoiceClient.warn_nacl = False

    async with Bot() as bot:
        try:
            await bot.start()
        except asyncio.CancelledError:
            pass


@click.group(invoke_without_command=True, options_metavar='[options]')
@click.pass_context
def main(ctx) -> None:
    """Launches the bot."""
    if ctx.invoked_subcommand is None:
        with setup_logging():
            asyncio.run(run_bot())


@main.group(short_help='Database Configuration', options_metavar='[options]')
def db() -> None:
    """Manages the database."""
    pass


@db.command()
def init() -> None:
    """Initializes the database and creates the initial revision."""
    migrations = Migrations()

    try:
        applied = asyncio.run(run_migration_upgrade(migrations))
    except:
        traceback.print_exc()
        click.secho(
            'Failed to initialize and apply migrations. Please check your database configuration and migration scripts.',
            fg='red')
    else:
        click.secho(f'Successfully initialized the database and applied {applied} migration(s).', fg='green')


@db.command()
@click.option('--reason', '-r', help='The reason for this revision.', required=True)
def migrate(reason: str) -> None:
    """Creates a new revision for you to edit."""
    migrations = Migrations()
    if migrations.is_next_revision_taken:
        click.secho(
            'An unapplied migration for the next version already exists. Please apply pending migrations before creating a new one.',
            fg='yellow')
        click.secho('Hint: Apply pending migrations with the `upgrade` command.', fg='yellow', bold=True)
        return

    revision = migrations.create_revision(reason)
    click.secho(f'Successfully created revision V{revision.version}.', fg='green')


async def run_migration_upgrade(migrations: Migrations, revision: int | None = None) -> int:
    connection: asyncpg.Connection = await asyncpg.connect(**DatabaseConfig.to_kwargs())
    return await migrations.upgrade(connection, revision_number=revision)


@db.command()
@click.option('--revision', '-r', help='The revision number to upgrade. Default is latest.')
@click.option('--sql', help='Print the SQL instead of executing it', is_flag=True)
def upgrade(revision: str, sql: bool) -> None:
    """Upgrades the database at the given revision (if any)."""
    migrations = Migrations()

    if sql:
        migrations.display()
        return

    if revision:
        try:
            revision = int(revision)
        except ValueError:
            click.secho('The revision number must be a valid integer.', fg='red')
            return

    try:
        applied = asyncio.run(run_migration_upgrade(migrations, revision))
    except:
        traceback.print_exc()
        click.secho('An error occurred while applying the database migrations. Please check your migration scripts.',
                    fg='red')
    else:
        click.secho(f'Successfully applied {applied} migration(s) to the database.', fg='green')


@db.command()
@click.option('--reverse', help='Print in reverse order (oldest first).', is_flag=True)
def log(reverse: bool) -> None:
    """Displays the revision history."""
    migrations = Migrations()
    revs = reversed(migrations.ordered_revisions) if not reverse else migrations.ordered_revisions
    for rev in revs:
        as_yellow = click.style(f'V{rev.version:>03}', fg='yellow')
        click.echo(f'{as_yellow} {rev.description.replace('_', ' ')}')


if __name__ == '__main__':
    main()
