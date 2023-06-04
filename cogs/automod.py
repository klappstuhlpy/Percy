import json
import traceback
from typing import Optional, Literal, List

import discord
from discord import app_commands
from discord.ext import commands
from discord.http import Route

from bot import Percy
from cogs import command
from cogs.base import RH_MUSIC_GUILD_ID
from cogs.utils.paginator import BasePaginator


class AutoModerationManaging(commands.Cog, name="AutoMod"):
    """Manage Standard Discord Auto Moderation Rules.

    EXPERIMENTAL: This feature is still in development and may not work as intended.
    """
    def __init__(self, bot: Percy):
        self.bot: Percy = bot

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="automod", id=1112496158124818433)

    automod = app_commands.Group(name="automod", description="Manage Auto Moderation Rules",
                                 default_permissions=discord.Permissions(manage_guild=True), guild_only=True)

    @command(
        automod.command,
        name="list",
        description="List all Auto Moderation Rules."
    )
    async def automod_list(self, interaction: discord.Interaction):
        rules = await self.bot.http.request(
            Route('GET', '/guilds/{guild_id}/auto-moderation/rules',
                  guild_id=interaction.guild_id)
        )

        fields = []
        for rule in rules:
            metas = ""
            for meta, value in rule.get('trigger_metadata', {}).items():
                metas += f'\n- **{meta.replace("_", " ").title()}**: {value}'

            value = f"ID: `{rule.get('id')}`\n" \
                    f"Event Type: `{rule.get('event_type')}`\n" \
                    f"Trigger Type: `{rule.get('trigger_type')}`\n" \
                    f"Enabled: `{rule.get('enabled')}`\n" \
                    f"Created By: **{interaction.guild.get_member(int(rule.get('creator_id')))}**\n" \
                    f"Trigger Meta:{metas}\n" \
                    f"Exempt Roles: {', '.join(interaction.guild.get_role(int(role_id)).mention for role_id in rule.get('exempt_roles', []))}\n" \
                    f"Exempt Channels: {', '.join(interaction.guild.get_channel(int(channel_id)).mention for channel_id in rule.get('exempt_channels', []))}\n"
            fields.append({"name": rule.get('name'), "value": value, "inline": False})

        class AutoModPaginator(BasePaginator[dict]):

            async def format_page(self, entries: List[dict], /) -> discord.Embed:
                embed = discord.Embed(title=f"{interaction.guild.name}'s Auto Moderation Rules",
                                      color=discord.Color.yellow(),
                                      description=f'Modify Auto Moderation Rules using "`/automod <rule_id> <payloads...>`"')
                embed.add_field(**entries[0])
                return embed

        await AutoModPaginator.start(interaction, entries=fields, per_page=1)

    @command(
        automod.command,
        name='create',
        description="Create a new Auto Moderation Rule."
    )
    @app_commands.describe(
        name="Name of the New AutoMod Rule.",
        trigger_type="Type of the AutoMod Rule.",
        enabled="Whether the AutoMod Rule is enabled or not.",
        exempt_roles="Roles that are exempt from the AutoMod Rule. (Use Role IDs and separate them with a space)",
        exempt_channels="Channels that are exempt from the AutoMod Rule. (Use Channel IDs and separate them with a space)"
    )
    async def automod_create(
            self,
            interaction: discord.Interaction,
            name: str,
            trigger_type: Literal["Keyword", "Spam", "Keyword Preset", "Mention Spam"],
            enabled: bool = True,
            exempt_roles: Optional[str] = None,
            exempt_channels: Optional[str] = None,
            kwarg_type: Optional[str] = None,
            kwargs: Optional[str] = None
    ):
        """Create a new AutoMod Rule.
        You cannot assign every value here, use the `automod edit` command to edit the rule."""
        await interaction.response.defer()
        tt = {
            "Keyword": 1,
            "Spam": 3,
            "Keyword Preset": 4,
            "Mention Spam": 5,
        }.get(trigger_type)

        meta = {
            "Keyword": {
                "keyword_filter": [""],
                "regex_patterns": [""]
            },
            "Spam": {},
            "Keyword Preset": {
                "presets": [1, 2]
            },
            "Mention Spam": {
                "mention_total_limit": 5,
            }
        }.get(trigger_type)

        try:
            payload = {
                "name": name,
                "trigger_type": tt,
                "event_type": 1,
                "enabled": enabled,
                "trigger_metadata": meta,
                "actions": [{
                    "type": 1,
                    "metadata": {"custom_message": "Please keep financial discussions limited to the #finance channel"}
                }],
                "exempt_roles": [str(role_id).strip("<>").replace("@&", "") for role_id in
                                 exempt_roles.split(" ")] if exempt_roles else None,
                "exempt_channels": [str(channel_id) for channel_id in
                                    exempt_channels.split(" ")] if exempt_channels else None,
            }

            if kwargs:
                try:
                    payload[kwarg_type] = json.loads(kwargs)
                except Exception as exc:
                    match exc:
                        case KeyError():
                            return await interaction.followup.send("Invalid kwarg type.")
                        case ValueError():
                            return await interaction.followup.send("Invalid kwarg type.")
                        case _:
                            return await interaction.followup.send("Invalid JSON payload.")

            await self.bot.http.request(
                Route('POST', '/guilds/{guild_id}/auto-moderation/rules', guild_id=interaction.guild_id), json=payload,
                reason="AutoMod Rule Created by Percy"
            )
        except Exception as e:
            match e:
                case ValueError():
                    return await interaction.followup.send("Invalid Role or Channel ID.")
                case _:
                    return await interaction.followup.send(
                        f"An error occurred while creating the rule.\n```py\n{traceback.format_exc()}```")

        await interaction.followup.send(f"<:greenTick:1079249732364406854> Rule `{name}` created.")

    @command(
        automod.command,
        name='presets',
        description="View the Auto Moderation Presets."
    )
    async def automod_presets(self, interaction: discord.Interaction):
        """View the AutoMod Presets."""
        await interaction.response.defer()

        presets = ["**Keyword Presets:**", '```json\n{"keyword_filter": ["", ...], "regex_patterns": ["", ...]}```',
                   "**Spam Presets:**",
                   '```json\n{"message_limit": 5, "time_limit": 5, "action": 1, "action_duration": 0}```',
                   "**Mention Spam Presets:**",
                   '```json\n{"mention_total_limit": 5, "mention_user_limit": 5, "time_limit": 5, "action": 1, "action_duration": 0}```']

        embed = discord.Embed(title="AutoMod Presets", color=discord.Color.green())
        embed.description = "\n".join(presets)
        await interaction.followup.send(embed=embed)

    @command(
        automod.command,
        name='edit',
        description="Edit an Auto Moderation Rule."
    )
    @app_commands.describe(
        rule_id="ID of the AutoMod Rule.",
        new_name="Name of the AutoMod Rule.",
        event_type="Event Type of the AutoMod Rule.",
        use_previous_values="Whether to use the previous values or not. (Ignored Roles/Channels)",
        enabled="Whether the AutoMod Rule is enabled or not.",
        exempt_roles="Roles that are exempt from the AutoMod Rule. (Use Role IDs and separate them with a space)",
        exempt_channels="Channels that are exempt from the AutoMod Rule. (Use Channel IDs and separate them with a space)"
    )
    async def automod_edit(
            self,
            interaction: discord.Interaction,
            rule_id: str,
            new_name: str,
            event_type: Literal["Send Message"],
            use_previous_values: Literal["True", "False"],
            enabled: bool = True,
            exempt_roles: Optional[str] = None,
            exempt_channels: Optional[str] = None,
    ):
        await interaction.response.defer()
        event_type = {
            "Send Message": 1
        }.get(event_type)

        if use_previous_values == "True":
            rule = await self.bot.http.request(
                Route('GET', '/guilds/{guild_id}/auto-moderation/rules/{rule_id}', guild_id=interaction.guild_id,
                      rule_id=rule_id)
            )

            exempt_roles = exempt_roles or rule.get("exempt_roles")
            exempt_channels = exempt_channels or rule.get("exempt_channels")
        else:
            exempt_roles = [str(role_id) for role_id in exempt_roles.split(" ")] if exempt_roles else None
            exempt_channels = [str(channel_id) for channel_id in exempt_channels.split(" ")] if exempt_channels else None

        try:
            payload = {
                "name": new_name,
                "event_type": event_type,
                "enabled": enabled,
                "exempt_roles": exempt_roles,
                "exempt_channels": exempt_channels,
            }

            await self.bot.http.request(
                Route('PATCH', '/guilds/{guild_id}/auto-moderation/rules/{rule_id}', guild_id=interaction.guild_id,
                      rule_id=rule_id), json=payload,
                reason="AutoMod Rule Edited by Percy"
            )
        except Exception as e:
            match e:
                case ValueError():
                    return await interaction.followup.send("Invalid Role or Channel ID.")
                case _:
                    return await interaction.followup.send(
                        f"An error occurred while editing the rule.\n```py\n{traceback.format_exc()}```")

        await interaction.followup.send(
            f"<:greenTick:1079249732364406854> Rule `{rule_id}` edited with payload:\n```json\n{payload}```")

    @commands.Cog.listener()
    async def on_automod_action(self, execution: discord.AutoModAction):
        channel = self.bot.get_channel(1092584107319504986)
        if not channel:
            return

        if execution.guild_id != RH_MUSIC_GUILD_ID:
            return

        action_list = []
        for var, value in execution.action.to_dict().items():
            if isinstance(value, dict):
                action_list.append(f"- **{var.title()}**")
                for k, v in value.items():
                    action_list.append(f" - {k.title()}: `{v}`")
            else:
                action_list.append(f"- {var.title()}: `{value}`")

        fmt = "```\n{content}```"

        content = execution.content
        if len(content) > 3992:
            content = content[:3989] + "..."

        embed = discord.Embed(title="AutoMod Action",
                              description=fmt.format(content=content),
                              color=0xfc7769)
        embed.set_author(name=execution.guild.name, icon_url=execution.guild.icon.url)
        embed.set_thumbnail(url="https://i.imgur.com/bwmc8P7.png")
        embed.add_field(name="Rule Trigger Type",
                        value=str(execution.rule_trigger_type).replace("AutoModRuleTriggerType.", "").replace("_", " ").title())
        embed.add_field(name="User", value=f"{self.bot.get_user(execution.user_id).mention} ({execution.user_id})")
        embed.add_field(name="Action Performed", value='\n'.join(action for action in action_list), inline=False)
        text = " ***BLOCKED***"
        if mess_id := execution.message_id:
            text = f"/{mess_id}"
        embed.add_field(name="Message",
                        value=f"https://discord.com/channels/{execution.guild_id}/{execution.channel_id}{text}")
        await channel.send(embed=embed)


async def setup(bot: Percy):
    await bot.add_cog(AutoModerationManaging(bot))
