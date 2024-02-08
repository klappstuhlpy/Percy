from __future__ import annotations

import random
from typing import Optional, List, TYPE_CHECKING

import discord
from discord import app_commands, Interaction

from .reminder import Timer
from .utils import commands
from .utils.context import tick
from .utils.helpers import PostgresItem
from .utils.timetools import TimeTransformer

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
            raise AssertionError(f'{tick(False)} Polls cog is not loaded')

        giveaway = await cog.get_giveaway(int(match['id']))
        if not giveaway:
            await interaction.response.send_message(
                f'{tick(False)} The poll you are trying to vote on does not exist.', ephemeral=True)
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
        embed = self.message.embeds[0]
        guild = self.bot.get_guild(self.giveaway.guild_id)

        winner_list = []
        entries = self.giveaway.entries.copy()

        # Loop through the number of winners specified
        for _ in range(self.giveaway.winner_count):
            if entries:
                # If there are entries remaining, randomly select one and add it to the winner list
                user_id = entries.pop(random.randint(0, len(entries) - 1))
                winner_list.append(user_id)
            else:
                # If there are no more entries, fill the remaining slots with zeros
                winner_list.extend([0] * (self.giveaway.winner_count - len(winner_list)))
                break

        field = embed.fields[0]
        lines = field.value.split('\n')
        winners = ', '.join(guild.get_member(x).mention for x in winner_list if x != 0)
        lines[3] = f'Winner(s): {winners}'
        embed.set_field_at(0, name=field.name, value='\n'.join(lines))

        await self.message.reply(
            f'<a:giveaway:1089511337161400390> Congratulations **{winners}**! '
            f'You won the giveaway for *{self.giveaway.prize}*!',
            allowed_mentions=discord.AllowedMentions(users=True)
        )

        await interaction.response.edit_message(embed=embed, view=None)


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
            raise AssertionError(f'{tick(False)} Polls cog is not loaded')

        giveaway = await cog.get_giveaway(int(match['id']))
        if not giveaway:
            await interaction.response.send_message(
                f'{tick(False)} The poll you are trying to vote on does not exist.', ephemeral=True)
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
        self.giveaway.entries.append(interaction.user.id)
        query = "UPDATE giveaways SET entries = $1 WHERE id = $2;"
        await self.bot.pool.execute(query, self.giveaway.entries, self.giveaway.id)

        embed = interaction.message.embeds[0]
        field = embed.fields[0]
        lines = field.value.split('\n')
        lines[2] = f'Entries: **{self.giveaway.entry_count}**'
        embed.set_field_at(0, name=field.name, value='\n'.join(lines))

        await interaction.response.edit_message(embed=embed)
        await interaction.followup.send(
            f'{tick(True)} You have successfully entered this giveaway.',
            ephemeral=True
        )


class CreateGiveawayModal(discord.ui.Modal, title='Create a Giveaway'):
    duration = discord.ui.TextInput(label='Duration', placeholder='e.g. 10 minutes, 2 days')
    winner_count = discord.ui.TextInput(label='Winner Count', default='1')
    prize = discord.ui.TextInput(label='Prize', placeholder='Short description of the prize', max_length=256)
    description = discord.ui.TextInput(
        label='Description', placeholder='Additional information about the giveaway', style=discord.TextStyle.long,
        max_length=1024, required=False
    )

    def __init__(self, bot: Percy):
        super().__init__(timeout=120.0)
        self.bot: Percy = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            when = await TimeTransformer(future=True).transform(interaction, self.duration.value)
        except commands.BadArgument:
            return await interaction.response.send_message(
                '<:redTick:1079249771975413910> Duration could not be parsed. Try something like "5 minutes" or "1 hour"',
                ephemeral=True
            )

        embed = discord.Embed(title=self.prize.value, timestamp=when, color=discord.Color.blurple())
        if value := self.description.value:
            embed.description = value

        embed.add_field(
            name='\u200c',
            value=f'Ends: {discord.utils.format_dt(when, style='R')} ({discord.utils.format_dt(when, style='F')})\n'
                  f'Hosted by: {interaction.user.mention}\n'
                  f'Entries: **0**\n'
                  f'Winner(s): {self.winner_count.value}')

        msg = await interaction.channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(roles=True))

        cog: Giveaways = self.bot.get_cog('Giveaways')  # type: ignore
        giveaway = await cog.create_giveaway(
            interaction.channel.id,
            msg.id,
            interaction.guild.id,
            interaction.user.id,
            self.description.value,
            self.prize.value,
            int(self.winner_count.value),
        )

        reminder = self.bot.reminder
        if reminder is None:
            await interaction.response.send_message(
                '<:redTick:1079249771975413910> Sorry, this functionality is currently unavailable. Try again later?')
        else:
            await reminder.create_timer(
                when,
                'giveaway',
                giveaway_id=giveaway.id,
                created=discord.utils.utcnow(),
                timezone='UTC',
            )

        view = discord.ui.View(timeout=None)
        view.add_item(GiveawayEnterButton(giveaway))
        await msg.edit(view=view)

        await interaction.response.send_message(
            f'<:greenTick:1079249732364406854> Giveaway [`{giveaway.id}`] successfully created. {msg.jump_url}',
            ephemeral=True
        )


