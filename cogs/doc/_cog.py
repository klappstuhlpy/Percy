from __future__ import annotations

import asyncio
import io
import os
import re
import sys
import textwrap
import zlib
from collections import defaultdict
from ssl import CertificateError
from types import SimpleNamespace
from typing import Literal, Any, List, Annotated, Optional, Generator

import aiohttp
import discord
from aiohttp import ClientConnectorError
from discord import app_commands
from discord.ext import commands
from pydis_core.utils.scheduling import Scheduler

from bot import Percy
from launcher import get_logger
from . import PRIORITY_PACKAGES, _batch_parser, doc_cache, _inventory_parser
from ._inventory_parser import InvalidHeaderError, InventoryDict, fetch_inventory
from .. import command
from ..utils import helpers, fuzzy
from ..utils.constants import PACKAGE_NAME_RE
from ..utils.context import Context
from ..utils.formats import plural
from ..utils.lock import lock, SharedEvent
from ..utils.paginator import BasePaginator

log = get_logger(__name__)

# symbols with a group contained here will get the group prefixed on duplicates
FORCE_PREFIX_GROUPS = (
    "term",
    "label",
    "token",
    "doc",
    "pdbcommand",
    "2to3fixer",
)

FETCH_RESCHEDULE_DELAY = SimpleNamespace(first=2, repeated=5)

COMMAND_LOCK_SINGLETON = "inventory refresh"


class PackageName(commands.Converter):
    """
    A converter that checks whether the given string is a valid package name.

    Package names are used for stats and are restricted to the a-z and _ characters.
    """

    async def convert(self, ctx: Context, argument: str) -> str:
        """Checks whether the given string is a valid package name."""
        if PACKAGE_NAME_RE.search(argument):
            raise commands.BadArgument(
                "The provided package name is not valid; please only use the ., _, 0-9, and a-z characters.")
        return argument


class ValidURL(commands.Converter):
    """
    Represents a valid webpage URL.

    This converter checks whether the given URL can be reached and requesting it returns a status
    code of 200. If not, `BadArgument` is raised.

    Otherwise, it simply passes through the given URL.
    """

    async def convert(self, ctx: Context, url: str) -> str:
        """This converter checks whether the given URL can be reached with a status code of 200."""
        try:
            async with ctx.bot.session.get(url) as resp:
                if resp.status != 200:
                    raise commands.BadArgument(
                        f"HTTP GET on `{url}` returned status `{resp.status}`, expected 200"
                    )
        except CertificateError:
            if url.startswith("https"):
                raise commands.BadArgument(
                    f"Got a `CertificateError` for URL `{url}`. Does it support HTTPS?"
                )
            raise commands.BadArgument(f"Got a `CertificateError` for URL `{url}`.")
        except ValueError:
            raise commands.BadArgument(f"`{url}` doesn't look like a valid hostname to me.")
        except ClientConnectorError:
            raise commands.BadArgument(f"Cannot connect to host with URL `{url}`.")
        return url


class Inventory(commands.Converter):
    """
    Represents an Intersphinx inventory URL.

    This converter checks whether intersphinx accepts the given inventory URL, and raises
    `BadArgument` if that is not the case or if the url is unreachable.

    Otherwise, it returns the url and the fetched inventory dict in a tuple.
    """

    async def convert(self, ctx: Context, url: str) -> tuple[str, _inventory_parser.InventoryDict]:
        """Convert url to Intersphinx inventory URL."""
        await ctx.typing()
        try:
            inventory = await _inventory_parser.fetch_inventory(ctx.bot.session, url)
        except _inventory_parser.InvalidHeaderError:
            raise commands.BadArgument(f"{ctx.tick(False)} Unable to parse inventory because of invalid header, check if URL is correct.")
        else:
            if inventory is None:
                raise commands.BadArgument(
                    f"Failed to fetch inventory file after {_inventory_parser.FAILED_REQUEST_ATTEMPTS} attempts."
                )
            return url, inventory


