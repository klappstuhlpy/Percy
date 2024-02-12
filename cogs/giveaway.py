from __future__ import annotations

import datetime
import random
from typing import Optional, List, TYPE_CHECKING, Any

import discord
from discord import app_commands, Interaction
from discord.utils import MISSING

from .reminder import Timer
from .utils import commands, fuzzy
from .utils.context import tick
from .utils.formats import get_shortened_string
from .utils.helpers import PostgresItem
from .utils.timetools import TimeTransformer, BadTimeTransform

if TYPE_CHECKING:
    from bot import Percy


class GiveawayRerollButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'giveaway:reroll:(?P<id>[0-9]+)',
):
    def __init__(self, giveaway: Giveaway) -> None:
        self.giveaway: Giveaway = giveaway
        super().__init__(
            discord.ui.Button(
                label='Reroll',
                style=discord.ButtonStyle.gray,
                emoji=discord.PartialEmoji(
                    name='\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}'),
                custom_id=f'giveaway:reroll:{giveaway.id}'
            )
        )

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Percy], item: discord.ui.Button, match: re.Match[str], /  # noqa
    ):
        cog: Optional[Giveaways] = interaction.client.get_cog('Giveaways')
        if cog is None:
            await interaction.response.send_message(
                f'{tick(False)} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{tick(False)} Giveaways cog is not loaded')

        giveaway = await cog.get_giveaway(int(match['id']))
        if not giveaway:
            await interaction.response.send_message(
                f'{tick(False)} The giveaway you are trying to vote on does not exist.', ephemeral=True)
            return

        return cls(giveaway)

    async def interaction_check(self, interaction: discord.Interaction[Percy], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.giveaway is None:
            await interaction.response.send_message(f'{tick(False)} Giveaway was not found.', ephemeral=True)
            return False

        if interaction.user.id != self.giveaway.author_id:
            await interaction.response.send_message(
                f'{tick(False)} You are not allowed to reroll this giveaway. Only the author can do this.',
                ephemeral=True)
            return False

        return True

    async def callback(self, interaction: discord.Interaction):
        winners = await self.giveaway.get_winners()
        await interaction.response.edit_message(
            f'<a:giveaway:1089511337161400390> Congratulations **{', '.join(x.mention for x in winners)}**! '
            f'You won the giveaway for *{self.giveaway.prize}*!',
            allowed_mentions=discord.AllowedMentions(users=True), view=None
        )
        await self.giveaway.message.edit(embed=self.giveaway.to_embed(winners), view=None)


class GiveawayEnterButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'giveaway:enter:(?P<id>[0-9]+)',
):
    def __init__(self, giveaway: Giveaway) -> None:
        self.giveaway: Giveaway = giveaway
        super().__init__(
            discord.ui.Button(
                label='Enter',
                style=discord.ButtonStyle.green,
                emoji=discord.PartialEmoji(name='giveaway', id=1089511337161400390, animated=True),
                custom_id=f'giveaway:enter:{giveaway.id}'
            )
        )

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Percy], item: discord.ui.Button, match: re.Match[str], /  # noqa
    ):
        cog: Optional[Giveaways] = interaction.client.get_cog('Giveaways')
        if cog is None:
            await interaction.response.send_message(
                f'{tick(False)} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{tick(False)} Giveaways cog is not loaded')

        giveaway = await cog.get_giveaway(int(match['id']))
        if not giveaway:
            await interaction.response.send_message(
                f'{tick(False)} The giveaway you are trying to vote on does not exist.', ephemeral=True)
            return

        return cls(giveaway)

    async def interaction_check(self, interaction: discord.Interaction[Percy], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.giveaway is None:
            await interaction.response.send_message(f'{tick(False)} Giveaway was not found.', ephemeral=True)
            return False

        if interaction.user.id in self.giveaway.entries:
            await interaction.response.send_message(
                f'{tick(False)} You have already entered this giveaway.',
                ephemeral=True)
            return False

        return True

    async def callback(self, interaction: Interaction) -> None:
        self.giveaway.entries.add(interaction.user.id)
        query = "UPDATE giveaways SET entries = $1 WHERE id = $2;"
        await interaction.client.pool.execute(query, self.giveaway.entries, self.giveaway.id)

        if self.giveaway.message is MISSING:
            await self.giveaway.fetch_message()

        await interaction.response.edit_message(embed=self.giveaway.to_embed())
        await interaction.followup.send(
            f'{tick(True)} You have successfully entered this giveaway.',
            ephemeral=True)


class CreateGiveawayModal(discord.ui.Modal, title='Create a Giveaway'):
    duration = discord.ui.TextInput(label='Duration', placeholder='e.g. 10 minutes, 2 days')
    winner_count = discord.ui.TextInput(label='Winner Count', default='1')
    prize = discord.ui.TextInput(label='Prize', placeholder='Short description of the prize', max_length=256)
    description = discord.ui.TextInput(
        label='Description', placeholder='Additional information about the giveaway', style=discord.TextStyle.long,
        max_length=1024, required=False
    )

    def __init__(self, bot: Percy, channel: discord.TextChannel):
        super().__init__(timeout=120.0)
        self.bot: Percy = bot
        self.channel: discord.TextChannel = channel

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            when = await TimeTransformer().transform(interaction, self.duration.value)
        except BadTimeTransform:
            return await interaction.response.send_message(
                f'{tick(False)} Duration could not be parsed. Try something like "5 minutes" or "1 hour"',
                ephemeral=True)

        try:
            winner_count = int(self.winner_count.value)
        except ValueError:
            return await interaction.response.send_message(
                f'{tick(False)} Winner count must be a number.', ephemeral=True)

        if winner_count < 1:
            return await interaction.response.send_message(
                f'{tick(False)} Winner count must be at least `1`.', ephemeral=True)

        message = await self.channel.send(embed=discord.Embed(description='*Preparing Giveaway...*'))

        cog: Optional[Giveaways] = self.bot.get_cog('Giveaways')
        giveaway = await cog.create_giveaway(
            message.channel.id,
            message.id,
            interaction.guild.id,
            interaction.user.id,
            description=self.description.value,
            prize=self.prize.value,
            winner_count=winner_count,
            created=discord.utils.utcnow().isoformat(),
            expires=when.isoformat()
        )

        reminder = self.bot.reminder
        if reminder is None:
            return await interaction.response.send_message(
                f'{tick(False)} Sorry, this functionality is currently unavailable. Try again later?',
                ephemeral=True)
        else:
            uconfig = await self.bot.user_settings.get_user_config(interaction.user.id)
            zone = uconfig.timezone if uconfig else None
            await reminder.create_timer(
                when,
                'giveaway',
                giveaway_id=giveaway.id,
                created=discord.utils.utcnow(),
                timezone=zone or 'UTC',
            )

        view = discord.ui.View(timeout=None)
        view.add_item(GiveawayEnterButton(giveaway))
        await message.edit(embed=giveaway.to_embed(), view=view)

        await interaction.response.send_message(
            f'{tick(True)} Giveaway [`{giveaway.id}`] successfully created. {message.jump_url}',
            ephemeral=True
        )


class Giveaway(PostgresItem):
    """Represents a giveaway item."""

    id: int
    channel_id: int
    message_id: int
    guild_id: int
    author_id: int
    entries: set[int]
    extra: dict[str, Any]

    __slots__ = (
        'id', 'channel_id', 'message_id', 'guild_id', 'author_id', 'extra',
        'entries', 'args', 'kwargs', 'entries', 'cog', 'bot', 'message',
        'prize', 'description', 'winner_count'
    )

    def __init__(self, cog: Giveaways, **kwargs):
        self.cog: Giveaways = cog
        self.bot: Percy = cog.bot
        super().__init__(**kwargs)

        self.args: List[Any] = self.extra.get('args', [])
        self.kwargs: dict[str, Any] = self.extra.get('kwargs', {})

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
    def guild(self) -> Optional[discord.Guild]:
        """The guild of the giveaway."""
        return self.bot.get_guild(self.guild_id)

    @property
    def jump_url(self) -> Optional[str]:
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

    def to_embed(self, winners: Optional[list[discord.Member]] = None) -> discord.Embed:
        """Creates an embed for the giveaway.

        Parameters
        -----------
        winners: Optional[List[:class:`discord.Member`]]
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
            color=discord.Color.blurple()
        )

        text_parts = []

        is_ended = self.expires < discord.utils.utcnow()
        prefix = 'Ended' if is_ended else 'Ends'
        text_parts.append(f'{prefix}: {discord.utils.format_dt(self.expires, style="R")} ({discord.utils.format_dt(self.expires, style="F")})')

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

    async def fetch_message(self) -> Optional[discord.Message]:
        """Fetches the giveaway message."""
        if self.message_id and self.channel_id:
            guild = self.bot.get_guild(self.guild_id)
            if guild:
                channel = guild.get_channel(self.channel_id)
                if channel:
                    self.message = await channel.fetch_message(self.message_id)
        return self.message

    async def get_winners(self) -> List[discord.Member]:
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
        await self.bot.pool.execute(query, self.entries, self.id)
        return winners


class Giveaways(commands.Cog):
    """Create Giveaways using Modals."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        bot.add_dynamic_items(GiveawayEnterButton, GiveawayRerollButton)

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='giveaway', id=1089511337161400390, animated=True)

    async def giveaway_id_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        polls = await self.get_guild_giveaways(interaction.guild.id)
        results = fuzzy.finder(current, polls, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, giveaway.choice_text), value=giveaway.id)
            for length, start, giveaway in results[:20]]

    async def get_giveaway(self, giveaway_id: int) -> Optional[Giveaway]:
        """|coro|

        Gets a giveaways from the database.

        Parameters
        -----------
        giveaway_id: :class:`int`
            The ID of the giveaway to get.

        Returns
        --------
        Optional[:class:`Giveaway`]
            The giveaway if found, else ``None``.
        """
        query = "SELECT * FROM giveaways WHERE id = $1 LIMIT 1;"
        record = await self.bot.pool.fetchrow(query, giveaway_id)
        giveaway = Giveaway(self, record=record) if record else None
        return giveaway

    async def get_guild_giveaway(self, guild_id: int, giveaway_id: int) -> Optional[Giveaway]:
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
        Optional[:class:`Giveaway`]
            The giveaway if found, else ``None``.
        """
        query = "SELECT * FROM giveaways WHERE guild_id = $1 AND id = $2 LIMIT 1;"
        record = await self.bot.pool.fetchrow(query, guild_id, giveaway_id)
        giveaway = Giveaway(self, record=record) if record else None
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
        records = await self.bot.pool.fetch(query, guild_id)
        return [Giveaway(self, record=record) for record in records]

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
        """|coro|

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
            self,
            channel_id=channel_id,
            message_id=message_id,
            guild_id=guild_id,
            author_id=author_id,
            entries=set(),
            extra={'args': args, 'kwargs': kwargs}
        )

        query = """
            INSERT INTO giveaways (channel_id, message_id, guild_id, author_id, extra)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id;
        """
        giveaway.id = await self.bot.pool.fetchval(
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
        timer = await self.bot.reminder.get_timer('giveaway', giveaway_id=str(giveaway_id))
        self.bot.dispatch('giveaway_timer_complete', timer)
        await self.bot.reminder.delete_timer('giveaway', giveaway_id=str(giveaway_id))

    giveaway = app_commands.Group(name='giveaway', description='Manage giveaways.', guild_only=True)

    @commands.command(
        giveaway.command,
        name='create',
        description='Create a giveaway.',
        guild_only=True
    )
    @app_commands.describe(channel='The channel to create the giveaway in.')
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def giveaway_create(self, interaction: discord.Interaction, *, channel: Optional[discord.TextChannel] = None):
        """Interactively creates a giveaway using a Modal."""
        channel = channel or interaction.channel
        await interaction.response.send_modal(CreateGiveawayModal(self.bot, channel))

    @commands.command(
        giveaway.command,
        name='end',
        description='End a giveaway.',
        guild_only=True
    )
    @app_commands.autocomplete(giveaway_id=giveaway_id_autocomplete)
    @app_commands.describe(giveaway_id='The ID of the giveaway to end.')
    @commands.permissions(user=commands.PermissionTemplate.mod)
    async def giveaway_end(self, interaction: discord.Interaction, giveaway_id: int):
        """Ends a giveaway."""
        await interaction.response.defer()

        giveaway = await self.get_guild_giveaway(interaction.guild.id, giveaway_id)
        if giveaway is None:
            return await interaction.followup.send(f'{tick(False)} Giveaway not found.', ephemeral=True)

        await self.end_giveaway(giveaway.id)
        await interaction.followup.send(f'{tick(True)} Giveaway [`{giveaway.id}`] has been ended manually.')

    @commands.Cog.listener()
    async def on_giveaway_timer_complete(self, timer: Timer):
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
        await self.bot.pool.execute(query, giveaway.id)

        winners = await giveaway.get_winners()
        await giveaway.message.edit(embed=giveaway.to_embed(winners), view=None)

        if len(winners) > 0:
            view = None
            if len(giveaway.entries) > 0:
                view = discord.ui.View(timeout=None)
                view.add_item(GiveawayRerollButton(giveaway))

            winners = ', '.join(x.mention for x in winners)
            await giveaway.message.reply(
                f'<a:giveaway:1089511337161400390> Congratulations **{winners}**! '
                f'You won the giveaway for *{giveaway.prize}*!',
                allowed_mentions=discord.AllowedMentions(users=True),
                view=view
            )
        else:
            await giveaway.message.reply(f'{tick(None)} No winners were determined for *{giveaway.prize}*.')


async def setup(bot: Percy):
    await bot.add_cog(Giveaways(bot))
