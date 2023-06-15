from __future__ import annotations

import asyncio
import csv
import datetime
import io
from typing import TYPE_CHECKING, Optional, List, Literal

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands
from typing_extensions import Annotated

from cogs.utils.paginator import LinePaginator
from . import command, command_permissions
from .emoji import usage_per_day
from .utils import formats, fuzzy, helpers
from .utils.formats import plural, medal_emojize
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


class TagName(commands.clean_content):
    def __init__(self, *, lower: bool = False):
        self.lower: bool = lower
        super().__init__()

    async def convert(self, ctx: Context, argument: str) -> str:
        converted = await super().convert(ctx, argument)
        lower = converted.lower().strip()

        if not lower:
            raise commands.BadArgument('<:redTick:1079249771975413910> Please enter a valid tag name.')

        if len(lower) > 100:
            raise commands.BadArgument(
                f'<:redTick:1079249771975413910> Tag names must be 100 characters or less. (You have *{len(lower)}* characters)')

        cog: Tags = ctx.bot.get_cog('Tags')  # noqa
        if cog is None:
            raise commands.BadArgument('<:redTick:1079249771975413910> Tags are currently unavailable.')

        if cog.is_tag_reserved(ctx.guild.id, argument):
            raise commands.BadArgument(
                '<:redTick:1079249771975413910> Hey, that\'s a reserved tag name. Choose another one.')

        return converted.strip() if not self.lower else lower


class TagSearchFlags(commands.FlagConverter, prefix='--', delimiter=' '):
    query: Optional[str] = commands.flag(description="The query to search for", aliases=['q'], default=None)
    sort: Literal['name', 'newest', 'oldest', 'id'] = commands.flag(
        description="The key to sort the results.", aliases=['s'], default='name')


