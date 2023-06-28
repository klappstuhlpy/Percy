from __future__ import annotations

import asyncio
import contextlib
import csv
import datetime
import io
from typing import TYPE_CHECKING, Optional, List, Literal, Union

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands
from typing_extensions import Annotated

from cogs.utils.paginator import LinePaginator
from .emoji import usage_per_day
from .utils import formats, fuzzy, helpers, cache, commands_ext
from .utils.formats import plural, medal_emojize, get_shortened_string
from .utils.helpers import PostgresItem

if TYPE_CHECKING:
    from bot import Percy
    from .utils.context import GuildContext, Context


class TagPageEntry(PostgresItem):
    id: int
    name: str

    __slots__ = ('id', 'name')

    def __str__(self) -> str:
        return f'{self.name} [`{self.id}`]'


class TagNameOrID(commands.clean_content):
    def __init__(self, *, lower: bool = False, with_id: bool = False):
        self.lower: bool = lower
        self.with_id: bool = with_id
        super().__init__()

    async def convert(self, ctx: Context, argument: str) -> str | int:
        converted = await super().convert(ctx, argument)
        lower = converted.lower().strip()

        if not lower:
            raise commands.BadArgument(
                '<:redTick:1079249771975413910> Please enter a valid tag name' + " or id." if self.with_id else '.')

        if len(lower) > 100:
            raise commands.BadArgument(
                f'<:redTick:1079249771975413910> Tag names must be 100 characters or less. (You have *{len(lower)}* characters)')

        cog: Tags = ctx.bot.get_cog('Tags')  # noqa
        if cog is None:
            raise commands.BadArgument('<:redTick:1079249771975413910> Tags are currently unavailable.')

        if cog.is_tag_reserved(ctx.guild.id, argument):
            raise commands.BadArgument(
                '<:redTick:1079249771975413910> Hey, that\'s a reserved tag name. Choose another one.')

        if self.with_id:
            if converted and converted.isdigit():
                return int(converted)

        return converted.strip() if not self.lower else lower


class TagContent(commands.clean_content):

    def __init__(self, *, required: bool = True):
        self.required = required
        super().__init__()

    async def convert(self, ctx: Context, argument: str) -> str:
        if not argument and not self.required:
            return argument

        converted = await super().convert(ctx, argument)

        if len(converted) > 2000:
            raise commands.BadArgument(
                f'<:redTick:1079249771975413910> Tag content must be 2000 characters or less. (You have *{len(argument)}* characters)')

        return converted


class TagSearchFlags(commands.FlagConverter, prefix='--', delimiter=' '):
    query: Optional[str] = commands.flag(description="The query to search for", aliases=['q'], default=None)
    sort: Literal['name', 'newest', 'oldest', 'id'] = commands.flag(
        description="The key to sort the results.", aliases=['s'], default='name')
    to_text: bool = commands.flag(description="Whether to output the results as raw tabular text.", aliases=['tt'],
                                  default=False)


class TagListFlags(commands.FlagConverter, prefix='--', delimiter=' '):
    member: Optional[discord.Member] = commands.flag(description="The member to search for", aliases=['m'],
                                                     default=None)
    query: Optional[str] = commands.flag(description="The query to search for", aliases=['q'], default=None)
    sort: Literal['name', 'newest', 'oldest', 'id'] = commands.flag(
        description="The key to sort the results.", aliases=['s'], default='name')
    to_text: bool = commands.flag(description="Whether to output the results as raw tabular text.", aliases=['tt'],
                                  default=False)


class TagEditModal(discord.ui.Modal, title='Edit Tag'):
    content = discord.ui.TextInput(
        label='New Content', required=True, style=discord.TextStyle.long, min_length=1, max_length=2000
    )

    def __init__(self, tag: Tag) -> None:
        super().__init__()
        self.content.default = tag.content

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction  # noqa
        self.text = str(self.content)  # noqa
        self.stop()


class TagMakeModal(discord.ui.Modal, title='Create a New Tag'):
    name = discord.ui.TextInput(label='Name', required=True, max_length=100, min_length=1)
    content = discord.ui.TextInput(
        label='Content', required=True, style=discord.TextStyle.long, min_length=1, max_length=2000
    )

    def __init__(self, cog: Tags, ctx: GuildContext):
        super().__init__()
        self.cog: Tags = cog
        self.ctx: GuildContext = ctx

    async def on_submit(self, interaction: discord.Interaction) -> None:
        assert interaction.guild_id is not None
        name = str(self.name)
        try:
            name = await TagNameOrID().convert(self.ctx, name)
        except commands.BadArgument as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        self.ctx.interaction = interaction
        content = str(self.content)
        if len(content) > 2000:
            await interaction.response.send_message(
                '<:redTick:1079249771975413910> Consider using a shorter description for your Tag. (2000 max characters)',
                ephemeral=True)
        else:
            with self.cog.reserve_tag(interaction.guild_id, name):
                await self.cog.create_tag(self.ctx, name, content)


