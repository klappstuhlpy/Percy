from __future__ import annotations

import random
import traceback
import warnings
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Literal, Self, TypedDict

import discord
from discord import app_commands
from discord.ext import commands, tasks
from discord.utils import MISSING

from app.core import Bot, Cog, Flags, View, flag, store_true
from app.core.converter import ColorTransformer, ValidURL
from app.core.flags import MockFlags
from app.core.models import (
    AppBadArgument,
    Context,
    EmbedBuilder,
    HybridContext,
    PermissionTemplate,
    cooldown,
    describe,
    group,
)
from app.database import BaseRecord
from app.utils import cache, fuzzy, get_asset_url, get_shortened_string, helpers, pluralize, timetools
from app.utils.pagination import BasePaginator, LinePaginator
from config import Emojis

if TYPE_CHECKING:
    import datetime
    import re
    from collections.abc import Callable

    import asyncpg

    from app.core.timer import Timer
    from app.database.base import GuildConfig


def to_emoji(index: int) -> str:
    INDEX_TO_LETTER = {
        0: 'A', 1: 'B', 2: 'C', 3: 'D', 4: 'E', 5: 'F', 6: 'G', 7: 'H'
    }
    return getattr(Emojis.PollVoteBar, INDEX_TO_LETTER[index])


_MAX_VOTE_BAR_LENGTH = 10


def lineformat(x: int) -> str:
    if not x:
        return Emojis.PollVoteBar.end

    txt = [Emojis.PollVoteBar.middle] * (x - 1)
    txt.append(Emojis.PollVoteBar.end)
    txt[0] = Emojis.PollVoteBar.start

    return ''.join(txt)


def uuid(ids: list[int]) -> int:
    _id = random.randint(10000, 99999)
    while _id in ids:
        _id = random.randint(10000, 99999)
    return _id


warnings.simplefilter(action='ignore', category=FutureWarning)


class PollCreateFlags(Flags):
    description: str = flag(description='The description for the poll.')
    color: helpers.Colour = flag(
        description='The color for the poll.', converter=ColorTransformer, default=helpers.Colour.white())
    channel: discord.TextChannel = flag(description='The channel to send the poll to.')
    thread_question: str = flag(description='The question for the thread.')
    ping: bool = store_true(description='Whether to ping the role or not.')
    with_reason: bool = store_true(description='Whether to ask for a reason for the vote or not.')
    image: discord.Attachment = flag(description='The image for the poll.')
    image_url: str = flag(description='The image URL for the poll.', converter=ValidURL)

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
    image_url: str = flag(description='The new image URL to use.', converter=ValidURL)
    color: helpers.Colour = flag(description='The new color to use.', converter=ColorTransformer)

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
    active: bool = store_true(description='Whether to search for active polls or not.')
    showextrainfo: bool = store_true(description='Whether to show extra information or not.')


class PollConfigFlags(Flags):
    channel: discord.TextChannel = flag(description='The channel to send the poll to.')
    reason_channel: discord.TextChannel = flag(description='The channel to send the poll reasons to.')
    ping_role: discord.Role = flag(description='The role to ping for the polls.')
    reset: bool = store_true(description='Whether to reset the poll settings or not.')


class VoteOption(TypedDict):
    """A vote option."""
    index: int
    content: str
    votes: int


class PollReasonModal(discord.ui.Modal, title='The Reason for you choice.'):
    def __init__(self, poll: Poll, selected_option: dict[str, Any], bot: Bot) -> None:
        super().__init__(timeout=60.0)
        self.poll = poll
        self.bot = bot
        self.selected_option = selected_option

    reason = discord.ui.TextInput(label='Reason', placeholder='Why did you choose this option.',
                                  style=discord.TextStyle.long, min_length=1, max_length=200)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title='New Poll Reason', color=helpers.Colour.white())
        embed.set_thumbnail(url=get_asset_url(interaction.guild))
        embed.set_author(name=interaction.user, icon_url=get_asset_url(interaction.user))
        embed.add_field(name='Poll', value=f'{self.poll.question}\n{self.poll.jump_url}', inline=False)
        embed.add_field(name='Reason', value=self.reason.value, inline=False)
        embed.add_field(name='Selected Option',
                        value=f'{to_emoji(self.selected_option['index'])}: {self.selected_option['content']}',
                        inline=False)
        embed.set_footer(text=f'#{self.poll.kwargs.get('index')} • [{self.poll.id}]')

        await interaction.response.send_message('Thank you for submitting your response.', ephemeral=True)

        with suppress(discord.HTTPException):
            config = await self.bot.db.get_guild_config(interaction.guild.id)
            if channel := config.poll_reason_channel:
                await channel.send(embed=embed)

        self.stop()


