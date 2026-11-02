"""Member reports: filing (command, context menu, modal) and the persistent moderator buttons."""

from __future__ import annotations

import datetime
import json
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import discord

from app.cogs.moderation.member_actions import HIERARCHY_ACTIONS, MemberActionError, apply_member_action
from app.services.reports import REPORT_ACTIONS, check_report_rate, report_meta
from app.utils import truncate
from app.utils.checks import hierarchy_error

from .decide import notify

if TYPE_CHECKING:
    import re

    from app.core import Bot

log = logging.getLogger(__name__)

__all__ = ("ReportButton", "ReportModal", "file_report")

TIMEOUT_SECONDS = 3600


def report_embed(meta: dict[str, Any], report_id: int, *, handled: str | None = None) -> discord.Embed:
    embed = discord.Embed(
        title=f"Report #{report_id}",
        description=meta["reason"],
        color=0x80848E if handled else 0xF0B232,
    )
    embed.add_field(name="Reported", value=f"<@{meta['target_id']}>")
    embed.add_field(name="By", value=f"<@{meta['reporter_id']}>")
    if meta.get("message_url"):
        embed.add_field(name="Message", value=f"[Jump]({meta['message_url']})")
    if meta.get("excerpt"):
        embed.add_field(name="Content", value=truncate(meta["excerpt"], 300), inline=False)
    if handled:
        embed.add_field(name="Handled", value=handled, inline=False)
    else:
        embed.set_footer(text="Needs a moderator with the matching permission.")
    return embed


def report_view(report_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for verb in REPORT_ACTIONS:
        view.add_item(ReportButton(verb, report_id))
    return view


async def file_report(
    bot: Bot, guild: discord.Guild, reporter: discord.Member, target: discord.abc.User | discord.Member, reason: str,
    *, message: discord.Message | None = None,
) -> str:  # fmt: skip
    """Records a report and posts it to the mod queue. Returns the text to show the reporter."""
    if target.id == reporter.id:
        return "You can't report yourself."
    if target.bot:
        return "Bots can't be reported."
    repo = bot.db.notifications
    now = datetime.datetime.now(datetime.UTC).replace(tzinfo=None)
    recent = await repo.recent_reports(guild.id, reporter.id, now - datetime.timedelta(hours=1))
    if error := check_report_rate(recent, target.id, now):
        return error

    meta = report_meta(
        reporter_id=reporter.id,
        target_id=target.id,
        reason=reason[:500],
        channel_id=message.channel.id if message else None,
        message_url=message.jump_url if message else None,
        content=message.content if message else None,
    )
    report_id = await repo.add(
        guild.id,
        kind="report",
        severity="warning",
        title=f"{reporter} reported {target}",
        body=meta["reason"],
        link=f"/dashboard/guild/{guild.id}/notifications",
        meta=meta,
    )
    if report_id is None:
        return "Could not record your report. Try again later."

    config = await bot.db.get_guild_config(guild.id)
    channel = guild.get_channel(config.request_channel_id) if config.request_channel_id else None
    if isinstance(channel, discord.TextChannel):
        try:
            await channel.send(
                embed=report_embed(meta, report_id),
                view=report_view(report_id),
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            await config.alert_missing_permission("post member reports", channel=channel)
    return "Thanks, your report was sent to the moderators. The reported member will not see it."


class ReportModal(discord.ui.Modal, title="Report message"):
    reason: discord.ui.TextInput = discord.ui.TextInput(
        label="Why are you reporting this?", style=discord.TextStyle.paragraph, max_length=500
    )

    def __init__(self, bot: Bot, message: discord.Message) -> None:
        super().__init__()
        self.bot = bot
        self.message = message

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("Use this in a server.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        text = await file_report(
            self.bot, interaction.guild, interaction.user, self.message.author, self.reason.value, message=self.message
        )
        await interaction.followup.send(text, ephemeral=True)


class ReportButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"rep:(?P<verb>dismiss|warn|timeout|ban):(?P<id>[0-9]+)"
):
    def __init__(self, verb: str, report_id: int) -> None:
        self.verb = verb
        self.report_id = report_id
        label = "Timeout 1h" if verb == "timeout" else verb.title()
        style = discord.ButtonStyle.red if verb == "ban" else discord.ButtonStyle.secondary
        super().__init__(discord.ui.Button(label=label, style=style, custom_id=f"rep:{verb}:{report_id}"))

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, item: Any, match: re.Match[str], /) -> ReportButton:
        return cls(match["verb"], int(match["id"]))

    async def callback(self, interaction: discord.Interaction[Bot]) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True, thinking=True)
        guild, moderator = interaction.guild, interaction.user
        if guild is None or not isinstance(moderator, discord.Member):
            await interaction.followup.send("Use this in the server.", ephemeral=True)
            return
        bot, repo = interaction.client, interaction.client.db.notifications
        row = await repo.get_report(guild.id, self.report_id)
        if row is None:
            await interaction.followup.send("Report not found.", ephemeral=True)
            return
        meta = row["meta"] if isinstance(row["meta"], dict) else json.loads(row["meta"])
        if meta.get("handled_by"):
            await interaction.followup.send("This report was already handled.", ephemeral=True)
            return
        target_id = int(meta["target_id"])
        if moderator.id == target_id:
            await interaction.followup.send("You can't act on a report about yourself.", ephemeral=True)
            return

        action, permission = REPORT_ACTIONS[self.verb]
        perms = moderator.guild_permissions
        if not (perms.administrator or getattr(perms, permission, False)):
            label = permission.replace("_", " ").title()
            await interaction.followup.send(f"You need the {label} permission.", ephemeral=True)
            return
        if action:
            target = guild.get_member(target_id) or discord.Object(id=target_id)
            if error := hierarchy_error(moderator, target, guild.me, action=action, check_bot=action in HIERARCHY_ACTIONS):
                await interaction.followup.send(error, ephemeral=True)
                return

        if not await repo.claim_report(guild.id, self.report_id, handler_id=moderator.id, outcome=self.verb):
            await interaction.followup.send("This report was already handled.", ephemeral=True)
            return

        if action:
            try:
                await apply_member_action(
                    bot,
                    guild,
                    moderator,
                    target_id,
                    action=action,
                    reason=f"Report #{self.report_id}: {truncate(meta['reason'], 200)}",
                    duration=TIMEOUT_SECONDS if action == "timeout" else None,
                )
            except Exception as exc:  # release the claim: the report stays open with its buttons
                if not isinstance(exc, MemberActionError):
                    log.exception("Report %s action failed unexpectedly.", self.report_id)
                await repo.release_report_claim(guild.id, self.report_id)
                why = exc.detail if isinstance(exc, MemberActionError) else "unexpected error"
                await interaction.followup.send(f"Report #{self.report_id}: {self.verb} failed ({why}). It is still open.", ephemeral=True)
                return
        outcome = "Dismissed" if action is None else self.verb.title()
        handled = f"{outcome} by {moderator.mention}"
        with suppress(discord.HTTPException):
            if interaction.message is not None:
                await interaction.message.edit(
                    embed=report_embed(meta, self.report_id, handled=handled),
                    view=None,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
        await notify(
            bot,
            guild.id,
            kind="request_decided",
            title=f"Report #{self.report_id} handled",
            body=handled,
            link=f"/dashboard/guild/{guild.id}/notifications",
        )
        await interaction.followup.send(f"Report #{self.report_id}: done.", ephemeral=True)
