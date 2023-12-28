from __future__ import annotations

import asyncio
import io
import re
import uuid
from typing import List, Optional, Any, TypeVar, Generic, Type, AnyStr, overload, Literal

import discord
from discord.ext import commands
from discord.utils import MISSING

from cogs.utils import fuzzy, helpers
from cogs.utils.context import Context
from cogs.utils.converters import aenumerate

T = TypeVar('T')

TYPE_MAPPING = {
    discord.Embed: 'embed',
    discord.File: 'file',
    str: 'content'
}


class TextSource:
    """A class that splits text into pages.

    Attributes
    -----------
    prefix: Optional[:class:`str`]
        The prefix inserted to every page. e.g. three backticks, if any.
    suffix: Optional[:class:`str`]
        The suffix appended at the end of every page. e.g. three backticks, if any.
    max_size: :class:`int`
        The maximum amount of codepoints allowed in a page.
    seperator: :class:`str`
        The character string inserted between lines. e.g. a newline character.
    """

    def __init__(
            self, prefix: Optional[str] = '```', suffix: Optional[str] = '```', max_size: int = 2000,
            seperator: str = '\n'
    ) -> None:
        self.prefix: Optional[str] = prefix
        self.suffix: Optional[str] = suffix
        self.max_size: int = max_size
        self.seperator: str = seperator

        if self.prefix is not None:
            self._current_page: List[str] = [self.prefix]
            self._count: int = len(self.prefix) + len(self.seperator)
        else:
            self._current_page = []
            self._count = 0

        self._pages: List[str] = []

    @property
    def prefix_len(self) -> int:
        """Returns the length of the prefix."""
        return len(self.prefix) if self.prefix is not None else 0

    @property
    def suffix_len(self) -> int:
        """Returns the length of the suffix."""
        return len(self.suffix) if self.suffix is not None else 0

    def add_line(self, line: str = '', *, empty: bool = False) -> None:
        """Adds a line to the current page.

        If the line exceeds the :attr:`max_size` then an exception
        is raised.

        Parameters
        -----------
        line: :class:`str`
            The line to add.
        empty: :class:`bool`
            Indicates if another empty line should be added.

        Raises
        ------
        RuntimeError
            The line was too big for the current :attr:`max_size`.
        """
        max_page_size = self.max_size - self.prefix_len - self.suffix_len - 2 * len(self.seperator)
        if len(line) > max_page_size:
            raise RuntimeError(f'Line exceeds maximum page size {max_page_size}')

        if self._count + len(line) + len(self.seperator) > self.max_size - self.suffix_len:
            self.close_page()

        self._count += len(line) + len(self.seperator)
        self._current_page.append(line)

        if empty:
            self._current_page.append('')
            self._count += len(self.seperator)

    def close_page(self) -> None:
        """Prematurely terminate a page."""
        if self.suffix is not None:
            self._current_page.append(self.suffix)
        self._pages.append(self.seperator.join(self._current_page))

        if self.prefix is not None:
            self._current_page = [self.prefix]
            self._count = len(self.prefix) + len(self.seperator)
        else:
            self._current_page = []
            self._count = 0

    def __len__(self) -> int:
        total = sum(len(p) for p in self._pages)
        return total + self._count

    @property
    def pages(self) -> List[str]:
        """List[:class:`str`]: Returns the rendered list of pages."""
        if len(self._current_page) > (0 if self.prefix is None else 1):
            current_page = self.seperator.join(
                [*self._current_page, self.suffix] if self.suffix is not None else self._current_page
            )
            return [*self._pages, current_page]

        return self._pages