class EditModal(discord.ui.Modal, title='Edit Poll'):
    question = discord.ui.TextInput(label='Question', placeholder='The Main Question for the poll.')
    description = discord.ui.TextInput(label='Description', placeholder='The Description for the poll.',
                                       style=discord.TextStyle.long, required=False)
    thread_question = discord.ui.TextInput(label='Thread Question', placeholder='The Question for the thread.',
                                           required=False)
    image = discord.ui.TextInput(label='Image URL', placeholder='The Image URL for the poll.', required=False)
    color = discord.ui.TextInput(label='Color', placeholder='The Color for the poll.', required=False)

    def __init__(self, poll: Poll) -> None:
        super().__init__(title=f'Edit Poll [{poll.id}]', timeout=180.0)

        self.question.default = poll.question
        self.description.default = poll.description
        self.thread_question.default = poll.kwargs.get('thread', None)
        self.image.default = poll.kwargs.get('image', None)
        self.color.default = poll.kwargs.get('color', None)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await interaction.response.send_message('Something broke!', ephemeral=True)
        traceback.print_tb(error.__traceback__)


class PollClearVoteButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'poll:clear:(?P<id>[0-9]+)',
):
    def __init__(self, poll: Poll) -> None:
        self.poll: Poll = poll
        super().__init__(
            discord.ui.Button(
                label='Clear Vote',
                style=discord.ButtonStyle.red,
                row=1,
                custom_id=f'poll:clear:{poll.id}'
            )
        )

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Bot], _, match: re.Match[str], /
    ) -> PollClearVoteButton:
        cog: Polls | None = interaction.client.get_cog('Polls')
        if cog is None:
            await interaction.response.send_message(
                f'{Emojis.error} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AppBadArgument(f'{Emojis.error} Polls cog is not loaded')

        poll = await cog.get_guild_poll(interaction.guild.id, int(match['id']))
        if poll is None:
            raise AppBadArgument(f'{Emojis.error} Poll was not found')

        return cls(poll)

    async def interaction_check(self, interaction: discord.Interaction[Bot], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f'{Emojis.error} Poll was not found.', ephemeral=True)
            return False

        entry = self.poll.get_entry(interaction.user.id)
        if not entry:
            await interaction.response.send_message(
                f'You haven\'t voted on the poll *{self.poll.question}* [`{self.poll.id}`].', ephemeral=True)
            return False

        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.message.embeds:
            return

        entry = self.poll.get_entry(interaction.user.id)

        self.poll.entries.discard(entry)
        options: list[VoteOption] = self.poll.options.copy()
        options[entry[1]]['votes'] -= 1
        self.poll = await self.poll.edit(options=options, votes=len(self.poll.entries))

        await interaction.response.edit_message(embed=self.poll.to_embed())


class PollInfoButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'poll:info:(?P<id>[0-9]+)',
):
    def __init__(self, poll: Poll) -> None:
        self.poll: Poll = poll
        super().__init__(
            discord.ui.Button(
                emoji=Emojis.PollVoteBar.info,
                style=discord.ButtonStyle.grey,
                row=1,
                custom_id=f'poll:info:{poll.id}'
            )
        )

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Bot], _, match: re.Match[str], /
    ) -> PollInfoButton:
        cog: Polls | None = interaction.client.get_cog('Polls')
        if cog is None:
            await interaction.response.send_message(
                f'{Emojis.error} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AppBadArgument(f'{Emojis.error} Polls cog is not loaded')

        poll = await cog.get_guild_poll(interaction.guild.id, int(match['id']))
        if poll is None:
            raise AppBadArgument(f'{Emojis.error} Poll was not found')

        return cls(poll)

    async def interaction_check(self, interaction: discord.Interaction[Bot], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f'{Emojis.error} Poll was not found.', ephemeral=True)
            return False

        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title=f'#{self.poll.kwargs.get('index')}: {self.poll.question}',
            colour=self.poll.color,
        )

        value = [field['value'] for field in self.poll.to_fields(extras=False)]
        embed.add_field(name='Votes', value='\n'.join(value))

        vote = next((option for (user, option) in self.poll.entries if user == interaction.user.id), None)
        if vote:
            option = next((i for i in self.poll.options if i['index'] == vote), None)
            text = f'You\'ve voted: {to_emoji(option['index'])} *{option['content']}*'
        else:
            text = 'You haven\'t voted yet.' if self.poll.kwargs.get('running') is True else 'You didn\'t vote.'

        embed.add_field(name='Your Vote', value=text, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


class PollEnterButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'poll:enter:(?P<id>[0-9]+):option:(?P<index>[0-9]+)',
):
    def __init__(self, poll: Poll, index: int) -> None:
        self.poll: Poll = poll
        self.index: int = index
        super().__init__(
            discord.ui.Button(
                emoji=to_emoji(index), style=discord.ButtonStyle.gray,
                custom_id=f'poll:enter:{poll.id}:option:{index}'
            )
        )

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Bot], _, match: re.Match[str], /
    ) -> PollEnterButton:
        cog: Polls | None = interaction.client.get_cog('Polls')
        if cog is None:
            await interaction.response.send_message(
                f'{Emojis.error} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AppBadArgument(f'{Emojis.error} Polls cog is not loaded')

        poll = await cog.get_guild_poll(interaction.guild.id, int(match['id']))
        if poll is None:
            raise AppBadArgument(f'{Emojis.error} Poll was not found')

        return cls(poll, int(match['index']))

    async def interaction_check(self, interaction: discord.Interaction[Bot], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f'{Emojis.error} Poll was not found.', ephemeral=True)
            return False

        entry = self.poll.get_entry(interaction.user.id)
        if entry:
            vote = self.poll.get_option(entry[1])
            await interaction.response.send_message(
                f'On the poll *{self.poll.question}* [`{self.poll.id}`], you voted:\n'
                f'{to_emoji(vote['index'])} - `{vote['content']}`',
                ephemeral=True)
            return False

        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.message.embeds:
            return

        option = self.poll.get_option(self.index)
        if self.poll.kwargs.get('with_reason'):
            modal = PollReasonModal(self.poll, option, interaction.client)
            await interaction.response.send_modal(modal)
            state = await modal.wait()
            if state is True:
                return await interaction.followup.send(
                    content=f'{Emojis.error} This poll requires you to submit a reason for your vote.',
                    ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        self.poll.entries.add((interaction.user.id, option['index']))
        options: list[VoteOption] = self.poll.options.copy()
        options[option['index']]['votes'] += 1
        self.poll = await self.poll.edit(options=options, votes=len(self.poll.entries))

        await interaction.edit_original_response(embed=self.poll.to_embed())
        await interaction.followup.send(
            f'On the poll *{self.poll.question}* [`{self.poll.id}`], you voted:\n'
            f'{to_emoji(option['index'])} - `{option['content']}`',
            ephemeral=True)


class PollEnterSelect(
    discord.ui.DynamicItem[discord.ui.Select],
    template=r'poll:select:(?P<id>[0-9]+)',
):
    def __init__(self, poll: Poll) -> None:
        self.poll: Poll = poll
        super().__init__(
            discord.ui.Select(
                placeholder='Select the option to vote for...',
                row=0,
                custom_id=f'poll:select:{poll.id}',
                options=[
                    discord.SelectOption(
                        label=option['content'], value=str(option['index']), emoji=to_emoji(option['index'])
                    ) for option in poll.options
                ]
            )
        )

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Bot], _, match: re.Match[str], /
    ) -> PollEnterSelect:
        cog: Polls | None = interaction.client.get_cog('Polls')
        if cog is None:
            await interaction.response.send_message(
                f'{Emojis.error} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AppBadArgument(f'{Emojis.error} Polls cog is not loaded')

        poll = await cog.get_guild_poll(interaction.guild.id, int(match['id']))
        if poll is None:
            raise AppBadArgument(f'{Emojis.error} Poll was not found')

        return cls(poll)

    async def interaction_check(self, interaction: discord.Interaction[Bot], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f'{Emojis.error} Poll was not found.', ephemeral=True)
            return False

        vote = next((option for (user, option) in self.poll.entries if user == interaction.user.id), None)
        if vote:
            option = next((i for i in self.poll.options if i['index'] == vote), None)
            await interaction.response.send_message(
                f'On the poll *{self.poll.question}* [`{self.poll.id}`], you voted:\n'
                f'{to_emoji(option['index'])} - `{option['content']}`',
                ephemeral=True)
            return False

        return True

    async def callback(self, interaction: discord.Interaction) -> None:
        if not interaction.message.embeds:
            return

        option = self.poll.get_option(int(self.values[0]))
        if self.poll.kwargs.get('with_reason'):
            modal = PollReasonModal(self.poll, option, interaction.client)
            await interaction.response.send_modal(modal)
            state = await modal.wait()
            if state is True:
                return await interaction.followup.send(
                    content=f'{Emojis.error} This poll requires you to submit a reason for your vote.',
                    ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        self.poll.entries.add((interaction.user.id, option['index']))
        options: list[VoteOption] = self.poll.options.copy()
        options[option['index']]['votes'] += 1
        self.poll = await self.poll.edit(options=options, votes=len(self.poll.entries))

        await interaction.edit_original_response(embed=self.poll.to_embed())
        await interaction.followup.send(
            f'On the poll *{self.poll.question}* [`{self.poll.id}`], you voted:\n'
            f'{to_emoji(option['index'])} - `{option['content']}`',
            ephemeral=True)


class PollRolePingButton(
    discord.ui.DynamicItem[discord.ui.Button],
    template=r'poll:ping:role:(?P<role_id>[0-9]+)',
):
    def __init__(self, role_id: int) -> None:
        self.role_id: int = role_id
        super().__init__(
            discord.ui.Button(
                label='Add/Remove Role',
                style=discord.ButtonStyle.grey,
                custom_id=f'poll:ping:role:{role_id}'
            )
        )

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Bot], _, match: re.Match[str], /
    ) -> PollRolePingButton:
        cog: Polls | None = interaction.client.get_cog('Polls')
        if cog is None:
            await interaction.response.send_message(
                f'{Emojis.error} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{Emojis.error} Polls cog is not loaded')

        return cls(int(match['role_id']))

    async def interaction_check(self, interaction: discord.Interaction[Bot], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f'{Emojis.error} Poll was not found.', ephemeral=True)
            return False

        return True

    async def callback(self, interaction: discord.Interaction) -> Any:
        role = discord.Object(id=self.role_id)
        if any(r.id == self.role_id for r in interaction.user.roles):
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(
                f'{Emojis.success} Successfully **removed** from you these roles: '
                f'<@&{self.role_id}>. Click again to re-add.',
                ephemeral=True
            )
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(
                f'{Emojis.success} Successfully **added** you the roles: '
                f'<@&{self.role_id}>. Click again to remove.',
                ephemeral=True
            )


