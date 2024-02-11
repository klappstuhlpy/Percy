from __future__ import annotations

import datetime
import random
import re
import traceback
import warnings
from typing import TYPE_CHECKING, Any, Optional, Self, List, Dict, Literal, TypedDict, Tuple

import discord
from discord import app_commands, Interaction
from discord.ext import tasks
from discord.utils import MISSING

from .utils.converters import get_asset_url
from .utils.paginator import BasePaginator, LinePaginator
from .reminder import Timer
from .utils import timetools, converters, fuzzy, cache, helpers, commands
from .utils.context import tick
from .utils.formats import plural, get_shortened_string, betterget
from .utils.helpers import PostgresItem

if TYPE_CHECKING:
    from bot import Percy


def to_emoji(index: int) -> str:
    EMOJIS = {
        0: discord.PartialEmoji(name='A_p', id=1102737491552895077),
        1: discord.PartialEmoji(name='B_p', id=1102737574088413205),
        2: discord.PartialEmoji(name='C_p', id=1102737650185687101),
        3: discord.PartialEmoji(name='D_p', id=1102737725712515142),
        4: discord.PartialEmoji(name='E_p', id=1102737784608927865),
        5: discord.PartialEmoji(name='F_p', id=1102737843018809414),
        6: discord.PartialEmoji(name='G_p', id=1103296375371874358),
        7: discord.PartialEmoji(name='H_p', id=1103296420259311748),
    }
    return str(EMOJIS.get(index))


LINE_EMOJIS = [
    '<:lf:1103076956645363712>',
    '<:le:1103076791666610197>',
    '<:lfc:1103076698687295568>',
    '<:red_info:1113513200319733790>',
    '<:ld:1103077171158859796>'
]


def lineformat(x: int):
    if not x:
        return LINE_EMOJIS[1]

    txt = [0] * (x - 1) + [1]
    txt[0] = txt[0] + 2

    return ''.join([LINE_EMOJIS[i] for i in txt])


def uuid(ids: list[int]) -> int:
    _id = random.randint(10000, 99999)
    while _id in ids:
        _id = random.randint(10000, 99999)
    return _id


warnings.simplefilter(action='ignore', category=FutureWarning)


class VoteOption(TypedDict):
    """A vote option."""
    index: int
    content: str
    votes: int


class PollReasonModal(discord.ui.Modal, title='The Reason for you choice.'):
    def __init__(self, poll: Poll, selected_option: Dict[str, Any], bot: Percy):
        self.poll = poll
        self.bot = bot
        self.selected_option = selected_option
        super().__init__(timeout=60.0)

    reason = discord.ui.TextInput(label='Reason', placeholder='Why did you choose this option.',
                                  style=discord.TextStyle.long, min_length=1, max_length=200)

    async def on_submit(self, interaction: Interaction) -> None:
        embed = discord.Embed(title='New Poll Reason', color=self.bot.colour.darker_red())
        embed.set_thumbnail(url=get_asset_url(interaction.guild))
        embed.set_author(name=interaction.user, icon_url=get_asset_url(interaction.user))
        embed.add_field(name='Poll', value=f'{self.poll.question}\n{self.poll.jump_url}', inline=False)
        embed.add_field(name='Reason', value=self.reason.value, inline=False)
        embed.add_field(name='Selected Option',
                        value=f'{to_emoji(self.selected_option['index'])}: {self.selected_option['content']}',
                        inline=False)
        embed.set_footer(text=f'#{self.poll.kwargs.get('index')} • [{self.poll.id}]')

        await interaction.response.send_message('Thank you for submitting your response.', ephemeral=True)

        try:
            if channel := self.poll.cog.mod.get_guild_config(interaction.guild.id).poll_reason_channel:  # type: ignore
                await channel.send(embed=embed)
        except discord.HTTPException:
            pass

        self.stop()


