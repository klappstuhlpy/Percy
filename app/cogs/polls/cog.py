from __future__ import annotations

import warnings
from typing import TYPE_CHECKING, Any, Literal

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.utils import MISSING

from app.cogs.polls.models import Poll, VoteOption, to_emoji, uuid
from app.cogs.polls.ui import (
    EditModal,
    PollClearVoteButton,
    PollEnterButton,
    PollEnterSelect,
    PollInfoButton,
    PollRolePingButton,
    create_view,
)
from app.core import Bot, Cog, Flags, flag, store_true
from app.core.converter import ColorTransformer, ValidURL
from app.core.flags import MockFlags
from app.core.models import (
    Context,
    HybridContext,
    PermissionTemplate,
    cooldown,
    describe,
    group,
)
from app.core.pagination import BasePaginator, LinePaginator
from app.utils import cache, fuzzy, get_asset_url, get_shortened_string, helpers, pluralize, timetools
from config import Emojis

if TYPE_CHECKING:
    import datetime

    from discord.app_commands import Choice

    from app.core.timer import Timer
    from app.database.base import GuildConfig

warnings.simplefilter(action='ignore', category=FutureWarning)


class PollCreateFlags(Flags):
    description: str = flag(description='The description for the poll.')
    color: helpers.Colour | ColorTransformer = flag(
        description='The color for the poll.', converter=ColorTransformer, default=helpers.Colour.white())
    channel: discord.TextChannel = flag(description='The channel to send the poll to.')
    thread_question: str = flag(description='The question for the thread.')
    ping: bool | Any = store_true(description='Whether to ping the role or not.')
    with_reason: bool | Any = store_true(description='Whether to ask for a reason for the vote or not.')
    image: discord.Attachment = flag(description='The image for the poll.')
    image_url: str | ValidURL = flag(description='The image URL for the poll.', converter=ValidURL)

    opt_1: str = flag(description='The first option for the poll.', aliases=['option_1', 'opt1', '1'], required=True)
    opt_2: str = flag(description='The second option for the poll.', aliases=['option_2', 'opt2', '2'], required=True)
    opt_3: str = flag(description='The third option for the poll.', aliases=['option_3', 'opt3', '3'])
    opt_4: str = flag(description='The fourth option for the poll.', aliases=['option_4', 'opt4', '4'])
    opt_5: str = flag(description='The fifth option for the poll.', aliases=['option_5', 'opt5', '5'])
    opt_6: str = flag(description='The sixth option for the poll.', aliases=['option_6', 'opt6', '6'])
    opt_7: str = flag(description='The seventh option for the poll.', aliases=['option_7', 'opt7', '7'])
    opt_8: str = flag(description='The eighth option for the poll.', aliases=['option_8', 'opt8', '8'])


class PollEditFlags(Flags):
    question: str = flag(description='The new question to ask.')
    description: str = flag(description='The new description to use.')
    thread_question: str = flag(description='The new thread question to use.')
    image: discord.Attachment = flag(description='The new image to use.')
    image_url: str | ValidURL = flag(description='The new image URL to use.', converter=ValidURL)
    color: helpers.Colour | ColorTransformer = flag(description='The new color to use.', converter=ColorTransformer)

    opt_1: str = flag(description='Option 1.')
    opt_2: str = flag(description='Option 2.')
    opt_3: str = flag(description='Option 3.')
    opt_4: str = flag(description='Option 4.')
    opt_5: str = flag(description='Option 5.')
    opt_6: str = flag(description='Option 6.')
    opt_7: str = flag(description='Option 7.')
    opt_8: str = flag(description='Option 8.')


class PollSearchFlags(Flags):
    keyword: str = flag(description='The keyword to search for.')
    sort: Literal['id', 'new', 'old', 'most votes', 'least votes'] = flag(
        description='The sorting method to use.', default='new')
    active: bool | Any = store_true(description='Whether to search for active polls or not.')
    showextrainfo: bool | Any = store_true(description='Whether to show extra information or not.')


