from __future__ import annotations

import random
from typing import Optional, List, TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands
from discord.utils import MISSING

from .reminder import Timer
from .utils import commands_ext
from .utils.helpers import PostgresItem
from .utils.timetools import TimeTransformer

if TYPE_CHECKING:
    from bot import Percy


class GiveawayItem(PostgresItem):
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


class GiveawayRerollView(discord.ui.View):
    def __init__(self, bot: Percy, cog: Giveaway, giveaway: GiveawayItem):
        super().__init__(timeout=None)
        self.giveaway: GiveawayItem = giveaway
        self.cog: Giveaway = cog
        self.bot: Percy = bot
        self.message: discord.Message = MISSING

        class RerollButton(discord.ui.Button):
            def __init__(self):
                self.giveaway: GiveawayItem = giveaway
                self.cog: Giveaway = cog
                self.bot = bot
                self.message: discord.Message = MISSING

                super().__init__(label="Reroll", style=discord.ButtonStyle.gray,
                                 emoji=discord.PartialEmoji(
                                     name="\N{CLOCKWISE RIGHTWARDS AND LEFTWARDS OPEN CIRCLE ARROWS}"),
                                 custom_id=f"giveaway_reroll:{self.giveaway.id}")

            async def callback(self, interaction: discord.Interaction):
                self.message = interaction.message
                embed = self.message.embeds[0]
                guild = self.bot.get_guild(self.giveaway.guild_id)

                winner_list = []
                for i in range(0, len(self.giveaway.entries)):
                    if len(winner_list) < self.giveaway.winner_count:
                        user_id = self.giveaway.entries.pop(random.randint(0, len(self.giveaway.entries) - 1))
                        winner_list.append(guild.get_member(user_id))
                winners = ", ".join(x.mention for x in winner_list)

                field = embed.fields[0]
                lines = field.value.split('\n')
                lines[3] = f"Winner(s): {winners}"
                embed.set_field_at(0, name=field.name, value='\n'.join(lines))

                await self.message.reply(
                    f"Congratulations {winners}! You won the giveaway for *{self.giveaway.prize}*!",
                    allowed_mentions=discord.AllowedMentions(users=True))

                await interaction.response.edit_message(
                    embed=embed,
                    view=GiveawayRerollView(self.bot, self.cog, self.giveaway)
                    if not len(self.giveaway.entries) == 0 else None
                )

        self.add_item(RerollButton())

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if not interaction.user.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "<:redTick:1079249771975413910> You are not allowed to reroll this giveaway.",
                ephemeral=True)
            return False
        return True


class GiveawayEntryView(discord.ui.View):
    def __init__(self, bot: Percy, giveaway: GiveawayItem):
        super().__init__(timeout=None)
        self.bot: Percy = bot
        self.giveaway: GiveawayItem = giveaway

        class EnterButton(discord.ui.Button):
            def __init__(self):
                self.bot: Percy = bot
                self.giveaway: GiveawayItem = giveaway
                super().__init__(label="Enter", style=discord.ButtonStyle.green,
                                 emoji=discord.PartialEmoji(name="giveaway", id=1089511337161400390, animated=True),
                                 custom_id=f"giveaway_enter:{self.giveaway.id}")

            async def callback(self, interaction: discord.Interaction):
                self.giveaway.entries.append(interaction.user.id)
                query = "UPDATE giveaways SET entries = $1 WHERE id = $2;"
                await self.bot.pool.execute(
                    query, self.giveaway.entries, self.giveaway.id
                )

                embed = interaction.message.embeds[0]
                field = embed.fields[0]
                lines = field.value.split('\n')
                lines[2] = f"Entries: **{self.giveaway.entry_count}**"
                embed.set_field_at(0, name=field.name, value='\n'.join(lines))

                await interaction.response.edit_message(embed=embed)
                await interaction.followup.send(
                    '<:greenTick:1079249732364406854> You have successfully entered this giveaway.',
                    ephemeral=True
                )

        self.add_item(EnterButton())

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id in self.giveaway.entries:
            await interaction.response.send_message(
                '<:redTick:1079249771975413910> You have already entered this giveaway.',
                ephemeral=True)
            return False
        return True


