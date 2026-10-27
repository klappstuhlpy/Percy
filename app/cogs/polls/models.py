from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any, TypedDict

import discord
from discord.utils import MISSING

from app.cogs.polls.ui import PollClearVoteButton, PollEnterButton, PollInfoButton
from app.core.models import EmbedBuilder
from app.database import BaseRecord
from app.utils import helpers, pluralize
from config import Emojis

if TYPE_CHECKING:
    import datetime
    from collections.abc import Iterator
    from typing import Self

    from app.cogs.polls.cog import Polls
    from app.core import Bot

__all__ = (
    "Poll",
    "PollEntry",
    "VoteOption",
    "lineformat",
    "percentages",
)

_MAX_VOTE_BAR_LENGTH = 10


def lineformat(x: int) -> str:
    if not x:
        return Emojis.PollVoteBar.end

    txt = [Emojis.PollVoteBar.middle] * (x - 1)
    txt.append(Emojis.PollVoteBar.end)
    txt[0] = Emojis.PollVoteBar.start

    return "".join(txt)


def percentages(counts: list[int]) -> list[int]:
    """Whole-number percentages that always sum to exactly 100 (or all 0 with no votes).

    Plain ``round()`` lets three equal options read 33/33/33 = 99%; the largest-remainder
    method hands the leftover points to the options with the biggest fractional parts.
    """
    total = sum(counts)
    if not total:
        return [0] * len(counts)
    exact = [c * 100 / total for c in counts]
    result = [int(x) for x in exact]
    by_remainder = sorted(range(len(counts)), key=lambda i: exact[i] - result[i], reverse=True)
    for i in by_remainder[: 100 - sum(result)]:
        result[i] += 1
    return result


class VoteOption(TypedDict):
    """A vote option."""

    index: int
    content: str
    votes: int


class PollEntry(BaseRecord, pk="user_id"):
    """Represents a poll entry."""

    user_id: int
    vote: int

    __slots__ = ("user_id", "vote")

    def __iter__(self) -> Iterator[int]:
        return iter((self.user_id, self.vote))


