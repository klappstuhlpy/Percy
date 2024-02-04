import asyncio
import io
from typing import Optional, Annotated, Counter
from collections import defaultdict, Counter

import asyncpg
import discord

import yarl
from discord import app_commands
from discord.ext import tasks

from bot import Percy
from .utils import commands
from .utils.context import GuildContext, Context
from .utils.converters import usage_per_day
from .utils.paginator import TextSource
from .utils.render import Render
from .utils.constants import EMOJI_REGEX, EMOJI_NAME_REGEX


def partial_emoji(argument: str, *, regex=EMOJI_REGEX) -> int:
    if argument.isdigit():
        # assume it's an emoji ID
        return int(argument)

    m = regex.match(argument)
    if m is None:
        raise commands.BadArgument('That\'s not a custom emoji...')
    return int(m.group(1))


def emoji_name(argument: str, *, regex=EMOJI_NAME_REGEX) -> str:
    m = regex.match(argument)
    if m is None:
        raise commands.BadArgument('Invalid emoji name.')
    return argument


class EmojiURL:
    def __init__(self, *, animated: bool, url: str):
        self.url: str = url
        self.animated: bool = animated

    @classmethod
    async def convert(cls, ctx: GuildContext, argument: str):
        try:
            partial = await commands.PartialEmojiConverter().convert(ctx, argument)
        except commands.BadArgument:
            try:
                url = yarl.URL(argument)
                if url.scheme not in ('http', 'https'):
                    raise RuntimeError
                path = url.path.lower()
                if not path.endswith(('.png', '.jpeg', '.jpg', '.gif')):
                    raise RuntimeError
                return cls(animated=url.path.endswith('.gif'), url=argument)
            except Exception:
                raise commands.BadArgument('Not a valid or supported emoji URL.') from None
        else:
            return cls(animated=partial.animated, url=str(partial.url))