class Tag(PostgresItem):
    """Represents a Tag."""

    id: int
    name: str
    content: str
    owner_id: int
    uses: int
    location_id: int
    created_at: datetime.datetime
    use_embed: bool

    __slots__ = ('bot', '_aliases', 'id', 'name', 'content', 'owner_id', 'uses', 'location_id', 'created_at', 'use_embed')

    def __init__(self, bot: Percy, **kwargs):
        super().__init__(**kwargs)
        self.bot: Percy = bot
        self._aliases: list[AliasTag] = []

    @property
    def choice_text(self) -> str:
        return f'[{self.id}] {self.name}'

    @property
    def raw_content(self) -> str:
        return discord.utils.escape_markdown(self.content)

    @property
    def aliases(self) -> List[AliasTag]:
        return self._aliases

    @aliases.setter
    def aliases(self, value: List[AliasTag]) -> None:
        if not isinstance(value, list):
            raise TypeError('Aliases must be a list of AliasTag objects.')

        if any(not isinstance(x, AliasTag) for x in value):
            raise TypeError('Aliases must be a list of AliasTag objects.')

        self._aliases = value

    @property
    def to_embed(self) -> discord.Embed:
        embed = discord.Embed(title=self.name, description=self.content)
        embed.timestamp = self.created_at.replace(tzinfo=datetime.timezone.utc)
        embed.set_footer(text=f'[{self.id}] • Created at')
        return embed

    async def get_rank(self) -> int:
        """|coro|

        Gets the rank of the tag.

        Returns
        -------
        int
            The rank of the tag.
        """
        query = """
            SELECT (
                SELECT COUNT(*)
                FROM tags second
                WHERE (second.uses, second.id) >= (first.uses, first.id)
                AND second.location_id = first.location_id
            ) AS rank
            FROM tags first
            WHERE first.id=$1
        """

        return await self.bot.pool.fetchval(query, self.id)

    async def edit(
            self,
            *,
            content: Optional[str] = None,
            use_embed: Optional[bool] = None,
    ) -> Optional[str]:
        """|coro|

        Edits the tag.

        Parameters
        ----------
        content: Optional[str]
            The new content of the tag.
        use_embed: Optional[bool]
            Whether to use embeds or not.

        Raises
        ------
        commands.BadArgument
            A Tag with this name already exists, or the Tag Name length is out of range or the Tag Name is not valid.

        Returns
        -------
        str
            The update status of the query.
        """
        kwargs = {}

        if content is not None:
            kwargs['content'] = content

        if use_embed is not None:
            kwargs['use_embed'] = use_embed

        if not kwargs:
            return None

        query = "UPDATE tags SET " + ', '.join(f'{k}=${i}' for i, k in enumerate(kwargs, start=2)) + " WHERE id=$1;"
        try:
            updated = await self.bot.pool.fetchrow(query, self.id, *kwargs.values())
        except Exception as e:
            match e:
                case asyncpg.UniqueViolationError():
                    raise commands.BadArgument(
                        '<:redTick:1079249771975413910> A Tag with this name already exists.')
                case asyncpg.StringDataRightTruncationError():
                    raise commands.BadArgument(
                        "<:redTick:1079249771975413910> Tag Name length out of range, max. 100 characters.")
                case asyncpg.CheckViolationError():
                    raise commands.BadArgument("<:redTick:1079249771975413910> Tag Content is missing.")
                case _:
                    raise e

        if content is not None:
            self.content = content
        if use_embed is not None:
            self.use_embed = use_embed

        return updated

    async def delete(self) -> None:
        """|coro|

        Deletes the tag and all corresponding aliases.
        """
        query = "DELETE FROM tags WHERE id=$1;"
        await self.bot.pool.execute(query, self.id)

        query = "DELETE FROM tag_lookup WHERE tag_id=$1;"
        await self.bot.pool.execute(query, self.id)

    async def transfer(self, member: discord.Member, only_parent: bool = False):
        """|coro|

        Transfers the tag to another user.

        Parameters
        ----------
        member: discord.Member
            The member to transfer the tag to.
        only_parent: bool
            Whether to only transfer the parent tag or all aliases as well.

        """
        async with self.bot.pool.acquire() as conn:
            async with conn.transaction():
                query = "UPDATE tags SET owner_id=$1 WHERE id=$2;"
                await conn.execute(query, member.id, self.id)
                if not only_parent:
                    query = "UPDATE tag_lookup SET owner_id=$1 WHERE tag_id=$2;"
                    await conn.execute(query, member.id, self.id)


class AliasTag(PostgresItem):
    """Represents an Alias for a Tag."""

    id: int
    name: str
    tag_id: int
    owner_id: int
    location_id: int
    created_at: datetime.datetime

    __slots__ = ('parent', 'id', 'name', 'tag_id', 'owner_id', 'location_id', 'created_at')

    def __init__(self, parent: Optional[Tag] = None, **kwargs):
        super().__init__(**kwargs)
        self.parent: Optional[Tag] = parent

    @property
    def choice_text(self) -> str:
        return f'[{self.id}] {self.name}'

    async def transfer(self, member: discord.Member, /, *, connection: Optional[asyncpg.Connection] = None):
        """|coro|

        Transfers the alias to another user.

        Parameters
        ----------
        member: discord.Member
            The member to transfer the alias to.
        connection: Optional[asyncpg.Connection]
            The connection to use. Defaults to the bot's pool.
            Needs to be used if there is no :attr:`parent` attribute.

        """
        if self.parent:
            con = self.parent.bot.pool
        else:
            con = connection

        async with con.acquire() as conn:
            async with conn.transaction():
                query = "UPDATE tag_lookup SET owner_id=$1 WHERE id=$2;"
                await conn.execute(query, member.id, self.id)

    async def delete(self) -> None:
        """|coro|

        Deletes the alias.
        """
        query = "DELETE FROM tag_lookup WHERE id=$1;"
        await self.parent.bot.pool.execute(query, self.id)


