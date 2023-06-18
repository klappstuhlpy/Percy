from __future__ import annotations

import asyncio
import sys
import textwrap
from collections import defaultdict
from ssl import CertificateError
from types import SimpleNamespace
from typing import Literal, Any, Annotated, Optional, List, Generic, TypeVar, Type

import aiohttp
import discord
from aiohttp import ClientConnectorError
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING

from bot import Percy
from launcher import get_logger
from . import PRIORITY_PACKAGES, _batch_parser, doc_cache, _inventory_parser
from ._inventory_parser import InvalidHeaderError, InventoryDict, fetch_inventory
from .. import command
from ..utils import helpers, fuzzy
from ..utils.tasks import Scheduler, executor
from ..utils.constants import PACKAGE_NAME_RE
from ..utils.context import Context
from ..utils.formats import plural
from ..utils.lock import lock, SharedEvent
from ..utils.paginator import LinePaginator

log = get_logger(__name__)

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
                "The provided package name is not valid; please only use the `.`, `_`, `0-9`, and `a-zA-Z` characters.")
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

        if not url.endswith("/objects.inv"):
            url = url.rstrip("/") + "/objects.inv"

        try:
            inventory = await _inventory_parser.fetch_inventory(ctx.bot.session, url)
        except _inventory_parser.InvalidHeaderError:
            raise commands.BadArgument(f"{ctx.tick(False)} Unable to parse inventory because of invalid header, check if URL is correct.")
        else:
            if inventory is None:
                raise commands.BadArgument(
                    f"Failed to fetch inventory file after `{_inventory_parser.FAILED_REQUEST_ATTEMPTS}` attempts."
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
            embed: discord.Embed = None,
    ):
        self.package: str = package
        self.group: str = group
        self.base_url: str = base_url
        self.relative_url_path: str = relative_url_path
        self.symbol_id: str = symbol_id

        self.resolved_fields: dict[str, Any] = resolved_fields or {}
        self.embed: discord.Embed = embed

    def __str__(self):
        return f"{self.package}.{self.symbol_id}"

    @property
    def url(self) -> str:
        """Return the absolute url to the symbol."""
        return self.base_url + self.relative_url_path


T = TypeVar("T", bound=DocItem)


class DocSelect(discord.ui.Select):
    def __init__(self, parent: DocView):
        self.parent = parent
        super().__init__(
            placeholder='Select a similar Documentation...',
            max_values=1,
            row=1,
        )
        self.__fill_options()

    def __fill_options(self) -> None:
        for item in self.parent.items:  # type: DocItem
            self.add_option(
                label=item.symbol_id,
                description=item.group,
                value=str(self.parent.items.index(item)),
            )

    async def callback(self, interaction: discord.Interaction):
        assert self.parent is not None
        self.parent._current = await self.parent.format_page(int(self.values[0]))
        await self.parent.update(interaction, embed=self.parent._current.embed)  # noqa


class DocView(discord.ui.View, Generic[T]):
    """A View that represents a documentation page for a specific object.

    Parameters
    ----------
    cog: Documentation
        The cog that created this view.
    items: list[DocItem]
        The list of items to display.
    timeout: int
        The timeout for the view.
    """

    def __init__(
            self,
            *,
            cog: Documentation,
            items: list[DocItem],
            timeout: int = 450,
    ):
        super().__init__(timeout=timeout)

        self.items: list[DocItem] = items
        self.cog: Documentation = cog

        self.ctx: Context | discord.Interaction = MISSING
        self.msg: discord.Message = MISSING

        self._current: DocItem = MISSING

    async def on_timeout(self) -> None:
        if self.msg is not MISSING:
            try:
                await self.msg.edit(view=None)
            except discord.HTTPException:
                pass

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message(
                "This message is not for you!", ephemeral=True
            )
            return False
        return True

    async def update(self, interaction: discord.Interaction | Context, **kwargs) -> None:
        if isinstance(interaction, Context):
            await self.msg.edit(**kwargs)
        elif isinstance(interaction, discord.Interaction):
            if interaction.response.is_done():
                await interaction.edit_original_response(**kwargs)
            else:
                await interaction.response.edit_message(**kwargs)

    async def send(self, ctx: discord.Interaction | Context, *args, **kwargs) -> Optional[discord.Message]:
        if isinstance(ctx, Context):
            self.msg = await ctx.send(*args, **kwargs)
        elif isinstance(ctx, discord.Interaction):
            if ctx.response.is_done():
                await ctx.edit_original_response(*args, **kwargs)
            else:
                await ctx.response.send_message(*args, **kwargs)

            self.msg = await ctx.original_response()
        return self.msg

    async def format_page(self, index: int) -> DocItem:
        """Format the page for the given item."""
        try:
            item = self.items[index]
        except IndexError:
            raise RuntimeError("Critical, Invalid index passed to `format_page`.")

        if item.embed is not None:
            # already formatted?
            return item

        embed = await self.cog.create_symbol_embed(item)
        item.embed = embed
        return item

    @classmethod
    async def start(
            cls: Type[DocView],
            context: Context | discord.Interaction,
            *,
            cog: Documentation,
            items: list[DocItem],
            timeout: int = 450,
    ) -> DocView[T]:
        """Initialize a new DocView.

        Parameters
        ----------
        context: Context | discord.Interaction
            The context or interaction that triggered this view.
        cog: Documentation
            The cog that created this view.
        items: list[DocItem]
            The list of items to display.
        timeout: int
            The timeout for the view.

        Returns
        -------
        DocView
            The initialized view.
        """
        self = cls(items=items, cog=cog, timeout=timeout)
        self.ctx = context

        self._current = await self.format_page(0)
        if len(self.items) > 1:
            self.add_item(DocSelect(self))

        self.msg = await self.send(context, view=self, embed=self._current.embed)
        return self