class DocItem:
    """Holds inventory symbol information."""

    def __init__(
            self,
            package: str,
            group: str,
            base_url: str,
            relative_url_path: str,
            symbol_id: str,
            resolved_fields: Optional[dict[str, str]] = None,
    ):
        self.package = package
        self.group = group
        self.base_url = base_url
        self.relative_url_path = relative_url_path
        self.symbol_id = symbol_id
        self.resolved_fields = resolved_fields or {}

    @property
    def url(self) -> str:
        """Return the absolute url to the symbol."""
        return self.base_url + self.relative_url_path


class LinePaginator(BasePaginator[tuple[str, str]]):

    async def format_page(self, entries: List[tuple[str, str]], /) -> discord.Embed:
        embed = discord.Embed(color=helpers.Colour.darker_red())
        embed.set_footer(text=f'{plural(len(self.entries)):inventory|invetories} found.')
        results = [f"• [`{entry[0]}`]({entry[1]})" for entry in entries]
        embed.add_field(name="Results", value='\n'.join(results))

        return embed


class SphinxObjectFileReader:
    # Inspired by Sphinx's InventoryFileReader
    BUFFSIZE = 16 * 1024  # 16KB

    def __init__(self, buffer: bytes):
        self.stream = io.BytesIO(buffer)

    def readline(self) -> str:
        return self.stream.readline().decode('utf-8')

    def skipline(self) -> None:
        self.stream.readline()

    def read_compressed_chunks(self) -> Generator[bytes, None, None]:
        decompressor = zlib.decompressobj()
        while True:
            chunk = self.stream.read(self.BUFFSIZE)
            if len(chunk) == 0:
                break
            yield decompressor.decompress(chunk)
        yield decompressor.flush()

    def read_compressed_lines(self) -> Generator[str, None, None]:
        buf = b''
        for chunk in self.read_compressed_chunks():
            buf += chunk
            pos = buf.find(b'\n')
            while pos != -1:
                yield buf[:pos].decode('utf-8')
                buf = buf[pos + 1:]
                pos = buf.find(b'\n')