def create_view(poll: Poll) -> discord.ui.View:
    view = View(timeout=None)
    if poll.kwargs.get('running') is True:
        if len(poll.options) <= 5:
            for option in poll.options:
                view.add_item(PollEnterButton(poll, option['index']))
        else:
            view.add_item(PollEnterSelect(poll))
        view.add_item(PollClearVoteButton(poll))
    view.add_item(PollInfoButton(poll))
    return view


class PollEntry(BaseRecord):
    """Represents a poll entry."""

    user_id: int
    vote: int

    __slots__ = ('user_id', 'vote')

    def __iter__(self) -> tuple[int, int]:
        return self.user_id, self.vote


class Poll(BaseRecord):
    """Represents a poll item."""

    cog: Polls
    id: int
    message_id: int
    channel_id: int
    guild_id: int
    metadata: dict[str, Any]
    entries: set[tuple[int, int]]
    expires: datetime.datetime
    published: datetime.datetime

    # metadata
    args: list[Any]
    kwargs: dict[str, Any]
    message: discord.Message
    question: str
    votes: int
    description: str
    options: list[list[VoteOption]]

    __slots__ = (
        'id', 'message_id', 'channel_id', 'guild_id', 'metadata', 'entries', 'expires', 'published',
        'args', 'kwargs', 'message', 'question', 'votes', 'description', 'options',
        'cog', 'bot', 'ping_message', 'color'
    )

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.bot: Bot = self.cog.bot

        self.args: list[Any] = self.metadata.get('args', [])
        self.kwargs: dict[str, Any] = self.metadata.get('kwargs', {})

        self.message: discord.Message = MISSING
        self.ping_message: discord.Message = MISSING

        self.question: str = self.kwargs.get('question')
        self.votes: int = self.kwargs.get('votes', 0)
        self.description: str = self.kwargs.get('description')
        self.options: list[VoteOption] = self.kwargs.get('options', [])
        self.color: helpers.Colour = helpers.Colour.from_str(self.kwargs.get('color'))

        self.entries = {PollEntry(record=entry).__iter__() for entry in self.entries or []}

    @property
    def jump_url(self) -> str | None:
        """The jump URL of the poll."""
        if self.message_id and self.channel_id:
            guild = self.guild_id or '@me'
            return f'https://discord.com/channels/{guild}/{self.channel_id}/{self.message_id}'
        return None

    @property
    def channel(self) -> discord.TextChannel | None:
        """The channel of the poll."""
        if self.channel_id is not None:
            return self.bot.get_channel(self.channel_id)
        return None

    @property
    def choice_text(self) -> str:
        """The text to use for the autocomplete."""
        return f'[{self.id}] {self.question}'

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> Poll:
        """|coro|

        Updates the poll.

        Parameters
        ----------
        key: Callable[[tuple[int, str]], str]
            The key to use for the update.
        values: dict[str, Any]
            The values to use for the update.
        connection: asyncpg.Connection
            The connection to use for the update.

        Returns
        -------
        Poll
            The updated poll.
        """
        query = f"""
            UPDATE polls
            SET {', '.join(map(key, enumerate(values.keys(), start=2)))}
            WHERE id = $1
            RETURNING *;
        """
        record = await (connection or self.bot.db).fetchrow(query, self.id, *values.values())

        cls = self.__class__(cog=self.cog, record=record)
        cls.message = self.message
        cls.ping_message = self.ping_message
        return cls

    def get_option(self, index: int) -> VoteOption | None:
        """Gets an option from the poll.

        Parameters
        ----------
        index: int
            The index of the option to get.

        Returns
        -------
        VoteOption
            The option from the poll.
        """
        return next((option for option in self.options if option['index'] == index), None)

    def get_entry(self, user_id: int) -> tuple[int, int] | None:
        """Gets the vote of a user.

        Parameters
        ----------
        user_id: int
            The user ID to get the vote of.

        Returns
        -------
        tuple[int, int] | None
            The vote of the user.
        """
        return next(((user, vote) for (user, vote) in self.entries if user == user_id), None)

    def to_fields(self, extras: bool = True) -> list[dict]:
        """Converts the poll to fields."""
        fields = []
        for i, option in enumerate(self.options):
            v = option['votes']
            votes = self.votes

            p = v / votes if votes else 0
            x = (v * _MAX_VOTE_BAR_LENGTH) // votes if votes else 0

            fields.append({
                'name': f'{Emojis.PollVoteBar.corner} ' + option['content'],
                'value': f'{to_emoji(option['index'])}{lineformat(x)} **{v}** {pluralize(v, pass_content=True):vote} ({round(p * 100)}%)',
                'inline': False
            })

        if extras:
            fields.append({'name': 'Voting', 'value': f'Total Votes: **{self.votes}**', 'inline': True})
            if self.expires:
                fields.append(
                    {'name': 'Poll ends', 'value': discord.utils.format_dt(self.expires, 'R'), 'inline': True})
            if (thread := self.kwargs.get('thread', None)) is not None:
                fields.append({'name': 'Discussion in Thread:', 'value': thread, 'inline': True})

        return fields

    async def fetch_message(self) -> None:
        """Fetches the message of the poll and if needed the ping_message."""
        channel = self.channel
        if channel is None:
            return

        if self.message_id is not None:
            message = await self.cog.get_message(channel, self.message_id)
            if message:
                self.message = message

        if (ping_message_id := self.kwargs.get('ping_message_id')) is not None:
            ping_message = await self.cog.get_message(channel, ping_message_id)
            if ping_message:
                self.ping_message = ping_message

    def to_embed(self) -> discord.Embed:
        """Converts the poll to an embed."""
        embed = EmbedBuilder(
            title=self.question,
            description=self.description,
            colour=self.color,
            timestamp=self.published,
            fields=self.to_fields()
        )
        embed.set_image(url=self.kwargs.get('image', None))
        embed.set_footer(text=f'#{self.kwargs.get('index')} • [{self.id}]')
        return embed

    def remove_option(self, option: VoteOption = MISSING) -> list[VoteOption] | None:
        """Removes an option from the poll by erasing the votes and removing the option.

        Parameters
        ----------
        option: VoteOption
            The option to remove from the poll.
        """
        if len(self.options) > 2:
            self.options.remove(option)
            for index, ch in enumerate(sorted(self.options, key=lambda x: x['index'])):
                ch['index'] = index

            self.votes -= option['votes']
            self.entries = {(user, user_option) for user, user_option in self.entries if user_option != option['index']}
            return self.options
        return None

    async def edit(
            self,
            *,
            question: str | None = MISSING,
            description: str | None = MISSING,
            thread: list[int, str] | None = MISSING,
            image_url: str | None = MISSING,
            color: str | None = MISSING,
            options: list[VoteOption] | None = MISSING,
            running: bool | None = MISSING,
            votes: int | None = MISSING,
    ) -> Self:
        """|coro|

        Edits the poll.

        Parameters
        ----------
        question: str | None
            The question to update the poll with.
        description: str | None
            The description to update the poll with.
        thread: List[int, str]
            The thread to update the poll with.
        image_url: str | None
            The image URL to update the poll with.
        color: str | None
            The color to update the poll with.
        options: List[Tuple[Dict[str, Any] | None, EditType, int | None]]
            The options to update the poll with.
        running: bool | None
            The running status to update the poll with.
        votes: int | None
            The votes to update the poll with.

        Returns
        -------
        Self
            The updated poll.
        """
        form: dict[str, Any] = {}

        if question is not MISSING:
            form['content'] = question
        if description is not MISSING:
            form['description'] = description
        if thread is not MISSING:
            form['thread'] = thread
        if image_url is not MISSING:
            form['image_url'] = image_url
        if running is not MISSING:
            form['running'] = running
        if color is not MISSING:
            form['color'] = color
        if options is not MISSING:
            form['options'] = options
        if votes is not MISSING:
            form['votes'] = votes
            # NOTE: This is a temporary fix for the votes not updating properly.
            self.votes = votes

        self.metadata.get('kwargs').update(form)
        self.cog.get_guild_polls.invalidate(self.guild_id)
        return await self.update(metadata=self.metadata, entries=self.entries)

    async def delete(self) -> None:
        """Deletes the poll."""
        query = "DELETE FROM polls WHERE id = $1;"
        await self.bot.db.execute(query, self.id)

        if self.message_id is not None and self.message is MISSING:
            await self.fetch_message()

        if self.message:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass

        self.cog.get_guild_polls.invalidate(self.guild_id)


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
    ) -> list[app_commands.Choice[int]]:
        polls = await self.get_guild_polls(interaction.guild.id)

        if interaction.command.name in ('end', 'edit', 'debug'):
            polls = [poll for poll in polls if poll.kwargs.get('running') is True]

        results = fuzzy.finder(current, polls, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, poll.choice_text), value=poll.id)
            for length, start, poll in results[:20]]

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

        poll = Poll.temporary(
            cog=self,
            channel_id=channel_id,
            message_id=message_id,
            guild_id=guild_id,
            published=published,
            expires=expires,
            entries=set(),
            metadata={'args': args, 'kwargs': kwargs}
        )

        query = """
            INSERT INTO polls (id, channel_id, message_id, guild_id, published, expires, metadata)
            VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            RETURNING id;
        """

        poll.id = await self.bot.db.fetchval(
            query, poll_id, channel_id, message_id, guild_id,
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
        query = "SELECT * FROM polls WHERE id = $1 AND guild_id = $2 LIMIT 1;"
        record = await self.bot.db.fetchrow(query, poll_id, guild_id)
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
        query = "SELECT * FROM polls WHERE guild_id = $1;"
        return [Poll(cog=self, record=record) for record in await self.bot.db.fetch(query, guild_id)]

    async def end_poll(self, poll: Poll, /) -> int | None:
        """|coro|

        Ends a poll and maybe removes the corresponding timer from the reminder system.
        This includes closing possible Threads and finishing up the poll message.

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
            embed.set_field_at(
                embed.fields.index(field),
                name='Poll finished',
                value=discord.utils.format_dt(discord.utils.utcnow(), 'R'),
                inline=True
            )

            open_thread: bool = poll.kwargs.get('thread') and poll.message.thread
            if open_thread and poll.channel:
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

        if self.bot.timers is None:
            await ctx.send_error('The timers system is not available at the moment.')
            return

        config = await self.bot.db.get_guild_config(ctx.guild.id)
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

        new_index = len(await self.get_guild_polls(ctx.guild.id)) + 1
        unique_id = uuid([rec[0] for rec in await self.bot.db.fetch("SELECT id FROM polls;")])

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
            if not channel.permissions_for(ctx.guild.me).manage_roles:
                await ctx.send_error('I do not have the `Manage Roles` permission in this channel.')
                return

            view = discord.ui.View(timeout=None)
            view.add_item(PollRolePingButton(config.poll_ping_role_id))
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
                    await thread.edit(name=poll.question)

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

    @polls_edit.define_app_command()
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

        await ctx.full_invoke(
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
                embed.colour = discord.Colour.from_str(poll.kwargs.get('color'))

                embed.set_footer(text=f'[{poll.id}] • {poll.guild_id}')
            else:
                embed = poll.to_embed()

            await ctx.send(embed=embed)
        else:
            text = ['**Filter(s):**']

            SORT = {
                'id': 'id',
                'new': "metadata #>> ARRAY['kwargs', 'published'] DESC",
                'old': "metadata #>> ARRAY['kwargs', 'published'] ASC",
                'most votes': "metadata #>> ARRAY['kwargs', 'votes'] DESC",
                'least votes': "metadata #>> ARRAY['kwargs', 'votes'] ASC"
            }.get(flags.sort)

            text.append(f'Sorted by: **{flags.sort.lower()}**')
            running = "AND metadata #>> ARRAY['kwargs', 'running'] = true" if flags.active else ''
            if flags.active:
                text.append('Running: **True**')

            query = f"SELECT * FROM polls WHERE guild_id = $1 {running} ORDER BY {SORT};"
            records = await self.bot.db.fetch(query, ctx.guild.id)

            if not records:
                await ctx.send_error('No polls found matching this filter.')
                return

            if flags.keyword:
                text.append(f'Keyword: **{flags.keyword}**')
                records = [r for r in records if fuzzy.partial_ratio(
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
        polls = await self.get_guild_polls(ctx.guild.id)
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

        config: GuildConfig = await self.bot.db.get_guild_config(guild_id=ctx.guild.id)
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

        query = "SELECT * FROM polls WHERE id = $1 LIMIT 1;"
        record = await self.bot.db.fetchrow(query, poll_id)
        poll = Poll(cog=self, record=record) if record else None

        if poll:
            await self.end_poll(poll)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Polls(bot))