class JumpToModal(discord.ui.Modal, title='Jump to'):
    """Modal that prompts users for the page number to change to"""
    page_number = discord.ui.TextInput(label='Page Index', style=discord.TextStyle.short)

    def __init__(self, paginator: BasePaginator):
        super().__init__(timeout=30)
        self.paginator: BasePaginator = paginator
        self.page_number.placeholder = f'Enter a Number between 1 and {self.paginator.total_pages}'
        self.page_number.min_length = 1
        self.page_number.max_length = len(str(self.paginator.total_pages))

    # noinspection PyProtectedMember
    async def on_submit(self, interaction: discord.Interaction, /):
        if not self.page_number.value.isdigit():
            return await interaction.response.send_message('Please enter a number.', ephemeral=True)
        if not 1 <= int(self.page_number.value) <= self.paginator.total_pages:
            return await interaction.response.send_message(
                f'Please enter a valid page number in range `1` to `{self.paginator.total_pages}`.', ephemeral=True)

        value = int(self.page_number.value) - 1
        count = value - self.paginator._current_page
        entries = self.paginator._switch_page(abs(count) if value > self.paginator._current_page else -abs(count))
        page = await self.paginator.format_page(entries)
        return await interaction.response.edit_message(**self.paginator._message_kwargs(page))


class SearchForModal(discord.ui.Modal, title='Search for Similarity'):
    """Modal that prompts users to search in all embeds for query similarities"""
    query = discord.ui.TextInput(label='Query', style=discord.TextStyle.short)

    def __init__(self, paginator: BasePaginator):
        super().__init__(timeout=60)
        self.paginator: BasePaginator = paginator
        self.query.min_length = 3

    async def on_submit(self, interaction: discord.Interaction, /):
        await self.paginator.search_for_query(self.query.value, interaction)
        self.stop()


class SearchForButton(discord.ui.Button):
    def __init__(self, paginator: BasePaginator):
        super().__init__(label='Search for …', emoji='\N{RIGHT-POINTING MAGNIFYING GLASS}',
                         style=discord.ButtonStyle.grey, row=1)
        self.paginator: BasePaginator = paginator

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.send_modal(SearchForModal(self.paginator))