class Giveaway(PostgresItem):
    """Represents a giveaway item."""

    id: int
    channel_id: int
    message_id: int
    guild_id: int
    author_id: int
    prize: str
    description: str
    winner_count: int
    entries: List[int]

    __slots__ = ('id', 'channel_id', 'message_id', 'guild_id', 'author_id', 'prize', 'description', 'winner_count', 'entries')

    @property
    def jump_url(self) -> Optional[str]:
        if self.message_id and self.channel_id:
            guild = self.guild_id or '@me'
            return f'https://discord.com/channels/{guild}/{self.channel_id}/{self.message_id}'
        return None

    @property
    def entry_count(self) -> int:
        return len(self.entries) or 0

    async def message(self, guild: discord.Guild) -> Optional[discord.Message]:
        if self.message_id and self.channel_id:
            channel = guild.get_channel(self.channel_id)
            if channel:
                return await channel.fetch_message(self.message_id)
        return None


class Giveaways(commands.Cog):
    """Create Giveaways using Modals."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        bot.add_dynamic_items(GiveawayEnterButton, GiveawayRerollButton)

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='giveaway', id=1089511337161400390, animated=True)

    async def create_giveaway(
            self,
            channel_id: int,
            message_id: int,
            guild_id: int,
            author_id: int,
            description: str,
            prize: str,
            winner_count: int
    ) -> Giveaway:
        """Creates a giveaway.
        Parameters
        -----------
        channel_id: :class:`int`
            The channel ID of the poll.
        message_id: :class:`int`
            The message ID of the poll.
        guild_id: :class:`int`
            The guild ID of the poll.
        author_id: :class:`int`
            The author ID of the poll.
        description: :class:`str`
            The description of the giveaway.
        prize: :class:`str`
            The prize of the giveaway.
        winner_count: :class:`int`
            The number of winners of the giveaway.
        Note
        ------
        Arguments and keyword arguments must be JSON serializable.
        """
        giveaway = Giveaway.temporary(
            channel_id=channel_id,
            message_id=message_id,
            guild_id=guild_id,
            author_id=author_id,
            prize=prize,
            description=description,
            winner_count=winner_count,
            entries=[]
        )

        query = """
            INSERT INTO giveaways (channel_id, message_id, guild_id, author_id, prize, description, winner_count)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id;
        """

        giveaway.id = await self.bot.pool.fetchval(
            query, channel_id, message_id, guild_id, author_id, prize, description, winner_count)
        return giveaway

    async def delete_giveaway(self, giveaway_id: int) -> str:
        """Deletes a giveaway from the database."""
        query = "DELETE FROM giveaways WHERE id = $1;"
        return await self.bot.pool.execute(query, giveaway_id)

    async def get_giveaway(self, giveaway_id: int) -> Optional[Giveaway]:
        """Gets a giveaways from the database."""
        query = "SELECT * FROM giveaways WHERE id = $1 LIMIT 1;"
        record = await self.bot.pool.fetchrow(query, giveaway_id)
        return Giveaway(record=record) if record else None

    giveaway = app_commands.Group(
        name='giveaway', description='Manage giveaways.',
        default_permissions=discord.Permissions(manage_channels=True), guild_only=True)

    @commands.command(
        giveaway.command,
        name='create',
        description='Create a giveaway.',
    )
    @app_commands.guild_only()
    @commands.permissions(user=['ban_members', 'manage_messages'])
    async def make_giveaway(self, interaction: discord.Interaction):
        """Interactively creates a giveaway using a Modal."""
        await interaction.response.send_modal(CreateGiveawayModal(self.bot))

    @commands.Cog.listener()
    async def on_giveaway_timer_complete(self, timer: Timer):
        await self.bot.wait_until_ready()
        _id = timer.kwargs.get('giveaway_id')

        giveaway = await self.get_giveaway(giveaway_id=_id)
        channel = self.bot.get_channel(giveaway.channel_id)
        message = await channel.fetch_message(giveaway.message_id)

        await self.delete_giveaway(giveaway_id=_id)

        embed = message.embeds[0]
        guild = self.bot.get_guild(giveaway.guild_id)

        winner_list = []
        if giveaway.entries:
            entries = giveaway.entries.copy()
            for _ in range(giveaway.winner_count):
                if len(entries) == 0:
                    # Assuming that there are more possible winners than entries
                    winner_list.extend([0 for _ in range(giveaway.winner_count - len(winner_list))])
                    break
                user_id = entries.pop(random.randint(0, len(entries) - 1))
                winner_list.append(user_id)

        field = embed.fields[0]
        lines = field.value.split('\n')
        lines[0] = lines[0].replace('Ends', 'Ended')
        winners = ', '.join(guild.get_member(x).mention for x in winner_list if x != 0)
        lines[3] = f'Winner(s): {winners}'
        embed.set_field_at(0, name=field.name, value='\n'.join(lines))

        embed.set_footer(text=f'Giveaway ended')

        if len(giveaway.entries) > 0 and any(winner != 0 for winner in winner_list):
            view = discord.ui.View(timeout=None)
            view.add_item(GiveawayRerollButton(giveaway))
            await message.edit(embed=embed, view=view)
            await message.reply(f'{self.display_emoji} Congratulations **{winners}**! You won the giveaway for *{giveaway.prize}*!',
                                allowed_mentions=discord.AllowedMentions(users=True))
        else:
            await message.edit(embed=embed, view=None)
            await message.reply(f'No winners were determined for *{giveaway.prize}*.')


async def setup(bot: Percy):
    await bot.add_cog(Giveaways(bot))
