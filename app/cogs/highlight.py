from __future__ import annotations

import itertools
from collections import defaultdict
from typing import TYPE_CHECKING, Any, NamedTuple

import asyncpg
import discord
from discord import app_commands, utils
from discord.ext import tasks

from app.core import Bot, Cog, Context, HybridContext, describe, group
from app.core.pagination import LinePaginator
from app.database import BaseRecord
from app.utils import fuzzy, helpers, validate_snowflakes
from app.utils.lock import lock

if TYPE_CHECKING:
    from collections.abc import Callable, Generator


class HighlightConfig(BaseRecord):
    bot: Bot
    id: int
    user_id: int
    location_id: int
    blocked: set[int]
    lookup: set[str]

    __slots__ = ('blocked', 'bot', 'id', 'location_id', 'lookup', 'user_id')

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.blocked = set(self.blocked or [])
        self.lookup = set(self.lookup or [])

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> HighlightConfig:
        record = await self.bot.db.highlights.update_config(self.id, key, values, connection=connection)
        return self.__class__(bot=self.bot, record=record)

    def match(self, text: str, /) -> str | None:
        """Match a highlight in a text.

        Parameters
        ----------
        text : str
            The text to check for highlights.

        Returns
        -------
        str | None
            The highlight if found, else None.
        """
        return next((lookup for lookup in self.lookup if lookup in text), None)

    async def delete(self, /) -> None:
        """|coro|

        Delete the highlight configuration.
        """
        await self.bot.db.highlights.delete_config(self.id)


class MessagedHighlight(NamedTuple):
    highlight: HighlightConfig
    message: discord.Message
    trigger: str