class DocCog(commands.Cog):
    """A set of commands for querying & displaying documentation."""

    def __init__(self, bot: Percy):
        self.base_urls: dict[str, Any] = {}
        self.bot: Percy = bot
        self.doc_symbols: dict[str, DocItem] = {}
        self.item_fetcher = _batch_parser.BatchParser(bot)
        self.renamed_symbols = defaultdict(list)

        self.inventory_scheduler = Scheduler(self.__class__.__name__)
        self.symbol_get_event: SharedEvent = SharedEvent()

        self.refresh_event = asyncio.Event()
        self.refresh_event.set()

    async def cog_load(self) -> None:
        """Refresh documentation inventory on cog initialization."""
        self.bot.loop.create_task(self.refresh_inventories())

    async def cog_unload(self) -> None:
        """Clear scheduled inventories, queued symbols and cleanup task on cog unload."""
        self.inventory_scheduler.cancel_all()
        await self.item_fetcher.clear()

    async def documentation_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:

        if not current:
            return []

        if len(current) < 3:
            return [app_commands.Choice(name=current, value=current)]

        assert interaction.command is not None

        _, matches = self.get_symbol_item(current, 15)
        return [app_commands.Choice(name=f"{m.symbol_id} ({m.package})", value=m.symbol_id) for _, m in matches]

    def update_single(self, package_name: str, base_url: str, inventory: InventoryDict) -> None:
        """
        Build the inventory for a single package.

        Where:
            * `package_name` is the package name to use in logs and when qualifying symbols
            * `base_url` is the root documentation URL for the specified package, used to build
                absolute paths that link to specific symbols
            * `package` is the content of a intersphinx inventory.
        """
        self.base_urls[package_name] = base_url

        for group, items in inventory.items():
            for symbol_name, relative_doc_url in items:
                group_name = group.split(":")[1]
                symbol_name = self.ensure_unique_symbol_name(
                    package_name,
                    group_name,
                    symbol_name,
                )

                relative_url_path, _, symbol_id = relative_doc_url.partition("#")
                doc_item = DocItem(
                    package_name,
                    sys.intern(group_name),
                    base_url,
                    sys.intern(relative_url_path),
                    symbol_id,
                )
                self.doc_symbols[symbol_name] = doc_item
                self.item_fetcher.add_item(doc_item)

        log.trace(f"Fetched inventory for {package_name}.")

    async def update_or_reschedule_inventory(
            self,
            api_package_name: str,
            base_url: str,
            inventory_url: str,
    ) -> None:
        """
        Update the cog's inventories, or reschedule this method to execute again if the remote inventory is unreachable.

        The first attempt is rescheduled to execute in `FETCH_RESCHEDULE_DELAY.first` minutes, the subsequent attempts
        in `FETCH_RESCHEDULE_DELAY.repeated` minutes.
        """
        try:
            package = await fetch_inventory(self.bot.session, inventory_url)
        except InvalidHeaderError as e:
            # Do not reschedule if the header is invalid, as the request went through but the contents are invalid.
            log.warning(f"Invalid inventory header at {inventory_url}. Reason: {e}")
            return

        if not package:
            if api_package_name in self.inventory_scheduler:
                self.inventory_scheduler.cancel(api_package_name)
                delay = FETCH_RESCHEDULE_DELAY.repeated
            else:
                delay = FETCH_RESCHEDULE_DELAY.first
            log.info(f"Failed to fetch inventory; attempting again in {delay} minutes.")
            self.inventory_scheduler.schedule_later(
                delay * 60,
                api_package_name,
                self.update_or_reschedule_inventory(api_package_name, base_url, inventory_url),
            )
        else:
            if not base_url:
                base_url = self.base_url_from_inventory_url(inventory_url)
            self.update_single(api_package_name, base_url, package)

    def ensure_unique_symbol_name(self, package_name: str, group_name: str, symbol_name: str) -> str:
        """
        Ensure `symbol_name` doesn't overwrite an another symbol in `doc_symbols`.

        For conflicts, rename either the current symbol or the existing symbol with which it conflicts.
        Store the new name in `renamed_symbols` and return the name to use for the symbol.

        If the existing symbol was renamed or there was no conflict, the returned name is equivalent to `symbol_name`.
        """
        if (item := self.doc_symbols.get(symbol_name)) is None:
            return symbol_name  # There's no conflict so it's fine to simply use the given symbol name.

        def rename(prefix: str, *, rename_extant: bool = False) -> str:
            new_name = f"{prefix}.{symbol_name}"
            if new_name in self.doc_symbols:
                # If there's still a conflict, qualify the name further.
                if rename_extant:
                    new_name = f"{item.package}.{item.group}.{symbol_name}"
                else:
                    new_name = f"{package_name}.{group_name}.{symbol_name}"

            self.renamed_symbols[symbol_name].append(new_name)

            if rename_extant:
                # Instead of renaming the current symbol, rename the symbol with which it conflicts.
                self.doc_symbols[new_name] = self.doc_symbols[symbol_name]
                return symbol_name
            return new_name

        # When there's a conflict, and the package names of the items differ, use the package name as a prefix.
        if package_name != item.package:
            if package_name in PRIORITY_PACKAGES:
                return rename(item.package, rename_extant=True)
            return rename(package_name)

        # If the symbol's group is a non-priority group from FORCE_PREFIX_GROUPS,
        # add it as a prefix to disambiguate the symbols.
        if group_name in FORCE_PREFIX_GROUPS:
            if item.group in FORCE_PREFIX_GROUPS:
                needs_moving = FORCE_PREFIX_GROUPS.index(group_name) < FORCE_PREFIX_GROUPS.index(item.group)
            else:
                needs_moving = False
            return rename(item.group if needs_moving else group_name, rename_extant=needs_moving)

        # If the above conditions didn't pass, either the existing symbol has its group in FORCE_PREFIX_GROUPS,
        # or deciding which item to rename would be arbitrary, so we rename the existing symbol.
        return rename(item.group, rename_extant=True)

    async def refresh_inventories(self) -> None:
        """Refresh internal documentation inventories."""
        self.refresh_event.clear()
        await self.symbol_get_event.wait()
        log.debug("Refreshing documentation inventory...")
        self.inventory_scheduler.cancel_all()

        self.base_urls.clear()
        self.doc_symbols.clear()
        self.renamed_symbols.clear()
        await self.item_fetcher.clear()

        coros = [
            self.update_or_reschedule_inventory(
                package["package"], package["base_url"], package["inventory_url"]
            ) for package in self.bot.data_storage.get("documentation_links", [])
        ]
        await asyncio.gather(*coros)
        log.debug("Finished inventory refresh.")
        self.refresh_event.set()

    def get_symbol_item(self, symbol_name: str, limit: int = 1) -> tuple[str, list[DocItem] | DocItem | None]:
        """
        Get the `DocItem` and the symbol name used to fetch it from the `doc_symbols` dict.

        If the doc item is not found directly from the passed in name and the name contains a space,
        the first word of the name will be attempted to be used to get the item.
        """
        result = fuzzy.finder(symbol_name, self.doc_symbols.items(), key=lambda x: x[0])[:limit]
        if limit == 1:
            result = result[0][1] if result else None
        return symbol_name, result if result else None

    async def get_symbol_markdown(self, doc_item: DocItem) -> str:
        """
        Get the Markdown from the symbol `doc_item` refers to.

        First a redis lookup is attempted, if that fails the `item_fetcher`
        is used to fetch the page and parse the HTML from it into Markdown.
        """
        markdown = await doc_cache.get(doc_item)

        if markdown is None:
            log.debug(f"Redis cache miss with {doc_item}.")
            try:
                markdown = await self.item_fetcher.get_markdown(doc_item)
            except aiohttp.ClientError as e:
                log.warning(f"A network error has occurred when requesting parsing of {doc_item}.", exc_info=e)
                return "Unable to parse the requested symbol due to a network error."
            except Exception:
                log.exception(f"An unexpected error has occurred when requesting parsing of {doc_item}.")
                return "Unable to parse the requested symbol due to an error."

            if markdown is None:
                return "Unable to parse the requested symbol."
        return markdown

    async def create_symbol_embed(self, symbol_name: str) -> discord.Embed | None:
        """
        Attempt to scrape and fetch the data for the given `symbol_name`, and build an embed from its contents.

        If the symbol is known, an Embed with documentation about it is returned.

        First check the DocRedisCache before querying the cog's `BatchParser`.
        """
        log.trace(f"Building embed for symbol `{symbol_name}`")
        if not self.refresh_event.is_set():
            log.debug("Waiting for inventories to be refreshed before processing item.")
            await self.refresh_event.wait()

        with self.symbol_get_event:
            symbol_name, doc_item = self.get_symbol_item(symbol_name)
            if doc_item is None:
                log.debug("Symbol does not exist.")
                return None

            if symbol_name in self.renamed_symbols:
                renamed_symbols = ", ".join(self.renamed_symbols[symbol_name])
                footer_text = textwrap.shorten(f"Similar names: {renamed_symbols}", 200, placeholder="...")
            else:
                footer_text = ""

            embed = discord.Embed(
                title=discord.utils.escape_markdown(doc_item.symbol_id),
                url=f"{doc_item.url}#{doc_item.symbol_id}",
                description=await self.get_symbol_markdown(doc_item)
            )
            embed.set_author(name=f"{doc_item.package} Documentation",
                             icon_url="https://cdn.discordapp.com/emojis/1070680561854709840.webp?size=96&quality=lossless")

            for name, value in doc_item.resolved_fields.items():
                embed.add_field(name=name, value=value, inline=False)

            embed.set_footer(text=footer_text)
            return embed

    @command(commands.hybrid_group, name="docs", aliases=("doc", "d"), invoke_without_command=True,
             description="Look up documentation for Python symbols.")
    async def docs_group(self, ctx: Context, *, symbol_name: Optional[str] = None) -> None:
        """Look up documentation for Python symbols."""
        await self.get_command(ctx, symbol_name=symbol_name)

    @command(docs_group.command, name="getdoc", aliases=("g",), description="Look up documentation for Python symbols.")
    @app_commands.describe(symbol_name="The symbol to look up documentation for.")
    @app_commands.autocomplete(symbol_name=documentation_autocomplete)  # type: ignore
    async def get_command(self, ctx: Context, *, symbol_name: Optional[str] = None) -> None:
        """Return a documentation embed for a given symbol.

        If no symbol is given, return a list of all available inventories.
        """
        if not symbol_name:
            if self.base_urls:
                await LinePaginator.start(ctx, entries=[(k, v) for k, v in self.base_urls.items()])  # type: ignore
            else:
                await ctx.send(f"{ctx.tick(False)} There are no inventories available at the moment.")

        else:
            symbol = symbol_name.strip("`")
            async with ctx.typing():
                doc_embed = await self.create_symbol_embed(symbol)

            if doc_embed is None:
                await ctx.send(f"{ctx.tick(False)} The symbol `{symbol}` was not found.")

            else:
                await ctx.send(embed=doc_embed)

    @staticmethod
    def base_url_from_inventory_url(inventory_url: str) -> str:
        """Get a base url from the url to an objects inventory by removing the last path segment."""
        return inventory_url.removesuffix("/").rsplit("/", maxsplit=1)[0] + "/"

    @command(docs_group.command, name="setdoc", aliases=("s",), hidden=True,
             description="Set a new documentation object.", with_app_command=False)
    @commands.is_owner()
    @lock('doc', COMMAND_LOCK_SINGLETON, raise_error=True)
    async def set_command(
            self,
            ctx: Context,
            package_name: Annotated[str, PackageName],
            inventory: Annotated[str, Inventory],
            base_url: Annotated[str, ValidURL] = "",
    ) -> None:
        """
        Adds a new documentation metadata object to the site's database.

        The database will update the object, should an existing item with the specified `package_name` already exist.
        If the base url is not specified, a default created by removing the last segment of the inventory url is used.
        """
        if base_url and not base_url.endswith("/"):
            raise commands.BadArgument(f"{ctx.tick(False)} The base url must end with a slash.")
        
        inventory_url, inventory_dict = inventory
        body = {
            "package": package_name,
            "base_url": base_url,
            "inventory_url": inventory_url
        }
        data: list[dict[str, Any]] = self.bot.data_storage.get("documentation_links", [])
        data.append(body)
        await self.bot.data_storage.put("documentation_links", data)

        log.info(
            f"User @{ctx.author} ({ctx.author.id}) added a new documentation package:\n"
            + "\n".join(f"{key}: {value}" for key, value in body.items())
        )

        if not base_url:
            base_url = self.base_url_from_inventory_url(inventory_url)
        self.update_single(package_name, base_url, inventory_dict)
        await ctx.send(f"{ctx.tick(True)} Added the package `{package_name}` to the database and updated the inventories.")

    @command(docs_group.command, name="deletedoc", hidden=True, aliases=("removedoc", "rm", "d"),
             description="Delete a documentation object.", with_app_command=False)
    @commands.is_owner()
    @lock('doc', COMMAND_LOCK_SINGLETON, raise_error=True)
    async def delete_command(self, ctx: Context, package_name: Annotated[str, PackageName]) -> None:
        """
        Removes the specified package from the database.

        Example:
            !docs deletedoc aiohttp
        """
        await self.bot.data_storage.remove_from_deep(f"documentation_links.{package_name}")

        async with ctx.typing():
            await self.refresh_inventories()
            await doc_cache.delete(package_name)
        await ctx.send(f"{ctx.tick(True)} Successfully deleted `{package_name}` and refreshed the inventories.")

    @command(docs_group.command, name="refreshdoc", aliases=("rfsh", "r"), hidden=True,
             description="Refresh the inventories.", with_app_command=False)
    @commands.is_owner()
    @lock('doc', COMMAND_LOCK_SINGLETON, raise_error=True)
    async def refresh_command(self, ctx: Context) -> None:
        """Refresh inventories and show the difference."""
        old_inventories = set(self.base_urls)
        async with ctx.typing():
            await self.refresh_inventories()
        new_inventories = set(self.base_urls)

        if added := ", ".join(new_inventories - old_inventories):
            added = "+ " + added

        if removed := ", ".join(old_inventories - new_inventories):
            removed = "- " + removed

        embed = discord.Embed(
            title="Inventories refreshed",
            description=f"```diff\n{added}\n{removed}```" if added or removed else ""
        )
        await ctx.send(embed=embed)

    @command(docs_group.command, name="cleardoccache", aliases=("deletedoccache",),
             description="Clear the cache for a package.", hidden=True, with_app_command=False)
    @commands.is_owner()
    async def clear_cache_command(
            self,
            ctx: Context,
            package_name: Annotated[str, PackageName] | Literal["*"]
    ) -> None:
        """Clear the persistent redis cache for `package`."""
        if await doc_cache.delete(package_name):
            await self.item_fetcher.remove(package_name)
            await ctx.send(f"{ctx.tick(True)} Successfully cleared the cache for `{package_name}`.")
        else:
            await ctx.send(f"{ctx.tick(False)} No keys matching the package found.")

    @staticmethod
    def parse_object_inv(stream: SphinxObjectFileReader, url: str) -> dict[str, str]:
        result: dict[str, str] = {}

        inv_version = stream.readline().rstrip()

        if inv_version != '# Sphinx inventory version 2':
            raise RuntimeError('Invalid objects.inv file version.')

        projname = stream.readline().rstrip()[11:]
        version = stream.readline().rstrip()[11:]  # noqa

        line = stream.readline()
        if 'zlib' not in line:
            raise RuntimeError('Invalid objects.inv file, not z-lib compatible.')

        entry_regex = re.compile(r'(?x)(.+?)\s+(\S*:\S*)\s+(-?\d+)\s+(\S+)\s+(.*)')
        for line in stream.read_compressed_lines():
            match = entry_regex.match(line.rstrip())
            if not match:
                continue

            name, directive, prio, location, dispname = match.groups()
            domain, _, subdirective = directive.partition(':')
            if directive == 'py:module' and name in result:
                continue

            if directive == 'std:doc':
                subdirective = 'label'

            if location.endswith('$'):
                location = location[:-1] + name

            key = name if dispname == '-' else dispname
            prefix = f'{subdirective}:' if domain == 'std' else ''

            if projname == 'discord.py':
                key = key.replace('discord.ext.commands.', '').replace('discord.', '')

            result[f'{prefix}{key}'] = os.path.join(url, location)

        return result

    @command(aliases=['rtfd'])
    @app_commands.describe(entity='The object to search for')
    @app_commands.autocomplete(entity=documentation_autocomplete)  # type: ignore
    async def rtfm(self, ctx: Context, *, entity: str):
        """Gives you a documentation link for a discord.py entity.

        Events, objects, and functions are all supported through
        a cruddy fuzzy algorithm.
        """
        await self.do_rtfm(ctx, entity)

    async def do_rtfm(self, ctx: Context, obj: str):
        _, matches = self.get_symbol_item(obj, 8)

        e = discord.Embed(colour=helpers.Colour.darker_red())
        if len(matches) == 0:
            return await ctx.send('Could not find anything. Sorry.')

        e.description = '\n'.join(f'**{doc_item.group}** [`{doc_item.symbol_id}`]({doc_item.url}) *({doc_item.package})*' for _, doc_item in matches)
        await ctx.send(embed=e, reference=ctx.replied_reference)
