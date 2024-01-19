from __future__ import annotations

import asyncio
import base64
import binascii
import datetime
import inspect
import io
import logging
import pprint
import textwrap
from typing import TYPE_CHECKING, Optional, Any, Annotated, Mapping, Literal
from urllib.parse import urljoin

import aiohttp
import discord
from discord import File
from discord.ext import commands, tasks

from .utils import commands, errors
from .utils.converters import Snowflake
from .utils.paginator import TextSource
from .utils.constants import GITHUB_RE, GITHUB_GIST_RE, PH_GUILD_ID, PH_BOTS_ROLE, PH_HELP_FORUM, TOKEN_REGEX, \
    PLAYGROUND_GUILD_ID, PH_MEMBERS_ROLE
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
        style=discord.ButtonStyle.red, emoji=discord.PartialEmoji(name='trashcan', id=1118870793393291294),
        label='Delete', custom_id='delete'
    )
    async def delete(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
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
        user_id = int(base64.b64decode(user_id + '==', validate=True))  # noqa
    except (ValueError, binascii.Error):
        return False
    else:
        return True


class GithubError(errors.CommandError):
    """Base exception for GitHub errors."""
    pass


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

        self.pattern_handlers = [
            (GITHUB_RE, self._fetch_github_snippet),
            (GITHUB_GIST_RE, self._fetch_github_gist_snippet),
        ]

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='dpy', id=1079788056560795648)

    def cog_unload(self) -> None:
        self.auto_archive_old_forum_threads.cancel()

    async def _prepare_invites(self):
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(PH_GUILD_ID)

        if guild is not None:
            invites = await guild.invites()
            self._invite_cache = {invite.code: invite.uses or 0 for invite in invites}

    @staticmethod
    def _find_ref(path: str, refs: tuple) -> tuple:
        """Loops through all branches and tags to find the required ref."""
        # Base case: there is no slash in the branch name
        ref, file_path = path.split('/', 1)
        # In case there are slashes in the branch name, we loop through all branches and tags
        for possible_ref in refs:
            if path.startswith(possible_ref['name'] + '/'):
                ref = possible_ref['name']
                file_path = path[len(ref) + 1:]
                break
        return ref, file_path

    async def _fetch_github_snippet(
            self, repo: str, path: str, start_line: str, end_line: str
    ) -> tuple[str, str | File]:
        """Fetches a snippet from a GitHub repo."""
        branches = await self.github_request('GET', f'repos/{repo}/branches')
        tags = await self.github_request('GET', f'repos/{repo}/tags')
        refs = branches + tags
        ref, file_path = self._find_ref(path, refs)

        rep = await self.github_request('GET', f'repos/{repo}/contents/{file_path}?ref={ref}',
                                        headers={'Accept': 'application/vnd.github+json'})

        dbytes = base64.b64decode(rep['content'])
        file_contents = dbytes.decode('utf-8')

        return self._snippet_to_codeblock(file_contents, file_path, repo, start_line, end_line)  # rep['html_url']

    async def _fetch_github_gist_snippet(
            self,
            gist_id: str,
            revision: str,
            file_path: str,
            start_line: str,
            end_line: str
    ) -> tuple[str, str | File]:
        """Fetches a snippet from a GitHub gist."""
        gist_json = await self.github_request('GET', f'gists/{gist_id}{f'/{revision}' if len(revision) > 0 else ''}',
                                              headers={'Accept': 'application/vnd.github+json'})

        for gist_file in gist_json['files']:
            if file_path == gist_file.lower().replace('.', '-'):
                url = gist_json['files'][gist_file]['raw_url']
                async with self.bot.session.request('GET', url) as resp:
                    if resp.status != 200:
                        raise GithubError(
                            f'Fetching snippet from GitHub gist returned Status Code `{resp.status}` with {resp.reason!r}.')

                    file_contents = await resp.text()
                title = gist_json['files'][gist_file]['title']
                return self._snippet_to_codeblock(file_contents, gist_file, title, start_line, end_line)
        return '', 'File not found in gist.'

    @staticmethod
    def _snippet_to_codeblock(
            file_contents: str, file_path: str, full_url: str, start_line: str, end_line: str
    ) -> tuple[str, str | File]:
        """Given the entire file contents and target lines, creates a code block.

        First, we split the file contents into a list of lines and then keep and join only the required
        ones together.

        We then dedent the lines to look nice, and replace all ` characters with `\u200b to prevent
        markdown injection.

        Finally, we surround the code with ``` characters.
        """
        # Parse start_line and end_line into integers

        split_file_contents = file_contents.splitlines()

        if end_line is None:
            if start_line is None:
                end_line = len(split_file_contents)
            else:
                end_line = int(start_line)

        if start_line is None:
            start_line = 1

        start_line = int(start_line)
        end_line = int(end_line)

        start_line = max(1, start_line)
        end_line = min(len(split_file_contents), end_line)

        required = '\n'.join(split_file_contents[start_line - 1:end_line])
        required = textwrap.dedent(required).rstrip().replace('`', '`\u200b')

        # Extracts the code language and checks whether it's a 'valid' language
        language = file_path.split('/')[-1].split('.')[-1]
        trimmed_language = language.replace('-', '').replace('+', '').replace('_', '')
        is_valid_language = trimmed_language.isalnum()
        if not is_valid_language:
            language = ''

        # Adds a label showing the file path to the snippet
        if start_line == end_line:
            ret = f'`{file_path}` from `{full_url}` line `{start_line}`\n'
        else:
            ret = f'`{file_path}` from `{full_url}` lines `{start_line}` to `{end_line}`\n'

        if len(required) != 0:
            fmt = f'{ret}```{language}\n{required}```'
            if len(fmt) <= 2000:
                return ret, fmt
            else:
                return ret, discord.File(io.BytesIO(required.encode()), filename=file_path)
        # Returns an empty codeblock if the snippet is empty
        return ret, f'{ret}``` ```'

    async def _parse_snippets(self, content: str) -> list[tuple[str, str | File]]:
        """Parse message content and return a string with a code block for each URL found."""
        all_snippets = []

        for pattern, handler in self.pattern_handlers:
            for match in pattern.finditer(content):
                try:
                    snippet = await handler(**match.groupdict())
                    all_snippets.append((match.start(), snippet))
                except aiohttp.ClientResponseError as error:
                    error_message = error.message
                    log.log(
                        logging.DEBUG if error.status == 404 else logging.ERROR,
                        f'Failed to fetch code snippet from {match[0]!r}: {error.status} '
                        f'{error_message} for GET {error.request_info.real_url.human_repr()}'
                    )

        # Sorts the list of snippets by their match index and joins them into a single message
        return [x[1] for x in sorted(all_snippets)]

    async def github_request(
            self,
            method: str,
            url: str,
            *,
            params: Optional[dict[str, Any]] = None,
            data: Optional[dict[str, Any]] = None,
            headers: Optional[dict[str, Any]] = None,
            return_type: Literal['json', 'text'] = 'json'
    ) -> Any:
        """|coro|

        Sends a request to the GitHub API.

        Parameters
        ----------
        method: :class:`str`
            The HTTP method to use.
        url: :class:`str`
            The URL to send the request to.
        params: Optional[:class:`dict`]
            The parameters to pass to the request.
        data: Optional[:class:`dict`]
            The data to pass to the request.
        headers: Optional[:class:`dict`]
            The headers to pass to the request.
        return_type: Literal[:class:`str`, :class:`str`]
            The type of response to return.

        Returns
        -------
        Any
            The JSON response from the API.
        """
        hdrs = {'Accept': 'application/vnd.github.inertia-preview+json',
                'User-Agent': 'Percy DPY-Exclusives',
                'Authorization': f'Bearer {self.bot.config.github_key}'}

        req_url = urljoin('https://api.github.com', url)

        if headers is not None and isinstance(headers, dict):
            hdrs.update(headers)

        async with self._req_lock:
            async with self.bot.session.request(method, req_url, params=params, json=data, headers=hdrs) as r:
                remaining = r.headers.get('X-Ratelimit-Remaining')
                js = await r.json()
                if r.status == 429 or remaining == '0':
                    delta = discord.utils._parse_ratelimit_header(r)  # noqa
                    await asyncio.sleep(delta)
                    self._req_lock.release()
                    return await self.github_request(method, url, params=params, data=data, headers=headers)
                elif 300 > r.status >= 200:
                    if return_type == 'json':
                        return js
                    else:
                        return await r.text()
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
        """|coro|

        Creates a gist on GitHub.

        Parameters
        ----------
        content: :class:`str`
            The content of the gist.
        description: Optional[:class:`str`]
            The description of the gist.
        filename: Optional[:class:`str`]
            The filename of the gist.
        public: :class:`bool`
            Whether the gist should be public or not.

        Returns
        -------
        :class:`str`
            The URL of the gist.
        """
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

    @tasks.loop(hours=1)
    async def auto_archive_old_forum_threads(self):
        """|coro|

        Automatically archives old threads in the Help Forum.
        This is done to prevent the Help Forum from being cluttered with old threads.
        This task runs every hour.
        """
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

        snippets = await self._parse_snippets(message.content)

        for ret, snippet in snippets:
            try:
                await message.edit(suppress=True)
            except discord.NotFound:
                return

            if isinstance(snippet, discord.File):
                await message.channel.send(ret, file=snippet, view=TrashView(message.author))
            else:
                await message.channel.send(snippet, view=TrashView(message.author))

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
                                  embed=discord.Embed(title='Thread Closed',
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

        out = ''

        for key, val in fields:
            if isinstance(val, dict):
                inner_width = int(field_width * 1.6)
                val = '\n' + self.format_fields(val, field_width=inner_width)

            elif isinstance(val, str):
                text = textwrap.fill(val, width=100, replace_whitespace=False)
                val = textwrap.indent(text, ' ' * (field_width + len(': ')))
                val = val.lstrip()

            if key == 'color':
                val = hex(val)

            out += '{0:>{width}}: {1}\n'.format(key, val, width=field_width)

        return out.rstrip()

    async def send_raw_content(self, ctx: Context, message: discord.Message, json: bool = False) -> None:
        """Send information about the raw API response for a `discord.Message`.

        If `json` is True, send the information in a copy-pasteable Python format.
        """

        if not message.channel.permissions_for(ctx.author).read_messages:
            await ctx.stick(False, 'You do not have permissions to see the channel this message is in.')
            return

        raw_data = await ctx.bot.http.get_message(message.channel.id, message.id)
        paginator = TextSource(prefix='```json', suffix='```')

        def add_content(title: str, content: str) -> None:  # noqa
            paginator.add_line(f'== {title} ==\n')
            paginator.add_line(content.replace('`', '`\u200b'))
            paginator.close_page()

        if message.content:
            add_content('Raw message', message.content)

        transformer = pprint.pformat if json else self.format_fields
        for field_name in ('embeds', 'attachments'):
            data = raw_data[field_name]  # type: ignore

            if not data:
                continue

            total = len(data)
            for current, item in enumerate(data, start=1):
                title = f'Raw {field_name} ({current}/{total})'
                add_content(title, transformer(item))

        for page in paginator.pages:
            await ctx.send(page)

    @commands.command(commands.core_command, description='Shows information about the raw API response for a message.')
    async def raw(self, ctx: Context, message: discord.Message, to_json: bool = False) -> None:
        """Shows information about the raw API response."""
        await self.send_raw_content(ctx, message, json=to_json)

    @commands.command(commands.core_command, aliases=('snf', 'snfl', 'sf'))
    async def snowflake(self, ctx: Context, *snowflakes: Annotated[int, Snowflake]) -> None:
        """Get Discord snowflake creation time."""
        if not snowflakes:
            raise errors.BadArgument(f'At least one snowflake must be provided.')

        lines = []
        for snowflake in snowflakes:
            created_at = discord.utils.snowflake_time(snowflake)
            lines.append(f'- **{snowflake}** • Created: {created_at} ({discord.utils.format_dt(created_at, 'R')})')

        await ctx.send('\n'.join(lines))


async def setup(bot: Percy):
    await bot.add_cog(Base(bot))