class Emoji(commands.Cog):
    """Emoji managing related commands."""
    def __init__(self, bot):
        self.bot: Percy = bot
        self.render: Render = Render  # type: ignore

        self._emoji_data_batch: defaultdict[int, Counter[int]] = defaultdict(Counter)
        self._batch_lock = asyncio.Lock()
        self.bulk_insert.add_exception_type(asyncpg.PostgresConnectionError)
        self.bulk_insert.start()

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='bloblul', id=1112406101812592640)

    def cog_unload(self):
        self.bulk_insert.stop()

    @tasks.loop(seconds=60.0)
    async def bulk_insert(self):
        query = """
            INSERT INTO emoji_stats (guild_id, emoji_id, total)
            SELECT x.guild, x.emoji, x.added
            FROM jsonb_to_recordset($1::jsonb) AS x(guild BIGINT, emoji BIGINT, added INT)
            ON CONFLICT (guild_id, emoji_id) DO UPDATE
            SET total = emoji_stats.total + excluded.total;
        """

        async with self._batch_lock:
            transformed = [
                {'guild': guild_id, 'emoji': emoji_id, 'added': count}
                for guild_id, data in self._emoji_data_batch.items()
                for emoji_id, count in data.items()
            ]
            self._emoji_data_batch.clear()
            await self.bot.pool.execute(query, transformed)

    @staticmethod
    def find_all_emoji(message: discord.Message, *, regex=EMOJI_REGEX) -> list[str]:
        return regex.findall(message.content)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None:
            return

        if message.author.bot:
            return

        matches = EMOJI_REGEX.findall(message.content)
        if not matches:
            return

        async with self._batch_lock:
            self._emoji_data_batch[message.guild.id].update(map(int, matches))

    async def get_random_emoji(
            self,
            *,
            connection: Optional[asyncpg.Connection | asyncpg.Pool] = None,
    ) -> Optional[int]:
        """Returns a random emoji from the database."""

        con = connection or self.bot.pool
        query = """
            SELECT emoji_id
            FROM emoji_stats
            OFFSET FLOOR(RANDOM() * (
                SELECT COUNT(*)
                FROM emoji_stats
            ))
            LIMIT 1;
        """

        return await con.fetchval(query)

    async def validate_emoji(
            self,
            emoji_id: int,
            *,
            connection: Optional[asyncpg.Connection | asyncpg.Pool] = None,
    ) -> Optional[discord.Emoji]:
        """Returns a discord.Emoji object if the emoji is valid, otherwise returns None."""
        con = connection or self.bot.pool

        query = 'SELECT * FROM emoji_stats WHERE emoji_id=$1 LIMIT 1;'
        record = await con.fetchrow(query, emoji_id)

        guild = self.bot.get_guild(record['guild_id'])
        if guild is None:
            return

        return await guild.fetch_emoji(emoji_id)

    @commands.command(
        commands.hybrid_group,
        name='emoji',
        aliases=['emotes', 'emote'],
        invoke_without_command=True,
        description='Create/Show/Manage emojis in the server.',
    )
    @commands.guild_only()
    @app_commands.guild_only()
    async def _emoji(self, ctx: GuildContext):
        """Emoji management commands."""
        await ctx.send_help(ctx.command)

    @commands.command(
        commands.core_command,
        aliases=['emojilist'],
        description='Fancy post all emojis in this server in a list.',
    )
    @commands.permissions(3, user=['administrator'])
    @commands.cooldown(1, 600, commands.BucketType.guild)
    async def emojipost(self, ctx: GuildContext):
        """Fancy post the emoji lists"""
        emojis = sorted([e for e in ctx.guild.emojis if len(e.roles) == 0 and e.available],
                        key=lambda e: e.name.lower())
        source = TextSource(suffix='', prefix='')

        for emoji in emojis:
            source.add_line(f'{emoji} • `{emoji}`')

        for page in source.pages:
            await ctx.send(page)

    @commands.command(
        commands.core_command,
        name='randomemoji',
        aliases=['randemoji', 'randemote', 'randomemote'],
        description='Sends a random emoji from the database.',
    )
    @commands.cooldown(1, 90, commands.BucketType.user)
    async def random_emoji(self, ctx: Context):
        """Sends a random emoji from the database."""
        emoji = self.bot.get_emoji(await self.get_random_emoji())
        await ctx.send(f'<{'a' if emoji.animated else ''}:{emoji.name}:{emoji.id}>')

    @commands.command(
        _emoji.command,
        name='create',
        description='Create an emoji for the server under the given name.',
        aliases=['add'],
        usage='<name> [file] [emoji]',
    )
    @commands.permissions(3, user=['manage_emojis'], bot=['manage_emojis'])
    @commands.guild_only()
    @app_commands.rename(emoji='emoji-or-url')
    @app_commands.describe(
        name='The emoji name.',
        file='The image file to use for uploading.',
        emoji='The emoji or its URL to use for uploading.',
    )
    async def _emoji_create(
            self,
            ctx: GuildContext,
            name: Annotated[str, emoji_name],
            file: Optional[discord.Attachment],
            *,
            emoji: Optional[str],
    ):
        """Create an emoji for the server under the given name.
        You must have Manage Emoji permission to use this.
        The bot must have this permission too.
        """
        if not ctx.me.guild_permissions.manage_emojis:
            raise commands.BadArgument('I don\'t have permission to add emojis.')

        reason = f'Action done by {ctx.author} (ID: {ctx.author.id})'

        if file is None and emoji is None:
            raise commands.BadArgument('Missing emoji, file or url to upload with.')

        if file is not None and emoji is not None:
            raise commands.BadArgument('Cannot mix both file and url arguments, choose **one** only.')

        is_animated = False
        request_url = ''
        if emoji is not None:
            upgraded = await EmojiURL.convert(ctx, emoji)
            is_animated = upgraded.animated
            request_url = upgraded.url
        elif file is not None:
            if not file.filename.endswith(('.png', '.jpg', '.jpeg', '.gif')):
                raise commands.BadArgument('Unsupported file type given, expected `png`, `jpg`, or `gif`')

            is_animated = file.filename.endswith('.gif')
            request_url = file.url

        emoji_count = sum(e.animated == is_animated for e in ctx.guild.emojis)
        if emoji_count >= ctx.guild.emoji_limit:
            raise commands.BadArgument('There are no more emoji slots in this server.')

        async with self.bot.session.get(request_url) as resp:
            if resp.status >= 400:
                raise commands.BadArgument('Could not fetch the image.')
            if int(resp.headers['Content-Length']) >= (256 * 1024):
                raise commands.BadArgument('Image is too big.')

            data = await resp.read()
            image_color = self.render.get_dominant_color(io.BytesIO(data))

            coro = ctx.guild.create_custom_emoji(name=name, image=data, reason=reason)
            async with ctx.typing():
                try:
                    created: discord.Emoji = await asyncio.wait_for(coro, timeout=10.0)
                except Exception as exc:
                    match exc:
                        case asyncio.TimeoutError():
                            raise commands.BadArgument('Sorry, the bot is rate limited or it took too long.')
                        case discord.HTTPException():
                            raise commands.BadArgument(f'Failed to create emoji somehow: {exc}')
                else:
                    embed = discord.Embed(title='Created Emoji',
                                          colour=discord.Colour.from_rgb(*image_color),
                                          description=f'Successfully added emoji to the server.\n'
                                                      f'<{'a' if created.animated else ''}:{created.name}:{created.id}> • `{created.name}` • [`{created.id}`]\n'
                                                      f'{'Animated ' if created.animated else ''}Emoji slots left: `{ctx.guild.emoji_limit - emoji_count - 1}`',
                                          timestamp=discord.utils.utcnow())
                    embed.set_thumbnail(url=created.url)
                    return await ctx.send(embed=embed)

    def emoji_fmt(self, emoji_id: int, count: int, total: int) -> str:
        emoji = self.bot.get_emoji(emoji_id)
        if emoji is None:
            name = f'[\N{WHITE QUESTION MARK ORNAMENT}](https://cdn.discordapp.com/emojis/{emoji_id}.png)'
            emoji = discord.Object(id=emoji_id)
        else:
            name = str(emoji)

        per_day = usage_per_day(emoji.created_at, count)
        p = count / total
        return f'{name}: {count} uses ({p:.1%}), {per_day:.1f} uses/day.'

    async def get_guild_stats(self, ctx: GuildContext) -> None:
        e = discord.Embed(title='Emoji Leaderboard', colour=ctx.bot.colour.darker_red())

        query = """
            SELECT
               COALESCE(SUM(total), 0) AS "Count",
               COUNT(*) AS "Emoji"
            FROM emoji_stats
            WHERE guild_id=$1
            GROUP BY guild_id;
        """
        record = await ctx.db.fetchrow(query, ctx.guild.id)
        if record is None:
            await ctx.stick(False, 'This server has no emoji stats yet.')
            return

        total = record['Count']
        emoji_used = record['Emoji']

        assert ctx.me.joined_at is not None
        per_day = usage_per_day(ctx.me.joined_at, total)
        e.description = f'`{total}` uses over `{emoji_used}` emoji for **{per_day:.2f}** uses per day.'
        e.set_footer(text=f'Emoji Stats since')
        e.timestamp = ctx.me.joined_at

        query = """
            SELECT emoji_id, total
            FROM emoji_stats
            WHERE guild_id=$1
            ORDER BY total DESC
            LIMIT 10;
        """

        top = await ctx.db.fetch(query, ctx.guild.id)

        e.description = '\n'.join(
            f'{i}. {self.emoji_fmt(emoji, count, total)}' for i, (emoji, count) in enumerate(top, 1))
        await ctx.send(embed=e)

    async def get_emoji_stats(self, ctx: GuildContext, emoji_id: int) -> None:
        e = discord.Embed(title='Emoji Stats')
        cdn = f'https://cdn.discordapp.com/emojis/{emoji_id}.png'

        async with ctx.session.get(cdn) as resp:
            if resp.status == 404:
                e.description = 'This isn\'t a valid emoji.'
                e.colour = 0x000000
                e.set_thumbnail(url='https://images.klappstuhl.me/gallery/fNnccSNJon.jpeg')
                await ctx.send(embed=e)
                return
            e.colour = discord.Colour.from_rgb(*self.render.get_dominant_color(io.BytesIO(await resp.read())))

        e.set_thumbnail(url=cdn)

        query = """
            SELECT guild_id, SUM(total) AS "Count"
            FROM emoji_stats
            WHERE emoji_id=$1
            GROUP BY guild_id;
        """

        records = await ctx.db.fetch(query, emoji_id)
        transformed = {k: v for k, v in records}
        total = sum(transformed.values())

        dt = discord.utils.snowflake_time(emoji_id)

        try:
            count = transformed[ctx.guild.id]
            per_day = usage_per_day(dt, count)
            value = f'{count} uses ({count / total:.2%} of global uses), {per_day:.2f} uses/day'
        except KeyError:
            value = 'Not used here.'

        e.add_field(name='**SERVER**', value=value, inline=False)

        per_day = usage_per_day(dt, total)
        value = f'`{total}` uses, `{per_day:.2f}` uses/day'
        e.add_field(name='**GLOBAL**', value=value, inline=False)
        e.set_footer(text='Statistics based on traffic I can see.')
        await ctx.send(embed=e)

    @_emoji.group(name='stats', fallback='show')
    @commands.guild_only()
    @app_commands.describe(emoji='The emoji to show stats for. If not given then it shows server stats')
    async def emojistats(self, ctx: GuildContext, *, emoji: Annotated[Optional[int], partial_emoji] = None):
        """Shows you statistics about the emoji usage in this server."""
        if emoji is None:
            await self.get_guild_stats(ctx)
        else:
            await self.get_emoji_stats(ctx, emoji)

    @emojistats.command(name='server', aliases=['guild'])
    @commands.guild_only()
    async def emojistats_guild(self, ctx: GuildContext):
        """Shows you statistics about the local server emojis in this server."""
        emoji_ids = [e.id for e in ctx.guild.emojis]

        if not emoji_ids:
            await ctx.stick(False, 'This guild has no custom emoji.')

        query = """SELECT emoji_id, total
                   FROM emoji_stats
                   WHERE guild_id=$1 AND emoji_id = ANY($2::bigint[])
                   ORDER BY total DESC
                """

        e = discord.Embed(title='Emoji Leaderboard', colour=ctx.bot.colour.darker_red())
        records = await ctx.db.fetch(query, ctx.guild.id, emoji_ids)

        total = sum(a for _, a in records)
        emoji_used = len(records)

        assert ctx.me.joined_at is not None
        per_day = usage_per_day(ctx.me.joined_at, total)
        e.set_footer(text=f'{total} uses over {emoji_used} emoji for {per_day:.2f} uses per day.')
        top = records[:10]
        value = '\n'.join(self.emoji_fmt(emoji, count, total) for (emoji, count) in top)
        e.add_field(name=f'Top {len(top)}', value=value or 'Nothing...')

        record_count = len(records)
        if record_count > 10:
            bottom = records[-10:] if record_count >= 20 else records[-record_count + 10:]
            value = '\n'.join(self.emoji_fmt(emoji, count, total) for (emoji, count) in bottom)
            e.add_field(name=f'Bottom {len(bottom)}', value=value)

        await ctx.send(embed=e)


async def setup(bot):
    await bot.add_cog(Emoji(bot))
