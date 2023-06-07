from __future__ import annotations

import enum
import logging
import textwrap
import traceback
from typing import List, Optional, Generic, TypeVar, Type

import discord
from discord import SelectOption
from discord.utils import MISSING

from bot import Percy
from . import helpers
from .paginator import BasePaginator
from cogs.utils.context import Context
from cogs.utils.formats import plural
from cogs.utils.sphinx_scraper import SphinxScraper, SearchResults, MethObject, MetaSpec


class DocType(enum.Enum):
    ATTRIBUTES = 1
    EXAMPLES = 2


T = TypeVar('T')
log = logging.getLogger(__name__)


class DocPaginator(BasePaginator[MethObject]):
    extra: DocType

    async def format_page(self, entries: List[MethObject], /) -> discord.Embed:
        if self.extra == DocType.EXAMPLES:
            embed = discord.Embed(
                title="Examples",
                description=f"```py\n{entries[0]}\n```",
                color=helpers.Colour.darker_red(),
            )
            embed.set_footer(text=f"Found {plural(len(self.entries)):example}.")
        elif self.extra == DocType.ATTRIBUTES:
            typ = "Methods" if entries[0].meta == MetaSpec.METHOD else "Attributes"

            def fmt(i: MethObject) -> str:
                if all(x == "" for x in i[1:3]):
                    return ""  # Placeholder for empty lines
                return f"[**{i.name}**]({i.url}) - {i.description}"

            embed = discord.Embed(
                title=typ,
                color=helpers.Colour.darker_red(),
                description="\n".join(fmt(i) for i in entries),
            )
            embed.set_footer(text=f"{plural(len(entries)):{typ.lower()[:-1]}} found")
        else:
            raise NotImplementedError(f"Unknown extra type {self.extra!r}")
        return embed