class Tags(commands.Cog):
    """Commands to fetch something by a tag name.

    ## Note
    If you want to create a Tag with not a Slash Commands, if you want to create a Tag with a name that is longer than one word,
    you need to wrap the name in double quotes, otherwise the command will only take the first word as the name and add the rest to the content.
    """

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        # We create this temporary cache to avoid Users creating two Tags with the
        # same name at the same time to avoid conflicts
        self._temporary_reserved_tags: dict[int, set[str]] = {}

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='navigate', id=1103420880056488038)

    @contextlib.contextmanager
    def reserve_tag(self, guild_id: int, name: str) -> None:
        """Reserves a tag name for a guild.

        This is to avoid two users creating a tag with the same name at the same time.
        """
        name = name.lower()

        if guild_id not in self._temporary_reserved_tags:
            self._temporary_reserved_tags[guild_id] = set()

        if name in self._temporary_reserved_tags[guild_id]:
            raise commands.BadArgument(
                '<:redTick:1079249771975413910> Hey, this Name is temporarily reserved, try again later or use a different one.')

        self._temporary_reserved_tags[guild_id].add(name)
        try:
            yield None
        finally:
            self._temporary_reserved_tags[guild_id].discard(name)

            if len(self._temporary_reserved_tags[guild_id]) == 0:
                del self._temporary_reserved_tags[guild_id]

    @cache.cache(strategy=cache.Strategy.ADDITIVE, maxsize=300)
    async def get_tag(
            self,
            name_or_id: Union[str, int],
            *,
            owner_id: Optional[int] = None,
            location_id: Optional[int] = None,
            only_parent: bool = False,
            similarites: bool = False,
            actual: bool = False,
    ) -> Optional[Union[list[AliasTag] | Tag | AliasTag]]:
        """|coro| @cached

        Gets the Original :class:`Tag` with Optional all :class:`AliasTag` s of it.
        If no exact match is found, it will return a list of :class:`AliasTag`s that are similar to the name.

        Note
        ----
        Returning a list with smiliar Tags is only possible if :attr:`name_or_id` is a string and :attr:`similarites` is True.

        Parameters
        ----------
        name_or_id: Union[str, int]
            The name or ID of the Tag to get.
        owner_id: Optional[int]
            The ID of the User to get the Tag from.
        location_id: Optional[int]
            The ID of the Guild to get the Tag from.
        only_parent: bool
            Whether to only get the parent Tag.
        similarites: bool
            Whether to get similar Tags.
        actual: bool
            Whether to get only the Tag that meets the requirements.

        Returns
        -------
        Optional[Union[Tag, AliasTag] | List[Union[Tag, AliasTag]]]
            The Tag or AliasTag if found, else None.
        """
        search_kwargs = {}

        assert isinstance(
            name_or_id, (str, int)
        ), f'`name_or_id` must be a `str` or `int`, not {name_or_id.__class__.__name__}'

        if isinstance(name_or_id, int):
            search_kwargs['id'] = name_or_id
        else:
            if name_or_id.isdigit():
                search_kwargs['id'] = int(name_or_id)
            else:
                search_kwargs['LOWER(name)'] = name_or_id.lower()

        if location_id:
            search_kwargs['location_id'] = location_id

        if owner_id:
            search_kwargs['owner_id'] = owner_id

        query = "SELECT * FROM tags WHERE " + ' AND '.join(
            f'{k}=${i}' for i, k in enumerate(search_kwargs, 1)) + " LIMIT 1;"
        parent = await self.bot.pool.fetchrow(query, *search_kwargs.values())

        if not parent and isinstance(name_or_id, str):
            query = "SELECT tags.* FROM tags INNER JOIN tag_lookup t on t.tag_id = tags.id WHERE t.name = $1 LIMIT 1;"
            parent = await self.bot.pool.fetchrow(query, name_or_id.lower())

        if actual:
            if parent:
                return Tag(self.bot, record=parent)
            else:
                query = "SELECT * FROM tag_lookup WHERE " + ' AND '.join(
                    f'{k}=${i}' for i, k in enumerate(search_kwargs, 1)) + " LIMIT 1;"
                alias = await self.bot.pool.fetchrow(query, name_or_id)
                if alias:
                    return AliasTag(record=alias)
                return None

        if not parent:
            return None

        to_return = Tag(self.bot, record=parent)

        if only_parent:
            return to_return

        if 'id' in search_kwargs:
            search_kwargs.pop('id')

        if 'name' in search_kwargs:
            search_kwargs.pop('name')

        search_kwargs['tag_id'] = parent['id']
        query = f"SELECT * FROM tag_lookup WHERE name != '{parent['name']}' AND " + ' AND '.join(
            f'{k}=${i}' for i, k in enumerate(search_kwargs, 1))
        aliases = await self.bot.pool.fetch(query, *search_kwargs.values())

        if aliases:
            to_return.aliases = [AliasTag(parent=to_return, record=alias) for alias in aliases]

        if similarites and not to_return:
            if not isinstance(name_or_id, str):
                raise ValueError('You need to specify a Tag Name to get similar Tags.')

            query = """
                SELECT
                    tag_lookup.name, tag_lookup.id,
                    tag_lookup.name <> t.name AS is_alias
                FROM tag_lookup
                INNER JOIN tags t on t.id = tag_lookup.tag_id
                WHERE tag_lookup.location_id=$1 AND tag_lookup.name % $2
                ORDER BY similarity(tag_lookup.name, $2) DESC
                LIMIT 3;
            """
            rows = await self.bot.pool.fetch(query, location_id, name_or_id)
            to_return = [AliasTag(parent, record=row) if row['is_alias'] else Tag(self.bot, record=row) for row in rows]

        return to_return

    async def send_tag(
            self,
            ctx: GuildContext,
            name_or_id: str | int,
            *,
            escape_markdown: bool = False
    ) -> None:
        """|coro|

        Look up a Tag by name in the given guild. Searching with similarity queries.

        If a Tag is found, sends it with the proper formatting to the destination.
        If no Tag with the exact (LOWERED) name is found, a disambiguation prompt is sent.

        Parameters
        ----------
        ctx: GuildContext
            The invocation context.
        name_or_id: str | int
            The name or ID of the Tag to get.
        escape_markdown: bool
            Whether to escape the markdown in the Tag content.
        """
        tag: Union[list[AliasTag] | Tag] = await self.get_tag(name_or_id=name_or_id, location_id=ctx.guild.id,
                                                              similarites=True)

        if isinstance(tag, list):
            # Assuming no tags were found and similarites are returned instead
            if tag is None or len(tag) == 0:
                await ctx.send_tick(False, 'No Tag with this name or similar name found.')
            else:
                names = '\n'.join(f"* **{r.name}** [`{r.id}`]" for r in tag)
                embed = discord.Embed(title="Did you mean ...", description=names,
                                      colour=self.bot.colour.darker_red())
                await ctx.send(embed=embed)
            return

        if not tag:
            raise commands.BadArgument(f'{ctx.tick(False)} No Tag with the name or ID `{name_or_id}` found.')

        if tag.use_embed and not escape_markdown:
            await ctx.send(embed=tag.to_embed, reference=ctx.replied_reference)
        else:
            await ctx.send(tag.content if not escape_markdown else tag.raw_content, reference=ctx.replied_reference)

        # Just updated the uses of the Tag
        query = "UPDATE tags SET uses = uses + 1 WHERE name = $1 AND location_id=$2 RETURNING *;"
        updated = await ctx.db.fetchrow(query, tag.name, ctx.guild.id)

        tag = Tag(self.bot, record=updated)
        tag.aliases = [AliasTag(parent=tag, record=alias) for alias in tag.aliases]

        self.get_tag.refactor_containing(str(tag.id), tag)
        self.get_tag.refactor_containing(tag.name, tag)

    @staticmethod
    async def create_tag(ctx: GuildContext, name: str, content: str) -> None:
        """|coro|

        Creates a new Tag in the Guild.
        Inserts into `tag_lookup` and `tags` table, `tag_lookup` is the summary of origin tags and aliases.
        In the `tags` table are the root tags with their original names, contents etc.

        Using a `transaction` session to avoid conflicts on inserting.

        Parameters
        ----------
        ctx: GuildContext
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
                RETURNING id
            )
            INSERT INTO tag_lookup (name, owner_id, location_id, tag_id)
            VALUES ($1, $3, $4, (SELECT id FROM tag_insert));
        """

        async with ctx.db.acquire() as connection:
            tr = connection.transaction()
            await tr.start()

            try:
                await connection.execute(query, name, content, ctx.author.id, ctx.guild.id)
            except Exception as e:
                # Rollback the transaction if anything goes wrong
                await tr.rollback()

                match e:
                    case asyncpg.UniqueViolationError():
                        raise commands.BadArgument(
                            '<:redTick:1079249771975413910> A Tag with this name already exists.')
                    case asyncpg.StringDataRightTruncationError():
                        raise commands.BadArgument(
                            "<:redTick:1079249771975413910> Tag Name length out of range, max. 100 characters.")
                    case asyncpg.CheckViolationError():
                        raise commands.BadArgument("<:redTick:1079249771975413910> Tag Content is missing.")
                    case _:
                        raise commands.BadArgument(
                            '<:redTick:1079249771975413910> Tag could not be created due to an Unknown reason. Try again later?')
            else:
                await tr.commit()
                await ctx.send(f'<:greenTick:1079249732364406854> Tag `{name}` was successfully created.')

    def is_tag_reserved(self, guild_id: int, name: str) -> bool:
        """Helper method to check if a Tag with ``name`` is currently being made or reserved.

        Note: This doesn't check if the Tag actually exists.
        This needs to be handled by the caller.
        """

        def in_prod_check() -> bool:
            try:
                being_made = self._temporary_reserved_tags[guild_id]
            except KeyError:
                return False
            else:
                return name.lower() in being_made

        first_word, _, _ = name.partition(' ')

        root: commands.GroupMixin = self.bot.get_command('tag')  # type: ignore
        if first_word in root.all_commands:
            return True
        else:
            return in_prod_check()

    async def non_aliased_tag_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        query = "SELECT * FROM tags WHERE location_id=$1 ORDER BY uses;"
        tags: list[Tag] = [Tag(self.bot, record=record) for record in await self.bot.pool.fetch(query, interaction.guild_id)]

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
            INNER JOIN tags ON tags.id = tag_lookup.tag_id
            WHERE tag_lookup.location_id=$1
            ORDER BY uses DESC;
        """
        tags: list[AliasTag] = [AliasTag(record=record) for record in await self.bot.pool.fetch(query, interaction.guild_id)]

        results = fuzzy.finder(current, tags, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, tag.choice_text), value=str(tag.id))
            for length, start, tag in results[:20]
        ]

    async def owned_non_aliased_tag_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        query = "SELECT * FROM tags WHERE location_id=$1 AND owner_id=$2 ORDER BY uses;"
        tags: list[Tag] = [Tag(self.bot, record=record) for record in
                           await self.bot.pool.fetch(query, interaction.guild_id, interaction.user.id)]

        results = fuzzy.finder(current, tags, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, tag.choice_text), value=str(tag.id))
            for length, start, tag in results[:20]
        ]

    @commands_ext.command(
        commands.hybrid_group,
        name="tag",
        description="Shows a tag from the server.",
        fallback="show"
    )
    @commands.guild_only()
    @app_commands.describe(name_or_id='The tag to retrieve')
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=aliased_tag_autocomplete)  # type: ignore
    async def tag(
            self,
            ctx: GuildContext,
            *,
            name_or_id: Annotated[Union[str, int], TagNameOrID(lower=True, with_id=True)]  # type: ignore
    ):
        """Retrieves a tag from the server.
        If the tag is an alias, the original tag will be retrieved instead.
        """
        await self.send_tag(ctx, name_or_id)

    @commands_ext.command(
        tag.command,
        name="alias",
        description="Creates a new alias for an existing tag.",
        examples=["new-alias original-tag",
                  "\"new alias\" original tag"]
    )
    @commands.guild_only()
    @app_commands.rename(new_alias='new-alias', original_tag='original-tag')
    @app_commands.describe(new_alias='The new alias to set', original_tag='The original tag to alias')
    @app_commands.autocomplete(original_tag=non_aliased_tag_autocomplete)  # type: ignore
    async def tag_alias(
            self,
            ctx: GuildContext,
            new_alias: Annotated[str, TagNameOrID],
            *,
            original_tag: Annotated[str, TagNameOrID]
    ):
        """Assign an alias to an existing tag of yours.
        `Note:` You have to be the owner of the Tag.
        One the Original Tag gets deleted, all the assigned aliases will be deleted too.
        Every alias can be only assigned to one Tag.
        If you want to edit an alias, you have to delete it and create a new one.
        """

        query = """
            INSERT INTO tag_lookup (name, owner_id, location_id, tag_id)
            SELECT $1, $4, tag_lookup.location_id, tag_lookup.tag_id
            FROM tag_lookup
            WHERE tag_lookup.location_id=$3 AND LOWER(tag_lookup.name)=$2;
        """

        try:
            status = await ctx.db.execute(query, new_alias, original_tag.lower(), ctx.guild.id, ctx.author.id)
        except asyncpg.UniqueViolationError:
            await ctx.send('<:redTick:1079249771975413910> This alias is already taken.')
        else:
            if status[-1] == '0':
                await ctx.send(
                    f'<:redTick:1079249771975413910> A tag with the name **{original_tag}** does not exist.')
            else:
                await ctx.send(
                    f'<:greenTick:1079249732364406854> Tag alias **{new_alias}** that redirects to **{original_tag}** successfully created.')

    @commands_ext.command(
        tag.command,
        name="create",
        description="Creates a new tag in the server.",
        aliases=["add"],
        examples=["new-tag This is the content of the tag.",
                  "\"new tag\" This is the content of the tag."]
    )
    @commands.guild_only()
    @app_commands.describe(name='The tag name', content='The tag content')
    async def tag_create(
            self,
            ctx: GuildContext,
            name: Annotated[str, TagNameOrID],
            *,
            content: Annotated[str, TagContent]
    ):
        """Creates a new Tag owned by yourself in this server.
        The tag name must be between 1 and 100 characters long.
        The tag content must be less than 2000 characters long.
        `Note:` You can create aliases for Tags using `tags alias <alias-name> <original-name>`
        """
        with self.reserve_tag(ctx.guild.id, name):
            await self.create_tag(ctx, name, content)

    @commands_ext.command(
        tag.command,
        name="make",
        description="Interactively create a Tag owned by yourself in this server.",
        ignore_extra=True
    )
    @commands.guild_only()
    async def tag_make(self, ctx: GuildContext):
        """Interactively create a Tag owned by yourself in this server.
        `Note:` May be useful for larger contents / bigger names.
        """

        if ctx.interaction is not None:
            modal = TagMakeModal(self, ctx)
            await ctx.interaction.response.send_modal(modal)
            return

        messages: List[discord.Message] = [ctx.message]

        converter = TagNameOrID()
        original = ctx.message

        messages.append(await ctx.send("What would you like the tag's **name** to be?"))

        def check(msg: discord.Message):  # noqa
            return msg.author == ctx.author and ctx.channel == msg.channel

        try:
            name = await self.bot.wait_for('message', timeout=60.0, check=check)
        except asyncio.TimeoutError:
            return

        try:
            ctx.message = name
            name = await converter.convert(ctx, name.content)
        except commands.BadArgument:
            raise
        finally:
            ctx.message = original

        tag = self.get_tag(name_or_id=name, location_id=ctx.guild.id, only_parent=True)
        if tag is not None:
            return await ctx.send(
                '<:redTick:1079249771975413910> Sorry. This name is already taken. Please choose another one.'
            )

        with self.reserve_tag(ctx.guild.id, name):
            messages.append(await ctx.send(
                f'The new Tags name is **{name}**.\n'
                f'Please enter now a content for the tag.\n'
                f'You can type "`{ctx.prefix}abort`" to abort the tag make process.'
            ))

            try:
                msg = await self.bot.wait_for('message', check=check, timeout=100.0)
            except asyncio.TimeoutError:
                return

            if msg.content == f'{ctx.prefix}abort':
                return
            else:
                clean_content = await TagContent().convert(ctx, msg.content)

            if msg.attachments:
                clean_content = f'{clean_content}\n{msg.attachments[0].url}'

            await self.create_tag(ctx, name, clean_content)

        try:
            await ctx.channel.delete_messages(messages)
        except discord.HTTPException:
            pass

    async def guild_tag_stats(self, ctx: GuildContext):
        e = discord.Embed(colour=self.bot.colour.darker_red(), title=f'Tag Statistics for {ctx.guild.name}')
        e.set_thumbnail(url=ctx.guild.icon.url)
        e.set_footer(text='Tag Statistics for this Server.')

        query = "SELECT COUNT(*) as total_tags FROM tags WHERE location_id=$1;"

        total_tags = await self.bot.pool.fetchval(query, ctx.guild.id)
        if not total_tags:
            e.description = '*There are no statistics available.*'
        else:
            query = "SELECT COUNT(*) FROM commands WHERE guild_id=$1 AND command='tag';"
            total_uses = await self.bot.pool.fetchval(query, ctx.guild.id)

            e.add_field(name='**Guild Stats**',
                        value=f'Total Tags: **{total_tags}**\n'
                              f'Total Uses: **{total_uses}**\n\n'
                              f'*with **{usage_per_day(ctx.me.joined_at, total_uses):.2f}** tag uses per day*',
                        inline=False)

        query = """
            SELECT
                name,
                uses
            FROM tags
            WHERE location_id=$1
            ORDER BY uses DESC
            LIMIT 3;
        """

        records = await ctx.db.fetch(query, ctx.guild.id)

        value = '\n'.join(
            f'{emoji}: {name} (**{uses}** uses)'
            for (emoji, (name, uses)) in medal_emojize(records)
        )

        e.add_field(name='**Most Used Tags**', value=value, inline=False)

        query = """
            SELECT
                COUNT(*) AS tag_uses, 
                author_id
            FROM commands
            WHERE guild_id=$1 AND command='tag'
            GROUP BY author_id
            ORDER BY COUNT(*) DESC
            LIMIT 3;
        """

        records = await ctx.db.fetch(query, ctx.guild.id)

        value = '\n'.join(
            f'{emoji}: <@{author_id}> (**{uses}** times)'
            for (emoji, (uses, author_id)) in medal_emojize(records)
        )
        e.add_field(name='**Top Tag Users**', value=value, inline=False)

        query = """
            SELECT
               COUNT(*) AS "tags",
               owner_id
            FROM tags
            WHERE location_id=$1
            GROUP BY owner_id
            ORDER BY COUNT(*) DESC
            LIMIT 3;
        """

        records = await ctx.db.fetch(query, ctx.guild.id)

        value = '\n'.join(
            f'{emoji}: <@{owner_id}> (**{count}** tags)'
            for (emoji, (count, owner_id)) in medal_emojize(records)
        )
        e.add_field(name='**Top Creators**', value=value, inline=False)

        await ctx.send(embed=e)

    async def member_tag_stats(self, ctx: GuildContext, member: discord.Member | discord.User):
        e = discord.Embed(color=self.bot.colour.darker_red())
        e.set_author(name=str(member), icon_url=member.display_avatar.url)
        e.set_thumbnail(url=member.avatar.url)
        e.set_footer(text='Tag Stats for this Member.')

        query = """
            SELECT
               name,
               uses,
               COUNT(*) OVER() AS "count",
               SUM(uses) OVER () AS "total_uses"
            FROM tags
            WHERE location_id=$1 AND owner_id=$2
            ORDER BY uses DESC
            LIMIT 3;
        """

        records = await ctx.db.fetch(query, ctx.guild.id, member.id)

        if len(records) > 1:
            owned = records[0]['count']
            uses = records[0]['total_uses']
        else:
            owned = 'N/A ***(Tag is claimable)***'
            uses = 0

        query = """
            SELECT COUNT(*)
            FROM commands
            WHERE guild_id=$1 AND command='tag' AND author_id=$2
        """

        count: tuple[int] = await ctx.db.fetchrow(query, ctx.guild.id, member.id)  # type: ignore

        e.add_field(name='**Tag Command Uses**', value=f"**{count[0]}** times", inline=False)
        e.add_field(name='**Owned Tags**', value=owned)
        e.add_field(name='**Owned Tags Used**', value=uses)

        for index, (emoji, (name, uses, _, _)) in enumerate(medal_emojize(records), 1):
            e.add_field(name=f'**#{index} {emoji}**', value=f'**{name}** (**{uses}** uses)', inline=False)

        await ctx.send(embed=e)

    @staticmethod
    async def send_tags_to_text(ctx: GuildContext, tags: list[asyncpg.Record]):
        table = formats.TabularData()
        table.set_columns(list(tags[0].keys()))
        table.add_rows(list(r.values()) for r in tags)
        fp = io.BytesIO(table.render().encode('utf-8'))
        await ctx.send(file=discord.File(fp, 'tags.txt'))

    @commands_ext.command(
        tag.command,
        name='stats',
        description='Shows Tag Statistics about the Server or a Member.',
    )
    @commands.guild_only()
    @app_commands.describe(
        member='The member to get tag statistics for. If not given, the server\'s tag statistics will be shown.')
    async def tag_stats(self, ctx: GuildContext, *, member: discord.User = None):
        """Shows Tag Statistics about the Server or a Member."""
        if member is None:
            await self.guild_tag_stats(ctx)
        else:
            await self.member_tag_stats(ctx, member)

    @commands_ext.command(
        tag.command,
        name='edit',
        description='Edit the content or name of a Tag.',
    )
    @commands.guild_only()
    @app_commands.describe(
        name_or_id='The Tag you want to edit. (Must be yours)',
        content='The new content of the tag. (If not given, you will be prompted to edit the tag in a modal.)',
    )
    @app_commands.rename(name_or_id="name-or-id")
    @app_commands.autocomplete(name_or_id=owned_non_aliased_tag_autocomplete)  # type: ignore
    async def tag_edit(
            self,
            ctx: GuildContext,
            name_or_id: Annotated[Union[str, int], TagNameOrID(lower=True, with_id=True)],  # type: ignore
            use_embed: Optional[bool] = None,
            *,
            content: Annotated[Optional[str], TagContent(required=False)] = None,  # type: ignore
    ):
        """Edit the content or name of a Tag.
        `Note:` If you don't pass a content, you will be prompted to edit the tag in a modal.
        This may be useful for larger contents."""

        if ctx.interaction:
            await ctx.defer()
        else:
            await ctx.channel.typing()

        tag = await self.get_tag(name_or_id=name_or_id, location_id=ctx.guild.id, owner_id=ctx.author.id)

        if content is None and use_embed is None:
            if ctx.interaction is None:
                raise commands.BadArgument('<:redTick:1079249771975413910> Missing content to edit tag with')
            else:
                if tag is None:
                    await ctx.send(
                        '<:redTick:1079249771975413910> Could not find a tag with that name, are you sure it exists or you own it?',
                        ephemeral=True
                    )
                    return
                modal = TagEditModal(tag)
                await ctx.interaction.response.send_modal(modal)
                await modal.wait()
                ctx.interaction = modal.interaction
                content = modal.text

        if content:
            if len(content) > 2000:
                return await ctx.send('<:redTick:1079249771975413910> Tag content can only be up to 2000 characters')

        await tag.edit(use_embed=use_embed, content=content)
        await ctx.send('<:greenTick:1079249732364406854> Successfully edited tag.')
        # Here we don't need to invalidate the cache because it's automatically done in the `send_tag` method.
        await self.send_tag(ctx, name_or_id)

    @commands_ext.command(
        tag.command,
        name='delete',
        description='Removes a Tag by Name or ID.',
        aliases=['remove']
    )
    @commands.guild_only()
    @app_commands.describe(name_or_id='The assigned Tag to delete.')
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=owned_non_aliased_tag_autocomplete)  # type: ignore
    async def tag_delete(
            self,
            ctx: GuildContext,
            *,
            name_or_id: Annotated[Union[str, int], TagNameOrID(lower=True, with_id=True)],  # type: ignore
    ):
        """Removes a Tag by ID owned by yourself.
        Your Tags can also be removed by Moderators if they have the `MANAGE MESSAGES` permission.
        `Note:` This will also remove all aliases of the tag.
        """

        bypass_owner_check = ctx.author.id == self.bot.owner_id or ctx.author.guild_permissions.manage_messages

        if bypass_owner_check:
            tag = await self.get_tag(name_or_id=name_or_id, location_id=ctx.guild.id, only_parent=True)
        else:
            tag = await self.get_tag(name_or_id=name_or_id, location_id=ctx.guild.id, owner_id=ctx.author.id,
                                     only_parent=True)

        if not tag:
            raise commands.BadArgument(
                '<:redTick:1079249771975413910> Could not find a tag with that name, are you sure it exists or you own it?')

        await tag.delete()

        self.get_tag.invalidate_containing(tag.name)
        self.get_tag.invalidate_containing(str(tag.id))

    @commands_ext.command(
        tag.command,
        name='info',
        description='Shows you Information about a Tag.',
    )
    @commands.guild_only()
    @app_commands.describe(name_or_id='The name or id of the tag to get info about.')
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=aliased_tag_autocomplete)  # type: ignore
    async def tag_info(
            self,
            ctx: GuildContext,
            *,
            name_or_id: Annotated[Union[str, int], TagNameOrID(lower=True, with_id=True)],  # type: ignore
    ):
        """Shows you Information about a Tag."""

        tag = await self.get_tag(name_or_id=name_or_id, location_id=ctx.guild.id)

        if tag is None:
            return await ctx.send('<:redTick:1079249771975413910> Tag was not found.')

        embed = discord.Embed(title="Tag Info", description=f"**```{tag.name}```**\n")
        embed.add_field(name="**Owner**", value=f"<@{tag.owner_id}>")

        user = self.bot.get_user(tag.owner_id) or (await self.bot.fetch_user(tag.owner_id))
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)

        embed.timestamp = tag.created_at.replace(tzinfo=datetime.timezone.utc)
        embed.set_footer(text=f'[{tag.id}] • Tag created at')

        rank = await tag.get_rank()
        if rank:
            text = '**Rank**'
            if rank in (1, 2, 3):
                text += f' {chr(129350 + int(rank))}'

            embed.add_field(name=text, value=f"**#{rank}**")

        embed.add_field(name='**Tag Used**', value=tag.uses)

        if tag.aliases:
            value = []
            for alias in tag.aliases:
                value.append(
                    f'**{alias.name}** [`{alias.id}`] ({discord.utils.format_dt(alias.created_at, style="D")})')

            embed.add_field(name=f"**Aliases ({len(tag.aliases)})**", value='\n'.join(value), inline=False)

        await ctx.send(embed=embed)

    @commands_ext.command(
        tag.command,
        name='raw',
        description='This displays you the raw content of a tag.',
        aliases=['content']
    )
    @commands.guild_only()
    @app_commands.describe(name_or_id='The name or id of the tag to display the escaped markdown content.')
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=non_aliased_tag_autocomplete)  # type: ignore
    async def tag_raw(
            self,
            ctx: GuildContext,
            *,
            name_or_id: Annotated[Union[str, int], TagNameOrID(lower=True, with_id=True)],  # type: ignore
    ):
        """This displays you the raw content of a tag."""
        await self.send_tag(ctx, name_or_id, escape_markdown=True)

    @commands_ext.command(
        tag.command,
        name='list',
        description='Shows a list of Tags owned by yourself or a given member.',
    )
    @commands.guild_only()
    @app_commands.describe(member='The member to list tags of, if not given then it defaults to you.')
    async def tag_list(self, ctx: GuildContext, *, flags: TagListFlags):
        """Shows a list of Tags owned by yourself or a given member."""
        member = flags.member or ctx.author

        SORT = {
            "id": "id",
            "newest": "created_at DESC",
            "oldest": "created_at ASC",
            "name": "name DESC"
        }.get(flags.sort, "name DESC")

        if not flags.query:
            query = f"""
                SELECT name, id
                FROM tag_lookup
                WHERE location_id=$1 AND owner_id=$2
                ORDER BY {SORT};
            """
            values = (ctx.guild.id, member.id)
        else:
            if flags.sort == "name":
                SORT = "similarity(name, $2) DESC"

            query = f"""
                SELECT name, id
                FROM tag_lookup
                WHERE location_id=$1 AND name % $2 AND owner_id=$3
                ORDER BY {SORT};
            """
            values = (ctx.guild.id, flags.query, member.id)

        rows = await ctx.db.fetch(query, *values)

        if rows:
            if flags.to_text:
                await self.send_tags_to_text(ctx, rows)
            else:
                embed = discord.Embed(title="Tag Search",
                                      description=f"**{member}'s** Tags in {ctx.guild.name}",
                                      colour=helpers.Colour.darker_red(),
                                      timestamp=discord.utils.utcnow())
                embed.set_footer(text=f"{plural(len(rows)):entry|entries}")

                results = [f"`{index}.` {entry}" for index, entry in
                           enumerate([TagPageEntry(record=row) for row in rows], 1)]
                await LinePaginator.start(
                    ctx, entries=results, search_for=True, per_page=20, embed=embed
                )
        else:
            await ctx.send(f'<:redTick:1079249771975413910> **{member}** currently has no tags.')

    @commands_ext.command(
        tag.command,
        name='purge',
        description='Bulk remove all Tags and assigned Aliases of a given User.',
    )
    @commands.guild_only()
    @commands_ext.command_permissions(user=['manage_messages'])
    @app_commands.describe(member='The member to remove all tags of')
    async def tag_purge(self, ctx: GuildContext, member: discord.User):
        """Bulk remove all Tags and assigned Aliases of a given User."""

        query = "SELECT COUNT(*) FROM tags WHERE location_id=$1 AND owner_id=$2;"
        row: tuple[int] = await ctx.db.fetchrow(query, ctx.guild.id, member.id)  # type: ignore
        count = row[0]

        if count == 0:
            return await ctx.send(f'<:redTick:1079249771975413910> **{member}** does not have any tags to purge.')

        confirm = await ctx.prompt(
            f'<:warning:1113421726861238363> This will delete **{count}** tags are you sure? **This action cannot be reversed**.')
        if not confirm:
            return await ctx.send('<:redTick:1079249771975413910> Cancelling tag purge request.')

        query = "DELETE FROM tags WHERE location_id=$1 AND owner_id=$2;"
        await ctx.db.execute(query, ctx.guild.id, member.id)

        await ctx.send(
            f'<:greenTick:1079249732364406854> Successfully removed all **{count}** tags that belong to **{member}**.')

    @commands_ext.command(
        tag.command,
        name='search',
        description='Search for tags matching the given query.',
    )
    @commands.guild_only()
    @app_commands.describe(query='The tag name to search for')
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
            ctx: GuildContext,
            *,
            flags: TagSearchFlags
    ):
        """Search for tags matching the given query.
        `Note:` To use autocomplete, you have to at least provide three characters.
        """

        SORT = {
            "id": "id",
            "newest": "created_at DESC",
            "oldest": "created_at ASC",
            "name": "name DESC"
        }.get(flags.sort, "name DESC")

        if not flags.query:
            query = f"""
                SELECT name, id
                FROM tag_lookup
                WHERE location_id=$1
                ORDER BY {SORT};
            """
            values = (ctx.guild.id,)
        else:
            if flags.sort == "name":
                SORT = "similarity(name, $2) DESC"

            query = f"""
                SELECT name, id
                FROM tag_lookup
                WHERE location_id=$1 AND name % $2
                ORDER BY {SORT};
            """
            values = (ctx.guild.id, flags.query)

        rows = await ctx.db.fetch(query, *values)

        if rows:
            if flags.to_text:
                await self.send_tags_to_text(ctx, rows)
            else:
                embed = discord.Embed(title="Tag Search",
                                      description=f"Sorted by: **{flags.sort}**",
                                      colour=helpers.Colour.darker_red(),
                                      timestamp=discord.utils.utcnow())
                embed.set_footer(text=f"{plural(len(rows)):entry|entries}")

                results = [f"`{index}.` {entry}" for index, entry in
                           enumerate([TagPageEntry(record=row) for row in rows], 1)]
                await LinePaginator.start(
                    ctx, entries=results, search_for=True, per_page=20, embed=embed
                )
        else:
            await ctx.send('<:redTick:1079249771975413910> No tags found.')

    @commands_ext.command(
        tag.command,
        name='claim',
        description='Claim a tag by yourself if the User is not in this server anymore or the tag has no owner.',
    )
    @commands.guild_only()
    @app_commands.describe(name_or_id='The tag to claim')
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=aliased_tag_autocomplete)  # type: ignore
    async def tag_claim(
            self,
            ctx: GuildContext,
            *,
            name_or_id: Annotated[Union[str, int], TagNameOrID(lower=True, with_id=True)],  # type: ignore
    ):
        """Claim a tag by yourself if the User is not in this server anymore or the tag has no owner."""
        tag = await self.get_tag(ctx, name_or_id, location_id=ctx.guild.id, actual=True)

        member = await self.bot.get_or_fetch_member(ctx.guild, tag.owner_id)
        if member is not None:
            return await ctx.send('<:redTick:1079249771975413910> Tag owner is still in server.')

        if isinstance(tag, AliasTag):
            await tag.transfer(ctx.author, connection=self.bot.pool)  # type: ignore
        else:
            await tag.transfer(ctx.author, only_parent=True)

        await ctx.send('<:greenTick:1079249732364406854> Successfully transferred tag ownership to you.')

    @commands_ext.command(
        tag.command,
        name='transfer',
        description='Transfer a tag owned by you to another member.',
    )
    @commands.guild_only()
    @app_commands.describe(member='The member to transfer the tag to', name_or_id='The tag to transfer')
    @app_commands.rename(name_or_id='name-or-id')
    @app_commands.autocomplete(name_or_id=aliased_tag_autocomplete)  # type: ignore
    async def tag_transfer(
            self,
            ctx: GuildContext,
            member: discord.Member,
            *,
            name_or_id: Annotated[str, TagNameOrID(with_id=True)]  # type: ignore
    ):
        """Transfer a tag owned by you to another member."""
        if member.bot:
            return await ctx.send('<:redTick:1079249771975413910> You cannot transfer a tag to a bot.')

        tag = await self.get_tag(name_or_id=name_or_id, location_id=ctx.guild.id, owner_id=ctx.author.id,
                                 only_parent=True)
        if tag is None:
            return await ctx.send(
                f'<:redTick:1079249771975413910> A tag "**{name_or_id}**" does not exist or is not owned by you.')

        await tag.transfer(member)
        await ctx.send(f'<:greenTick:1079249732364406854> Successfully transferred tag ownership to **{member}**.')

    @commands_ext.command(tag.command, name='export', description="Exports all your tags/server tags to a csv file.")
    @commands.cooldown(1, 60, commands.BucketType.member)
    @commands.guild_only()
    @app_commands.describe(which='Whether to export server tags or personal tags. (Server tags only for server owners)')
    async def tag_export(
            self,
            ctx: GuildContext,
            which: Optional[Literal['server', 'personal']] = 'personal',
    ):
        """Exports all your tags/server tags to a csv file."""
        if which == 'server':
            if ctx.author.id != ctx.guild.owner_id:
                return await ctx.send('<:redTick:1079249771975413910> Only the server owner can export server tags.')

            query = "SELECT name, content FROM tags WHERE location_id=$1;"
            values = (ctx.guild.id,)
        else:
            query = "SELECT name, content FROM tags WHERE location_id=$1 AND owner_id=$2;"
            values = (ctx.guild.id, ctx.author.id)

        async with ctx.channel.typing():
            async with ctx.db.acquire() as conn:
                async with conn.transaction():
                    records = await conn.fetch(query, *values)

        if not records:
            return await ctx.send('<:redTick:1079249771975413910> No tags were found.')

        buffer = io.StringIO()
        writer = csv.writer(buffer, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        for record in records:
            writer.writerow([record[0], record[1]])
        buffer.seek(0)

        file = discord.File(
            fp=buffer,  # type: ignore
            filename=f'{ctx.author.id}_tags.csv' if which == 'personal' else f'{ctx.guild.id}_tags.csv'
        )
        await ctx.send(file=file)


async def setup(bot: Percy):
    await bot.add_cog(Tags(bot))
