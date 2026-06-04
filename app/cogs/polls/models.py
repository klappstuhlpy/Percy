from __future__ import annotations

import random
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
    from collections.abc import Callable, Iterator
    from typing import Self

    import asyncpg

    from app.cogs.polls.cog import Polls
    from app.core import Bot

__all__ = (
    "Poll",
    "PollEntry",
    "VoteOption",
    "lineformat",
    "uuid",
)

_MAX_VOTE_BAR_LENGTH = 10


def lineformat(x: int) -> str:
    if not x:
        return Emojis.PollVoteBar.end

    txt = [Emojis.PollVoteBar.middle] * (x - 1)
    txt.append(Emojis.PollVoteBar.end)
    txt[0] = Emojis.PollVoteBar.start

    return "".join(txt)


def uuid(ids: list[int]) -> int:
    _id = random.randint(10000, 99999)
    while _id in ids:
        _id = random.randint(10000, 99999)
    return _id


class VoteOption(TypedDict):
    """A vote option."""

    index: int
    content: str
    votes: int


class PollEntry(BaseRecord):
    """Represents a poll entry."""

    user_id: int
    vote: int

    __slots__ = ("user_id", "vote")

    def __iter__(self) -> Iterator[int]:
        return iter((self.user_id, self.vote))


class Poll(BaseRecord):
    """Represents a poll item."""

    cog: Polls
    id: int
    message_id: int
    channel_id: int
    guild_id: int
    metadata: dict[str, Any]
    entries: set[PollEntry]
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
        super().__init__(**kwargs)
        self.bot: Bot = self.cog.bot

        self.args: list[Any] = self.metadata.get("args", [])
        self.kwargs: dict[str, Any] = self.metadata.get("kwargs", {})

        self.message: discord.Message = MISSING
        self.ping_message: discord.Message = MISSING

        self.question: str = self.kwargs.get("question", "N/A")
        self.votes: int = self.kwargs.get("votes", 0)
        self.description: str = self.kwargs.get("description", "N/A")
        self.options: list[VoteOption] = self.kwargs.get("options", [])
        self.color: helpers.Colour = helpers.Colour.from_str(self.kwargs.get("color") or "#ffffff")

        self.entries: set[PollEntry] = {PollEntry(record=entry) for entry in self.entries or []}

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

    async def _update(
        self,
        key: Callable[[tuple[int, str]], str],
        values: dict[str, Any],
        *,
        connection: asyncpg.Connection | None = None,
    ) -> Poll:
        """|coro|

        Updates the poll.

        Parameters
        ----------
        key: Callable[[tuple[int, str]], str]
            The key to use for the update.
        values: dict[str, Any]
            The values to use for the update.
        connection: asyncpg.Connection
            The connection to use for the update.

        Returns
        -------
        Poll
            The updated poll.
        """
        record = await self.bot.db.polls.update(self.id, key, values, connection=connection)

        cls = self.__class__(cog=self.cog, record=record)
        cls.message = self.message
        cls.ping_message = self.ping_message
        return cls

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
        for i, option in enumerate(self.options):
            v = option["votes"]
            votes = self.votes

            p = v / votes if votes else 0
            x = (v * _MAX_VOTE_BAR_LENGTH) // votes if votes else 0

            fields.append(
                {
                    "name": f"{Emojis.PollVoteBar.corner} " + option["content"],
                    "value": f"{self.to_emoji(option['index'])}{lineformat(x)} **{v}** {pluralize(v, pass_content=True):vote} ({round(p * 100)}%)",
                    "inline": False
                }
            )

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

        running = self.kwargs.get("running") is True
        for i, field in enumerate(self.to_fields(extras=False)):
            if running:
                item = discord.ui.Section(
                    f"{field['name']}\n{field['value']}",
                    accessory=PollEnterButton(self, i)
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
            for index, ch in enumerate(sorted(self.options, key=lambda x: x["index"])):
                ch["index"] = index

            self.votes -= option["votes"]
            self.entries = {
                PollEntry.temporary(user, user_option)
                for user, user_option in self.entries
                if user_option != option["index"]
            }
            return self.options
        return None

    async def edit(
        self,
        *,
        question: str | None = MISSING,
        description: str | None = MISSING,
        thread: list[int | str] | None = MISSING,
        image_url: str | None = MISSING,
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
        image_url: str | None
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
        if image_url is not MISSING:
            form["image_url"] = image_url
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
        return await self.update(metadata=self.metadata, entries=[(e.user_id, e.vote) for e in self.entries])  # type: ignore[return-value]

    async def delete(self) -> None:
        """Deletes the poll."""
        await self.bot.db.polls.delete(self.id)

        if self.message_id is not None and self.message is MISSING:
            await self.fetch_message()

        if self.message:
            with suppress(discord.HTTPException):
                await self.message.delete()

        self.cog.get_guild_polls.invalidate(self.guild_id)
