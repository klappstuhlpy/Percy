from __future__ import annotations

from typing import Annotated

import discord
from discord import AppCommandOptionType, app_commands
from discord.ext import commands, tasks
from discord.ext.commands import Range

from app.cogs.leveling.models import _MAX_LEVEL, _MAX_XP, GuildLevelConfig, LevelConfig
from app.cogs.leveling.ui import InteractiveLevelRolesView, InteractiveMultiplierView
from app.core import Bot, Cog, Flags, converter, flag
from app.core.converter import IgnoreableEntity, IgnoreEntity
from app.core.models import Context, PermissionTemplate, describe, group
from app.utils import cache, fnumb, get_asset_url, helpers, humanize_duration, medal_emoji, truncate
from config import Emojis


class LevelSetFlags(Flags):
    xp: Range[int, 1, _MAX_XP] = flag(description="The amount of XP you want to set.", alias="experience")
    level: Range[int, 1, _MAX_LEVEL] = flag(description="The level you want to set.")


class AnyLevelChannel(commands.Converter, app_commands.Transformer):
    async def convert(self, ctx: Context, argument: str) -> discord.TextChannel | str:
        if argument.lower() in ("dm", "channel"):
            return argument.lower()
        return await commands.TextChannelConverter().convert(ctx, argument)

    @property
    def type(self) -> AppCommandOptionType:
        return AppCommandOptionType.channel


