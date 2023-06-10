from __future__ import annotations

import asyncio
import base64
import binascii
import datetime
import inspect
import io
import pprint
import re
import textwrap
from typing import TYPE_CHECKING, Optional, Any, List, NamedTuple, Self, Annotated, Mapping
from urllib.parse import urlparse, urljoin

import aiohttp
import discord
import yarl
from discord.ext import commands, tasks

from cogs import command
from cogs.utils.converters import Snowflake
from cogs.utils.paginator import TextSource
from cogs.utils.scope import GITHUB_URL_REGEX, PH_GUILD_ID, PH_BOTS_ROLE, PH_HELP_FORUM, TOKEN_REGEX, \
    PLAYGROUND_GUILD_ID, PH_MEMBERS_ROLE, GITHUB_FULL_REGEX
from launcher import get_logger

if TYPE_CHECKING:
    from bot import Percy
    from utils.context import Context

log = get_logger(__name__)


class TrashView(discord.ui.View):
    def __init__(self, author: discord.Member):
        super().__init__()
        self.author: discord.Member = author

    @discord.ui.button(
        style=discord.ButtonStyle.red, emoji="🗑️", label="Delete", custom_id="delete"
    )
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.author.id:
            return
        await interaction.message.delete()

    async def on_timeout(self) -> None:
        for children in self.children:
            children.disabled = True


def validate_token(token: str) -> bool:
    """Validate a Discord token using base64 decoding.

    Returns
    -------
    bool
        Whether the token is valid.
    """
    try:
        (user_id, _, _) = token.split('.')
        user_id = int(base64.b64decode(user_id + '==', validate=True))
    except (ValueError, binascii.Error):
        return False
    else:
        return True


class GithubError(commands.CommandError):
    """Base exception for GitHub errors."""
    pass


class ParsedBase(NamedTuple):
    url: str
    raw_url: str
    lines: List[int]
    user: str
    filename: str
    extension: str
    branch: str
    repository: str
    file_path: str


class CodeSnippet:
    session: aiohttp.ClientSession
    base: Optional[ParsedBase]
    message: discord.Message

    def __repr__(self):
        return f"<GitHub url={self.base.url} lines={self.base.lines} filename={self.base.filename}>"

    @classmethod
    def match_url(cls, url: str) -> Optional[ParsedBase]:
        match = GITHUB_FULL_REGEX.match(url)

        if match is None:
            return None

        user = match.group('user')
        repository = match.group('repository')
        branch = match.group('branch')
        file_path = (match.group('file_path') or '....')[:-1]
        (filename, _, file_extension) = match.group('filename').partition('.')

        parsed = urlparse(url)
        line_compiled = re.compile(r'L(?:(?P<start>\d+)(?:-L(?P<end>\d+))?)?$')

        line_numbers = None
        match = line_compiled.match(parsed.fragment)

        if match:
            start_line = match.group('start')
            end_line = match.group('end')

            if start_line is not None:
                line_numbers = [int(start_line)]

                if end_line is not None:
                    line_numbers.append(int(end_line))
                else:
                    line_numbers.append(int(start_line))

        raw_url = re.sub(r'^https?://(?:www\.)?github\.com/([^/]+)/([^/]+)/blob/(.*)$',
                         r'https://raw.githubusercontent.com/\1/\2/\3', url)

        return ParsedBase(
            url=url,
            raw_url=raw_url,
            lines=line_numbers,
            user=user,
            filename=filename,
            extension=file_extension,
            branch=branch,
            repository=repository,
            file_path=file_path,
        )

    @classmethod
    def open(cls, session: aiohttp.ClientSession, message: discord.Message | str) -> List[Self]:
        """Open a GitHub URL.
        Returns
        -------
        Optional[Self]
            The GitHub object."""

        temporay = [
            cls.match_url(x) for x in GITHUB_URL_REGEX.findall(
                message.content if isinstance(message, discord.Message) else message
            )
        ]

        for base in temporay:
            if base is None:
                continue

            new = cls()
            new.session = session
            new.base = base
            new.message = message
            yield new

    async def format(self) -> tuple[str, discord.Embed] | tuple[None, None]:
        """Format the GitHub URL into a string.
        Returns
        -------
        tuple[str, str]
            A tuple of the formatted string and the info string."""

        async with self.session.get(self.base.raw_url) as resp:
            if resp.status != 200:
                return None, None

            text = await resp.text()

            embed = discord.Embed(color=0x171515)
            embed.set_author(
                name=f"{self.base.user} / {self.base.repository}",
                url=urljoin("https://github.com", f"{self.base.user}/{self.base.repository}"),
                icon_url="https://cdn.discordapp.com/attachments/1066703171243745377/1108088021586284544/Octicons-mark-github.svg.png"
            )
            embed.add_field(name="File", value=f"[`{self.base.filename}.{self.base.extension}`]({self.base.url})")
            embed.add_field(name="Path", value=f"`{self.base.file_path}`")
            embed.add_field(name="Branch", value=f"`{self.base.branch}`")

            if self.base.lines:
                text = "\n".join(text.split("\n")[self.base.lines[0] - 1:self.base.lines[1]])
                embed.set_footer(text=f"Lines {self.base.lines[0]}-{self.base.lines[1]}")
            else:
                text = text.strip()

            return text, embed

    async def post(self) -> None:
        """Post the formatted GitHub URL to the chat."""

        message = self.message
        text, embed = await self.format()
        if any([text is None, embed is None]):
            return

        async with message.channel.typing():
            await message.edit(suppress=True)

            if len(text) < 2000:
                await message.reply(embed=embed, content=f"```{self.base.extension}\n{text}```",
                                    view=TrashView(message.author),
                                    mention_author=False)
            else:
                file = discord.File(io.BytesIO(text.encode()), filename=f"{self.base.filename}.{self.base.extension}")
                await message.reply(embed=embed, file=file, view=TrashView(message.author), mention_author=False)


