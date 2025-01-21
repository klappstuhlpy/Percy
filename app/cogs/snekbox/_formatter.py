"""I/O File protocols for snekbox."""
from __future__ import annotations

from base64 import b64decode, b64encode
from dataclasses import dataclass
from io import BytesIO
from pathlib import PurePosixPath

import regex
from discord import File

FILE_SIZE_LIMIT = 8 * 1024 * 1024
FILE_COUNT_LIMIT = 10


RE_ANSI = regex.compile(r'\\u.*\[(.*?)m')
RE_BACKSLASH = regex.compile(r'\\.')
RE_DISCORD_FILE_NAME_DISALLOWED = regex.compile(r'[^a-zA-Z0-9._-]+')


def sizeof_fmt(num: int | float, suffix: str = 'B') -> str:
    """Return a human-readable file size."""
    num = float(num)
    for unit in ("", 'Ki', 'Mi', 'Gi', 'Ti', 'Pi', 'Ei', 'Zi'):
        if abs(num) < 1024:
            num_str = f'{int(num)}' if num.is_integer() else f'{num:3.1f}'
            return f'{num_str} {unit}{suffix}'
        num /= 1024
    num_str = f'{int(num)}' if num.is_integer() else f'{num:3.1f}'
    return f'{num_str} Yi{suffix}'


def normalize_discord_file_name(name: str) -> str:
    """Return a normalized valid discord file name."""
    name = RE_ANSI.sub('_', name)
    name = RE_BACKSLASH.sub('_', name)
    name = RE_DISCORD_FILE_NAME_DISALLOWED.sub('_', name)
    return name


@dataclass(frozen=True)
class FileAttachment:
    """File Attachment from Snekbox eval."""

    filename: str
    content: bytes

    def __repr__(self) -> str:
        """Return the content as a string."""
        content = f'{self.content[:10]}...' if len(self.content) > 10 else self.content
        return f'FileAttachment(path={self.filename!r}, content={content})'

    @property
    def suffix(self) -> str:
        """Return the file suffix."""
        return PurePosixPath(self.filename).suffix

    @property
    def name(self) -> str:
        """Return the file name."""
        return PurePosixPath(self.filename).name

    @classmethod
    def from_dict(cls, data: dict, size_limit: int = FILE_SIZE_LIMIT) -> FileAttachment:
        """Create a FileAttachment from a dict response."""
        size = data.get('size')
        if (size and size > size_limit) or (len(data['content']) > size_limit):
            raise ValueError('File size exceeds limit')

        content = b64decode(data['content'])

        if len(content) > size_limit:
            raise ValueError('File size exceeds limit')

        return cls(data['path'], content)

    def to_dict(self) -> dict[str, str]:
        """Convert the attachment to a json dict."""
        content = self.content
        if isinstance(content, str):
            content = content.encode('utf-8')

        return {
            'path': self.filename,
            'content': b64encode(content).decode('ascii'),
        }

    def to_file(self) -> File:
        """Convert to a discord.File."""
        name = normalize_discord_file_name(self.name)
        return File(BytesIO(self.content), filename=name)
