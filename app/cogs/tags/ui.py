from __future__ import annotations

import contextlib
import datetime
from typing import TYPE_CHECKING, Any, cast

import asyncpg
import discord

from app.core import ConfirmationView, Context, LayoutView
from app.core.models import AppBadArgument, BadArgument
from app.core.pagination import BasePaginatorV2
from app.utils import (
    get_asset_url,
    helpers,
    medal_emoji,
    pluralize,
)
from config import Emojis

if TYPE_CHECKING:
    import re



from app.cogs.tags.models import (
    ALIASES_SHOWN,
    NO_MENTIONS,
    PENDING_OWNER,
    AliasTag,
    Tag,
    TagNameOrID,
    TagPageEntry,
    content_payload,
)

if TYPE_CHECKING:
    from app.cogs.tags.cog import Tags


def _header(text: str, thumbnail: str | None) -> discord.ui.Item[Any]:
    """A card heading, with the thumbnail beside it only when there is one (an empty URL is a 400)."""
    if thumbnail:
        return discord.ui.Section(text, accessory=discord.ui.Thumbnail(thumbnail))
    return discord.ui.TextDisplay(text)



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
        accent: discord.Colour | None = helpers.Colour.brand(),
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

        container.add_item(_header(
            f"## {discord.utils.escape_markdown(tag.name)}\n-# Tag Information", get_asset_url(self.ctx.guild)
        ))

        container.add_item(discord.ui.Separator())

        fields: list[str] = []
        owner = "*Pending transfer*" if tag.owner_id == -1 else f"<@{tag.owner_id}>"
        fields.append(f"**Owner** — {owner}")
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
                f"• **{discord.utils.escape_markdown(alias.name)}** [`{alias.id}`] — "
                f"{discord.utils.format_dt(alias.created_at.replace(tzinfo=datetime.UTC), 'D')}"
                for alias in tag.aliases[:ALIASES_SHOWN]
            )
            if len(tag.aliases) > ALIASES_SHOWN:
                aliases_text += f"\n-# … and {len(tag.aliases) - ALIASES_SHOWN} more"
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
        await interaction.response.send_message(**content_payload(self.tag.content), ephemeral=True)

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

        confirm = ConfirmationView(
            interaction.user, delete_after=True, timeout=60.0,
            content="Are you sure you want to delete this tag? This cannot be undone.",
        )
        await interaction.response.send_message(view=confirm, ephemeral=True)
        confirm.message = await interaction.original_response()
        await confirm.wait()
        if not confirm.value:
            return

        await self.tag.delete()
        # Replace the info card itself so its Edit/Delete buttons can't act on a deleted tag.
        self.clear_items()
        container = discord.ui.Container(accent_colour=helpers.Colour.success_accent())
        container.add_item(discord.ui.TextDisplay(
            f"{Emojis.success} Tag **{discord.utils.escape_markdown(self.tag.name)}** [`{self.tag.id}`] has been deleted."
        ))
        self.add_item(container)
        if interaction.message is not None:
            with contextlib.suppress(discord.HTTPException):
                await interaction.message.edit(view=self, allowed_mentions=NO_MENTIONS)
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

        await interaction.response.send_message(**content_payload(tag.content))
        if interaction.message:
            with contextlib.suppress(discord.HTTPException):
                await interaction.message.delete()
        await tag.add(uses=1)
        self.stop()


class TagListPaginator(BasePaginatorV2[asyncpg.Record]):
    """Paginated CV2 card for tag list and search results (``title``/``description``/``sort`` ride in ``extras``)."""

    async def format_page(self, entries: list[asyncpg.Record], /) -> list[discord.ui.Item[Any]]:
        header = f"## {self.extras.get('title', 'Tags')}"
        if description := self.extras.get("description"):
            header += f"\n-# {description}"
        lines = "\n".join(
            f"`{i}.` {TagPageEntry(record=row)}" for i, row in enumerate(entries, self.numerate_start)
        )
        return [discord.ui.TextDisplay(header), discord.ui.Separator(), discord.ui.TextDisplay(lines or "*No tags.*")]

    def footer_items(self) -> list[discord.ui.Item[Any]]:
        return [discord.ui.TextDisplay(
            f"-# Page {self.current_page}/{self.total_pages} \N{BULLET} "
            f"{pluralize(len(self.entries)):entry|entries} \N{BULLET} Sorted by: {self.extras.get('sort', 'name')}"
        )]


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

        container.add_item(_header(
            f"## Tag Statistics\n-# {discord.utils.escape_markdown(guild.name)}", get_asset_url(guild)
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
                f"{medal_emoji(i)} **{discord.utils.escape_markdown(record['name'])}** — {record['uses']} uses"
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

        container.add_item(_header(
            f"## {discord.utils.escape_markdown(member.display_name)}\n-# Tag Statistics", member.display_avatar.url
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
                f"{medal_emoji(i)} **{discord.utils.escape_markdown(record['name'])}** — {record['uses']} uses"
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
        tag = await cog.get_tag(tag_id, owner_id=PENDING_OWNER, only_parent=True)
        if tag is None:
            if interaction.message is not None:
                await interaction.message.delete()
            raise AppBadArgument(f"{Emojis.error} Tag was not found")

        if not isinstance(tag, Tag) or tag.owner_id != PENDING_OWNER:
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
        # The request lives in DMs, so the user is a plain ``discord.User`` here — never assert Member.
        await interaction.client.db.tags.end_transfer(self.tag.id)  # type: ignore[attr-defined]
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
        tag = await cog.get_tag(tag_id, owner_id=PENDING_OWNER, only_parent=True)
        if tag is None:
            if interaction.message is not None:
                await interaction.message.delete()
            raise AppBadArgument(f"{Emojis.error} Tag was not found")

        if not isinstance(tag, Tag) or tag.owner_id != PENDING_OWNER:
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
        await interaction.client.db.tags.end_transfer(self.tag.id)  # type: ignore[attr-defined]
        await self.tag.update(owner_id=self.from_id)
        if interaction.message is not None:
            await interaction.message.delete()
        await interaction.response.send_message(f"{Emojis.success} Tag transfer was declined.", ephemeral=True)


# endregion
