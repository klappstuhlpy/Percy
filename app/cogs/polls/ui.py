from __future__ import annotations

import traceback
from contextlib import suppress
from typing import TYPE_CHECKING

import discord

from app.core import LayoutView
from app.core.models import AppBadArgument
from app.utils import get_asset_url, helpers
from config import Emojis

if TYPE_CHECKING:
    import re
    from typing import Any

    from app.cogs.polls.cog import Polls
    from app.cogs.polls.models import Poll, VoteOption
    from app.core import Bot

__all__ = (
    "EditModal",
    "PollClearVoteButton",
    "PollEnterButton",
    "PollEnterSelect",
    "PollInfoButton",
    "PollReasonModal",
    "PollRolePingButton",
    "create_view",
)


class PollReasonModal(discord.ui.Modal, title="The Reason for you choice."):
    def __init__(self, poll: Poll, selected_option: dict[str, Any], bot: Bot) -> None:
        super().__init__(timeout=60.0)
        self.poll = poll
        self.bot = bot
        self.selected_option = selected_option

    reason = discord.ui.TextInput(
        label="Reason",
        placeholder="Why did you choose this option.",
        style=discord.TextStyle.long,
        min_length=1,
        max_length=200,
    )

    async def post_reason(self, interaction: discord.Interaction) -> None:
        """Post the reason to the guild's reason channel; call only once the vote was saved."""
        embed = discord.Embed(title="New Poll Reason", color=helpers.Colour.white())
        guild = interaction.guild
        embed.set_thumbnail(url=get_asset_url(guild) if guild else None)
        embed.set_author(name=interaction.user, icon_url=get_asset_url(interaction.user))
        embed.add_field(name="Poll", value=f"{self.poll.question}\n{self.poll.jump_url}", inline=False)
        embed.add_field(name="Reason", value=self.reason.value, inline=False)
        embed.add_field(
            name="Selected Option",
            value=f"{self.poll.to_emoji(self.selected_option['index'])}: {self.selected_option['content']}",
            inline=False,
        )
        embed.set_footer(text=f"#{self.poll.kwargs.get('index')} • [{self.poll.id}]")

        with suppress(discord.HTTPException):
            assert interaction.guild is not None
            config = await self.bot.db.get_guild_config(guild_id=interaction.guild.id)
            if channel := config.poll_reason_channel:
                await channel.send(embed=embed)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("Thank you for submitting your response.", ephemeral=True)
        self.stop()


class EditModal(discord.ui.Modal, title="Edit Poll"):
    question = discord.ui.TextInput(label="Question", placeholder="The Main Question for the poll.")
    description = discord.ui.TextInput(
        label="Description", placeholder="The Description for the poll.", style=discord.TextStyle.long, required=False
    )
    thread_question = discord.ui.TextInput(
        label="Thread Question", placeholder="The Question for the thread.", required=False
    )
    image = discord.ui.TextInput(label="Image URL", placeholder="The Image URL for the poll.", required=False)
    color = discord.ui.TextInput(label="Color", placeholder="The Color for the poll.", required=False)

    def __init__(self, poll: Poll) -> None:
        super().__init__(title=f"Edit Poll [{poll.id}]", timeout=180.0)

        self.question.default = poll.question
        self.description.default = poll.description
        self.thread_question.default = poll.kwargs.get("thread", None)
        self.image.default = poll.kwargs.get("image", None)
        self.color.default = poll.kwargs.get("color", None)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        # Acknowledge the submit; the command replies through its own interaction.
        # Without this Discord reports "Something went wrong" on every successful edit.
        await interaction.response.defer()
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.response.send_message("Something broke!", ephemeral=True)
        traceback.print_tb(error.__traceback__)


def _vote_text(poll: Poll, index: int, *, prefix: str = "you voted") -> str:
    option = poll.get_option(index)
    content = option["content"] if option else "?"
    return f"On the poll *{poll.question}* [`{poll.id}`], {prefix}:\n{poll.to_emoji(index)} - `{content}`"


_OUTCOME_ERRORS: dict[str, str] = {
    "missing": f"{Emojis.error} That poll or option no longer exists.",
    "ended": f"{Emojis.error} This poll has already ended.",
    "forbidden": f"{Emojis.error} You don't have the role required to vote on this poll.",
    "novote": "You haven't voted on this poll.",
}


async def _get_cog(interaction: discord.Interaction) -> Polls:
    cog: Polls | None = interaction.client.get_cog("Polls")  # type: ignore[attr-defined]  # interaction.client is Bot
    if cog is None:
        await interaction.response.send_message(
            f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
        )
        raise AppBadArgument(f"{Emojis.error} Polls cog is not loaded")
    return cog