class BasePaginator(discord.ui.View, Generic[T]):
    """
    The Base Button Paginator class. Will handle all page switching without
    you having to do anything.

    Attributes
    ----------
    entries: List[Any]
        A list of entries to get spread across pages.
    per_page: :class: `int`
        The number of entries that get passed onto one page.
    pages: List[List[Any]]
        A list of pages which contain all entries for that page.
    clamp_pages: :class:`bool`
        Whether to clamp the pages to the min and max.
    timeout: :class: `int`
        The timeout for the paginator.
    """

    def __init__(self, *, entries: List[T], per_page: int = 10, clamp_pages: bool = True,
                 timeout: int = 180) -> None:
        super().__init__(timeout=timeout)
        self.entries: List[T] = entries
        self.per_page: int = per_page
        self.clamp_pages: bool = clamp_pages

        self._current_page = 0
        self.pages = [entries[i: i + per_page] for i in range(0, len(entries), per_page)]

        self.msg: discord.Message = MISSING
        self.ctx: Context | discord.Interaction = MISSING

        self.update_buttons()

    @property
    def numerate_start(self) -> int:
        """:class:`int`: The start of the numerate."""
        return (self._current_page * self.per_page) + 1

    @property
    def current_page(self) -> int:
        """:class:`int`: The current page the user is on."""
        return self._current_page + 1

    @property
    def total_pages(self) -> int:
        """:class:`int`: Returns the total amount of pages."""
        return len(self.pages)

    @property
    def middle(self) -> str:
        """:class:`str`: Returns the middle text for the paginator."""
        return f'{self.current_page}/{self.total_pages}'

    def _message_kwargs(self, page: T) -> dict:
        """:class:`dict`: The kwargs to edit/send the message with."""
        payload = {'view': self, TYPE_MAPPING[page.__class__]: page}
        return payload

    async def on_timeout(self) -> None:
        """|coro|

        Called when the paginator times out.
        """
        if self.msg:
            await self.msg.edit(view=None)

    async def format_page(self, entries: List[T], /) -> discord.Embed:
        """|coro|

        Used to make the embed that the user sees.

        Parameters
        ----------
        entries: List[Any]
            A list of entries for the current page.

        Returns
        -------
        :class:`discord.Embed`
            The embed for this page.
        """
        raise NotImplementedError('Subclass did not overwrite format_page coro.')

    def _switch_page(self, count: int, /) -> List[T]:
        self._current_page += count

        if self.clamp_pages:
            if count < 0:  # Going down
                if self._current_page < 0:
                    self._current_page = self.total_pages - 1
            elif count > 0:  # Going up
                if self._current_page > self.total_pages - 1:  # - 1 for indexing
                    self._current_page = 0

        self.update_buttons()
        return self.pages[self._current_page]

    @discord.ui.button(label='<==', style=discord.ButtonStyle.green)
    async def on_arrow_backward(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa
        entries = self._switch_page(-1)
        page = await self.format_page(entries)
        return await interaction.response.edit_message(**self._message_kwargs(page))

    @discord.ui.button(label='1/-', style=discord.ButtonStyle.grey)
    async def on_middle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa
        await interaction.response.send_modal(JumpToModal(self))

    @discord.ui.button(label='==>', style=discord.ButtonStyle.green)
    async def on_arrow_forward(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:  # noqa
        entries = self._switch_page(1)
        page = await self.format_page(entries)
        return await interaction.response.edit_message(**self._message_kwargs(page))

    def update_buttons(self):
        self.on_middle.label = self.middle

    async def _paged_embeds(self) -> List[discord.Embed]:
        for page in self.pages:
            yield await self.format_page(page)

    async def search_for_query(self, query: str, interaction: discord.Interaction):
        results = []
        async for index, current in aenumerate(self._paged_embeds(), 1):
            if isinstance(current, discord.Embed):
                if current.fields:
                    name_results = fuzzy.finder(query, current.fields, key=lambda x: x.name or x.value)
                    for entry in name_results:
                        results.append(f'[{index}] {discord.utils.remove_markdown(entry.name)}')
                if current.description:
                    description_results = fuzzy.finder(query, current.description.split('\n'))
                    for entry in description_results:
                        results.append(f'[{index}] {discord.utils.remove_markdown(entry)}')
            elif isinstance(current, str):
                current_results = fuzzy.finder(query, current)
                for entry in current_results:
                    results.append(f'[{index}] {discord.utils.remove_markdown(entry)}')
        try:
            result = results[0]
        except ValueError:
            return await self._send(
                interaction, content=f'<:redTick:1079249771975413910> Could not find match for {query!r}',
                ephemeral=True
            )

        ID_REGEX = re.compile(r'\[(\d+)] .+')
        value = int(ID_REGEX.match(result[0]).groups()[0]) - 1
        count = value - self._current_page
        entries = self._switch_page(abs(count) if value > self._current_page else -abs(count))
        page = await self.format_page(entries)
        await self.msg.edit(**self._message_kwargs(page))

    @classmethod
    @overload
    async def start(
            cls: Type[BasePaginator],
            context: Context,
            /,
            *,
            entries: List[T],
            per_page: int = 10,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: None
    ) -> BasePaginator[T]:
        ...

    @classmethod
    @overload
    async def start(
            cls: Type[BasePaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: List[T],
            per_page: int = 10,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: None
    ) -> BasePaginator[T]:
        ...

    @classmethod
    async def start(
            cls: Type[BasePaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: List[T],
            per_page: int = 10,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: Any
    ) -> BasePaginator[T]:
        """|coro|

        Used to start the paginator.

        Parameters
        ----------
        context: Union[:class:`commands.Context`, :class:`discord.Interaction`]
            The context to send to. This could also be discord.abc.Messageable as `ctx.send` is the only method
            used.
        entries: List[T]
            A list of entries to pass onto the paginator.
        per_page: :class:`int`
            A number of how many entries you want per page.
        clamp_pages: :class:`bool`
            Whether to clamp the pages to the amount of entries.
        timeout: :class:`int`
            How long to wait before the paginator closes due to inactivity.
        search_for: :class:`bool`
            Whether to enable the search feature.
        ephemeral: :class:`bool`
            Whether to make the message ephemeral.
        *args: :class:`Any`
            Any arguments to pass onto the paginator.
        **kwargs: :class:`Any`
            Any keyword arguments to pass onto the paginator.

        Returns
        -------
        class:`BaseButtonPaginator`[T]
            The paginator that was started.
        """
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        page = await self.format_page(self.pages[0])
        object_kwargs = self._message_kwargs(page)
        if self.total_pages <= 1:
            object_kwargs.pop('view')

        if search_for and self.total_pages > 5:
            self.add_item(SearchForButton(self))

        self.msg = await cls._send(context, ephemeral, **object_kwargs)
        return self

    @classmethod
    async def _send(cls, ctx: Context | discord.Interaction, ephemeral: bool, **kwargs) -> discord.Message:
        if isinstance(ctx, Context):
            message = await ctx.send(**kwargs)
        elif isinstance(ctx, discord.Message):
            message = await ctx.channel.send(**kwargs)
        elif isinstance(ctx, discord.Interaction):
            if ctx.response.is_done():
                await ctx.followup.send(**kwargs, ephemeral=ephemeral)
            else:
                await ctx.response.send_message(**kwargs, ephemeral=ephemeral)
            message = await ctx.original_response()
        return message  # noqa


class EmbedPaginator(BasePaginator[discord.Embed]):
    """Subclass of :class:`BasePaginator` that is used to paginate :class:`discord.Embed`'s."""

    async def format_page(self, entries: List[discord.Embed], /) -> List[discord.Embed]:
        return entries

    def _message_kwargs(self, page: List[discord.Embed]) -> dict:
        return {'embeds': page, 'view': self}

    @classmethod
    async def start(
            cls: Type[EmbedPaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: List[discord.Embed],
            per_page: int = 1,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: None
    ) -> EmbedPaginator[discord.Embed]:
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        pages = await self.format_page(self.pages[0])
        object_kwargs = self._message_kwargs(pages)
        if self.total_pages <= 1:
            object_kwargs.pop('view')

        self.msg = await cls._send(context, ephemeral, **object_kwargs)
        return self


class LinePaginator(BasePaginator[List[Any]]):
    """Subclass of :class:`BasePaginator` that is used to paginate lines with a template Embed."""

    embed: discord.Embed
    location: Literal['field', 'description']

    async def format_page(self, entries: List[Any], /) -> discord.Embed:
        embed = self.embed.copy()
        if self.location == 'field':
            embed.add_field(name='Results', value='\n'.join(entries))
        else:
            embed.description = '\n'.join(entries)
        return embed

    def _message_kwargs(self, page: discord.Embed) -> dict:
        return {'embed': page, 'view': self}

    @classmethod
    async def start(
            cls: Type[LinePaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: List[Any],
            per_page: int = 1,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: Any
    ) -> LinePaginator[Any]:
        """|coro|

        Used to start the paginator.

        Parameters
        ----------
        context: :class:`commands.Context`
            The context to send to. This could also be discord.abc.Messageable as `ctx.send` is the only method
            used.
        entries: List[T]
            A list of entries to pass onto the paginator.
        per_page: :class:`int`
            A number of how many entries you want per page.
        clamp_pages: :class:`bool`
            Whether to clamp the pages to the amount of entries.
        timeout: :class:`int`
            How long to wait before the paginator closes due to inactivity.
        search_for: :class:`bool`
            Whether to enable the search feature.
        ephemeral: :class:`bool`
            Whether to make the message ephemeral.
        *args: :class:`Any`
            Any arguments to pass onto the paginator.
        **kwargs: :class:`Any`
            Any keyword arguments to pass onto the paginator.

        ...
            embed: :class:`discord.Embed`
                 Special keyword-only argument for the embed to use for the paginator.
            location: Literal['field', 'description']
                 Special keyword-only argument for the location to put the entries in.

        Returns
        -------
        class:`BaseButtonPaginator`[T]
            The paginator that was started.
        """
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        if not kwargs.get('embed'):
            kwargs['embed'] = discord.Embed(colour=helpers.Colour.darker_red())

        self.embed = kwargs.pop('embed')
        self.location = kwargs.pop('location', 'field')

        pages = await self.format_page(self.pages[0])
        object_kwargs = self._message_kwargs(pages)
        if self.total_pages <= 1:
            object_kwargs.pop('view')

        self.msg = await cls._send(context, ephemeral, **object_kwargs)
        return self


class TextPaginator(BasePaginator[str]):
    """Subclass of :class:`BasePaginator` that is used to paginate a text."""

    async def format_page(self, entries: List[str], /) -> str:
        return entries[0]

    def _message_kwargs(self, page: str) -> dict:
        return {'content': page, 'view': self}

    @classmethod
    async def start(
            cls: Type[TextPaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: List[str],
            per_page: int = 1,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: None
    ) -> TextPaginator[str]:
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        page = await self.format_page(self.pages[0])
        object_kwargs = self._message_kwargs(page)  # type: ignore  # lying
        if self.total_pages <= 1:
            object_kwargs.pop('view')

        self.msg = await cls._send(context, ephemeral, **object_kwargs)
        return self


class FilePaginator(BasePaginator[AnyStr]):
    """Subclass of :class:`BasePaginator` that is used to paginate files."""

    async def format_page(self, entries: List[AnyStr]) -> List[discord.File]:
        files = []
        for entry in entries:
            if len(entry) < 8388608:  # 8 MB
                files.append(discord.File(fp=io.BytesIO(entry), filename=f'{uuid.uuid4()}.png'))
        return files

    def _message_kwargs(self, page: List[discord.File]) -> dict:
        return {'attachments': page, 'view': self}

    @classmethod
    async def start(
            cls: Type[FilePaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: List[AnyStr],
            per_page: int = 1,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: None
    ) -> FilePaginator[AnyStr]:
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        page = await self.format_page(self.pages[0])
        object_kwargs = {'files': page, 'view': self}
        if self.total_pages <= 1:
            object_kwargs.pop('view')

        self.msg = await cls._send(context, ephemeral, **object_kwargs)
        return self


class PaginatorInterface(BasePaginator[AnyStr]):
    """A message and reaction based interface for paginators.

    This allows users to interactively navigate the pages of a Paginator, and supports live output."""

    def __init__(self, *, prefix: str = '```', suffix: str = '```', max_size: int = 2000, entries: List[AnyStr],
                 per_page: int = 10, clamp_pages: bool = True, timeout: int = 180) -> None:
        self.interface: TextSource = TextSource(prefix=prefix, suffix=suffix, max_size=max_size)
        self.sent_page_reactions = False

        self.task: Optional[asyncio.Task[None]] = None
        self.send_lock: asyncio.Event = asyncio.Event()

        self.close_exception: Optional[BaseException] = None

        if len(self.pages) > self.max_page_size:
            raise ValueError(
                f'Paginator passed has too large of a page size for this interface. '
                f'({len(self.pages)} > {self.max_page_size})'
            )

        super().__init__(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)

    async def format_page(self, entries: List[AnyStr], /) -> str:
        return entries[0]

    # noinspection PyProtectedMember
    @property
    def pages(self):
        """Returns the paginator's pages without prematurely closing the active page."""
        paginator_pages = list(self.interface._pages)
        if len(self.interface._current_page) > 1:
            paginator_pages.append(
                '\n'.join(self.interface._current_page)
                + '\n'
                + (self.interface.suffix or '')
            )

        return paginator_pages

    @property
    def page_count(self):
        """Returns the page count of the internal paginator."""
        return len(self.pages)

    @property
    def display_page(self):
        """Returns the current page the paginator interface is on."""
        self._current_page = max(0, min(self.page_count - 1, self._current_page))
        return self._current_page

    @display_page.setter
    def display_page(self, value: int):
        """
        Sets the current page the paginator is on. Automatically pushes values inbounds.
        """

        self._current_page = max(0, min(self.page_count - 1, value))

    max_page_size = 2000

    async def add_line(self, *args: Any, **kwargs: Any):
        """A proxy function that allows this PaginatorInterface to remain locked to the last page
        if it is already on it."""

        display_page = self.display_page
        page_count = self.page_count

        self.interface.add_line(*args, **kwargs)

        new_page_count = self.page_count

        if display_page + 1 == page_count:
            self._current_page = new_page_count

        self.send_lock.set()

    async def send_to(self, destination: discord.abc.Messageable):
        """Sends a message to the given destination with this interface.
        This automatically creates the response task for you."""
        page = await self.format_page([self.pages[self.display_page]])
        self.msg = await destination.send(
            **self._message_kwargs(page), allowed_mentions=discord.AllowedMentions.none()
        )

        self.send_lock.set()

        if self.task:
            self.task.cancel()

        self.task = self.ctx.bot.loop.create_task(self.wait_loop())

        return self

    @property
    def closed(self):
        """Is this interface closed?"""
        if not self.task:
            return False
        return self.task.done()

    async def send_lock_delayed(self):
        """A coroutine that returns 1 second after the send lock has been released
        This helps reduce release spam that hits rate limits quickly."""

        gathered = await self.send_lock.wait()
        self.send_lock.clear()
        await asyncio.sleep(1)
        return gathered

    async def wait_loop(self):
        """Waits on a loop for updates to the interface. This should not be called manually - it is handled by `send_to`."""

        if not self.msg:
            raise RuntimeError('Message not set on PaginatorInterface')

        if not self.ctx.bot.user:
            raise RuntimeError('A PaginatorInterface cannot be started while the bot is offline')

        try:
            while not self.ctx.bot.is_closed():
                await asyncio.wait_for(self.send_lock_delayed(), timeout=self.timeout)

                self.update_buttons()

                try:
                    page = await self.format_page([self.pages[self._current_page]])
                    await self.msg.edit(**self._message_kwargs(page))
                except discord.NotFound:
                    return

        except (asyncio.CancelledError, asyncio.TimeoutError) as exception:
            self.close_exception = exception

            if self.ctx.bot.is_closed():
                return

            if not self.msg:
                return

            self.stop()

    @classmethod
    async def start(
            cls: Type[PaginatorInterface],
            context: Context | discord.Interaction,
            *,
            entries: List[AnyStr],
            per_page: int = 10,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            prefix: str = '```',
            suffix: str = '```',
            max_size: int = 2000
    ) -> PaginatorInterface[AnyStr]:
        """
        Starts a pagination session.

        Parameters
        ----------
        context: Context | discord.Interaction
            The context or interaction to start the pagination session in.
        entries: List[AnyStr]
            The entries to paginate.
        per_page: int
            How many entries to show per page.
        clamp_pages: bool
            Whether to clamp the page number to the number of pages.
        timeout: int
            How long to wait for new reactions before the pagination session closes.
        search_for: bool
            Whether to search for the message to edit.
        ephemeral: bool
            Whether to make the message ephemeral.
        prefix: str
            The prefix to add to each page.
        suffix: str
            The suffix to add to each page.
        max_size: int
            The maximum size of each page.

        Returns
        -------
        PaginatorInterface[AnyStr]
            The paginator object.
        """
        self = cls(
            entries=entries,
            per_page=per_page,
            clamp_pages=clamp_pages,
            timeout=timeout,
            prefix=prefix,
            suffix=suffix,
            max_size=max_size
        )
        self.ctx = context

        page = await self.format_page([self.pages[self.display_page]])
        kwargs = self._message_kwargs(page)  # type: ignore  # lying

        self.msg = await cls._send(context, ephemeral, **kwargs)
        return self
