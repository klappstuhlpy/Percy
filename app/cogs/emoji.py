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
from app.core.pagination import TextSource
from app.rendering import get_dominant_color
from app.utils import helpers, usage_per_day
from app.utils.lock import lock
from config import Emojis

EMOJI_REGEX = re.compile(r"<a?:.+?:([0-9]{15,21})>")
EMOJI_NAME_REGEX = re.compile(r"^[0-9a-zA-Z-_]{2,32}$")


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
        raise commands.BadArgument("Invalid emoji name.")
    return argument


class EmojiURL:
    def __init__(self, *, animated: bool, url: str) -> None:
        self.url: str = url
        self.animated: bool = animated

    @classmethod
    async def convert(cls, ctx: Context, argument: str) -> "EmojiURL":
        try:
            partial = await commands.PartialEmojiConverter().convert(ctx, argument)
        except commands.BadArgument:
            try:
                url = yarl.URL(argument)
                if url.scheme not in ("http", "https"):
                    raise RuntimeError
                path = url.path.lower()
                if not path.endswith((".png", ".jpeg", ".jpg", ".gif")):
                    raise RuntimeError
                return cls(animated=url.path.endswith(".gif"), url=argument)
            except Exception:
                raise commands.BadArgument("Not a valid or supported emoji URL.") from None
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

    @lock("Emoji", "emoji_batch", wait=True)
    async def bulk_insert(self) -> None:
        transformed = [
            {"guild": guild_id, "emoji": emoji_id, "added": count}
            for guild_id, data in self._emoji_data_batch.items()
            for emoji_id, count in data.items()
        ]
        self._emoji_data_batch.clear()
        await self.bot.db.emoji_stats.bulk_insert(transformed)

    @tasks.loop(seconds=60.0)
    async def bulk_insert_loop(self) -> None:
        await self.bulk_insert()

    @staticmethod
    def find_all_emoji(message: discord.Message, *, regex: re.Pattern = EMOJI_REGEX) -> list[str]:
        return regex.findall(message.content)

    @lock("Emoji", "emoji_batch", wait=True)
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
        return await self.bot.db.emoji_stats.get_random_emoji_id(connection=connection)

    async def resolve_random_emoji_until_available(
        self, *, connection: asyncpg.Connection | None = None
    ) -> discord.Emoji | None:
        emoji = None
        while emoji is None:
            emoji = self.bot.get_emoji(await self.get_random_emoji(connection=connection))  # type: ignore
        return emoji

    async def validate_emoji(
        self,
        emoji_id: int,
        *,
        connection: asyncpg.Connection | None = None,
    ) -> discord.Emoji | None:
        """Returns a discord.Emoji object if the emoji is valid, otherwise returns None."""
        record = await self.bot.db.emoji_stats.get_emoji_record(emoji_id, connection=connection)

        guild = self.bot.get_guild(record["guild_id"])
        if guild is None:
            return
        return await guild.fetch_emoji(emoji_id)

    @group(
        "emoji",
        aliases=["emotes", "emote"],
        invoke_without_command=True,
        description="Create/Show/Manage emojis in the server.",
        guild_only=True,
        hybrid=True,
    )
    async def _emoji(self, ctx: Context) -> None:
        """Emoji management commands."""
        assert ctx.guild is not None
        await ctx.send_help(ctx.command)

    @command(aliases=["emojilist"], description="Fancy post all emojis in this server in a list.", guild_only=True)
    @cooldown(1, 15, commands.BucketType.guild)
    async def emojipost(self, ctx: Context) -> None:
        """Fancy post the emoji lists"""
        assert ctx.guild is not None
        emojis = sorted([e for e in ctx.guild.emojis if len(e.roles) == 0 and e.available], key=lambda e: e.name.lower())
        source = TextSource(suffix="", prefix="")

        for emoji in emojis:
            source.add_line(f"{emoji} • `{emoji}`")

        for page in source.pages:
            await ctx.send(page)

    @command(
        "randomemoji",
        aliases=["randemoji", "randemote", "randomemote"],
        description="Sends a random emoji from the database.",
    )
    @cooldown(1, 5, commands.BucketType.user)
    async def random_emoji(self, ctx: Context) -> None:
        """Sends a random emoji from the database."""
        emoji = await self.resolve_random_emoji_until_available()
        assert emoji is not None
        await ctx.send(f"<{'a' if emoji.animated else ''}:{emoji.name}:{emoji.id}>")

    @_emoji.command(
        "create",
        description="Create an emoji for the server under the given name.",
        aliases=["add"],
        usage="<name> [file] [emoji]",
        guild_only=True,
        user_permissions=["manage_emojis"],
        bot_permissions=["manage_emojis"],
    )
    @app_commands.rename(emoji="emoji-or-url")
    @describe(
        name="The emoji name.",
        file="The image file to use for uploading.",
        emoji="The emoji or its URL to use for uploading.",
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
        assert ctx.guild is not None
        if not ctx.me.guild_permissions.manage_emojis:
            raise commands.BadArgument("I don't have permission to add emojis.")

        reason = f"Action done by {ctx.author} (ID: {ctx.author.id})"

        if file is None and emoji is None:
            raise commands.BadArgument("Missing emoji, file or url to upload with.")

        if file is not None and emoji is not None:
            raise commands.BadArgument("Cannot mix both file and url arguments, choose **one** only.")

        is_animated = False
        request_url = ""
        if emoji is not None:
            upgraded = await EmojiURL.convert(ctx, emoji)
            is_animated = upgraded.animated
            request_url = upgraded.url
        elif file is not None:
            if not file.filename.endswith((".png", ".jpg", ".jpeg", ".gif")):
                raise commands.BadArgument("Unsupported file type given, expected `png`, `jpg`, or `gif`")

            is_animated = file.filename.endswith(".gif")
            request_url = file.url

        emoji_count = sum(e.animated == is_animated for e in ctx.guild.emojis)
        if emoji_count >= ctx.guild.emoji_limit:
            raise commands.BadArgument("There are no more emoji slots in this server.")

        async with self.bot.session.get(request_url) as resp:
            if resp.status >= 400:
                raise commands.BadArgument("Could not fetch the image.")
            if int(resp.headers["Content-Length"]) >= (256 * 1024):
                raise commands.BadArgument("Image is too big.")

            data = await resp.read()
            image_color = get_dominant_color(io.BytesIO(data))

            coro = ctx.guild.create_custom_emoji(name=name, image=data, reason=reason)
            async with ctx.typing():
                try:
                    created: discord.Emoji = await asyncio.wait_for(coro, timeout=10.0)
                except Exception as exc:
                    match exc:
                        case TimeoutError():
                            raise commands.BadArgument("Sorry, the bot is rate limited or it took too long.")
                        case discord.HTTPException():
                            raise commands.BadArgument(f"Failed to create emoji somehow: {exc}")
                else:
                    embed = discord.Embed(
                        title="Created Emoji",
                        colour=discord.Colour.from_rgb(*image_color),
                        description=f"Successfully added emoji to the server.\n"
                        f"<{'a' if created.animated else ''}:{created.name}:{created.id}> • `{created.name}` • [`{created.id}`]\n"
                        f"{'Animated ' if created.animated else ''}Emoji slots left: `{ctx.guild.emoji_limit - emoji_count - 1}`",
                        timestamp=discord.utils.utcnow(),
                    )
                    embed.set_thumbnail(url=created.url)
                    await ctx.send(embed=embed)

    def emoji_fmt(self, emoji_id: int, count: int, total: int) -> str:
        emoji = self.bot.get_emoji(emoji_id)
        if emoji is None:
            name = f"[\N{WHITE QUESTION MARK ORNAMENT}](https://cdn.discordapp.com/emojis/{emoji_id}.png)"
            created_at = discord.utils.snowflake_time(emoji_id)
        else:
            name = str(emoji)
            created_at = emoji.created_at

        per_day = usage_per_day(created_at, count)
        p = count / total
        return f"{name}: {count} uses ({p:.1%}), {per_day:.1f} uses/day."

    async def get_guild_stats(self, ctx: Context) -> None:
        embed = discord.Embed(title="Emoji Leaderboard", colour=helpers.Colour.white())

        assert ctx.guild is not None
        assert ctx.me.joined_at is not None
        record = await ctx.db.emoji_stats.get_guild_summary(ctx.guild.id)
        if record is None:
            await ctx.send_error("This server has no emoji stats yet.")
            return

        total = record["Count"]
        emoji_used = record["Emoji"]

        per_day = usage_per_day(ctx.me.joined_at, total)
        embed.description = f"`{total}` uses over `{emoji_used}` emoji for **{per_day:.2f}** uses per day."
        embed.set_footer(text="Emoji Stats since")
        embed.timestamp = ctx.me.joined_at

        top = await ctx.db.emoji_stats.get_top_guild_emojis(ctx.guild.id)

        embed.description = "\n".join(
            f"{i}. {self.emoji_fmt(emoji, count, total)}" for i, (emoji, count) in enumerate(top, 1)
        )
        await ctx.send(embed=embed)

    @staticmethod
    async def get_emoji_stats(ctx: Context, emoji_id: int) -> None:
        embed = discord.Embed(title="Emoji Stats")
        cdn = f"https://cdn.discordapp.com/emojis/{emoji_id}.png"

        async with ctx.session.get(cdn) as resp:
            if resp.status == 404:
                embed.description = "This isn't a valid emoji."
                embed.colour = 0x000000
                embed.set_thumbnail(url="https://klappstuhl.me/gallery/LHIcq.jpeg")
                await ctx.send(embed=embed)
                return
            embed.colour = discord.Colour.from_rgb(*get_dominant_color(io.BytesIO(await resp.read())))

        embed.set_thumbnail(url=cdn)

        assert ctx.guild is not None
        records = await ctx.db.emoji_stats.get_emoji_guild_breakdown(emoji_id)
        transformed: dict[int, int] = {record["guild_id"]: record["count"] for record in records}
        total = sum(transformed.values())

        dt = discord.utils.snowflake_time(emoji_id)

        try:
            count = transformed[ctx.guild.id]
            per_day = usage_per_day(dt, count)
            value = f"{count} uses ({count / total:.2%} of global uses), {per_day:.2f} uses/day"
        except KeyError:
            value = "Not used here."

        embed.add_field(name="**Server**", value=value, inline=False)

        per_day = usage_per_day(dt, total)
        value = f"`{total}` uses, `{per_day:.2f}` uses/day"
        embed.add_field(name="**Global**", value=value, inline=False)
        embed.set_footer(text="Statistics based on traffic I can see.")
        await ctx.send(embed=embed)

    @_emoji.group("stats", fallback="show", guild_only=True)
    @describe(emoji="The emoji to show stats for. If not given then it shows server stats")
    async def emojistats(self, ctx: Context, *, emoji: Annotated[str | int, partial_emoji]) -> None:
        """Shows you statistics about the emoji usage."""
        assert ctx.guild is not None
        if isinstance(emoji, int):
            emoji = self.bot.get_emoji(emoji)
            if emoji is None:
                await ctx.send_error("Could not find the emoji.")
                return
        else:
            emoji = discord.utils.get(ctx.guild.emojis, name=emoji)

        if emoji is None:
            await ctx.send_error("Could not find the emoji.")
            return

        if emoji is None:
            await self.get_guild_stats(ctx)
        else:
            await self.get_emoji_stats(ctx, emoji.id)

    @emojistats.command("server", aliases=["guild"], guild_only=True)
    async def emojistats_guild(self, ctx: Context) -> None:
        """Shows you statistics about the local server emojis in this server."""
        assert ctx.guild is not None
        emoji_ids = [e.id for e in ctx.guild.emojis]

        if not emoji_ids:
            await ctx.send_error("This guild has no custom emoji.")
            return

        embed = discord.Embed(title="Emoji Leaderboard", colour=helpers.Colour.white())
        records = await ctx.db.emoji_stats.get_guild_emoji_stats(ctx.guild.id, emoji_ids)

        total = sum(a for _, a in records)
        emoji_used = len(records)

        assert ctx.me.joined_at is not None
        per_day = usage_per_day(ctx.me.joined_at, total)
        embed.set_footer(text=f"{total} uses over {emoji_used} emoji for {per_day:.2f} uses per day.")
        top = records[:10]
        value = "\n".join(self.emoji_fmt(emoji, count, total) for (emoji, count) in top)
        embed.add_field(name=f"Top {len(top)}", value=value or "Nothing...")

        record_count = len(records)
        if record_count > 10:
            bottom = records[-10:] if record_count >= 20 else records[-record_count + 10 :]
            value = "\n".join(self.emoji_fmt(emoji, count, total) for (emoji, count) in bottom)
            embed.add_field(name=f"Bottom {len(bottom)}", value=value)

        await ctx.send(embed=embed)

    @emojistats.command("global", aliases=["all"], guild_only=True)
    async def emojistats_global(self, ctx: Context) -> None:
        """Shows you statistics about the global emoji usage."""
        assert ctx.me.joined_at is not None
        embed = discord.Embed(title="Emoji Leaderboard", colour=helpers.Colour.white())
        records = await ctx.db.emoji_stats.get_global_top_emojis()

        total = sum(a for _, a in records)
        emoji_used = len(records)

        per_day = usage_per_day(ctx.me.joined_at, total)
        embed.set_footer(text=f"{total} uses over {emoji_used} emoji for {per_day:.2f} uses per day.")
        top = records[:10]
        value = "\n".join(self.emoji_fmt(emoji, count, total) for (emoji, count) in top)
        embed.add_field(name=f"Top {len(top)}", value=value or "Nothing...")

        record_count = len(records)
        if record_count > 10:
            bottom = records[-10:] if record_count >= 20 else records[-record_count + 10:]
            value = '\n'.join(self.emoji_fmt(emoji, count, total) for (emoji, count) in bottom)
            embed.add_field(name=f'Bottom {len(bottom)}', value=value)

        await ctx.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(Emoji(bot))