class Documentation(commands.Cog):
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

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="\N{OPEN BOOK}")

    async def documentation_autocomplete(
            self, interaction: discord.Interaction, current: str  # noqa
    ) -> list[app_commands.Choice[str]]:

        if not current:
            return []

        _, matches = await self.get_symbol_item(current, 15)
        return [app_commands.Choice(name=f"{m.symbol_id} ({m.package})", value=m.symbol_id) for m in matches]

    def update_single(self, package_name: str, base_url: str, inventory: InventoryDict) -> None:
        """
        Build the inventory for a single package.

        Parameters
        ----------
        package_name: :class:`str`
            The name of the package.
        base_url: :class:`str`
            The base URL of the package.
        inventory: :class:`dict`
            The inventory of the package.
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
        """Update the cog's inventories, or reschedule this method to execute again if the remote inventory is unreachable.

        The first attempt is rescheduled to execute in `FETCH_RESCHEDULE_DELAY.first` minutes, the subsequent attempts
        in `FETCH_RESCHEDULE_DELAY.repeated` minutes.
        """
        try:
            package = await fetch_inventory(self.bot.session, inventory_url)
        except InvalidHeaderError as e:
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
        """Ensure `symbol_name` doesn't overwrite an another symbol in `doc_symbols`.

        For conflicts, rename either the current symbol or the existing symbol with which it conflicts.
        Store the new name in `renamed_symbols` and return the name to use for the symbol.

        If the existing symbol was renamed or there was no conflict, the returned name is equivalent to `symbol_name`.
        """
        if (item := self.doc_symbols.get(symbol_name)) is None:
            return symbol_name

        def rename(prefix: str, *, rename_extant: bool = False) -> str:
            new_name = f"{prefix}.{symbol_name}"
            if new_name in self.doc_symbols:
                if rename_extant:
                    new_name = f"{item.package}.{item.group}.{symbol_name}"
                else:
                    new_name = f"{package_name}.{group_name}.{symbol_name}"

            self.renamed_symbols[symbol_name].append(new_name)

            if rename_extant:
                self.doc_symbols[new_name] = self.doc_symbols[symbol_name]
                return symbol_name
            return new_name

        if package_name != item.package:
            if package_name in PRIORITY_PACKAGES:
                return rename(item.package, rename_extant=True)
            return rename(package_name)

        if group_name in FORCE_PREFIX_GROUPS:
            if item.group in FORCE_PREFIX_GROUPS:
                needs_moving = FORCE_PREFIX_GROUPS.index(group_name) < FORCE_PREFIX_GROUPS.index(item.group)
            else:
                needs_moving = False
            return rename(item.group if needs_moving else group_name, rename_extant=needs_moving)

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

    @executor
    def get_symbol_item(self, symbol_name: str, limit: int = 1) -> tuple[str, list[DocItem] | DocItem | None]:
        """Get the :class:`DocItem` and the symbol name used to fetch it from the `doc_symbols` dict.

        If the doc item is not found directly from the passed in name and the name contains a space,
        the first word of the name will be attempted to be used to get the item.
        """
        result = fuzzy.finder(symbol_name, self.doc_symbols.items(), key=lambda x: x[0])[:limit]
        if limit == 1:
            result = result[0][1] if result else None
        return symbol_name, [r[1] for r in result] if result else None

    async def get_symbol_markdown(self, doc_item: DocItem) -> str:
        """
        Get the Markdown from the symbol `doc_item` refers to.

        First a redis lookup is attempted, if that fails the `item_fetcher`
        is used to fetch the page and parse the HTML from it into Markdown.
        """
        markdown = await doc_cache.get(doc_item)

        if markdown is None:
            log.debug(f"Doc cache miss with {doc_item}.")
            try:
                markdown = await self.item_fetcher.get_markdown(doc_item)
            except aiohttp.ClientError as e:
                log.warning(f"A network error has occurred when requesting parsing of {doc_item}.", exc_info=e)
                return "Unable to parse the requested symbol due to a network error."
            except Exception:  # noqa
                log.exception(f"An unexpected error has occurred when requesting parsing of {doc_item}.")
                return "Unable to parse the requested symbol due to an error."

            if markdown is None:
                return "Unable to parse the requested symbol."
            return markdown

    async def create_symbol_embed(self, item: DocItem) -> discord.Embed | None:
        """
        Attempt to scrape and fetch the data for the given `symbol_name`, and build an embed from its contents.

        If the symbol is known, an Embed with documentation about it is returned.

        First check the DocRedisCache before querying the cog's `BatchParser`.
        """
        log.trace(f"Building embed for symbol `{item.symbol_id}`.")
        if not self.refresh_event.is_set():
            log.debug("Waiting for inventories to be refreshed before processing item.")
            await self.refresh_event.wait()

        with self.symbol_get_event:
            if item is None:
                log.debug("Symbol does not exist.")
                return None

            embed = discord.Embed(
                title=discord.utils.escape_markdown(item.symbol_id),
                url=f"{item.url}#{item.symbol_id}",
                description=await self.get_symbol_markdown(item)
            )
            embed.set_author(name=f"{item.package} Documentation",
                             icon_url="https://cdn.discordapp.com/emojis/1070680561854709840.webp?size=96&quality=lossless")

            for name, value in item.resolved_fields.items():
                embed.add_field(name=name, value=value, inline=False)
            return embed

    @command(commands.hybrid_group, name="docs", fallback="search", aliases=["d"],
             description="Look up documentation for Python symbols.", invoke_without_command=True)
    @app_commands.describe(symbol_name="The symbol to look up documentation for.")
    @app_commands.autocomplete(symbol_name=documentation_autocomplete)  # type: ignore
    async def docs_group(self, ctx: Context, *, symbol_name: Optional[str] = None):
        """Return a documentation embed for a given symbol.

        If no symbol is given, return a list of all available inventories.
        """
        if not symbol_name:
            if self.base_urls:
                embed = discord.Embed(color=helpers.Colour.darker_red())
                embed.set_footer(text=f'{plural(len(self.base_urls)):inventory|invetories} found.')
                results = [f"• [`{entry[0]}`]({entry[1]})" for entry in [(k, v) for k, v in self.base_urls.items()]]
                await LinePaginator.start(ctx, entries=results, per_page=15, embed=embed)
            else:
                await ctx.send(f"{ctx.tick(False)} There are no inventories available at the moment.")

        else:
            symbol = symbol_name.strip("`")
            async with ctx.typing():
                _, doc_items = await self.get_symbol_item(symbol, limit=12)  # type: str, List[DocItem]

                if not doc_items:
                    return await ctx.send(f"{ctx.tick(False)} The symbol `{symbol_name}` was not found.")

            await DocView.start(ctx, cog=self, items=doc_items)

    @staticmethod
    def base_url_from_inventory_url(inventory_url: str) -> str:
        """Get a base url from the url to an objects inventory by removing the last path segment."""
        return inventory_url.removesuffix("/").rsplit("/", maxsplit=1)[0] + "/"

    @command(docs_group.command, name="set", hidden=True, description="Set a new documentation object.",
             with_app_command=False)
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

    @command(docs_group.command, name="delete", hidden=True, aliases=["remove", "rm"],
             description="Delete a documentation object.", with_app_command=False)
    @commands.is_owner()
    @lock('doc', COMMAND_LOCK_SINGLETON, raise_error=True)
    async def delete_command(self, ctx: Context, package_name: Annotated[str, PackageName]) -> None:
        """Removes the specified package from the database."""
        await self.bot.data_storage.remove_from_deep(f"documentation_links.{package_name}")

        async with ctx.typing():
            await self.refresh_inventories()
            await doc_cache.delete(package_name)
        await ctx.send(f"{ctx.tick(True)} Successfully deleted `{package_name}` and refreshed the inventories.")

    @command(docs_group.command, name="refresh", aliases=["rfsh", "r"], hidden=True,
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

    @command(docs_group.command, name="clearcache", aliases=["deletecache"],
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

    @command(aliases=['rtfd'], description='Searches some documentations for the given query. (Short)')
    @app_commands.describe(entity='The object to search for')
    @app_commands.autocomplete(entity=documentation_autocomplete)  # type: ignore
    async def rtfm(self, ctx: Context, *, entity: str):
        """Gives you a documentation link for a discord.py entity.

        Events, objects, and functions are all supported through
        a cruddy fuzzy algorithm.
        """
        _, matches = await self.get_symbol_item(entity, 8)

        e = discord.Embed(colour=helpers.Colour.darker_red())
        if len(matches) == 0:
            return await ctx.send('Could not find anything. Sorry.')

        e.description = '\n'.join(
            f'**{doc_item.group}** [`{doc_item.symbol_id}`]({doc_item.url}) *({doc_item.package})*'
            for doc_item in matches)
        await ctx.send(embed=e, reference=ctx.replied_reference)
