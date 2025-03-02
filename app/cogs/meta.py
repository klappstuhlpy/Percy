from __future__ import annotations

import asyncio
import base64
import binascii
import inspect
import io
import logging
import pprint
import re
import textwrap
from urllib.parse import urljoin

import aiohttp
import unicodedata
from collections import Counter
from typing import TYPE_CHECKING, ClassVar, Final, Any, Literal

import discord
from dateutil.relativedelta import relativedelta
from discord import app_commands, File
from discord.ext import commands, tasks

import config
from app.core import Cog, Context, Flags, flag, Bot
from app.core.models import PermissionSpec, command, cooldown, describe, group, guilds
from app.core.views import View, TrashView
from app.rendering import get_dominant_color, Quote
from app.utils import AnsiColor, AnsiStringBuilder, Timer, get_asset_url, helpers, humanize_small_duration, pluralize, \
    format_fields, RelativeDelta
from app.utils.lock import lock
from app.utils.pagination import LinePaginator, TextSource, TextSourcePaginator
from config import main_guild_id, Emojis, github_key, default_prefix, test_guild_id

if TYPE_CHECKING:
    import datetime

    from app.database.base import GuildConfig

log = logging.getLogger(__name__)


class GuildUserJoinView(View):
    def __init__(self, author: discord.Member) -> None:
        super().__init__(timeout=60.0, members=author)

    @discord.ui.button(label='Join List', style=discord.ButtonStyle.blurple, emoji=Emojis.join)
    async def join_list(self, interaction: discord.Interaction, _) -> None:
        chunked_users = sorted(await interaction.guild.chunk(), key=lambda m: m.joined_at)
        chunked = [[position, user, user.joined_at]
                   for position, user in enumerate(chunked_users, start=1)]

        def fmt(p: int, u: discord.Member, j: datetime.datetime) -> str:
            return f'`{p}.` **{u}** ({discord.utils.format_dt(j, style='f')})'

        source = TextSource(prefix=None, suffix=None, max_size=4000)
        for line in chunked:
            source.add_line(fmt(*line))

        embed = discord.Embed(title=f'Join List in {interaction.guild}', colour=helpers.Colour.white())
        embed.set_author(name=interaction.guild, icon_url=get_asset_url(interaction.guild))
        embed.set_footer(text=f'{pluralize(len(chunked_users)):entry|entries}')

        self.disable_all()
        await interaction.response.edit_message(view=self)
        await LinePaginator.start(interaction, entries=source.pages, per_page=1, location='description')
        self.stop()


help_forum_id = 1079786704862445668
solved_tag_id = 1079787335803207701

TOKEN_REGEX = re.compile(r'[a-zA-Z0-9_-]{23,28}\.[a-zA-Z0-9_-]{6,7}\.[a-zA-Z0-9_-]{27,}')
GITHUB_URL_REGEX = re.compile(r'https?://(?:www\.)?github\.com/[^/\s]+/[^/\s]+(?:/[^/\s]+)*/?')


class UnsolvedFlags(Flags):
    messages: int = flag(
        default=5, description='The maximum number of messages the thread needs to be considered active. Defaults to 5.'
    )
    threshold: relativedelta = flag(
        default=relativedelta(minutes=5),
        description='How old the thread needs to be (e.g. "10m" or "22m"). Defaults to 5 minutes.',
        converter=RelativeDelta,
    )


def validate_token(token: str) -> bool:
    """Validate a Discord token using base64 decoding.

    Returns
    -------
    bool
        Whether the token is valid.
    """
    try:
        (user_id, _, _) = token.split('.')
        _ = int(base64.b64decode(user_id + '==', validate=True))
    except (ValueError, binascii.Error):
        return False
    return True


def can_close_threads(ctx: Context) -> bool:
    if not isinstance(ctx.channel, discord.Thread):
        return False

    permissions = ctx.channel.permissions_for(ctx.author)
    return ctx.channel.parent_id == help_forum_id and (permissions.manage_threads or ctx.channel.owner_id == ctx.author.id)


def is_help_thread() -> commands.core.Check:
    def predicate(ctx: Context) -> bool:
        if isinstance(ctx.channel, discord.Thread) and ctx.channel.parent_id == help_forum_id:
            return True
        raise commands.CommandError('This command can only be used in help threads.')

    return commands.check(predicate)


class GithubError(commands.BadArgument):
    """Base exception for GitHub errors."""
    pass


