from __future__ import annotations

import asyncio
import contextlib
import sys
from ssl import CertificateError
from types import SimpleNamespace
from typing import Literal, Any, Annotated, Optional, List, Generic, TypeVar, Type

import aiohttp
import discord
from aiohttp import ClientConnectorError
from discord import app_commands
from discord.utils import MISSING

from bot import Percy
from launcher import get_logger
from cogs.doc import PRIORITY_PACKAGES, _batch_parser, doc_cache, _inventory_parser
from ._inventory_parser import InvalidHeaderError, InventoryDict, fetch_inventory

from ..utils import helpers, fuzzy, commands
from ..utils.tasks import Scheduler, executor
from ..utils.constants import PACKAGE_NAME_RE
from ..utils.context import Context
from ..utils.formats import plural
from ..utils.lock import lock, SharedEvent, lock_func, LockedResourceError
from ..utils.paginator import LinePaginator

log = get_logger(__name__)

FORCE_PREFIX_GROUPS = (
    'term',
    'label',
    'token',
    'doc',
    'pdbcommand',
    '2to3fixer',
)

FETCH_RESCHEDULE_DELAY = SimpleNamespace(first=2, repeated=5)


class PackageName(commands.Converter):
    """
    A converter that checks whether the given string is a valid package name.

    Package names are used for stats and are restricted to the a-z and _ characters.
    """

    def __init__(self, available: bool = False):
        self.available = available

    async def convert(self, ctx: Context, argument: str) -> str:
        """Checks whether the given string is a valid package name."""

        if self.available:
            cog: Documentation = ctx.bot.get_cog('Documentation')  # type: ignore
            if argument not in cog.doc_symbols:
                if cog.base_urls:
                    embed = discord.Embed(color=helpers.Colour.white())
                    embed.set_footer(text=f'{plural(len(cog.base_urls)):inventory|invetories} found.')
                    results = [f'• [`{entry[0]}`]({entry[1]})' for entry in [(k, v) for k, v in cog.base_urls.items()]]
                    await LinePaginator.start(ctx, entries=results, per_page=15, embed=embed)
                    raise commands.BadArgument(f'The package `{argument}` is not available.')
                else:
                    raise commands.BadArgument(f'There are no inventories available at the moment.')

        if PACKAGE_NAME_RE.search(argument):
            raise commands.BadArgument(
                'The provided package name is not valid; please only use the `.`, `_`, `0-9`, and `a-zA-Z` characters.')
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
                    raise commands.BadArgument(f'HTTP GET on `{url}` returned status `{resp.status}`, expected 200')
        except CertificateError:
            if url.startswith('https'):
                raise commands.BadArgument(f'Got a `CertificateError` for URL `{url}`. Does it support HTTPS?')
            raise commands.BadArgument(f'Got a `CertificateError` for URL `{url}`.')
        except ValueError:
            raise commands.BadArgument(f'`{url}` doesn\'t look like a valid hostname to me.')
        except ClientConnectorError:
            raise commands.BadArgument(f'Cannot connect to host with URL `{url}`.')
        return url


class Inventory(commands.Converter):
    """Represents an Intersphinx inventory URL.

    This converter checks whether intersphinx accepts the given inventory URL, and raises
    `BadArgument` if that is not the case or if the url is unreachable.

    Otherwise, it returns the url and the fetched inventory dict in a tuple.
    """

    async def convert(self, ctx: Context, url: str) -> tuple[str, _inventory_parser.InventoryDict]:
        """Convert url to Intersphinx inventory URL."""
        await ctx.typing()

        if not url.endswith('/objects.inv'):
            url = url.rstrip('/') + '/objects.inv'

        try:
            inventory = await _inventory_parser.fetch_inventory(ctx.bot.session, url)
        except _inventory_parser.InvalidHeaderError:
            raise commands.BadArgument(
                f'{ctx.tick(False)} Unable to parse inventory because of invalid header, check if URL is correct.')
        else:
            if inventory is None:
                raise commands.BadArgument(
                    f'Failed to fetch inventory file after `{_inventory_parser.FAILED_REQUEST_ATTEMPTS}` attempts.')
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
        return f'{self.package}.{self.symbol_id}'

    @property
    def url(self) -> str:
        """Return the absolute url to the symbol."""
        return self.base_url + self.relative_url_path