class Base(commands.Cog, name='Exclusives'):
    """Utility related Commands and Functions.

    Functions:
    -----------
    `URL to File:` Converts the Code from a GitHub File URL and sends it in the chat by passing a valid GitHub URL.
    `Member Join:` Adds some Roles to Members when they join the Server.
    `Auto Archive Threads:` Archives the Threads in the Help Forum after a certain amount of days.
    `Automatic Token Invalidator:` Automatically invalidates Discord Tokens when they are sent in the chat.
    """

    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self.bot.loop.create_task(self._prepare_invites())
        self._req_lock = asyncio.Lock()
        self.auto_archive_old_forum_threads.start()

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='dpy', id=596577034537402378)

    def cog_unload(self) -> None:
        self.auto_archive_old_forum_threads.cancel()

    async def _prepare_invites(self):
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(PH_GUILD_ID)

        if guild is not None:
            invites = await guild.invites()
            self._invite_cache = {invite.code: invite.uses or 0 for invite in invites}

    async def github_request(
            self,
            method: str,
            url: str,
            *,
            params: Optional[dict[str, Any]] = None,
            data: Optional[dict[str, Any]] = None,
            headers: Optional[dict[str, Any]] = None,
    ) -> Any:
        hdrs = {'Accept': 'application/vnd.github.inertia-preview+json',
                'User-Agent': 'Percy DPY-Exclusives',
                'Authorization': f'Bearer {self.bot.config.github_key}'}

        req_url = yarl.URL('https://api.github.com') / url

        if headers is not None and isinstance(headers, dict):
            hdrs.update(headers)

        async with self._req_lock:
            async with self.bot.session.request(method, req_url, params=params, json=data, headers=hdrs) as r:
                remaining = r.headers.get('X-Ratelimit-Remaining')
                js = await r.json()
                if r.status == 429 or remaining == '0':
                    delta = discord.utils._parse_ratelimit_header(r)
                    await asyncio.sleep(delta)
                    self._req_lock.release()
                    return await self.github_request(method, url, params=params, data=data, headers=headers)
                elif 300 > r.status >= 200:
                    return js
                else:
                    raise GithubError(js['message'])

    async def create_gist(
            self,
            content: str,
            *,
            description: Optional[str] = None,
            filename: Optional[str] = None,
            public: bool = True,
    ) -> str:
        headers = {'Accept': 'application/vnd.github.v3+json'}

        filename = filename or 'output.txt'
        data = {
            'public': public,
            'files': {
                filename: {
                    'content': content,
                }
            },
        }

        if description:
            data['description'] = description

        js = await self.github_request('POST', 'gists', data=data, headers=headers)
        return js['html_url']

    def cog_check(self, ctx: Context):
        return ctx.guild and ctx.guild.id == PH_GUILD_ID

    async def cog_command_error(self, ctx: Context, error: commands.CommandError):
        if isinstance(error, GithubError):
            await ctx.send(f'Github Error: {error}')

    @tasks.loop(hours=1)
    async def auto_archive_old_forum_threads(self):
        guild = self.bot.get_guild(PH_GUILD_ID)
        if guild is None:
            return

        forum: discord.ForumChannel = guild.get_channel(PH_HELP_FORUM)  # type: ignore
        if forum is None:
            return

        now = discord.utils.utcnow()
        for thread in forum.threads:
            if thread.archived or thread.flags.pinned:
                continue

            if thread.last_message_id is None:
                continue

            last_message = discord.utils.snowflake_time(thread.last_message_id)
            expires = last_message + datetime.timedelta(minutes=thread.auto_archive_duration)
            if now > expires:
                await thread.edit(archived=True, reason='Auto-archived due to inactivity.')

    @auto_archive_old_forum_threads.before_loop
    async def before_auto_archive_old_forum_threads(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if member.guild.id != PH_GUILD_ID:
            return

        if member.bot:
            await member.add_roles(discord.Object(id=PH_BOTS_ROLE))
            return

        await member.add_roles(discord.Object(id=PH_MEMBERS_ROLE))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if not message.guild or message.guild.id not in (PH_GUILD_ID, PLAYGROUND_GUILD_ID):
            return

        tokens = [token for token in TOKEN_REGEX.findall(message.content) if validate_token(token)]
        if tokens and message.author.id != self.bot.user.id:
            url = await self.create_gist('\n'.join(tokens), description='Discord Bot Tokens detected')
            msg = f'{message.author.mention}, I have found tokens and sent them to <{url}> to be invalidated for you.'
            return await message.channel.send(msg)

        if message.author.bot:
            return

        for match in CodeSnippet.open(self.bot.session, message):
            if match.base is None:
                continue
            await match.post()

    @commands.Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        if thread.parent_id != PH_HELP_FORUM:
            return

        if len(thread.name) <= 20:
            low_quality_title = (
                '<:warning:1113421726861238363> **Warning**\n'
                'This thread has been automatically closed due to a potentially low quality title. '
                'Please reopen your thread with a more specified Title, to experience the best help.\n\n'
                '*Consider, your title should be more than `20` Characters.*'
            )
            try:
                await thread.send(content=thread.owner.mention,
                                  embed=discord.Embed(title="Thread Closed",
                                                      description=low_quality_title,
                                                      color=discord.Color.yellow()))
            except discord.Forbidden as e:
                if e.code == 40058:
                    await asyncio.sleep(2)
                    await thread.send(low_quality_title)
            finally:
                await thread.edit(archived=True, locked=True, reason='Low quality title.')
            return

        message = thread.get_partial_message(thread.id)
        try:
            await message.pin()
            await thread.send(inspect.cleandoc(
                """
                ### Welcome to the Help Forum!
                Please be patient and don't unnecessarily ping people while you're waiting for someone to help with your problem.
                Once you solved your problem, please close this thread by invoking `?solved`.
                """
            ))
        except discord.HTTPException:
            pass

    def format_fields(self, mapping: Mapping[str, Any], field_width: int | None = None) -> str:
        """Format a mapping to be readable to a human."""
        fields = sorted(mapping.items(), key=lambda item: item[0])

        if field_width is None:
            field_width = len(max(mapping.keys(), key=len))

        out = ""

        for key, val in fields:
            if isinstance(val, dict):
                inner_width = int(field_width * 1.6)
                val = "\n" + self.format_fields(val, field_width=inner_width)

            elif isinstance(val, str):
                text = textwrap.fill(val, width=100, replace_whitespace=False)
                val = textwrap.indent(text, " " * (field_width + len(": ")))
                val = val.lstrip()

            if key == "color":
                val = hex(val)

            out += "{0:>{width}}: {1}\n".format(key, val, width=field_width)

        return out.rstrip()

    async def send_raw_content(self, ctx: Context, message: discord.Message, json: bool = False) -> None:
        """Send information about the raw API response for a `discord.Message`.

        If `json` is True, send the information in a copy-pasteable Python format.
        """

        if not message.channel.permissions_for(ctx.author).read_messages:
            await ctx.send(f"{ctx.tick(False)} You do not have permissions to see the channel this message is in.")
            return

        raw_data = await ctx.bot.http.get_message(message.channel.id, message.id)
        paginator = TextSource(prefix="```json", suffix="```")

        def add_content(title: str, content: str) -> None:
            paginator.add_line(f"== {title} ==\n")
            paginator.add_line(content.replace("`", "`\u200b"))
            paginator.close_page()

        if message.content:
            add_content("Raw message", message.content)

        transformer = pprint.pformat if json else self.format_fields
        for field_name in ("embeds", "attachments"):
            data = raw_data[field_name]

            if not data:
                continue

            total = len(data)
            for current, item in enumerate(data, start=1):
                title = f"Raw {field_name} ({current}/{total})"
                add_content(title, transformer(item))

        for page in paginator.pages:
            await ctx.send(page)

    @command(commands.command, description="Shows information about the raw API response for a message.")
    async def raw(self, ctx: Context, message: discord.Message, to_json: bool = False) -> None:
        """Shows information about the raw API response."""
        await self.send_raw_content(ctx, message, json=to_json)

    @command(commands.command, aliases=("snf", "snfl", "sf"))
    async def snowflake(self, ctx: Context, *snowflakes: Annotated[int, Snowflake]) -> None:
        """Get Discord snowflake creation time."""
        if not snowflakes:
            raise commands.BadArgument(f"{ctx.tick(False)} At least one snowflake must be provided.")

        lines = []
        for snowflake in snowflakes:
            created_at = discord.utils.snowflake_time(snowflake)
            lines.append(f"- **{snowflake}** • Created: {created_at} ({discord.utils.format_dt(created_at, 'R')})")

        await ctx.send("\n".join(lines))


async def setup(bot: Percy):
    await bot.add_cog(Base(bot))