class TagEditModal(discord.ui.Modal, title='Edit Tag'):
    content = discord.ui.TextInput(
        label='New Content', required=True, style=discord.TextStyle.long, min_length=1, max_length=2000
    )

    def __init__(self, text: str) -> None:
        super().__init__()
        self.content.default = text

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
            name = await TagName().convert(self.ctx, name)
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
            await self.cog.create_tag(self.ctx, name, content)


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

    async def get_possible_tag(
            self,
            guild: discord.abc.Snowflake,
            argument: str,
            *,
            connection: Optional[asyncpg.Connection | asyncpg.Pool] = None,
    ) -> Optional[asyncpg.Record]:
        """Returns a possible Tag that can be executed in the Guild."""

        con = connection or self.bot.pool

        query = """
            SELECT
                tags.name, 
                tags.content
            FROM tag_lookup
            INNER JOIN tags ON tags.id = tag_lookup.tag_id
            WHERE tag_lookup.location_id=$1
        """

        if argument.isdigit():
            query += " AND tag_lookup.tag_id=$2;"
        else:
            query += " AND LOWER(tag_lookup.name)=$2;"
        return await con.fetchrow(query, guild.id, argument)

    async def send_tag(
            self,
            ctx: GuildContext,
            name: str,
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
        name: str
            The name of the Tag to look up.
        escape_markdown: bool
            Whether to escape the markdown in the Tag content.
        """
        query = """
            SELECT
                tags.name, 
                tags.content
            FROM tag_lookup
            INNER JOIN tags ON tags.id = tag_lookup.tag_id
            WHERE tag_lookup.location_id=$1 AND LOWER(tag_lookup.name)=$2;
        """
        row = await self.bot.pool.fetchrow(query, ctx.guild.id, name)

        if row is None:  # If we didn't find a Tag with the exact name, we try to find the three most similar ones
            query = """
                SELECT
                    tag_lookup.name, tag_lookup.id
                FROM tag_lookup
                WHERE tag_lookup.location_id=$1 AND tag_lookup.name % $2
                ORDER BY similarity(tag_lookup.name, $2) DESC
                LIMIT 3;
            """
            rows = await self.bot.pool.fetch(query, ctx.guild.id, name)

            if rows is None or len(rows) == 0:
                await ctx.send(ctx.tick(False, 'No Tag with this name or similar name found.'))
            else:
                names = '\n'.join(f"* **{r['name']}** [`{r['id']}`]" for r in rows)
                embed = discord.Embed(title="Tag not found", description=f"Found Tags with similar name.",
                                      colour=self.bot.colour.darker_red())
                embed.add_field(name="Similar Tags", value=names)
                await ctx.send(embed=embed)
            return

        assert row is not None  # for mypy

        if escape_markdown:
            first_step = discord.utils.escape_markdown(row['content'])
            await ctx.safe_send(first_step.replace('<', '\\<'), escape_mentions=False)
        else:
            await ctx.send(row['content'], reference=ctx.replied_reference)

        # Just updated the uses of the Tag
        query = "UPDATE tags SET uses = uses + 1 WHERE name = $1 AND location_id=$2;"
        await ctx.db.execute(query, row['name'], ctx.guild.id)

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
                match e:
                    case asyncpg.UniqueViolationError():
                        await tr.rollback()
                        await ctx.send('<:redTick:1079249771975413910> A Tag with this name already exists.')
                    case _:
                        await tr.rollback()
                        await ctx.send(
                            '<:redTick:1079249771975413910> Tag could not be created due to an Unknown reason. Try again later?')
            else:
                await tr.commit()
                await ctx.send(f'<:greenTick:1079249732364406854> Tag `{name}` was successfully created.')

    def is_tag_reserved(self, guild_id: int, name: str) -> bool:
        """Helper method to check if a Tag with ``name`` is currently being made."""
        def in_prod_check() -> bool:
            try:
                being_made = self._temporary_reserved_tags[guild_id]
            except KeyError:
                return False
            else:
                return name.lower() in being_made

        first_word, _, _ = name.partition(' ')

        root: commands.GroupMixin = self.bot.get_command('tags')   # type: ignore
        if first_word in root.all_commands:
            return False
        else:
            return in_prod_check()

    def add_in_progress_tag(self, guild_id: int, name: str) -> None:
        tags = self._temporary_reserved_tags.setdefault(guild_id, set())
        tags.add(name.lower())

    def remove_in_progress_tag(self, guild_id: int, name: str) -> None:
        try:
            being_made = self._temporary_reserved_tags[guild_id]
        except KeyError:
            return

        being_made.discard(name.lower())
        if len(being_made) == 0:
            del self._temporary_reserved_tags[guild_id]

    async def non_aliased_tag_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        query = "SELECT name, id FROM tags WHERE location_id=$1 LIMIT 20;"
        results: list[asyncpg.Record] = await self.bot.pool.fetch(query, interaction.guild_id)
        results = fuzzy.finder(current, results, key=lambda a: a['id'] if current.isdigit() else a['name'])
        return [app_commands.Choice(name=f"[{a['id']}] {a['name']}", value=a['name']) for a in results]

    async def aliased_tag_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        query = "SELECT name FROM tag_lookup WHERE location_id=$1 LIMIT 20;"
        results: list[asyncpg.Record] = await self.bot.pool.fetch(query, interaction.guild_id)
        results = fuzzy.finder(current, results, key=lambda a: a['name'])
        return [app_commands.Choice(name=a['name'], value=a['name']) for a in results]

    async def owned_non_aliased_tag_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        query = "SELECT name, id FROM tags WHERE location_id=$1 AND owner_id=$2 LIMIT 20;"
        results: list[asyncpg.Record] = await self.bot.pool.fetch(query, interaction.guild_id, interaction.user.id)
        results = fuzzy.finder(current, results, key=lambda a: a['id'] if current.isdigit() else a['name'])
        return [app_commands.Choice(name=f"[{a['id']}] {a['name']}", value=a['name']) for a in results]

    async def owned_aliased_tag_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        query = "SELECT name FROM tag_lookup WHERE location_id=$1 AND owner_id=$2 LIMIT 20;"
        results: list[asyncpg.Record] = await self.bot.pool.fetch(query, interaction.guild_id, interaction.user.id)
        results = fuzzy.finder(current, results, key=lambda a: a['name'])
        return [app_commands.Choice(name=a['name'], value=a['name']) for a in results]

    @command(
        commands.hybrid_group,
        name="tags",
        description="Commands for managing tags.",
        invoke_without_command=True,
        fallback="view"
    )
    @commands.guild_only()
    @app_commands.guild_only()
    @app_commands.describe(name='The tag to retrieve')
    @app_commands.autocomplete(name=aliased_tag_autocomplete)  # type: ignore
    async def tags(self, ctx: GuildContext, *, name: Annotated[str, TagName(lower=True)]):  # type: ignore
        """Retrieves a tag from the server.
        If the tag is an alias, the original tag will be retrieved instead.
        """
        await self.send_tag(ctx, name)

    @command(
        tags.command,
        name="alias",
        description="Creates a new alias for an existing tag.",
        examples=["new-alias original-tag",
                  "\"new alias\" original tag"]
    )
    @commands.guild_only()
    @app_commands.rename(new_alias='new-alias', original_tag='original-tag')
    @app_commands.describe(new_alias='The new alias to set', original_tag='The original tag to alias')
    @app_commands.autocomplete(original_tag=non_aliased_tag_autocomplete)  # type: ignore
    async def tags_alias(
            self,
            ctx: GuildContext,
            new_alias: Annotated[str, TagName],
            *,
            original_tag: Annotated[str, TagName]
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
                    f'<:redTick:1079249771975413910> A tag with the name **{original_tag!r}** does not exist.')
            else:
                await ctx.send(
                    f'<:greenTick:1079249732364406854> Tag alias **{new_alias!r}** that redirects to **{original_tag!r}** successfully created.')

    @command(
        tags.command,
        name="create",
        description="Creates a new tag in the server.",
        aliases=["add"],
        examples=["new-tag This is the content of the tag.",
                  "\"new tag\" This is the content of the tag."]
    )
    @commands.guild_only()
    @app_commands.describe(name='The tag name', content='The tag content')
    async def tags_create(
            self,
            ctx: GuildContext,
            name: Annotated[str, TagName],
            *,
            content: Annotated[str, commands.clean_content]
    ):
        """Creates a new Tag owned by yourself in this server.
        The tag name must be between 1 and 100 characters long.
        The tag content must be less than 2000 characters long.
        `Note:` You can create aliases for Tags using `tags alias <alias-name> <original-name>`
        """

        if self.is_tag_reserved(ctx.guild.id, name):
            return await ctx.send(
                '<:redTick:1079249771975413910> This tag name is reserved or currently being made.')

        if len(content) > 2000:
            return await ctx.send('<:redTick:1079249771975413910> Tag content must be less than `2000` characters.')

        await self.create_tag(ctx, name, content)

    @command(
        tags.command,
        name="make",
        description="Interactively create a Tag owned by yourself in this server.",
        ignore_extra=True
    )
    @commands.guild_only()
    async def tags_make(self, ctx: GuildContext):
        """Interactively create a Tag owned by yourself in this server.
        `Note:` May be useful for larger contents / bigger names.
        """

        if ctx.interaction is not None:
            modal = TagMakeModal(self, ctx)
            await ctx.interaction.response.send_modal(modal)
            return

        messages: List[discord.Message] = [ctx.message]

        converter = TagName()
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
        except commands.BadArgument as e:
            return await ctx.send(
                f'<:redTick:1079249771975413910> {e}.\nRedo the command "`{ctx.prefix}tag make`" to retry.')
        finally:
            ctx.message = original

        if self.is_tag_reserved(ctx.guild.id, name):
            return await ctx.send(
                '<:redTick:1079249771975413910> This tag name is reserved or currently being made..'
            )

        query = "SELECT 1 FROM tags WHERE location_id=$1 AND LOWER(name)=$2;"
        row = await ctx.db.fetchrow(query, ctx.guild.id, name.lower())
        if row is not None:
            return await ctx.send(
                '<:redTick:1079249771975413910> Sorry. This name is already taken. Please choose another one.'
            )

        self.add_in_progress_tag(ctx.guild.id, name)

        messages.append(await ctx.send(
            f'The new Tags name is **{name}**.\n'
            f'Please enter now a content for the tag.\n'
            f'You can type "`{ctx.prefix}abort`" to abort the tag make process.\n'
            f'**Timeout ETA:** {discord.utils.format_dt(datetime.datetime.utcnow() + datetime.timedelta(seconds=60), "R")}.'
        ))

        try:
            msg = await self.bot.wait_for('message', check=check, timeout=300.0)
        except asyncio.TimeoutError:
            self.remove_in_progress_tag(ctx.guild.id, name)
            return

        if msg.content == f'{ctx.prefix}abort':
            self.remove_in_progress_tag(ctx.guild.id, name)
            return
        elif msg.content:
            clean_content = await commands.clean_content().convert(ctx, msg.content)
        else:
            clean_content = msg.content

        if msg.attachments:
            clean_content = f'{clean_content}\n{msg.attachments[0].url}'

        if len(clean_content) > 2000:
            return await ctx.send(f'{ctx.tick(None)} Tag content is a maximum of **2000** characters.')

        try:
            await self.create_tag(ctx, name, clean_content)
        finally:
            self.remove_in_progress_tag(ctx.guild.id, name)

        for message in messages:
            try:
                await message.delete()
            except discord.HTTPException:
                pass

    async def guild_tag_stats(self, ctx: GuildContext):
        e = discord.Embed(colour=self.bot.colour.darker_red(), title=f'Tag Statistics for {ctx.guild.name}')
        e.set_thumbnail(url=ctx.guild.icon.url)
        e.set_footer(text='Tag Statistics for this Server.')

        query = """
            SELECT
                name,
                uses,
                COUNT(*) OVER () AS "count",
                SUM(uses) OVER () AS "total_uses"
            FROM tags
            WHERE location_id=$1
            ORDER BY uses DESC
            LIMIT 3;
        """

        records = await ctx.db.fetch(query, ctx.guild.id)
        if not records:
            e.description = '*There are no statistics available.*'
        else:
            total = records[0]
            e.add_field(name='**STATS**',
                        value=f'Total Tags: **{total["count"]}**\n'
                              f'Total Uses: **{total["total_uses"]}**\n\n'
                              f'with **{usage_per_day(ctx.me.joined_at, total["total_uses"]):.2f}** tag uses per day',
                        inline=False)

        value = '\n'.join(
            f'{emoji}: {name} (**{uses}** uses)'
            for (emoji, (name, uses, _, _)) in medal_emojize(records)
        )

        e.add_field(name='**MOST USED TAGS**', value=value, inline=False)

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
        e.add_field(name='**TOP TAG USERS**', value=value, inline=False)

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
        e.add_field(name='**TOP CREATORS**', value=value, inline=False)

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

        e.add_field(name='**TAG COMMAND USAGE**', value=f"**{count[0]}** times", inline=False)
        e.add_field(name='**TAGS OWNED**', value=owned)
        e.add_field(name='**OWNED TAGS USES**', value=uses)

        for (emoji, (name, uses, _, _)) in medal_emojize(records):
            e.add_field(name=f'**{emoji} Owned Tag**', value=f'**{name}** (**{uses}** uses)', inline=False)

        await ctx.send(embed=e)

    @command(
        tags.command,
        name='stats',
        description='Shows Tag Statistics about the Server or a Member.',
    )
    @commands.guild_only()
    @app_commands.describe(
        member='The member to get tag statistics for. If not given, the server\'s tag statistics will be shown.')
    async def tags_stats(self, ctx: GuildContext, *, member: discord.User = None):
        """Shows Tag Statistics about the Server or a Member."""
        if member is None:
            await self.guild_tag_stats(ctx)
        else:
            await self.member_tag_stats(ctx, member)

    @command(
        tags.command,
        name='edit',
        description='Edit the content or name of a Tag.',
    )
    @commands.guild_only()
    @app_commands.describe(
        name='The Tag you want to edit. (Must be yours)',
        content='The new content of the tag. (If not given, you will be prompted to edit the tag in a modal.)',
    )
    @app_commands.autocomplete(name=owned_non_aliased_tag_autocomplete)  # type: ignore
    async def tags_edit(
            self,
            ctx: GuildContext,
            name: Annotated[str, TagName(lower=True)],  # type: ignore
            *,
            content: Annotated[Optional[str], commands.clean_content] = None,
    ):
        """Edit the content or name of a Tag.
        `Note:` If you don't pass a content, you will be prompted to edit the tag in a modal.
        This may be useful for larger contents."""

        if content is None:
            if ctx.interaction is None:
                raise commands.BadArgument('<:redTick:1079249771975413910> Missing content to edit tag with')
            else:
                query = "SELECT content FROM tags WHERE LOWER(name)=$1 AND location_id=$2 AND owner_id=$3;"
                row: Optional[tuple[str]] = await ctx.db.fetchrow(query, name, ctx.guild.id, ctx.author.id)
                if row is None:
                    await ctx.send(
                        '<:redTick:1079249771975413910> Could not find a tag with that name, are you sure it exists or you own it?',
                        ephemeral=True
                    )
                    return
                modal = TagEditModal(row[0])
                await ctx.interaction.response.send_modal(modal)
                await modal.wait()
                ctx.interaction = modal.interaction
                content = modal.text

        if len(content) > 2000:
            return await ctx.send('<:redTick:1079249771975413910> Tag content can only be up to 2000 characters')

        query = "UPDATE tags SET content=$1 WHERE LOWER(name)=$2 AND location_id=$3 AND owner_id=$4;"
        status = await ctx.db.execute(query, content, name, ctx.guild.id, ctx.author.id)

        if status[-1] == '0':
            await ctx.send(
                '<:redTick:1079249771975413910> Could not edit that tag. Are you sure it exists and you own it?')
        else:
            await ctx.send('<:greenTick:1079249732364406854> Successfully edited tag.')
            await ctx.send(content, ephemeral=True)

    @command(
        tags.command,
        name='delete',
        description='Removes a Tag by Name. (Must be yours, or you must have the `MANAGE MESSAGES` permission.)',
        aliases=['remove']
    )
    @commands.guild_only()
    @app_commands.describe(name='The assigned Tag Name to delete.')
    @app_commands.autocomplete(name=owned_non_aliased_tag_autocomplete)  # type: ignore
    async def tags_delete(self, ctx: GuildContext, name: Annotated[str, TagName(lower=True)]):  # type: ignore
        """Removes a Tag by ID owned by yourself.
        Your Tags can also be removed by Moderators if they have the `MANAGE MESSAGES` permission.
        `Note:` This will also remove all aliases of the tag.
        """

        bypass_owner_check = ctx.author.id == self.bot.owner_id or ctx.author.guild_permissions.manage_messages
        clause = 'LOWER(name)=$1 AND location_id=$2'

        if bypass_owner_check:
            args = [name.lower(), ctx.guild.id]
        else:
            args = [name.lower(), ctx.guild.id, ctx.author.id]
            clause = f'{clause} AND owner_id=$3'

        query = f'DELETE FROM tag_lookup WHERE {clause} RETURNING tag_id;'
        deleted = await ctx.db.fetchrow(query, *args)

        if deleted is None:
            await ctx.send(
                '<:redTick:1079249771975413910> Could not delete tag. Either it does not exist or you do not have permissions to do so.')
            return

        if bypass_owner_check:
            clause = 'id=$1 AND location_id=$2'
            args = [deleted[0], ctx.guild.id]
        else:
            clause = 'id=$1 AND location_id=$2 AND owner_id=$3'
            args = [deleted[0], ctx.guild.id, ctx.author.id]

        query = f'DELETE FROM tags WHERE {clause};'
        status = await ctx.db.execute(query, *args)

        if status[-1] == '0':
            await ctx.send('<:greenTick:1079249732364406854> Tag alias successfully deleted.')
        else:
            await ctx.send('<:greenTick:1079249732364406854> Tag and corresponding aliases successfully deleted.')

    async def _send_alias_info(self, ctx: GuildContext, record: asyncpg.Record):
        embed = discord.Embed(color=self.bot.colour.darker_red())

        owner_id = record['lookup_owner_id']
        embed.title = "*<:discord_info:1113421814132117545> ALIAS:* " + record['lookup_name']
        embed.timestamp = record['lookup_created_at'].replace(tzinfo=datetime.timezone.utc)
        embed.set_footer(text=f'[{record["lookup_alias_id"]}] • Alias created at')

        user = self.bot.get_user(owner_id) or (await self.bot.fetch_user(owner_id))
        embed.set_thumbnail(url=user.avatar.url)
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)

        embed.add_field(name='**Owner**', value=f'<@{owner_id}>')
        embed.add_field(name='**Linked To** \N{LINK SYMBOL}', value=record['name'])
        await ctx.send(embed=embed)

    async def _send_tag_info(self, ctx: GuildContext, record: asyncpg.Record):
        embed = discord.Embed(color=self.bot.colour.darker_red())

        owner_id = record['owner_id']
        embed.title = record['name']
        embed.timestamp = record['created_at'].replace(tzinfo=datetime.timezone.utc)
        embed.set_footer(text=f'[{record["id"]}] • Tag created at')

        user = self.bot.get_user(owner_id) or (await self.bot.fetch_user(owner_id))
        embed.set_thumbnail(url=user.avatar.url)
        embed.set_author(name=str(user), icon_url=user.display_avatar.url)

        embed.add_field(name='**Owner**', value=f'<@{owner_id}>', inline=False)

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

        rank = await ctx.db.fetchrow(query, record['id'])

        if rank is not None:
            text = '**Rank**'
            if rank['rank'] in (1, 2, 3):
                text += f' {chr(129350 + int(rank["rank"]))}'

            embed.add_field(name=text, value=f"**#{rank['rank']}**")

        embed.add_field(name='**Tag Used**', value=record['uses'])

        query = """
            SELECT COUNT(*) AS count
            FROM tag_lookup
               WHERE tag_lookup.tag_id=$1 AND tag_lookup.name != $2
                AND tag_lookup.location_id=$3
        """
        alias_count = await self.bot.pool.fetchrow(query, record['id'], record["name"], ctx.guild.id)

        embed.add_field(name="**Aliases**", value=alias_count['count'])

        await ctx.send(embed=embed)

    @command(
        tags.command,
        name='info',
        description='Shows you Information about a Tag.',
    )
    @commands.guild_only()
    @app_commands.describe(name='The name of the tag to get info about.')
    @app_commands.autocomplete(name=aliased_tag_autocomplete)  # type: ignore
    async def tags_info(self, ctx: GuildContext, *, name: Annotated[str, TagName(lower=True)]):  # type: ignore
        """Shows you Information about a Tag."""

        query = """
            SELECT
               tag_lookup.name <> tags.name AS "alias",
               tag_lookup.id AS lookup_alias_id,
               tag_lookup.name AS lookup_name,
               tag_lookup.created_at AS lookup_created_at,
               tag_lookup.owner_id AS lookup_owner_id,
               tags.*
            FROM tag_lookup
            INNER JOIN tags ON tag_lookup.tag_id = tags.id
            WHERE LOWER(tag_lookup.name)=$1 AND tag_lookup.location_id=$2
        """

        record = await ctx.db.fetchrow(query, name, ctx.guild.id)
        if record is None:
            return await ctx.send('<:redTick:1079249771975413910> Tag was not found.')

        if record['alias']:
            await self._send_alias_info(ctx, record)
        else:
            await self._send_tag_info(ctx, record)

    @command(
        tags.command,
        name='raw',
        description='This displays you the raw content of a tag.',
        aliases=['content']
    )
    @commands.guild_only()
    @app_commands.describe(name='The name of the tag to display the escaped markdown content.')
    @app_commands.autocomplete(name=non_aliased_tag_autocomplete)  # type: ignore
    async def tags_raw(self, ctx: GuildContext, *, name: Annotated[str, TagName(lower=True)]):  # type: ignore
        """This displays you the raw content of a tag."""
        await self.send_tag(ctx, name, escape_markdown=True)

    @command(
        tags.command,
        name='list',
        description='Shows a list of Tags owned by yourself or a given member.',
    )
    @commands.guild_only()
    @app_commands.describe(member='The member to list tags of, if not given then it defaults to you.')
    async def tags_list(self, ctx: GuildContext, *, member: discord.User = commands.Author):
        """Shows a list of Tags owned by yourself or a given member."""
        query = """
            SELECT name, id FROM tag_lookup
            WHERE location_id=$1 AND owner_id=$2
            ORDER BY name
        """

        rows = await ctx.db.fetch(query, ctx.guild.id, member.id)

        if rows:
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

    async def _tag_all_text_mode(self, ctx: GuildContext):
        query = """
            SELECT
                tag_lookup.id,
                tag_lookup.name,
                tag_lookup.owner_id,
                tags.uses,
                $2 OR $3 = tag_lookup.owner_id AS "can_delete",
                LOWER(tag_lookup.name) <> LOWER(tags.name) AS "is_alias"
            FROM tag_lookup
            INNER JOIN tags ON tags.id = tag_lookup.tag_id
            WHERE tag_lookup.location_id=$1
            ORDER BY tags.uses DESC;
        """

        bypass_owner_check = ctx.author.id == self.bot.owner_id or ctx.author.guild_permissions.manage_messages
        rows = await ctx.db.fetch(query, ctx.guild.id, bypass_owner_check, ctx.author.id)
        if not rows:
            return await ctx.send('<:redTick:1079249771975413910> There are no tags in this server.')

        table = formats.TabularData()
        table.set_columns(list(rows[0].keys()))
        table.add_rows(list(r.values()) for r in rows)
        fp = io.BytesIO(table.render().encode('utf-8'))
        await ctx.send(file=discord.File(fp, 'tags.txt'))

    @command(
        tags.command,
        name='purge',
        description='Bulk remove all Tags and assigned Aliases of a given User.',
    )
    @commands.guild_only()
    @command_permissions(user=['manage_messages'])
    @app_commands.describe(member='The member to remove all tags of')
    async def tags_purge(self, ctx: GuildContext, member: discord.User):
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

    @command(
        tags.command,
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

    @command(
        tags.command,
        name='claim',
        description='Claim a tag by yourself if the User is not in this server anymore or the tag has no owner.',
    )
    @commands.guild_only()
    @app_commands.describe(tag='The tag to claim')
    @app_commands.autocomplete(tag=aliased_tag_autocomplete)  # type: ignore
    async def tags_claim(self, ctx: GuildContext, *, tag: Annotated[str, TagName]):
        """Claim a tag by yourself if the User is not in this server anymore or the tag has no owner."""
        alias = False
        query = "SELECT id, owner_id FROM tags WHERE location_id=$1 AND LOWER(name)=$2;"
        row = await ctx.db.fetchrow(query, ctx.guild.id, tag.lower())
        if row is None:
            alias_query = "SELECT tag_id, owner_id FROM tag_lookup WHERE location_id = $1 and LOWER(name) = $2;"
            row = await ctx.db.fetchrow(alias_query, ctx.guild.id, tag.lower())
            if row is None:
                return await ctx.send(
                    f'<:redTick:1079249771975413910> A tag with the name of "**{tag}**" does not exist.')
            alias = True

        member = await self.bot.get_or_fetch_member(ctx.guild, row[1])
        if member is not None:
            return await ctx.send('<:redTick:1079249771975413910> Tag owner is still in server.')

        async with ctx.db.acquire() as conn:
            async with conn.transaction():
                if not alias:
                    query = "UPDATE tags SET owner_id=$1 WHERE id=$2;"
                    await conn.execute(query, ctx.author.id, row[0])
                query = "UPDATE tag_lookup SET owner_id=$1 WHERE tag_id=$2;"
                await conn.execute(query, ctx.author.id, row[0])

            await ctx.send('<:greenTick:1079249732364406854> Successfully transferred tag ownership to you.')

    @command(
        tags.command,
        name='transfer',
        description='Transfer a tag owned by you to another member.',
    )
    @commands.guild_only()
    @app_commands.describe(member='The member to transfer the tag to')
    @app_commands.autocomplete(tag=aliased_tag_autocomplete)  # type: ignore
    async def tags_transfer(self, ctx: GuildContext, member: discord.Member, *, tag: Annotated[str, TagName]):
        """Transfer a tag owned by you to another member."""
        if member.bot:
            return await ctx.send('<:redTick:1079249771975413910> You cannot transfer a tag to a bot.')

        query = "SELECT id FROM tags WHERE location_id=$1 AND LOWER(name)=$2 AND owner_id=$3;"

        row = await ctx.db.fetchrow(query, ctx.guild.id, tag.lower(), ctx.author.id)
        if row is None:
            return await ctx.send(
                f'<:redTick:1079249771975413910> A tag with the name of "**{tag}**" does not exist or is not owned by you.')

        async with ctx.db.acquire() as conn:
            async with conn.transaction():
                query = "UPDATE tags SET owner_id=$1 WHERE id=$2;"
                await conn.execute(query, member.id, row[0])
                query = "UPDATE tag_lookup SET owner_id=$1 WHERE tag_id=$2;"
                await conn.execute(query, member.id, row[0])

        await ctx.send(f'<:greenTick:1079249732364406854> Successfully transferred tag ownership to **{member}**.')

    @command(tags.command, name='export', description="Exports all your tags/server tags to a csv file.")
    @commands.cooldown(1, 60, commands.BucketType.member)
    @commands.guild_only()
    @app_commands.describe(which='Whether to export server tags or personal tags. (Server tags only for server owners)')
    async def tags_export(
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
