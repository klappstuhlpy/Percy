from __future__ import annotations

from abc import ABCMeta, abstractmethod
from collections.abc import Collection, AsyncGenerator
from typing import TYPE_CHECKING, Any, AnyStr, Generic, Literal, NamedTuple, Self, TypeVar, overload, override

import discord
import numpy as np
from discord.ext import commands
from discord.utils import MISSING

from app.core.models import Context
from app.core.views import View
from app.utils import aenumerate, fuzzy, helpers
from config import Emojis

__all__ = (
    'TextSource',
    'BasePaginator',
    'EmbedPaginator',
    'LinePaginator',
    'TextPaginator',
    'FilePaginator',
    'TextSourcePaginator'
)

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
    prefix: :class:`str`
        The prefix inserted to every page. e.g. three backticks, if any.
    suffix: :class:`str`
        The suffix appended at the end of every page. e.g. three backticks, if any.
    max_size: :class:`int`
        The maximum amount of codepoints allowed in a page.
    seperator: :class:`str`
        The character string inserted between lines. e.g. a newline character.
    """

    def __init__(
            self,
            prefix: str | None = '```',
            suffix: str | None = '```',
            max_size: int = 2000,
            seperator: str = '\n'
    ) -> None:
        self.prefix: str | None = prefix
        self.suffix: str | None = suffix
        self.max_size: int = max_size
        self.seperator: str = seperator

        if self.prefix is not None:
            self._current_page: list[str] = [self.prefix]
            self._count: int = len(self.prefix) + len(self.seperator)
        else:
            self._current_page = []
            self._count = 0

        self._pages: list[str] = []

    @property
    def prefix_len(self) -> int:
        """Returns the length of the prefix."""
        return len(self.prefix) if self.prefix is not None else 0

    @property
    def suffix_len(self) -> int:
        """Returns the length of the suffix."""
        return len(self.suffix) if self.suffix is not None else 0

    def add_lines(self, lines: list[str], /, *, empty: bool = False) -> Self:
        """Adds multiple lines to the current page.

        If the line exceeds the :attr:`max_size` then an exception
        is raised.

        Parameters
        -----------
        lines: List[:class:`str`]
            The lines to add.
        empty: :class:`bool`
            Indicates if another empty line should be added.

        Raises
        ------
        RuntimeError
            The line was too big for the current :attr:`max_size`.
        """
        for line in lines:
            self.add_line(line, empty=empty)

        return self

    def add_line(self, line: str = '', *, empty: bool = False) -> Self:
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
        MAX_PAGE_SIZE = self.max_size - self.prefix_len - self.suffix_len - 2 * len(self.seperator)
        if len(line) > MAX_PAGE_SIZE:
            raise RuntimeError(f'Line exceeds maximum page size {MAX_PAGE_SIZE}')

        if self._count + len(line) + len(self.seperator) > self.max_size - self.suffix_len:
            self.close_page()

        self._count += len(line) + len(self.seperator)
        self._current_page.append(line)

        if empty:
            self._current_page.append('')
            self._count += len(self.seperator)

        return self

    def close_page(self) -> Self:
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

        return self

    def __len__(self) -> int:
        total = sum(len(p) for p in self._pages)
        return total + self._count

    @property
    def pages(self) -> list[str]:
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

    def __init__(self, paginator: BasePaginator) -> None:
        super().__init__(timeout=30)
        self.paginator: BasePaginator = paginator
        self.page_number.placeholder = f'Enter a Number between 1 and {self.paginator.total_pages}'
        self.page_number.min_length = 1
        self.page_number.max_length = len(str(self.paginator.total_pages))

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        if not self.page_number.value.isdigit():
            return await interaction.response.send_message('Please enter a number.', ephemeral=True)
        if not 1 <= int(self.page_number.value) <= self.paginator.total_pages:
            return await interaction.response.send_message(
                f'Please enter a valid page number in range `1` to `{self.paginator.total_pages}`.', ephemeral=True)

        value = int(self.page_number.value) - 1
        count = value - self.paginator._current_page
        entries = self.paginator.switch_page(abs(count) if value > self.paginator._current_page else -abs(count))
        page = await self.paginator.format_page(entries)
        return await interaction.response.edit_message(**self.paginator.resolve_msg_kwargs(page))


class SearchForModal(discord.ui.Modal, title='Search for Similarity'):
    """Modal that prompts users to search in all embeds for query similarities"""
    query = discord.ui.TextInput(label='Query', style=discord.TextStyle.short)

    def __init__(self, paginator: BasePaginator) -> None:
        super().__init__(timeout=60)
        self.paginator: BasePaginator = paginator
        self.query.min_length = 3

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await self.paginator.search_for_query(self.query.value, interaction)
        self.stop()


class SearchForButton(discord.ui.Button):
    def __init__(self, paginator: BasePaginator) -> None:
        super().__init__(label='Search for …', emoji='\N{RIGHT-POINTING MAGNIFYING GLASS}',
                         style=discord.ButtonStyle.grey, row=1)
        self.paginator: BasePaginator = paginator

    async def callback(self, interaction: discord.Interaction) -> Any:
        await interaction.response.send_modal(SearchForModal(self.paginator))


BasePaginatorT = TypeVar('BasePaginatorT', bound='BasePaginator')


class BasePaginator(View, Generic[T], metaclass=ABCMeta):
    """The Base Button Paginator class. Will handle all page switching without
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
    
    if TYPE_CHECKING:
        entries: list[T]
        per_page: int
        clamp_pages: bool
        extras: dict[str, Any]
        pages: list[list[T]] | list[dict[..., T]]
        msg: discord.Message

    def __init__(
            self,
            *,
            entries: Collection[T],
            per_page: int = 10,
            clamp_pages: bool = True,
            timeout: int = 180
    ) -> None:
        super().__init__(timeout=timeout)
        self.entries: list[T] | dict[..., T] = list(entries) if not isinstance(entries, dict) else entries
        self.per_page: int = per_page
        self.clamp_pages: bool = clamp_pages

        self.extras: dict[str, Any] = {}

        self._current_page: int = 0

        try:
            self.pages = [self.entries[i: i + per_page] for i in range(0, len(self.entries), per_page)]
        except KeyError:
            self.pages = [self.entries]

        self.msg: discord.Message = MISSING
        self._ctx: Context | discord.Interaction = MISSING

        self.update_buttons()

    @property
    def ctx(self) -> Context | discord.Interaction:
        """:class:`commands.Context` | :class:`discord.Interaction`: The context to send to."""
        return self._ctx

    @ctx.setter
    def ctx(self, new_context: Context | discord.Interaction) -> None:
        """Sets the context to send to."""
        self._ctx = new_context
        self.members = new_context.user

    @property
    def numerate_start(self) -> int:
        """:class:`int`: A helper property to numerate items in the paginator correctly."""
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

    async def to_array(self) -> np.ndarray:
        """Returns a 2D array of the pages, their index and the page content."""

        def resolve_entries(e: T) -> list[str]:
            if isinstance(e, discord.Embed):
                if e.fields:
                    for field in e.fields:
                        yield f'{field.name}: {field.value}'
                if e.description:
                    yield e.description
                if e.title:
                    yield e.title
            elif isinstance(e, str):
                yield e
            else:
                yield str(e)

        return np.array([(i, list(resolve_entries(page))) async for i, page in aenumerate(self._paged_embeds())])

    def resolve_msg_kwargs(self, page: T) -> dict[str, BasePaginatorT | T]:
        """:class:`dict`: The kwargs to edit/send the message with."""
        try:
            payload = {'view': self, TYPE_MAPPING[page.__class__]: page}
        except KeyError:
            raise TypeError(f'Unsupported type {page.__class__} for pagination.')
        return payload

    async def on_timeout(self) -> None:
        """|coro|

        Called when the paginator times out.
        """
        if self.msg:
            await self.msg.edit(view=None)

    @abstractmethod
    async def format_page(self, entries: list[T], /) -> discord.Embed:
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
        raise NotImplementedError

    def switch_page(self, count: int, /) -> list[T]:
        """Switches the page by a certain amount.

        If the count is negative, it will go backwards.
        If the count is positive, it will go forwards.
        If the count exceeds the total pages, it will clamp to the first or last page if `clamp_pages` is enabled.

        Parameters
        ----------
        count: :class:`int`
            The amount of pages to switch by.

        Returns
        -------
        list[Any]
            The entries for the new page.
        """
        self._current_page += count

        if self.clamp_pages:
            if count < 0:
                if self._current_page < 0:
                    self._current_page = self.total_pages - 1
            elif count > 0 and self._current_page > self.total_pages - 1:
                self._current_page = 0

        self.update_buttons()
        return self.pages[self._current_page]

    @discord.ui.button(label='<==', style=discord.ButtonStyle.green)
    async def on_arrow_backward(self, interaction: discord.Interaction, _) -> None:
        entries = self.switch_page(-1)
        page = await self.format_page(entries)
        return await interaction.response.edit_message(**self.resolve_msg_kwargs(page))

    @discord.ui.button(label='1/-', style=discord.ButtonStyle.grey)
    async def on_middle(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.send_modal(JumpToModal(self))

    @discord.ui.button(label='==>', style=discord.ButtonStyle.green)
    async def on_arrow_forward(self, interaction: discord.Interaction, _) -> None:
        entries = self.switch_page(1)
        page = await self.format_page(entries)
        return await interaction.response.edit_message(**self.resolve_msg_kwargs(page))

    def update_buttons(self) -> None:
        self.on_middle.label = self.middle

    async def _paged_embeds(self) -> AsyncGenerator[discord.Embed, None]:
        """Returns an async generator of the pages."""
        for page in self.pages:
            yield await self.format_page(page)

    async def search_for_query(self, query: str, interaction: discord.Interaction) -> None:
        """|coro|

        Search for a query in all embeds and return the matches.
        This uses a fuzzy search algorithm to find the best match by comparing the previous ratio.

        Parameters
        ----------
        query: :class:`str`
            The query to search for.
        interaction: :class:`discord.Interaction`
            The interaction to respond to.
        """
        class SearchResult(NamedTuple):
            ratio: float
            index: int

        current_result: SearchResult | None = None
        arr = await self.to_array()
        for index, item in arr:
            for entry in item:
                ratio = fuzzy.ratio(query, entry)
                if current_result is None or ratio > current_result.ratio:
                    current_result = SearchResult(ratio, index)

        if current_result is None:
            await self._send(interaction, content=f'{Emojis.error} No matches found.', ephemeral=True)
            return

        entries = self.switch_page(current_result.index - self._current_page)
        page = await self.format_page(entries)
        await self._edit(interaction, **self.resolve_msg_kwargs(page))

    @classmethod
    @overload
    async def start(
            cls,
            context: Context,
            /,
            *,
            entries: list[T],
            per_page: int = 10,
            timeout: int = 180,
            clamp_pages: Literal[True] = True,
            search_for: Literal[True] = False,
            ephemeral: Literal[True] = False,
            **kwargs: None
    ) -> BasePaginatorT:
        ...

    @classmethod
    @overload
    async def start(
            cls,
            context: discord.Interaction,
            /,
            *,
            entries: list[T],
            per_page: int = 10,
            timeout: int = 180,
            clamp_pages: Literal[False] = True,
            search_for: Literal[False] = False,
            ephemeral: Literal[False] = False,
            **kwargs: None
    ) -> BasePaginatorT:
        ...

    @classmethod
    async def start(
            cls,
            context: Context | discord.Interaction,
            /,
            *,
            entries: list[T],
            per_page: int = 10,
            timeout: int = 180,
            clamp_pages: bool = True,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: Any
    ) -> BasePaginatorT:
        """|coro|

        Used to start the paginator.

        Parameters
        ----------
        context: :class:`commands.Context` | :class:`discord.Interaction`
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
        **kwargs: :class:`Any`
            Any keyword arguments to pass onto the paginator.

        Returns
        -------
        class:`BaseButtonPaginator`[T]
            The paginator that was started.
        """
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context
        self.extras.update(kwargs)

        page = await self.format_page(self.pages[0])
        object_kwargs = self.resolve_msg_kwargs(page)

        if search_for and self.total_pages > 3:
            self.add_item(SearchForButton(self))

        if self.total_pages <= 1:
            object_kwargs.pop('view')

        self.msg = await cls._send(context, ephemeral, **object_kwargs)
        return self

    @classmethod
    async def _send(cls, ctx: Context | discord.Interaction | discord.Message, ephemeral: bool, **kwargs: Any) -> discord.Message:
        if isinstance(ctx, Context):
            message = await ctx.send(**kwargs)
        elif isinstance(ctx, discord.Message):
            message = await ctx.channel.send(**kwargs)
        else:
            if ctx.response.is_done():
                await ctx.followup.send(**kwargs, ephemeral=ephemeral)
            else:
                await ctx.response.send_message(**kwargs, ephemeral=ephemeral)
            message = await ctx.original_response()
        return message

    @classmethod
    async def _edit(cls, ctx: Context | discord.Interaction, **kwargs: Any) -> discord.Message:
        kwargs.pop('ephemeral', None)

        if isinstance(ctx, discord.Interaction):
            if ctx.response.is_done():
                message = await ctx.edit_original_response(**kwargs)
            else:
                message = await ctx.response.edit_message(**kwargs)
        else:
            message = await ctx.message.edit(**kwargs)
        return message


class EmbedPaginator(BasePaginator[discord.Embed]):
    """Subclass of :class:`BasePaginator` that is used to paginate :class:`discord.Embed`'s."""

    async def format_page(self, entries: list[discord.Embed], /) -> list[discord.Embed]:
        return entries

    def resolve_msg_kwargs(self, page: list[discord.Embed]) -> dict:
        return {'embeds': page, 'view': self}

    @classmethod
    async def start(
            cls: type[EmbedPaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: list[discord.Embed],
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
        object_kwargs = self.resolve_msg_kwargs(pages)
        if self.total_pages <= 1:
            object_kwargs.pop('view')

        self.msg = await cls._send(context, ephemeral, **object_kwargs)
        return self


class LinePaginator(BasePaginator[T]):
    """Subclass of :class:`BasePaginator` that is used to paginate lines with a template Embed."""

    if TYPE_CHECKING:
        embed: discord.Embed
        location: Literal['field', 'description']
        numerate: bool

    async def format_page(self, entries: list[T], /) -> discord.Embed:
        embed = self.embed.copy()
        custom_fields = isinstance(entries[0], tuple)

        def fmt(x: T) -> str:
            if self.numerate:
                return f'{self.numerate_start + entries.index(x)}. {x}'
            return str(x)

        match self.location:
            case 'field':
                if custom_fields:
                    for name, value, inline in entries:
                        embed.add_field(name=name, value=value, inline=inline)
                else:
                    embed.add_field(name='Results', value='\n'.join(fmt(e) for e in entries))
            case 'description':
                embed.description = '\n'.join(fmt(e) for e in entries)

        return embed

    def resolve_msg_kwargs(self, page: discord.Embed) -> dict:
        return {'embed': page, 'view': self}

    @classmethod
    async def start(
            cls: type[LinePaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: Collection[T],
            per_page: int = 15,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            location: Literal['field', 'description'] = 'field',
            embed: discord.Embed = discord.Embed(colour=helpers.Colour.white()),
            numerate: bool = False
    ) -> LinePaginator[T]:
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        self.numerate = numerate
        self.embed = embed
        self.location = location

        if not self.pages:
            await cls._send(context, ephemeral, content=f'{Emojis.error} No entries to paginate currently.')
            return self

        pages = await self.format_page(self.pages[0])
        object_kwargs = self.resolve_msg_kwargs(pages)
        if self.total_pages <= 1:
            object_kwargs.pop('view')

        self.msg = await cls._send(context, ephemeral, **object_kwargs)
        return self


class TextPaginator(BasePaginator[str]):
    """Subclass of :class:`BasePaginator` that is used to paginate a text."""

    async def format_page(self, entries: list[str], /) -> str:
        return entries[0]

    def resolve_msg_kwargs(self, page: str) -> dict:
        return {'content': page, 'view': self}

    @classmethod
    async def start(
            cls: type[TextPaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: list[str],
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
        object_kwargs = self.resolve_msg_kwargs(page)  # type: ignore  # lying
        if self.total_pages <= 1:
            object_kwargs.pop('view')

        self.msg = await cls._send(context, ephemeral, **object_kwargs)
        return self


class FilePaginator(BasePaginator[discord.File]):
    """Subclass of :class:`BasePaginator` that is used to paginate files."""

    async def format_page(self, entries: list[discord.File]) -> list[discord.File]:
        return entries

    def resolve_msg_kwargs(self, page: list[discord.File]) -> dict:
        return {'attachments': page, 'view': self}

    @classmethod
    async def start(
            cls: type[FilePaginator],
            context: Context | discord.Interaction,
            /,
            *,
            entries: list[discord.File],
            per_page: int = 1,
            clamp_pages: bool = True,
            timeout: int = 180,
            search_for: bool = False,
            ephemeral: bool = False,
            **kwargs: None
    ) -> FilePaginator[discord.File]:
        self = cls(entries=entries, per_page=per_page, clamp_pages=clamp_pages, timeout=timeout)
        self.ctx = context

        page = await self.format_page(self.pages[0])
        object_kwargs = {'files': page, 'view': self}
        if self.total_pages <= 1:
            object_kwargs.pop('view')

        self.msg = await cls._send(context, ephemeral, **object_kwargs)
        return self


class TextSourcePaginator(BasePaginator[AnyStr]):
    """A paginator interface that handles the pagination session for you."""

    def __init__(
            self,
            ctx: Context,
            *,
            prefix: str = '```',
            suffix: str = '```',
            max_size: int = 2000,
            timeout: int = 180
    ) -> None:
        self.interface: TextSource = TextSource(prefix=prefix, suffix=suffix, max_size=max_size)
        super().__init__(entries=[], per_page=1, clamp_pages=False, timeout=timeout)
        self.ctx: Context = ctx

    async def format_page(self, entries: list[AnyStr], /) -> str:
        return entries[0]

    def add_line(self, line: str = '', *, empty: bool = False) -> None:
        self.interface.add_line(line, empty=empty)

    def close_page(self) -> None:
        self.interface.close_page()

    @override
    async def start(  # noqa[override]
            self,
            *,
            search_for: bool = False,
            ephemeral: bool = False,
    ) -> TextSourcePaginator[AnyStr]:
        """
        Starts a pagination session.

        Parameters
        ----------
        search_for: bool
            Whether to search for the message to edit.
        ephemeral: bool
            Whether to make the message ephemeral.

        Returns
        -------
        TextSourcePaginator[AnyStr]
            The paginator object.
        """
        # override the entries with the finished pages
        self.entries = self.interface.pages
        self.pages = [self.entries[i: i + 1] for i in range(0, len(self.entries), 1)]

        page = await self.format_page(self.pages[0])
        kwargs = self.resolve_msg_kwargs(page)

        if self.total_pages <= 1:
            kwargs.pop('view')

        self.msg = await self._send(self.ctx, ephemeral, **kwargs, allowed_mentions=discord.AllowedMentions.none())
        return self
