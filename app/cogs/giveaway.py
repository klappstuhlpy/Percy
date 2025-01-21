from __future__ import annotations

import datetime
import random
import re
from typing import TYPE_CHECKING, Any

import discord
from discord import Interaction, app_commands
from discord.ext import commands
from discord.utils import MISSING

from app.core import Bot, Cog, Flags, flag
from app.core.models import Context, PermissionTemplate, describe, group
from app.database import BaseRecord
from app.utils import checks, fuzzy, get_shortened_string, helpers, timetools
from config import Emojis

if TYPE_CHECKING:
    from app.core.timer import Timer


class GiveawayCreateFlags(Flags):
    winners: commands.Range[int, 1] = flag(aliases=('winner', 'win'), short='w', default=1)
    description: str = flag(aliases=('msg', 'description', 'desc', 'comment'), short='d')
    channel: discord.TextChannel = flag(aliases=('chan', 'c'), short='ch')


class GiveawayRerollButton(discord.ui.DynamicItem[discord.ui.Button], template=r'giveaway:reroll:(?P<id>[0-9]+)'):
    def __init__(self, giveaway: Giveaway | None) -> None:
        self.giveaway: Giveaway = giveaway
        super().__init__(
            discord.ui.Button(
                label='Reroll',
                style=discord.ButtonStyle.gray,
                emoji='\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}',
                custom_id=f'giveaway:reroll:{giveaway.id}'
            )
        )

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Bot], _, match: re.Match[str], /
    ) -> GiveawayRerollButton:
        cog: Giveaways | None = interaction.client.get_cog('Giveaways')
        if cog is None:
            await interaction.response.send_message(
                f'{Emojis.error} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{Emojis.error} Giveaways cog is not loaded')

        giveaway = await cog.get_giveaway(int(match['id']))
        if giveaway is None:
            await interaction.response.send_message(
                f'{Emojis.error} The giveaway you are trying to vote on does not exist.', ephemeral=True)
            return cls(None)

        return cls(giveaway)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.giveaway is None:
            await interaction.response.send_message(f'{Emojis.error} Giveaway was not found.', ephemeral=True)
            return False

        if interaction.user.id != self.giveaway.author_id:
            await interaction.response.send_message(
                f'{Emojis.error} You are not allowed to reroll this giveaway. Only the author can do this.',
                ephemeral=True)
            return False

        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        winners = await self.giveaway.get_winners()
        await interaction.response.edit_message(
            f'{Emojis.giveaway} Congratulations **{', '.join(x.mention for x in winners)}**! '
            f'You won the giveaway for *{self.giveaway.prize}*!',
            allowed_mentions=discord.AllowedMentions(users=True), view=None
        )
        await self.giveaway.message.edit(embed=self.giveaway.to_embed(winners), view=None)


