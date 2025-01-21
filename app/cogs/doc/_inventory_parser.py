import logging
import re
import zlib
from collections import defaultdict
from collections.abc import AsyncIterator

import aiohttp

log = logging.getLogger(__name__)

FAILED_REQUEST_ATTEMPTS = 3
_V2_LINE_RE = re.compile(r'(?x)(.+?)\s+(\S*:\S*)\s+(-?\d+)\s+?(\S*)\s+(.*)')

InventoryDict = defaultdict[str, list[tuple[str, str]]]


class InvalidHeaderError(Exception):
    """Raised when an inventory file has an invalid header."""


class ZlibStreamReader:
    """Class used for decoding zlib data of a streamline by line."""

    READ_CHUNK_SIZE = 16 * 1024  # 16 KiB

    def __init__(self, stream: aiohttp.StreamReader) -> None:
        self.stream = stream

    async def _read_compressed_chunks(self) -> AsyncIterator[bytes]:
        """Read zlib data in `READ_CHUNK_SIZE` sized chunks and decompress."""
        decompressor = zlib.decompressobj()
        async for chunk in self.stream.iter_chunked(self.READ_CHUNK_SIZE):
            yield decompressor.decompress(chunk)  # type: ignore

        yield decompressor.flush()

    async def __aiter__(self) -> AsyncIterator[str]:
        """Yield lines of decompressed text."""
        buf = b""
        async for chunk in self._read_compressed_chunks():
            buf += chunk
            pos = buf.find(b'\n')
            while pos != -1:
                yield buf[:pos].decode()
                buf = buf[pos + 1:]
                pos = buf.find(b'\n')


async def _load_v1(stream: aiohttp.StreamReader) -> InventoryDict:
    """Load a v1 intersphinx inventory file."""
    invdata = defaultdict(list)

    async for line in stream:
        name, type_, location = line.decode().rstrip().split(maxsplit=2)
        if type_ == 'mod':
            type_ = 'py:module'
            location += '#module-' + name
        else:
            type_ = 'py:' + type_
            location += '#' + name
        invdata[type_].append((name, location))
    return invdata


async def _load_v2(stream: aiohttp.StreamReader) -> InventoryDict:
    """Load a v2 intersphinx inventory file."""
    invdata = defaultdict(list)

    async for line in ZlibStreamReader(stream):
        m = _V2_LINE_RE.match(line.rstrip())
        name, type_, _prio, location, _dispname = m.groups()
        if location.endswith('$'):
            location = location[:-1] + name

        name = (
            name
            .replace('discord.ext.commands.', 'commands.')
            .replace('discord.commands.', 'commands.')
            .replace('discord.ext.tasks.', 'tasks.')
        )

        invdata[type_].append((name, location))
    return invdata


async def _fetch_inventory(session: aiohttp.ClientSession, url: str) -> InventoryDict:
    """Fetch, parse and return an intersphinx inventory file from an url."""
    timeout = aiohttp.ClientTimeout(sock_connect=5, sock_read=5)
    async with session.get(url, timeout=timeout, raise_for_status=True) as response:
        stream = response.content

        inventory_header = (await stream.readline()).decode().rstrip()
        try:
            inventory_version = int(inventory_header[-1:])
        except ValueError:
            raise InvalidHeaderError('Unable to convert inventory version header.')

        has_project_header = (await stream.readline()).startswith(b'# Project')
        has_version_header = (await stream.readline()).startswith(b'# Version')
        if not (has_project_header and has_version_header):
            raise InvalidHeaderError('Inventory missing project or version header.')

        if inventory_version == 1:
            return await _load_v1(stream)

        if inventory_version == 2:
            if b'zlib' not in await stream.readline():
                raise InvalidHeaderError('"zlib" not found in header of compressed inventory.')
            return await _load_v2(stream)

        raise InvalidHeaderError(f'Incompatible inventory version. Expected v1 or v2, got v{inventory_version}')


async def fetch_inventory(session: aiohttp.ClientSession, url: str) -> InventoryDict | None:
    """Get an inventory dict from `url`, retrying `FAILED_REQUEST_ATTEMPTS` times on errors.

    `url` should point at a valid sphinx objects.inv file, which will be parsed into the
    inventory dict in the format of {'domain:role': [('symbol_name', 'relative_url_to_symbol'), ...], ...}
    """
    for attempt in range(1, FAILED_REQUEST_ATTEMPTS+1):
        try:
            inventory = await _fetch_inventory(session, url)
        except aiohttp.ClientResponseError as e:
            if e.status == 404:
                log.warning('Inventory not found at %s; trying again (%r/%r).', url, attempt, FAILED_REQUEST_ATTEMPTS)
            else:
                # Somehow reachable, but not a valid inventory file?
                log.error(
                    'Failed to get inventory from %s with status %r; '
                    'trying again (%r/%r).', url, e.status, attempt. FAILED_REQUEST_ATTEMPTS
                )
        except aiohttp.ClientError:
            log.error(
                'Failed to get inventory from %s; '
                'trying again (%s/%s).', url, attempt, FAILED_REQUEST_ATTEMPTS
            )
        except InvalidHeaderError:
            raise
        except Exception:
            log.exception(
                'An unexpected error has occurred during fetching of %s; '
                'trying again (%r/%r).', url, attempt, FAILED_REQUEST_ATTEMPTS
            )
        else:
            return inventory

    return None