class Highlights(Cog):
    """Highlighting allows you to be notified when a specific word or phrase is mentioned in a message."""

    emoji = '<:pen:1322507977583759390>'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        self._highlight_data_batch: defaultdict[int, list[MessagedHighlight]] = defaultdict(list)

        self.bulk_send_loop.add_exception_type(asyncpg.PostgresConnectionError)
        self.bulk_send_loop.start()

    async def cog_unload(self) -> None:
        self.bulk_send_loop.stop()

    @lock('Highlight', 'batch', wait=True)
    async def bulk_send(self) -> None:
        if not self._highlight_data_batch:
            return

        for user_id, highlights in self._highlight_data_batch.items():
            user = self.bot.get_user(user_id)
            if user is None or not highlights:
                continue

            grouped_highlights = itertools.groupby(highlights, key=lambda x: x.trigger)
            for trigger, grouped in grouped_highlights:
                # transform back to list
                grouped_list: list[MessagedHighlight] = list(grouped)

                latest_triggered = max(grouped_list, key=lambda x: x.message.created_at)
                message = latest_triggered.message

                previous = [
                    f'[{utils.format_dt(m.created_at, "T")}] @{m.author}: {m.content}'
                    async for m in message.channel.history(limit=3, before=message)
                ]

                embed = discord.Embed(
                    title=f'Highlight triggered for "{latest_triggered.trigger}"',
                    description=(
                            '\n'.join(previous) +
                            f'\n'
                            f'[**{utils.format_dt(message.created_at, "T")}**] @{message.author}: {message.content}'
                    ),
                    color=helpers.Colour.white(),
                    timestamp=message.created_at
                )
                embed.add_field(name='Destination', value=message.jump_url, inline=False)
                embed.set_footer(text=f'From {message.guild.name}')  # type: ignore[union-attr]
                await user.send(embed=embed)

        self._highlight_data_batch.clear()

    @tasks.loop(seconds=15.0)
    async def bulk_send_loop(self) -> None:
        await self.bulk_send()

    async def get_guild_highlights(self, guild_id: int, /) -> list[HighlightConfig]:
        """|coro|

        Get all highlights for a user in a guild.

        Parameters
        ----------
        guild_id : int
            The guild's ID.

        Returns
        -------
        list[Highlight]
            A list of the guilds' highlights.
        """
        records = await self.bot.db.highlights.get_guild_configs(guild_id)
        return [HighlightConfig(bot=self.bot, record=record) for record in records]

    async def get_highlight_config(self, guild_id: int, user_id: int, /, *, initialize: bool = True) -> HighlightConfig | None:
        """|coro|

        Get a user's highlight configuration in a guild.

        Parameters
        ----------
        guild_id : int
            The guild's ID.
        user_id : int
            The user's ID.
        initialize : bool
            Whether to initialize the user's configuration if not found.

        Returns
        -------
        Highlight
            The user's highlight configuration.
        """
        record = await self.bot.db.highlights.get_config(guild_id, user_id)
        if not record and initialize:
            record = await self.bot.db.highlights.create_config(user_id, guild_id)
        return HighlightConfig(bot=self.bot, record=record) if record else None

    @staticmethod
    def find_highlight(
            highlights: list[HighlightConfig], message: discord.Message, /
    ) -> Generator[MessagedHighlight, None, None]:
        """|coro|

        Find a highlight in a list of highlights.

        Parameters
        ----------
        highlights : list[Highlight]
            A list of highlights.
        message : discord.Message
            The message to check for highlights.

        Returns
        -------
        Highlight | None
            The highlight if found, else None.
        """
        content = message.clean_content.casefold()
        for highlight in highlights:
            if (
                    (match := highlight.match(content))
                    and message.author.id != highlight.user_id
                    and message.author.id not in highlight.blocked
                    and message.channel.id not in highlight.blocked
            ):
                yield MessagedHighlight(highlight, message, match)

    @group('highlight', description='Manage highlight related commands.', hybrid=True, guild_only=True)
    async def highlight(self, ctx: Context) -> None:
        """Manage highlight related commands.

        Highlighting allows you to be notified when a specific word or phrase is mentioned in a message.
        ## Usage
        Notifications are sent to your DMs and with a little bit of delay to prevent spam,
        this works by grouping mentions and sending only the latest with few previous messages as context.
        This also works on for messages that have been deleted or edited.
        """
        if not ctx.invoked_subcommand:
            await ctx.send_help('highlight')

    @highlight.command('add', aliases=['+'], description='Adds a highlight word or phrase.')
    @describe(trigger='The word or phrase to highlight. Not case-sensitive.')
    async def highlight_add(self, ctx: Context, *, trigger: str) -> None:
        highlight = await self.get_highlight_config(ctx.guild.id, ctx.author.id)  # type: ignore[union-attr]
        if trigger in highlight.lookup:  # type: ignore[union-attr]
            await ctx.send_error('This highlight already exists.')
            return

        await highlight.append(lookup=trigger.casefold())  # type: ignore[union-attr]
        await ctx.send_success('Added highlight.', ephemeral=True)

    @highlight.command('remove', aliases=['rm', '-'], description='Removes a highlight word or phrase.')
    @describe(trigger='The word or phrase to remove.')
    async def highlight_remove(self, ctx: Context, *, trigger: str) -> None:
        highlight = await self.get_highlight_config(ctx.guild.id, ctx.author.id)  # type: ignore[union-attr]
        if trigger.casefold() not in highlight.lookup:  # type: ignore[union-attr]
            await ctx.send_error('Such a highlight does not exist.')
            return

        await highlight.prune(lookup=trigger)  # type: ignore[union-attr]
        await ctx.send_success('Removed highlight.', ephemeral=True)

    @highlight.command(
        'block',
        description='Block an entity from triggering your highlights.',
        with_app_command=False
    )
    @describe(entities='The entities to block.')
    async def highlight_block(self, ctx: Context, *entities: discord.TextChannel | discord.Member) -> None:
        """Block an entity from triggering your highlights."""
        if not entities:
            await ctx.send_error('You need to provide at least one entity to block.')
            return

        highlight = await self.get_highlight_config(ctx.guild.id, ctx.author.id)  # type: ignore[union-attr]
        blocked = highlight.blocked  # type: ignore[union-attr]
        blocked.update(entity.id for entity in entities)
        await highlight.update(blocked=blocked)  # type: ignore[union-attr]
        await ctx.send_success('Blocked entities from triggering highlights.', ephemeral=True)

    @highlight_block.define_app_command()  # type: ignore[attr-defined]
    @describe(entity='The entity to block.')
    async def highlight_block_app_command(self, ctx: HybridContext, entity: str) -> None:
        await ctx.full_invoke(*validate_snowflakes(entity, guild=ctx.guild, to_obj=True))  # type: ignore[arg-type, misc]

    @highlight.command(
        'unblock',
        description='Unblock an entity from triggering your highlights.',
        with_app_command=False
    )
    @describe(entities='The entities to unblock.')
    async def highlight_unblock(self, ctx: Context, *entities: discord.TextChannel | discord.Member) -> None:
        """Unblock an entity from triggering your highlights."""
        if not entities:
            await ctx.send_error('You need to provide at least one entity to unblock.')
            return

        highlight = await self.get_highlight_config(ctx.guild.id, ctx.author.id)  # type: ignore[union-attr]
        blocked = highlight.blocked  # type: ignore[union-attr]
        blocked.difference_update(entity.id for entity in entities)
        await highlight.update(blocked=blocked)  # type: ignore[union-attr]
        await ctx.send_success('Unblocked entities from triggering highlights.', ephemeral=True)

    @highlight_unblock.define_app_command()  # type: ignore[attr-defined]
    @describe(entity='The entity to unblock.')
    async def highlight_unblock_app_command(self, ctx: HybridContext, entity: str) -> None:
        await ctx.full_invoke(*validate_snowflakes(entity, guild=ctx.guild, to_obj=True))  # type: ignore[arg-type, misc]

    @highlight.command('list', aliases=['ls'], description='List all your highlights.')
    async def highlight_list(self, ctx: Context) -> None:
        highlight = await self.get_highlight_config(ctx.guild.id, ctx.author.id)  # type: ignore[union-attr]
        embed = discord.Embed(
            title='Triggers',
            color=helpers.Colour.white()
        )
        embed.set_author(name=ctx.author, icon_url=ctx.author.display_avatar.url)
        await LinePaginator.start(ctx, entries=highlight.lookup, location='description', embed=embed, numerate=True, ephemeral=True)  # type: ignore[union-attr]

    @highlight.command('blocked', description='List all blocked entities from triggering your highlights.')
    async def highlight_blocked(self, ctx: Context) -> None:
        highlight = await self.get_highlight_config(ctx.guild.id, ctx.author.id)  # type: ignore[union-attr]
        embed = discord.Embed(
            title='Blocked Entities',
            description='\n'.join(
                f'`{ctx.guild.get_member(user_id) or ctx.guild.get_channel(user_id)}`'  # type: ignore[union-attr]
                for user_id in highlight.blocked  # type: ignore[union-attr]
            ) or 'No blocked entities.',
            color=helpers.Colour.white()
        )
        await ctx.send(embed=embed, ephemeral=True)

    @highlight.command('import', description='Import highlights from another guild.')
    @describe(guild='The guild to import highlights from.')
    async def highlight_import(self, ctx: Context, guild: discord.Guild) -> None:
        other = await self.get_highlight_config(guild.id, ctx.author.id, initialize=False)
        if not other:
            await ctx.send_error('No highlights to import.')
            return

        highlight = await self.get_highlight_config(ctx.guild.id, ctx.author.id)  # type: ignore[union-attr]
        await highlight.update(lookup=highlight.lookup | other.lookup)  # type: ignore[union-attr]
        await ctx.send_success(f'Imported {len(other.lookup.difference(highlight.lookup))} highlights.', ephemeral=True)  # type: ignore[union-attr]

    @highlight_import.autocomplete('guild')  # type: ignore[attr-defined]
    async def highlight_import_guild_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        records = await self.bot.db.highlights.get_import_locations(
            interaction.user.id, interaction.guild.id)  # type: ignore[union-attr]
        if not records:
            return []

        guilds = [interaction.client.get_guild(record['location_id']) for record in records]
        results = fuzzy.finder(current, guilds, key=lambda x: x.name)  # type: ignore[union-attr]
        return [app_commands.Choice(name=guild.name, value=str(guild.id)) for guild in results if guild]  # type: ignore[union-attr]

    @Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """|coro|

        Check for highlights in a message and store them for later processing.

        Parameters
        ----------
        message : discord.Message
            The message to check for highlights.
        """
        if (
                message.guild is None
                or not isinstance(message.author, discord.Member)
                or message.author.bot
        ):
            return

        highlights = await self.get_guild_highlights(message.guild.id)
        for match in self.find_highlight(highlights, message):
            self._highlight_data_batch.setdefault(match.highlight.user_id, []).append(match)

    @Cog.listener()
    async def on_raw_member_remove(self, payload: discord.RawMemberRemoveEvent) -> None:
        """|coro|

        Remove highlights for a user when they leave the guild.

        Parameters
        ----------
        payload : discord.RawMemberRemoveEvent
            The raw member remove event.
        """
        highlight = await self.get_highlight_config(payload.guild_id, payload.user.id, initialize=False)
        if highlight:
            await highlight.delete()


async def setup(bot: Bot) -> None:
    await bot.add_cog(Highlights(bot))
