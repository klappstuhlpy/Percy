from __future__ import annotations

import contextlib
import csv
import datetime
import io
from typing import TYPE_CHECKING, Annotated, Any, Literal, cast

import asyncpg
import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

from app.core import Bot, Cog, Context, Flags, LayoutView, flag, store_true, ConfirmationView
from app.core.models import AppBadArgument, BadArgument, PermissionTemplate, cooldown, describe, group
from app.database import BaseRecord
from app.services import TagFinder
from app.utils import (
    TabularData,
    fuzzy,
    get_asset_url,
    get_shortened_string,
    helpers,
    medal_emoji,
    pluralize,
    usage_per_day,
)
from config import Emojis

if TYPE_CHECKING:
    import re
    from collections.abc import Callable, Generator


# region Converters & Flags


class TagPageEntry(BaseRecord, table="tags", pk="id"):
    id: int
    name: str

    __slots__ = ("id", "name")

    def __str__(self) -> str:
        return f"{self.name} [`{self.id}`]"


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
            raise BadArgument("Please enter a valid tag name" + " or id." if self.with_id else ".")

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
            raise BadArgument("Tag content must be 2000 characters or less. (You have *{len(argument)}* characters)")

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

# region Components V2 Views


class TagLayoutView(LayoutView):
    """Base Components V2 view for all tag displays.

    Provides common structure: a single container with accent colour,
    optional header section, content, and action row. Subclasses override
    ``_build()`` to compose the container.
    """

    def __init__(
        self,
        *,
        accent: discord.Colour = helpers.Colour.brand(),
        timeout: float | None = 180.0,
        members: discord.abc.Snowflake | None = None,
    ) -> None:
        super().__init__(timeout=timeout, members=members)
        self._accent = accent

    def _make_container(self) -> discord.ui.Container:
        return discord.ui.Container(accent_colour=self._accent)

    def _build(self) -> None:
        """Rebuild the view layout. Called by subclasses after state changes."""
        self.clear_items()