class EditModal(discord.ui.Modal, title='Edit Poll'):
    question = discord.ui.TextInput(label='Question', placeholder='The Main Question for the poll.')
    description = discord.ui.TextInput(label='Description', placeholder='The Description for the poll.',
                                       style=discord.TextStyle.long, required=False)
    thread_question = discord.ui.TextInput(label='Thread Question', placeholder='The Question for the thread.',
                                           required=False)
    image = discord.ui.TextInput(label='Image URL', placeholder='The Image URL for the poll.', required=False)
    color = discord.ui.TextInput(label='Color', placeholder='The Color for the poll.', required=False)

    def __init__(self, poll: Poll):
        super().__init__(title=f'Edit Poll [{poll.id}]', timeout=180.0)

        self.question.default = poll.question
        self.description.default = poll.description
        self.thread_question.default = poll.kwargs.get('thread', [None, None])[1]
        self.image.default = poll.kwargs.get('image', None)
        self.color.default = poll.kwargs.get('color', None)

    async def on_submit(self, interaction: discord.Interaction):
        self.interaction = interaction  # noqa
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
            cls, interaction: discord.Interaction[Percy], item: discord.ui.Button, match: re.Match[str], /  # noqa
    ):
        cog: Optional[Polls] = interaction.client.get_cog('Polls')
        if cog is None:
            await interaction.response.send_message(
                f'{tick(False)} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{tick(False)} Polls cog is not loaded')

        poll = await cog.get_guild_poll(interaction.guild.id, int(match['id']))
        if not poll:
            await interaction.response.send_message(
                f'{tick(False)} The poll you are trying to vote on does not exist.', ephemeral=True)
            return

        return cls(poll)

    async def interaction_check(self, interaction: discord.Interaction[Percy], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f'{tick(False)} Poll was not found.', ephemeral=True)
            return False

        entry = self.poll.get_entry(interaction.user.id)
        if not entry:
            await interaction.response.send_message(
                f'You haven\'t voted on the poll *{self.poll.question}* [`{self.poll.id}`].', ephemeral=True)
            return False

        return True

    async def callback(self, interaction: Interaction) -> None:
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
                emoji=discord.PartialEmoji(name='red_info', id=1113513200319733790),
                style=discord.ButtonStyle.grey,
                row=1,
                custom_id=f'poll:info:{poll.id}'
            )
        )

    @classmethod
    async def from_custom_id(
            cls, interaction: discord.Interaction[Percy], item: discord.ui.Button, match: re.Match[str], /  # noqa
    ):
        cog: Optional[Polls] = interaction.client.get_cog('Polls')
        if cog is None:
            await interaction.response.send_message(
                f'{tick(False)} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{tick(False)} Polls cog is not loaded')

        poll = await cog.get_guild_poll(interaction.guild.id, int(match['id']))
        if not poll:
            await interaction.response.send_message(
                f'{tick(False)} The poll you are trying to vote on does not exist.', ephemeral=True)
            return

        return cls(poll)

    async def interaction_check(self, interaction: discord.Interaction[Percy], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f'{tick(False)} Poll was not found.', ephemeral=True)
            return False

        return True

    async def callback(self, interaction: Interaction) -> None:
        embed = discord.Embed(title=f'#{self.poll.kwargs.get('index')}: {self.poll.question}')
        embed.colour = discord.Colour.from_str(self.poll.kwargs.get('color'))

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
            cls, interaction: discord.Interaction[Percy], item: discord.ui.Button, match: re.Match[str], /  # noqa
    ):
        cog: Optional[Polls] = interaction.client.get_cog('Polls')
        if cog is None:
            await interaction.response.send_message(
                f'{tick(False)} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{tick(False)} Polls cog is not loaded')

        poll = await cog.get_guild_poll(interaction.guild.id, int(match['id']))
        if not poll:
            await interaction.response.send_message(
                f'{tick(False)} The poll you are trying to vote on does not exist.', ephemeral=True)
            return

        return cls(poll, int(match['index']))

    async def interaction_check(self, interaction: discord.Interaction[Percy], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f'{tick(False)} Poll was not found.', ephemeral=True)
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

    async def callback(self, interaction: Interaction) -> None:
        if not interaction.message.embeds:
            return

        option = self.poll.get_option(self.index)
        if self.poll.kwargs.get('user_reason'):
            modal = PollReasonModal(self.poll, option, interaction.client)
            await interaction.response.send_modal(modal)
            state = await modal.wait()
            if state is True:
                return await interaction.followup.send(
                    content=f'{tick(False)} This poll requires you to submit a reason for your vote.',
                    ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        self.poll.entries.add((interaction.user.id, option['index']))
        options: list[VoteOption] = self.poll.options.copy()
        options[option['index']]['votes'] += 1
        self.poll = await self.poll.edit(options=options, votes=len(self.poll.entries))

        await interaction.edit_original_response(embed=self.poll.to_embed())
        await interaction.followup.send(
            f'On the self.poll *{self.poll.question}* [`{self.poll.id}`], you voted:\n'
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
            cls, interaction: discord.Interaction[Percy], item: discord.ui.Button, match: re.Match[str], /  # noqa
    ):
        cog: Optional[Polls] = interaction.client.get_cog('Polls')
        if cog is None:
            await interaction.response.send_message(
                f'{tick(False)} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{tick(False)} Polls cog is not loaded')

        poll = await cog.get_guild_poll(interaction.guild.id, int(match['id']))
        if not poll:
            await interaction.response.send_message(
                f'{tick(False)} The poll you are trying to vote on does not exist.', ephemeral=True)
            return

        return cls(poll)

    async def interaction_check(self, interaction: discord.Interaction[Percy], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f'{tick(False)} Poll was not found.', ephemeral=True)
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

    async def callback(self, interaction: Interaction) -> None:
        if not interaction.message.embeds:
            return

        option = self.poll.get_option(int(self.values[0]))
        if self.poll.kwargs.get('user_reason'):
            modal = PollReasonModal(self.poll, option, interaction.client)
            await interaction.response.send_modal(modal)
            state = await modal.wait()
            if state is True:
                return await interaction.followup.send(
                    content=f'{tick(False)} This poll requires you to submit a reason for your vote.',
                    ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        self.poll.entries.add((interaction.user.id, option['index']))
        options: list[VoteOption] = self.poll.options.copy()
        options[option['index']]['votes'] += 1
        self.poll = await self.poll.edit(options=options, votes=len(self.poll.entries))

        await interaction.edit_original_response(embed=self.poll.to_embed())
        await interaction.followup.send(
            f'On the self.poll *{self.poll.question}* [`{self.poll.id}`], you voted:\n'
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
            cls, interaction: discord.Interaction[Percy], item: discord.ui.Button, match: re.Match[str], /  # noqa
    ):
        cog: Optional[Polls] = interaction.client.get_cog('Polls')
        if cog is None:
            await interaction.response.send_message(
                f'{tick(False)} Sorry, this button does not work at the moment. Try again later', ephemeral=True
            )
            raise AssertionError(f'{tick(False)} Polls cog is not loaded')

        return cls(int(match['role_id']))

    async def interaction_check(self, interaction: discord.Interaction[Percy], /) -> bool:
        if interaction.guild_id is None:
            return False

        if self.poll is None:
            await interaction.response.send_message(f'{tick(False)} Poll was not found.', ephemeral=True)
            return False

        return True

    async def callback(self, interaction: Interaction) -> Any:
        role = discord.Object(id=self.role_id)
        if any(r.id == self.role_id for r in interaction.user.roles):
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(
                f'{tick(True)} Successfully **removed** from you these roles: '
                f'<@&{self.role_id}>. Click again to re-add.',
                ephemeral=True
            )
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(
                f'{tick(True)} Successfully **added** you the roles: '
                f'<@&{self.role_id}>. Click again to remove.',
                ephemeral=True
            )


def create_view(poll: Poll) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    if poll.kwargs.get('running') is True:
        if len(poll.options) <= 5:
            for option in poll.options:
                view.add_item(PollEnterButton(poll, option['index']))
        else:
            view.add_item(PollEnterSelect(poll))
        view.add_item(PollClearVoteButton(poll))
    view.add_item(PollInfoButton(poll))
    return view


class PollEntry(PostgresItem):
    """Represents a poll entry."""

    user_id: int
    vote: int

    __slots__ = ('user_id', 'vote')

    def __iter__(self) -> tuple[int, int]:
        return tuple((self.user_id, self.vote))


class Poll(PostgresItem):
    """Represents a poll item."""

    id: int
    extra: Dict[str, Any]
    channel_id: int
    message_id: int
    guild_id: int
    entries: set[tuple[int, int]]
    args: List[Any]
    kwargs: dict[str, Any]
    message: discord.Message
    question: str
    votes: int
    description: str
    options: List[list[VoteOption]]

    __slots__ = (
        'cog', 'bot', 'id', 'extra', 'channel_id', 'message_id', 'guild_id', 'entries',
        'args', 'kwargs', 'message', 'ping_message', 'question', 'votes', 'description', 'options'
    )

    def __init__(self, cog: Polls, **kwargs):
        self.cog: Polls = cog
        self.bot: Percy = cog.bot
        super().__init__(**kwargs)

        self.args: List[Any] = self.extra.get('args', [])
        self.kwargs: dict[str, Any] = self.extra.get('kwargs', {})

        self.message: discord.Message = MISSING
        self.ping_message: discord.Message = MISSING

        self.question: str = self.kwargs.get('question')
        self.votes: int = self.kwargs.get('votes', 0)
        self.description: str = self.kwargs.get('description')
        self.options: List[VoteOption] = self.kwargs.get('options', [])

        self.entries = set(PollEntry(record=entry).__iter__() for entry in self.entries or [])

    @property
    def jump_url(self) -> Optional[str]:
        """The jump URL of the poll."""
        if self.message_id and self.channel_id:
            guild = self.guild_id or '@me'
            return f'https://discord.com/channels/{guild}/{self.channel_id}/{self.message_id}'
        return None

    @property
    def channel(self) -> Optional[discord.TextChannel]:
        """The channel of the poll."""
        if self.channel_id is not None:
            return self.bot.get_channel(self.channel_id)
        return None

    @property
    def choice_text(self) -> str:
        """The text to use for the autocomplete."""
        return f'[{self.id}] {self.question}'

    @property
    def published(self) -> datetime.datetime:
        """The published date of the poll."""
        return datetime.datetime.fromisoformat(self.kwargs.get('published'))

    def get_option(self, index: int) -> Optional[VoteOption]:
        """Gets an option from the poll.

        Parameters
        ----------
        index: int
            The index of the option to get.

        Returns
        -------
        Optional[VoteOption]
            The option from the poll.
        """
        return next((option for option in self.options if option['index'] == index), None)

    def get_entry(self, user_id: int) -> Optional[tuple[int, int]]:
        """Gets the vote of a user.

        Parameters
        ----------
        user_id: int
            The user ID to get the vote of.

        Returns
        -------
        Optional[tuple[int, int]]
            The vote of the user.
        """
        return next(((user, vote) for (user, vote) in self.entries if user == user_id), None)

    def to_fields(self, extras: bool = True) -> list[dict]:
        """Converts the poll to fields."""
        fields = []
        for i, option in enumerate(self.options):
            v = option['votes']
            max_length = 10
            votes = self.votes

            p = v / votes if votes else 0
            x = (v * max_length) // votes if votes else 0

            fields.append({
                'name': f'{LINE_EMOJIS[4]} ' + option['content'],
                'value': f'{to_emoji(option['index'])}{lineformat(x)} **{v}** {plural(v, pass_content=True):vote} ({round(p * 100)}%)',
                'inline': False
            })

        if extras:
            fields.append({'name': 'Voting', 'value': f'Total Votes: **{self.votes}**', 'inline': True})
            if expires := betterget(self.kwargs, 'expires'):
                fields.append(
                    {'name': 'Poll ends', 'value': discord.utils.format_dt(expires, 'R'), 'inline': True})
            if thread := self.kwargs.get('thread'):
                fields.append({'name': 'Discussion in Thread:', 'value': thread[1], 'inline': True})

        return fields

    async def fetch_message(self) -> None:
        """Fetches the message of the poll."""
        channel = self.channel
        if channel is not None and self.message_id is not None:
            message = await self.cog.get_message(channel, self.message_id)
            if (ping_message_id := self.kwargs.get('ping_message_id')) is not None:
                ping_message = await self.cog.get_message(channel, ping_message_id)
                if ping_message:
                    self.ping_message = ping_message
            if message:
                self.message = message

    def to_embed(self) -> discord.Embed:
        """Converts the poll to an embed."""
        embed = discord.Embed(
            title=self.question,
            description=self.description,
            timestamp=betterget(self.kwargs, 'published')
        )
        embed.set_image(url=self.kwargs.get('image'))
        embed.colour = discord.Colour.from_str(self.kwargs.get('color'))

        for field in self.to_fields():
            embed.add_field(**field)

        embed.set_footer(text=f'#{self.kwargs.get('index')} • [{self.id}]')
        return embed

    def remove_option(self, option: VoteOption = MISSING) -> Optional[list[VoteOption]]:
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
            question: Optional[str] = MISSING,
            description: Optional[str] = MISSING,
            thread: Optional[List[int, str]] = MISSING,
            image_url: Optional[str] = MISSING,
            color: Optional[str] = MISSING,
            options: Optional[list[VoteOption]] = MISSING,
            running: Optional[bool] = MISSING,
            votes: Optional[int] = MISSING,
    ) -> Self:
        """|coro|

        Edits the poll.

        Parameters
        ----------
        question: Optional[str]
            The question to update the poll with.
        description: Optional[str]
            The description to update the poll with.
        thread: Optional[List[int, str]]
            The thread to update the poll with.
        image_url: Optional[str]
            The image URL to update the poll with.
        color: Optional[str]
            The color to update the poll with.
        options: Optional[List[Tuple[Dict[str, Any] | None, EditType, int | None]]]
            The options to update the poll with.
        running: Optional[bool]
            The running status to update the poll with.
        votes: Optional[int]
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

        self.extra.get('kwargs').update(form)

        query = "UPDATE polls SET extra = $1::jsonb, entries = $2 WHERE id = $3;"
        await self.bot.pool.execute(query, self.extra, self.entries, self.id)

        return self

    async def delete(self) -> None:
        """Deletes the poll."""
        query = "DELETE FROM polls WHERE id = $1;"
        await self.bot.pool.execute(query, self.id)

        if self.message_id is not None and self.message is MISSING:
            await self.fetch_message()

        if self.message:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass

        self.cog.get_guild_polls.invalidate(self, self.guild_id)


class Polls(commands.Cog):
    """Poll voting system."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        self._message_cache: dict[int, discord.Message] = {}
        self.cooldown: commands.CooldownMapping = commands.CooldownMapping.from_cooldown(
            2, 5, lambda interaction: interaction.user)

        bot.add_dynamic_items(
            PollEnterButton, PollEnterSelect, PollClearVoteButton, PollInfoButton, PollRolePingButton)

    async def cog_load(self) -> None:
        self.cleanup_message_cache.start()

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='\N{BAR CHART}')

    @tasks.loop(hours=1.0)
    async def cleanup_message_cache(self):
        self._message_cache.clear()

    async def get_message(
            self,
            channel: discord.abc.Messageable,
            message_id: int
    ) -> Optional[discord.Message]:
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
            self, poll_id: int, channel_id: int, message_id: int, guild_id: int, /, *args: Any, **kwargs: Any
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
        poll = Poll.temporary(
            self,
            channel_id=channel_id,
            message_id=message_id,
            guild_id=guild_id,
            entries=set(),
            extra={'args': args, 'kwargs': kwargs}
        )

        query = """
            INSERT INTO polls (id, channel_id, message_id, guild_id, extra)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id;
        """

        poll.id = await self.bot.pool.fetchval(
            query, poll_id, channel_id, message_id, guild_id, {'args': args, 'kwargs': kwargs})

        self.get_guild_polls.invalidate(self, guild_id)
        return poll

    async def get_guild_poll(self, guild_id: int, poll_id: int) -> Optional[Poll]:
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
        record = await self.bot.pool.fetchrow(query, poll_id, guild_id)
        return Poll(self, record=record) if record else None

    @cache.cache()
    async def get_guild_polls(self, guild_id: int) -> List[Poll]:
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
        return [Poll(self, record=record) for record in await self.bot.pool.fetch(query, guild_id)]

    async def end_poll(self, poll: Poll) -> Optional[int]:
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
        await self.bot.reminder.delete_timer('poll', poll_id=str(poll.id))

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

            if thread := poll.kwargs.get('thread'):
                channel = poll.channel
                if channel:
                    thread = channel.get_thread(thread[0])
                    await thread.edit(archived=True, locked=True)

            try:
                await poll.message.edit(embed=embed, view=create_view(poll))
                if poll.ping_message:
                    await poll.ping_message.delete()
            except discord.HTTPException:
                pass

        self.get_guild_polls.invalidate(self, poll.guild_id)
        return poll.id

    polls = app_commands.Group(name='polls', description='Commands for managing polls.', guild_only=True)

    @commands.command(
        polls.command,
        name='create',
        description='Creates a new poll with customizable settings.',
    )
    @commands.permissions(user=['ban_members', 'manage_messages'])
    @app_commands.describe(
        question='Main Poll Question to ask.',
        description='Additional notes/description about the question.',
        opt_1='Option 1.', opt_2='Option 2.', opt_3='Option 3.', opt_4='Option 4.',
        opt_5='Option 5.', opt_6='Option 6.', opt_7='Option 7.', opt_8='Option 8.',
        thread_question='Question to ask in the accompanying Thread.',
        image='Image to accompany Poll Question.',
        image_url='Image as URL (alternative to upload)',
        when='When to end the poll.',
        color='Color of the embed.',
        channel='Channel to post the poll in if no channel is set in the configuration.',
        ping='Whether to ping the role.',
        user_reason='Whether to ask for a reason on vote.',
    )
    async def polls_create(
            self,
            interaction: discord.Interaction,
            question: str,
            when: app_commands.Transform[datetime.datetime, timetools.TimeTransformer],
            description: str = None,
            color: app_commands.Transform[discord.Colour, converters.ColorTransformer] = helpers.Colour.darker_red(),
            channel: discord.TextChannel = None,
            thread_question: str = None,
            ping: bool = False,
            user_reason: bool = False,
            image: discord.Attachment = None,
            image_url: app_commands.Transform[str, converters.URLConverter] = None,
            opt_1: str = None, opt_2: str = None, opt_3: str = None, opt_4: str = None,
            opt_5: str = None, opt_6: str = None, opt_7: str = None, opt_8: str = None
    ):
        """Creates a poll with customizable settings."""
        await interaction.response.defer()

        config = await self.bot.moderation.get_guild_config(interaction.guild.id)
        if not channel and (not config or config and not config.poll_channel):
            return await interaction.followup.send(
                f'{tick(False)} You must set a poll channel first or use the `channel` parameter.')
        else:
            channel = channel or config.poll_channel

        image_url = image_url or (image.proxy_url if image else None)
        options = list(filter(lambda x: x is not None, [opt_1, opt_2, opt_3, opt_4, opt_5, opt_6, opt_7, opt_8]))

        if len(options) < 2:
            return await interaction.followup.send(f'{tick(False)} You must provide at least 2 options.')

        to_options = [VoteOption(index=index, content=content, votes=0) for index, content in enumerate(options)]

        message = await channel.send(embed=discord.Embed(description='*Preparing Poll...*'))
        ping_message = None
        if ping:
            ping_message = await channel.send(f'*...*')

        new_index = len(await self.get_guild_polls(guild_id=interaction.guild.id)) + 1
        unique_id = uuid([rec[0] for rec in await self.bot.pool.fetch('SELECT id FROM polls')])

        if thread_question:
            thread = await message.create_thread(name=question, auto_archive_duration=4320)
            thread_message = await thread.send(thread_question)
            await thread_message.pin(reason='Poll Discussion')

        if user_reason and config and not config.poll_reason_channel:
            return await interaction.followup.send(
                f'{tick(False)} You must set a poll reason channel if you want to set user reasons.'
            )

        if ping and config and not config.poll_ping_role_id:
            return await interaction.followup.send(f'{tick(False)} You must set a ping role to set pings.')

        poll = await self.create_poll(
            unique_id,
            channel.id,
            message.id,
            interaction.guild.id,
            interaction.user.id,
            ping_message_id=ping_message.id if ping else None,
            question=question,
            description=description,
            options=to_options,
            thread=[thread.id, thread_question] if thread_question else [],  # noqa
            user_reason=user_reason,
            image=image_url,
            color=str(color),
            votes=0,
            index=new_index,
            running=True,
            published=discord.utils.utcnow().isoformat(),
            expires=when.isoformat(),
        )

        reminder = self.bot.reminder
        if reminder is None:
            return await interaction.followup.send(
                f'{tick(False)} The Timer function is currently unavailable, please wait or contact '
                'the Bot Developer if this problem persists.')
        else:
            uconfig = await self.bot.user_settings.get_user_config(interaction.user.id)
            zone = uconfig.timezone if uconfig else None
            await reminder.create_timer(
                when,
                'poll',
                poll_id=poll.id,
                created=discord.utils.utcnow(),
                timezone=zone or 'UTC',
            )

        await interaction.followup.send(
            f'{tick(True)} Poll #{new_index} [`{poll.id}`] successfully created. {message.jump_url}')

        await message.edit(embed=poll.to_embed(), view=create_view(poll))

        if ping:
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

    @polls_create.error
    async def polls_create_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, timetools.BadTimeTransform):
            await interaction.response.send_message(str(error), ephemeral=True)

    @commands.command(
        polls.command,
        name='end',
        description='Ends the voting for a running question.',
    )
    @commands.permissions(user=['ban_members', 'manage_messages'])
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)
    @app_commands.describe(poll_id='5-digit ID of the poll to end.')
    async def polls_end(self, interaction: discord.Interaction, poll_id: int):
        """Ends a poll."""
        await interaction.response.defer()

        poll = await self.get_guild_poll(interaction.guild.id, poll_id)
        if poll is None:
            return await interaction.followup.send(f'{tick(False)} Poll not found.', ephemeral=True)

        check = await self.end_poll(poll)
        if check is None:
            return await interaction.followup.send(f'{tick(False)} Poll is already ended.', ephemeral=True)

        await interaction.followup.send(f'{tick(True)} Poll [`{check}`] has been ended.')

    @commands.command(
        polls.command,
        name='delete',
        description='Deletes a poll question.',
    )
    @commands.permissions(user=['ban_members', 'manage_messages'])
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)
    @app_commands.describe(poll_id='5-digit ID of the poll to delete.')
    async def polls_delete(self, interaction: discord.Interaction, poll_id: int):
        """Deletes a poll question."""
        poll = await self.get_guild_poll(interaction.guild.id, poll_id)
        if poll is None:
            return await interaction.response.send_message(f'{tick(False)} Poll not found.', ephemeral=True)

        await poll.delete()
        await interaction.response.send_message(f'{tick(True)} Poll [`{poll_id}`] has been deleted.')

    @commands.command(
        polls.command,
        name='edit',
        description='Edits a poll question. Type "-clear" to clear the current value.',
    )
    @commands.permissions(user=['ban_members', 'manage_messages'])
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)
    @app_commands.describe(
        poll_id='5-digit ID of the poll to search for.',
        question='The new question to ask.',
        description='The new description to use.',
        color='The new color to use.',
        thread_question='The new thread question to use.',
        image='The new image to use.',
        image_url='The new image URL to use.',
        opt_1='Option 1.', opt_2='Option 2.', opt_3='Option 3.', opt_4='Option 4.',
        opt_5='Option 5.', opt_6='Option 6.', opt_7='Option 7.', opt_8='Option 8.',
    )
    async def polls_edit(
            self,
            interaction: discord.Interaction,
            poll_id: int,
            question: str = None,
            description: str = None,
            color: app_commands.Transform[str, converters.ColorTransformer] = None,
            thread_question: str = None,
            image: discord.Attachment = None,
            image_url: app_commands.Transform[str, converters.URLConverter] = None,
            opt_1: str = None, opt_2: str = None, opt_3: str = None, opt_4: str = None,
            opt_5: str = None, opt_6: str = None, opt_7: str = None, opt_8: str = None
    ):
        """Edits a poll question.

        You can also remove the following fields by typing `--clear` as the value to change.

        Possible Parameters to remove:
        - Question
        - Description
        - Any not None Field
        - Thread
        """
        await interaction.response.defer()

        poll = await self.get_guild_poll(interaction.guild.id, poll_id)
        if not poll:
            return await interaction.followup.send(f'{tick(False)} Poll not found.', ephemeral=True)

        if not poll.kwargs.get('running'):
            return await interaction.followup.send(f'{tick(False)} Poll is already ended.', ephemeral=True)

        if poll.message_id is not None and poll.message is MISSING:
            await poll.fetch_message()

        open_thread = poll.kwargs.get('thread')
        form: dict[str, Any] = {}

        if all(value is None for value in [
            question, description, thread_question, image, image_url,
            opt_1, opt_2, opt_3, opt_4, opt_5, opt_6, opt_7, opt_8
        ]):
            modal = EditModal(poll)
            await interaction.response.send_modal(modal)
            await modal.wait()
            interaction = modal.interaction

            if modal.question.value != poll.question:
                form['question'] = modal.question.value
            if modal.description.value != poll.description:
                form['description'] = modal.description.value
            if modal.color.value != poll.kwargs.get('color'):
                form['color'] = modal.color.value

            if modal.thread_question.value != open_thread[1] if open_thread else None:
                if open_thread:
                    thread = poll.channel.get_thread(open_thread[0])

                    if modal.thread_question.value == '--clear':
                        if thread:
                            await thread.edit(archived=True, locked=True)

                        form['thread'] = []
                    else:
                        if thread:
                            msg = [msg async for msg in thread.history(limit=2, oldest_first=True)][1]
                            if msg.author.id == self.bot.user.id:
                                await msg.edit(content=modal.thread_question.value)

                        form['thread'] = [thread.id, modal.thread_question.value]
                else:
                    thread = await poll.message.create_thread(name=poll.question, auto_archive_duration=4320)
                    thread_message = await thread.send(modal.thread_question.value)
                    await thread_message.pin(reason='Poll Discussion')

                    form['thread'] = [thread.id, modal.thread_question.value]

            if modal.image.value != poll.kwargs.get('image'):
                form['image_url'] = modal.image.value
        else:
            if question:
                if question != '--clear':
                    form['question'] = question

            if description:
                if description == '--clear':
                    form['description'] = None
                else:
                    form['description'] = description

            if image or image_url:
                image_url = image_url or (image.proxy_url if image else None)
                form['image_url'] = image_url

            if color:
                form['color'] = str(color)

            if thread_question:
                if open_thread:
                    thread = poll.channel.get_thread(open_thread[0])

                    if thread_question == '--clear':
                        if thread:
                            await thread.edit(archived=True, locked=True)

                        form['thread'] = []
                    else:
                        if thread:
                            msg = [msg async for msg in thread.history(limit=2, oldest_first=True)][1]
                            if msg.author.id == self.bot.user.id:
                                await msg.edit(content=thread_question)

                        form['thread'] = [thread.id, thread_question]
                else:
                    thread = await poll.message.create_thread(name=poll.question, auto_archive_duration=4320)
                    thread_message = await thread.send(thread_question)
                    await thread_message.pin(reason='Poll Discussion')

                    form['thread'] = [thread.id, thread_question]

        options: list[VoteOption] = poll.options.copy()
        for index, content in enumerate([opt_1, opt_2, opt_3, opt_4, opt_5, opt_6, opt_7, opt_8]):
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

        await interaction.followup.send(f'{tick(True)} Poll [`{poll.id}`] edited successfully.', ephemeral=True)

    @commands.command(
        polls.command,
        name='search',
        description='Searches poll questions. Search by ID, keyword or flags.',
    )
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)
    @app_commands.describe(
        poll_id='The ID of the poll to search for.',
        keyword='The keyword to search for.',
        sort='The sorting method to use.',
        active='Whether to search for active polls.',
        showextrainfo='Whether to show extra information about the poll. (Only for Admins)',
    )
    async def polls_search(
            self,
            interaction: discord.Interaction,
            poll_id: int = None,
            keyword: str = None,
            sort: Literal['Poll ID', 'Newest', 'Oldest', 'Most Votes', 'Least Votes'] = 'Newest',
            active: bool = False,
            showextrainfo: bool = False,
    ):
        """Searches poll questions. Search by ID, keyword or flags."""
        await interaction.response.defer()

        if poll_id:
            poll = await self.get_guild_poll(interaction.guild.id, poll_id)
            if not poll:
                return await interaction.followup.send(f'{tick(False)} Poll not found.', ephemeral=True)

            if showextrainfo and interaction.channel.permissions_for(interaction.user).manage_messages:
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
                    value=discord.utils.format_dt(betterget(poll.kwargs, 'published'), 'f'))
                embed.add_field(
                    name='Poll ends' if running else 'Poll finished',
                    value=discord.utils.format_dt(betterget(poll.kwargs, 'expires'), 'R'))

                embed.add_field(name='Poll Message',
                                value=poll.jump_url or f'Can\'t locate message `{poll.message_id}`')
                embed.add_field(name='User Reason', value=poll.kwargs.get('user_reason'))

                if thread := poll.kwargs.get('thread'):
                    embed.add_field(name='Thread Question', value=thread[1])

                embed.set_image(url=poll.kwargs.get('image'))
                embed.colour = discord.Colour.from_str(poll.kwargs.get('color'))

                embed.set_footer(text=f'[{poll.id}] • {poll.guild_id}')
            else:
                embed = poll.to_embed()

            await interaction.followup.send(embed=embed)
        else:
            text = ['**Filter(s):**']

            SORT = {
                'Poll ID': 'id',
                'Newest': "extra #>> ARRAY['kwargs', 'published'] DESC",
                'Oldest': "extra #>> ARRAY['kwargs', 'published'] ASC",
                'Most Votes': "extra #>> ARRAY['kwargs', 'votes'] DESC",
                'Least Votes': "extra #>> ARRAY['kwargs', 'votes'] ASC"
            }.get(sort)

            text.append(f'Sorted by: **{sort.lower()}**')
            running = f"AND extra #>> ARRAY['kwargs', 'running'] = true" if active else ""
            if active:
                text.append('Running: **True**')

            query = f"SELECT * FROM polls WHERE guild_id = $1 {running} ORDER BY {SORT};"
            records = await self.bot.pool.fetch(query, interaction.guild.id)

            if not records:
                return await interaction.followup.send(
                    f'{tick(False)} No polls found matching this filter.', ephemeral=True)

            if keyword:
                text.append(f'Keyword: **{keyword}**')
                records = [r for r in records if fuzzy.partial_ratio(
                    keyword.lower(), r['extra']['kwargs'].get('question').lower()) > 70]

            def fmt_poll(_poll: Poll) -> str:
                fmt_timestamp = discord.utils.format_dt(_poll.published, 'd')
                return f'`{_poll.id}` (`#{_poll.kwargs.get("index")}`): {_poll.question} ({fmt_timestamp})'

            results = [fmt_poll(poll) for poll in [Poll(self, record=r) for r in records]]
            embed = discord.Embed(
                title='Poll Search',
                description='\n'.join(text),
                colour=helpers.Colour.darker_red(),
                timestamp=discord.utils.utcnow()
            )
            embed.set_thumbnail(url=get_asset_url(interaction.guild))
            embed.set_footer(text=f'{plural(len(records)):entry|entries}')

            await LinePaginator.start(interaction, entries=results, per_page=12, embed=embed)

    @commands.command(
        polls.command,
        name='history',
        description='Shows the vote history of a user for polls.',
    )
    @app_commands.describe(member='The Member to show the history for.')
    async def polls_history(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        """Shows the vote history of a user for polls."""
        polls = await self.get_guild_polls(guild_id=interaction.guild.id)

        if not polls:
            return await interaction.response.send_message(
                f'{tick(False)} You haven\'t voted in this guild yet.', ephemeral=True)

        member = member or interaction.user
        user_polls = list(filter(lambda poll: any(x[0] == member.id for x in poll.users), polls))

        class FieldPaginator(BasePaginator[Poll]):

            async def format_page(self, entries: List[Poll], /) -> discord.Embed:
                embed = discord.Embed(
                    title=f'Poll History for {member}',
                    colour=helpers.Colour.darker_red(),
                    timestamp=discord.utils.utcnow())
                embed.set_footer(text=f'{plural(len(polls)):entry|entries}')

                for poll in entries:
                    vote = next(option for (user, option) in poll.entries if user == member.id)
                    embed.add_field(
                        name=f'{poll.id} (#{poll.kwargs.get('index')}): {poll.question}',
                        value=f'You\'ve voted: {to_emoji(poll.options[vote]['index'])} - '
                              f'*{poll.options[vote]['content']}*',
                        inline=False)

                return embed

        await FieldPaginator.start(interaction, entries=user_polls, per_page=12)

    @commands.command(
        polls.command,
        name='debug',
        description='Refactor all existing Polls in this guild and reattach the views.',
    )
    @commands.permissions(user=['ban_members', 'manage_messages'])
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)
    @app_commands.describe(poll_id='The ID of the Poll to debug.')
    @app_commands.checks.cooldown(1, 15.0, key=lambda i: i.guild_id)
    async def polls_debug(self, interaction: discord.Interaction, poll_id: int):
        """Refactor all existing Polls in this guild and reattach the views."""
        poll = await self.get_guild_poll(interaction.guild.id, poll_id)

        if not poll:
            return await interaction.response.send_message(
                f'{tick(False)} Poll not found.', ephemeral=True)

        if poll.guild_id != interaction.guild.id:
            return await interaction.response.send_message(
                f'{tick(False)} Poll not found.', ephemeral=True)

        embed = poll.to_embed()
        await poll.fetch_message()
        if poll.message:
            await poll.message.edit(embed=embed, view=create_view(poll))

        await interaction.response.send_message(f'{tick(True)} Poll [`{poll.id}`] debugged.', ephemeral=True)

    @commands.command(
        polls.command,
        name='config',
        description='Shows the current configuration for polls.',
    )
    @app_commands.rename(
        reason_channel='reason-channel',
        ping_role='ping-role'
    )
    @app_commands.describe(
        channel='The channel to post polls in.',
        reason_channel='The channel to ask for a reason on vote.',
        ping_role='The role to ping for polls.',
        reset='Whether to reset the configuration.'
    )
    @commands.permissions(user=['ban_members', 'manage_messages'])
    async def polls_config(
            self,
            interaction: discord.Interaction,
            channel: discord.TextChannel = None,
            reason_channel: discord.TextChannel = None,
            ping_role: discord.Role = None,
            reset: bool = False
    ):
        """Shows/Changes the current configuration for polls."""
        await interaction.response.defer()

        config = await self.bot.moderation.get_guild_config(guild_id=interaction.guild.id)

        if not config:
            return await interaction.followup.send(f'{tick(False)} No configuration found.', ephemeral=True)

        if all(i is None for i in [channel, reason_channel, ping_role]):
            embed = discord.Embed(title='Poll Configuration',
                                  colour=helpers.Colour.darker_red(),
                                  timestamp=discord.utils.utcnow())
            embed.add_field(name='Poll Channel',
                            value=f'<#{config.poll_channel_id}>' if config.poll_channel_id else 'N/A')
            embed.add_field(name='Poll Reason Channel',
                            value=f'<#{config.poll_reason_channel_id}>' if config.poll_reason_channel_id else 'N/A')
            embed.add_field(name='Poll Role',
                            value=f'<@&{config.poll_ping_role_id}>' if config.poll_ping_role_id else 'N/A')
            embed.set_footer(text=f'Use "/polls config" to change the configuration.')
            return await interaction.followup.send(embed=embed)
        else:
            if reset:
                kwargs = {
                    'poll_channel_id': None,
                    'poll_reason_channel_id': None,
                    'poll_ping_role_id': None
                }
                content = f'{tick(True)} Poll configuration reset.'
            else:
                kwargs = {}
                if channel:
                    kwargs['poll_channel_id'] = channel.id
                if reason_channel:
                    kwargs['poll_reason_channel_id'] = reason_channel.id
                if ping_role:
                    kwargs['poll_ping_role_id'] = ping_role.id

                content = f'{tick(True)} Poll configuration updated.'

            updates = ', '.join(f'{k} = ${i}' for i, k in enumerate(kwargs.keys(), start=2))
            query = f"UPDATE guild_config SET {updates} WHERE id = $1;"
            await self.bot.pool.execute(query, interaction.guild.id, *list(kwargs.values()))
            self.bot.moderation.get_guild_config.invalidate(self.bot.moderation, interaction.guild.id)
            return await interaction.followup.send(content)

    @commands.Cog.listener()
    async def on_poll_timer_complete(self, timer: Timer) -> None:
        """Called when a Poll timer completes.

        Parameters
        ----------
        timer: Timer
            The Timer object that completed.
        """

        await self.bot.wait_until_ready()
        poll_id = timer.kwargs.get('poll_id')

        query = "SELECT * FROM polls WHERE id = $1 LIMIT 1;"
        record = await self.bot.pool.fetchrow(query, poll_id)
        poll = Poll(self, record=record) if record else None

        if poll:
            await self.end_poll(poll)


async def setup(bot: Percy):
    await bot.add_cog(Polls(bot))
