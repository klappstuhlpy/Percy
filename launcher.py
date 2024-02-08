import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import traceback
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TypedDict, TYPE_CHECKING, cast

import asyncpg
import click
import discord
from sqlalchemy.ext.asyncio import create_async_engine

import config

from bot import Percy
from cogs.utils.constants import REVISION_FILE, BOT_BASE_FOLDER

try:
    import uvloop  # noqa
except ImportError:
    pass
else:
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())


class Revisions(TypedDict):
    version: int
    database_uri: str


class Revision:
    __slots__ = ('kind', 'version', 'description', 'file')

    def __init__(self, *, kind: str, version: int, description: str, file: Path) -> None:
        self.kind: str = kind
        self.version: int = version
        self.description: str = description
        self.file: Path = file

    @classmethod
    def from_match(cls, match: re.Match[str], file: Path):
        return cls(
            kind=match.group('kind'), version=int(match.group('version')), description=match.group('description'), file=file
        )


class Migrations:
    def __init__(self, *, filename: str = 'migrations/revisions.json'):
        self.filename: str = filename
        self.root: Path = Path(filename).parent
        self.revisions: dict[int, Revision] = self.get_revisions()
        self.load()

    def ensure_path(self) -> None:
        self.root.mkdir(exist_ok=True)

    def load_metadata(self) -> Revisions:
        try:
            with open(self.filename, 'r', encoding='utf-8') as fp:
                return Revisions(**json.load(fp))
        except FileNotFoundError:
            return Revisions(version=0, database_uri='')

    def get_revisions(self) -> dict[int, Revision]:
        result: dict[int, Revision] = {}
        for file in self.root.glob('*.sql'):
            match = REVISION_FILE.match(file.name)
            if match is not None:
                rev = Revision.from_match(match, file)
                result[rev.version] = rev

        return result

    def dump(self) -> Revisions:
        return Revisions(version=self.version, database_uri=self.database_uri)

    # noinspection PyAttributeOutsideInit
    def load(self) -> None:
        self.ensure_path()
        data = self.load_metadata()

        self.version: int = data['version']
        self.database_uri: str = data['database_uri']

    def save(self):
        temp = f'{self.filename}.{uuid.uuid4()}.tmp'
        with open(temp, 'w', encoding='utf-8') as tmp:
            json.dump(self.dump(), tmp)

        os.replace(temp, self.filename)

    @property
    def is_next_revision_taken(self) -> bool:
        return self.version + 1 in self.revisions

    @property
    def ordered_revisions(self) -> list[Revision]:
        return sorted(self.revisions.values(), key=lambda r: r.version)

    def create_revision(self, reason: str, *, kind: str = 'V') -> Revision:
        cleaned = re.sub(r'\s', '_', reason)
        filename = f'{kind}{self.version + 1}__{cleaned}.sql'
        path = self.root / filename

        stub = (
            f'-- Revises: V{self.version}\n'
            f'-- Creation Date: {discord.utils.utcnow()} UTC\n'
            f'-- Reason: {reason}\n\n'
        )

        with open(path, 'w', encoding='utf-8', newline='\n') as fp:
            fp.write(stub)

        self.save()
        return Revision(kind=kind, description=reason, version=self.version + 1, file=path)

    async def upgrade(self, connection: asyncpg.Connection) -> int:
        ordered = self.ordered_revisions
        successes = 0
        async with connection.transaction():
            for revision in ordered:
                if revision.version > self.version:
                    sql = revision.file.read_text('utf-8')
                    await connection.execute(sql)
                    successes += 1

        self.version += successes
        self.save()
        return successes

    def display(self) -> None:
        ordered = self.ordered_revisions
        for revision in ordered:
            if revision.version > self.version:
                sql = revision.file.read_text('utf-8')
                click.echo(sql)


TRACE_LEVEL = 5


if TYPE_CHECKING:
    LoggerClass = logging.Logger
else:
    LoggerClass = logging.getLoggerClass()


class PercyLogger(LoggerClass):
    """Custom implementation of the `Logger` class with an added `trace` method."""

    def trace(self, msg: str, *args, **kwargs) -> None:
        """
        Log 'msg % args' with severity 'TRACE'.

        To pass exception information, use the keyword argument exc_info with
        a true value, e.g.

        logger.trace('Houston, we have an %s', 'interesting problem', exc_info=1)
        """
        if self.isEnabledFor(TRACE_LEVEL):
            self.log(TRACE_LEVEL, msg, *args, **kwargs)


def get_logger(name: str | None = None) -> PercyLogger:
    """Utility to make mypy recognise that logger is of type `PercyLogger`."""
    return cast(PercyLogger, logging.getLogger(name))


class RemoveNoise(logging.Filter):
    def __init__(self):
        super().__init__(name='discord.state')

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelname == 'WARNING' and 'referencing an unknown' in record.msg:
            return False
        return True