class GiveawayEnterButton(discord.ui.DynamicItem[discord.ui.Button], template=r'giveaway:enter:(?P<id>[0-9]+)'):
    def __init__(self, giveaway: Giveaway | None) -> None:
        self.giveaway: Giveaway = giveaway
        super().__init__(
            discord.ui.Button(
                label='Enter',
                style=discord.ButtonStyle.green,
                emoji=Emojis.giveaway,
                custom_id=f'giveaway:enter:{giveaway.id}'
            )
        )

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Bot], _, match: re.Match[str], /
    ) -> GiveawayEnterButton:
        cog: Giveaways | None = interaction.client.get_cog('Giveaways')
        if cog is None:
            await interaction.response.send_message(
                f'{Emojis.error} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{Emojis.error} Giveaways cog is not loaded')

        giveaway = await cog.get_giveaway(int(match['id']))
        if giveaway is None:
            await interaction.response.send_message(
                f'{Emojis.error} The giveaway you are trying to vote on does not exist.', ephemeral=True)
            return cls(None)

        return cls(giveaway)

    async def interaction_check(self, interaction: discord.Interaction[Bot], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.giveaway is None:
            await interaction.response.send_message(f'{Emojis.error} Giveaway was not found.', ephemeral=True)
            return False

        if interaction.user.id in self.giveaway.entries:
            await interaction.response.send_message(
                f'{Emojis.error} You have already entered this giveaway.',
                ephemeral=True)
            return False

        return True

    async def callback(self, interaction: Interaction) -> None:
        self.giveaway.entries.add(interaction.user.id)
        query = "UPDATE giveaways SET entries = $1 WHERE id = $2;"
        await interaction.client.db.execute(query, self.giveaway.entries, self.giveaway.id)

        if self.giveaway.message is MISSING:
            await self.giveaway.fetch_message()

        await interaction.response.edit_message(embed=self.giveaway.to_embed())
        await interaction.followup.send(
            f'{Emojis.success} You have successfully entered this giveaway.',
            ephemeral=True)


class Giveaway(BaseRecord):
    """Represents a giveaway item."""

    cog: Giveaways
    id: int
    channel_id: int
    message_id: int
    guild_id: int
    author_id: int
    entries: set[int]
    metadata: dict[str, Any]

    __slots__ = (
        'id', 'channel_id', 'message_id', 'guild_id', 'author_id', 'metadata',
        'entries', 'args', 'kwargs', 'entries', 'cog', 'bot', 'message',
        'prize', 'description', 'winner_count'
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)

        self.args: list[Any] = self.metadata.get('args', [])
        self.kwargs: dict[str, Any] = self.metadata.get('kwargs', {})

        self.message: discord.Message = MISSING

        self.prize = self.kwargs.get('prize')
        self.description = self.kwargs.get('description')
        self.winner_count = self.kwargs.get('winner_count', 0)

        self.entries = set(self.entries or [])

    @property
    def choice_text(self) -> str:
        """The text to use for the autocomplete."""
        return f'[{self.id}] {self.prize}'

    @property
    def guild(self) -> discord.Guild | None:
        """The guild of the giveaway."""
        return self.bot.get_guild(self.guild_id)

    @property
    def jump_url(self) -> str | None:
        """The jump URL for the giveaway message."""
        if self.message_id and self.channel_id:
            guild = self.guild_id or '@me'
            return f'https://discord.com/channels/{guild}/{self.channel_id}/{self.message_id}'
        return None

    @property
    def entry_count(self) -> int:
        """The number of entries in the giveaway."""
        return len(self.entries) or 0

    @property
    def created(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(self.kwargs.get('created'))

    @property
    def expires(self) -> datetime.datetime:
        return datetime.datetime.fromisoformat(self.kwargs.get('expires'))

    def to_embed(self, winners: list[discord.Member] | None = None) -> discord.Embed:
        """Creates an embed for the giveaway.

        Parameters
        -----------
        winners: List[:class:`discord.Member`]
            The winners of the giveaway.

        Returns
        --------
        :class:`discord.Embed`
            The embed for the giveaway.
        """
        embed = discord.Embed(
            title=self.prize,
            description=self.description,
            timestamp=self.expires,
            color=helpers.Colour.white()
        )

        text_parts = []

        is_ended = self.expires < discord.utils.utcnow()
        prefix = 'Ended' if is_ended else 'Ends'
        text_parts.append(
            f'{prefix}: {discord.utils.format_dt(self.expires, style="R")} ({discord.utils.format_dt(self.expires, style="F")})')

        text_parts.append(f'Hosted by: <@{self.author_id}>')
        text_parts.append(f'Entries: **{self.entry_count}**')

        if winners is not None:
            winners = ', '.join(x.mention for x in winners)
            text_parts.append(f'Winner(s): {winners}')
        else:
            text_parts.append(f'Winner(s): {self.winner_count}')

        embed.add_field(
            name='\u200c',
            value='\n'.join(text_parts)
        )

        return embed

    async def fetch_message(self) -> discord.Message | None:
        """Fetches the giveaway message."""
        if self.message_id and self.channel_id:
            guild = self.bot.get_guild(self.guild_id)
            if guild:
                channel = guild.get_channel(self.channel_id)
                if channel:
                    self.message = await channel.fetch_message(self.message_id)
        return self.message

    async def get_winners(self) -> list[discord.Member]:
        """Gets the winners of the giveaway."""
        winners = []
        for _ in range(self.winner_count):
            if not self.entries:
                break
            user_id = random.choice(list(self.entries))
            self.entries.remove(user_id)
            member = self.guild.get_member(user_id)
            if member:
                winners.append(member)

        query = "UPDATE giveaways SET entries = $1 WHERE id = $2;"
        await self.bot.db.execute(query, self.entries, self.id)
        return winners


class Giveaways(Cog):
    """Create Giveaways using Modals."""

    emoji = Emojis.giveaway

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        bot.add_dynamic_items(GiveawayEnterButton, GiveawayRerollButton)

    async def giveaway_id_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        polls = await self.get_guild_giveaways(interaction.guild.id)
        results = fuzzy.finder(current, polls, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, giveaway.choice_text), value=giveaway.id)
            for length, start, giveaway in results[:20]]

    async def get_giveaway(self, giveaway_id: int) -> Giveaway | None:
        """|coro|

        Gets a giveaways from the database.

        Parameters
        -----------
        giveaway_id: :class:`int`
            The ID of the giveaway to get.

        Returns
        --------
        :class:`Giveaway`
            The giveaway if found, else ``None``.
        """
        query = "SELECT * FROM giveaways WHERE id = $1 LIMIT 1;"
        record = await self.bot.db.fetchrow(query, giveaway_id)
        giveaway = Giveaway(cog=self, record=record) if record else None
        return giveaway

    async def get_guild_giveaway(self, guild_id: int, giveaway_id: int) -> Giveaway | None:
        """|coro|

        Gets a giveaway from the database.

        Parameters
        -----------
        guild_id: :class:`int`
            The ID of the guild to get the giveaway from.
        giveaway_id: :class:`int`
            The ID of the giveaway to get.

        Returns
        --------
        class:`Giveaway`
            The giveaway if found, else ``None``.
        """
        query = "SELECT * FROM giveaways WHERE guild_id = $1 AND id = $2 LIMIT 1;"
        record = await self.bot.db.fetchrow(query, guild_id, giveaway_id)
        giveaway = Giveaway(cog=self, record=record) if record else None
        return giveaway

    async def get_guild_giveaways(self, guild_id: int) -> list[Giveaway]:
        """|coro|

        Gets all the giveaways in a guild.

        Parameters
        -----------
        guild_id: :class:`int`
            The ID of the guild to get the giveaways from.

        Returns
        --------
        List[:class:`Giveaway`]
            The giveaways in the guild.
        """
        query = "SELECT * FROM giveaways WHERE guild_id = $1;"
        records = await self.bot.db.fetch(query, guild_id)
        return [Giveaway(cog=self, record=record) for record in records]

    async def create_giveaway(
            self,
            channel_id: int,
            message_id: int,
            guild_id: int,
            author_id: int,
            /,
            *args: Any,
            **kwargs: Any,
    ) -> Giveaway:
        r"""|coro|

        Creates a giveaway.

        Parameters
        -----------
        channel_id: :class:`int`
            The channel ID of the giveaway.
        message_id: :class:`int`
            The message ID of the giveaway.
        guild_id: :class:`int`
            The guild ID of the giveaway.
        author_id: :class:`int`
            The author ID of the giveaway.
        \*args: :class:`Any`
            The arguments to pass to the giveaway.
        \*\*kwargs: :class:`Any`
            The keyword arguments to pass to the giveaway.

        Note
        ------
        Arguments and keyword arguments must be JSON serializable.
        """
        giveaway = Giveaway.temporary(
            cog=self,
            channel_id=channel_id,
            message_id=message_id,
            guild_id=guild_id,
            author_id=author_id,
            entries=set(),
            metadata={'args': args, 'kwargs': kwargs}
        )

        query = """
            INSERT INTO giveaways (channel_id, message_id, guild_id, author_id, metadata)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id;
        """
        giveaway.id = await self.bot.db.fetchval(
            query, channel_id, message_id, guild_id, author_id, {'args': args, 'kwargs': kwargs})
        return giveaway

    async def end_giveaway(self, giveaway_id: int) -> None:
        """|coro|

        Ends a giveaway by deleting the timer and manually dispatching the event.

        Parameters
        -----------
        giveaway_id: :class:`int`
            The giveaway id to delete.
        """
        timer = await self.bot.timers.fetch('giveaway', giveaway_id=str(giveaway_id))
        self.bot.dispatch('giveaway_timer_complete', timer)
        await self.bot.timers.delete('giveaway', giveaway_id=str(giveaway_id))

    @group(
        'giveaway',
        description='Manage giveaways.',
        guild_only=True,
        hybrid=True
    )
    async def giveaway(self, ctx: Context) -> None:
        """Manage giveaways."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @giveaway.command(
        'create',
        description='Create a giveaway.',
        guild_only=True,
        user_permissions=PermissionTemplate.mod
    )
    @checks.requires_timer()
    @describe(when='When the giveaway ends.', prize='The prize for the giveaway.')
    async def giveaway_create(
            self,
            ctx: Context,
            when: timetools.FutureTime,
            *,
            prize: str,
            flags: GiveawayCreateFlags
    ) -> None:
        """Interactively creates a giveaway using a Modal."""
        channel = flags.channel or ctx.channel
        message = await channel.send(embed=discord.Embed(description='*Preparing Giveaway...*'))

        cog: Giveaways | None = self.bot.get_cog('Giveaways')
        giveaway = await cog.create_giveaway(
            message.channel.id,
            message.id,
            ctx.guild.id,
            ctx.user.id,
            description=flags.description,
            prize=prize,
            winner_count=flags.winners,
            created=discord.utils.utcnow().isoformat(),
            expires=when.dt.isoformat()
        )

        zone = await self.bot.db.get_user_timezone(ctx.author.id)
        await self.bot.timers.create(
            when.dt,
            'giveaway',
            giveaway_id=giveaway.id,
            created=discord.utils.utcnow(),
            timezone=zone or 'UTC',
        )

        view = discord.ui.View(timeout=None)
        view.add_item(GiveawayEnterButton(giveaway))
        await message.edit(embed=giveaway.to_embed(), view=view)

        await ctx.send_success(
            f'Giveaway [`{giveaway.id}`] successfully created. {message.jump_url}', ephemeral=True)

    @giveaway.command(
        'end',
        description='End a giveaway.',
        guild_only=True,
        user_permissions=PermissionTemplate.mod
    )
    @app_commands.autocomplete(giveaway_id=giveaway_id_autocomplete)
    @describe(giveaway_id='The ID of the giveaway to end.')
    async def giveaway_end(self, ctx: Context, giveaway_id: int) -> None:
        """Ends a giveaway."""
        await ctx.defer()

        giveaway = await self.get_guild_giveaway(ctx.guild.id, giveaway_id)
        if giveaway is None:
            raise commands.BadArgument(f'Giveaway with ID `{giveaway_id}` was not found.')

        await self.end_giveaway(giveaway.id)
        await ctx.send_success(f'Giveaway [`{giveaway.id}`] has been ended manually.')

    @Cog.listener()
    async def on_giveaway_timer_complete(self, timer: Timer) -> None:
        """|coro|

        Called when a giveaway timer completes.

        Parameters
        -----------
        timer: :class:`Timer`
            The timer that completed.
        """
        await self.bot.wait_until_ready()
        _id = timer.kwargs.get('giveaway_id')

        giveaway = await self.get_giveaway(_id)
        # Set the expiry time manually to the current one,
        # to make sure the time is correct (important for manual ending)
        giveaway.kwargs['expires'] = discord.utils.utcnow().isoformat()

        if giveaway.message is MISSING:
            await giveaway.fetch_message()

        query = "DELETE FROM giveaways WHERE id = $1;"
        await self.bot.db.execute(query, giveaway.id)

        winners = await giveaway.get_winners()
        await giveaway.message.edit(embed=giveaway.to_embed(winners), view=None)

        if len(winners) > 0:
            view = None
            if len(giveaway.entries) > 0:
                view = discord.ui.View(timeout=None)
                view.add_item(GiveawayRerollButton(giveaway))

            winners = ', '.join(x.mention for x in winners)
            await giveaway.message.reply(
                f'{Emojis.giveaway} Congratulations **{winners}**! '
                f'You won the giveaway for *{giveaway.prize}*!',
                view=view
            )
        else:
            await giveaway.message.reply(f'{Emojis.error} No winners were determined for *{giveaway.prize}*.')


async def setup(bot: Bot) -> None:
    await bot.add_cog(Giveaways(bot))