class CreateGiveawayModal(discord.ui.Modal, title="Create a Giveaway"):
    duration = discord.ui.TextInput(label="Duration", placeholder="e.g. 10 minutes, 2 days")
    winner_count = discord.ui.TextInput(label="Winner Count", default="1")
    prize = discord.ui.TextInput(label="Prize", placeholder="Short description of the prize", max_length=256)
    description = discord.ui.TextInput(
        label="Description", placeholder="Additional information about the giveaway", style=discord.TextStyle.long,
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
        embed.add_field(name='\u200c',
                        value=f'Ends: {discord.utils.format_dt(when, style="R")} ({discord.utils.format_dt(when, style="F")})\n'
                              f'Hosted by: {interaction.user.mention}\n'
                              f'Entries: **0**\n'
                              f'Winner(s): {self.winner_count.value}')

        msg = await interaction.channel.send(
            embed=embed, allowed_mentions=discord.AllowedMentions(roles=True)
        )

        giveaway: Giveaway = self.bot.get_cog('Giveaway')  # type: ignore
        gw = await giveaway.create_giveaway(
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
                giveaway_id=gw.id,
                created=discord.utils.utcnow(),
                timezone='UTC',
            )

        await msg.edit(view=GiveawayEntryView(self.bot, giveaway=gw))

        await interaction.response.send_message(
            f"<:greenTick:1079249732364406854> Giveaway [`{gw.id}`] successfully created. {msg.jump_url}",
            ephemeral=True
        )


class Giveaway(commands.Cog):
    """Create Giveaways using Modals."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="giveaway", id=1089511337161400390, animated=True)

    async def create_giveaway(
            self,
            channel_id: int,
            message_id: int,
            guild_id: int,
            author_id: int,
            description: str,
            prize: str,
            winner_count: int
    ) -> GiveawayItem:
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
        giveaway = GiveawayItem.temporary(
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

        row = await self.bot.pool.fetchrow(query, channel_id, message_id, guild_id, author_id, prize, description,
                                           winner_count)
        giveaway.id = row[0]

        return giveaway

    async def delete_giveaway(self, giveaway_id: int) -> str:
        """Deletes a giveaway from the database."""
        query = "DELETE FROM giveaways WHERE id = $1;"
        # DELETE <num>
        return await self.bot.pool.execute(query, giveaway_id)

    async def get_giveaway(self, giveaway_id: int) -> Optional[GiveawayItem]:
        """Gets a giveaways from the database."""
        query = "SELECT * FROM giveaways WHERE id = $1 LIMIT 1;"
        record = await self.bot.pool.fetchrow(query, giveaway_id)
        return GiveawayItem(record=record) if record else None

    giveaway = app_commands.Group(name="giveaway", description="Manage giveaways.",
                                  default_permissions=discord.Permissions(manage_channels=True), guild_only=True)

    @commands_ext.command(
        giveaway.command,
        name='create',
        description='Create a giveaway.',
    )
    @app_commands.guild_only()
    @commands_ext.command_permissions(1, user=["ban_members", "manage_messages"])
    async def make_giveaway(self, interaction: discord.Interaction):
        """Interactively creates a giveaway using a Modal."""
        await interaction.response.send_modal(CreateGiveawayModal(self.bot))

    @commands.Cog.listener()
    async def on_giveaway_timer_complete(self, timer: Timer):
        await self.bot.wait_until_ready()
        _id = timer.kwargs.get("giveaway_id")

        record = await self.get_giveaway(giveaway_id=_id)
        channel = self.bot.get_channel(record.channel_id)
        message = await channel.fetch_message(record.message_id)

        await self.delete_giveaway(giveaway_id=_id)

        embed = message.embeds[0]
        guild = self.bot.get_guild(record.guild_id)

        if record.entries:
            winner_status = True
            winner_list = []
            for i in range(0, len(record.entries)):
                if len(winner_list) < record.winner_count:
                    user_id = record.entries.pop(random.randint(0, len(record.entries) - 1))
                    winner_list.append(guild.get_member(user_id))
            winners = ", ".join(x.mention for x in winner_list)
        else:
            winner_status = False
            winners = "*No one entered the giveaway.*"

        field = embed.fields[0]
        lines = field.value.split('\n')
        lines[0] = lines[0].replace('Ends', 'Ended')
        lines[3] = f"Winner(s): {winners}"
        embed.set_field_at(0, name=field.name, value='\n'.join(lines))

        embed.set_footer(text=f"Giveaway ended")

        if winner_status and len(record.entries) > 0:
            await message.reply(f"Congratulations {winners}! You won the giveaway for *{record.prize}*!",
                                allowed_mentions=discord.AllowedMentions(users=True))

            view = GiveawayRerollView(self.bot, self, record)
            await message.edit(embed=embed, view=view)
        else:
            await message.reply(f"No one entered the giveaway for *{record.prize}*.")
            await message.edit(embed=embed, view=None)


async def setup(bot: Percy):
    await bot.add_cog(Giveaway(bot))