async def _load_poll(interaction: discord.Interaction, poll_id: int) -> Poll:
    cog = await _get_cog(interaction)
    assert interaction.guild is not None
    poll = await cog.get_guild_poll(interaction.guild.id, poll_id)
    if poll is None:
        raise AppBadArgument(f"{Emojis.error} Poll was not found")
    return poll


async def _precheck_vote(poll: Poll, interaction: discord.Interaction, index: int) -> bool:
    """Reject a vote early (before a reason modal opens) when it can't succeed.

    ``Polls.submit_vote`` re-checks all of this under the poll lock; this only spares the
    voter typing a reason for a vote that would be refused anyway.
    """
    error: str | None = None
    if not poll.running:
        error = _OUTCOME_ERRORS["ended"]
    elif not poll.can_vote(interaction.user):
        error = _OUTCOME_ERRORS["forbidden"]
    elif (entry := poll.get_entry(interaction.user.id)) is not None and entry[1] == index:
        error = _vote_text(poll, index)

    if error is not None:
        await interaction.response.send_message(error, ephemeral=True)
        return False
    return True


async def _submit_vote(poll: Poll, interaction: discord.Interaction, index: int) -> None:
    """Shared vote path of the option buttons and the legacy select."""
    if interaction.message is None or interaction.guild is None:
        return

    option = poll.get_option(index)
    if option is None:
        await interaction.response.send_message(_OUTCOME_ERRORS["missing"], ephemeral=True)
        return

    responded = False
    modal: PollReasonModal | None = None
    if poll.kwargs.get("with_reason"):
        modal = PollReasonModal(poll, option, interaction.client)  # type: ignore[arg-type]
        await interaction.response.send_modal(modal)
        if await modal.wait():
            await interaction.followup.send(
                content=f"{Emojis.error} This poll requires you to submit a reason for your vote.", ephemeral=True
            )
            return
        # The modal consumed the interaction response: refresh the card by direct edit.
        responded = True

    cog = await _get_cog(interaction)
    fresh, outcome = await cog.submit_vote(interaction.guild.id, poll.id, interaction.user, index)
    if fresh is None or outcome not in ("voted", "changed", "unchanged"):
        send = interaction.followup.send if responded else interaction.response.send_message
        await send(_OUTCOME_ERRORS.get(outcome, _OUTCOME_ERRORS["missing"]), ephemeral=True)
        return

    if modal is not None:
        await modal.post_reason(interaction)

    view = create_view(fresh)
    if responded:
        await interaction.message.edit(view=view)
    else:
        await interaction.response.edit_message(view=view)
    prefix = "you changed your vote to" if outcome == "changed" else "you voted"
    await interaction.followup.send(_vote_text(fresh, index, prefix=prefix), ephemeral=True)


class PollClearVoteButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"poll:clear:(?P<id>[0-9]+)",
):
    def __init__(self, poll: Poll) -> None:
        self.poll: Poll = poll
        super().__init__(
            discord.ui.Button(label="Clear Vote", style=discord.ButtonStyle.red, row=1, custom_id=f"poll:clear:{poll.id}")
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, _, match: re.Match[str], /) -> PollClearVoteButton:
        return cls(await _load_poll(interaction, int(match["id"])))

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return interaction.guild_id is not None

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.guild is None:
            return
        cog = await _get_cog(interaction)
        fresh, outcome = await cog.submit_vote(interaction.guild.id, self.poll.id, interaction.user, None)
        if fresh is None or outcome != "cleared":
            await interaction.response.send_message(_OUTCOME_ERRORS.get(outcome, _OUTCOME_ERRORS["missing"]), ephemeral=True)
            return
        await interaction.response.edit_message(view=create_view(fresh))


class PollInfoButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"poll:info:(?P<id>[0-9]+)",
):
    def __init__(self, poll: Poll) -> None:
        self.poll: Poll = poll
        super().__init__(
            discord.ui.Button(
                emoji=Emojis.PollVoteBar.info, style=discord.ButtonStyle.grey, row=1, custom_id=f"poll:info:{poll.id}"
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, _, match: re.Match[str], /) -> PollInfoButton:
        return cls(await _load_poll(interaction, int(match["id"])))

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return interaction.guild_id is not None

    async def callback(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title=f"#{self.poll.kwargs.get('index')}: {self.poll.question}",
            colour=self.poll.color,
        )

        value = [field["value"] for field in self.poll.to_fields(extras=False)]
        embed.add_field(name="Votes", value="\n".join(value))

        entry = self.poll.get_entry(interaction.user.id)
        # ``is not None``: option A has index 0, which a truthiness check reads as "no vote".
        if entry is not None:
            option = self.poll.get_option(entry[1])
            text = f"You've voted: {self.poll.to_emoji(option['index'])} *{option['content']}*" if option else "You've voted."
        else:
            text = "You haven't voted yet." if self.poll.running else "You didn't vote."

        embed.add_field(name="Your Vote", value=text, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


class PollEnterButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"poll:enter:(?P<id>[0-9]+):option:(?P<index>[0-9]+)",
):
    def __init__(self, poll: Poll, index: int) -> None:
        self.poll: Poll = poll
        self.index: int = index
        super().__init__(
            discord.ui.Button(
                emoji=poll.to_emoji(index), style=discord.ButtonStyle.gray, custom_id=f"poll:enter:{poll.id}:option:{index}"
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, _, match: re.Match[str], /) -> PollEnterButton:
        return cls(await _load_poll(interaction, int(match["id"])), int(match["index"]))

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.guild_id is None:
            return False
        return await _precheck_vote(self.poll, interaction, self.index)

    async def callback(self, interaction: discord.Interaction) -> None:
        await _submit_vote(self.poll, interaction, self.index)


class PollEnterSelect(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r"poll:select:(?P<id>[0-9]+)",
):
    """Legacy vote select -- no longer rendered, kept so old poll messages still work."""

    def __init__(self, poll: Poll) -> None:
        self.poll: Poll = poll
        super().__init__(
            discord.ui.Select(
                placeholder="Select the option to vote for...",
                row=0,
                custom_id=f"poll:select:{poll.id}",
                options=[
                    discord.SelectOption(
                        label=option["content"], value=str(option["index"]), emoji=poll.to_emoji(option["index"])
                    )
                    for option in poll.options
                ],
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, _, match: re.Match[str], /) -> PollEnterSelect:
        return cls(await _load_poll(interaction, int(match["id"])))

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.guild_id is None:
            return False
        return await _precheck_vote(self.poll, interaction, int(self.item.values[0]))

    async def callback(self, interaction: discord.Interaction) -> None:
        await _submit_vote(self.poll, interaction, int(self.item.values[0]))


class PollRolePingButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r"poll:ping:role:(?P<role_id>[0-9]+)",
):
    def __init__(self, role_id: int) -> None:
        self.role_id: int = role_id
        super().__init__(
            discord.ui.Button(label="Add/Remove Role", style=discord.ButtonStyle.grey, custom_id=f"poll:ping:role:{role_id}")
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, _, match: re.Match[str], /) -> PollRolePingButton:
        cog: Polls | None = interaction.client.get_cog("Polls")  # type: ignore
        if cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
            )
            raise AssertionError(f"{Emojis.error} Polls cog is not loaded")

        return cls(int(match["role_id"]))

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        return interaction.guild_id is not None

    async def callback(self, interaction: discord.Interaction) -> Any:
        assert isinstance(interaction.user, discord.Member) and interaction.guild is not None
        # The role id rides in the custom_id; only ever toggle the role currently configured
        # as the poll ping role, never an arbitrary id (or a role the server since changed).
        config = await interaction.client.db.get_guild_config(interaction.guild.id)  # type: ignore[attr-defined]
        if config is None or config.poll_ping_role_id != self.role_id:
            await interaction.response.send_message(
                f"{Emojis.error} This role is no longer the poll notification role.", ephemeral=True
            )
            return

        role = discord.Object(id=self.role_id)
        try:
            await self._toggle(interaction, interaction.user, role)
        except discord.Forbidden:
            await interaction.response.send_message(
                f"{Emojis.error} I'm not allowed to manage that role. Please tell a moderator.", ephemeral=True
            )

    async def _toggle(self, interaction: discord.Interaction, member: discord.Member, role: discord.Object) -> None:
        if any(r.id == self.role_id for r in member.roles):
            await member.remove_roles(role)
            await interaction.response.send_message(
                f"{Emojis.success} Successfully **removed** from you these roles: "
                f"<@&{self.role_id}>. Click again to re-add.",
                ephemeral=True,
            )
        else:
            await member.add_roles(role)
            await interaction.response.send_message(
                f"{Emojis.success} Successfully **added** you the roles: <@&{self.role_id}>. Click again to remove.",
                ephemeral=True,
            )


def create_view(poll: Poll) -> LayoutView:
    """Build the Components V2 poll message: the card plus its (persistent) vote controls.

    The vote buttons/select, clear-vote and info buttons keep their stable ``custom_id``s,
    so the persistent ``DynamicItem`` registration survives restarts exactly as before.
    """
    view = LayoutView(timeout=None)
    view.add_item(poll.to_container())
    return view