class PollConfigFlags(Flags):
    channel: discord.TextChannel = flag(description='The channel to send the poll to.')
    reason_channel: discord.TextChannel = flag(description='The channel to send the poll reasons to.')
    ping_role: discord.Role = flag(description='The role to ping for the polls.')
    reset: bool | Any = store_true(description='Whether to reset the poll settings or not.')


class Polls(Cog):
    """Poll voting system."""

    emoji = '\N{BAR CHART}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)

        self._message_cache: dict[int, discord.Message] = {}
        self.cooldown: commands.CooldownMapping = commands.CooldownMapping.from_cooldown(
            2, 5, lambda interaction: interaction.user)

        bot.add_dynamic_items(
            PollEnterButton, PollEnterSelect, PollClearVoteButton, PollInfoButton, PollRolePingButton)

    async def cog_load(self) -> None:
        self.cleanup_message_cache.start()

    @tasks.loop(hours=1.0)
    async def cleanup_message_cache(self) -> None:
        self._message_cache.clear()

    async def get_message(
            self,
            channel: discord.abc.Messageable,
            message_id: int
    ) -> discord.Message | None:
        try:
            return self._message_cache[message_id]
        except KeyError:
            try:
                msg = await channel.fetch_message(message_id)
            except discord.HTTPException:
                return None
            else:
                self._message_cache[message_id] = msg
                return msg

    async def poll_id_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[Choice[str | int | float]]:
        assert interaction.guild is not None
        polls = await self.get_guild_polls(interaction.guild.id)  # type: ignore[misc]

        assert interaction.command is not None
        if interaction.command.name in ('end', 'edit', 'debug'):
            polls = [poll for poll in polls if poll.kwargs.get('running') is True]

        results = fuzzy.finder(current, polls, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, poll.choice_text), value=poll.id)
            for length, start, poll in results[:20]
        ]

    async def create_poll(
            self,
            poll_id: int,
            channel_id: int,
            message_id: int,
            guild_id: int,
            expires: datetime.datetime,
            /,
            *args: Any,
            **kwargs: Any
    ) -> Poll:
        r"""Creates a poll.

        Parameters
        -----------
        poll_id
            The unqiue ID of the poll to manage it.
        channel_id
            The channel ID of the poll.
        message_id
            The message ID of the poll.
        guild_id
            The guild ID of the poll.
        expires
            The expiration date of the poll.
        \*args
            Arguments to pass to the event
        \*\*kwargs
            Keyword arguments to pass to the event
        Note
        ------
        Arguments and keyword arguments must be JSON serializable.

        Returns
        --------
        :class:`Poll`
            The created Poll if creation succeeded, otherwise ``None``.
        """
        published = discord.utils.utcnow()

        poll: Poll = Poll.temporary(
            cog=self,
            channel_id=channel_id,
            message_id=message_id,
            guild_id=guild_id,
            published=published,
            expires=expires,
            entries=set(),
            metadata={'args': args, 'kwargs': kwargs}
        )

        poll.id = await self.bot.db.polls.create(
            poll_id, channel_id, message_id, guild_id,
            published, expires, {'args': args, 'kwargs': kwargs})

        self.get_guild_polls.invalidate(guild_id)
        return poll

    async def get_guild_poll(self, guild_id: int, poll_id: int, /) -> Poll | None:
        """|coro|

        Parameters
        ----------
        guild_id: int
            The Guild ID to search in for the poll.
        poll_id: int
            The Poll ID to search for.

        Returns
        -------
        Poll
            The :class:`Poll` object from the fetched record.
        """
        record = await self.bot.db.polls.get(poll_id, guild_id)
        return Poll(cog=self, record=record) if record else None

    @cache.cache()
    async def get_guild_polls(self, guild_id: int, /) -> list[Poll]:
        """|coro| @cached

        Parameters
        ----------
        guild_id: int
            The Guild ID to search in for the polls.

        Returns
        -------
        List[Poll]
            A list of :class:`Poll` objects from the fetched records.
        """
        records = await self.bot.db.polls.get_for_guild(guild_id)
        return [Poll(cog=self, record=record) for record in records]

    async def end_poll(self, poll: Poll, /) -> int | None:
        """|coro|

        Ends a poll and maybe removes the corresponding timer from the reminder system.

        Parameters
        ----------
        poll: Poll
            The poll to end.

        Returns
        -------
        int
            The ID of the poll that was ended.
        """
        if poll.kwargs.get('running') is False:
            return None

        poll = await poll.edit(running=False)
        await self.bot.timers.delete('poll', poll_id=str(poll.id))

        if (
                (poll.message_id is not None and poll.message is MISSING)
                or (poll.kwargs.get('ping_message_id') is not None and poll.ping_message is MISSING)
        ):
            await poll.fetch_message()

        if poll.message:
            embed = poll.message.embeds[0]

            field = discord.utils.get(embed.fields, name='Poll ends')
            if field is not None:
                embed.set_field_at(
                    embed.fields.index(field),
                    name='Poll finished',
                    value=discord.utils.format_dt(discord.utils.utcnow(), 'R'),
                    inline=True
                )

            open_thread: bool = bool(poll.kwargs.get('thread') and poll.message.thread)
            if open_thread and poll.channel and poll.message.thread:
                await poll.message.thread.edit(archived=True, locked=True)

            try:
                await poll.message.edit(embed=embed, view=create_view(poll))
                if poll.ping_message:
                    await poll.ping_message.delete()
            except discord.HTTPException:
                pass

        self.get_guild_polls.invalidate(poll.guild_id)
        return poll.id

    @group(
        'polls',
        aliases=['poll'],
        description='Group command for managing polls.',
        guild_only=True,
        hybrid=True
    )
    async def polls(self, ctx: Context) -> None:
        """Group command for managing polls."""
        if ctx.invoked_subcommand is None:
            await ctx.send_help(ctx.command)

    @polls.command(
        'create',
        description='Creates a new poll with customizable settings.',
        guild_only=True,
        user_permissions=PermissionTemplate.mod
    )
    @describe(when='When the poll should end.', question='The question to ask.')
    async def polls_create(
            self,
            ctx: Context,
            when: timetools.FutureTime,
            *,
            question: str,
            flags: PollCreateFlags
    ) -> None:
        """Creates a poll with customizable settings."""
        await ctx.defer()
        assert ctx.guild is not None

        if self.bot.timers is None:
            await ctx.send_error('The timers system is not available at the moment.')
            return

        config = await self.bot.db.get_guild_config(ctx.guild.id)  # type: ignore[misc]
        if not config.poll_channel and not flags.channel:
            await ctx.send_error('You must set a poll channel first or use the `channel` parameter.')
            return
        else:
            channel = flags.channel or config.poll_channel

        image_url = flags.image_url or (flags.image.proxy_url if flags.image else None)
        options = list(filter(lambda x: x is not None, [
            flags.opt_1, flags.opt_2, flags.opt_3, flags.opt_4, flags.opt_5, flags.opt_6, flags.opt_7, flags.opt_8]))

        if len(options) < 2:
            await ctx.send_error('You must provide at least 2 options.')
            return

        to_options = [VoteOption(index=index, content=content, votes=0) for index, content in enumerate(options)]

        message = await channel.send(embed=discord.Embed(description='*Preparing Poll...*'))
        ping_message = None
        if flags.ping:
            ping_message = await channel.send('*...*')

        new_index = len(await self.get_guild_polls(ctx.guild.id)) + 1  # type: ignore[misc]
        unique_id = uuid([rec[0] for rec in await self.bot.db.polls.get_all_ids()])

        if flags.thread_question:
            thread = await message.create_thread(name=question, auto_archive_duration=4320)
            thread_message = await thread.send(flags.thread_question)
            await thread_message.pin(reason='Poll Discussion')

        if flags.with_reason and not config.poll_reason_channel:
            await ctx.send_error('You must set a reason channel to require user reasons.')
            return

        if flags.ping and not config.poll_ping_role_id:
            await ctx.send_error('You must set a ping role to use the ping feature.')
            return

        poll = await self.create_poll(
            unique_id,
            channel.id,
            message.id,
            ctx.guild.id,
            when.dt,
            ctx.user.id,
            ping_message_id=ping_message.id if flags.ping else None,
            question=question,
            description=flags.description,
            options=to_options,
            thread_question=flags.thread_question,
            with_reason=flags.with_reason,
            image=image_url,
            color=str(flags.color),
            votes=0,
            index=new_index,
            running=True
        )

        zone = await self.bot.db.get_user_timezone(ctx.author.id)
        await self.bot.timers.create(
            when.dt,
            'poll',
            poll_id=poll.id,
            created=discord.utils.utcnow(),
            timezone=zone or 'UTC',
        )

        await ctx.send_success(f'Poll #{new_index} [`{poll.id}`] successfully created. {message.jump_url}')

        await message.edit(embed=poll.to_embed(), view=create_view(poll))

        if flags.ping:
            assert ctx.guild.me is not None
            if not channel.permissions_for(ctx.guild.me).manage_roles:
                await ctx.send_error('I do not have the `Manage Roles` permission in this channel.')
                return

            view = discord.ui.View(timeout=None)
            view.add_item(PollRolePingButton(config.poll_ping_role_id))
            assert ping_message is not None
            await ping_message.edit(
                content=f'<@&{config.poll_ping_role_id}>',
                embed=discord.Embed(
                    description='You wanna tell us your opinion?\n'
                                'To be notified when new polls are posted, click below!',
                    color=helpers.Colour.light_grey()),
                view=view,
                allowed_mentions=discord.AllowedMentions(roles=True)
            )

    @polls.command(
        'end',
        description='Ends the voting for a running question.',
        guild_only=True,
        user_permissions=PermissionTemplate.mod
    )
    @describe(poll_id='5-digit ID of the poll to end.')
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)
    async def polls_end(self, ctx: Context, poll_id: int) -> None:
        """Ends a poll."""
        await ctx.defer()
        assert ctx.guild is not None

        poll = await self.get_guild_poll(ctx.guild.id, poll_id)
        if poll is None:
            await ctx.send_error('Poll not found.')
            return

        check = await self.end_poll(poll)
        if check is None:
            await ctx.send_error('Poll is already ended.')
            return

        await ctx.send_success(f'Poll [`{poll_id}`] has been ended.')

    @polls.command(
        'delete',
        description='Deletes a poll question.',
        guild_only=True,
        user_permissions=PermissionTemplate.mod
    )
    @describe(poll_id='5-digit ID of the poll to delete.')
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)
    async def polls_delete(self, ctx: Context, poll_id: int) -> None:
        """Deletes a poll question."""
        assert ctx.guild is not None
        poll = await self.get_guild_poll(ctx.guild.id, poll_id)
        if poll is None:
            await ctx.send_error('Poll not found.')
            return

        await poll.delete()
        await ctx.send_success(f'Poll [`{poll_id}`] has been deleted.')

    @polls.command(
        'edit',
        description='Edits a poll question. Type "-clear" to clear the current value.',
        guild_only=True,
        with_app_command=False,
        user_permissions=PermissionTemplate.mod
    )
    @describe(poll_id='5-digit ID of the poll to edit.')
    async def polls_edit(self, ctx: Context, poll_id: int, *, flags: PollEditFlags) -> None:
        """Edits a poll question.

        You can also remove the following fields by typing `--clear` as the value to change.

        Possible Parameters to remove:
        - Color
        - Description
        - Any not None Field
        - Thread
        """
        await ctx.defer()
        assert ctx.guild is not None

        poll = await self.get_guild_poll(ctx.guild.id, poll_id)
        if not poll:
            await ctx.send_error('Poll not found.')
            return

        if not poll.kwargs.get('running'):
            await ctx.send_error('Poll is not running.')
            return

        if poll.message_id is not None and poll.message is MISSING:
            await poll.fetch_message()

        open_thread = poll.kwargs.get('thread_question', None) and poll.message.thread
        form: dict[str, Any] = {}

        if flags.question and flags.question != '--clear':
            form['question'] = flags.question

        if flags.description:
            if flags.description == '--clear':
                form['description'] = None
            else:
                form['description'] = flags.description

        if flags.image or flags.image_url:
            image_url = flags.image_url or getattr(flags.image, 'proxy_url', None)
            form['image_url'] = image_url

        if flags.color:
            form['color'] = str(flags.color)

        if flags.thread_question:
            if open_thread:
                thread = poll.message.thread

                if flags.thread_question == '--clear':
                    if thread:
                        await thread.edit(archived=True, locked=True)

                    form['thread'] = None
                else:
                    if thread:
                        msg = [msg async for msg in thread.history(limit=2, oldest_first=True)][1]
                        if msg.author.id == self.bot.user.id:
                            await msg.edit(content=flags.thread_question)

                    form['thread'] = flags.thread_question
            else:
                try:
                    thread = await poll.message.create_thread(name=poll.question, auto_archive_duration=4320)
                except discord.HTTPException as exc:
                    # Somehow, a thread already exists.
                    if exc.code != 160004:
                        raise exc
                    thread = poll.message.thread
                    if thread is not None:
                        await thread.edit(name=poll.question)

                if thread is None:
                    return
                thread_message = await thread.send(flags.thread_question)
                await thread_message.pin(reason='Poll Discussion')

                form['thread'] = flags.thread_question

        options: list[VoteOption] = poll.options.copy()
        for index, content in enumerate([
            flags.opt_1, flags.opt_2, flags.opt_3, flags.opt_4, flags.opt_5, flags.opt_6, flags.opt_7, flags.opt_8
        ]):
            if content:
                is_option = index + 1 <= len(options)
                if not is_option:
                    if content != '--clear':
                        options.append(VoteOption(index=index, content=content, votes=0))
                elif content == '--clear':
                    options = poll.remove_option(options[index]) or options
                else:
                    options[index]['content'] = content

        form['votes'] = poll.votes
        form['options'] = options

        poll = await poll.edit(**form)
        await poll.message.edit(embed=poll.to_embed(), view=create_view(poll))
        await ctx.send_success(f'Poll [`{poll.id}`] edited successfully.', ephemeral=True)

    @polls_edit.define_app_command()  # type: ignore[attr-defined]
    @describe(poll_id='5-digit ID of the poll to edit.')
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)
    async def polls_edit_app_command(self, ctx: HybridContext, poll_id: int) -> None:
        """Edits a poll question. Type "-clear" to clear the current value.

        Possible Parameters to remove:
        - Question
        - Description
        - Any not None Field
        - Thread
        - Image
        - Color
        - Options
        """
        assert ctx.guild is not None
        poll = await self.get_guild_poll(ctx.guild.id, poll_id)
        if not poll:
            await ctx.send_error('Poll not found.')
            return

        if not poll.kwargs.get('running'):
            await ctx.send_error('Poll is not running.')
            return

        if poll.message_id is not None and poll.message is MISSING:
            await poll.fetch_message()

        modal = EditModal(poll)
        await ctx.interaction.response.send_modal(modal)
        await modal.wait()

        await ctx.full_invoke(  # type: ignore[call-arg]
            poll_id,
            flags=MockFlags(
                question=modal.question.value,
                description=modal.description.value,
                image=modal.image.value,
                color=modal.color.value,
                thread_question=modal.thread_question.value,
            )
        )

    @polls.command(
        'search',
        description='Searches poll questions. Search by ID, keyword or flags.',
        guild_only=True
    )
    @app_commands.choices(
        sort=[
            app_commands.Choice(name='Poll ID', value='id'),
            app_commands.Choice(name='Newest', value='new'),
            app_commands.Choice(name='Oldest', value='old'),
            app_commands.Choice(name='Most Votes', value='most votes'),
            app_commands.Choice(name='Least Votes', value='least votes')
        ]
    )
    @describe(poll_id='5-digit ID of the poll to search for.')
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)
    async def polls_search(
            self,
            ctx: Context,
            poll_id: int | None = None,
            *,
            flags: PollSearchFlags
    ) -> None:
        """Searches poll questions. Search by ID, keyword or flags."""
        await ctx.defer()
        assert ctx.guild is not None

        if poll_id:
            poll = await self.get_guild_poll(ctx.guild.id, poll_id)
            if not poll:
                await ctx.send_error('Poll not found.')
                return

            if flags.showextrainfo and ctx.channel.permissions_for(ctx.user).manage_messages:
                embed = discord.Embed(
                    title=f'#{poll.kwargs.get('index')}: {poll.question}',
                    description=poll.description)

                embed.add_field(
                    name='Choices',
                    value='\n'.join(f'{v['value']}' for v in poll.to_fields(extras=False)),
                    inline=False)
                embed.add_field(name='Voting', value=f'Total Votes: **{poll.votes}**')

                running = poll.kwargs.get('running')
                embed.add_field(name='Active?', value=running)

                embed.add_field(
                    name='Poll published',
                    value=discord.utils.format_dt(poll.published, 'f'))
                embed.add_field(
                    name='Poll ends' if running else 'Poll finished',
                    value=discord.utils.format_dt(poll.expires, 'R'))

                embed.add_field(name='Poll Message',
                                value=poll.jump_url or f'Can\'t locate message `{poll.message_id}`')
                embed.add_field(name='User Reason', value=poll.kwargs.get('with_reason'))

                if thread := poll.kwargs.get('thread'):
                    embed.add_field(name='Thread Question', value=thread)

                embed.set_image(url=poll.kwargs.get('image'))
                embed.colour = discord.Colour.from_str(poll.kwargs.get('color') or '#ffffff')

                embed.set_footer(text=f'[{poll.id}] • {poll.guild_id}')
            else:
                embed = poll.to_embed()

            await ctx.send(embed=embed)
        else:
            text = ['**Filter(s):**']

            text.append(f'Sorted by: **{flags.sort.lower()}**')
            if flags.active:
                text.append('Running: **True**')

            records = await self.bot.db.polls.search_for_guild(
                ctx.guild.id, sort=flags.sort, active=flags.active)

            if not records:
                await ctx.send_error('No polls found matching this filter.')
                return

            if flags.keyword:
                text.append(f'Keyword: **{flags.keyword}**')
                records = [r for r in records if fuzzy.partial_ratio(  # type: ignore[call-arg]
                    flags.keyword.lower(), r['metadata']['kwargs'].get('question').lower()) > 70]

            def fmt_poll(_poll: Poll) -> str:
                fmt_timestamp = discord.utils.format_dt(_poll.published, 'd')
                return f'`{_poll.id}` (`#{_poll.kwargs.get("index")}`): {_poll.question} ({fmt_timestamp})'

            results = [fmt_poll(poll) for poll in [Poll(cog=self, record=r) for r in records]]
            embed = discord.Embed(
                title='Poll Search',
                description='\n'.join(text),
                colour=helpers.Colour.white(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_thumbnail(url=get_asset_url(ctx.guild))
            embed.set_footer(text=f'{pluralize(len(records)):entry|entries}')

            await LinePaginator.start(ctx, entries=results, per_page=12, embed=embed)

    @polls.command(
        'history',
        description='Shows the vote history of a user for polls.',
        guild_only=True
    )
    @describe(member='The Member to show the history for.')
    async def polls_history(self, ctx: Context, member: discord.Member | None = None) -> None:
        """Shows the vote history of a user for polls."""
        assert ctx.guild is not None
        polls = await self.get_guild_polls(ctx.guild.id)  # type: ignore[misc]
        if not polls:
            await ctx.send_error('No polls found.')
            return

        member = member or ctx.author
        user_polls = list(filter(lambda poll: any(x[0] == member.id for x in poll.entries), polls))

        class FieldPaginator(BasePaginator[Poll]):
            async def format_page(self, entries: list[Poll], /) -> discord.Embed:
                embed = discord.Embed(
                    title=f'Poll History for {member}',
                    colour=helpers.Colour.white(),
                    timestamp=discord.utils.utcnow())
                embed.set_footer(text=f'{pluralize(len(polls)):entry|entries}')

                for poll in entries:
                    vote = next(option for (user, option) in poll.entries if user == member.id)
                    embed.add_field(
                        name=f'{poll.id} (#{poll.kwargs.get('index')}): {poll.question}',
                        value=f'You\'ve voted: {to_emoji(poll.options[vote]['index'])} - '
                              f'*{poll.options[vote]['content']}*',
                        inline=False)

                return embed

        await FieldPaginator.start(ctx, entries=user_polls, per_page=12)

    @polls.command(
        'debug',
        description='Refactor all existing Polls in this guild and reattach the views.',
        guild_only=True,
        user_permissions=PermissionTemplate.mod
    )
    @describe(poll_id='5-digit ID of the poll to debug.')
    @cooldown(1, 5, commands.BucketType.guild)
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)
    async def polls_debug(self, ctx: Context, poll_id: int) -> None:
        """Refactor all existing Polls in this guild and reattach the views."""
        assert ctx.guild is not None
        poll = await self.get_guild_poll(ctx.guild.id, poll_id)
        if not poll:
            await ctx.send_error('Poll not found.')
            return

        if poll.guild_id != ctx.guild.id:
            await ctx.send_error('Poll not found in this guild.')
            return

        if not poll.channel:
            await ctx.send_error('Poll channel not found.')
            return

        embed = poll.to_embed()
        await poll.fetch_message()

        if not poll.message:
            await ctx.send_error('Poll message not found. :/')
            return

        try:
            view = create_view(poll)
        except Exception as exc:
            await ctx.send_error(f'Failed to create view.: {exc}')
            return

        await poll.message.edit(embed=embed, view=view)

        await ctx.send_success(f'Poll [`{poll.id}`] debugged.', ephemeral=True)

    @polls.command(
        name='config',
        description='Shows the current configuration for polls.',
        guild_only=True,
        user_permissions=PermissionTemplate.mod
    )
    async def polls_config(
            self,
            ctx: Context,
            *,
            flags: PollConfigFlags
    ) -> None:
        """Shows/Changes the current configuration for polls."""
        await ctx.defer()
        assert ctx.guild is not None

        config: GuildConfig = await self.bot.db.get_guild_config(guild_id=ctx.guild.id)  # type: ignore[misc]
        if not config:
            await ctx.send_error('No configuration found.')
            return

        if all(x is None for x in [flags.channel, flags.reason_channel, flags.ping_role]):
            embed = discord.Embed(
                title='Poll Configuration',
                colour=helpers.Colour.white(),
                timestamp=discord.utils.utcnow())
            embed.add_field(name='Poll Channel',
                            value=f'<#{config.poll_channel_id}>' if config.poll_channel_id else 'N/A')
            embed.add_field(name='Poll Reason Channel',
                            value=f'<#{config.poll_reason_channel_id}>' if config.poll_reason_channel_id else 'N/A')
            embed.add_field(name='Poll Role',
                            value=f'<@&{config.poll_ping_role_id}>' if config.poll_ping_role_id else 'N/A')
            embed.set_footer(text='Use "/polls config" to change the configuration.')
            await ctx.send(embed=embed)
        else:
            if flags.reset:
                form = {
                    'poll_channel_id': None,
                    'poll_reason_channel_id': None,
                    'poll_ping_role_id': None
                }
                await ctx.send_success('Poll configuration reset.', ephemeral=True)
            else:
                form = {}
                if flags.channel:
                    form['poll_channel_id'] = flags.channel.id
                if flags.reason_channel:
                    form['poll_reason_channel_id'] = flags.reason_channel.id
                if flags.ping_role:
                    form['poll_ping_role_id'] = flags.ping_role.id

                await ctx.send_success('Poll configuration updated.', ephemeral=True)

            await config.update(**form)

    @Cog.listener()
    async def on_poll_timer_complete(self, timer: Timer) -> None:
        """Called when a Poll timer completes.

        Parameters
        ----------
        timer: Timer
            The Timer object that completed.
        """
        await self.bot.wait_until_ready()
        poll_id = timer['poll_id']

        record = await self.bot.db.polls.get_by_id(poll_id)
        poll = Poll(cog=self, record=record) if record else None

        if poll:
            await self.end_poll(poll)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Polls(bot))
