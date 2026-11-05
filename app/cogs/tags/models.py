from __future__ import annotations

import datetime  # record annotations
import io
from typing import TYPE_CHECKING, Any, Literal, cast

import asyncpg
import discord
from discord.ext import commands

from app.core import Bot, Context, Flags, flag, store_true
from app.core.models import BadArgument
from app.database import BaseRecord

if TYPE_CHECKING:
    from collections.abc import Callable




if TYPE_CHECKING:
    from app.cogs.tags.cog import Tags

# Tag content is user-authored and only `clean_content`-ed on the prefix path (modals skip it), so every
# send of it must refuse to ping: no @everyone/@here, no roles, no users. Replying still pings the replied user.
TAG_MENTIONS = discord.AllowedMentions(everyone=False, roles=False, users=False, replied_user=True)
# Meta cards (info/stats/lists) render owners as mentions purely for display.
NO_MENTIONS = discord.AllowedMentions.none()
CONTENT_LIMIT = 2000
PENDING_OWNER = -1  # owner_id while a transfer request awaits the recipient
ALIASES_SHOWN = 12  # keeps the info card under the 4000-char CV2 text cap even with fully-escaped 100-char names


def content_payload(content: str, *, filename: str = "tag.txt") -> dict[str, Any]:
    """Send kwargs for tag content: plain text when it fits, a text file when it does not (e.g. escaped raw output)."""
    if len(content) <= CONTENT_LIMIT:
        return {"content": content, "allowed_mentions": TAG_MENTIONS}
    return {"file": discord.File(io.BytesIO(content.encode("utf-8")), filename), "allowed_mentions": TAG_MENTIONS}



# region Converters & Flags


class TagPageEntry(BaseRecord, table="tags", pk="id"):
    id: int
    name: str

    __slots__ = ("id", "name")

    def __str__(self) -> str:
        return f"{discord.utils.escape_markdown(self.name)} [`{self.id}`]"


class TagNameOrID(commands.clean_content):
    """Converts the content to either an integer or string."""

    def __init__(self, *, lower: bool = False, with_id: bool = False) -> None:
        self.lower: bool = lower
        self.with_id: bool = with_id
        super().__init__()

    async def convert(self, ctx: Context, argument: str) -> str | int:
        converted = await super().convert(ctx, argument)
        lower = converted.lower().strip()

        if not lower:
            raise BadArgument("Please enter a valid tag name" + (" or id." if self.with_id else "."))

        if len(lower) > 100:
            raise BadArgument(f"Tag names must be 100 characters or less. (You have *{len(lower)}* characters)")

        cog: Tags | None = cast("Tags | None", ctx.bot.get_cog("Tags"))
        if cog is None:
            raise BadArgument("Tags are currently unavailable.")

        if ctx.guild is None:
            raise BadArgument("This command can only be used in a server.")

        if cog.is_tag_reserved(ctx.guild.id, argument):
            raise BadArgument("Hey, that's a reserved tag name. Choose another one.")

        if self.with_id and converted and converted.isdigit():
            return int(converted)

        return converted.strip() if not self.lower else lower


class TagContent(commands.clean_content):
    """Converts a commands content to a tag like content."""

    def __init__(self, *, required: bool = True) -> None:
        self.required = required
        super().__init__()

    async def convert(self, ctx: Context, argument: str) -> str:
        if not argument and not self.required:
            return argument

        converted = await super().convert(ctx, argument)

        if len(converted) > 2000:
            raise BadArgument(f"Tag content must be 2000 characters or less. (You have *{len(converted)}* characters)")

        return converted


class TagSearchFlags(Flags):
    sort: Literal["name", "newest", "oldest", "id"] = flag(
        description="The key to sort the results.", aliases=["s"], default="name"
    )
    to_text: bool = store_true(description="Whether to output the results as raw tabular text.", aliases=["tt"])


class TagListFlags(Flags):
    member: discord.Member | None = flag(description="The member to search for", aliases=["m"])
    sort: Literal["name", "newest", "oldest", "id"] = flag(
        description="The key to sort the results.", aliases=["s"], default="name"
    )
    to_text: bool = store_true(description="Whether to output the results as raw tabular text.", aliases=["tt"])


# endregion

# region Domain Models


class Tag(BaseRecord, table="tags", pk="id"):
    """Represents a Tag."""

    bot: Bot
    id: int
    name: str
    content: str
    owner_id: int
    uses: int
    location_id: int
    created_at: datetime.datetime
    use_embed: bool

    __slots__ = (
        "aliases",
        "bot",
        "content",
        "created_at",
        "id",
        "location_id",
        "name",
        "owner_id",
        "use_embed",
        "uses",
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.aliases: list[AliasTag] = []

    @property
    def choice_text(self) -> str:
        return f"[{self.id}] {self.name}"

    @property
    def raw_content(self) -> str:
        return discord.utils.escape_markdown(self.content)

    async def _update(
        self,
        key: Callable[[tuple[int, str]], str],
        values: dict[str, Any],
        *,
        connection: asyncpg.Connection | None = None,
    ) -> Tag:
        try:
            if "name" in values and values["name"] != self.name:
                # The canonical tag_lookup row carries the name too; renaming only `tags` would leave the
                # old name resolving and surface it as a phantom alias.
                async with self.bot.db.acquire() as conn, conn.transaction():  # type: ignore[union-attr]
                    await self.bot.db.tags.rename_lookup(self.id, self.name, values["name"], connection=conn)
                    return await super()._update(key, values, connection=conn)
            return await super()._update(key, values, connection=connection)
        except asyncpg.UniqueViolationError:
            raise BadArgument("A Tag with this name already exists.", "name_or_id")
        except asyncpg.StringDataRightTruncationError:
            raise BadArgument("Tag Name length out of range, max. 100 characters.", "name_or_id")
        except asyncpg.CheckViolationError:
            raise BadArgument("Tag Content is missing.", "name_or_id")

    async def get_rank(self) -> int:
        return await self.bot.db.tags.get_tag_rank(self.id)

    async def delete(self) -> None:
        await self.bot.db.tags.delete_tag(self.id)

    async def transfer(self, to: discord.abc.Snowflake, only_parent: bool = False) -> None:
        async with self.bot.db.acquire() as conn, conn.transaction():  # type: ignore[union-attr]
            await self.update(owner_id=to.id, connection=conn)  # type: ignore[arg-type]
            if not only_parent:
                await self.bot.db.tags.transfer_aliases(self.id, to.id, connection=conn)  # type: ignore[arg-type]


class AliasTag(BaseRecord, table="tag_lookup", pk="id"):
    """Represents an Alias for a Tag."""

    parent: Tag | None
    id: int
    name: str
    parent_id: int
    owner_id: int
    location_id: int
    created_at: datetime.datetime

    __slots__ = ("created_at", "id", "location_id", "name", "owner_id", "parent", "parent_id")

    @property
    def choice_text(self) -> str:
        return f"[{self.id}] {self.name}"

    async def transfer(self, to: discord.abc.Snowflake, /, *, connection: asyncpg.Connection | None = None) -> None:
        db = self.parent.bot.db if self.parent else connection
        async with db.acquire() as conn, conn.transaction():  # type: ignore[union-attr]
            await db.tags.transfer_alias(self.id, to.id, connection=conn)  # type: ignore[union-attr]

    async def delete(self) -> None:
        assert self.parent is not None, "AliasTag.delete requires a parent tag with a bot reference"
        await self.parent.bot.db.tags.delete_alias(self.id)


# endregion