class Leveling(Cog):
    """Leveling system, commands and utilities."""

    emoji = Emojis.level_up

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.award_voice_xp.start()
        self.snapshot_xp.start()

    async def cog_unload(self) -> None:
        self.award_voice_xp.cancel()
        self.snapshot_xp.cancel()

    @tasks.loop(hours=24)
    async def snapshot_xp(self) -> None:
        """Record a daily per-guild cumulative-XP snapshot for the dashboard chart.

        Runs once on startup and then every 24h. For each guild with leveling
        enabled, sums every member's *total* XP (resolved through the guild's
        :class:`LevelingSpec`, since the stored ``xp`` column only holds
        within-level progress) and upserts today's ``xp_history`` row.
        """
        for guild in self.bot.guilds:
            config: GuildLevelConfig | None = await self.get_guild_level_config(guild.id)  # type: ignore[misc]
            if config is None or not config.enabled:
                continue

            records = await self.bot.db.leveling.get_user_levels(guild.id)
            total_xp = 0
            gainers = 0
            for record in records:
                member_total = config.spec.get_total_xp(record['level'], record['xp'])
                total_xp += member_total
                if member_total > 0:
                    gainers += 1

            await self.bot.db.leveling.record_xp_snapshot(guild.id, total_xp, gainers)

    @snapshot_xp.before_loop
    async def _before_snapshot_xp(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def award_voice_xp(self) -> None:
        """Grant voice XP each minute to active, non-idle members in populated channels.

        Members are skipped if they are alone (fewer than two humans in the channel),
        AFK, server- or self-deafened, or blacklisted — mirroring the message-XP rules.
        """
        for guild in self.bot.guilds:
            config: GuildLevelConfig | None = await self.get_guild_level_config(guild.id)  # type: ignore[misc]
            if config is None or not config.enabled or not config.voice_enabled:
                continue

            for channel in (*guild.voice_channels, *guild.stage_channels):
                humans = [m for m in channel.members if not m.bot]
                if len(humans) < 2:
                    continue

                for member in humans:
                    voice = member.voice
                    if voice is None or voice.afk or voice.self_deaf or voice.deaf:
                        continue

                    level_config = await self.get_level_config(member.id, guild.id)
                    if level_config is None or not level_config.can_gain_voice(channel):
                        continue

                    boost = await self.bot.db.economy.get_boost_multiplier(member.id, guild.id, 'xp')
                    await level_config.add_voice_xp(round(config.voice_xp * boost), channel=channel)

    @award_voice_xp.before_loop
    async def _before_award_voice_xp(self) -> None:
        await self.bot.wait_until_ready()

    @cache.cache()
    async def get_guild_level_config(self, guild_id: int, /) -> GuildLevelConfig | None:
        """|coro| @cached

        Returns the :class:`GuildLevelConfig` for the given guild.

        Parameters
        ----------
        guild_id: :class:`int`
            The guild ID to get the level config for.

        Returns
        -------
        :class:`GuildLevelConfig`
            The level config for the given guild.
        """
        record = await self.bot.db.leveling.get_guild_config_record(guild_id)
        if not record:
            return None
        return GuildLevelConfig(cog=self, record=record)

    async def get_level_config(self, user_id: int, guild_id: int) -> LevelConfig | None:
        """|coro| @cached

        Returns the :class:`LevelConfig` for the given user and guild.

        Parameters
        ----------
        user_id: :class:`int`
            The user ID to get the level config for.
        guild_id: :class:`int`
            The guild ID to get the level config for.

        Returns
        -------
        :class:`LevelConfig`
            The level config for the given user and guild.
        """
        record = await self.bot.db.leveling.get_or_create_user_level(user_id, guild_id)
        return LevelConfig(cog=self, config=await self.get_guild_level_config(guild_id), record=record)  # type: ignore[misc]

    @Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild:
            return

        if message.author.bot:
            return

        guild_config: GuildLevelConfig = await self.get_guild_level_config(message.guild.id)  # type: ignore[misc]

        if guild_config is None:
            return

        if not guild_config.enabled:
            return

        if any(user.bot for user in message.mentions):
            return

        if len(message.content) <= 2:
            return

        config = await self.get_level_config(message.author.id, message.guild.id)
        assert config is not None
        await config.process_invoke(message)

    @Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        guild_config = await self.get_guild_level_config(member.guild.id)  # type: ignore[misc]
        if guild_config is None:
            return

        if not guild_config.delete_after_leave:
            return

        await guild_config.delete_member(member)

    @group("level", fallback="rank", description="Leveling purpose Commands.", guild_only=True, hybrid=True)
    @describe(member="The member to view the rank card of.")
    async def level(self, ctx: Context, *, member: Annotated[discord.Member, converter.MemberConverter]) -> None:
        """View yours or someone else's rank card."""
        user: discord.Member = member or ctx.author  # type: ignore

        if user.bot:
            await ctx.send_error("You can't view the rank card of a bot.")
            return

        await ctx.defer(typing=True)

        config: LevelConfig | None = await self.get_level_config(user.id, user.guild.id)
        if config is None:
            await ctx.send_error(f"**{user}** has not gained any XP yet.")
            return

        if config.xp == 0:
            await ctx.send_error(f"**{user}** has not gained any XP yet.")
            return

        image = await self.bot.render.level_card(user, config)
        await ctx.send(file=image)

    @level.command(aliases=["top"], description="View the server leaderboard.", guild_only=True)
    async def leaderboard(self, ctx: Context) -> None:
        """View the Top 10 users of the server."""
        assert ctx.guild is not None
        records = await self.bot.db.leveling.get_leaderboard(ctx.guild.id, limit=10)

        embed = discord.Embed(colour=helpers.Colour.white(), title=f"Level Statistics for {ctx.guild.name}")
        embed.set_thumbnail(url=get_asset_url(ctx.guild))
        embed.set_footer(text="Level Statistics for this Server.")

        if not records:
            value = "*There are no statistics for this category available.*"
        else:
            value = "\n".join(
                [
                    f"{medal_emoji(index, numerate=True)}: <@{record['user_id']}> • Level **{record['level']}** • **{fnumb(record['xp'])}** XP"
                    for index, record in enumerate(records, 1)
                ]
            )

        embed.description = value
        await ctx.send(embed=embed)

    @level.command(
        "set", description="Set a members experience or level.", guild_only=True, user_permissions=PermissionTemplate.admin
    )
    @describe(target="The target member to modify.")
    async def level_set(
        self, ctx: Context, target: Annotated[discord.Member, converter.MemberConverter], *, flags: LevelSetFlags
    ) -> None:
        """Set a users experience/level."""
        assert ctx.guild is not None
        guild_config: GuildLevelConfig | None = await self.get_guild_level_config(ctx.guild.id)  # type: ignore[misc]
        if guild_config is None or (guild_config and not guild_config.enabled):
            await ctx.send_error("Leveling is not enabled in this server.")
            return

        if target.bot:
            await ctx.send_error("You can't manage Bot's Level/Experience.")
            return

        if (flags.xp is None and flags.level is None) or (flags.xp and flags.level):
            await ctx.send_error("You need to provide either a level or xp to set.")
            return

        config: LevelConfig | None = await self.get_level_config(target.id, target.guild.id)
        assert config is not None

        if flags.level:
            if flags.level > _MAX_LEVEL:
                await ctx.send_error(f"You can't set more than **Level {_MAX_LEVEL}**.")
                return

            level = flags.level
            xp = guild_config.spec.level_requirement_for(flags.level)
        else:
            if flags.xp > _MAX_XP:
                await ctx.send_error(f"Sorry. You can't set more than **{fnumb(_MAX_XP)} XP**. (Level **{_MAX_LEVEL}**)")
                return

            level = guild_config.spec.xp_requirement_for(flags.xp)
            xp = flags.xp

        await config.update(xp=xp, level=level)

        await ctx.send(f"**{target}** is now level **{level}** with **{fnumb(xp)}** total XP. {self.emoji}")

    @level.group("config", fallback="view", description="Leveling Configuration Commands.", guild_only=True, hybrid=True)
    async def level_config(self, ctx: Context) -> None:
        """Leveling Configuration Commands."""
        assert ctx.guild is not None
        config: GuildLevelConfig | None = await self.get_guild_level_config(ctx.guild.id)  # type: ignore[misc]
        if not config:
            await ctx.send_error("Leveling is not enabled in this server.")
            return

        match config.level_up_channel:
            case 0:
                channel = "Don't send"
            case 1:
                channel = "Source Channel"
            case 2:
                channel = "DMs"
            case custom:
                channel = f"<#{custom}>"

        default = "*N/A*"

        def to_emoji(val: bool) -> str:
            return Emojis.success if val else Emojis.error

        level_roles = "\n".join(f"- Level **{level}**: <@&{role}>" for role, level in config.level_roles.items()) or default
        embed = discord.Embed(
            title="Leveling Configuration",
            colour=helpers.Colour.white(),
            description=f"**Enabled:** {to_emoji(config.enabled)} `{config.enabled}`\n"
            f"**Delete User Data After Leave:** {to_emoji(config.delete_after_leave)} `{config.delete_after_leave}`\n"
            f"**Voice XP:** {to_emoji(config.voice_enabled)} `{config.voice_enabled}` "
            f"({config.voice_xp} XP/min)\n"
            f"**Level Up Message:** ```\n{config.level_up_message}```\n"
            f"**Level Up Channel:** {channel}\n\n"
            f"**Level Roles:**\n"
            f"{level_roles}",
        )

        cooldown = config.cooldown_manager
        embed.add_field(
            name="Cooldown", value=f"{cooldown.rate} time(s) per {humanize_duration(cooldown.per)}", inline=False
        )

        embed.add_field(
            name="Blacklisted Roles",
            value=truncate(", ".join(f"<@&{role}>" for role in config.blacklisted_roles) or default, 1024),
            inline=False,
        )
        embed.add_field(
            name="Blacklisted Channels",
            value=truncate(", ".join(f"<#{channel}>" for channel in config.blacklisted_channels) or default, 1024),
            inline=False,
        )
        embed.add_field(
            name="Blacklisted Users",
            value=truncate(", ".join(f"<@{user}>" for user in config.blacklisted_users) or default, 1024),
            inline=False,
        )

        embed.add_field(
            name="Multiplier Roles",
            value=truncate(
                ", ".join(f"<@&{role}>: **{multiplier}**" for role, multiplier in config.multiplier_roles.items())
                or default,
                1024,
            ),
            inline=False,
        )

        embed.add_field(
            name="Multiplier Channels",
            value=truncate(
                ", ".join(f"<#{channel}>: **{multiplier}**" for channel, multiplier in config.multiplier_channels.items())
                or default,
                1024,
            ),
            inline=False,
        )

        if ctx.guild is not None:
            embed.set_thumbnail(url=get_asset_url(ctx.guild))
        embed.set_footer(text="Leveling Configuration for this Server.")
        await ctx.send(embed=embed)

    @level_config.command(
        "toggle", description="Toggle leveling on or off.", guild_only=True, user_permissions=PermissionTemplate.manager
    )
    @describe(enabled="Boolean to enable or disable leveling. If not provided, it will toggle.")
    async def level_config_toggle(self, ctx: Context, enabled: bool) -> None:
        """Toggle leveling on or off."""
        assert ctx.guild is not None
        config = await self.get_guild_level_config(ctx.guild.id)  # type: ignore[misc]
        if enabled:
            if config is not None:
                await config.update(enabled=True)
            else:
                await self.bot.db.leveling.create_guild_config(ctx.guild.id, enabled)
                self.get_guild_level_config.invalidate(ctx.guild.id)
        else:
            if not config:
                await ctx.send_error("Leveling is already disabled.")
                return

            await config.update(enabled=False)

        fmt = "*enabled*" if enabled else "*disabled*"
        await ctx.send_success(f"Leveling {fmt}.")

    @level_config.command(
        "delete-after-leave",
        description="Toggle deleting user data after leave.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(delete="Boolean to enable or disable deleting user data after leave.")
    async def level_config_delete_after_leave(self, ctx: Context, delete: bool) -> None:
        """Toggle deleting user data after leave."""
        assert ctx.guild is not None
        config = await self.get_guild_level_config(ctx.guild.id)  # type: ignore[misc]
        if not config:
            await ctx.send_error("Leveling is not enabled in this server.")
            return

        await config.update(delete_after_leave=delete)
        fmt = "*enabled*" if delete else "*disabled*"
        await ctx.send_success(f"Deleting user data after leave {fmt}.")

    @level_config.command(
        "roles",
        description="Set the level roles for the server.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    async def level_config_roles(self, ctx: Context) -> None:
        """Set the level up message for the server."""
        assert ctx.guild is not None
        config = await self.get_guild_level_config(ctx.guild.id)  # type: ignore[misc]
        if not config:
            await ctx.send_error("Leveling is not enabled in this server.")
            return

        view = InteractiveLevelRolesView(ctx, config=config)
        await ctx.send(embed=view.make_embed(), view=view)

    @level_config.command(
        "message",
        description="Set the level up message for the server (Use {level} for the level and {user} for the user).",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(message="The message to set the level up message to.")
    async def level_config_message(self, ctx: Context, *, message: str) -> None:
        """Set the level up message for the server.

        Use {level} for the level and {user} for the user.
        """
        assert ctx.guild is not None
        config = await self.get_guild_level_config(ctx.guild.id)  # type: ignore[misc]
        if not config:
            await ctx.send_error("Leveling is not enabled in this server.")
            return

        await config.update(level_up_message=message)
        await ctx.send_success("Level up message has been updated.")

    @level_config.command(
        "channel",
        description="Set the level up channel for the server.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(channel="The channel to set the level up channel to.")
    async def level_config_channel(self, ctx: Context, channel: AnyLevelChannel | None = None) -> None:
        """Set the level up channel for the server.

        Leave `channel` empty to don't send level up messages, use `dm` for DMs
        and `channel` for the current channel or provide a channel.

        Note: To set the channel to dm or current channel, please use the text command version of this command.
        """
        assert ctx.guild is not None
        config = await self.get_guild_level_config(ctx.guild.id)  # type: ignore[misc]
        if not config:
            await ctx.send_error("Leveling is not enabled in this server.")
            return

        if channel is None:
            channel_id = 0
        elif isinstance(channel, str):
            match channel:
                case "dm":
                    channel_id = 2
                case "channel":
                    channel_id = 1
                case _:
                    channel_id = 0
        else:
            assert isinstance(channel, discord.TextChannel)
            channel_id = channel.id

        await config.update(level_up_channel=channel_id)
        await ctx.send_success("Level up channel has been updated.")

    @level_config.command(
        "ignore",
        description="Set ignorable entities for the leveling system.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(entities="The entities to ignore.")
    async def level_config_ignore(
        self, ctx: Context, entities: Annotated[list[IgnoreableEntity], commands.Greedy[IgnoreEntity]]
    ) -> None:
        """Set ignorable entities for the leveling system.

        You can ignore roles, channels and users from the leveling system.
        """
        assert ctx.guild is not None
        config: GuildLevelConfig | None = await self.get_guild_level_config(ctx.guild.id)  # type: ignore[misc]
        if not config:
            await ctx.send_error("Leveling is not enabled in this server.")
            return

        roles = set()
        channels = set()
        users = set()

        for entity in entities:
            if isinstance(entity, discord.Role):
                roles.add(entity.id)
            elif isinstance(entity, discord.TextChannel):
                channels.add(entity.id)
            elif isinstance(entity, discord.Member):
                users.add(entity.id)

        await config.merge(blacklisted_roles=roles, blacklisted_channels=channels, blacklisted_users=users)
        await ctx.send_success("Blacklisted entities have been updated.")

    @level_config.command(
        "unignore",
        description="Unset ignored entities for the leveling system.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(entities="The entities to unignore.")
    async def level_config_unignore(
        self, ctx: Context, entities: Annotated[list[IgnoreableEntity], commands.Greedy[IgnoreEntity]]
    ) -> None:
        """Unset ignored entities for the leveling system."""
        assert ctx.guild is not None
        config: GuildLevelConfig | None = await self.get_guild_level_config(ctx.guild.id)  # type: ignore[misc]
        if not config:
            await ctx.send_error("Leveling is not enabled in this server.")
            return

        roles = set()
        channels = set()
        users = set()

        for entity in entities:
            if isinstance(entity, discord.Role):
                roles.add(entity.id)
            elif isinstance(entity, discord.TextChannel):
                channels.add(entity.id)
            elif isinstance(entity, discord.Member):
                users.add(entity.id)

        await config.update(
            blacklisted_roles=config.blacklisted_roles - roles,
            blacklisted_channels=config.blacklisted_channels - channels,
            blacklisted_users=config.blacklisted_users - users,
        )
        await ctx.send_success("Blacklisted entities have been updated.")

    @level_config.command(
        "multiplier",
        description="Set the multiplier roles for the server.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    async def level_config_multipliers(self, ctx: Context) -> None:
        """Set the multiplier roles for the server."""
        assert ctx.guild is not None
        config = await self.get_guild_level_config(ctx.guild.id)  # type: ignore[misc]
        if not config:
            await ctx.send_error("Leveling is not enabled in this server.")
            return

        view = InteractiveMultiplierView(ctx, config=config)
        await ctx.send(embed=view.make_embed(), view=view)

    @level_config.command(
        "voice",
        description="Toggle voice-activity XP and set the per-minute gain.",
        guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(
        enabled="Whether members earn XP while active in voice channels.",
        xp_per_minute="XP granted each minute spent active in voice (defaults unchanged).",
    )
    async def level_config_voice(
        self, ctx: Context, enabled: bool, xp_per_minute: Range[int, 1, 1000] | None = None
    ) -> None:
        """Toggle voice-activity XP.

        Members earn XP each minute they are active (not alone, AFK or deafened) in a
        voice channel. The same blacklists and level-roles as message XP apply.
        """
        assert ctx.guild is not None
        config = await self.get_guild_level_config(ctx.guild.id)  # type: ignore[misc]
        if not config:
            await ctx.send_error("Leveling is not enabled in this server.")
            return

        values: dict[str, object] = {"voice_enabled": enabled}
        if xp_per_minute is not None:
            values["voice_xp"] = xp_per_minute
        await config.update(**values)

        fmt = "*enabled*" if enabled else "*disabled*"
        suffix = f" ({xp_per_minute} XP/min)" if xp_per_minute is not None else ""
        await ctx.send_success(f"Voice-activity XP {fmt}{suffix}.")


async def setup(bot: Bot) -> None:
    await bot.add_cog(Leveling(bot))
