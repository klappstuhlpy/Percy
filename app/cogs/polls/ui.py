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

    async def on_submit(self, interaction: discord.Interaction) -> None:
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

        await interaction.response.send_message("Thank you for submitting your response.", ephemeral=True)

        with suppress(discord.HTTPException):
            assert interaction.guild is not None
            config = await self.bot.db.get_guild_config(guild_id=interaction.guild.id)
            if channel := config.poll_reason_channel:
                await channel.send(embed=embed)

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
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.response.send_message("Something broke!", ephemeral=True)
        traceback.print_tb(error.__traceback__)


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
        cog: Polls | None = interaction.client.get_cog("Polls")  # type: ignore[attr-defined]  # interaction.client is Bot
        if cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
            )
            raise AppBadArgument(f"{Emojis.error} Polls cog is not loaded")

        assert interaction.guild is not None
        poll = await cog.get_guild_poll(interaction.guild.id, int(match["id"]))
        if poll is None:
            raise AppBadArgument(f"{Emojis.error} Poll was not found")

        return cls(poll)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f"{Emojis.error} Poll was not found.", ephemeral=True)
            return False

        entry = self.poll.get_entry(interaction.user.id)
        if not entry:
            await interaction.response.send_message(
                f"You haven't voted on the poll *{self.poll.question}* [`{self.poll.id}`].", ephemeral=True
            )
            return False

        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.message is None:
            return

        entry = self.poll.get_entry(interaction.user.id)
        if entry is None:
            return

        # ``entries`` holds ``PollEntry`` objects whose hash/eq are record-based, so
        # discarding the plain ``(user, vote)`` tuple from ``get_entry`` would match
        # nothing. Rebuild the set by user id instead (handles tuples and PollEntry).
        self.poll.entries = {e for e in self.poll.entries if next(iter(e)) != interaction.user.id}

        option = self.poll.get_option(entry[1])
        if option is not None:
            option["votes"] = max(0, option["votes"] - 1)

        self.poll = await self.poll.edit(options=self.poll.options, votes=len(self.poll.entries))

        await interaction.response.edit_message(view=create_view(self.poll))


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
        cog: Polls | None = interaction.client.get_cog("Polls")  # type: ignore[attr-defined]  # interaction.client is Bot
        if cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
            )
            raise AppBadArgument(f"{Emojis.error} Polls cog is not loaded")

        assert interaction.guild is not None
        poll = await cog.get_guild_poll(interaction.guild.id, int(match["id"]))
        if poll is None:
            raise AppBadArgument(f"{Emojis.error} Poll was not found")

        return cls(poll)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f"{Emojis.error} Poll was not found.", ephemeral=True)
            return False

        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title=f"#{self.poll.kwargs.get('index')}: {self.poll.question}",
            colour=self.poll.color,
        )

        value = [field["value"] for field in self.poll.to_fields(extras=False)]
        embed.add_field(name="Votes", value="\n".join(value))

        vote = next((option for (user, option) in self.poll.entries if user == interaction.user.id), None)
        if vote:
            option = next((i for i in self.poll.options if i["index"] == vote), None)
            text = f"You've voted: {self.poll.to_emoji(option['index'])} *{option['content']}*" if option else "You've voted."  # type: ignore[index]
        else:
            text = "You haven't voted yet." if self.poll.kwargs.get("running") is True else "You didn't vote."

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
        cog: Polls | None = interaction.client.get_cog("Polls")  # type: ignore[attr-defined]  # interaction.client is Bot
        if cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
            )
            raise AppBadArgument(f"{Emojis.error} Polls cog is not loaded")

        assert interaction.guild is not None
        poll = await cog.get_guild_poll(interaction.guild.id, int(match["id"]))
        if poll is None:
            raise AppBadArgument(f"{Emojis.error} Poll was not found")

        return cls(poll, int(match["index"]))

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f"{Emojis.error} Poll was not found.", ephemeral=True)
            return False

        entry = self.poll.get_entry(interaction.user.id)
        if entry:
            vote = self.poll.get_option(entry[1])
            await interaction.response.send_message(
                f"On the poll *{self.poll.question}* [`{self.poll.id}`], you voted:\n"
                f"{self.poll.to_emoji(vote['index'])} - `{vote['content']}`",  # type: ignore[index]
                ephemeral=True,
            )
            return False

        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.message is None:
            return

        option = self.poll.get_option(self.index)
        if option is None:
            return
        if self.poll.kwargs.get("with_reason"):
            modal = PollReasonModal(self.poll, option, interaction.client)  # type: ignore
            await interaction.response.send_modal(modal)
            if await modal.wait():
                await interaction.followup.send(
                    content=f"{Emojis.error} This poll requires you to submit a reason for your vote.", ephemeral=True
                )
                return
            # The modal already consumed the interaction response, so refresh the
            # poll card with a direct message edit rather than via the interaction.
            self.poll.entries.add((interaction.user.id, option["index"]))  # type: ignore
            reason_options: list[VoteOption] = self.poll.options.copy()
            reason_options[option["index"]]["votes"] += 1
            self.poll = await self.poll.edit(options=reason_options, votes=len(self.poll.entries))
            await interaction.message.edit(view=create_view(self.poll))
            return

        self.poll.entries.add((interaction.user.id, option["index"]))  # type: ignore
        options: list[VoteOption] = self.poll.options.copy()
        options[option["index"]]["votes"] += 1
        self.poll = await self.poll.edit(options=options, votes=len(self.poll.entries))

        await interaction.response.edit_message(view=create_view(self.poll))
        await interaction.followup.send(
            f"On the poll *{self.poll.question}* [`{self.poll.id}`], you voted:\n"
            f"{self.poll.to_emoji(option['index'])} - `{option['content']}`",
            ephemeral=True,
        )