class TagInfoView(TagLayoutView):
    """Displays detailed tag metadata in a CV2 card with manage buttons for the owner."""

    def __init__(self, tag: Tag, *, ctx: Context, rank: int | None = None) -> None:
        super().__init__(members=ctx.author)
        self.tag = tag
        self.ctx = ctx
        self._rank = rank
        self._is_owner = ctx.author.id == tag.owner_id
        self._build_layout()

    def _build_layout(self) -> None:
        self.clear_items()
        container = self._make_container()
        tag = self.tag

        container.add_item(discord.ui.Section(
            f"## {tag.name}\n-# Tag Information",
            accessory=discord.ui.Thumbnail(get_asset_url(self.ctx.guild) or "")
        ))

        container.add_item(discord.ui.Separator())

        fields: list[str] = []
        fields.append(f"**Owner** — <@{tag.owner_id}>")
        uses_line = f"**Uses** — {tag.uses}"
        if self._rank and self._rank in (1, 2, 3):
            uses_line += f" • **#{self._rank}** {medal_emoji(self._rank - 1)}"
        fields.append(uses_line)
        fields.append(f"**Created** — {discord.utils.format_dt(tag.created_at.replace(tzinfo=datetime.UTC), 'R')}")
        fields.append(f"**ID** — `{tag.id}`")

        container.add_item(discord.ui.TextDisplay("\n".join(fields)))

        if tag.aliases:
            container.add_item(discord.ui.Separator())
            aliases_text = "\n".join(
                f"• **{alias.name}** [`{alias.id}`] — {discord.utils.format_dt(alias.created_at.replace(tzinfo=datetime.UTC), 'D')}"
                for alias in tag.aliases
            )
            container.add_item(discord.ui.TextDisplay(
                f"### Aliases ({len(tag.aliases)})\n{aliases_text}"
            ))

        view_btn = discord.ui.Button(label="View Content", style=discord.ButtonStyle.secondary)
        view_btn.callback = self._view_content

        if self._is_owner:
            edit_btn = discord.ui.Button(label="Edit", style=discord.ButtonStyle.primary)
            edit_btn.callback = self._edit_tag
            delete_btn = discord.ui.Button(label="Delete", style=discord.ButtonStyle.red)
            delete_btn.callback = self._delete_tag
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(view_btn, edit_btn, delete_btn))
        else:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(view_btn))

        self.add_item(container)

    async def _view_content(self, interaction: discord.Interaction) -> None:
        content = self.tag.content
        if len(content) > 2000:
            content = content[:1997] + "…"
        await interaction.response.send_message(content, ephemeral=True)

    async def _edit_tag(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.tag.owner_id:
            await interaction.response.send_message(
                f"{Emojis.error} You are not the owner of this tag.", ephemeral=True
            )
            return

        modal = TagEditModal(self.tag)
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return
        content = modal.content.value
        name = modal.tag_name.value
        if content and len(content) <= 2000:
            await self.tag.update(name=name, content=content)
            self.tag.content = content
            self.tag.name = name
            self._build_layout()
            await modal.interaction.response.edit_message(view=self)

    async def _delete_tag(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.tag.owner_id:
            await interaction.response.send_message(
                f"{Emojis.error} You are not the owner of this tag.", ephemeral=True
            )
            return

        confirm = ConfirmationView(interaction.user, delete_after=True, timeout=60.0, content="Are you sure you want to delete this tag? This cannot be undone.")
        confirm.message = await interaction.response.send_message(view=confirm)
        await confirm.wait()
        if not confirm.value:
            return

        await self.tag.delete()
        self.clear_items()
        container = discord.ui.Container(accent_colour=helpers.Colour.success_accent())
        container.add_item(discord.ui.TextDisplay(
            f"{Emojis.success} Tag **{self.tag.name}** [`{self.tag.id}`] has been deleted."
        ))
        self.add_item(container)
        await interaction.followup.send(view=self, ephemeral=True)
        self.stop()


class TagSuggestView(TagLayoutView):
    """'Did you mean ...' disambiguation using a select menu."""

    def __init__(self, results: list[AliasTag], *, ctx: Context) -> None:
        super().__init__(accent=helpers.Colour.warning_accent(), members=ctx.author)
        self.results = results
        self.ctx = ctx
        self._build_layout()

    def _build_layout(self) -> None:
        self.clear_items()
        container = self._make_container()

        container.add_item(discord.ui.TextDisplay("## Did you mean …\n-# Your query didn't match any tags, but I found some similar ones:"))

        lines = []
        options = []
        for i, tag in enumerate(self.results[:25]):
            options.append(
                discord.SelectOption(
                    label=tag.name[:100],
                    value=str(i),
                    description=f"ID: {tag.id}",
                )
            )
            entry = TagPageEntry(record={"name": tag.name, "id": tag.id})
            lines.append(f"`{i + 1}.` {entry}")
        container.add_item(discord.ui.TextDisplay("\n".join(lines)))

        container.add_item(discord.ui.Separator())

        self._select = discord.ui.Select(placeholder="Select a tag…", options=options)
        self._select.callback = self._on_select
        container.add_item(discord.ui.ActionRow(self._select))

        container.add_item(discord.ui.TextDisplay(
            f"-# {pluralize(len(self.results)):similar tag|similar tags} found - showing {len(self.results[:25])}"
        ))
        self.add_item(container)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        index = int(self._select.values[0])
        selected = self.results[index]

        cog: Tags | None = cast("Tags | None", interaction.client.get_cog("Tags"))  # type: ignore[union-attr]
        if cog is None:
            return

        tag = await cog.get_tag(selected.name, location_id=selected.location_id)
        if not isinstance(tag, Tag):
            await interaction.response.send_message(
                f"{Emojis.error} Could not resolve that tag.", ephemeral=True
            )
            return

        content = tag.content
        if len(content) > 2000:
            content = content[:1997] + "…"
        await interaction.response.send_message(content)
        if interaction.message:
            with contextlib.suppress(discord.HTTPException):
                await interaction.message.delete()
        await tag.add(uses=1)
        self.stop()


class TagListView(TagLayoutView):
    """Paginated CV2 view for tag list and search results."""

    PER_PAGE = 15

    def __init__(
        self,
        entries: list[asyncpg.Record],
        *,
        ctx: Context,
        title: str = "Tags",
        description: str = "",
        sort: str = "name",
    ) -> None:
        super().__init__(members=ctx.author)
        self.entries = entries
        self.ctx = ctx
        self._title = title
        self._description = description
        self._sort = sort
        self._page = 0
        self._total_pages = max(1, (len(entries) + self.PER_PAGE - 1) // self.PER_PAGE)
        self._build_page()

    def _build_page(self) -> None:
        self.clear_items()
        container = self._make_container()

        header = f"## {self._title}"
        if self._description:
            header += f"\n-# {self._description}"
        container.add_item(discord.ui.TextDisplay(header))
        container.add_item(discord.ui.Separator())

        start = self._page * self.PER_PAGE
        end = start + self.PER_PAGE
        page_entries = self.entries[start:end]

        lines = []
        for i, row in enumerate(page_entries, start + 1):
            entry = TagPageEntry(record=row)
            lines.append(f"`{i}.` {entry}")
        container.add_item(discord.ui.TextDisplay("\n".join(lines)))

        container.add_item(discord.ui.Separator())

        footer = f"-# Page {self._page + 1}/{self._total_pages} • {pluralize(len(self.entries)):entry|entries} • Sorted by: {self._sort}"
        container.add_item(discord.ui.TextDisplay(footer))

        if self._total_pages > 1:
            prev_btn = discord.ui.Button(
                label="◀",
                style=discord.ButtonStyle.secondary,
                disabled=self._page == 0,
            )
            prev_btn.callback = self._prev

            page_btn = discord.ui.Button(
                label=f"{self._page + 1}/{self._total_pages}",
                style=discord.ButtonStyle.secondary,
                disabled=True,
            )

            next_btn = discord.ui.Button(
                label="▶",
                style=discord.ButtonStyle.secondary,
                disabled=self._page >= self._total_pages - 1,
            )
            next_btn.callback = self._next

            search_btn = discord.ui.Button(
                label="Jump",
                style=discord.ButtonStyle.primary,
            )
            search_btn.callback = self._jump

            container.add_item(discord.ui.ActionRow(prev_btn, page_btn, next_btn, search_btn))

        self.add_item(container)

    async def _prev(self, interaction: discord.Interaction) -> None:
        self._page = max(0, self._page - 1)
        self._build_page()
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction) -> None:
        self._page = min(self._total_pages - 1, self._page + 1)
        self._build_page()
        await interaction.response.edit_message(view=self)

    async def _jump(self, interaction: discord.Interaction) -> None:
        modal = _JumpToPageModal(self._total_pages)
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return
        self._page = modal.page
        self._build_page()
        await modal.interaction.response.edit_message(view=self)


class _JumpToPageModal(discord.ui.Modal, title="Jump to Page"):
    page_number = discord.ui.TextInput(label="Page Number", style=discord.TextStyle.short)

    def __init__(self, total: int) -> None:
        super().__init__(timeout=30)
        self._total = total
        self.page_number.placeholder = f"1 – {total}"
        self.page: int = 0

    async def on_submit(self, interaction: discord.Interaction) -> None:
        value = self.page_number.value
        if not value.isdigit() or not (1 <= int(value) <= self._total):
            await interaction.response.send_message(
                f"{Emojis.error} Enter a number between 1 and {self._total}.", ephemeral=True
            )
            self.stop()
            return
        self.page = int(value) - 1
        self.interaction = interaction
        self.stop()


class TagGuildStatsView(TagLayoutView):
    """Displays guild-wide tag statistics in a CV2 card."""

    def __init__(
        self,
        *,
        ctx: Context,
        total_tags: int,
        total_uses: int,
        uses_per_day: float,
        most_used: list[asyncpg.Record],
        top_users: list[asyncpg.Record],
        top_creators: list[asyncpg.Record],
    ) -> None:
        super().__init__(members=ctx.author)
        self.ctx = ctx
        container = self._make_container()

        guild = ctx.guild
        assert guild is not None

        container.add_item(discord.ui.Section(
            f"## Tag Statistics\n-# {guild.name}",
            accessory=discord.ui.Thumbnail(get_asset_url(guild) or "")
        ))

        container.add_item(discord.ui.Separator())

        stats_text = (
            f"**Total Tags** — {total_tags}\n"
            f"**Total Uses** — {total_uses}\n"
            f"**Uses/Day** — {uses_per_day:.2f}"
        )
        container.add_item(discord.ui.TextDisplay(stats_text))

        if most_used:
            container.add_item(discord.ui.Separator())
            most_used_text = "\n".join(
                f"{medal_emoji(i)} **{record['name']}** — {record['uses']} uses"
                for i, record in enumerate(most_used)
            )
            container.add_item(discord.ui.TextDisplay(f"### Most Used Tags\n{most_used_text}"))

        if top_users:
            container.add_item(discord.ui.Separator())
            top_users_text = "\n".join(
                f"{medal_emoji(i)} <@{record['author_id']}> — {record['uses']} times"
                for i, record in enumerate(top_users)
            )
            container.add_item(discord.ui.TextDisplay(f"### Top Tag Users\n{top_users_text}"))

        if top_creators:
            container.add_item(discord.ui.Separator())
            top_creators_text = "\n".join(
                f"{medal_emoji(i)} <@{record['owner_id']}> — {record['count']} tags"
                for i, record in enumerate(top_creators)
            )
            container.add_item(discord.ui.TextDisplay(f"### Top Creators\n{top_creators_text}"))

        self.add_item(container)


class TagMemberStatsView(TagLayoutView):
    """Displays per-member tag statistics in a CV2 card."""

    def __init__(
        self,
        *,
        ctx: Context,
        member: discord.Member | discord.User,
        command_uses: int,
        owned_count: int,
        total_uses: int,
        top_tags: list[asyncpg.Record],
    ) -> None:
        super().__init__(members=ctx.author)

        container = self._make_container()

        container.add_item(discord.ui.Section(
            f"## {member.display_name}\n-# Tag Statistics",
            accessory=discord.ui.Thumbnail(member.display_avatar.url)
        ))

        container.add_item(discord.ui.Separator())

        stats_text = (
            f"**Tag Commands Used** — {command_uses}\n"
            f"**Owned Tags** — {owned_count}\n"
            f"**Owned Tags Used** — {total_uses}"
        )
        container.add_item(discord.ui.TextDisplay(stats_text))

        if top_tags:
            container.add_item(discord.ui.Separator())
            top_text = "\n".join(
                f"{medal_emoji(i)} **{record['name']}** — {record['uses']} uses"
                for i, record in enumerate(top_tags)
            )
            container.add_item(discord.ui.TextDisplay(f"### Top Tags\n{top_text}"))

        self.add_item(container)


class TagTransferView(TagLayoutView):
    """CV2 transfer request card sent to the recipient's DMs."""

    def __init__(self, tag: Tag, *, from_user: discord.Member | discord.User, guild: discord.Guild) -> None:
        super().__init__(accent=helpers.Colour.info_accent(), timeout=None)
        self.tag = tag
        self.from_id = from_user.id

        container = self._make_container()

        container.add_item(discord.ui.TextDisplay(
            f"## Tag Transfer Request\n"
            f"**{from_user}** from **{guild.name}** wants to transfer the tag "
            f"**{tag.name}** [`{tag.id}`] to you.\n\n"
            f"Do you want to accept this transfer?"
        ))

        container.add_item(discord.ui.Separator())

        accept_btn = discord.ui.Button(
            label="Accept",
            style=discord.ButtonStyle.green,
            custom_id=f"tag:transfer:confirm:{tag.id}:{from_user.id}",
        )
        decline_btn = discord.ui.Button(
            label="Decline",
            style=discord.ButtonStyle.red,
            custom_id=f"tag:transfer:decline:{tag.id}:{from_user.id}",
        )
        container.add_item(discord.ui.ActionRow(accept_btn, decline_btn))

        self.add_item(container)


# endregion

# region Modals


class TagEditModal(discord.ui.Modal, title="Edit Tag"):
    tag_name = discord.ui.TextInput(label="Name", required=True, max_length=100, min_length=1)
    content = discord.ui.TextInput(
        label="Content", required=True, style=discord.TextStyle.long, min_length=1, max_length=2000
    )

    def __init__(self, tag: Tag) -> None:
        super().__init__()
        self.content.default = tag.content
        self.tag_name.default = tag.name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class TagMakeModal(discord.ui.Modal, title="Create a New Tag"):
    name = discord.ui.TextInput(label="Name", required=True, max_length=100, min_length=1)
    content = discord.ui.TextInput(
        label="Content", required=True, style=discord.TextStyle.long, min_length=1, max_length=2000
    )

    def __init__(self, cog: Tags, ctx: Context) -> None:
        super().__init__()
        self.cog: Tags = cog
        self.ctx: Context = ctx

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = str(self.name)
        try:
            name = await TagNameOrID().convert(self.ctx, name)
        except BadArgument as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        self.ctx.interaction = interaction  # type: ignore
        content = str(self.content)
        if len(content) > 2000:
            await interaction.response.send_message(
                f"{Emojis.error} Consider using a shorter description for your Tag. (2000 max characters)", ephemeral=True
            )
        else:
            if interaction.guild_id is None:
                await interaction.response.send_message(
                    f"{Emojis.error} This command can only be used in a server.", ephemeral=True
                )
                return
            assert isinstance(name, str)
            with self.cog.reserve_tag(interaction.guild_id, name):
                await self.cog.create_tag(self.ctx, name, content)


# endregion

# region Dynamic Items (persistent transfer buttons)


class TagTransferConfirmButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"tag:transfer:confirm:(?P<tag_id>[0-9]+):(?P<from_id>[0-9]+)"
):
    def __init__(self, tag: Tag, from_id: int) -> None:
        self.tag = tag
        self.from_id = from_id
        super().__init__(
            discord.ui.Button(
                label="Accept", style=discord.ButtonStyle.green, row=0, custom_id=f"tag:transfer:confirm:{tag.id}:{from_id}"
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, _, match: re.Match[str], /) -> TagTransferConfirmButton:
        cog: Tags | None = cast("Tags | None", interaction.client.get_cog("Tags"))  # type: ignore[union-attr]
        if cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
            )
            raise AppBadArgument(f"{Emojis.error} Tags cog is not loaded")

        tag_id = int(match["tag_id"])
        from_id = int(match["from_id"])
        tag = await cog.get_tag(tag_id, owner_id=from_id)
        if tag is None:
            if interaction.message is not None:
                await interaction.message.delete()
            raise AppBadArgument(f"{Emojis.error} Tag was not found")

        if not isinstance(tag, Tag) or tag.owner_id != -1:
            if interaction.message is not None:
                await interaction.message.delete()
            raise AppBadArgument(f"{Emojis.error} Tag is not pending for transfer.")

        return cls(tag, from_id)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if self.tag is None:
            await interaction.response.send_message(f"{Emojis.error} Tag was not found.", ephemeral=True)
            return False
        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        assert isinstance(interaction.user, discord.Member)
        await self.tag.transfer(interaction.user, only_parent=True)
        if interaction.message is not None:
            await interaction.message.delete()
        await interaction.response.send_message(
            f"{Emojis.success} Tag **{self.tag.name}** [`{self.tag.id}`] was successfully transferred to you.",
            ephemeral=True,
        )


class TagTransferDeclineButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r"tag:transfer:decline:(?P<tag_id>[0-9]+):(?P<from_id>[0-9]+)"
):
    def __init__(self, tag: Tag, from_id: int) -> None:
        self.tag = tag
        self.from_id = from_id
        super().__init__(
            discord.ui.Button(
                label="Decline", style=discord.ButtonStyle.red, row=0, custom_id=f"tag:transfer:decline:{tag.id}:{from_id}"
            )
        )

    @classmethod
    async def from_custom_id(cls, interaction: discord.Interaction, _, match: re.Match[str], /) -> TagTransferDeclineButton:
        cog: Tags | None = cast("Tags | None", interaction.client.get_cog("Tags"))  # type: ignore[union-attr]
        if cog is None:
            await interaction.response.send_message(
                f"{Emojis.error} Sorry, this button does not work at the moment. Try again later", ephemeral=True
            )
            raise AppBadArgument(f"{Emojis.error} Tags cog is not loaded")

        tag_id = int(match["tag_id"])
        from_id = int(match["from_id"])
        tag = await cog.get_tag(tag_id, owner_id=from_id)
        if tag is None:
            if interaction.message is not None:
                await interaction.message.delete()
            raise AppBadArgument(f"{Emojis.error} Tag was not found")

        if not isinstance(tag, Tag) or tag.owner_id != -1:
            if interaction.message is not None:
                await interaction.message.delete()
            raise AppBadArgument(f"{Emojis.error} Tag is not pending for transfer.")

        return cls(tag, from_id)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if self.tag is None:
            await interaction.response.send_message(f"{Emojis.error} Tag was not found.", ephemeral=True)
            return False
        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.tag.update(owner_id=self.from_id)
        if interaction.message is not None:
            await interaction.message.delete()
        await interaction.response.send_message(f"{Emojis.success} Tag transfer was declined.", ephemeral=True)


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

    async def transfer(self, to: discord.Member, only_parent: bool = False) -> None:
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

    async def transfer(self, to: discord.Member, /, *, connection: asyncpg.Connection | None = None) -> None:
        db = self.parent.bot.db if self.parent else connection
        async with db.acquire() as conn, conn.transaction():  # type: ignore[union-attr]
            await db.tags.transfer_alias(self.id, to.id, connection=conn)  # type: ignore[union-attr]

    async def delete(self) -> None:
        assert self.parent is not None, "AliasTag.delete requires a parent tag with a bot reference"
        await self.parent.bot.db.tags.delete_alias(self.id)