class DocSelect(discord.ui.Select):
    def __init__(self, parent: DocumentationView, results: SearchResults):
        self._parent: DocumentationView = parent
        self.texts = []

        for item in results.results:
            if item.name not in self.texts:
                self.texts.append(item.name)

        self._options = [
            SelectOption(label=name)
            for name in self.texts
        ]
        super().__init__(
            placeholder="Select an entry to view...",
            options=self._options,
            disabled=True if len(self._options) == 1 else False,
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user != self._parent.ctx.author:
            return await self._parent._send(  # noqa
                interaction,
                content="<:redTick:1079249771975413910> This select menu is not for your.",
                ephemeral=True
            )

        await interaction.response.defer()
        option = discord.utils.get(self._options, label=self.values[0])
        await self._parent._update(interaction, option.label)  # noqa


class DocumentationView(discord.ui.View, Generic[T]):
    """A View that represents a documentation page for a specific object.
    Using the Sphinx inventory parser that allows to scraper all important information
    into a :class:`Documentation` object.

    Parameters
    ----------
    scraper: :class:`.SphinxScraper`
        The scraper that is used to scrape the documentation.
    query: :class:`str`
        The query that was used to search for the documentation.
    timeout: :class:`int`
        The timeout for the view.
    """

    def __init__(
            self,
            *,
            bot: Percy,
            scraper: SphinxScraper,
            library: str,
            query: str,
            timeout: int = 450,
    ):
        super().__init__(timeout=timeout)

        self.bot: Percy = bot
        self.scraper: SphinxScraper = scraper
        self.library: str = library
        self.query: str = query

        self.ctx: Context | discord.Interaction = MISSING
        self.msg: discord.Message = MISSING

        self._current = None

    async def on_timeout(self) -> None:
        embed: discord.Embed = self.msg.embeds[0]
        embed.set_footer(text="View timed out.")
        embed.timestamp = discord.utils.utcnow()
        if self.msg is not MISSING:
            try:
                await self.msg.edit(view=None, embed=embed)
            except discord.HTTPException:
                pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        trc = "".join(traceback.format_exception(type(error), error, error.__traceback__, 4))
        log.error(f"Error while handling {item!r}:\n{trc}")
        await self._send(interaction,
                         content="<:warning:1113421726861238363> Unknown error occurred while scraping, "
                                 "maybe the sphinx inventory parser returned an invalid element.\n"
                                 "Please try again later, or contact the bot owner if the problem persists.",
                         ephemeral=True)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if not self._current:
            await self._send(interaction,
                             content="Seems like the Documentation Data isn't loaded though it should be, "
                                     "report this to the owner if the problem persists.",
                             ephemeral=True)
            return False

        if interaction.data.get("custom_id") == "show_attr":
            if not self._current.attributes:
                await self._send(interaction,
                                 content="There are no attributes for this Option, report this to "
                                         "the bot owner if there should be some.",
                                 ephemeral=True)
                return False
        elif interaction.data.get("custom_id") == "show_ex":
            if not self._current.examples:
                await self._send(interaction,
                                 content="There are no examples for this Option, report this to the bot "
                                         "owner if there should be some.",
                                 ephemeral=True)
                return False

        return True

    @staticmethod
    async def _send(interaction: discord.Interaction | Context, **kwargs) -> None:
        if isinstance(interaction, Context):
            await interaction.send(**kwargs)
        else:
            if interaction.response.is_done():
                await interaction.followup.send(**kwargs)
            else:
                await interaction.response.send_message(**kwargs)
        return

    async def _resolve(self, interaction: discord.Interaction | Context, *args, **kwargs) -> Optional[discord.Message]:
        if getattr(interaction, "_message", None) is not None:
            return await interaction._message.edit(content=None, **kwargs)  # noqa
        else:
            if isinstance(interaction, Context):
                self.msg = await interaction.send(*args, **kwargs)
            elif isinstance(interaction, discord.Interaction):
                if interaction.response.is_done():
                    await interaction.edit_original_response(*args, **kwargs)
                else:
                    await interaction.response.send_message(*args, **kwargs)

                self.msg = await interaction.original_response()
        return self.msg

    async def _update(self, interaction: discord.Interaction | Context, obj: str):
        if isinstance(interaction, Context):
            self.msg: discord.Message = MISSING

        docs = discord.utils.get(self.scraper._docs_cache[self.library], name=obj)

        if docs is None:
            return

        self.on_examples.disabled = bool(not docs.examples)
        self.on_attributes.disabled = bool(not docs.attributes)

        self._current = docs

        embed = docs.to_embed(color=self.bot.colour.darker_red(), library=self.library)
        await self._resolve(interaction, embed=embed, view=self)

    @discord.ui.button(
        label="Show Attributes",
        emoji="\N{OPEN FILE FOLDER}",
        custom_id="show_attr",
        style=discord.ButtonStyle.grey,
    )
    async def on_attributes(self, interaction: discord.Interaction, button: discord.Button) -> None:

        def format_desc(obj: MethObject):
            return textwrap.shorten(obj.description.split('\n')[0] if obj.description else "…", width=512, placeholder="…")

        attributes = [MethObject(MetaSpec.ATTRIBUTE, attr.name, attr.url, format_desc(attr)) for attr in self._current.attributes[0]]
        methods = [MethObject(MetaSpec.METHOD, attr.name, attr.url, format_desc(attr)) for attr in self._current.attributes[1]]

        num_attribute_pages = (len(attributes) + 9) // 10
        num_method_pages = (len(methods) + 9) // 10

        attributes.extend([MethObject(MetaSpec.EMPTY, "", "", "")] * (num_attribute_pages * 10 - len(attributes)))
        methods.extend([MethObject(MetaSpec.EMPTY, "", "", "")] * (num_method_pages * 10 - len(methods)))

        chunked = attributes + methods
        DocPaginator.extra = DocType.ATTRIBUTES
        await DocPaginator.start(interaction, entries=chunked, per_page=10, timeout=300, ephemeral=True, search_for=True)

    @discord.ui.button(
        label="Show Examples",
        emoji="\N{OPEN FILE FOLDER}",
        custom_id="show_ex",
        style=discord.ButtonStyle.grey
    )
    async def on_examples(self, interaction: discord.Interaction, button: discord.Button) -> None:
        DocPaginator.extra = DocType.EXAMPLES
        await DocPaginator.start(interaction, entries=self._current.examples, per_page=1, ephemeral=True)

    @classmethod
    async def start(
            cls: Type[DocumentationView],
            context: Context | discord.Interaction,
            *,
            bot: Percy,
            scraper: SphinxScraper,
            library: str,
            query: str,
            timeout: int = 450,
    ) -> DocumentationView[T]:
        self = cls(library=library, query=query, scraper=scraper, bot=bot, timeout=timeout)
        self.ctx = context

        results = await self.scraper.search(self.query, library=self.library, limit=25)

        if not results.results:
            await self._send(context, content="<:redTick:1079249771975413910> No results matching that query found.",
                             ephemeral=True)

        self.add_item(DocSelect(self, results))
        await self._update(self.ctx, results.results[0].original_name)
        return self