class Poll(BaseRecord, table="polls", pk="id"):
    """Represents a poll item."""

    cog: Polls
    id: int
    message_id: int
    channel_id: int
    guild_id: int
    metadata: dict[str, Any]
    entries: set[tuple[int, int]]
    expires: datetime.datetime
    published: datetime.datetime

    # metadata
    args: list[Any]
    kwargs: dict[str, Any]
    message: discord.Message
    question: str
    votes: int
    description: str
    options: list[VoteOption]

    __slots__ = (
        "args",
        "bot",
        "channel_id",
        "cog",
        "color",
        "description",
        "entries",
        "expires",
        "guild_id",
        "id",
        "kwargs",
        "message",
        "message_id",
        "metadata",
        "options",
        "ping_message",
        "published",
        "question",
        "votes",
    )

    def __init__(self, **kwargs: Any) -> None:
        self.message: discord.Message = MISSING
        self.ping_message: discord.Message = MISSING
        super().__init__(**kwargs)
        self.bot: Bot = self.cog.bot

    def _coerce(self) -> None:
        self.args = self.metadata.get("args", [])
        self.kwargs = self.metadata.get("kwargs", {})
        # ``edit()`` and the dashboard PATCH store the question under ``content``, creation
        # under ``question`` -- the newer ``content`` wins, matching the dashboard's GET.
        self.question = self.kwargs.get("content") or self.kwargs.get("question") or "N/A"
        self.description = self.kwargs.get("description", "N/A")
        self.options = self.kwargs.get("options", [])
        try:
            self.color = helpers.Colour.from_str(self.kwargs.get("color") or "#ffffff")
        except ValueError:
            # A free-text colour (edit modal, dashboard) must not make the poll unloadable.
            self.color = helpers.Colour.white()
        # Plain ``(user_id, vote)`` tuples: DB composites and tuples index the same way.
        self.entries = {(int(e[0]), int(e[1])) for e in self.entries or []}
        self.recount()

    def recount(self) -> None:
        """Derive every vote counter from ``entries``, the only source of truth.

        Entries pointing at an option that no longer exists are dropped, so a removed
        option can't leave a voter locked out with a vote for nothing.
        """
        valid = {option["index"] for option in self.options}
        self.entries = {(user, vote) for user, vote in self.entries if vote in valid}
        for option in self.options:
            option["votes"] = sum(1 for _, vote in self.entries if vote == option["index"])
        self.votes = len(self.entries)
        self.kwargs["votes"] = self.votes

    @property
    def running(self) -> bool:
        return self.kwargs.get("running") is True

    @property
    def results_hidden(self) -> bool:
        """Whether counts stay hidden until the poll closes."""
        return self.running and bool(self.kwargs.get("hide_results"))

    def can_vote(self, user: discord.abc.User) -> bool:
        """Whether ``user`` holds the poll's required role (always true without one)."""
        role_id = self.kwargs.get("role_id")
        return not role_id or any(role.id == role_id for role in getattr(user, "roles", ()))

    def cast_vote(self, user_id: int, index: int) -> int | None:
        """Record ``user_id``'s vote for option ``index``, replacing an earlier vote.

        Returns the option index the user voted for before (``None`` if this is their first
        vote). Raises ``ValueError`` for an unknown option.
        """
        if self.get_option(index) is None:
            raise ValueError(f"unknown option {index}")
        previous = self.get_entry(user_id)
        self.entries = {entry for entry in self.entries if entry[0] != user_id}
        self.entries.add((user_id, index))
        self.recount()
        return previous[1] if previous else None

    def clear_vote(self, user_id: int) -> int | None:
        """Remove ``user_id``'s vote; returns the option index it was for, if any."""
        previous = self.get_entry(user_id)
        if previous is not None:
            self.entries.discard(previous)
            self.recount()
        return previous[1] if previous else None

    @property
    def jump_url(self) -> str | None:
        """The jump URL of the poll."""
        if self.message_id and self.channel_id:
            guild = self.guild_id or "@me"
            return f"https://discord.com/channels/{guild}/{self.channel_id}/{self.message_id}"
        return None

    @property
    def channel(self) -> discord.TextChannel | None:
        """The channel of the poll."""
        if self.channel_id is not None:
            return self.bot.get_channel(self.channel_id)  # type: ignore[return-value]
        return None

    @property
    def choice_text(self) -> str:
        """The text to use for the autocomplete."""
        return f"[{self.id}] {self.question}"

    def get_option(self, index: int) -> VoteOption | None:
        """Gets an option from the poll.

        Parameters
        ----------
        index: int
            The index of the option to get.

        Returns
        -------
        VoteOption
            The option from the poll.
        """
        return next((option for option in self.options if option["index"] == index), None)

    def get_entry(self, user_id: int) -> tuple[int, int] | None:
        """Gets the vote of a user.

        Parameters
        ----------
        user_id: int
            The user ID to get the vote of.

        Returns
        -------
        tuple[int, int] | None
            The vote of the user.
        """
        return next(((user, vote) for (user, vote) in self.entries if user == user_id), None)

    def to_fields(self, extras: bool = True) -> list[dict]:
        """Converts the poll to fields."""
        fields = []
        hidden = self.results_hidden
        shares = percentages([option["votes"] for option in self.options])
        for option, share in zip(self.options, shares, strict=True):
            v = option["votes"]
            votes = self.votes
            x = (v * _MAX_VOTE_BAR_LENGTH) // votes if votes else 0

            if hidden:
                value = f"{self.to_emoji(option['index'])} *Results are shown when the poll ends.*"
            else:
                value = f"{self.to_emoji(option['index'])}{lineformat(x)} **{v}** {pluralize(v, pass_content=True):vote} ({share}%)"
            fields.append({"name": f"{Emojis.PollVoteBar.corner} " + option["content"], "value": value, "inline": False})

        if extras:
            fields.append({"name": "Voting", "value": f"Total Votes: **{self.votes}**", "inline": True})
            if self.expires:
                fields.append({"name": "Poll ends", "value": discord.utils.format_dt(self.expires, "R"), "inline": True})
            if (thread := self.kwargs.get("thread", None)) is not None:
                fields.append({"name": "Discussion in Thread:", "value": thread, "inline": True})

        return fields

    async def fetch_message(self) -> None:
        """Fetches the message of the poll and if needed the ping_message."""
        channel = self.channel
        if channel is None:
            return

        if self.message_id is not None:
            message = await self.cog.get_message(channel, self.message_id)
            if message:
                self.message = message

        if (ping_message_id := self.kwargs.get("ping_message_id")) is not None:
            assert isinstance(ping_message_id, int)
            ping_message = await self.cog.get_message(channel, ping_message_id)
            if ping_message:
                self.ping_message = ping_message

    @staticmethod
    def to_emoji(index: int) -> str:
        INDEX_TO_LETTER = {0: "A", 1: "B", 2: "C", 3: "D", 4: "E", 5: "F", 6: "G", 7: "H"}
        return getattr(Emojis.PollVoteBar, INDEX_TO_LETTER[index])

    def to_embed(self) -> discord.Embed:
        """Converts the poll to an embed (used by the search/history command outputs)."""
        embed = EmbedBuilder(
            title=self.question,
            description=self.description,
            colour=self.color,
            timestamp=self.published,
            fields=self.to_fields(),
        )
        embed.set_image(url=self.kwargs.get("image", None))
        embed.set_footer(text=f"#{self.kwargs.get('index')} • [{self.id}]")
        return embed

    def to_container(self) -> discord.ui.Container:
        """Build the Components V2 card for the live poll message (the voting surface).

        Renders the question, the animated vote bars, an optional image gallery and a
        status line that reads "Poll ends" while running and "Poll finished" once closed.
        """
        container = discord.ui.Container(accent_colour=self.color)

        header = f"## {self.question}"
        if self.description and self.description != "N/A":
            header += f"\n{self.description}"
        container.add_item(discord.ui.TextDisplay(header))
        container.add_item(discord.ui.Separator())

        running = self.running
        for option, field in zip(self.options, self.to_fields(extras=False), strict=True):
            if running:
                item = discord.ui.Section(
                    f"{field['name']}\n{field['value']}",
                    # The option's own index, not its list position: the button resolves
                    # the vote through ``get_option(index)``.
                    accessory=PollEnterButton(self, option["index"])
                )
            else:
                item = discord.ui.TextDisplay(f"{field['name']}\n{field['value']}")
            container.add_item(item)

        if image := self.kwargs.get("image"):
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(image)))

        container.add_item(discord.ui.Separator())
        extras = [f"Total Votes: **{self.votes}**"]
        if self.expires:
            label = "Poll ends" if running else "Poll finished"
            extras.append(f"{label} {discord.utils.format_dt(self.expires, 'R')}")
        if thread := self.kwargs.get("thread"):
            extras.append(f"Thread: {thread}")
        if running and (role_id := self.kwargs.get("role_id")):
            extras.append(f"Only <@&{role_id}> can vote")

        container.add_item(discord.ui.TextDisplay(" • ".join(extras)))

        container.add_item(discord.ui.Separator())

        row = discord.ui.ActionRow()
        if running:
            row.add_item(PollClearVoteButton(self))
        row.add_item(PollInfoButton(self))

        container.add_item(row)
        container.add_item(discord.ui.TextDisplay(f"-# #{self.kwargs.get('index')} • [{self.id}]"))
        return container

    def remove_option(self, option: VoteOption = MISSING) -> list[VoteOption] | None:
        """Removes an option from the poll by erasing the votes and removing the option.

        Parameters
        ----------
        option: VoteOption
            The option to remove from the poll.
        """
        if len(self.options) > 2:
            self.options.remove(option)
            # Re-number the remaining options and move every vote with its option --
            # otherwise a vote for "C" would silently become a vote for whatever slid into C's slot.
            remap: dict[int, int] = {}
            for index, ch in enumerate(sorted(self.options, key=lambda x: x["index"])):
                remap[ch["index"]] = index
                ch["index"] = index

            self.entries = {(user, remap[vote]) for user, vote in self.entries if vote in remap}
            self.recount()
            return self.options
        return None

    async def edit(
        self,
        *,
        question: str | None = MISSING,
        description: str | None = MISSING,
        thread: list[int | str] | None = MISSING,
        image: str | None = MISSING,
        color: str | None = MISSING,
        options: list[VoteOption] | None = MISSING,
        running: bool | None = MISSING,
        votes: int | None = MISSING,
    ) -> Self:
        """|coro|

        Edits the poll.

        Parameters
        ----------
        question: str | None
            The question to update the poll with.
        description: str | None
            The description to update the poll with.
        thread: List[int, str]
            The thread to update the poll with.
        image: str | None
            The image URL to update the poll with.
        color: str | None
            The color to update the poll with.
        options: List[Tuple[Dict[str, Any] | None, EditType, int | None]]
            The options to update the poll with.
        running: bool | None
            The running status to update the poll with.
        votes: int | None
            The votes to update the poll with.

        Returns
        -------
        Self
            The updated poll.
        """
        form: dict[str, Any] = {}

        if question is not MISSING:
            form["content"] = question
        if description is not MISSING:
            form["description"] = description
        if thread is not MISSING:
            form["thread"] = thread
        if image is not MISSING:
            form["image"] = image
        if running is not MISSING:
            form["running"] = running
        if color is not MISSING:
            form["color"] = color
        if options is not MISSING:
            form["options"] = options
        if votes is not MISSING:
            form["votes"] = votes
            # NOTE: This is a temporary fix for the votes not updating properly.
            self.votes = votes  # type: ignore[misc]

        self.metadata.get("kwargs").update(form)  # type: ignore[union-attr]
        self.cog.get_guild_polls.invalidate(self.guild_id)
        # ``entries`` may hold ``PollEntry`` objects (from ``_coerce``) or raw
        # ``(user_id, vote)`` tuples appended by the vote callbacks; ``tuple()``
        # normalises both since ``PollEntry.__iter__`` yields the same pair.
        return await self.update(metadata=self.metadata, entries=[tuple(e) for e in self.entries])  # type: ignore[return-value]

    async def delete(self) -> None:
        """Deletes the poll from the database and removes the Discord message."""
        await super().delete()

        if self.message_id is not None and self.message is MISSING:
            await self.fetch_message()

        if self.message:
            with suppress(discord.HTTPException):
                await self.message.delete()

        self.cog.get_guild_polls.invalidate(self.guild_id)
