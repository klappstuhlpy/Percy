import asyncio
import io
import re
from collections import Counter, defaultdict
from typing import Annotated, Any

import asyncpg
import discord
import yarl
from discord import app_commands
from discord.ext import commands, tasks

from app.core import Bot, Cog, Context
from app.core.models import command, cooldown, describe, group
from app.rendering import get_dominant_color
from app.utils import helpers, usage_per_day
from app.utils.lock import lock
from app.utils.pagination import TextSource
from config import Emojis

EMOJI_REGEX = re.compile(r'<a?:.+?:([0-9]{15,21})>')
EMOJI_NAME_REGEX = re.compile(r'^[0-9a-zA-Z-_]{2,32}$')


def partial_emoji(argument: str, *, regex: re.Pattern = EMOJI_REGEX) -> int | str:
    if argument.isdigit():
        # assume it's an emoji ID
        return int(argument)

    m = regex.match(argument)
    if m is None:
        return argument
    return int(m.group(1))


def emoji_name(argument: str, *, regex: re.Pattern = EMOJI_NAME_REGEX) -> str:
    m = regex.match(argument)
    if m is None:
        raise commands.BadArgument('Invalid emoji name.')
    return argument


class EmojiURL:
    def __init__(self, *, animated: bool, url: str) -> None:
        self.url: str = url
        self.animated: bool = animated

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> 'EmojiURL':
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


