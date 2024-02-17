from __future__ import annotations

from contextlib import suppress
from typing import Optional
import discord
from discord import app_commands

from bot import Percy
from .utils.context import Context
from .utils import fuzzy, commands, helpers
from .utils.formats import plural
from .utils.helpers import PostgresItem


class TempChannel(PostgresItem):
    """A temporary voice channel dataclass."""

    guild_id: int
    channel_id: int
    format: str

    __slots__ = ('guild_id', 'channel_id', 'format')

    @property
    def choice_text(self) -> str:
        """Create a field for an embed."""
        return f'<#{self.channel_id}> • `{self.format}`'

    def display_name(self, member: discord.Member) -> str:
        """Display the name of the channel."""
        return (
            self.format
            .replace('%name', str(member))
            .replace('%display_name', member.display_name)
            .replace('%guild', member.guild.name)
            .replace('%channel', member.voice.channel.name)
        )


class TempChannels(commands.Cog):
    """Create temporary voice hub channels for users to join."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='\N{HOURGLASS}')

    async def get_guild_temp_channels(self, guild_id: int) -> list[TempChannel]:
        """Get the temporary channels for a guild."""
        query = "SELECT * FROM temp_channels WHERE guild_id = $1;"
        return [TempChannel(record=record) for record in await self.bot.pool.fetch(query, guild_id)]

    async def get_guild_temp_channel(self, guild_id: int, channel_id: int) -> Optional[TempChannel]:
        """Get a temporary channel for a guild."""
        query = "SELECT * FROM temp_channels WHERE guild_id = $1 AND channel_id = $2;"
        record = await self.bot.pool.fetchrow(query, guild_id, channel_id)
        return TempChannel(record=record) if record else None

    async def temp_channel_id_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        channels = await self.get_guild_temp_channels(interaction.guild_id)
        results: list[discord.VoiceChannel] = fuzzy.finder(current, channels, key=lambda c: c.name)  # type: ignore
        return [app_commands.Choice(name=ch.name, value=str(ch.id)) for ch in results][:25]

    @commands.command(
        commands.hybrid_group,
        name='temp',
        description='Manage Temp Channels.',
        guild_only=True
    )
    async def _temp(self, ctx: Context):
        """Get an overview of the Use of the TempChannels."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @commands.command(_temp.command, name='list', description='List of current temporary channels.')
    async def temp_list(self, ctx: Context):
        """List of current temporary channels."""
        temp_channels = await self.get_guild_temp_channels(ctx.guild.id)
        if not temp_channels:
            return await ctx.stick(False, 'There are no temporary channels set up.', ephemeral=True)

        items = [f'- {temp.choice_text}' for index, temp in enumerate(temp_channels, 1)]
        embed = discord.Embed(title='Temporary Voice Hubs',
                              description='\n'.join(items),
                              color=helpers.Colour.white())
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
        embed.set_footer(text=f'{plural(len(temp_channels)):channel}')
        await ctx.send(embed=embed)

    @commands.command(_temp.command, name='set', description='Transforms a voice channel into a temporary channel.')
    @commands.permissions(user=['manage_channels'], bot=['manage_channels'])
    @app_commands.rename(_format='format')
    @app_commands.describe(
        channel='The voice channel to set.',
        _format='The format of the voice channel. (Default: "⏳ | %name")')
    async def temp_set(self, ctx: Context, channel: discord.VoiceChannel, _format: Optional[str] = '⏳ | %name'):
        """Sets the channel where to create a temporary channel.

        ## Formats
        - **%name**: The name of the user.
        - **%display_name**: The name of the user.
        - **%channel**: The name of the voice hub.
        - **%guild**: The name of the guild.
        """
        query = """
            INSERT INTO temp_channels (guild_id, channel_id, format) VALUES ($1, $2, $3)
            ON CONFLICT (channel_id) DO UPDATE 
                SET format = $3;
        """
        await self.bot.pool.execute(query, ctx.guild.id, channel.id, _format)
        await ctx.stick(True, f'Successfully set {channel.mention} with format **`{_format}`**.')

    @commands.command(_temp.command, name='remove', description='Remove an existing temp channel.')
    @commands.permissions(user=['manage_channels'], bot=['manage_channels'])
    @app_commands.describe(channel_id='The voice channel to remove')
    @app_commands.autocomplete(channel_id=temp_channel_id_autocomplete)  # type: ignore
    async def temp_remove(self, ctx: Context, channel_id: str):
        """Remove an existing temp channel."""
        query = "DELETE FROM temp_channels WHERE channel_id = $1 AND guild_id = $2;"
        result = await self.bot.pool.execute(query, int(channel_id), ctx.guild.id)
        if result == 'DELETE 0':
            return await ctx.stick(False, 'This is not a Temporary Voice Hub.', ephemeral=True)

        await ctx.stick(True, f'Successfully removed the Temporary Voice Hub.')

    @commands.Cog.listener()
    async def on_voice_state_update(self, member: discord.Member, before, after):
        await self.bot.wait_until_ready()

        if before.channel:
            if self.bot.temp_channels.get(before.channel.id):
                if len(before.channel.members) == 0:
                    await self.bot.temp_channels.remove(before.channel.id)
                    with suppress(discord.errors.NotFound):
                        await before.channel.delete()

        elif after.channel and before.channel is None:
            channel = await self.get_guild_temp_channel(member.guild.id, after.channel.id)
            if channel:
                try:
                    channel = await member.guild.create_voice_channel(
                        name=channel.display_name(member),
                        category=after.channel.category,
                        reason=f'Temporary Voice Hub for {member} ({member.id})')
                    ow = discord.PermissionOverwrite(manage_channels=True, manage_roles=True, move_members=True)
                    await channel.set_permissions(member, overwrite=ow)

                    await member.move_to(channel)
                    await self.bot.temp_channels.put(channel.id, True)
                except discord.HTTPException as exc:
                    if exc.code == 50013:
                        await member.guild.system_channel.send(
                            f'<:warning:1076913452775383080> {member.mention} I don\'t have the permissions to create or '
                            f'manage a temporary channel in **{after.channel.category}**.')
                    else:
                        await member.guild.system_channel.send(
                            f'<:warning:1076913452775383080> {member.mention} An error occurred while creating a temporary channel.')


async def setup(bot: Percy):
    await bot.add_cog(TempChannels(bot))