class PollEnterSelect(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r"poll:select:(?P<id>[0-9]+)",
):
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
        cog: Polls | None = interaction.client.get_cog("Polls")  # type: ignore
        if cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
            )
            raise AppBadArgument(f"{Emojis.error} Polls cog is not loaded")

        assert interaction.guild is not None
        poll = await cog.get_guild_poll(interaction.guild.id, int(match["id"]))
        if poll is None:
            raise AppBadArgument(f"{Emojis.error} Poll was not found")

        return cls(poll)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f"{Emojis.error} Poll was not found.", ephemeral=True)
            return False

        vote = next((option for (user, option) in self.poll.entries if user == interaction.user.id), None)
        if vote:
            option = next((i for i in self.poll.options if i["index"] == vote), None)
            await interaction.response.send_message(
                f"On the poll *{self.poll.question}* [`{self.poll.id}`], you voted:\n"
                f"{self.poll.to_emoji(option['index'])} - `{option['content']}`",
                ephemeral=True,
            )
            return False

        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.message is None:
            return

        option = self.poll.get_option(int(self.item.values[0]))
        if option is None:
            return
        if self.poll.kwargs.get("with_reason"):
            modal = PollReasonModal(self.poll, option, interaction.client)  # type: ignore
            await interaction.response.send_modal(modal)
            if await modal.wait():
                await interaction.followup.send(
                    content=f"{Emojis.error} This poll requires you to submit a reason for your vote.", ephemeral=True
                )
                return
            # The modal already consumed the interaction response, so refresh the
            # poll card with a direct message edit rather than via the interaction.
            self.poll.entries.add((interaction.user.id, option["index"]))  # type: ignore
            reason_options: list[VoteOption] = self.poll.options.copy()
            reason_options[option["index"]]["votes"] += 1
            self.poll = await self.poll.edit(options=reason_options, votes=len(self.poll.entries))
            await interaction.message.edit(view=create_view(self.poll))
            return

        self.poll.entries.add((interaction.user.id, option["index"]))  # type: ignore
        options: list[VoteOption] = self.poll.options.copy()
        options[option["index"]]["votes"] += 1
        self.poll = await self.poll.edit(options=options, votes=len(self.poll.entries))

        await interaction.response.edit_message(view=create_view(self.poll))
        await interaction.followup.send(
            f"On the poll *{self.poll.question}* [`{self.poll.id}`], you voted:\n"
            f"{self.poll.to_emoji(option['index'])} - `{option['content']}`",
            ephemeral=True,
        )


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
        assert isinstance(interaction.user, discord.Member)
        role = discord.Object(id=self.role_id)
        if any(r.id == self.role_id for r in interaction.user.roles):
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(
                f"{Emojis.success} Successfully **removed** from you these roles: "
                f"<@&{self.role_id}>. Click again to re-add.",
                ephemeral=True,
            )
        else:
            await interaction.user.add_roles(role)
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