T = TypeVar('T', bound=DocItem)


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
                'This message is not for you!', ephemeral=True
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
            raise RuntimeError('Critical, Invalid index passed to `format_page`.')

        if item.embed is not None:
            # already formatted?
            return item

        embed = await self.cog.create_symbol_embed(item)
        item.embed = embed

        try:
            # update the embed in the doc_symbols cache
            # we set the embed permanently, so we don't have to re-create it next time
            origin = self.cog.doc_symbols[item.package][item.symbol_id]
        except KeyError:
            pass
        else:
            origin.embed = embed
            self.cog.doc_symbols[item.package][item.symbol_id] = origin

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
        self.bot: Percy = bot

        self.base_urls: dict[str, Any] = {}
        self.doc_symbols: dict[str, dict[str, DocItem]] = {}
        self.item_fetcher = _batch_parser.BatchParser(bot)

        self.inventory_scheduler = Scheduler(self.__class__.__name__)
        self.symbol_get_event: SharedEvent = SharedEvent()

        self.inv_retries: dict[str, int] = {}

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='\N{OPEN BOOK}')

    async def reset_cache(self) -> None:
        """Reset the internal cache of the cog."""
        await self.symbol_get_event.wait()
        self.inventory_scheduler.cancel_all()

        self.base_urls.clear()
        self.doc_symbols.clear()
        await self.item_fetcher.clear()

    async def cog_load(self) -> None:
        """Refresh documentation inventory on cog initialization."""
        self.bot.loop.create_task(self.refresh_inventories())  # noqa

    async def cog_unload(self) -> None:
        """Clear scheduled inventories, queued symbols and cleanup task on cog unload."""
        self.inventory_scheduler.cancel_all()
        await self.item_fetcher.clear()

    async def documentation_autocomplete(
            self, interaction: discord.Interaction, current: str  # noqa
    ) -> list[app_commands.Choice[str]]:
        if not current:
            return []

        package_name = interaction.namespace.package or interaction.namespace.package_name
        if not package_name:
            package_name = 'python'

        _, matches = await self.get_symbol_item(package_name, current, 15)  # noqa
        return [app_commands.Choice(name=m.symbol_id, value=m.symbol_id) for m in matches]

    async def package_autocomplete(
            self, interaction: discord.Interaction, current: str  # noqa
    ):
        return [app_commands.Choice(name=package, value=package)
                for package in fuzzy.finder(current, self.base_urls.keys())][:25]

    def update_single(self, package_name: str, base_url: str, inventory: InventoryDict) -> None:
        """Build the inventory for a single package.

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
        self.doc_symbols.setdefault(package_name, {})

        for group, items in inventory.items():
            for symbol_name, relative_doc_url in items:
                group_name = group.split(':')[1]
                symbol_name = self.ensure_unique_symbol_name(
                    package_name,
                    group_name,
                    symbol_name,
                )

                relative_url_path, _, symbol_id = relative_doc_url.partition('#')
                doc_item = DocItem(
                    package_name,
                    sys.intern(group_name),
                    base_url,
                    sys.intern(relative_url_path),
                    symbol_id,
                )
                self.doc_symbols[package_name] |= {symbol_name: doc_item}
                self.item_fetcher.add_item(doc_item)

        log.trace(f'Fetched inventory for {package_name}.')

    async def update_or_reschedule_inventory(
            self,
            api_package_name: str,
            base_url: str,
            inventory_url: str,
    ) -> None:
        """|coro|

        Update the cog's inventories, or reschedule this method to execute again if the remote inventory is unreachable.

        Note
        ----
        The first attempt is rescheduled to execute in `FETCH_RESCHEDULE_DELAY.first` minutes, the subsequent attempts
        in `FETCH_RESCHEDULE_DELAY.repeated` minutes.

        Parameters
        ----------
        api_package_name: :class:`str`
            The name of the package.
        base_url: :class:`str`
            The base URL of the package.
        inventory_url: :class:`str`
            The URL of the package's inventory.
        """
        try:
            package = await fetch_inventory(self.bot.session, inventory_url)
        except InvalidHeaderError as e:
            log.warning(f'Invalid inventory header at {inventory_url}. Reason: {e}')
            return

        if not package:
            if api_package_name in self.inventory_scheduler:
                self.inv_retries[api_package_name] += 1
                self.inventory_scheduler.cancel(api_package_name)
                delay = FETCH_RESCHEDULE_DELAY.repeated
            else:
                self.inv_retries[api_package_name] = 0
                delay = FETCH_RESCHEDULE_DELAY.first

            if self.inv_retries[api_package_name] > 5:
                log.error(f'Failed to fetch inventory for {api_package_name} after 5 attempts.\n'
                          f'Refresh the inventory manually with `?docs refresh`.')
                return

            log.info(f'Failed to fetch inventory; attempting again in {delay} minutes.')
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
        """Ensure `symbol_name` doesn't overwrite a symbol in `doc_symbols`.

        For conflicts, rename either the current symbol or the existing symbol with which it conflicts.
        Store the new name in `renamed_symbols` and return the name to use for the symbol.

        If the existing symbol was renamed or there was no conflict, the returned name is equivalent to `symbol_name`.
        """
        if self.doc_symbols.get(package_name) is None:
            raise ValueError(f'Package `{package_name}` is somehow not supported.')

        if (item := self.doc_symbols[package_name].get(symbol_name)) is None:
            return symbol_name

        def _rename(prefix: str, *, rename_extant: bool = False) -> str:
            new_name = f'{prefix}.{symbol_name}'
            if new_name in self.doc_symbols[package_name]:
                if rename_extant:
                    new_name = f'{item.package}.{item.group}.{symbol_name}'
                else:
                    new_name = f'{package_name}.{group_name}.{symbol_name}'

            if rename_extant:
                self.doc_symbols[package_name][new_name] = self.doc_symbols[package_name][symbol_name]
                return symbol_name
            return new_name

        if package_name != item.package:
            if package_name in PRIORITY_PACKAGES:
                return _rename(item.package, rename_extant=True)
            return _rename(package_name)

        if group_name in FORCE_PREFIX_GROUPS:
            if item.group in FORCE_PREFIX_GROUPS:
                needs_moving = FORCE_PREFIX_GROUPS.index(group_name) < FORCE_PREFIX_GROUPS.index(item.group)
            else:
                needs_moving = False
            return _rename(item.group if needs_moving else group_name, rename_extant=needs_moving)

        return _rename(item.group, rename_extant=True)

    @lock('DocCache.refresh', 'inventory refresh task', wait=True, raise_error=True)
    async def refresh_inventories(self) -> None:
        """Refresh internal documentation inventories."""
        log.debug('Refreshing documentation inventory...')
        # Cleanup
        await self.reset_cache()

        coros = [
            self.update_or_reschedule_inventory(
                package['package'], package['base_url'], package['inventory_url']
            ) for package in self.bot.data_storage.get('documentation_links', [])
        ]
        await asyncio.gather(*coros)
        log.debug('Finished inventory refresh.')

    @executor
    def get_symbol_item(
            self, package_name: str, symbol_name: str, limit: int = 1
    ) -> tuple[str, list[DocItem] | DocItem | None]:
        """Get the :class:`DocItem` and the symbol name used to fetch it from the `doc_symbols` dict.

        If the doc item is not found directly from the passed in name and the name contains a space,
        the first word of the name will be attempted to be used to get the item.
        """

        results: list[tuple[str, DocItem]] = []

        try:
            # do this for faster lookup
            match = (symbol_name, self.doc_symbols[package_name][symbol_name])
        except KeyError:
            results.extend(
                fuzzy.finder(symbol_name, self.doc_symbols[package_name].items(), key=lambda x: x[0])[:limit]
            )
        else:
            results.append(match)
            results.extend(
                filter(
                    lambda x: x[0] != symbol_name,
                    fuzzy.finder(symbol_name, self.doc_symbols[package_name].items(), key=lambda x: x[0])[:limit]
                )
            )

        if not results:
            return symbol_name, None

        return symbol_name, [result[1] for result in results]

    async def get_symbol_markdown(self, doc_item: DocItem) -> str:
        """Get the Markdown from the symbol `doc_item` refers to.

        First a redis lookup is attempted, if that fails the `item_fetcher`
        is used to fetch the page and parse the HTML from it into Markdown.
        """
        markdown = await doc_cache.get(doc_item)

        if markdown is None:
            log.debug(f'Doc cache miss with {doc_item}.')
            try:
                markdown = await self.item_fetcher.get_markdown(doc_item)
            except aiohttp.ClientError as e:
                log.warning(f'A network error has occurred when requesting parsing of {doc_item}.', exc_info=e)
                return 'Unable to parse the requested symbol due to a network error.'
            except Exception:  # noqa
                log.exception(f'An unexpected error has occurred when requesting parsing of {doc_item}.')
                return 'Unable to parse the requested symbol due to an error.'
            else:
                if markdown is None:
                    return 'Unable to parse the requested symbol.'
                return markdown

    @lock_func(refresh_inventories, wait=True)
    async def create_symbol_embed(self, item: DocItem) -> discord.Embed | None:
        """Attempt to scrape and fetch the data for the given `symbol_name`, and build an embed from its contents.

        If the symbol is known, an Embed with documentation about it is returned.

        First check the DocRedisCache before querying the cog's `BatchParser`.
        """
        with self.symbol_get_event:
            if item is None:
                log.debug('Symbol does not exist.')
                return None

            embed = discord.Embed(
                title=discord.utils.escape_markdown(item.symbol_id),
                url=f'{item.url}#{item.symbol_id}',
                description=await self.get_symbol_markdown(item)
            )
            embed.set_author(
                name=f'{item.package} Documentation', icon_url='https://images.klappstuhl.me/gallery/UYzvwImyRS.png')

            for name, value in item.resolved_fields.items():
                embed.add_field(name=name, value=value, inline=False)
            return embed

    @commands.command(
        commands.hybrid_group,
        name='docs',
        fallback='search',
        aliases=['d'],
        description='Look up documentation for Python symbols.',
        invoke_without_command=True
    )
    @app_commands.describe(
        symbol_name='The symbol to look up documentation for.', package='The package to look up documentation for.')
    @app_commands.autocomplete(symbol_name=documentation_autocomplete, package=package_autocomplete)
    @lock_func(refresh_inventories, raise_error=True)
    async def docs_group(
            self,
            ctx: Context,
            package: Annotated[str, PackageName(available=True)],  # type: ignore
            *,
            symbol_name: str
    ):
        """Return a documentation embed for a given symbol.

        If no symbol is given, return a list of all available inventories.
        """

        symbol = symbol_name.strip('`')
        async with ctx.typing():
            _, doc_items = await self.get_symbol_item(package, symbol, limit=12)  # type: str, List[DocItem]  # noqa

            if not doc_items:
                return await ctx.send(f'{ctx.tick(False)} The symbol `{symbol_name}` was not found.')

        await DocView.start(ctx, cog=self, items=doc_items)

    @staticmethod
    def base_url_from_inventory_url(inventory_url: str) -> str:
        """Get a base url from the url to an objects inventory by removing the last path segment."""
        return inventory_url.removesuffix('/').rsplit('/', maxsplit=1)[0] + '/'

    @commands.command(
        docs_group.command,
        name='set',
        hidden=True,
        description='Set a new documentation object.',
        with_app_command=False
    )
    @commands.is_owner()
    @lock('Docs', 'set', raise_error=True)
    async def set_command(
            self,
            ctx: Context,
            package_name: Annotated[str, PackageName],
            inventory: Annotated[str, Inventory],
    ) -> None:
        """Adds a new documentation metadata object to the site's database.

        The database will update the object, should an existing item with the specified `package_name` already exist.
        If the base url is not specified, a default created by removing the last segment of the inventory url is used.
        """
        inventory_url, inventory_dict = inventory
        body = {
            'package': package_name,
            'base_url': self.base_url_from_inventory_url(inventory_url),
            'inventory_url': inventory_url
        }
        data: list[dict[str, Any]] = self.bot.data_storage.get('documentation_links', [])
        data.append(body)
        await self.bot.data_storage.put('documentation_links', data)

        log.info(
            f'User @{ctx.author} ({ctx.author.id}) added a new documentation package:\n'
            + '\n'.join(f'{key}: {value}' for key, value in body.items())
        )

        self.update_single(package_name, body['base_url'], inventory_dict)
        await ctx.send(
            f'{ctx.tick(True)} Added the package `{package_name}` to the database and updated the inventories.')

    @commands.command(
        docs_group.command,
        name='delete',
        hidden=True,
        aliases=['remove', 'rm'],
        description='Delete a documentation object.',
        with_app_command=False
    )
    @commands.is_owner()
    @lock('Docs', 'delete', raise_error=True)
    async def delete_command(
            self, ctx: Context, package_name: Annotated[str, PackageName(available=True)]  # type: ignore
    ) -> None:
        """Removes the specified package from the database."""
        await self.bot.data_storage.remove_from_deep(f'documentation_links.{package_name}')

        async with ctx.typing():
            await self.refresh_inventories()
            await doc_cache.delete(package_name)
        await ctx.send(f'{ctx.tick(True)} Successfully deleted `{package_name}` and refreshed the inventories.')

    @commands.command(
        docs_group.command,
        name='refresh',
        aliases=['rfsh', 'r'],
        hidden=True,
        description='Refresh the inventories.',
        with_app_command=False
    )
    @commands.is_owner()
    @lock('Docs', 'refresh', raise_error=True)
    async def refresh_command(self, ctx: Context) -> None:
        """Refresh inventories and show the difference."""
        old_inventories = set(self.base_urls)
        async with ctx.typing():
            await self.refresh_inventories()

        new_inventories = set(self.base_urls)

        if added := ', '.join(new_inventories - old_inventories):
            added = '+ ' + added

        if removed := ', '.join(old_inventories - new_inventories):
            removed = '- ' + removed

        embed = discord.Embed(
            title='Inventories refreshed',
            description=f'```diff\n{added}\n{removed}```' if added or removed else ""
        )
        await ctx.send(embed=embed)

    @commands.command(
        docs_group.command,
        name='clearcache',
        aliases=['deletecache', 'cc'],
        description='Clear the cache for a package.',
        hidden=True,
        with_app_command=False
    )
    @commands.is_owner()
    async def clear_cache_command(
            self,
            ctx: Context,
            package_name: Annotated[str, PackageName] | Literal['*']
    ) -> None:
        """Clear the persistent redis cache for `package`."""
        if await doc_cache.delete(package_name):
            await self.item_fetcher.remove(package_name)
            await ctx.send(f'{ctx.tick(True)} Successfully cleared the cache for `{package_name}`.')
        else:
            await ctx.send(f'{ctx.tick(False)} No keys matching the package found.')

    @commands.command(
        aliases=['rtfd'],
        description='Searches some documentations for the given query. (Short)'
    )
    @app_commands.describe(
        symbol_name='The object to search for', package='The package to search in.')
    @app_commands.autocomplete(symbol_name=documentation_autocomplete, package=package_autocomplete)
    @lock_func(refresh_inventories, raise_error=True)
    async def rtfm(
            self,
            ctx: Context,
            package: Annotated[str, PackageName(available=True)],  # type: ignore
            *,
            symbol_name: str
    ):
        """Gives you a documentation link for a commands.py entity.

        Events, objects, and functions are all supported through
        a cruddy fuzzy algorithm.
        """
        _, matches = await self.get_symbol_item(package, symbol_name, 8)  # noqa

        if len(matches) == 0:
            return await ctx.send(f'{ctx.tick(False)} The symbol `{symbol_name}` was not found.')

        e = discord.Embed(title=f'{package} Search', colour=helpers.Colour.white())
        e.description = '\n'.join(
            f'**{doc_item.group}** [`{doc_item.symbol_id}`]({doc_item.url})'
            for doc_item in matches)
        await ctx.send(embed=e, reference=ctx.replied_reference)
