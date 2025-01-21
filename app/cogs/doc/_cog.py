from __future__ import annotations

import asyncio
import itertools
import logging
import re
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, Annotated, Any, ClassVar, Final, TypeVar

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from app.cogs.doc import PRIORITY_PACKAGES, _batch_parser, _inventory_parser, doc_cache
from app.core import Bot, Cog, Context
from app.core.models import command, describe, group
from app.utils import fuzzy, helpers, pluralize
from app.utils.lock import SharedEvent, lock, lock_from
from app.utils.pagination import BasePaginator, LinePaginator
from app.utils.tasks import Scheduler, executor
from config import Emojis

if TYPE_CHECKING:
    from app.cogs.doc._inventory_parser import InventoryDict

log = logging.getLogger(__name__)

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

    PACKAGE_NAME_RE: Final[ClassVar[re.Pattern]] = re.compile(r'[^a-zA-Z0-9_.]')

    def __init__(self, available: bool = False, maybe_all: bool = False) -> None:
        self.available = available
        self.maybe_all = maybe_all

    async def convert(self, ctx: Context, argument: str) -> str | None:
        """Checks whether the given string is a valid package name."""

        argument = argument.casefold()

        if self.maybe_all and argument == '*':
            return argument

        if self.available:
            cog: Documentation | None = ctx.bot.get_cog('Documentation')
            argument = cog.base_aliases.get(argument, argument)
            if argument not in cog.doc_symbols:
                if cog.base_urls:
                    embed = discord.Embed(color=helpers.Colour.white())
                    embed.set_footer(text=f'{pluralize(len(cog.base_urls)):inventory|invetories} found.')

                    def fmt(entry: tuple[str, str]) -> str:
                        first = f'• [`{entry[0]}`]({entry[1]})'
                        if entry[0] in cog.grouped_aliases:
                            return f'{first} ({', '.join(f'*`{alias}`*' for alias in cog.grouped_aliases[entry[0]])})'
                        return first

                    results = list(map(fmt, cog.base_urls.items()))
                    await LinePaginator.start(ctx, entries=results, per_page=15, embed=embed)
                    raise AssertionError(f'The package `{argument}` is not available.')
                else:
                    raise AssertionError('There are no inventories available at the moment.')

        if self.PACKAGE_NAME_RE.search(argument):
            raise commands.BadArgument(
                'The provided package name is not valid; please only use the `.`, `_`, `0-9`, and `a-zA-Z` characters.')
        return argument


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
                f'{Emojis.error} Unable to parse inventory because of invalid header, check if URL is correct.')
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
            resolved_fields: dict[str, str] | None = None,
            embed: discord.Embed = None,
    ) -> None:
        self.package: str = package
        self.group: str = group
        self.base_url: str = base_url
        self.relative_url_path: str = relative_url_path
        self.symbol_id: str = symbol_id
        self.embed: discord.Embed = embed

        self.resolved_fields: dict[str, Any] = resolved_fields or {}

    def __str__(self) -> str:
        return f'{self.package}.{self.symbol_id}'

    @property
    def url(self) -> str:
        """Return the absolute url to the symbol."""
        return self.base_url + self.relative_url_path


DocItemT = TypeVar('DocItemT', bound=DocItem | discord.Embed)


class DocSelect(discord.ui.Select):
    def __init__(self, parent: BasePaginator[DocItemT]) -> None:
        self.parent = parent
        super().__init__(
            placeholder='Select a similar Documentation...',
            max_values=1,
            row=1,
        )

        for item in self.parent.entries:
            self.add_option(
                label=item.symbol_id,
                description=item.group,
                value=str(self.parent.entries.index(item)),
            )

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        self.parent._current_page = int(self.values[0])
        entries = self.parent.switch_page(0)
        page = await self.parent.format_page(entries)
        await self.parent._edit(interaction, **self.parent.resolve_msg_kwargs(page))


class DocPaginator(BasePaginator[DocItemT]):
    """A View that represents a documentation page for a specific object."""

    async def format_page(self, entries: list[DocItem]) -> discord.Embed:
        """Format the page for the given item."""
        item = entries[0]

        if item.embed is not None:
            return item.embed

        cog: Documentation | None = self.extras.get('cog', None)
        if cog is None:
            raise ValueError('The cog was not passed to the paginator.')

        embed = await cog.create_symbol_embed(item)
        item.embed = embed

        try:
            # update the embed in the doc_symbols cache
            # we set the embed permanently, so we don't have to re-create it next time
            origin = cog.doc_symbols[item.package][item.symbol_id]
        except KeyError:
            pass
        else:
            origin.embed = embed
            cog.doc_symbols[item.package][item.symbol_id] = origin

        return embed

    @classmethod
    async def start(
            cls,
            context: Context | discord.Interaction,
            /,
            *,
            entries: list[DocItemT],
            per_page: int = 1,
            clamp_pages: bool = True,
            timeout: int = 450,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: Any
    ) -> BasePaginator[DocItemT]:
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context
        self.extras.update(kwargs)

        page = await self.format_page(self.pages[0])
        object_kwargs = self.resolve_msg_kwargs(page)

        # Dont need no pagination buttons
        self.clear_items()

        if len(self.entries) > 1:
            self.add_item(DocSelect(self))

        self.msg = await cls._send(context, ephemeral, **object_kwargs)
        return self


