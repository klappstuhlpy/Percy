from __future__ import annotations

import contextlib
import csv
import datetime
import io
import re
from typing import TYPE_CHECKING, Annotated, Any, Literal

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from app.core import Bot, Cog, Context, Flags, flag, store_true, View
from app.core.models import BadArgument, PermissionTemplate, cooldown, describe, group, AppBadArgument
from app.database import BaseRecord
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
from app.utils.pagination import LinePaginator
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Callable


class TagPageEntry(BaseRecord):
    id: int
    name: str

    __slots__ = ('id', 'name')

    def __str__(self) -> str:
        return f'{self.name} [`{self.id}`]'


class TagNameOrID(commands.clean_content):
    """Converts the content to either an integer or string."""

    def __init__(self, *, lower: bool = False, with_id: bool = False):
        self.lower: bool = lower
        self.with_id: bool = with_id
        super().__init__()

    async def convert(self, ctx: Context, argument: str) -> str | int:
        converted = await super().convert(ctx, argument)
        lower = converted.lower().strip()

        if not lower:
            raise BadArgument('Please enter a valid tag name' + ' or id.' if self.with_id else '.')

        if len(lower) > 100:
            raise BadArgument(
                f'Tag names must be 100 characters or less. (You have *{len(lower)}* characters)')

        cog: Tags | None = ctx.bot.get_cog('Tags')
        if cog is None:
            raise BadArgument('Tags are currently unavailable.')

        if cog.is_tag_reserved(ctx.guild.id, argument):
            raise BadArgument('Hey, that\'s a reserved tag name. Choose another one.')

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
            raise BadArgument(
                'Tag content must be 2000 characters or less. (You have *{len(argument)}* characters)')

        return converted


class TagSearchFlags(Flags):
    query: str | None = flag(description='The query to search for', aliases=['q'])
    sort: Literal['name', 'newest', 'oldest', 'id'] = flag(
        description='The key to sort the results.', aliases=['s'], default='name')
    to_text: bool = store_true(description='Whether to output the results as raw tabular text.', aliases=['tt'])


class TagListFlags(Flags):
    member: discord.Member | None = flag(
        description='The member to search for', aliases=['m'])
    query: str | None = flag(description='The query to search for', aliases=['q'])
    sort: Literal['name', 'newest', 'oldest', 'id'] = flag(
        description='The key to sort the results.', aliases=['s'], default='name')
    to_text: bool = store_true(description='Whether to output the results as raw tabular text.', aliases=['tt'])


class TagTransferConfirmButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'tag:transfer:confirm:(?P<tag_id>[0-9]+):(?P<from_id>[0-9]+)'
):
    def __init__(self, tag: Tag, from_id: int) -> None:
        self.tag = tag
        self.from_id = from_id
        super().__init__(
            discord.ui.Button(
                label='Accept',
                style=discord.ButtonStyle.green,
                row=0,
                custom_id=f'tag:transfer:confirm:{tag.id}:{from_id}'
            )
        )

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Bot], _, match: re.Match[str], /
    ) -> TagTransferConfirmButton:
        cog: Tags | None = interaction.client.get_cog('Tags')
        if cog is None:
            await interaction.response.send_message(
                f'{Emojis.error} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AppBadArgument(f'{Emojis.error} Tags cog is not loaded')

        tag_id = int(match['tag_id'])
        from_id = int(match['from_id'])
        tag = await cog.get_tag(tag_id, owner_id=from_id)
        if tag is None:
            await interaction.message.delete()
            raise AppBadArgument(f'{Emojis.error} Tag was not found')

        if tag.owner_id != -1:
            await interaction.message.delete()
            raise AppBadArgument(f'{Emojis.error} Tag is not pending for transfer.')

        return cls(tag, from_id)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if self.tag is None:
            await interaction.response.send_message(f'{Emojis.error} Tag was not found.', ephemeral=True)
            return False
        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.tag.transfer(interaction.user, only_parent=True)
        await interaction.message.delete()
        await interaction.response.send_message(
            f'{Emojis.success} Tag **{self.tag.name}** [`{self.tag.id}`] was successfully transferred to you.',
            ephemeral=True)


class TagTransferDeclineButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'tag:transfer:decline:(?P<tag_id>[0-9]+):(?P<from_id>[0-9]+)'
):
    def __init__(self, tag: Tag, from_id: int) -> None:
        self.tag = tag
        self.from_id = from_id
        super().__init__(
            discord.ui.Button(
                label='Decline',
                style=discord.ButtonStyle.red,
                row=0,
                custom_id=f'tag:transfer:decline:{tag.id}:{from_id}'
            )
        )

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Bot], _, match: re.Match[str], /
    ) -> TagTransferConfirmButton:
        cog: Tags | None = interaction.client.get_cog('Tags')
        if cog is None:
            await interaction.response.send_message(
                f'{Emojis.error} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AppBadArgument(f'{Emojis.error} Tags cog is not loaded')

        tag_id = int(match['tag_id'])
        from_id = int(match['from_id'])
        tag = await cog.get_tag(tag_id, owner_id=from_id)
        if tag is None:
            await interaction.message.delete()
            raise AppBadArgument(f'{Emojis.error} Tag was not found')

        if tag.owner_id != -1:
            await interaction.message.delete()
            raise AppBadArgument(f'{Emojis.error} Tag is not pending for transfer.')

        return cls(tag, from_id)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if self.tag is None:
            await interaction.response.send_message(f'{Emojis.error} Tag was not found.', ephemeral=True)
            return False
        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        await self.tag.update(owner_id=self.from_id)
        await interaction.message.delete()
        await interaction.response.send_message(f'{Emojis.success} Tag transfer was declined.', ephemeral=True)


class TagEditModal(discord.ui.Modal, title='Edit Tag'):
    tag_name = discord.ui.TextInput(label='Name', required=True, max_length=100, min_length=1)
    content = discord.ui.TextInput(
        label='Content', required=True, style=discord.TextStyle.long, min_length=1, max_length=2000)

    def __init__(self, tag: Tag) -> None:
        super().__init__()
        self.content.default = tag.content
        self.tag_name.default = tag.name

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class TagMakeModal(discord.ui.Modal, title='Create a New Tag'):
    name = discord.ui.TextInput(label='Name', required=True, max_length=100, min_length=1)
    content = discord.ui.TextInput(
        label='Content', required=True, style=discord.TextStyle.long, min_length=1, max_length=2000
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

        self.ctx.interaction = interaction
        content = str(self.content)
        if len(content) > 2000:
            await interaction.response.send_message(
                f'{Emojis.error} Consider using a shorter description for your Tag. (2000 max characters)',
                ephemeral=True)
        else:
            with self.cog.reserve_tag(interaction.guild_id, name):
                await self.cog.create_tag(self.ctx, name, content)


class Tag(BaseRecord):
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

    __slots__ = ('bot', 'aliases', 'id', 'name', 'content', 'owner_id',
                 'uses', 'location_id', 'created_at', 'use_embed')

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.aliases: list[AliasTag] = []

    @property
    def choice_text(self) -> str:
        return f'[{self.id}] {self.name}'

    @property
    def raw_content(self) -> str:
        return discord.utils.escape_markdown(self.content)

    @property
    def to_embed(self) -> discord.Embed:
        embed = discord.Embed(title=self.name, description=self.content)
        embed.timestamp = self.created_at.replace(tzinfo=datetime.UTC)
        embed.set_footer(text=f'[{self.id}] • Created at')
        return embed

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> Tag:
        """|coro|

        Updates the Tag in the database.

        Parameters
        ----------
        key: Callable[[tuple[int, str]], str]
            The key to update.
        values: dict[str, Any]
            The values to update.
        connection: asyncpg.Connection | None
            The connection to use. Defaults to the bot's db.

        Returns
        -------
        Tag
            The updated Tag.
        """
        query = f"""
            UPDATE tags
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """

        try:
            record = await (connection or self.bot.db).fetchrow(query, self.id, *values.values())
        except Exception as e:
            match e:
                case asyncpg.UniqueViolationError():
                    raise BadArgument('A Tag with this name already exists.', 'name_or_id')
                case asyncpg.StringDataRightTruncationError():
                    raise BadArgument('Tag Name length out of range, max. 100 characters.', 'name_or_id')
                case asyncpg.CheckViolationError():
                    raise BadArgument('Tag Content is missing.', 'name_or_id')
                case _:
                    raise e
        else:
            return self.__class__(bot=self.bot, record=record)

    async def get_rank(self) -> int:
        """|coro|

        Gets the rank of the tag.

        Returns
        -------
        int
            The rank of the tag.
        """
        query = """
            SELECT (SELECT COUNT(*)
                    FROM tags second
                    WHERE (second.uses, second.id) >= (first.uses, first.id)
                      AND second.location_id = first.location_id) AS rank
            FROM tags first
            WHERE first.id = $1
        """
        return await self.bot.db.fetchval(query, self.id)

    async def delete(self) -> None:
        """|coro|

        Deletes the tag and all corresponding aliases.
        """
        query = "DELETE FROM tags WHERE id=$1;"
        await self.bot.db.execute(query, self.id)

        query = "DELETE FROM tag_lookup WHERE parent_id=$1;"
        await self.bot.db.execute(query, self.id)

    async def transfer(self, to: discord.Member, only_parent: bool = False) -> None:
        """|coro|

        Transfers the tag to another user.

        Parameters
        ----------
        to: discord.Member
            The member to transfer the tag to.
        only_parent: bool
            Whether to only transfer the parent tag or all aliases as well.
        """
        async with self.bot.db.acquire() as conn, conn.transaction():
            await self.update(owner_id=to.id, connection=conn)
            if not only_parent:
                query = "UPDATE tag_lookup SET owner_id=$1 WHERE parent_id=$2;"
                await conn.execute(query, to.id, self.id)


class AliasTag(BaseRecord):
    """Represents an Alias for a Tag."""

    parent: Tag | None
    id: int
    name: str
    parent_id: int
    owner_id: int
    location_id: int
    created_at: datetime.datetime

    __slots__ = ('parent', 'id', 'name', 'parent_id', 'owner_id', 'location_id', 'created_at')

    @property
    def choice_text(self) -> str:
        return f'[{self.id}] {self.name}'

    async def transfer(self, to: discord.Member, /, *, connection: asyncpg.Connection | None = None) -> None:
        """|coro|

        Transfers the alias to another user.

        Parameters
        ----------
        to: discord.Member
            The member to transfer the alias to.
        connection: asyncpg.Connection | None
            The connection to use. Defaults to the bot's db.
            Needs to be used if there is no :attr:`parent` attribute.

        """
        con = self.parent.bot.db if self.parent else connection
        async with con.acquire() as conn, conn.transaction():
            query = "UPDATE tag_lookup SET owner_id=$1 WHERE id=$2;"
            await conn.execute(query, to.id, self.id)

    async def delete(self) -> None:
        """|coro|

        Deletes the alias.
        """
        query = "DELETE FROM tag_lookup WHERE id=$1;"
        await self.parent.bot.db.execute(query, self.id)


class Tags(Cog):
    """Commands to fetch something by a tag name."""

    emoji = '<:tag:1322338570484322304>'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        bot.add_dynamic_items(TagTransferConfirmButton, TagTransferDeclineButton)

        # We create this temporary cache to avoid Users creating two Tags with the
        # same name at the same time to avoid conflicts
        self._temporary_reserved_tags: dict[int, set[str]] = {}

    @contextlib.contextmanager
    def reserve_tag(self, guild_id: int, name: str, /) -> None:
        """Reserves a tag name for a guild.

        This is to avoid two users creating a tag with the same name at the same time.
        """
        name = name.lower()

        if guild_id not in self._temporary_reserved_tags:
            self._temporary_reserved_tags[guild_id] = set()

        if name in self._temporary_reserved_tags[guild_id]:
            raise BadArgument('This name is currently reserved, try again later or use a different one.', 'name_or_id')

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

        Note
        ----
        Returning a list with similar Tags is only possible if :attr:`name_or_id` is a string and :attr:`similarites` is True.

        Parameters
        ----------
        name_or_id: str | int
            The name or ID of the tag to get.
        owner_id: int | None
            The ID of the user to get the tag from.
        location_id: int | None
            The ID of the guild to get the tag from.
        only_parent: bool
            Whether to only get the parent tag.
        similarites: bool
            Whether to return similar tags if no tag(s) were found.
        exact_match: bool
            Checks if no parent was found with the provided name/id,
            if there is an AliasTag with the provided name/id.

        Returns
        -------
        list[AliasTag] | Tag | AliasTag | None
            The Tag or a list of AliasTags or None if no Tag was found.
        """
        form: dict[str, Any] = {}
        parent_form: dict[str, Any] = {}
        is_id: bool = isinstance(name_or_id, int) or name_or_id.isdigit()

        if is_id:
            parent_form['tags.id'] = name_or_id
        else:
            parent_form['LOWER(tags.name)'] = name_or_id.lower()

        if location_id:
            form['location_id'] = location_id
        if owner_id:
            form['owner_id'] = owner_id

        query = f"SELECT * FROM tags WHERE {' AND '.join(f'{k}=${i}' for i, k in enumerate(form | parent_form, 1))} LIMIT 1;"
        record = await self.bot.db.fetchrow(query, *(form | parent_form).values())
        parent = Tag(bot=self.bot, record=record) if record else None

        if not parent:
            query = f"""
                SELECT tags.*
                FROM tags
                         INNER JOIN tag_lookup t on t.parent_id = tags.id
                WHERE {' AND '.join(f'{k}=${i}' for i, k in enumerate(parent_form, 1))}
                LIMIT 1;
            """
            record = await self.bot.db.fetchrow(query, *parent_form.values())
            parent = Tag(bot=self.bot, record=record) if record else None

        if parent and not exact_match:
            if not only_parent:
                form['parent_id'] = parent.id

                query = f"""
                    SELECT * FROM tag_lookup
                    WHERE name != '{parent['name']}'
                    AND {' AND '.join(f'{k}=${i}' for i, k in enumerate(form, 1))}
                """
                aliases = await self.bot.db.fetch(query, *form.values())
                parent.aliases = [AliasTag(parent=parent, record=alias) for alias in aliases]

            return parent

        if not parent and exact_match:
            query = f"SELECT * FROM tag_lookup WHERE {' AND '.join(f'{k}=${i}' for i, k in enumerate(form, 1))} LIMIT 1;"
            alias = await self.bot.db.fetchrow(query, name_or_id)
            return AliasTag(record=alias) if alias else None

        if similarites and isinstance(name_or_id, str):
            query = """
                SELECT tag_lookup.*
                FROM tag_lookup
                         INNER JOIN tags t on t.id = tag_lookup.parent_id
                WHERE tag_lookup.location_id = $1
                  AND tag_lookup.name % $2
                ORDER BY similarity(tag_lookup.name, $2) DESC
                LIMIT 25;
            """
            rows = await self.bot.db.fetch(query, location_id, name_or_id)
            return [AliasTag(parent=parent, record=row) for row in rows]

    async def send_tag(
            self,
            ctx: Context,
            name_or_id: str | int,
            *,
            escape_markdown: bool = False
    ) -> None:
        """|coro|

        Look up a Tag by name in the given guild. Searching with similarity queries.

        If a Tag is found, sends it with the proper formatting to the destination.
        If no Tag with the exact_match (LOWERED) name is found, a disambiguation prompt is sent.

        Parameters
        ----------
        ctx: Context
            The invocation context.
        name_or_id: str | int
            The name or ID of the Tag to get.
        escape_markdown: bool
            Whether to escape the markdown in the Tag content.
        """
        tag: list[AliasTag] | Tag = await self.get_tag(name_or_id, location_id=ctx.guild.id, similarites=True)

        if isinstance(tag, list):
            # Assuming no tags were found and similarites are returned instead
            if len(tag) == 0:
                raise BadArgument(f'No Tag with the name or ID `{name_or_id}` found.', 'name_or_id')
            else:
                embed = discord.Embed(title='*Did you mean ...*', colour=helpers.Colour.white())
                await LinePaginator.start(
                    ctx, entries=[f'* **{r.name}** [`{r.id}`]' for r in tag], embed=embed, per_page=20)
            return

        if not tag:
            raise BadArgument(f'No Tag with the name or ID `{name_or_id}` found.', 'name_or_id')

        if tag.use_embed and not escape_markdown:
            await ctx.send(embed=tag.to_embed, reference=ctx.replied_reference)
        else:
            await ctx.send(tag.content if not escape_markdown else tag.raw_content, reference=ctx.replied_reference)

        _aliases = getattr(tag, 'aliases', None)
        tag = await tag.add(uses=1)
        if _aliases:
            tag.aliases = _aliases

    @staticmethod
    async def create_tag(ctx: Context, name: str, content: str) -> None:
        """|coro|

        Creates a new Tag in the Guild.
        Inserts into `tag_lookup` and `tags` table, `tag_lookup` is the summary of origin tags and aliases.
        In the `tags` table are the root tags with their original names, contents etc.

        Using a `transaction` session to avoid conflicts on inserting.

        Parameters
        ----------
        ctx: Context
            The invocation context.
        name: str
            The name of the Tag.
        content: str
            The content of the Tag.
        """
        query = """
            WITH tag_insert AS (
                INSERT INTO tags (name, content, owner_id, location_id)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id)
            INSERT
            INTO tag_lookup (name, owner_id, location_id, parent_id)
            VALUES ($1, $3, $4, (SELECT id FROM tag_insert));
        """
        async with ctx.db.acquire() as connection:
            tr = connection.transaction()
            await tr.start()

            try:
                await connection.execute(query, name, content, ctx.author.id, ctx.guild.id)
            except Exception as e:
                await tr.rollback()
                match e:
                    case asyncpg.UniqueViolationError():
                        raise BadArgument('A Tag with this name already exists.', 'name')
                    case asyncpg.StringDataRightTruncationError():
                        raise BadArgument('Tag Name length out of range, max. 100 characters.', 'name')
                    case asyncpg.CheckViolationError():
                        raise BadArgument('Tag Content is missing.', 'name')
                    case _:
                        raise BadArgument(
                            'Tag could not be created due to an Unknown reason. Try again later?', 'name')
            else:
                await tr.commit()
                await ctx.send_success(f'Tag `{name}` was successfully created.')

    def is_tag_reserved(self, guild_id: int, name: str) -> bool:
        """Helper method to check if a Tag with ``name`` is currently being made or reserved.

        Note
        ----
        This doesn't check if the Tag exact_matchly exists.
        This needs to be handled by the caller.
        """
        first_word, *_ = name.partition(' ')

        root: commands.GroupMixin = self.bot.get_command('tag')  # type: ignore
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
    ) -> list[app_commands.Choice[int]]:
        query = "SELECT * FROM tags WHERE location_id=$1 ORDER BY uses;"
        tags: list[Tag] = [
            Tag(bot=self.bot, record=record) for record in await self.bot.db.fetch(query, interaction.guild_id)]

        results = fuzzy.finder(current, tags, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, tag.choice_text), value=str(tag.id))
            for length, start, tag in results[:20]
        ]

    async def aliased_tag_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        query = """
            SELECT tag_lookup.*
            FROM tag_lookup
                     INNER JOIN tags ON tags.id = tag_lookup.parent_id
            WHERE tag_lookup.location_id = $1
            ORDER BY uses DESC;
        """
        tags: list[AliasTag] = [
            AliasTag(record=record) for record in await self.bot.db.fetch(query, interaction.guild_id)]

        results = fuzzy.finder(current, tags, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, tag.choice_text), value=str(tag.id))
            for length, start, tag in results[:20]
        ]

    async def owned_non_aliased_tag_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        query = "SELECT * FROM tags WHERE location_id=$1 AND owner_id=$2 ORDER BY uses;"
        tags: list[Tag] = [
            Tag(bot=self.bot, record=record) for record in await
            self.bot.db.fetch(query, interaction.guild_id, interaction.user.id)]

        results = fuzzy.finder(current, tags, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, tag.choice_text), value=str(tag.id))
            for length, start, tag in results[:20]
        ]

    @group(
        'tag',
        description='Shows a tag from the server.',
        fallback='show',
        guild_only=True,
        hybrid=True
    )
    @describe(name_or_id='The tag to retrieve')
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=aliased_tag_autocomplete)
    async def tag(
            self,
            ctx: Context,
            *,
            name_or_id: Annotated[str | int, TagNameOrID(lower=True, with_id=True)]  # type: ignore
    ) -> None:
        """Retrieves a tag from the server.
        If the tag is an alias, the original tag will be retrieved instead.
        """
        await self.send_tag(ctx, name_or_id)

    @tag.command(
        'alias',
        description='Creates a new alias for an existing tag.',
        examples=['new-alias original-tag',
                  '\'new alias\' original tag'],
        guild_only=True
    )
    @describe(new_alias='The new alias to set', original_tag='The original tag to alias')
    @app_commands.rename(new_alias='new-alias', original_tag='original-tag')
    @app_commands.autocomplete(original_tag=non_aliased_tag_autocomplete)  # type: ignore
    async def tag_alias(
            self,
            ctx: Context,
            new_alias: Annotated[str, TagNameOrID],
            *,
            original_tag: Annotated[str, TagNameOrID]
    ) -> None:
        """Assign an alias to an existing tag of yours.
        `Note:` You have to be the owner of the Tag.
        One the Original Tag gets deleted, all the assigned aliases will be deleted too.
        Every alias can be only assigned to one Tag.
        If you want to edit an alias, you have to delete it and create a new one.
        """
        query = """
            INSERT INTO tag_lookup (name, owner_id, location_id, parent_id)
            SELECT $1, $4, tag_lookup.location_id, tag_lookup.parent_id
            FROM tag_lookup
            WHERE tag_lookup.location_id = $3
              AND LOWER(tag_lookup.name) = $2;
        """
        try:
            status = await ctx.db.execute(query, new_alias, original_tag.lower(), ctx.guild.id, ctx.author.id)
        except asyncpg.UniqueViolationError:
            raise BadArgument('This alias is already taken.', 'new_alias')
        else:
            if status[-1] == '0':
                raise BadArgument('The original tag could not be found.', 'original_tag')
            else:
                await ctx.send_success(
                    f'Tag alias **{new_alias}** that redirects to **{original_tag}** successfully created.')

    @tag.command(
        'create',
        description='Creates a new tag in the server.',
        aliases=['add'],
        examples=['new-tag This is the content of the tag.',
                  '\'new tag\' This is the content of the tag.'],
        guild_only=True
    )
    @describe(name='The tag name', content='The tag content')
    async def tag_create(
            self,
            ctx: Context,
            name: Annotated[str, TagNameOrID],
            *,
            content: Annotated[str, TagContent]
    ) -> None:
        """Creates a new Tag owned by yourself in this server.
        The tag name must be between 1 and 100 characters long.
        The tag content must be less than 2000 characters long.
        `Note:` You can create aliases for Tags using `tags alias <alias-name> <original-name>`
        """
        with self.reserve_tag(ctx.guild.id, name):
            await self.create_tag(ctx, name, content)

    @tag.command(
        'make',
        description='Interactively create a Tag owned by yourself in this server.',
        ignore_extra=True,
        guild_only=True
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
                    'message', timeout=timeout,
                    check=lambda msg: msg.author == ctx.author and ctx.channel == msg.channel)
                return user_input.content
            except TimeoutError:
                return None

        name = await get_user_input('What would you like the tag\'s **name** to be?')
        if name is None:
            return

        try:
            ctx.message = original
            name = await converter.convert(ctx, name)
        except BadArgument:
            raise
        finally:
            ctx.message = original

        tag = self.get_tag(name_or_id=name, location_id=ctx.guild.id, only_parent=True, exact_match=True)
        if tag is not None:
            raise BadArgument('A Tag with this name already exists.')

        with self.reserve_tag(ctx.guild.id, name):
            content_prompt = (
                f'The new Tags name is **{name}**.\n'
                f'Please enter now a content for the tag.\n'
                f'You can type "`{ctx.prefix}abort`" to abort the tag make process.'
            )
            content = await get_user_input(content_prompt, timeout=100.0)

            if content == f'{ctx.prefix}abort':
                return

            if content:
                clean_content = await TagContent().convert(ctx, content)

                if ctx.message.attachments:
                    clean_content = f'{clean_content}\n{ctx.message.attachments[0].url}'

                await self.create_tag(ctx, name, clean_content)

        try:
            await ctx.channel.delete_messages(messages)
        except discord.HTTPException:
            pass

    async def guild_tag_stats(self, ctx: Context) -> None:
        embed = discord.Embed(colour=helpers.Colour.white(), title=f'Tag Statistics for {ctx.guild.name}')
        embed.set_thumbnail(url=get_asset_url(ctx.guild))
        embed.set_footer(text='Tag Statistics for this Server.')

        total_tags_query = "SELECT COUNT(*) as total_tags FROM tags WHERE location_id=$1;"
        total_tags = await self.bot.db.fetchval(total_tags_query, ctx.guild.id)

        if not total_tags:
            embed.description = '*There are no statistics available.*'
        else:
            total_uses_query = "SELECT COUNT(*) FROM commands WHERE guild_id=$1 AND command='tag';"
            total_uses = await self.bot.db.fetchval(total_uses_query, ctx.guild.id)

            embed.add_field(
                name='**Guild Stats**',
                value=f'Total Tags: **{total_tags}**\n'
                      f'Total Uses: **{total_uses}**\n\n'
                      f'*with **{usage_per_day(ctx.me.joined_at, total_uses):.2f}** tag uses per day*',
                inline=False
            )

        most_used_tags_query = """
            SELECT
                name,
                uses
            FROM tags
            WHERE location_id=$1
            ORDER BY uses DESC
            LIMIT 3;
        """
        most_used_records = await ctx.db.fetch(most_used_tags_query, ctx.guild.id)
        most_used_tags_value = '\n'.join(
            f'{medal_emoji(index)}: {name} (**{uses}** uses)'
            for index, (name, uses) in enumerate(most_used_records)
        )

        embed.add_field(name='**Most Used Tags**', value=most_used_tags_value, inline=False)

        top_tag_users_query = """
            SELECT
                COUNT(*) AS "uses",
                author_id
            FROM commands
            WHERE guild_id=$1 AND command='tag'
            GROUP BY author_id
            ORDER BY COUNT(*) DESC
            LIMIT 3;
        """
        top_tag_users_records = await ctx.db.fetch(top_tag_users_query, ctx.guild.id)
        top_tag_users_value = '\n'.join(
            f'{medal_emoji(index)}: <@{author_id}> (**{uses}** times)'
            for index, (uses, author_id) in enumerate(top_tag_users_records)
        )

        embed.add_field(name='**Top Tag Users**', value=top_tag_users_value, inline=False)

        top_creators_query = """
            SELECT
               COUNT(*) AS "count",
               owner_id
            FROM tags
            WHERE location_id=$1
            GROUP BY owner_id
            ORDER BY COUNT(*) DESC
            LIMIT 3;
        """
        top_creators_records = await ctx.db.fetch(top_creators_query, ctx.guild.id)
        top_creators_value = '\n'.join(
            f'{medal_emoji(index)}: <@{owner_id}> (**{count}** tags)'
            for index, (count, owner_id) in enumerate(top_creators_records)
        )
        embed.add_field(name='**Top Creators**', value=top_creators_value, inline=False)

        await ctx.send(embed=embed)

    @staticmethod
    async def member_tag_stats(ctx: Context, member: discord.Member | discord.User) -> None:
        query = """
            SELECT COUNT(*) OVER ()  AS "count",
                   SUM(uses) OVER () AS "total_uses"
            FROM tags
            WHERE location_id = $1
              AND owner_id = $2
            ORDER BY uses DESC
            LIMIT 1;
        """
        records = await ctx.db.fetchrow(query, ctx.guild.id, member.id)

        if not records:
            await ctx.send_error('No Tag Statistics found for this member.')
            return

        embed = discord.Embed(color=helpers.Colour.white())
        embed.set_author(name=str(member), icon_url=member.display_avatar.url)
        embed.set_thumbnail(url=get_asset_url(member))
        embed.set_footer(text='Tag Stats for this Member.')

        query = "SELECT COUNT(*) FROM commands WHERE guild_id=$1 AND command='tag' AND author_id=$2;"
        count: tuple[int] = await ctx.db.fetchrow(query, ctx.guild.id, member.id)  # type: ignore

        embed.add_field(name='**Tag Command invoked**', value=f'**{count[0]}** times', inline=False)
        embed.add_field(name='**Owned Tags**', value=records['count'])
        embed.add_field(name='**Owned Tags Used**', value=records['total_uses'])

        query = """
            SELECT name,
                   uses
            FROM tags
            WHERE location_id = $1
              AND owner_id = $2
            ORDER BY uses DESC
            LIMIT 3;
        """
        records = await ctx.db.fetch(query, ctx.guild.id, member.id)
        for index, (name, uses) in enumerate(records):
            embed.add_field(
                name=f'**#{index + 1} {medal_emoji(index)}**',
                value=f'**{name}** (**{uses}** uses)',
                inline=False
            )

        await ctx.send(embed=embed)

    @staticmethod
    async def send_tags_to_text(ctx: Context, tags: list[asyncpg.Record]) -> None:
        table = TabularData()
        table.set_columns(list(tags[0].keys()))
        table.add_rows(list(r.values()) for r in tags)
        fp = io.BytesIO(table.render().encode('utf-8'))
        await ctx.send(file=discord.File(fp, 'tags.txt'))

    @tag.command(
        'stats',
        description='Shows Tag Statistics about the Server or a Member.',
        guild_only=True
    )
    @describe(
        member='The member to get tag statistics for. If not given, the server\'s tag statistics will be shown.')
    async def tag_stats(self, ctx: Context, *, member: discord.Member | None = None) -> None:
        """Shows Tag Statistics about the Server or a Member."""
        if member is None:
            await self.guild_tag_stats(ctx)
        else:
            await self.member_tag_stats(ctx, member)

    @tag.command(
        'edit',
        description='Edit the content or name of a Tag.',
        guild_only=True
    )
    @describe(
        name_or_id='The Tag you want to edit. (Must be yours)',
        content='The new content of the tag. (If not given, you will be prompted to edit the tag in a modal.)',
    )
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=owned_non_aliased_tag_autocomplete)
    async def tag_edit(
            self,
            ctx: Context,
            name_or_id: Annotated[str | int, TagNameOrID(lower=True, with_id=True)],  # type: ignore
            use_embed: bool | None = None,
            *,
            content: Annotated[str | None, TagContent(required=False)] = None,  # type: ignore
    ) -> None:
        """Edit the content or name of a Tag.
        `Note:` If you don't pass a content, you will be prompted to edit the tag in a modal.
        This may be useful for larger contents.

        You can only edit the name of the tag in within the modal.
        """
        await ctx.defer()

        tag = await self.get_tag(name_or_id, location_id=ctx.guild.id, owner_id=ctx.author.id, only_parent=True)

        if not tag:
            raise BadArgument('Could not find a tag with that name, are you sure it exists or you own it?',
                              'name_or_id')

        name = tag.name
        if content is None and use_embed is None:
            if ctx.interaction is None:
                raise BadArgument('You need to pass a content or use the modal to edit the tag.', 'content')
            else:
                modal = TagEditModal(tag)
                await ctx.interaction.response.send_modal(modal)
                await modal.wait()
                ctx.interaction = modal.interaction
                content = modal.content.value
                name = modal.tag_name.value

        if content and len(content) > 2000:
            raise BadArgument('Tag Content is too long, max. 2000 characters.', 'content')

        await tag.update(name=name, use_embed=use_embed, content=content)
        await ctx.send_success('Successfully edited tag.')
        await self.send_tag(ctx, tag.id)

    @tag.command(
        'delete',
        description='Removes a Tag by Name or ID.',
        aliases=['remove'],
        guild_only=True
    )
    @describe(name_or_id='The assigned Tag to delete.')
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=owned_non_aliased_tag_autocomplete)
    async def tag_delete(
            self,
            ctx: Context,
            *,
            name_or_id: Annotated[str | int, TagNameOrID(lower=True, with_id=True)],  # type: ignore
    ) -> None:
        """Removes a Tag by ID owned by yourself.
        Your Tags can also be removed by Moderators if they have the `MANAGE MESSAGES` permission.
        `Note:` This will also remove all aliases of the tag.
        """
        form = {
            'location_id': ctx.guild.id,
            'only_parent': True,
        }
        if not (ctx.author.id == self.bot.owner_id or ctx.author.guild_permissions.manage_messages):
            form['owner_id'] = ctx.author.id

        tag = await self.get_tag(name_or_id, **form)

        if not tag:
            raise BadArgument('Could not find a tag with that name, are you sure it exists or you own it?',
                              'name_or_id')

        await tag.delete()

    @tag.command(
        'info',
        description='Shows you Information about a Tag.',
        guild_only=True
    )
    @describe(name_or_id='The name or id of the tag to get info about.')
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=aliased_tag_autocomplete)
    async def tag_info(
            self,
            ctx: Context,
            *,
            name_or_id: Annotated[str | int, TagNameOrID(lower=True, with_id=True)],  # type: ignore
    ) -> None:
        """Shows you Information about a Tag."""
        tag = await self.get_tag(name_or_id, location_id=ctx.guild.id)

        if tag is None:
            raise BadArgument('Could not find a tag with that name, are you sure it exists or you own it?',
                              'name_or_id')

        embed = discord.Embed(title='Tag Info', description=f'**```{tag.name}```**\n')
        embed.add_field(name='**Owner**', value=f'<@{tag.owner_id}>')

        user = self.bot.get_user(tag.owner_id) or (await self.bot.fetch_user(tag.owner_id))
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)

        embed.timestamp = tag.created_at.replace(tzinfo=datetime.UTC)
        embed.set_footer(text=f'[{tag.id}] • Tag created at')

        rank = await tag.get_rank()
        if rank and rank in (1, 2, 3):
            embed.add_field(name='**Rank**', value=f'**#{rank}** {chr(129350 + int(rank))}')

        embed.add_field(name='**Tag Used**', value=tag.uses)

        if tag.aliases:
            aliases_info = [
                f'**{alias.name}** [`{alias.id}`] ({discord.utils.format_dt(alias.created_at, style='D')})'
                for alias in tag.aliases
            ]
            embed.add_field(name=f'**Aliases ({len(tag.aliases)})**', value='\n'.join(aliases_info), inline=False)

        await ctx.send(embed=embed)

    @tag.command(
        'raw',
        description='This displays you the raw content of a tag.',
        aliases=['content'],
        guild_only=True
    )
    @describe(name_or_id='The name or id of the tag to display the escaped markdown content.')
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=non_aliased_tag_autocomplete)
    async def tag_raw(
            self,
            ctx: Context,
            *,
            name_or_id: Annotated[str | int, TagNameOrID(lower=True, with_id=True)],  # type: ignore
    ) -> None:
        """This displays you the raw content of a tag."""
        await self.send_tag(ctx, name_or_id, escape_markdown=True)

    @staticmethod
    async def filter_tags(ctx: Context, flags: TagListFlags | TagSearchFlags) -> list[asyncpg.Record]:
        """|coro|

        Filters the Tags based on the given flags.
        This is used for the `tag list` and `tag search` commands.

        Parameters
        ----------
        ctx: Context
            The invocation context.
        flags: TagListFlags | TagSearchFlags
            The flags to filter the Tags with.

        Returns
        -------
        list[asyncpg.Record]
            The list of Tags that were found.
        """
        SORT = {
            'id': 'id',
            'newest': 'created_at DESC',
            'oldest': 'created_at ASC',
            'name': 'name'
        }.get(flags.sort, 'name')

        member: discord.Member | None = None
        if hasattr(flags, 'member'):
            member = flags.member or ctx.author

        if not flags.query:
            query = """
                SELECT name, id
                FROM tag_lookup
                WHERE location_id=$1
            """
            if member:
                query += " AND owner_id=$2"
                values = (ctx.guild.id, member.id)
            else:
                values = (ctx.guild.id,)

            query += f" ORDER BY {SORT};"
        else:
            if flags.sort == 'name':
                SORT = 'similarity(name, $2) DESC'

            query = f"""
                SELECT name, id
                FROM tag_lookup
                WHERE location_id=$1 AND name % $2
                ORDER BY {SORT};
            """
            if member:
                query += " AND owner_id=$3"
                values = (ctx.guild.id, flags.query, member.id)
            else:
                values = (ctx.guild.id, flags.query)

            query += f" ORDER BY {SORT};"

        return await ctx.db.fetch(query, *values)

    @tag.command(
        'list',
        description='Shows a list of Tags owned by yourself or a given member.',
        guild_only=True
    )
    @describe(member='The member to list tags of, if not given then it defaults to you.')
    async def tag_list(self, ctx: Context, *, flags: TagListFlags) -> None:
        """Shows a list of Tags owned by yourself or a given member."""
        member = flags.member or ctx.author
        rows = await self.filter_tags(ctx, flags)
        if not rows:
            await ctx.send_error(f'No tags found for **{member}**.')
            return

        if flags.to_text:
            return await self.send_tags_to_text(ctx, rows)

        embed = discord.Embed(
            title='Tag Search',
            description=f'**{member}\'s** Tags in {ctx.guild.name}\n'
                        f'Sorted by: **{flags.sort}**',
            colour=helpers.Colour.white(),
            timestamp=discord.utils.utcnow())
        embed.set_footer(text=f'{pluralize(len(rows)):entry|entries}')

        results = [f'`{index}.` {entry}' for index, entry in
                   enumerate([TagPageEntry(record=row) for row in rows], 1)]
        await LinePaginator.start(
            ctx, entries=results, search_for=True, per_page=20, embed=embed
        )

    @tag.command(
        'search',
        description='Search for tags matching the given query.',
        guild_only=True
    )
    @describe(query='The tag name to search for')
    @app_commands.choices(
        sort=[
            app_commands.Choice(name='Name', value='name'),
            app_commands.Choice(name='Newest', value='newest'),
            app_commands.Choice(name='Oldest', value='oldest'),
            app_commands.Choice(name='ID', value='id'),
        ]
    )
    async def tags_search(
            self,
            ctx: Context,
            *,
            flags: TagSearchFlags
    ) -> None:
        """Search for tags matching the given query.
        `Note:` To use autocomplete, you have to at least provide three characters.
        """
        rows = await self.filter_tags(ctx, flags)
        if not rows:
            await ctx.send_error('No tags found.')
            return

        if flags.to_text:
            return await self.send_tags_to_text(ctx, rows)

        embed = discord.Embed(
            title='Tag Search',
            description=f'Sorted by: **{flags.sort}**',
            colour=helpers.Colour.white(),
            timestamp=discord.utils.utcnow())
        embed.set_footer(text=f'{pluralize(len(rows)):entry|entries}')

        results = [f'`{index}.` {TagPageEntry(record=row)}' for index, row in enumerate(rows, 1)]
        await LinePaginator.start(
            ctx, entries=results, search_for=True, per_page=20, embed=embed)

    @tag.command(
        'purge',
        description='Bulk remove all Tags and assigned Aliases of a given User.',
        guild_only=True,
        user_permissions=PermissionTemplate.mod
    )
    @describe(member='The member to remove all tags of')
    async def tag_purge(self, ctx: Context, member: discord.User) -> None:
        """Bulk remove all Tags and assigned Aliases of a given User."""
        query = "SELECT COUNT(*) FROM tags WHERE location_id=$1 AND owner_id=$2;"
        count: int = await self.bot.db.fetchval(query, ctx.guild.id, member.id)

        if count == 0:
            await ctx.send_error(f'No tags found for **{member}**.')
            return

        confirm = await ctx.confirm(
            f'{Emojis.warning} This will delete **{count}** tags are you sure? **This action cannot be reversed**.')
        if not confirm:
            return

        query = "DELETE FROM tags WHERE location_id=$1 AND owner_id=$2;"
        await ctx.db.execute(query, ctx.guild.id, member.id)

        await ctx.send_success(f'Successfully removed all **{count}** tags that belong to **{member}**.')

    @tag.command(
        'claim',
        description='Claim a tag by yourself if the User is not in this server anymore or the tag has no owner.',
        guild_only=True
    )
    @describe(name_or_id='The tag to claim')
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=aliased_tag_autocomplete)
    async def tag_claim(
            self,
            ctx: Context,
            *,
            name_or_id: Annotated[str | int, TagNameOrID(lower=True, with_id=True)],  # type: ignore
    ) -> None:
        """Claim a tag by yourself if the User is not in this server anymore or the tag has no owner."""
        tag = await self.get_tag(name_or_id, location_id=ctx.guild.id, exact_match=True)

        member = await self.bot.get_or_fetch_member(ctx.guild, tag.owner_id)
        if member is not None:
            await ctx.send_error(f'Tag **{tag.name}** is already owned by **{member}**.')
            return

        if isinstance(tag, AliasTag):
            await tag.transfer(ctx.author, connection=self.bot.db)  # type: ignore
        else:
            await tag.transfer(ctx.author, only_parent=True)

        await ctx.send_success('Successfully transferred tag ownership to you.')

    @tag.command(
        'transfer',
        description='Transfer a tag owned by you to another member.',
        guild_only=True
    )
    @describe(member='The member to transfer the tag to.', name_or_id='The tag to transfer.')
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=aliased_tag_autocomplete)
    async def tag_transfer(
            self,
            ctx: Context,
            member: discord.Member,
            *,
            name_or_id: Annotated[str, TagNameOrID(with_id=True)]  # type: ignore
    ) -> None:
        """Transfer a tag owned by you to another member."""
        if member.bot:
            await ctx.send_error('You cannot transfer tags to bots.')
            return

        tag = await self.get_tag(
            name_or_id, location_id=ctx.guild.id, owner_id=ctx.author.id, only_parent=True)

        if tag is None:
            raise BadArgument('Could not find a tag with that name, are you sure it exists or you own it?',
                              'name_or_id')

        view = View.from_items(TagTransferConfirmButton(tag, ctx.author.id),
                               TagTransferDeclineButton(tag, ctx.author.id), timeout=None)
        embed = discord.Embed(
            title='Tag Transfer Request',
            description=f'User **{ctx.author}** from Server **{ctx.guild}** wants to transfer the tag **{tag.name}** [`{tag.id}`] to you.'
                        f'\n\nDo you want to accept this transfer?',
            color=helpers.Colour.light_grey(),
            timestamp=ctx.utcnow()
        )
        await member.send(embed=embed, view=view)
        await ctx.send_info(f'Transfer request for tag **{tag.name}** has been sent to **{member}**.')
        await tag.update(owner_id=-1)  # -1 indicates that the tag is in transfer

    @tag.command(
        'export',
        description='Exports all your tags/server tags to a csv file.',
        guild_only=True
    )
    @cooldown(1, 30, commands.BucketType.member)
    @describe(which='Whether to export server tags or personal tags. (Server tags only for server owners)')
    async def tag_export(
            self,
            ctx: Context,
            which: Literal['server', 'personal'] = 'personal',
    ) -> None:
        """Exports all your tags/server tags to a csv file."""
        form = {
            'location_id': ctx.guild.id,
        }
        if which == 'server':
            if ctx.author.id != ctx.guild.owner_id:
                raise BadArgument('You need to be the server owner to export all server tags.')
        else:
            form['owner_id'] = ctx.author.id

        async with ctx.channel.typing(), ctx.db.acquire() as conn, conn.transaction():
            records = await conn.fetch(
                f"SELECT name, content FROM tags WHERE {' AND '.join(f'{k}=${i}' for i, k in enumerate(form, 1))};",
                *form.values()
            )

        if not records:
            await ctx.send_error('No tags found to export.')
            return

        buffer = io.BytesIO()
        writer = csv.writer(buffer, delimiter=',', quotechar="'", quoting=csv.QUOTE_MINIMAL)
        for record in records:
            writer.writerow([record[0], record[1]])
        buffer.seek(0)

        file = discord.File(
            fp=buffer, filename=f'{ctx.author.id}_tags.csv' if which == 'personal' else f'{ctx.guild.id}_tags.csv'
        )
        await ctx.send(file=file)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Tags(bot))
