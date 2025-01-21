import json
import re
import uuid
from pathlib import Path
from typing import ClassVar, TypedDict

import asyncpg
import click
import discord

__all__ = (
    'Migrations'
)


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
    def from_match(cls, match: re.Match[str], file: Path) -> 'Revision':
        return cls(
            kind=match.group('kind'), version=int(match.group('version')), description=match.group('description'),
            file=file
        )


class Migrations:
    """Represents the migration manager for the bot."""

    REVISION_FILE: ClassVar[re.Pattern] = re.compile(r'(?P<kind>[VU])(?P<version>[0-9]+)__(?P<description>.+).sql')

    def __init__(self, *, filename: str = 'migrations/revisions.json') -> None:
        self.filename: str = filename
        self.root: Path = Path(filename).parent
        self.revisions: dict[int, Revision] = self.get_revisions()
        self.load()

    def ensure_path(self) -> None:
        self.root.mkdir(exist_ok=True)

    def load_metadata(self) -> Revisions:
        try:
            with Path(self.filename).open(encoding='utf-8') as fp:
                return Revisions(**json.load(fp))
        except FileNotFoundError:
            return Revisions(version=0, database_uri='')

    def get_revisions(self) -> dict[int, Revision]:
        result: dict[int, Revision] = {}
        for file in self.root.glob('*.sql'):
            match = self.REVISION_FILE.match(file.name)
            if match is not None:
                rev = Revision.from_match(match, file)
                result[rev.version] = rev

        return result

    def dump(self) -> Revisions:
        return Revisions(version=self.version, database_uri=self.database_uri)

    def load(self) -> None:
        self.ensure_path()
        data = self.load_metadata()

        self.version: int = data['version']
        self.database_uri: str = data['database_uri']

    def save(self) -> None:
        temp = f'{self.filename}.{uuid.uuid4()}.tmp'
        _path = Path(temp)
        with _path.open('w', encoding='utf-8') as tmp:
            json.dump(self.dump(), tmp)

        _path.replace(self.filename)

    @property
    def is_next_revision_taken(self) -> bool:
        return self.version + 1 in self.revisions

    @property
    def ordered_revisions(self) -> list[Revision]:
        return sorted(self.revisions.values(), key=lambda r: r.version)

    def create_revision(self, reason: str, *, kind: str = 'V') -> Revision:
        cleaned = re.sub(r'\s', '_', reason)
        filename = f'{kind}{self.version + 1}__{cleaned}.sql'
        file_path = self.root / filename

        with Path(file_path).open('w', encoding='utf-8', newline='\n') as fp:
            fp.write((
                f'-- Revises: V{self.version}\n'
                f'-- Creation Date: {discord.utils.utcnow()} UTC\n'
                f'-- Reason: {reason}'
            ))

        self.save()
        return Revision(kind=kind, description=reason, version=self.version + 1, file=file_path)

    async def upgrade(self, connection: asyncpg.Connection, revision_number: int | None = None) -> int:
        successes = 0
        async with connection.transaction():
            if revision_number is not None:
                revision = self.revisions.get(revision_number, None)
                if revision is None:
                    raise ValueError(f'No such revision `{revision_number}`')

                sql = revision.file.read_text('utf-8')
                await connection.execute(sql)
                successes += 1
            else:
                for revision in self.ordered_revisions:
                    if revision.version > self.version:
                        sql = revision.file.read_text('utf-8')
                        await connection.execute(sql)
                        successes += 1

        if revision_number is None:
            self.version += successes

        self.save()
        return successes

    def display(self) -> None:
        ordered = self.ordered_revisions
        for revision in ordered:
            if revision.version > self.version:
                sql = revision.file.read_text('utf-8')
                click.echo(sql)