class Meta(Cog):
    """Commands for utilities related to Discord or the Bot itself."""

    emoji = '<a:staff_animated:1322337965774602313>'

    MENTION_REGEX: Final[ClassVar[re.Pattern[str]]] = re.compile(r'<@!?\d+>')

    GITHUB_RE: Final[ClassVar[re.Pattern]] = re.compile(
        r'https://github\.com/(?P<repo>[a-zA-Z0-9-]+/[\w.-]+)/blob/'
        r'(?P<path>[^#>]+)(\?[^#>]+)?(#L(?P<start_line>\d+)(([-~:]|(\.\.))L(?P<end_line>\d+))?)?'
    )
    GITHUB_GIST_RE: Final[ClassVar[re.Pattern]] = re.compile(
        r'https://gist\.github\.com/([a-zA-Z0-9-]+)/(?P<gist_id>[a-zA-Z0-9]+)/*'
        r'(?P<revision>[a-zA-Z0-9]*)/*#file-(?P<file_path>[^#>]+?)(\?[^#>]+)?'
        r'(-L(?P<start_line>\d+)([-~:]L(?P<end_line>\d+))?)'
    )

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.bot.loop.create_task(self._prepare_invites())
        self.auto_archive_old_forum_threads.start()

        self.pattern_handlers = [
            (self.GITHUB_RE, self._fetch_github_snippet),
            (self.GITHUB_GIST_RE, self._fetch_github_gist_snippet),
        ]

    def cog_unload(self) -> None:
        self.auto_archive_old_forum_threads.cancel()

    async def _prepare_invites(self) -> None:
        await self.bot.wait_until_ready()
        guild = self.bot.get_guild(main_guild_id)

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

        rep = await self.github_request(
            'GET', f'repos/{repo}/contents/{file_path}?ref={ref}', headers={'Accept': 'application/vnd.github+json'}
        )

        dbytes = base64.b64decode(rep['content'])
        file_contents = dbytes.decode('utf-8')

        return self._snippet_to_codeblock(file_contents, file_path, repo, start_line, end_line)

    async def _fetch_github_gist_snippet(
            self, gist_id: str, revision: str, file_path: str, start_line: str, end_line: str
    ) -> tuple[str, str | File] | None:
        """Fetches a snippet from a GitHub gist."""
        gist_json = await self.github_request(
            'GET',
            f'gists/{gist_id}{f'/{revision}' if len(revision) > 0 else ''}',
            headers={'Accept': 'application/vnd.github+json'},
        )

        for gist_file in gist_json['files']:
            if file_path == gist_file.lower().replace('.', '-'):
                url = gist_json['files'][gist_file]['raw_url']
                async with self.bot.session.request('GET', url) as resp:
                    if resp.status != 200:
                        raise GithubError(
                            f'Fetching snippet from GitHub gist returned Status Code `{resp.status}` with {resp.reason!r}.'
                        )

                    file_contents = await resp.text()
                title = gist_json['files'][gist_file]['title']
                return self._snippet_to_codeblock(file_contents, gist_file, title, start_line, end_line)
        return None

    @staticmethod
    def _snippet_to_codeblock(
            file_contents: str, file_path: str, full_url: str, start_line: str, end_line: str
    ) -> tuple[str, str | File] | None:
        """Given the entire file contents and target lines, creates a code block.

        First, we split the file contents into a list of lines and then keep and join only the required
        ones together.

        We then dedent the lines to look nice, and replace all characters with '\u200b' to prevent
        markdown injection.

        Finally, we surround the code with '```' characters.
        """
        split_file_contents = file_contents.splitlines()

        if end_line is None:
            end_line = len(split_file_contents) if start_line is None else int(start_line)

        if start_line is None:
            start_line = 1

        start_line = int(start_line)
        end_line = int(end_line)

        start_line = max(1, start_line)
        end_line = min(len(split_file_contents), end_line)

        required = '\n'.join(split_file_contents[start_line - 1: end_line])
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
        return None

    async def _parse_snippets(self, content: str) -> list[tuple[str, str | File]]:
        """Parse message content and return a string with a code block for each URL found."""
        all_snippets = []

        for pattern, handler in self.pattern_handlers:
            for match in pattern.finditer(content):
                try:
                    snippet = await handler(**match.groupdict())
                    if snippet is None:
                        continue
                    all_snippets.append((match.start(), snippet))
                except aiohttp.ClientResponseError as error:
                    error_message = error.message
                    log.log(
                        logging.DEBUG if error.status == 404 else logging.ERROR,
                        f'Failed to fetch code snippet from {match[0]!r}: {error.status} '  # noqa: G004
                        f'{error_message} for GET {error.request_info.real_url.human_repr()}',
                    )

        return [x[1] for x in sorted(all_snippets)]

    @lock('Base', 'github_request', wait=True)
    async def github_request(
            self,
            method: str,
            url: str,
            *,
            params: dict[str, Any] | None = None,
            data: dict[str, Any] | None = None,
            headers: dict[str, Any] | None = None,
            return_type: Literal['json', 'text'] = 'json',
    ) -> Any:
        """|coro|

        Sends a request to the GitHub API.

        Parameters
        ----------
        method: :class:`str`
            The HTTP method to use.
        url: :class:`str`
            The URL to send the request to.
        params: :class:`dict`
            The parameters to pass to the request.
        data: :class:`dict`
            The data to pass to the request.
        headers: :class:`dict`
            The headers to pass to the request.
        return_type: Literal[:class:`str`, :class:`str`]
            The type of response to return.

        Returns
        -------
        Any
            The JSON response from the API.
        """
        hdrs = {
            'Accept': 'application/vnd.github.inertia-preview+json',
            'User-Agent': 'Percy Exclusives',
            'Authorization': f'Bearer {github_key}',
        }

        req_url = urljoin('https://api.github.com', url)

        if headers is not None and isinstance(headers, dict):
            hdrs.update(headers)

        async with self.bot.session.request(method, req_url, params=params, json=data, headers=hdrs) as r:
            remaining = r.headers.get('X-Ratelimit-Remaining')
            js = await r.json()
            if r.status == 429 or remaining == '0':
                delta = discord.utils._parse_ratelimit_header(r)
                await asyncio.sleep(delta)
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
            description: str | None = None,
            filename: str | None = None,
            public: bool = True,
    ) -> str:
        """|coro|

        Creates a gist on GitHub.

        Parameters
        ----------
        content: :class:`str`
            The content of the gist.
        description: :class:`str`
            The description of the gist.
        filename: :class:`str`
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
    async def auto_archive_old_forum_threads(self) -> None:
        """|coro|

        Automatically archives old threads in the Help Forum.
        This is done to prevent the Help Forum from being cluttered with old threads.
        This task runs every hour.
        """
        guild = self.bot.get_guild(main_guild_id)
        if guild is None:
            return

        forum: discord.ForumChannel = guild.get_channel(help_forum_id)  # type: ignore
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
    async def before_auto_archive_old_forum_threads(self) -> None:
        await self.bot.wait_until_ready()

    @Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if not message.guild or message.guild.id not in (main_guild_id, test_guild_id):
            return

        tokens = [token for token in TOKEN_REGEX.findall(message.content) if validate_token(token)]
        if tokens and message.author.id != self.bot.user.id:
            url = await self.create_gist('\n'.join(tokens), description='Discord Bot Tokens detected')
            msg = f'{message.author.mention}, I have found tokens and sent them to <{url}> to be invalidated for you.'
            await message.channel.send(msg)
            return

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

    @Cog.listener()
    async def on_thread_create(self, thread: discord.Thread) -> None:
        if thread.parent_id != help_forum_id:
            return

        if len(thread.name) <= 20:
            embed = discord.Embed(
                title='Thread Closed',
                description=(
                    f'{Emojis.warning} **Warning**\n'
                    'This thread has been automatically closed due to a potentially low quality title. '
                    'Please reopen your thread with a more specified Title, to experience the best help.\n\n'
                    '*Consider, your title should be more than `20` Characters.*'
                ),
                colour=discord.Color.yellow(),
            )
            try:
                await thread.send(content=thread.owner.mention, embed=embed)
            except discord.Forbidden as e:
                if e.code == 40058:
                    await asyncio.sleep(2)
                    await thread.send(embed.description)
            finally:
                await thread.edit(archived=True, locked=True, reason='Low quality title.')
            return

        message = thread.get_partial_message(thread.id)
        try:
            await message.pin()
            await thread.send(
                inspect.cleandoc(
                    f"""
                    ### Welcome to the Help Forum!
                    Please be patient and don't unnecessarily ping people while you're waiting for someone to help with your problem.
                    Once you solved your problem, please close this thread by invoking `{default_prefix}solved`.
                    """
                )
            )
        except discord.HTTPException:
            pass

    @staticmethod
    async def mark_as_solved(thread: discord.Thread, user: discord.abc.User) -> None:
        tags: list[discord.ForumTag] = thread.applied_tags

        if not any(tag.id == solved_tag_id for tag in tags):
            tags.append(discord.Object(id=solved_tag_id))  # type: ignore

        await thread.edit(
            locked=True,
            archived=True,
            applied_tags=tags[:5],
            reason=f'Marked as solved by {user} (ID: {user.id})',
        )

    @command(
        'solved',
        description='Marks a thread as solved.',
        guild_only=True,
        hybrid=True,
    )
    @guilds(main_guild_id, test_guild_id)
    @cooldown(1, 5, commands.BucketType.member)
    @is_help_thread()
    async def solved(self, ctx: Context) -> None:
        """Marks a thread as solved."""
        if not isinstance(ctx.channel, discord.Thread):
            await ctx.send_error('This command can only be used in help threads.')
            return

        if can_close_threads(ctx):
            await ctx.message.add_reaction(Emojis.success)
            await self.mark_as_solved(ctx.channel, ctx.author._user)
        else:
            msg = f'<@!{ctx.channel.owner_id}>, would you like to mark this thread as solved? This has been requested by {ctx.author.mention}.'
            confirm = await ctx.confirm(msg, author_id=ctx.channel.owner_id, timeout=300.0)

            if ctx.channel.locked:
                return

            if confirm:
                await ctx.send_success(
                    f'Marking as solved. Note that next time, you can mark the thread as solved yourself with `{default_prefix}solved`.'
                )
                await self.mark_as_solved(ctx.channel, ctx.channel.owner._user or ctx.user)
            elif confirm is None:
                await ctx.send_error('Timed out waiting for a response. Not marking as solved.')
            else:
                await ctx.send_error('Not marking as solved.')

    @staticmethod
    async def send_raw_content(ctx: Context, message: discord.Message, json: bool = False) -> None:
        """Send information about the raw API response for a `discord.Message`.

        If `json` is True, send the information in a copy-pasteable Python format.
        """
        if not message.channel.permissions_for(ctx.author).read_messages:
            await ctx.send_error('You do not have permissions to see the channel this message is in.')
            return

        raw_data = await ctx.bot.http.get_message(message.channel.id, message.id)
        paginator = TextSourcePaginator(ctx, prefix='```json', suffix='```')

        def add_content(title: str, content: str) -> None:
            paginator.add_line(f'== {title} ==\n')
            paginator.add_line(content.replace('`', '`\u200b'))
            paginator.close_page()

        transformer = pprint.pformat if json else format_fields

        _dict_without_embeds_and_attachments = {k: v for k, v in raw_data.items() if k not in ('embeds', 'attachments')}
        add_content('Raw Message', transformer(_dict_without_embeds_and_attachments))

        for field in ('embeds', 'attachments'):
            data = raw_data[field]  # type: ignore
            if not data:
                continue

            for current, item in enumerate(data, start=1):
                title = f'Raw {field} ({current}/{len(data)})'
                add_content(title, transformer(item))

        await paginator.start()

    @command(description='Shows information about the raw API response for a message.')
    @describe(message='The message to get the raw API response for.',
              to_json='Whether to send the response in JSON format.')
    async def raw(self, ctx: Context, message: discord.Message, to_json: bool = False) -> None:
        """Shows information about the raw API response."""
        await self.send_raw_content(ctx, message, json=to_json)

    @command(aliases=['snf', 'snfl', 'sf'], description='Get the creation time of discord snowflakes.')
    @describe(snowflakes='The snowflakes to get the creation time for.')
    async def snowflake(self, ctx: Context, *snowflakes: int) -> None:
        """Get Discord snowflake creation time."""
        if not snowflakes:
            raise commands.BadArgument('At least one snowflake must be provided.')

        lines = []
        for snowflake in snowflakes:
            created_at = discord.utils.snowflake_time(snowflake)
            lines.append(f'- **{snowflake}** • Created: {created_at} ({discord.utils.format_dt(created_at, 'R')})')

        await ctx.send('\n'.join(lines))

    # @command(
    #     aliases=['src'],
    #     description='Shows parts of the Bots Source Command.'
    # )
    # @describe(command='The command to show the source of.')
    # async def source(self, ctx: Context, *, command: str | None = None):
    #     """Displays my full source code or for a specific command.
    #
    #     To display the source code of a subcommand, you can separate it by
    #     periods, e.g., tag.create for the creation subcommand of the tag command
    #     or by spaces.
    #     """
    #     if command is None:
    #         return await ctx.send(repo_url)
    #
    #     obj = self.bot.get_command(command)
    #     if obj is None:
    #         return await ctx.send_error('Could not find command.')
    #
    #     if command == 'help':
    #         src = type(self.bot.help_command)
    #         filename = inspect.getsourcefile(src)
    #     else:
    #         item = inspect.unwrap(obj.callback)
    #         src = item.__code__
    #         filename = src.co_filename
    #
    #     lines, firstlineno = inspect.getsourcelines(src)
    #     if filename is None:
    #         return await ctx.send_error('Could not find source for command.')
    #
    #     location_parts = filename.split(os.path.sep)
    #     cogs_index = location_parts.index('cogs')
    #     location = os.path.sep.join(location_parts[cogs_index:])  # Join parts from 'cogs' onwards
    #
    #     final_url = f'<{repo_url}blob/master/{location}#L{firstlineno}-L{firstlineno + len(lines) - 1}>'
    #
    #     embed = discord.Embed(title=f'Command: {command}', description=obj.description)
    #     embed.add_field(name='Source Code', value=f'[Jump to GitHub]({final_url})')
    #     embed.set_footer(text=f'{location}:{firstlineno}')
    #     await ctx.send(embed=embed)

    @app_commands.command(
        name='help',
        description='Get help for a command or module.'
    )
    @describe(module='Get help for a module.', command='Get help for a command')
    async def help(self, interaction: discord.Interaction, module: str | None = None, command: str | None = None) -> None:
        """Shows help for a command or module."""
        await interaction.response.defer()
        ctx: Context = await self.bot.get_context(interaction)
        if module or command:
            await ctx.send_help(module or command)
        else:
            await ctx.send_help()

    @command(
        'v2',
        description='Shows info about Percy-v2.'
    )
    async def v2(self, ctx: Context) -> None:
        """Shows info about Percy-v2."""
        embed = discord.Embed(
            title='Percy-v2',
            colour=helpers.Colour.white()
        )

        assert isinstance(config.owners, int)
        owner = ctx.bot.get_user(config.owners)

        embed.set_author(name=owner, icon_url=owner.display_avatar.url)
        embed.set_thumbnail(url=ctx.bot.user.display_avatar.url)

        embed.description = (
            'Percy has been rewritten from the ground up to be more efficient and faster.\n'
            'With improving the codebase, the bot has been made more stable and reliable for such as user interaction '
            'and command execution. The bots inner structure has been completely reworked to be more modular and '
            'flexible for future updates and features.\n\n'
            'The **v2** also brings some new features and improvements to the bot such as:\n'
            '- **Updated Casino/Game System**\n'
            ' * The Poker Games has been finished and should now be fully functional.\n'
            ' * The Economy system has been deployed to all minigames for more interaction and fun.\n'
            ' * New Minigames such as **Tower** or **Slot** have been added.\n'
            ' * The Hangman Games has been reworked and improved.\n'
            '- **Moderation:**\n'
            ' * You have now the possibility to use the so-called **Gatekeeper** functionality that acts as your '
            ' personal front door security for your server. This stops raiders and spammers from floating your server '
            ' by letting the users verify themselves by entering a captcha to gain access to the server.\n'
            ' * The **Moderation** commands have been reworked and improved, for example better *check* handling for '
            ' utility commands (ban, kick, mute, ...).\n'
            '- \N{SQUARED NEW} **Music**:\n'
            ' * The v2 version brings the Music System, previously known from my second bot (R. Hashira thats now '
            ' deprecated), to Percy.\n'
            ' * This brings also the *playlist* system to Percy, so you can now create and manage your own playlists.\n'
            '- **Error Handling:**\n'
            'But also the error handling has greatly improved. Command invokes that cause an error because of for example '
            'command parsing failures are now handeled entirely different by relying on the new **ANSI** command tracer '
            'that shows exactly what has been going wrong and where. This is especially useful for people with lesser '
            'knowledge about programming to make it easier to understand what went wrong and how to fix it.\n'
            '- **Updated Functionality**:\n'
            ' * Overall the entire internal command handling has been updated to support better check implementations '
            ' and also a proper permission handling that has been a little bit missing in the previous version.\n'
            ' * Updated Poll/Giveaway/Reminder and Tag System to be more reliable and efficient.\n'
            '- **Rendering**:\n'
            ' * The internal rendering done, for example for the leveling cards has been completly rewritten '
            ' to be more efficient and updated to new versions of the used libraries.\n'
            ' * The new Users Stats/History System that added the `presence`, `names` and `avatarhistory` commands '
            ' also uses newly written rendering functions.\n'
            '\n'
            '**This and much more small improvements and bug fixes have been made to the bot to make the experience '
            'with Percy even better. And also in the future there are going to be more updates and features to come.**\n\n'
            'This said, I would be happy if you help me improving issues by using the `feedback` command to submit '
            'bugs or even request new features. I am always open for new ideas and improvements.\n\n'
            'Thank you for using Percy!'
        )

        embed.set_footer(text='Percy-v2 - Changelog')
        await ctx.send(embed=embed, ephemeral=True)

    @command(
        'featureinfo',
        alias='fi',
        description='Shows the features of a guild.',
        hydra=True,
        guild_only=True
    )
    @describe(guild_id='The ID of the server to show info about. (Default: Current server)')
    async def featureinfo(self, ctx: Context, guild_id: str | None = None) -> None:
        """Shows the features of a guild."""
        if guild_id and not guild_id.isdigit():
            await ctx.send_error('Guild ID must be an int.')
            return

        guild_id = guild_id or ctx.guild.id
        guild = self.bot.get_guild(guild_id)
        if not guild:
            await ctx.send_error(f'Guild with ID `{guild_id}` not found.')
            return

        features = [f'**{e[0]}** - {e[1]}' for e in list(self.bot.get_guild_features(guild.features, only_current=True))]
        embed = discord.Embed(title='Guild Features',
                              timestamp=discord.utils.utcnow(),
                              color=helpers.Colour.white())
        embed.set_footer(text=f'{pluralize(len(features)):feature|features}')
        await LinePaginator.start(ctx, entries=features, per_page=12, embed=embed, location='description')

    @command(
        'serverinfo',
        alias='si',
        description='Shows info about a server.',
        hybrid=True,
        guild_only=True
    )
    @describe(guild_id='The ID of the server to show info about. (Default: Current server)')
    async def serverinfo(self, ctx: Context, guild_id: str | None = None) -> None:
        """Shows info about the current or a specified server."""
        if not guild_id or (guild_id and not await self.bot.is_owner(ctx.author)):
            if not ctx.guild:
                await ctx.send_error('You must specify a guild ID.')
                return
            guild = ctx.guild
        else:
            if not guild_id.isdigit():
                await ctx.send_error('Guild ID must be a number.')
                return
            guild = self.bot.get_guild(int(guild_id))

        if not guild:
            await ctx.send_error('Guild not found.')
            return

        roles = [role.name.replace('@', '@\u200b') for role in guild.roles]

        if not guild.chunked:
            async with ctx.channel.typing():
                await guild.chunk(cache=True)

        everyone = guild.default_role
        everyone_perms = everyone.permissions.value
        secret = Counter()
        totals = Counter()

        for channel in guild.channels:
            allow, deny = channel.overwrites_for(everyone).pair()
            perms = discord.Permissions((everyone_perms & ~deny.value) | allow.value)
            channel_type = type(channel)
            totals[channel_type] += 1
            if not perms.read_messages or isinstance(channel, discord.VoiceChannel) and (not perms.connect or not perms.speak):
                secret[channel_type] += 1

        embed = discord.Embed(
            title=guild.name,
            description=(
                f'**Name:** {guild.name}'
                f'**ID:** {guild.id}\n'
                f'**Owner:** {guild.owner}\n'
                f'**Auth:** {str(guild.verification_level).title()}\n'
            )
        )
        embed.set_thumbnail(url=get_asset_url(guild))

        channel_info = []
        key_to_emoji = {
            discord.TextChannel: '<:text_channel:1322355145182281748>',
            discord.VoiceChannel: '<:voice_channel:1322355161850445866>',
        }
        for key, total in totals.items():
            secrets = secret[key]
            try:
                emoji = key_to_emoji[key]
            except KeyError:
                continue

            if secrets:
                channel_info.append(f'{emoji} {total} (<:channel_locked:1322354602695327744> {secrets} locked)')
            else:
                channel_info.append(f'{emoji} {total}')

        embed.add_field(name='Features', value=f'Use `{ctx.clean_prefix}info features` to see the features of this server.')

        embed.add_field(name='Channels', value='\n'.join(channel_info))

        if guild.premium_tier != 0:
            boosts = f'Level {guild.premium_tier}\n{guild.premium_subscription_count} boosts'
            last_boost = max(guild.members, key=lambda m: m.premium_since or guild.created_at)
            if last_boost.premium_since is not None:
                boosts = f'{boosts}\nLast Boost: {last_boost} ({discord.utils.format_dt(last_boost.premium_since, style='R')})'
            embed.add_field(name='Boosts', value=boosts, inline=False)

        bots = sum(m.bot for m in guild.members)
        fmt = f'Total: {guild.member_count} ({pluralize(bots):bot} `{bots / guild.member_count:.2%}`)'

        embed.add_field(name='Members', value=fmt, inline=False)
        embed.add_field(name='Roles', value=', '.join(roles) if len(roles) < 10 else f'{len(roles)} roles')

        emoji_stats = Counter()
        for emoji in guild.emojis:
            if emoji.animated:
                emoji_stats['animated'] += 1
                emoji_stats['animated_disabled'] += not emoji.available
            else:
                emoji_stats['regular'] += 1
                emoji_stats['disabled'] += not emoji.available

        fmt = (
            f'Regular: {emoji_stats['regular']}/{guild.emoji_limit}\n'
            f'Animated: {emoji_stats['animated']}/{guild.emoji_limit}\n'
        )
        if emoji_stats['disabled'] or emoji_stats['animated_disabled']:
            fmt = f'{fmt}Disabled: {emoji_stats['disabled']} regular, {emoji_stats['animated_disabled']} animated\n'

        fmt = f'{fmt}Total Emoji: {len(guild.emojis)}/{guild.emoji_limit * 2}'
        embed.add_field(name='Emoji', value=fmt, inline=False)

        if guild.banner:
            embed.set_image(url=guild.banner.url)

        embed.set_footer(text='Created').timestamp = guild.created_at
        await ctx.send(embed=embed, view=GuildUserJoinView(ctx.author))

    @command(description='Shows the avatar of a user.', alias='av')
    @describe(user='The user to show the avatar of. (Default: You)')
    async def avatar(self, ctx: Context, *, user: discord.Member | discord.User = None) -> None:
        """Shows a user's enlarged avatar (if possible)."""
        user = user or ctx.author
        avatar = user.display_avatar.with_static_format('png')
        embed = discord.Embed(colour=discord.Colour.from_rgb(*get_dominant_color(io.BytesIO(await avatar.read()))))
        embed.set_author(name=str(user), url=avatar)
        embed.set_image(url=avatar)
        await ctx.send(embed=embed)

    @command(name='quote', alias='q', description='Quotes a message by a user.', hybrid=True)
    @describe(user='The user to quote.', message='The message to quote.')
    async def quote(
            self,
            ctx: Context,
            user: discord.Member | None = None,
            *,
            message: str | None = None
    ) -> None:
        """Quotes a message by a user."""
        if ctx.replied_message:
            user = ctx.replied_message.author
            message = ctx.replied_message.clean_content

        if not user or not message:
            await ctx.send_error('You must specify a user and a message, reply to a message or provide a message_id to quote.')
            return

        quote = Quote(await user.display_avatar.read(), message, user)
        await ctx.send(file=quote.create())

    @command(name='appinfo', aliases=['ai'], description='Shows information about a discord application.')
    @describe(app_id='The ID of the application to show info about.')
    async def appinfo(self, ctx: Context, *, app_id: str) -> None:
        """Displays information about a discord application."""
        if not re.match(r'[0-9]{17,19}', app_id):
            await ctx.send_error('Invalid Application ID')
            return

        try:
            application = await ctx.bot.fetch_application(int(app_id))
        except discord.NotFound:
            await ctx.send_error('Application not found.')
            return

        embed = discord.Embed(
            title=f'{application.name} (ID: {application.id})',
            description=application.description
        )
        if application.icon:
            embed.set_thumbnail(url=application.icon.url)

        bot_info = (
            f'App is public: `{application.bot_public}`\n'
            f'App requires code grant: `{application.bot_require_code_grant}`\n'
            f'Is monetized: `{application.is_monetized}`\n'
        )
        if application.guild_id:
            bot_info += f'Guild ID: `{application.guild_id}`\n'
        if application.custom_install_url:
            bot_info += f'Custom Install URL: `{application.custom_install_url}`\n'
        if application.category_ids:
            bot_info += f'App Directory Category IDs: `{', '.join(map(str, application.category_ids))}`\n'

        embed.add_field(name='Bot', value=bot_info, inline=False)

        if application.tags:
            embed.add_field(name='Tags', value=', '.join(f'`{tag}`' for tag in application.tags), inline=False)
        if application.flags:
            embed.add_field(
                name='Flags',
                value=', '.join(f'`{flag.replace('_', ' ').title()}`' for flag, enabled in application.flags if enabled),
                inline=False)

        if application.install_params:
            embed.add_field(
                name='Install Params',
                value=f'Scopes: `{', '.join(application.install_params.scopes)}`\n'
                      f'Permissions: `{application.install_params.permissions}`',
                inline=False
            )

        if application.permissions:
            perms = application.permissions.value
            embed.add_field(
                name='Permissions',
                value=f'[`{perms}`](https://discordapi.com/permissions.html#{perms})',
                inline=False
            )

        if application.terms_of_service_url or application.privacy_policy_url:
            text = ''
            if application.terms_of_service_url:
                text += f'[Terms of Service]({application.terms_of_service_url})\n'
            if application.privacy_policy_url:
                text += f'[Privacy Policy]({application.privacy_policy_url})\n'
            embed.add_field(name='Links', value=text, inline=False)

        assets = await application.get_assets()
        if assets:
            embed.add_field(
                name='Assets',
                value='\n'.join([f'[{asset.name}]({asset.url})' for asset in assets]),
                inline=False
            )

        await ctx.send(embed=embed)

    @command(
        'charinfo',
        description='Shows you information about a number of characters.',
    )
    @describe(characters='A String of characters that should be introspected.')
    async def charinfo(self, ctx: Context, *, characters: str) -> None:
        """Shows you information on up to 50 unicode characters."""
        match = re.match(r'<(a?):(\w+):(\d+)>', characters)
        if match:
            raise commands.BadArgument('Cannot get information on custom emoji.')

        if len(characters) > 50:
            raise commands.BadArgument(f'Too many characters ({len(characters)}/50)')

        def char_info(char: str) -> tuple[str, str]:
            digit = f'{ord(char):x}'
            u_code = f'\\u{digit:>04}' if len(digit) <= 4 else f'\\U{digit:>08}'
            url = f'https://www.compart.com/en/unicode/U+{digit:>04}'
            name = f'[{unicodedata.name(char, '')}]({url})'
            info = f'`{u_code.ljust(10)}`: {name} - {discord.utils.escape_markdown(char)}'
            return info, u_code

        char_list, raw_list = zip(*(char_info(c) for c in characters), strict=True)
        embed = discord.Embed(title='Char Info', colour=helpers.Colour.white())

        if len(characters) > 1:
            embed.add_field(name='Full Text', value=f'`{''.join(raw_list)}`', inline=False)

        await LinePaginator.start(ctx, entries=char_list, per_page=10, embed=embed, location='description')

    @group(
        'prefix',
        description='Manages or show the server\'s custom prefixes.',
        invoke_without_command=True,
        guild_only=True
    )
    async def prefix(self, ctx: Context) -> None:
        """Manages the server's custom prefixes."""
        config: GuildConfig = await self.bot.db.get_guild_config(ctx.guild.id)
        prefixes = config.prefixes.copy()
        prefixes.add(ctx.bot.user.mention)  # mention will always be available
        prefixes = sorted(prefixes, key=len, reverse=True)

        embed = discord.Embed(title='Prefix List', colour=helpers.Colour.white())
        embed.set_author(name=ctx.guild.name, icon_url=get_asset_url(ctx.guild))
        embed.set_thumbnail(url=get_asset_url(ctx.guild))
        embed.set_footer(text=f'{pluralize(len(prefixes)):prefix}')
        embed.description = '\n'.join(f'`{index}.` {elem}' for index, elem in enumerate(prefixes, 1))
        await ctx.send(embed=embed)

    @prefix.command(
        'add',
        description='Appends a prefix to the list of custom prefixes.',
        ignore_extra=False,
        guild_only=True,
        aliases=['append', 'create', '+', 'update', 'new'],
        user_permissions=['manage_guild']
    )
    @describe(prefixes='The prefixes to add.')
    async def prefix_add(self, ctx: Context, *prefixes: str) -> None:
        """Adds a prefix to the list of custom prefixes.
        **Multi-word prefixes must be quoted.**
        """
        if not prefixes:
            raise commands.BadArgument('You must specify at least one prefix.')

        config: GuildConfig = await self.bot.db.get_guild_config(ctx.guild.id)

        if len(prefixes) + len(config.prefixes) > 25:
            raise commands.BadArgument('You cannot have more than 25 prefixes.')

        if any(self.MENTION_REGEX.search(prefix) for prefix in prefixes):
            raise commands.BadArgument('You cannot use mentions as prefixes.')

        if any(len(prefix) > 100 for prefix in prefixes):
            raise commands.BadArgument('Prefixes cannot be longer than 100 characters.')

        config.prefixes.update(prefixes)
        await config.update(prefixes=list(config.prefixes))

        if len(prefixes) == 1:
            await ctx.send_success(f'Added {prefixes[0]} as a prefix.')
        else:
            await ctx.send_success(f'Added **{len(prefixes)}** prefixes.')

    @prefix.command(
        'remove',
        aliases=['delete', 'del', 'rm', '-'],
        ignore_extra=False,
        guild_only=True,
        user_permissions=['manage_guild']
    )
    @describe(prefixes='The prefixes to remove.')
    async def prefix_remove(self, ctx: Context, *prefixes: str) -> None:
        """Removes a prefix from the list of custom prefixes.
        You can use this to remove prefixes from the default set as well.
        """
        if not prefixes:
            raise commands.BadArgument('You must specify at least one prefix.')

        config: GuildConfig = await self.bot.db.get_guild_config(ctx.guild.id)

        updated = [prefix for prefix in config.prefixes if prefix not in prefixes]

        if len(updated) == len(config.prefixes):
            raise commands.BadArgument('None of the prefixes you specified are custom prefixes.')

        diff = len(config.prefixes) - len(updated)
        await config.update(prefixes=updated)

        if len(prefixes) == 1:
            await ctx.send_success(f'Removed {prefixes[0]} as a prefix.')
        else:
            await ctx.send_success(f'Removed **{diff}** prefixes.')

    @prefix.command(
        'reset',
        description='Removes all custom prefixes.',
        ignore_extra=False,
        guild_only=True,
        aliases=['clear', 'wipe', 'purge'],
        user_permissions=['manage_guild']
    )
    async def prefix_reset(self, ctx: Context) -> None:
        """Removes all custom prefixes.
        **After this, the bot will listen to only mention prefixes.**
        """
        config: GuildConfig = await self.bot.db.get_guild_config(ctx.guild.id)
        await config.update(prefixes=[])
        await ctx.send_success('Cleared all prefixes.')

    @command(
        name='vote',
        alias='v',
        description='Shows the vote link for the bot.',
        hybrid=True
    )
    @cooldown(1, 3)
    async def vote(self, ctx: Context) -> None:
        """Shows the vote link for the bot."""
        await ctx.send('You can vote for me on Top.gg [here](https://top.gg/bot/1070054930125176923/)!')

    @staticmethod
    def _ping_metric(latency: float, bad: float, good: float) -> AnsiColor:
        if latency > bad:
            return AnsiColor.red
        if latency < good:
            return AnsiColor.green
        return AnsiColor.yellow

    @command(aliases=('pong', 'latency'), hybrid=True)
    @cooldown(rate=2, per=3)
    async def ping(self, ctx: Context) -> None:
        """Pong! Sends detailed information about the bot's latency."""
        with Timer() as api:
            await ctx.typing()

        with Timer() as database:
            await ctx.db.execute('SELECT 1')

        api = api.milliseconds
        database = database.milliseconds
        ws = ctx.bot.latency

        round_trip = api + database + ws
        result = AnsiStringBuilder()

        result.append('Pong! ', color=AnsiColor.white, bold=True)
        result.append(humanize_small_duration(round_trip) + ' ', color=self._ping_metric(round_trip, 1, 0.4), bold=True)
        result.append('(Round-trip)', color=AnsiColor.gray).newline(2)

        result.append('API:      ', color=AnsiColor.gray)
        result.append(humanize_small_duration(api), color=self._ping_metric(api, 0.7, 0.3), bold=True).newline()

        result.append('Gateway:  ', color=AnsiColor.gray)
        result.append(humanize_small_duration(ws), color=self._ping_metric(ws, 0.25, 0.1), bold=True).newline()

        result.append('Database: ', color=AnsiColor.gray)
        result.append(humanize_small_duration(database), color=self._ping_metric(database, 0.25, 0.1), bold=True)

        result = result.ensure_codeblock().dynamic(ctx)
        await ctx.send(result)

    @staticmethod
    async def say_permissions(
            ctx: Context, member: discord.Member, channel: discord.abc.GuildChannel | discord.Thread
    ) -> None:
        fmt = PermissionSpec.permission_as_str
        permissions = channel.permissions_for(member)
        embed = discord.Embed(
            title=f'Permissions for {member} in {channel.name}',
            colour=member.colour
        )
        allowed, denied = AnsiStringBuilder(), AnsiStringBuilder()
        for name, value in permissions:
            if value:
                allowed.append(fmt(name), color=AnsiColor.green).newline()
            else:
                denied.append(fmt(name), color=AnsiColor.red).newline()

        allowed = allowed.ensure_codeblock().dynamic(ctx)
        denied = denied.ensure_codeblock().dynamic(ctx)
        embed.add_field(name='Allowed', value=allowed)
        embed.add_field(name='Denied', value=denied)
        await ctx.send(embed=embed)

    @group(
        'permissions',
        aliases=['perms', 'permsfor'],
        description='Shows permissions for a member or the bot in a specific channel.',
        guild_only=True
    )
    async def permissions(self, ctx: Context) -> None:
        """Shows permissions for a member or the bot in a specific channel."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @permissions.command(
        'user',
        description='Shows a member\'s permissions in a specific channel.',
        guild_only=True
    )
    @describe(member='The member to show the permissions of.', channel='The channel to show the permissions in.')
    async def permissions_user(
            self,
            ctx: Context,
            member: discord.Member = None,
            channel: discord.abc.GuildChannel | discord.Thread = None,
    ) -> None:
        """Shows a member's permissions in a specific channel.
        If no channel is given then it uses the current one.
        You cannot use this in private messages. If no member is given then
        the info returned will be yours.
        """
        channel = channel or ctx.channel
        member = member or ctx.author
        await self.say_permissions(ctx, member, channel)

    @permissions.command(
        'bot',
        description='Shows the bot\'s permissions in a specific channel.',
        guild_only=True
    )
    @describe(channel='The channel to show the permissions in.')
    async def permissions_bot(
            self, ctx: Context, *, channel: discord.abc.GuildChannel | discord.Thread = None) -> None:
        """Shows the bots permissions in a specific channel.
        If no channel is given then it uses the current one.
        This is a good way of checking if the bot has the permissions needed
        to execute the commands it wants to execute.
        """
        channel = channel or ctx.channel
        await self.say_permissions(ctx, ctx.guild.me, channel)


async def setup(bot) -> None:
    await bot.add_cog(Meta(bot))