# endregion

# region Cog


class Tags(Cog):
    """Commands to fetch something by a tag name."""

    emoji = "<:tag:1322338570484322304>"

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        bot.add_dynamic_items(TagTransferConfirmButton, TagTransferDeclineButton)

        self._temporary_reserved_tags: dict[int, set[str]] = {}
        # Phase 6: free-text question → best-matching tag name (gated on AIFlags.tags).
        self._tag_finder: TagFinder = TagFinder(bot.ai)

    @contextlib.contextmanager
    def reserve_tag(self, guild_id: int, name: str, /) -> Generator[None, None, None]:
        """Reserves a tag name for a guild."""
        name = name.lower()

        if guild_id not in self._temporary_reserved_tags:
            self._temporary_reserved_tags[guild_id] = set()

        if name in self._temporary_reserved_tags[guild_id]:
            raise BadArgument("This name is currently reserved, try again later or use a different one.", "name_or_id")

        self._temporary_reserved_tags[guild_id].add(name)
        try:
            yield None
        finally:
            self._temporary_reserved_tags[guild_id].discard(name)

            if len(self._temporary_reserved_tags[guild_id]) == 0:
                del self._temporary_reserved_tags[guild_id]

    async def get_tag(
        self,
        name_or_id: str | int,
        *,
        owner_id: int | None = None,
        location_id: int | None = None,
        only_parent: bool = False,
        similarites: bool = False,
        exact_match: bool = False,
    ) -> list[AliasTag] | Tag | AliasTag | None:
        """|coro| @cached

        Gets the Original :class:`Tag` with Optional all :class:`AliasTag`s of it.
        If no exact_match match is found, it will return a list of :class:`AliasTag`s that are similar to the name.
        """
        repo = self.bot.db.tags

        record = await repo.get_tag_record(name_or_id, owner_id=owner_id, location_id=location_id)
        parent = Tag(bot=self.bot, record=record) if record else None

        if not parent:
            record = await repo.get_parent_record_via_alias(name_or_id)
            parent = Tag(bot=self.bot, record=record) if record else None

        if parent and not exact_match:
            if not only_parent:
                aliases = await repo.get_alias_records(parent.id, parent.name, owner_id=owner_id, location_id=location_id)
                parent.aliases = [AliasTag(parent=parent, record=alias) for alias in aliases]

            return parent

        if not parent and exact_match:
            alias = await repo.get_alias_record(name_or_id, owner_id=owner_id, location_id=location_id)
            return AliasTag(record=alias) if alias else None

        if similarites and isinstance(name_or_id, str):
            assert location_id is not None
            rows = await repo.get_similar_aliases(location_id, name_or_id)
            return [AliasTag(parent=parent, record=row) for row in rows]
        return None

    async def send_tag(self, ctx: Context, name_or_id: str | int, *, escape_markdown: bool = False) -> None:
        """|coro|

        Look up a Tag by name in the given guild. Searching with similarity queries.
        """
        assert ctx.guild is not None
        result = await self.get_tag(name_or_id, location_id=ctx.guild.id, similarites=True)

        if isinstance(result, list):
            if len(result) == 0:
                raise BadArgument(f"No Tag with the name or ID `{name_or_id}` found.", "name_or_id")
            else:
                view = TagSuggestView(result, ctx=ctx)
                await ctx.send(view=view, reference=ctx.replied_reference)
            return

        if not result:
            raise BadArgument(f"No Tag with the name or ID `{name_or_id}` found.", "name_or_id")

        tag: Tag = result  # type: ignore
        content = tag.raw_content if escape_markdown else tag.content
        await ctx.send(content, reference=ctx.replied_reference)

        _aliases = getattr(tag, "aliases", None)
        updated = await tag.add(uses=1)
        tag = updated
        if _aliases:
            tag.aliases = _aliases  # type: ignore

    @staticmethod
    async def create_tag(ctx: Context, name: str, content: str) -> None:
        """|coro|

        Creates a new Tag in the Guild.
        """
        async with ctx.db.acquire() as connection:
            tr = connection.transaction()
            await tr.start()

            try:
                assert ctx.guild is not None
                await ctx.db.tags.create_tag(name, content, ctx.author.id, ctx.guild.id, connection=connection)
            except AssertionError:
                await tr.rollback()
                raise BadArgument("This command can only be used in a server.", "name")
            except Exception as e:
                await tr.rollback()
                match e:
                    case asyncpg.UniqueViolationError():
                        raise BadArgument("A Tag with this name already exists.", "name")
                    case asyncpg.StringDataRightTruncationError():
                        raise BadArgument("Tag Name length out of range, max. 100 characters.", "name")
                    case asyncpg.CheckViolationError():
                        raise BadArgument("Tag Content is missing.", "name")
                    case _:
                        raise BadArgument("Tag could not be created due to an Unknown reason. Try again later?", "name")
            else:
                await tr.commit()
                await ctx.send_success(f"Tag `{name}` was successfully created.")

    def is_tag_reserved(self, guild_id: int, name: str) -> bool:
        """Helper method to check if a Tag with ``name`` is currently being made or reserved."""
        first_word, *_ = name.partition(" ")

        root: commands.GroupMixin = self.bot.get_command("tag")  # type: ignore
        if first_word in root.all_commands:
            return True
        else:
            try:
                being_made = self._temporary_reserved_tags[guild_id]
            except KeyError:
                return False
            else:
                return name.lower() in being_made

    async def non_aliased_tag_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Choice[str | int | float]]:
        assert interaction.guild_id is not None
        tags: list[Tag] = [
            Tag(bot=self.bot, record=record) for record in await self.bot.db.tags.get_guild_tags(interaction.guild_id)
        ]

        results = fuzzy.finder(current, tags, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, tag.choice_text), value=str(tag.id))
            for length, start, tag in results[:20]
        ]

    async def aliased_tag_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Choice[str | int | float]]:
        assert interaction.guild_id is not None
        tags: list[AliasTag] = [
            AliasTag(record=record) for record in await self.bot.db.tags.get_guild_aliases(interaction.guild_id)
        ]

        results = fuzzy.finder(current, tags, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, tag.choice_text), value=str(tag.id))
            for length, start, tag in results[:20]
        ]

    async def owned_non_aliased_tag_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Choice[str | int | float]]:
        assert interaction.guild_id is not None
        tags: list[Tag] = [
            Tag(bot=self.bot, record=record)
            for record in await self.bot.db.tags.get_owned_tags(interaction.guild_id, interaction.user.id)
        ]

        results = fuzzy.finder(current, tags, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, tag.choice_text), value=str(tag.id))
            for length, start, tag in results[:20]
        ]

    @group("tag", description="Shows a tag from the server.", fallback="show", guild_only=True, hybrid=True)
    @describe(name_or_id="The tag to retrieve")
    @app_commands.rename(name_or_id="name-or-id")
    @app_commands.autocomplete(name_or_id=aliased_tag_autocomplete)  # type: ignore
    async def tag(
        self,
        ctx: Context,
        *,
        name_or_id: Annotated[str | int, TagNameOrID(lower=True, with_id=True)],  # type: ignore
    ) -> None:
        """Retrieves a tag from the server.
        If the tag is an alias, the original tag will be retrieved instead.
        """
        await self.send_tag(ctx, name_or_id)

    @tag.command(
        "alias",
        description="Creates a new alias for an existing tag.",
        examples=["new-alias original-tag", "'new alias' original tag"],
        guild_only=True,
    )
    @describe(new_alias="The new alias to set", original_tag="The original tag to alias")
    @app_commands.rename(new_alias="new-alias", original_tag="original-tag")
    @app_commands.autocomplete(original_tag=non_aliased_tag_autocomplete)  # type: ignore
    async def tag_alias(
        self, ctx: Context, new_alias: Annotated[str, TagNameOrID], *, original_tag: Annotated[str, TagNameOrID]
    ) -> None:
        """Assign an alias to an existing tag of yours.
        `Note:` You have to be the owner of the Tag.
        One the Original Tag gets deleted, all the assigned aliases will be deleted too.
        Every alias can be only assigned to one Tag.
        If you want to edit an alias, you have to delete it and create a new one.
        """
        assert ctx.guild is not None
        try:
            status = await ctx.db.tags.create_alias(new_alias, original_tag, ctx.guild.id, ctx.author.id)
        except asyncpg.UniqueViolationError:
            raise BadArgument("This alias is already taken.", "new_alias")
        else:
            if status[-1] == "0":
                raise BadArgument("The original tag could not be found.", "original_tag")
            else:
                await ctx.send_success(
                    f"Tag alias **{new_alias}** that redirects to **{original_tag}** successfully created."
                )

    @tag.command(
        "create",
        description="Creates a new tag in the server.",
        aliases=["add"],
        examples=["new-tag This is the content of the tag.", "'new tag' This is the content of the tag."],
        guild_only=True,
    )
    @describe(name="The tag name", content="The tag content")
    async def tag_create(
        self, ctx: Context, name: Annotated[str, TagNameOrID], *, content: Annotated[str, TagContent]
    ) -> None:
        """Creates a new Tag owned by yourself in this server.
        The tag name must be between 1 and 100 characters long.
        The tag content must be less than 2000 characters long.
        `Note:` You can create aliases for Tags using `tags alias <alias-name> <original-name>`
        """
        assert ctx.guild is not None
        with self.reserve_tag(ctx.guild.id, name):
            await self.create_tag(ctx, name, content)

    @tag.command(
        "make",
        description="Interactively create a Tag owned by yourself in this server.",
        ignore_extra=True,
        guild_only=True,
    )
    async def tag_make(self, ctx: Context) -> None:
        """Interactively create a Tag owned by yourself in this server.

        Note: May be useful for larger contents / bigger names.
        """
        if ctx.interaction is not None:
            modal = TagMakeModal(self, ctx)
            await ctx.interaction.response.send_modal(modal)
            return

        messages = [ctx.message]

        converter = TagNameOrID()
        original = ctx.message

        async def get_user_input(prompt: str, timeout: float = 60.0) -> str | None:
            try:
                await ctx.send(prompt)
                user_input = await self.bot.wait_for(
                    "message", timeout=timeout, check=lambda msg: msg.author == ctx.author and ctx.channel == msg.channel
                )
                return user_input.content
            except TimeoutError:
                return None

        name = await get_user_input("What would you like the tag's **name** to be?")
        if name is None:
            return

        try:
            ctx.message = original
            name = await converter.convert(ctx, name)
        except BadArgument:
            raise
        finally:
            ctx.message = original

        assert ctx.guild is not None
        tag = await self.get_tag(name_or_id=name, location_id=ctx.guild.id, only_parent=True, exact_match=True)
        if tag is not None:
            raise BadArgument("A Tag with this name already exists.")

        assert isinstance(name, str)
        with self.reserve_tag(ctx.guild.id, name):
            content_prompt = (
                f"The new Tags name is **{name}**.\n"
                f"Please enter now a content for the tag.\n"
                f'You can type "`{ctx.prefix}abort`" to abort the tag make process.'
            )
            content = await get_user_input(content_prompt, timeout=100.0)

            if content == f"{ctx.prefix}abort":
                return

            if content:
                clean_content = await TagContent().convert(ctx, content)

                if ctx.message.attachments:
                    clean_content = f"{clean_content}\n{ctx.message.attachments[0].url}"

                await self.create_tag(ctx, name, clean_content)

        try:
            if hasattr(ctx.channel, "delete_messages"):
                await ctx.channel.delete_messages(messages)  # type: ignore[union-attr]
        except discord.HTTPException:
            pass

    async def guild_tag_stats(self, ctx: Context) -> None:
        assert ctx.guild is not None
        repo = self.bot.db.tags
        total_tags = await repo.count_tags(ctx.guild.id)

        if not total_tags:
            await ctx.send_error("There are no tag statistics available for this server.")
            return

        total_uses = await repo.count_tag_command_uses(ctx.guild.id)
        joined_at = ctx.me.joined_at if isinstance(ctx.me, discord.Member) else None
        upd = usage_per_day(joined_at, total_uses)  # type: ignore[arg-type]

        most_used_records = await repo.get_most_used_tags(ctx.guild.id)
        top_tag_users_records = await repo.get_top_tag_users(ctx.guild.id)
        top_creators_records = await repo.get_top_tag_creators(ctx.guild.id)

        view = TagGuildStatsView(
            ctx=ctx,
            total_tags=total_tags,
            total_uses=total_uses,
            uses_per_day=upd,
            most_used=list(most_used_records),
            top_users=list(top_tag_users_records),
            top_creators=list(top_creators_records),
        )
        await ctx.send(view=view)

    async def member_tag_stats(self, ctx: Context, member: discord.Member | discord.User) -> None:
        assert ctx.guild is not None
        repo = ctx.db.tags
        records = await repo.get_member_tag_summary(ctx.guild.id, member.id)

        if not records:
            await ctx.send_error("No Tag Statistics found for this member.")
            return

        count = await repo.count_member_tag_command_uses(ctx.guild.id, member.id)
        top_records = await repo.get_member_top_tags(ctx.guild.id, member.id)

        view = TagMemberStatsView(
            ctx=ctx,
            member=member,
            command_uses=count,
            owned_count=records["count"],
            total_uses=records["total_uses"],
            top_tags=list(top_records),
        )
        await ctx.send(view=view)

    @staticmethod
    async def send_tags_to_text(ctx: Context, tags: list[asyncpg.Record]) -> None:
        table = TabularData()
        table.set_columns(list(tags[0].keys()))
        table.add_rows(list(r.values()) for r in tags)
        fp = io.BytesIO(table.render().encode("utf-8"))
        await ctx.send(file=discord.File(fp, "tags.txt"))

    @tag.command("stats", description="Shows Tag Statistics about the Server or a Member.", guild_only=True)
    @describe(member="The member to get tag statistics for. If not given, the server's tag statistics will be shown.")
    async def tag_stats(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        """Shows Tag Statistics about the Server or a Member."""
        if member is None:
            await self.guild_tag_stats(ctx)
        else:
            await self.member_tag_stats(ctx, member)

    @tag.command("edit", description="Edit the content or name of a Tag.", guild_only=True)
    @describe(
        name_or_id="The Tag you want to edit. (Must be yours)",
        content="The new content of the tag. (If not given, you will be prompted to edit the tag in a modal.)",
    )
    @app_commands.rename(name_or_id="name-or-id")
    @app_commands.autocomplete(name_or_id=owned_non_aliased_tag_autocomplete)  # type: ignore
    async def tag_edit(
        self,
        ctx: Context,
        name_or_id: Annotated[str | int, TagNameOrID(lower=True, with_id=True)],
        use_embed: bool | None = None,
        *,
        content: Annotated[str | None, TagContent(required=False)] = None,
    ) -> None:
        """Edit the content or name of a Tag.
        `Note:` If you don't pass a content, you will be prompted to edit the tag in a modal.
        This may be useful for larger contents.

        You can only edit the name of the tag in within the modal.
        """
        assert ctx.guild is not None
        await ctx.defer()

        raw_tag = await self.get_tag(name_or_id, location_id=ctx.guild.id, owner_id=ctx.author.id, only_parent=True)

        if not raw_tag or not isinstance(raw_tag, Tag):
            raise BadArgument("Could not find a tag with that name, are you sure it exists or you own it?", "name_or_id")

        tag: Tag = raw_tag
        name = tag.name
        if content is None and use_embed is None:
            if ctx.interaction is None:
                raise BadArgument("You need to pass a content or use the modal to edit the tag.", "content")
            else:
                modal = TagEditModal(tag)
                await ctx.interaction.response.send_modal(modal)
                await modal.wait()
                ctx.interaction = modal.interaction  # type: ignore
                content = modal.content.value
                name = modal.tag_name.value

        if content and len(content) > 2000:
            raise BadArgument("Tag Content is too long, max. 2000 characters.", "content")

        await tag.update(name=name, use_embed=use_embed, content=content)
        await ctx.send_success("Successfully edited tag.")
        await self.send_tag(ctx, tag.id)

    @tag.command("delete", description="Removes a Tag by Name or ID.", aliases=["remove"], guild_only=True)
    @describe(name_or_id="The assigned Tag to delete.")
    @app_commands.rename(name_or_id="name-or-id")
    @app_commands.autocomplete(name_or_id=owned_non_aliased_tag_autocomplete)  # type: ignore
    async def tag_delete(
        self,
        ctx: Context,
        *,
        name_or_id: Annotated[str | int, TagNameOrID(lower=True, with_id=True)],
    ) -> None:
        """Removes a Tag by ID owned by yourself.
        Your Tags can also be removed by Moderators if they have the `MANAGE MESSAGES` permission.
        `Note:` This will also remove all aliases of the tag.
        """
        assert ctx.guild is not None
        form = {
            "location_id": ctx.guild.id,
            "only_parent": True,
        }
        can_manage = ctx.author.id == self.bot.owner_id or (
            isinstance(ctx.author, discord.Member) and ctx.author.guild_permissions.manage_messages
        )
        if not can_manage:
            form["owner_id"] = ctx.author.id

        raw_tag = await self.get_tag(name_or_id, **form)

        if not raw_tag or isinstance(raw_tag, list):
            raise BadArgument("Could not find a tag with that name, are you sure it exists or you own it?", "name_or_id")

        await raw_tag.delete()

    @tag.command("info", description="Shows you Information about a Tag.", guild_only=True)
    @describe(name_or_id="The name or id of the tag to get info about.")
    @app_commands.rename(name_or_id="name-or-id")
    @app_commands.autocomplete(name_or_id=aliased_tag_autocomplete)  # type: ignore
    async def tag_info(
        self,
        ctx: Context,
        *,
        name_or_id: Annotated[str | int, TagNameOrID(lower=True, with_id=True)],
    ) -> None:
        """Shows you Information about a Tag."""
        assert ctx.guild is not None
        raw_tag = await self.get_tag(name_or_id, location_id=ctx.guild.id)

        if raw_tag is None or isinstance(raw_tag, list) or not isinstance(raw_tag, Tag):
            raise BadArgument("Could not find a tag with that name, are you sure it exists or you own it?", "name_or_id")

        tag: Tag = raw_tag
        rank = await tag.get_rank()

        view = TagInfoView(tag, ctx=ctx, rank=rank)
        await ctx.send(view=view, allowed_mentions=None)

    @tag.command("raw", description="This displays you the raw content of a tag.", aliases=["content"], guild_only=True)
    @describe(name_or_id="The name or id of the tag to display the escaped markdown content.")
    @app_commands.rename(name_or_id="name-or-id")
    @app_commands.autocomplete(name_or_id=non_aliased_tag_autocomplete)  # type: ignore
    async def tag_raw(
        self,
        ctx: Context,
        *,
        name_or_id: Annotated[str | int, TagNameOrID(lower=True, with_id=True)],
    ) -> None:
        """This displays you the raw content of a tag."""
        await self.send_tag(ctx, name_or_id, escape_markdown=True)

    @staticmethod
    async def filter_tags(ctx: Context, flags: TagListFlags | TagSearchFlags, query: str | None = None) -> list[asyncpg.Record]:
        assert ctx.guild is not None
        member: discord.Member | None = None
        if query is None:
            raw_member = flags.member or ctx.author
            member = raw_member if isinstance(raw_member, discord.Member) else None

        return await ctx.db.tags.filter_tags(
            ctx.guild.id, query=query, owner_id=member.id if member else None, sort=flags.sort
        )

    @tag.command("list", description="Shows a list of Tags owned by yourself or a given member.", guild_only=True)
    @describe(member="The member to list tags of, if not given then it defaults to you.")
    async def tag_list(self, ctx: Context, *, flags: TagListFlags) -> None:
        """Shows a list of Tags owned by yourself or a given member."""
        member = flags.member or ctx.author
        rows = await self.filter_tags(ctx, flags)
        if not rows:
            await ctx.send_error(f"No tags found for **{member}**.")
            return

        if flags.to_text:
            await self.send_tags_to_text(ctx, rows)
            return

        guild_name = ctx.guild.name if ctx.guild is not None else "this server"
        view = TagListView(
            rows,
            ctx=ctx,
            title="Tag List",
            description=f"{member}'s tags in {guild_name}",
            sort=flags.sort,
        )
        msg = await ctx.send(view=view)
        view.message = msg

    @tag.command("search", description="Search for tags matching the given query.", guild_only=True)
    @describe(query="The tag name to search for")
    @app_commands.choices(
        sort=[
            app_commands.Choice(name="Name", value="name"),
            app_commands.Choice(name="Newest", value="newest"),
            app_commands.Choice(name="Oldest", value="oldest"),
            app_commands.Choice(name="ID", value="id"),
        ]
    )
    async def tags_search(self, ctx: Context, *, query: str, flags: TagSearchFlags) -> None:
        """Search for tags matching the given query.
        `Note:` To use autocomplete, you have to at least provide three characters.
        """
        rows = await self.filter_tags(ctx, flags, query)
        if not rows:
            await ctx.send_error("No tags found.")
            return

        if flags.to_text:
            await self.send_tags_to_text(ctx, rows)
            return

        view = TagListView(
            rows,
            ctx=ctx,
            title="Tag Search",
            description=f"Results for \"{query}\"",
            sort=flags.sort,
        )
        msg = await ctx.send(view=view)
        view.message = msg

    @tag.command("find", description="Find the most relevant tag for a question (AI).", guild_only=True)
    @describe(query="Describe what you're looking for in plain language.")
    async def tag_find(self, ctx: Context, *, query: str) -> None:
        """Find the tag that best answers a plain-language question — even if you don't know its name.

        Unlike `tag search` (which matches the name), this matches the *intent* of your question
        to a tag. Falls back to `tag search` when AI is unavailable.
        """
        assert ctx.guild is not None
        if not self.bot.ai.available:
            await ctx.send_error(f"The AI assistant is unavailable — try `{ctx.clean_prefix}tag search {query}`.")
            return
        ai_config = await self.bot.db.get_guild_ai_config(ctx.guild.id)
        if not ai_config.is_enabled("tags", ctx.channel.id):
            await ctx.send_error(
                f"AI tag search isn't enabled in this channel. A moderator can enable it on the "
                f"dashboard, or use `{ctx.clean_prefix}tag search`."
            )
            return

        records = await self.bot.db.tags.get_guild_tags(ctx.guild.id)
        if not records:
            await ctx.send_error("This server has no tags yet.")
            return

        async with ctx.typing():
            names = [record["name"] for record in reversed(records)]  # most-used first
            match = await self._tag_finder.find(query, names)

        if match is None:
            await ctx.send_error(f"I couldn't find a tag matching that. Try `{ctx.clean_prefix}tag search {query}`.")
            return

        await self.send_tag(ctx, match)

    @tag.command(
        "purge",
        description="Bulk remove all Tags and assigned Aliases of a given User.",
        guild_only=True,
        user_permissions=PermissionTemplate.mod,
    )
    @describe(member="The member to remove all tags of")
    async def tag_purge(self, ctx: Context, member: discord.User) -> None:
        """Bulk remove all Tags and assigned Aliases of a given User."""
        assert ctx.guild is not None
        count = await ctx.db.tags.count_owned_tags(ctx.guild.id, member.id)

        if count == 0:
            await ctx.send_error(f"No tags found for **{member}**.")
            return

        confirm = await ctx.confirm(
            f"{Emojis.warning} This will delete **{count}** tags are you sure? **This action cannot be reversed**."
        )
        if not confirm:
            return

        await ctx.db.tags.delete_owned_tags(ctx.guild.id, member.id)

        await ctx.send_success(f"Successfully removed all **{count}** tags that belong to **{member}**.")

    @tag.command("transfer", description="Transfer a tag owned by you to another member.", guild_only=True)
    @describe(member="The member to transfer the tag to.", name_or_id="The tag to transfer.")
    @app_commands.rename(name_or_id="name-or-id")
    @app_commands.autocomplete(name_or_id=aliased_tag_autocomplete)  # type: ignore
    async def tag_transfer(
        self,
        ctx: Context,
        member: discord.Member,
        *,
        name_or_id: Annotated[str, TagNameOrID(with_id=True)],
    ) -> None:
        """Transfer a tag owned by you to another member."""
        assert ctx.guild is not None
        if member.bot:
            await ctx.send_error("You cannot transfer tags to bots.")
            return

        raw_tag = await self.get_tag(name_or_id, location_id=ctx.guild.id, owner_id=ctx.author.id, only_parent=True)

        if raw_tag is None or not isinstance(raw_tag, Tag):
            raise BadArgument("Could not find a tag with that name, are you sure it exists or you own it?", "name_or_id")

        tag: Tag = raw_tag
        view = TagTransferView(tag, from_user=ctx.author, guild=ctx.guild)
        await member.send(view=view)
        await ctx.send_info(f"Transfer request for tag **{tag.name}** has been sent to **{member}**.")
        await tag.update(owner_id=-1)

    @tag.command(
        "claim",
        description="Claim a tag by yourself if the User is not in this server anymore or the tag has no owner.",
        guild_only=True,
    )
    @describe(name_or_id="The tag to claim")
    @app_commands.rename(name_or_id="name-or-id")
    @app_commands.autocomplete(name_or_id=aliased_tag_autocomplete)  # type: ignore
    async def tag_claim(
        self,
        ctx: Context,
        *,
        name_or_id: Annotated[str | int, TagNameOrID(lower=True, with_id=True)],
    ) -> None:
        """Claim a tag by yourself if the User is not in this server anymore or the tag has no owner."""
        assert ctx.guild is not None
        raw_tag = await self.get_tag(name_or_id, location_id=ctx.guild.id, exact_match=True)

        if raw_tag is None or isinstance(raw_tag, list):
            raise BadArgument("Could not find a tag with that name.", "name_or_id")

        tag: Tag | AliasTag = raw_tag
        guild = ctx.guild
        assert guild is not None
        member = await self.bot.get_or_fetch_member(guild, tag.owner_id)
        if member is not None:
            await ctx.send_error(f"Tag **{tag.name}** is already owned by **{member}**.")
            return

        assert isinstance(ctx.author, discord.Member)
        if isinstance(tag, AliasTag):
            await tag.transfer(ctx.author, connection=self.bot.db)  # type: ignore
        else:
            await tag.transfer(ctx.author, only_parent=True)  # type: ignore

        await ctx.send_success("Successfully transferred tag ownership to you.")

    @tag.command("export", description="Exports all your tags/server tags to a csv file.", guild_only=True)
    @cooldown(1, 30, commands.BucketType.member)
    @describe(which="Whether to export server tags or personal tags. (Server tags only for server owners)")
    async def tag_export(
            self,
            ctx: Context,
            which: Literal['server', 'personal'] = 'personal',
    ) -> None:
        """Exports all your tags/server tags to a csv file."""
        assert ctx.guild is not None
        owner_id: int | None = None
        if which == 'server':
            if ctx.author.id != ctx.guild.owner_id:
                raise BadArgument('You need to be the server owner to export all server tags.')
        else:
            owner_id = ctx.author.id

        async with ctx.channel.typing():
            records = await ctx.db.tags.export_tags(ctx.guild.id, owner_id=owner_id)

        if not records:
            await ctx.send_error('No tags found to export.')
            return

        buffer = io.BytesIO()
        writer = csv.writer(buffer, delimiter=',', quotechar="'", quoting=csv.QUOTE_MINIMAL)  # type: ignore
        for record in records:
            writer.writerow([record[0], record[1]])
        buffer.seek(0)

        file = discord.File(
            fp=buffer, filename=f'{ctx.author.id}_tags.csv' if which == 'personal' else f'{ctx.guild.id}_tags.csv'
        )
        await ctx.send(file=file)


# endregion


async def setup(bot: Bot) -> None:
    await bot.add_cog(Tags(bot))
