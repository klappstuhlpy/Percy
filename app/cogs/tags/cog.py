from __future__ import annotations

import contextlib
import csv
import io
from typing import TYPE_CHECKING, Annotated, ClassVar, Literal

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from app.core import Bot, Cog, Context
from app.core.models import BadArgument, PermissionTemplate, cooldown, describe, group
from app.services import TagFinder
from app.utils import (
    TabularData,
    fuzzy,
    get_shortened_string,
    usage_per_day,
)
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Generator

    from discord.app_commands import Choice


from app.cogs.tags.models import (
    NO_MENTIONS,
    PENDING_OWNER,
    AliasTag,
    Tag,
    TagContent,
    TagListFlags,
    TagNameOrID,
    TagSearchFlags,
    content_payload,
)
from app.cogs.tags.ui import (
    TagEditModal,
    TagGuildStatsView,
    TagInfoView,
    TagListPaginator,
    TagMakeModal,
    TagMemberStatsView,
    TagSuggestView,
    TagTransferConfirmButton,
    TagTransferDeclineButton,
    TagTransferView,
)

# region Cog


class Tags(Cog):
    """Commands to fetch something by a tag name."""

    emoji = "<:tag:1322338570484322304>"

    #: Help-menu sections (qualified command names; a claim covers subcommands).
    help_sections: ClassVar[dict[str, tuple[str, ...]]] = {
        "Browse": ("tag list", "tag search", "tag find", "tag info", "tag raw", "tag stats"),
        "Manage": ("tag create", "tag make", "tag alias", "tag edit", "tag delete", "tag purge", "tag export"),
        "Ownership": ("tag transfer", "tag claim"),
    }

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        bot.add_dynamic_items(TagTransferConfirmButton, TagTransferDeclineButton)

        self._temporary_reserved_tags: dict[int, set[str]] = {}
        # Phase 6: free-text question → best-matching tag name (gated on AIFlags.tags).
        self._tag_finder: TagFinder = TagFinder(bot.ai)

    @contextlib.contextmanager
    def reserve_tag(self, guild_id: int, name: str, /) -> Generator[None]:
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

        # exact_match wants the row that owns *this* name, so an alias must come back as the alias, not its parent.
        if not parent and not exact_match:
            record = await repo.get_parent_record_via_alias(name_or_id, owner_id=owner_id, location_id=location_id)
            parent = Tag(bot=self.bot, record=record) if record else None

        if parent and parent.owner_id == PENDING_OWNER:
            # A transfer nobody answered lapses after 24h: hand the tag back on first access.
            restored = await repo.expire_transfer(parent.id)
            if restored is not None:
                parent.owner_id = restored

        if parent and exact_match:
            return parent

        if parent:
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

        assert isinstance(result, Tag)
        content = result.raw_content if escape_markdown else result.content
        await ctx.send(**content_payload(content), reference=ctx.replied_reference)
        await result.add(uses=1)

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
        first_word = name.partition(" ")[0].lower()

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
        original = await self.get_tag(original_tag, location_id=ctx.guild.id, only_parent=True)
        if original is None or not isinstance(original, Tag):
            raise BadArgument("The original tag could not be found.", "original_tag")
        perms = getattr(ctx.author, "guild_permissions", None)
        if original.owner_id != ctx.author.id and not (perms and perms.manage_guild):
            raise BadArgument("You can only alias tags you own (or need Manage Server).", "original_tag")
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

        async def get_user_input(prompt: str, timeout: float = 60.0) -> discord.Message | None:
            try:
                await ctx.send(prompt)
                user_input = await self.bot.wait_for(
                    "message", timeout=timeout, check=lambda msg: msg.author == ctx.author and ctx.channel == msg.channel
                )
            except TimeoutError:
                return None
            messages.append(user_input)
            return user_input

        name_message = await get_user_input("What would you like the tag's **name** to be?")
        if name_message is None:
            return
        name = name_message.content

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
            content_message = await get_user_input(content_prompt, timeout=100.0)
            content = content_message.content if content_message else None

            if content == f"{ctx.prefix}abort":
                return

            if content or (content_message and content_message.attachments):
                clean_content = await TagContent().convert(ctx, content or "")

                # The attachment rides on the reply, not on the original command message.
                if content_message and content_message.attachments:
                    clean_content = f"{clean_content}\n{content_message.attachments[0].url}".strip()

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
        await ctx.send(view=view, allowed_mentions=NO_MENTIONS)

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
        await ctx.send(view=view, allowed_mentions=NO_MENTIONS)

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
                if await modal.wait():  # timed out: nothing was submitted
                    return
                ctx.interaction = modal.interaction  # type: ignore
                content = modal.content.value
                name = modal.tag_name.value.strip()
                if self.is_tag_reserved(ctx.guild.id, name):
                    raise BadArgument("Hey, that's a reserved tag name. Choose another one.", "name_or_id")

        if content and len(content) > 2000:
            raise BadArgument("Tag Content is too long, max. 2000 characters.", "content")

        # Only the fields actually given: `content=None` would null the column (a CHECK violation).
        changes = {"name": name, "use_embed": use_embed, "content": content}
        await tag.update(**{k: v for k, v in changes.items() if v is not None})
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
        await ctx.send_success(f"Tag **{discord.utils.escape_markdown(raw_tag.name)}** [`{raw_tag.id}`] was deleted.")

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
        await ctx.send(view=view, allowed_mentions=NO_MENTIONS)

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
        await TagListPaginator.start(
            ctx,
            entries=rows,
            per_page=15,
            title="Tag List",
            description=discord.utils.escape_markdown(f"{member.display_name}'s tags in {guild_name}"),
            sort=flags.sort,
        )

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

        await TagListPaginator.start(
            ctx,
            entries=rows,
            per_page=15,
            title="Tag Search",
            description=f"Results for \"{discord.utils.escape_markdown(query[:100])}\"",
            sort=flags.sort,
        )

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

    @tag.group(
        "transfer",
        description="Transfer a tag owned by you to another member.",
        invoke_without_command=True,
        guild_only=True,
    )
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
        try:
            await member.send(view=view)
        except discord.Forbidden:
            await ctx.send_error(f"I can't DM **{member}**, so they can't accept the transfer.")
            return
        await self.bot.db.tags.start_transfer(tag.id, ctx.author.id)
        await ctx.send_info(f"Transfer request for tag **{tag.name}** has been sent to **{member}**.")

    @tag_transfer.command("cancel", description="Cancel a pending tag transfer you started.", guild_only=True)
    @describe(name_or_id="The tag whose transfer to cancel.")
    @app_commands.rename(name_or_id="name-or-id")
    async def tag_transfer_cancel(
        self, ctx: Context, *, name_or_id: Annotated[str, TagNameOrID(with_id=True)]
    ) -> None:
        """Cancel a pending transfer; the tag returns to you."""
        assert ctx.guild is not None
        tag = await self.get_tag(name_or_id, location_id=ctx.guild.id, only_parent=True)
        if not isinstance(tag, Tag) or tag.owner_id != PENDING_OWNER:
            raise BadArgument("That tag has no pending transfer.", "name_or_id")
        if await self.bot.db.tags.end_transfer(tag.id) != ctx.author.id:
            raise BadArgument("Only the member who started the transfer can cancel it.", "name_or_id")
        await tag.update(owner_id=ctx.author.id)
        await ctx.send_success(f"Transfer of tag **{tag.name}** cancelled.")

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
        if tag.owner_id == PENDING_OWNER:
            await ctx.send_error(f"Tag **{tag.name}** is waiting on a transfer and cannot be claimed.")
            return
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