class Emoji(Cog):
    """Emoji managing related commands."""

    emoji = Emojis.very_cool

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self._emoji_data_batch: defaultdict[int, Counter[int]] = defaultdict(Counter)

        self.bulk_insert_loop.add_exception_type(asyncpg.PostgresConnectionError)
        self.bulk_insert_loop.start()

    def cog_unload(self) -> None:
        self.bulk_insert_loop.stop()

    @lock('Emoji', 'emoji_batch', wait=True)
    async def bulk_insert(self) -> None:
        query = """
            INSERT INTO emoji_stats (guild_id, emoji_id, total)
            SELECT x.guild, x.emoji, x.added
            FROM jsonb_to_recordset($1::jsonb)
                     AS x(
                          guild BIGINT,
                          emoji BIGINT,
                          added INT
                    )
            ON CONFLICT (guild_id, emoji_id) DO UPDATE
                SET total = emoji_stats.total + excluded.total;
        """

        transformed = [
            {'guild': guild_id, 'emoji': emoji_id, 'added': count}
            for guild_id, data in self._emoji_data_batch.items()
            for emoji_id, count in data.items()
        ]
        self._emoji_data_batch.clear()
        await self.bot.db.execute(query, transformed)

    @tasks.loop(seconds=60.0)
    async def bulk_insert_loop(self) -> None:
        await self.bulk_insert()

    @staticmethod
    def find_all_emoji(message: discord.Message, *, regex: re.Pattern = EMOJI_REGEX) -> list[str]:
        return regex.findall(message.content)

    @lock('Emoji', 'emoji_batch', wait=True)
    async def send_emoji_patch(self, guild_id: int, matches: list[Any]) -> None:
        self._emoji_data_batch[guild_id].update(map(int, matches))

    @Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.guild is None:
            return

        if message.author.bot:
            return

        matches = EMOJI_REGEX.findall(message.content)
        if not matches:
            return

        await self.send_emoji_patch(message.guild.id, matches)

    async def get_random_emoji(
            self,
            *,
            connection: asyncpg.Connection | None = None,
    ) -> int | None:
        """Returns a random emoji from the database."""

        con = connection or self.bot.db
        query = """
            SELECT emoji_id
            FROM emoji_stats
            OFFSET FLOOR(RANDOM() * (SELECT COUNT(*) FROM emoji_stats))
            LIMIT 1;
        """
        return await con.fetchval(query)

    async def resolve_random_emoji_until_available(
            self, *, connection: asyncpg.Connection | None = None
    ) -> discord.Emoji | None:
        con = connection or self.bot.db

        emoji = None
        while emoji is None:
            emoji = self.bot.get_emoji(await self.get_random_emoji(connection=con))
        return emoji

    async def validate_emoji(
            self,
            emoji_id: int,
            *,
            connection: asyncpg.Connection | None = None,
    ) -> discord.Emoji | None:
        """Returns a discord.Emoji object if the emoji is valid, otherwise returns None."""
        con = connection or self.bot.db

        query = 'SELECT * FROM emoji_stats WHERE emoji_id=$1 LIMIT 1;'
        record = await con.fetchrow(query, emoji_id)

        guild = self.bot.get_guild(record['guild_id'])
        if guild is None:
            return
        return await guild.fetch_emoji(emoji_id)

    @group(
        'emoji',
        aliases=['emotes', 'emote'],
        invoke_without_command=True,
        description='Create/Show/Manage emojis in the server.',
        guild_only=True,
        hybrid=True
    )
    async def _emoji(self, ctx: Context) -> None:
        """Emoji management commands."""
        await ctx.send_help(ctx.command)

    @command(
        aliases=['emojilist'],
        description='Fancy post all emojis in this server in a list.',
        guild_only=True
    )
    @cooldown(1, 15, commands.BucketType.guild)
    async def emojipost(self, ctx: Context) -> None:
        """Fancy post the emoji lists"""
        emojis = sorted([e for e in ctx.guild.emojis if len(e.roles) == 0 and e.available],
                        key=lambda e: e.name.lower())
        source = TextSource(suffix='', prefix='')

        for emoji in emojis:
            source.add_line(f'{emoji} • `{emoji}`')

        for page in source.pages:
            await ctx.send(page)

    @command(
        'randomemoji',
        aliases=['randemoji', 'randemote', 'randomemote'],
        description='Sends a random emoji from the database.',
    )
    @cooldown(1, 5, commands.BucketType.user)
    async def random_emoji(self, ctx: Context) -> None:
        """Sends a random emoji from the database."""
        emoji = await self.resolve_random_emoji_until_available()
        await ctx.send(f'<{'a' if emoji.animated else ''}:{emoji.name}:{emoji.id}>')

    @_emoji.command(
        'create',
        description='Create an emoji for the server under the given name.',
        aliases=['add'],
        usage='<name> [file] [emoji]',
        guild_only=True,
        user_permissions=['manage_emojis'],
        bot_permissions=['manage_emojis']
    )
    @app_commands.rename(emoji='emoji-or-url')
    @describe(
        name='The emoji name.',
        file='The image file to use for uploading.',
        emoji='The emoji or its URL to use for uploading.',
    )
    async def _emoji_create(
            self,
            ctx: Context,
            name: Annotated[str, emoji_name],
            file: discord.Attachment | None,
            *,
            emoji: str | None,
    ) -> None:
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
            image_color = get_dominant_color(io.BytesIO(data))

            coro = ctx.guild.create_custom_emoji(name=name, image=data, reason=reason)
            async with ctx.typing():
                try:
                    created: discord.Emoji = await asyncio.wait_for(coro, timeout=10.0)
                except Exception as exc:
                    match exc:
                        case TimeoutError():
                            raise commands.BadArgument('Sorry, the bot is rate limited or it took too long.')
                        case discord.HTTPException():
                            raise commands.BadArgument(f'Failed to create emoji somehow: {exc}')
                else:
                    embed = discord.Embed(
                        title='Created Emoji',
                        colour=discord.Colour.from_rgb(*image_color),
                        description=f'Successfully added emoji to the server.\n'
                                    f'<{'a' if created.animated else ''}:{created.name}:{created.id}> • `{created.name}` • [`{created.id}`]\n'
                                    f'{'Animated ' if created.animated else ''}Emoji slots left: `{ctx.guild.emoji_limit - emoji_count - 1}`',
                        timestamp=discord.utils.utcnow())
                    embed.set_thumbnail(url=created.url)
                    await ctx.send(embed=embed)

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

    async def get_guild_stats(self, ctx: Context) -> None:
        embed = discord.Embed(title='Emoji Leaderboard', colour=helpers.Colour.white())

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
            await ctx.send_error('This server has no emoji stats yet.')
            return

        total = record['Count']
        emoji_used = record['Emoji']

        assert ctx.me.joined_at is not None
        per_day = usage_per_day(ctx.me.joined_at, total)
        embed.description = f'`{total}` uses over `{emoji_used}` emoji for **{per_day:.2f}** uses per day.'
        embed.set_footer(text='Emoji Stats since')
        embed.timestamp = ctx.me.joined_at

        query = """
            SELECT emoji_id, total
            FROM emoji_stats
            WHERE guild_id=$1
            ORDER BY total DESC
            LIMIT 10;
        """

        top = await ctx.db.fetch(query, ctx.guild.id)

        embed.description = '\n'.join(
            f'{i}. {self.emoji_fmt(emoji, count, total)}' for i, (emoji, count) in enumerate(top, 1))
        await ctx.send(embed=embed)

    @staticmethod
    async def get_emoji_stats(ctx: Context, emoji_id: int) -> None:
        embed = discord.Embed(title='Emoji Stats')
        cdn = f'https://cdn.discordapp.com/emojis/{emoji_id}.png'

        async with ctx.session.get(cdn) as resp:
            if resp.status == 404:
                embed.description = 'This isn\'t a valid emoji.'
                embed.colour = 0x000000
                embed.set_thumbnail(url='https://klappstuhl.me/gallery/XTobhlFAyH.jpeg')
                await ctx.send(embed=embed)
                return
            embed.colour = discord.Colour.from_rgb(*get_dominant_color(io.BytesIO(await resp.read())))

        embed.set_thumbnail(url=cdn)

        query = """
            SELECT guild_id, SUM(total) AS "count"
            FROM emoji_stats
            WHERE emoji_id=$1
            GROUP BY guild_id;
        """

        records = await ctx.db.fetch(query, emoji_id)
        transformed = dict(records)
        total = sum(transformed.values())

        dt = discord.utils.snowflake_time(emoji_id)

        try:
            count = transformed[ctx.guild.id]
            per_day = usage_per_day(dt, count)
            value = f'{count} uses ({count / total:.2%} of global uses), {per_day:.2f} uses/day'
        except KeyError:
            value = 'Not used here.'

        embed.add_field(name='**Server**', value=value, inline=False)

        per_day = usage_per_day(dt, total)
        value = f'`{total}` uses, `{per_day:.2f}` uses/day'
        embed.add_field(name='**Global**', value=value, inline=False)
        embed.set_footer(text='Statistics based on traffic I can see.')
        await ctx.send(embed=embed)

    @_emoji.group(
        'stats',
        fallback='show',
        guild_only=True
    )
    @describe(emoji='The emoji to show stats for. If not given then it shows server stats')
    async def emojistats(self, ctx: Context, *, emoji: Annotated[str | int, partial_emoji]) -> None:
        """Shows you statistics about the emoji usage."""
        if isinstance(emoji, int):
            emoji = self.bot.get_emoji(emoji)
            if emoji is None:
                await ctx.send_error('Could not find the emoji.')
                return
        else:
            emoji = discord.utils.get(ctx.guild.emojis, name=emoji)

        if emoji is None:
            await ctx.send_error('Could not find the emoji.')
            return

        if emoji is None:
            await self.get_guild_stats(ctx)
        else:
            await self.get_emoji_stats(ctx, emoji.id)

    @emojistats.command(
        'server',
        aliases=['guild'],
        guild_only=True
    )
    async def emojistats_guild(self, ctx: Context) -> None:
        """Shows you statistics about the local server emojis in this server."""
        emoji_ids = [e.id for e in ctx.guild.emojis]

        if not emoji_ids:
            await ctx.send_error('This guild has no custom emoji.')
            return

        query = """
            SELECT emoji_id, total
            FROM emoji_stats
            WHERE guild_id=$1 AND emoji_id = ANY($2::bigint[])
            ORDER BY total DESC;
        """

        embed = discord.Embed(title='Emoji Leaderboard', colour=helpers.Colour.white())
        records = await ctx.db.fetch(query, ctx.guild.id, emoji_ids)

        total = sum(a for _, a in records)
        emoji_used = len(records)

        assert ctx.me.joined_at is not None
        per_day = usage_per_day(ctx.me.joined_at, total)
        embed.set_footer(text=f'{total} uses over {emoji_used} emoji for {per_day:.2f} uses per day.')
        top = records[:10]
        value = '\n'.join(self.emoji_fmt(emoji, count, total) for (emoji, count) in top)
        embed.add_field(name=f'Top {len(top)}', value=value or 'Nothing...')

        record_count = len(records)
        if record_count > 10:
            bottom = records[-10:] if record_count >= 20 else records[-record_count + 10:]
            value = '\n'.join(self.emoji_fmt(emoji, count, total) for (emoji, count) in bottom)
            embed.add_field(name=f'Bottom {len(bottom)}', value=value)

        await ctx.send(embed=embed)

    @emojistats.command(
        'global',
        aliases=['all'],
        guild_only=True
    )
    async def emojistats_global(self, ctx: Context) -> None:
        """Shows you statistics about the global emoji usage."""
        query = """
            SELECT emoji_id, SUM(total) AS "count"
            FROM emoji_stats
            GROUP BY emoji_id
            ORDER BY "count" DESC
            LIMIT 10;
        """

        embed = discord.Embed(title='Emoji Leaderboard', colour=helpers.Colour.white())
        records = await ctx.db.fetch(query)

        total = sum(a for _, a in records)
        emoji_used = len(records)

        per_day = usage_per_day(ctx.me.joined_at, total)
        embed.set_footer(text=f'{total} uses over {emoji_used} emoji for {per_day:.2f} uses per day.')
        top = records[:10]
        value = '\n'.join(self.emoji_fmt(emoji, count, total) for (emoji, count) in top)
        embed.add_field(name=f'Top {len(top)}', value=value or 'Nothing...')

        record_count = len(records)
        if record_count > 10:
            bottom = records[-10:] if record_count >= 20 else records[-record_count + 10:]
            value = '\n'.join(self.emoji_fmt(emoji, count, total) for (emoji, count) in bottom)
            embed.add_field(name=f'Bottom {len(bottom)}', value=value)

        await ctx.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Emoji(bot))
