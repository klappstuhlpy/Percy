from __future__ import annotations

import io
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
)

_MAX_VOTE_BAR_LENGTH = 10


def lineformat(x: int) -> str:
    if not x:
        return Emojis.PollVoteBar.end

    txt = [Emojis.PollVoteBar.middle] * (x - 1)
    txt.append(Emojis.PollVoteBar.end)
    txt[0] = Emojis.PollVoteBar.start

    return "".join(txt)


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
        self.message: discord.Message = MISSING
        self.ping_message: discord.Message = MISSING
        super().__init__(**kwargs)
        self.bot: Bot = self.cog.bot

    def _coerce(self) -> None:
        self.args = self.metadata.get("args", [])
        self.kwargs = self.metadata.get("kwargs", {})
        self.question = self.kwargs.get("question", "N/A")
        self.votes = self.kwargs.get("votes", 0)
        self.description = self.kwargs.get("description", "N/A")
        self.options = self.kwargs.get("options", [])
        self.color = helpers.Colour.from_str(self.kwargs.get("color") or "#ffffff")
        self.entries = {PollEntry(record=entry) for entry in self.entries or []}

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

        if image_bytes := self.kwargs.get("image_bytes"):
            container.add_item(discord.ui.MediaGallery(discord.MediaGalleryItem(discord.File(io.BytesIO(image_bytes), filename="attachment://image.png"))))

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
        image: str | None = MISSING,
        image_bytes: io.BytesIO | None = MISSING,
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
        image_bytes: io.BytesIO | None
            The image bytes to update the poll with. Will be prioritized if "image" is also supplied!
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
        if image is not MISSING and image_bytes is MISSING:
            form["image"] = image
        if image_bytes is not MISSING:
            form["image_bytes"] = image_bytes
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