class Documentation(Cog):
    """A set of commands for querying & displaying documentation."""

    emoji = '\N{OPEN BOOK}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        self._base_aliases: dict[str, str] = {}
        self.grouped_aliases: dict[str, list[str]] = {}
        self.base_urls: dict[str, Any] = {}
        self.doc_symbols: dict[str, dict[str, DocItem]] = {}
        self.item_fetcher = _batch_parser.BatchParser(bot)

        self.inventory_scheduler = Scheduler('Documentation')
        self.symbol_get_event: SharedEvent = SharedEvent()

        self.inv_retries: dict[str, int] = {}

    @property
    def base_aliases(self) -> dict[str, str]:
        return self._base_aliases

    @base_aliases.setter
    def base_aliases(self, value: dict[str, str]) -> None:
        self._base_aliases = value

        sorted_aliases = sorted([(alias, package) for alias, package in value.items()], key=lambda x: x[1])
        self.grouped_aliases = {
            k: [alias for alias, _ in v] for k, v in itertools.groupby(sorted_aliases, key=lambda x: x[1])}

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
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        if not current:
            return []

        package_name = interaction.namespace.package or interaction.namespace.package_name
        if not package_name:
            package_name = 'python'

        _, matches = await self.get_symbol_item(package_name, current, 15)
        return [app_commands.Choice(name=m.symbol_id, value=m.symbol_id) for m in matches]

    async def package_autocomplete(self, _, current: str) -> list[app_commands.Choice[str]]:
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

        for dgroup, items in inventory.items():
            for symbol_name, relative_doc_url in items:
                group_name = dgroup.split(':')[1]
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

        log.debug('Fetched inventory for %s.', package_name)

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
            package = await _inventory_parser.fetch_inventory(self.bot.session, inventory_url)
        except _inventory_parser.InvalidHeaderError as e:
            log.warning('Invalid inventory header at %s. Reason: %s', inventory_url, e)
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
                log.error('Failed to fetch inventory for %s after 5 attempts.\n'
                          'Refresh the inventory manually with `?docs refresh`.', api_package_name)
                return

            log.info('Failed to fetch inventory; attempting again in %s minutes.', delay)
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
        await self.reset_cache()

        docs = self.bot.doc_links.all().items()
        self.base_aliases = {alias: package for package, value in docs for alias in value['aliases']}

        coros = [
            self.update_or_reschedule_inventory(
                package, value['base_url'], value['inventory_url']
            ) for package, value in docs
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
            log.debug('Doc cache miss with %s.', doc_item)
            try:
                markdown = await self.item_fetcher.get_markdown(doc_item)
            except aiohttp.ClientError as e:
                log.warning('A network error has occurred when requesting parsing of %s.', doc_item, exc_info=e)
                return 'Unable to parse the requested symbol due to a network error.'
            except Exception:
                log.exception('An unexpected error has occurred when requesting parsing of %s.', doc_item)
                return 'Unable to parse the requested symbol due to an error.'
            else:
                if markdown is None:
                    return 'Unable to parse the requested symbol.'
        return markdown

    @lock_from(refresh_inventories, wait=True)
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
                name=f'{item.package} Documentation', icon_url='https://klappstuhl.me/gallery/jUksiGZDtC.png')

            for name, value in item.resolved_fields.items():
                embed.add_field(name=name, value=value, inline=False)
            return embed

    @group(
        'docs',
        fallback='search',
        alias='d',
        description='Look up documentation for Python symbols.',
        hybrid=True
    )
    @describe(
        symbol_name='The symbol to look up documentation for.', package='The package to look up documentation for.')
    @app_commands.autocomplete(symbol_name=documentation_autocomplete, package=package_autocomplete)
    @lock_from(refresh_inventories, raise_error=True)
    async def docs(
            self,
            ctx: Context,
            package: Annotated[str, PackageName(available=True)],  # type: ignore
            *,
            symbol_name: str | None = None
    ) -> None:
        """Return a documentation embed for a given symbol.

        If no symbol is given, return a list of all available inventories.
        """
        if symbol_name is None:
            await ctx.send_error('Please provide a symbol to look up.')
            return

        symbol = symbol_name.strip('`')
        async with ctx.typing():
            _, doc_items = await self.get_symbol_item(package, symbol, limit=12)  # type: str, list[DocItem]

            if not doc_items:
                await ctx.send_error(f'The symbol `{symbol_name}` was not found.')
                return

        await DocPaginator.start(ctx, entries=doc_items, cog=self)

    @staticmethod
    def base_url_from_inventory_url(inventory_url: str) -> str:
        """Get a base url from the url to an objects inventory by removing the last path segment."""
        return inventory_url.removesuffix('/').rsplit('/', maxsplit=1)[0] + '/'

    @docs.command(
        'set',
        description='Set a new documentation object.',
        hidden=True,
        with_app_command=False
    )
    @describe(
        package_name='The name of the package to add.',
        inventory='The inventory URL for the package.'
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
            'base_url': self.base_url_from_inventory_url(inventory_url),
            'inventory_url': inventory_url,
            'aliases': [],
        }
        await self.bot.doc_links.put(package_name, body)

        _items = '\n'.join(f'{key}: {value}' for key, value in body.items())
        log.info('User @%s (ID: %s) added a new documentation package:\n%s', ctx.author, ctx.author.id, _items)

        self.update_single(package_name, body['base_url'], inventory_dict)
        await ctx.send_success(f'Added the package `{package_name}` to the database and updated the inventories.')

    # alias setting command
    @docs.command(
        'alias',
        description='Set an alias for a package.',
        hidden=True,
        with_app_command=False
    )
    @describe(
        package_name='The name of the package to set an alias for.',
        alias='The alias to set for the package.'
    )
    @commands.is_owner()
    @lock('Docs', 'alias', raise_error=True)
    async def alias_command(
            self, ctx: Context, package_name: Annotated[str, PackageName], alias: str
    ) -> None:
        """Set an alias for a package."""
        if not package_name:
            await ctx.send_error(f'The package `{package_name}` was not found.')
            return

        assert alias not in self.base_aliases, 'Alias already exists.'

        await self.bot.doc_links.add_deep(f'{package_name!r}.aliases', [alias])
        await ctx.send_success(f'Successfully set the alias `{alias}` for the package `{package_name}`.')

    @docs.command(
        'delete',
        aliases=['remove', 'rm'],
        description='Delete a documentation object.',
        hidden=True,
        with_app_command=False
    )
    @describe(package_name='The name of the package to remove.')
    @commands.is_owner()
    @lock('Docs', 'delete', raise_error=True)
    async def delete_command(
            self, ctx: Context, package_name: Annotated[str, PackageName(available=True)]  # type: ignore
    ) -> None:
        """Removes the specified package from the database."""
        await self.bot.doc_links.remove(package_name)

        async with ctx.typing():
            await self.refresh_inventories()
            await doc_cache.delete(package_name)
        await ctx.send_success(f'Successfully deleted `{package_name}` and refreshed the inventories.')

    @docs.command(
        name='refresh',
        aliases=['rfsh', 'r'],
        description='Refresh the inventories.',
        hidden=True,
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

    @docs.command(
        'clearcache',
        aliases=['deletecache', 'cc'],
        description='Clear the cache for a package.',
        hidden=True
    )
    @describe(package_name='The name of the package to clear the cache for.')
    @commands.is_owner()
    async def clear_cache_command(
            self,
            ctx: Context,
            package_name: Annotated[str, PackageName(maybe_all=True)]  # type: ignore
    ) -> None:
        """Clear the persistent redis cache for `package`."""
        if await doc_cache.delete(package_name):
            await self.item_fetcher.remove(package_name)
            await ctx.send_success(f'Successfully cleared the cache for `{package_name}`.')
        else:
            await ctx.send_error('No keys matching the package found.')

    @command(
        aliases=['rtfd'],
        description='Searches some documentations for the given query. (Short)',
        hybrid=True
    )
    @describe(
        symbol_name='The object to search for', package='The package to search in.')
    @app_commands.autocomplete(symbol_name=documentation_autocomplete, package=package_autocomplete)
    @lock_from(refresh_inventories, raise_error=True)
    async def rtfm(
            self,
            ctx: Context,
            package: Annotated[str, PackageName(available=True)],  # type: ignore
            *,
            symbol_name: str
    ) -> None:
        """Gives you a documentation link for a commands.py entity.

        Events, objects, and functions are all supported through
        a cruddy fuzzy algorithm.
        """
        _, matches = await self.get_symbol_item(package, symbol_name, 8)  # type: DocItem, list[DocItem]

        if len(matches) == 0:
            await ctx.send_error(f'The symbol `{symbol_name}` was not found.')
            return

        embed = discord.Embed(title=f'{package} Search', colour=helpers.Colour.white())
        embed.description = '\n'.join(
            f'**{doc_item.group}** [`{doc_item.symbol_id}`]({doc_item.url})'
            for doc_item in matches)
        await ctx.send(embed=embed, reference=ctx.replied_reference)
