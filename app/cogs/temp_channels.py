from __future__ import annotations

from contextlib import suppress
from operator import attrgetter
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands

from app.core import Bot, Cog
from app.core.models import Context, PermissionTemplate, describe, group
from app.database import BaseRecord
from app.utils import fuzzy, helpers, pluralize
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Callable

    import asyncpg

    from app.database.base import GuildConfig


class TempChannel(BaseRecord):
    """A temporary voice channel dataclass."""

    bot: Bot
    guild_id: int
    channel_id: int
    format: str

    __slots__ = ('bot', 'guild_id', 'channel_id', 'format')

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ):
        query = f"""
            UPDATE temp_channels
            SET {', '.join(map(key, enumerate(values.keys(), start=3)))}
            WHERE guild_id = $1 AND channel_id = $2
            RETURNING *;
        """
        record = await (connection or self.bot.db).fetchrow(query, self.guild_id, self.channel_id, *values.values())
        return self.__class__(bot=self.bot, record=record)

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

    async def delete(self) -> None:
        """Delete the temporary channel."""
        query = "DELETE FROM temp_channels WHERE guild_id = $1 AND channel_id = $2;"
        await self.bot.db.execute(query, self.guild_id, self.channel_id)

    async def delete_all(self) -> None:
        """Delete all temporary channels."""
        query = "DELETE FROM temp_channels WHERE guild_id = $1;"
        await self.bot.db.execute(query, self.guild_id)


class TempChannels(Cog):
    """Create temporary voice hub channels for users to join."""

    emoji = '\N{HOURGLASS}'

    async def get_guild_temp_channels(self, guild_id: int, convert: bool = False) -> list[TempChannel | discord.VoiceChannel]:
        """|coro|

        Get a list of temporary channels for a guild.

        Parameters
        ----------
        guild_id: int
            The guild ID.
        convert: bool
            Whether to convert the records to discord.VoiceChannel.

        Returns
        -------
        list[TempChannel | discord.VoiceChannel]
            A list of temporary channels.
        """
        query = "SELECT * FROM temp_channels WHERE guild_id = $1;"
        records = await self.bot.db.fetch(query, guild_id)
        if convert:
            guild = self.bot.get_guild(guild_id)
            return [guild.get_channel(record['channel_id']) for record in records]
        return [TempChannel(bot=self.bot, record=record) for record in records]

    async def get_guild_temp_channel(self, guild_id: int, channel_id: int) -> TempChannel | None:
        """|coro|

        Get a temporary channel for a guild.

        Parameters
        ----------
        guild_id: int
            The guild ID.
        channel_id: int
            The channel ID.

        Returns
        -------
        TempChannel
            A temporary channel.
        """
        query = "SELECT * FROM temp_channels WHERE guild_id = $1 AND channel_id = $2;"
        record = await self.bot.db.fetchrow(query, guild_id, channel_id)
        return TempChannel(bot=self.bot, record=record) if record else None

    async def temp_channel_id_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """The autocomplete for the temp channel ID."""
        channels = await self.get_guild_temp_channels(interaction.guild_id, convert=True)
        results = fuzzy.finder(current, channels, key=attrgetter('name'))
        return [app_commands.Choice(name=ch.name, value=str(ch.id)) for ch in results][:25]

    @group(
        'temp',
        description='Manage Temp Channels.',
        guild_only=True,
        hybrid=True
    )
    async def temp(self, ctx: Context) -> None:
        """Get an overview of the Use of the TempChannels."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @temp.command(name='list', description='List of current temporary channels.')
    async def temp_list(self, ctx: Context) -> None:
        """List of current temporary channels."""
        temp_channels = await self.get_guild_temp_channels(ctx.guild.id)
        if not temp_channels:
            await ctx.send_error('There are no temporary channels set up.')
            return

        items = [f'- {temp.choice_text}' for index, temp in enumerate(temp_channels, 1)]
        embed = discord.Embed(title='Temporary Voice Hubs',
                              description='\n'.join(items),
                              color=helpers.Colour.white())
        embed.set_author(name=ctx.guild.name, icon_url=ctx.guild.icon.url)
        embed.set_footer(text=f'{pluralize(len(temp_channels)):channel}')
        await ctx.send(embed=embed)

    @temp.command(
        'set',
        description='Transforms a voice channel into a temporary channel.',
        bot_permissions=['manage_channels'],
        user_permissions=['manage_channels']
    )
    @app_commands.rename(_format='format')
    @describe(
        channel='The voice channel to set.',
        _format='The format of the voice channel. (Default: "⏳ | %name")')
    async def temp_set(self, ctx: Context, channel: discord.VoiceChannel, _format: str | None = '⏳ | %name') -> None:
        """Sets the channel where to create a temporary channel.

        **Format Variables:**
        - **%name**: The name of the user.
        - **%display_name**: The name of the user.
        - **%channel**: The name of the voice hub.
        - **%guild**: The name of the guild.
        """
        config = await self.get_guild_temp_channel(ctx.guild.id, channel.id)
        if config:
            await config.update(format=_format)
            await ctx.send_success(f'Successfully updated {channel.mention} with format **`{_format}`**.')
            return

        query = "INSERT INTO temp_channels (guild_id, channel_id, format) VALUES ($1, $2, $3);"
        await self.bot.db.execute(query, ctx.guild.id, channel.id, _format)
        await ctx.send_success(f'Successfully set {channel.mention} with format **`{_format}`**.')

    @temp.command(
        'remove',
        description='Remove an existing temp channel.',
        bot_permissions=['manage_channels'],
        user_permissions=['manage_channels']
    )
    @describe(channel_id='The voice channel to remove')
    @app_commands.autocomplete(channel_id=temp_channel_id_autocomplete)
    async def temp_remove(self, ctx: Context, channel_id: str) -> None:
        """Remove an existing temp channel."""
        config = await self.get_guild_temp_channel(ctx.guild.id, int(channel_id))
        if not config:
            await ctx.send_error('This channel is not a temporary channel.')
            return

        await config.delete()
        await ctx.send_success('Successfully removed the temporary channel.')

    @temp.command(
        'purge',
        description='Remove all temporary channels.',
        user_permissions=PermissionTemplate.mod
    )
    async def temp_purge(self, ctx: Context) -> None:
        """Remove all temporary channels."""
        config = await self.get_guild_temp_channel(ctx.guild.id, ctx.channel.id)
        if not config:
            await ctx.send_error('There are no temporary channels set up.')
            return

        await config.delete_all()
        await ctx.send_success('Successfully removed all temporary channels.')

    @Cog.listener()
    async def on_voice_state_update(
            self,
            member: discord.Member,
            before: discord.VoiceState,
            after: discord.VoiceState
    ) -> None:
        """|coro|

        Manage the temporary voice channels.
        """
        await self.bot.wait_until_ready()

        if before.channel:
            if self.bot.temp_channels.get(before.channel.id) and len(before.channel.members) == 0:
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
                        message = (
                            f'{Emojis.warning} {member.mention} I don\'t have the permissions to create or '
                            f'manage a temporary channel in **{after.channel.category}**.'
                        )
                    else:
                        message = f'{Emojis.warning} {member.mention} An error occurred while creating a temporary channel.'

                    config: GuildConfig = await self.bot.db.get_guild_config(member.guild.id)
                    if config.alert_webhook:
                        await config.send_alert(message)
                    else:
                        with suppress(discord.HTTPException):
                            await member.guild.system_channel.send(message)


async def setup(bot) -> None:
    await bot.add_cog(TempChannels(bot))