class _ColourFormatter(logging.Formatter):
    LEVEL_COLOURS = [
        (logging.DEBUG, '\x1b[40;1m', 5),
        (logging.INFO, '\x1b[34;1m', 4),
        (logging.WARNING, '\x1b[33;1m', 7),
        (logging.ERROR, '\x1b[31m', 5),
        (logging.CRITICAL, '\x1b[41m', 8),
    ]

    FORMATS = {
        level: logging.Formatter(
            f'%(asctime)s\x1b[0m | {colour}%(levelname)-{length}s\x1b[0m \x1b[35m%(name)s\x1b[0m: %(message)s',
            '%Y-%m-%d %H:%M:%S',
        )
        for level, colour, length in LEVEL_COLOURS
    }

    def format(self, record):
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
def setup_logging():
    logging.TRACE = TRACE_LEVEL
    logging.addLevelName(TRACE_LEVEL, 'TRACE')
    logging.setLoggerClass(PercyLogger)

    root_log = get_logger()

    try:
        dt_fmt = '%Y-%m-%d %H:%M:%S'
        fmt = logging.Formatter(fmt='[{asctime}] | {levelname:<7} - {name}: {message}', datefmt=dt_fmt, style='{')

        discord.utils.setup_logging(formatter=_ColourFormatter())

        max_bytes = 32 * 1024 * 1024  # 32 MiB
        get_logger('discord').setLevel(logging.INFO)
        get_logger('discord.http').setLevel(logging.WARNING)
        get_logger('discord.state').addFilter(RemoveNoise())
        get_logger('charset_normalizer').setLevel(logging.ERROR)

        root_log.setLevel(logging.INFO if not config.debug else logging.DEBUG)
        handler = RotatingFileHandler(
            filename=os.path.join(BOT_BASE_FOLDER, 'percy.log'),
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


async def create_pool() -> asyncpg.Pool:
    def _encode_jsonb(value):
        return json.dumps(value)

    def _decode_jsonb(value):
        return json.loads(value)

    async def init(con):  # noqa
        await con.set_type_codec(
            'jsonb',
            schema='pg_catalog',
            encoder=_encode_jsonb,
            decoder=_decode_jsonb,
            format='text',
        )

    return await asyncpg.create_pool(
        config.postgresql,
        init=init,
        command_timeout=300,
        max_size=20,
        min_size=20,
    )


async def run_bot():
    discord.VoiceClient.warn_nacl = False
    _log = get_logger()

    try:
        pool = await create_pool()
    except Exception:  # noqa
        click.echo('Unable to establish a connection with PostgreSQL. Exiting.', file=sys.stderr)
        _log.exception('Unable to establish a connection with PostgreSQL. Exiting.')
        return

    async with Percy() as bot:
        bot.pool = pool
        bot.alchemy_engine = create_async_engine(bot.config.alchemy_postgresql, echo=False)  # ORM feature
        await bot.start()


@click.group(invoke_without_command=True, options_metavar='[options]')
@click.pass_context
def main(ctx):
    """Launches the bot."""
    if ctx.invoked_subcommand is None:
        with setup_logging():
            asyncio.run(run_bot())


@main.group(short_help='Database Configuration', options_metavar='[options]')
def db():
    pass


async def ensure_db_use() -> bool:
    connection: asyncpg.Connection = await asyncpg.connect(config.postgresql)
    await connection.close()
    return True


@db.command()
def init():
    """Initializes the database and creates the initial revision."""

    asyncio.run(ensure_db_use())

    migrations = Migrations()
    migrations.database_uri = config.postgresql

    try:
        applied = asyncio.run(run_upgrade(migrations))
    except Exception:  # noqa
        traceback.print_exc()
        click.secho('Failed to initialize and apply migrations. Please check your database configuration and migration scripts.', fg='red')
    else:
        click.secho(f'Successfully initialized the database and applied {applied} migration(s).', fg='green')


@db.command()
@click.option('--reason', '-r', help='The reason for this revision.', required=True)
def migrate(reason: str):
    """Creates a new revision for you to edit."""
    migrations = Migrations()
    if migrations.is_next_revision_taken:
        click.secho('An unapplied migration for the next version already exists. Please apply pending migrations before creating a new one.', fg='yellow')
        click.secho('Hint: Apply pending migrations with the `upgrade` command.', fg='yellow', bold=True)
        return

    revision = migrations.create_revision(reason)
    click.secho(f'Successfully created revision V{revision.version}.', fg='green')


async def run_upgrade(migrations: Migrations) -> int:
    connection: asyncpg.Connection = await asyncpg.connect(migrations.database_uri)  # type: ignore
    return await migrations.upgrade(connection)


@db.command()
@click.option('--sql', help='Print the SQL instead of executing it', is_flag=True)
def upgrade(sql: bool):
    """Upgrades the database at the given revision (if any)."""
    migrations = Migrations()

    if sql:
        migrations.display()
        return

    try:
        applied = asyncio.run(run_upgrade(migrations))
    except Exception:  # noqa
        traceback.print_exc()
        click.secho('An error occurred while applying the database migrations. Please check your migration scripts.', fg='red')
    else:
        click.secho(f'Successfully applied {applied} migration(s) to the database.', fg='green')


@db.command()
def current():
    """Shows the current active revision version."""
    migrations = Migrations()
    as_yellow = click.style(f'{migrations.version:>03}', fg='yellow')
    click.echo(f'Version {as_yellow}')


@db.command()
@click.option('--reverse', help='Print in reverse order (oldest first).', is_flag=True)
def log(reverse: bool):
    """Displays the revision history."""
    migrations = Migrations()
    # Revisions are oldest first already
    revs = reversed(migrations.ordered_revisions) if not reverse else migrations.ordered_revisions
    for rev in revs:
        as_yellow = click.style(f'V{rev.version:>03}', fg='yellow')
        click.echo(f'{as_yellow} {rev.description.replace('_', ' ')}')


if __name__ == '__main__':
    main()
