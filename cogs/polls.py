from __future__ import annotations

import datetime
import enum
import random
import traceback
import warnings
from typing import TYPE_CHECKING, Any, Optional, Self, List, Dict, Literal, TypedDict, Tuple

import discord
from discord import app_commands, Interaction
from discord.ext import commands, tasks
from discord.utils import MISSING

from cogs.utils.paginator import BasePaginator, LinePaginator
from . import command, command_permissions
from .reminder import Timer
from .utils import timetools, converters, fuzzy, cache, helpers
from .utils.context import Context
from .utils.converters import colour_autocomplete
from .utils.formats import plural, get_shortened_string
from .utils.helpers import PostgresItem

if TYPE_CHECKING:
    from bot import Percy


def to_emoji(index: int) -> str:
    EMOJIS = {
        0: discord.PartialEmoji(name="A_p", id=1102737491552895077),
        1: discord.PartialEmoji(name="B_p", id=1102737574088413205),
        2: discord.PartialEmoji(name="C_p", id=1102737650185687101),
        3: discord.PartialEmoji(name="D_p", id=1102737725712515142),
        4: discord.PartialEmoji(name="E_p", id=1102737784608927865),
        5: discord.PartialEmoji(name="F_p", id=1102737843018809414),
        6: discord.PartialEmoji(name="G_p", id=1103296375371874358),
        7: discord.PartialEmoji(name="H_p", id=1103296420259311748),
    }
    return str(EMOJIS.get(index))


tick = Context.tick  # tick link because we have only app commands here


LINE_EMOJIS = ['<:lf:1103076956645363712>', '<:le:1103076791666610197>', '<:lfc:1103076698687295568>',
               '<:red_info:1113513200319733790>', '<:ld:1103077171158859796>']


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


class EditType(enum.Enum):
    """The type of editing to perform."""
    CONTENT = 1
    DELETE = 2
    VOTES = 3


class VoteOption(TypedDict):
    """A vote option."""
    index: int
    content: str
    votes: int


async def interaction_check(poll: PollItem, interaction: Interaction) -> bool:
    entry = next((i for i in poll.users if i[0] == interaction.user.id), None)
    if entry:
        option = next((i for i in poll.options if i["index"] == entry[1]), None)
        await interaction.response.send_message(
            f"On the poll *{poll.question}* [`{poll.id}`], you voted:\n"
            f"{to_emoji(option['index'])} - `{option['content']}`",
            ephemeral=True)
        return False
    return True


class PollReasonModal(discord.ui.Modal, title="The Reason for you choice."):
    def __init__(self, poll: PollItem, selected_option: Dict[str, Any], bot: Percy):
        super().__init__(timeout=60.0)
        self.poll = poll
        self.selected_option = selected_option
        self.bot = bot

    reason = discord.ui.TextInput(label="Reason", placeholder="Why did you choose this option.",
                                  style=discord.TextStyle.long, min_length=1, max_length=200)

    async def on_submit(self, interaction: Interaction) -> None:
        embed = discord.Embed(title="New Poll Reason", color=self.bot.colour.darker_red())
        embed.set_thumbnail(url=interaction.guild.icon.url)
        embed.set_author(name=interaction.user, icon_url=interaction.user.avatar.url)
        embed.add_field(name="Poll", value=f"{self.poll.question}\n{self.poll.jump_url}", inline=False)
        embed.add_field(name="Reason", value=self.reason.value, inline=False)
        embed.add_field(name="Selected Option",
                        value=f"{to_emoji(self.selected_option['index'])}: {self.selected_option['content']}",
                        inline=False)
        embed.set_footer(text=f"#{self.poll.kwargs.get('index')} • [{self.poll.id}]")

        await interaction.response.send_message("Thank you for submitting your response.", ephemeral=True)

        try:
            if channel := self.poll.cog.mod.get_guild_config(interaction.guild.id).poll_reason_channel:  # type: ignore
                await channel.send(embed=embed)
        except discord.HTTPException:
            pass

        self.stop()


class ClearVoteButton(discord.ui.Button):
    def __init__(self, bot: Percy, poll: PollItem):
        self.poll = poll
        self.bot = bot

        super().__init__(label="Clear Vote", style=discord.ButtonStyle.red,
                         custom_id=f"poll:{poll.id}:clear_vote", row=1)

    async def callback(self, interaction: Interaction) -> None:
        if not interaction.message.embeds:
            return

        user = next((i for i in self.poll.users if i[0] == interaction.user.id), None)

        if not user:
            return await interaction.response.send_message(
                f"You haven't voted on the poll *{self.poll.question}* [`{self.poll.id}`].",
                ephemeral=True)

        user_option = next((i for i in self.poll.options if i["index"] == user[1]), None)
        self.poll.users.remove(user)

        await self.poll.edit(options=[(user_option, EditType.VOTES, user_option['votes'] - 1)],
                             votes=self.poll.votes - 1)

        await interaction.response.edit_message(embed=self.poll.to_embed())


class PollInfoButton(discord.ui.Button):
    def __init__(self, bot: Percy, poll: PollItem):
        self.poll = poll
        self.bot = bot

        super().__init__(emoji=discord.PartialEmoji(name="red_info", id=1113513200319733790),
                         style=discord.ButtonStyle.grey,
                         custom_id=f"poll:{poll.id}:info", row=1)

    async def callback(self, interaction: Interaction) -> None:
        embed = discord.Embed(title=f"#{self.poll.kwargs.get('index')}: {self.poll.question}")
        embed.colour = discord.Colour.from_str(self.poll.kwargs.get('color'))

        value = [field["value"] for field in self.poll.to_fields(extras=False)]
        embed.add_field(name="Votes", value="\n".join(value))

        entry = next((i for i in self.poll.users if i[0] == interaction.user.id), None)

        text = "You haven't voted yet." if self.poll.kwargs.get('running') is True else "You didn't vote."
        if entry:
            option = next((i for i in self.poll.options if i["index"] == entry[1]), None)
            text = f"You've voted: {to_emoji(option['index'])} *{option['content']}*"

        embed.add_field(name="Your Vote", value=text, inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)


class PollEnterButton(discord.ui.Button):
    def __init__(self, bot: Percy, poll: PollItem, index: int):
        self.poll = poll
        self.bot = bot
        self.index = index

        super().__init__(emoji=to_emoji(index), style=discord.ButtonStyle.gray,
                         custom_id=f"poll:{poll.id}:opt_{index}")

    async def callback(self, interaction: Interaction) -> None:
        if not await interaction_check(self.poll, interaction):
            return

        if not interaction.message.embeds:
            return

        current_option = next((i for i in self.poll.options if i["index"] == self.index), None)

        is_expired = False
        if self.poll.kwargs.get("user_reason"):
            is_expired = True
            modal = PollReasonModal(self.poll, current_option, self.bot)
            await interaction.response.send_modal(modal)
            state = await modal.wait()
            if state is True:
                return await interaction.followup.send(
                    content="<:redTick:1079249771975413910> This poll requires a reason to vote.",
                    ephemeral=True)

        self.poll.users.append([interaction.user.id, current_option["index"]])
        await self.poll.edit(options=[(current_option, EditType.VOTES, current_option['votes'] + 1)],
                             votes=self.poll.votes + 1)

        if is_expired:
            await interaction.edit_original_response(embed=self.poll.to_embed())
        else:
            await interaction.response.edit_message(embed=self.poll.to_embed())

        await interaction.followup.send(
            f"On the poll *{self.poll.question}* [`{self.poll.id}`], you voted:\n"
            f"{to_emoji(current_option['index'])} - `{current_option['content']}`",
            ephemeral=True)


class PollEnterSelect(discord.ui.Select):
    def __init__(self, bot: Percy, poll: PollItem):
        self.poll = poll
        self.bot = bot

        options = [
            discord.SelectOption(
                label=option["content"], value=str(option["index"]), emoji=to_emoji(option["index"])
            ) for option in self.poll.options
        ]

        super().__init__(placeholder="Click here to vote.", custom_id=f"poll:{poll.id}:select",
                         options=options, row=0)

    async def callback(self, interaction: Interaction) -> None:
        if not await interaction_check(self.poll, interaction):
            return

        if not interaction.message.embeds:
            return

        current_option = next((i for i in self.poll.options if i["index"] == int(self.values[0])), None)

        is_expired = False
        if self.poll.kwargs.get("user_reason"):
            is_expired = True
            modal = PollReasonModal(self.poll, current_option, self.bot)
            await interaction.response.send_modal(modal)
            state = await modal.wait()
            if state is True:
                return await interaction.followup.send(
                    content="<:redTick:1079249771975413910> This poll requires a reason to vote.",
                    ephemeral=True)

        self.poll.users.append([interaction.user.id, current_option["index"]])
        await self.poll.edit(options=[(current_option, EditType.VOTES, current_option['votes'] + 1)],
                             votes=self.poll.votes + 1)

        if is_expired:
            await interaction.edit_original_response(embed=self.poll.to_embed())
        else:
            await interaction.response.edit_message(embed=self.poll.to_embed())

        await interaction.followup.send(
            f"On the poll *{self.poll.question}* [`{self.poll.id}`], you voted:\n"
            f"{to_emoji(current_option['index'])} - `{current_option['content']}`",
            ephemeral=True)


class PollView(discord.ui.View):
    def __init__(self, bot: Percy, poll: PollItem, archived: bool = False):
        super().__init__(timeout=None)
        self.poll = poll
        self.bot = bot

        if not archived:
            if abs(len(self.poll.options)) <= 5:
                for option in self.poll.options:
                    self.add_item(PollEnterButton(bot=bot, poll=poll, index=option["index"]))
            else:
                self.add_item(PollEnterSelect(bot=bot, poll=poll))

            self.add_item(ClearVoteButton(bot=bot, poll=poll))
        self.add_item(PollInfoButton(bot=bot, poll=poll))

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        if retry_after := self.poll.cog.cooldown.update_rate_limit(interaction):
            return await interaction.response.send_message(
                f"<:redTick:1079249771975413910> You are being rate limited. Try again in {retry_after:.2f} seconds.",
                ephemeral=True
            )
        return True


class PollItem(PostgresItem):
    id: int
    extra: Dict[str, Any]
    channel_id: int
    message_id: int
    guild_id: int
    users: List
    args: List[Any]
    kwargs: dict[str, Any]
    message: discord.Message
    question: str
    votes: int
    description: str
    options: List[dict[str, Any]]

    __slots__ = ('cog', 'bot', 'id', 'extra', 'channel_id', 'message_id', 'guild_id', 'users', 'args', 'kwargs', 'message',
                 'question', 'votes', 'description', 'options')

    def __init__(self, cog: Polls, **kwargs):
        self.cog: Polls = cog
        self.bot: Percy = cog.bot

        super().__init__(**kwargs)

        self.users: List = self.extra.get('users', [])
        self.args: List[Any] = self.extra.get('args', [])
        self.kwargs: dict[str, Any] = self.extra.get('kwargs', {})

        self.message: discord.Message = MISSING
        self.question = self.kwargs.get("question")
        self.votes = self.kwargs.get("votes", 0)
        self.description = self.kwargs.get("description")
        self.options: List[Dict[str, Any]] = self.kwargs.get("options", [])

    @property
    def jump_url(self) -> Optional[str]:
        if self.message_id and self.channel_id:
            guild = self.guild_id or '@me'
            return f'https://discord.com/channels/{guild}/{self.channel_id}/{self.message_id}'
        return None

    @property
    def published(self) -> Optional[datetime.datetime]:
        if self.kwargs:
            stamp = datetime.datetime.fromtimestamp(self.kwargs.get('published'))
            return stamp.astimezone(datetime.timezone.utc)
        return None

    @property
    def expires(self) -> Optional[datetime.datetime]:
        if self.kwargs:
            stamp = datetime.datetime.fromtimestamp(self.kwargs.get('expires'))
            return stamp.astimezone(datetime.timezone.utc)
        return None

    @property
    def channel(self) -> Optional[discord.TextChannel]:
        if self.channel_id is not None:
            return self.bot.get_channel(self.channel_id)
        return None

    @property
    def choice_text(self) -> str:
        return f'[{self.id}] {self.question}'

    async def fetch_message(self) -> None:
        channel = self.channel
        if channel is not None and self.message_id is not None:
            message = await self.cog.get_message(channel, self.message_id)
            if message:
                self.message = message

    def to_fields(self, extras: bool = True) -> list[dict]:
        fields = []
        for i, option in enumerate(self.options):
            v = option['votes']
            max_length = 10
            votes = self.votes

            p = v / votes if votes else 0
            x = (v * max_length) // votes if votes else 0

            fields.append({
                "name": f"{LINE_EMOJIS[4]} " + option['content'],
                "value": f"{to_emoji(option['index'])}{lineformat(x)} **{v}** {plural(v, pass_content=True):vote} ({round(p * 100)}%)",
                "inline": False
            })

        if extras:
            fields.append({"name": "Voting", "value": f"Total Votes: **{self.votes}**", "inline": True})
            if self.expires:
                fields.append(
                    {"name": "Poll ends", "value": discord.utils.format_dt(self.expires, 'R'), "inline": True})
            if thread := self.kwargs.get("thread"):
                fields.append({"name": "Discussion in Thread:", "value": thread[1], "inline": True})

        return fields

    def to_embed(self) -> discord.Embed:
        embed = discord.Embed(title=self.question, description=self.description, timestamp=self.published)
        embed.set_image(url=self.kwargs.get('image'))
        embed.colour = discord.Colour.from_str(self.kwargs.get('color'))

        for field in self.to_fields():
            embed.add_field(**field)

        embed.set_footer(text=f"#{self.kwargs.get('index')} • [{self.id}]")
        return embed

    async def update_option(
            self, option: Dict[str, Any] | None, edit_type: EditType, value: Optional[str | int] = None
    ) -> None:
        if option:
            if edit_type == EditType.DELETE:
                if len(self.options) == 2:
                    pass
                else:
                    self.options.remove(option)
                    for index, ch in enumerate(sorted(self.options, key=lambda x: x['index'])):
                        ch['index'] = index

                    self.votes -= option['votes']
                    for user in self.users:
                        if user[1] == option["index"]:
                            self.users.remove(user)
            elif edit_type == EditType.CONTENT:
                self.options[option["index"]]["content"] = value
            elif edit_type == EditType.VOTES:
                self.options[option["index"]]["votes"] = value
        else:
            self.options.append(VoteOption(index=len(self.options), content=value, votes=0))

    async def edit(
            self,
            *,
            question: Optional[str] = MISSING,
            description: Optional[str] = MISSING,
            thread: Optional[List[int, str]] = MISSING,
            image_url: Optional[str] = MISSING,
            color: Optional[str] = MISSING,
            options: Optional[List[Tuple[Dict[str, Any] | None, EditType, int | None]]] = MISSING,
            running: Optional[bool] = MISSING,
            votes: Optional[int] = MISSING,
    ) -> Self:
        if question is not MISSING:
            self.kwargs['content'] = question

        if description is not MISSING:
            self.kwargs['description'] = description

        if thread is not MISSING:
            self.kwargs['thread'] = thread

        if image_url is not MISSING:
            self.kwargs['image_url'] = image_url

        if running is not MISSING:
            self.kwargs['running'] = running

        if color is not MISSING:
            self.kwargs['color'] = color

        if options is not MISSING:
            for option in options:
                await self.update_option(*option)

        if votes is not MISSING:
            self.votes = votes

        query = "UPDATE polls SET extra = $1 WHERE id = $2;"
        await self.bot.pool.execute(query, self.extra, self.id)

        return self

    async def delete(self) -> None:
        query = 'DELETE FROM polls WHERE id = $1;'
        await self.bot.pool.execute(query, self.id)

        if self.message_id is not None and self.message is MISSING:
            await self.fetch_message()

        if self.message:
            try:
                await self.message.delete()
            except discord.HTTPException:
                pass

        self.cog.get_guild_polls.invalidate(self, self.guild_id)


class PollPingView(discord.ui.View):
    def __init__(self, role_id: int):
        self.role_id = role_id
        super().__init__(timeout=None)

    @discord.ui.button(style=discord.ButtonStyle.gray, label="Add/Remove Role")
    async def callback(self, interaction: Interaction) -> Any:
        role = discord.Object(id=self.role_id)
        if any(r.id == self.role_id for r in interaction.user.roles):
            await interaction.user.remove_roles(role)
            await interaction.response.send_message(
                f"Successfully **removed** from you these roles:"
                f" <@&{self.role_id}>. Click again to re-add.",
                ephemeral=True
            )
        else:
            await interaction.user.add_roles(role)
            await interaction.response.send_message(
                f"Successfully **gave** you the roles:"
                f" <@&{self.role_id}>. Click again to remove.",
                ephemeral=True
            )


class EditModal(discord.ui.Modal, title="Edit Poll"):
    question = discord.ui.TextInput(label="Question", placeholder="The Main Question for the poll.")
    description = discord.ui.TextInput(label="Description", placeholder="The Description for the poll.",
                                       style=discord.TextStyle.long, required=False)
    thread_question = discord.ui.TextInput(label="Thread Question", placeholder="The Question for the thread.",
                                           required=False)
    image = discord.ui.TextInput(label="Image URL", placeholder="The Image URL for the poll.", required=False)
    color = discord.ui.TextInput(label="Color", placeholder="The Color for the poll.", required=False)

    def __init__(self, poll: PollItem):
        super().__init__(title=f"Edit Poll [{poll.id}]", timeout=180.0)

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


class Polls(commands.Cog):
    """Poll voting system."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        self._message_cache: dict[int, discord.Message] = {}
        self.cooldown: commands.CooldownMapping = commands.CooldownMapping.from_cooldown(
            2, 5, lambda interaction: interaction.user
        )

    async def cog_load(self) -> None:
        self.cleanup_message_cache.start()

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="\N{BAR CHART}")

    @tasks.loop(hours=1.0)
    async def cleanup_message_cache(self):
        self._message_cache.clear()

    async def get_message(self, channel: discord.abc.Messageable, message_id: int) -> Optional[discord.Message]:
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

    @staticmethod
    async def send(interaction: discord.Interaction, *args, **kwargs) -> discord.Message:
        if interaction.response.is_done():
            return await interaction.followup.send(*args, **kwargs)
        else:
            return await interaction.response.send_message(*args, **kwargs)

    async def poll_id_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        polls = await self.get_guild_polls(interaction.guild.id)
        results = fuzzy.finder(current, polls, key=lambda p: p.choice_text, raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(length, start, poll.choice_text), value=poll.id)
            for length, start, poll in results[:20]
        ]

    async def create_poll(
            self, _id: int, channel_id: int, message_id: int, guild_id: int, /, *args: Any, **kwargs: Any
    ) -> PollItem:
        r"""Creates a poll.
        Parameters
        -----------
        _id
            The ID of the poll.
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
        :class:`PollItem`
            The created Poll if creation succeeded, otherwise ``None``.
        """
        poll = PollItem.temporary(
            self, channel_id=channel_id, message_id=message_id, guild_id=guild_id, extra={'args': args, 'kwargs': kwargs, 'users': []}
        )

        query = """
            INSERT INTO polls (id, channel_id, message_id, guild_id, extra)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING id;
        """

        row = await self.bot.pool.fetchrow(
            query, _id, channel_id, message_id, guild_id, {'args': args, 'kwargs': kwargs, 'users': []})
        poll.id = row[0]

        self.get_guild_polls.invalidate(self, guild_id)

        return poll

    async def get_guild_poll(self, guild_id: int, poll_id: int) -> Optional[PollItem]:
        """Gets a poll by ID."""
        query = "SELECT * FROM polls WHERE id = $1 AND guild_id = $2 LIMIT 1;"
        record = await self.bot.pool.fetchrow(query, poll_id, guild_id)
        return PollItem(self, record=record) if record else None

    @cache.cache()
    async def get_guild_polls(self, guild_id: int) -> List[PollItem]:
        """Gets all polls for a guild."""
        query = "SELECT * FROM polls WHERE guild_id = $1;"
        return [PollItem(self, record=record) for record in await self.bot.pool.fetch(query, guild_id)]

    async def end_poll(self, poll: PollItem) -> int | None:
        """Ends a poll if running and archives a thread if it exists."""
        if poll.kwargs.get("running") is False:
            return None

        await poll.edit(running=False)
        await self.bot.reminder.delete_timer('poll', poll_id=poll.id)

        if poll.message_id is not None and poll.message is MISSING:
            await poll.fetch_message()

        if poll.message:
            embed = poll.message.embeds[0]

            field = next((elem for elem in embed.fields if elem.name == "Poll ends"), None)
            embed.set_field_at(
                embed.fields.index(field),
                name="Poll finished",
                value=discord.utils.format_dt(discord.utils.utcnow(), 'R'),
                inline=True
            )

            if thread := poll.kwargs.get('thread'):
                channel = poll.channel
                if channel:
                    thread = channel.get_thread(thread[0])
                    await thread.edit(archived=True, locked=True)

            try:
                await poll.message.edit(embed=embed, view=PollView(self.bot, poll, archived=True))
            except discord.HTTPException:
                pass

        return poll.id

    polls = app_commands.Group(name="polls", description="Commands for managing polls.", guild_only=True)

    @command(
        polls.command,
        name="create",
        description="Creates a new poll with customizable settings.",
    )
    @command_permissions(1, user=["ban_members", "manage_messages"])
    @app_commands.describe(
        question="Main Poll Question to ask.",
        description="Additional notes/description about the question.",
        opt_1="Option 1.", opt_2="Option 2.", opt_3="Option 3.", opt_4="Option 4.",
        opt_5="Option 5.", opt_6="Option 6.", opt_7="Option 7.", opt_8="Option 8.",
        thread_question="Question to ask in the accompanying Thread.",
        image="Image to accompany Poll Question.",
        image_url="Image as URL (alternative to upload)",
        when="When to end the poll.",
        color="Color of the embed.",
        ping="Whether to ping the role.",
        user_reason="Whether to ask for a reason on vote.",
    )
    @app_commands.autocomplete(color=colour_autocomplete)
    async def polls_create(
            self, interaction: discord.Interaction,
            question: str,
            when: app_commands.Transform[datetime.datetime, timetools.TimeTransformer],
            description: str = None,
            color: app_commands.Transform[discord.Colour, converters.ColorTransformer] = helpers.Colour.darker_red(),
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
        if not (channel := config.poll_channel):
            return await self.send(interaction, f"{tick(False)} You must set a poll channel first.")

        image_url = image_url or (image.proxy_url if image else None)
        options = list(filter(lambda x: x is not None, [opt_1, opt_2, opt_3, opt_4, opt_5, opt_6, opt_7, opt_8]))

        if len(options) < 2:
            return await self.send(interaction, f"{tick(False)} You must provide at least 2 options.")

        to_options = [VoteOption(index=index, content=content, votes=0) for index, content in enumerate(options)]

        message = await channel.send(embed=discord.Embed(description="*Preparing Poll...*"))

        new_index = len(await self.get_guild_polls(guild_id=interaction.guild.id)) + 1
        unique_id = uuid([rec[0] for rec in await self.bot.pool.fetch("SELECT id FROM polls")])

        if thread_question:
            thread = await message.create_thread(name=question, auto_archive_duration=4320)
            thread_message = await thread.send(thread_question)
            await thread_message.pin(reason="Poll Discussion")

        if user_reason and not config.poll_reason_channel:
            return await self.send(
                interaction, f"{tick(False)} You must set a poll reason channel if you want to set user reasons."
            )

        if ping and not config.poll_ping_role_id:
            return await self.send(interaction, f"{tick(False)} You must set a ping role to set Pings.")

        poll = await self.create_poll(
            unique_id,
            channel.id,
            message.id,
            interaction.guild.id,
            interaction.user.id,
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
            published=discord.utils.utcnow().timestamp(),  # TODO: Don't use timestamps for this
            expires=when.timestamp(),  # TODO: Don't use timestamps for this
        )

        reminder = self.bot.reminder
        uconfig = await self.bot.user_settings.get_user_config(interaction.user.id)
        zone = uconfig.timezone if uconfig else None
        if reminder is None:
            return await self.send(
                interaction,
                '<:redTick:1079249771975413910> The Timer function is currently unavailable, please wait or contact '
                'the Bot Developer if this problem persists.'
            )
        else:
            await reminder.create_timer(
                when,
                'poll',
                poll_id=poll.id,
                created=discord.utils.utcnow(),
                timezone=zone or 'UTC',
            )

        await self.send(
            interaction,
            f"{tick(True)} Poll #{new_index} [`{poll.id}`] successfully created. {message.jump_url}"
        )

        await message.edit(embed=poll.to_embed(), view=PollView(bot=self.bot, poll=poll))

        if ping:
            await channel.send(
                content=f"<@&{config.poll_ping_role_id}>",
                embed=discord.Embed(description="You wanna tell us your opinion?\n"
                                                "To be notified when new polls are posted, click below!",
                                    color=discord.Color.green()),
                view=PollPingView(config.poll_ping_role_id),
                allowed_mentions=discord.AllowedMentions(roles=True)
            )

    @command(
        polls.command,
        name="end",
        description="Ends the voting for a running question.",
    )
    @command_permissions(1, user=["ban_members", "manage_messages"])
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)  # type: ignore
    @app_commands.describe(poll_id="5-digit ID of the poll to end.")
    async def polls_end(self, interaction: discord.Interaction, poll_id: int):
        """Ends a poll."""
        await interaction.response.defer()

        poll = await self.get_guild_poll(interaction.guild.id, poll_id)
        if poll is None:
            return await self.send(interaction, f"{tick(False)} Poll not found.", ephemeral=True)

        check = await self.end_poll(poll)
        if check is None:
            return await self.send(interaction, f"{tick(False)} Poll is already ended.", ephemeral=True)

        await self.send(interaction, f"{tick(True)} Poll [`{check}`] has been ended.")

    @command(
        polls.command,
        name="delete",
        description="Deletes a poll question.",
    )
    @command_permissions(1, user=["ban_members", "manage_messages"])
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)  # type: ignore
    @app_commands.describe(poll_id="5-digit ID of the poll to delete.")
    async def polls_delete(self, interaction: discord.Interaction, poll_id: int):
        """Deletes a poll question."""
        poll = await self.get_guild_poll(interaction.guild.id, poll_id)
        if poll is None:
            return await interaction.response.send_message(f"{tick(False)} Poll not found.", ephemeral=True)

        await poll.delete()

        await interaction.response.send_message(f"{tick(True)} Poll [`{poll_id}`] has been deleted.")

    @command(
        polls.command,
        name="edit",
        description="Edits a poll question. Type '-clear' to clear the current value.",
    )
    @command_permissions(1, user=["ban_members", "manage_messages"])
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)  # type: ignore
    @app_commands.describe(
        poll_id="5-digit ID of the poll to search for.",
        question="The new question to ask.",
        description="The new description to use.",
        color="The new color to use.",
        thread_question="The new thread question to use.",
        image="The new image to use.",
        image_url="The new image URL to use.",
        opt_1="Option 1.", opt_2="Option 2.", opt_3="Option 3.", opt_4="Option 4.",
        opt_5="Option 5.", opt_6="Option 6.", opt_7="Option 7.", opt_8="Option 8.",
    )
    async def polls_edit(
            self, interaction: discord.Interaction,
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
        """Edits a poll question. Type '-clear' to clear the current value."""
        poll = await self.get_guild_poll(interaction.guild.id, poll_id)
        if not poll:
            return await interaction.response.send_message(f"{tick(False)} Poll not found.", ephemeral=True)

        if not poll.kwargs.get("running"):
            return await interaction.response.send_message(f"{tick(False)} Poll is already ended.", ephemeral=True)

        if poll.message_id is not None and poll.message is MISSING:
            await poll.fetch_message()

        open_thread = poll.kwargs.get("thread")
        kwargs = {}

        if all(value is None for value in [
            question, description, thread_question, image, image_url,
            opt_1, opt_2, opt_3, opt_4, opt_5, opt_6, opt_7, opt_8
        ]):
            modal = EditModal(poll)
            await interaction.response.send_modal(modal)
            await modal.wait()
            interaction = modal.interaction

            if modal.question.value != poll.question:
                kwargs["question"] = modal.question.value

            if modal.description.value != poll.description:
                kwargs["description"] = modal.description.value

            if modal.color.value != poll.kwargs.get("color"):
                kwargs["color"] = modal.color.value

            if modal.thread_question.value != open_thread[1] if open_thread else None:
                if open_thread:
                    thread = poll.channel.get_thread(open_thread[0])

                    if modal.thread_question.value == "-clear":
                        if thread:
                            await thread.edit(archived=True, locked=True)

                        kwargs["thread"] = []
                    else:
                        if thread:
                            msg = [msg async for msg in thread.history(limit=2, oldest_first=True)][1]
                            if msg.author.id == self.bot.user.id:
                                await msg.edit(content=modal.thread_question.value)

                        kwargs["thread"] = [thread.id, modal.thread_question.value]
                else:
                    thread = await poll.message.create_thread(name=poll.question, auto_archive_duration=4320)
                    thread_message = await thread.send(modal.thread_question.value)
                    await thread_message.pin(reason="Poll Discussion")

                    kwargs["thread"] = [thread.id, modal.thread_question.value]

            if modal.image.value != poll.kwargs.get("image"):
                kwargs["image_url"] = modal.image.value

        else:
            await interaction.response.defer()

            if question:
                if question != "-clear":
                    kwargs["question"] = question

            if description:
                if description == "-clear":
                    kwargs["description"] = None
                else:
                    kwargs["description"] = description

            if image or image_url:
                image_url = image_url or (image.proxy_url if image else None)
                kwargs["image_url"] = image_url

            if color:
                kwargs["color"] = str(color)

            if thread_question:
                if open_thread:
                    thread = poll.channel.get_thread(open_thread[0])

                    if thread_question == "-clear":
                        if thread:
                            await thread.edit(archived=True, locked=True)

                        kwargs["thread"] = []
                    else:
                        if thread:
                            msg = [msg async for msg in thread.history(limit=2, oldest_first=True)][1]
                            if msg.author.id == self.bot.user.id:
                                await msg.edit(content=thread_question)

                        kwargs["thread"] = [thread.id, thread_question]
                else:
                    thread = await poll.message.create_thread(name=poll.question, auto_archive_duration=4320)
                    thread_message = await thread.send(thread_question)
                    await thread_message.pin(reason="Poll Discussion")

                    kwargs["thread"] = [thread.id, thread_question]

        contents = [
            (value, index) for index, value in enumerate([opt_1, opt_2, opt_3, opt_4, opt_5, opt_6, opt_7, opt_8])
            if value
        ]

        options = []
        for option in contents:
            opt_index = option[1]
            try:
                opt = poll.options[opt_index]
            except IndexError:
                opt = None

            if option[0] == "-clear" and opt:
                options.append((opt, EditType.DELETE))
            elif opt and option[0] != opt:
                options.append((opt, EditType.CONTENT, option[0]))

        if options:
            kwargs["options"] = options

        poll = await poll.edit(**kwargs)
        await poll.message.edit(embed=poll.to_embed(), view=PollView(self.bot, poll))

        await self.send(interaction, f"{tick(True)} Poll [`{poll.id}`] edited successfully.", ephemeral=True)

    @command(
        polls.command,
        name="search",
        description="Searches poll questions. Search by ID, keyword or flags.",
    )
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)  # type: ignore
    @app_commands.describe(
        poll_id="The ID of the poll to search for.",
        keyword="The keyword to search for.",
        sort="The sorting method to use.",
        active="Whether to search for active polls.",
        showextrainfo="Whether to show extra information about the poll. (Only for Admins)",
    )
    async def polls_search(
            self, interaction: discord.Interaction,
            poll_id: int = None,
            keyword: str = None,
            sort: Literal["Poll ID", "Newest", "Oldest", "Most Votes", "Least Votes"] = "Newest",
            active: bool = None,
            showextrainfo: bool = False,
    ):
        await interaction.response.defer()

        if poll_id:
            poll = await self.get_guild_poll(interaction.guild.id, poll_id)
            if not poll:
                return await self.send(interaction, f"{tick(False)} Poll not found.", ephemeral=True)

            if showextrainfo and interaction.channel.permissions_for(interaction.user).manage_messages:
                embed = discord.Embed(title=f"#{poll.kwargs.get('index')}: {poll.question}",
                                      description=poll.description)

                embed.add_field(name="Choices", value='\n'.join(f"{v['value']}" for v in poll.to_fields(extras=False)),
                                inline=False)
                embed.add_field(name="Voting", value=f"Total Votes: **{poll.votes}**")

                running = poll.kwargs.get('running')
                embed.add_field(name="Active?", value=running)

                embed.add_field(name="Poll published", value=discord.utils.format_dt(poll.published, 'f'))
                embed.add_field(name="Poll ends" if running else "Poll finished",
                                value=discord.utils.format_dt(poll.expires, 'R'))

                embed.add_field(name="Poll Message", value=poll.jump_url or f"Can't locate message `{poll.message_id}`")
                embed.add_field(name="User Reason", value=poll.kwargs.get('user_reason'))

                if thread := poll.kwargs.get('thread'):
                    embed.add_field(name="Thread Question", value=thread[1])

                embed.set_image(url=poll.kwargs.get('image'))
                embed.colour = discord.Colour.from_str(poll.kwargs.get('color'))

                embed.set_footer(text=f"[{poll.id}] • {poll.guild_id}")
            else:
                embed = poll.to_embed()

            await self.send(interaction, embed=embed)
        else:
            kwargs: Dict[str, str] = {}

            if active is not None:
                kwargs["running"] = "true" if active else "false"

            SORT = {
                "Poll ID": "id",
                "Newest": "extra #>> ARRAY['kwargs', 'published'] DESC",
                "Oldest": "extra #>> ARRAY['kwargs', 'published'] ASC",
                "Most Votes": "extra #>> ARRAY['kwargs', 'votes'] DESC",
                "Least Votes": "extra #>> ARRAY['kwargs', 'votes'] ASC"
            }.get(sort)

            filtered_clause = [f"extra #>> ARRAY['kwargs', '{key}'] = ${i}" for (i, key) in
                               enumerate(kwargs.keys(), start=2)]
            query = (
                f"SELECT * FROM polls WHERE guild_id = $1 {'AND' if filtered_clause else ''} "
                f"{' AND '.join(filtered_clause)} ORDER BY {SORT};"
            )
            records = await self.bot.pool.fetch(query, interaction.guild.id, *list(kwargs.values()))

            if not records:
                return await self.send(interaction, f"{tick(False)} No polls found matching this filter.",
                                       ephemeral=True)

            if keyword:
                records = [
                    r for r in records if fuzzy.partial_ratio(
                        keyword.lower(), r["extra"]["kwargs"].get('question').lower()
                    ) > 70
                ]

            results = [f"`{poll.id}` (`#{poll.kwargs.get('index')}`): "
                       f"{poll.question} (<t:{int(poll.kwargs.get('published'))}:d>)"
                       for poll in [PollItem(self, record=r) for r in records]]

            embed = discord.Embed(title="Poll Search",
                                  description=f"Sorted by: **{sort.lower()}**",
                                  colour=helpers.Colour.darker_red(),
                                  timestamp=discord.utils.utcnow())
            embed.set_footer(text=f"{plural(len(records)):entry|entries}")
            await LinePaginator.start(interaction, entries=results, per_page=12, embed=embed)

    @command(
        polls.command,
        name="history",
        description="Shows the vote history of a user for polls.",
    )
    @app_commands.describe(member="The Member to show the history for.")
    async def polls_history(self, interaction: discord.Interaction, member: Optional[discord.Member] = None):
        """Shows the vote history of a user for polls."""
        polls = await self.get_guild_polls(guild_id=interaction.guild.id)

        if not polls:
            return await interaction.response.send_message(f"{tick(False)} You haven't voted in this guild yet.",
                                                           ephemeral=True)

        member = member or interaction.user
        user_polls = list(filter(lambda poll: any(x[0] == member.id for x in poll.users), polls))

        class FieldPaginator(BasePaginator[PollItem]):

            async def format_page(self, entries: List[PollItem], /) -> discord.Embed:
                embed = discord.Embed(title=f"Poll History for {member}",
                                      colour=helpers.Colour.darker_red(),
                                      timestamp=discord.utils.utcnow())
                embed.set_footer(text=f"{plural(len(polls)):entry|entries}")

                for poll in entries:
                    vote = next(i[1] for i in poll.users if i[0] == member.id)
                    embed.add_field(name=f"{poll.id} (#{poll.kwargs.get('index')}): {poll.question}",
                                    value=f"You've voted: {to_emoji(poll.options[vote]['index'])} - "
                                         f"*{poll.options[vote]['content']}*",
                                    inline=False)

                return embed

        await FieldPaginator.start(interaction, entries=user_polls, per_page=12)

    @command(
        polls.command,
        name="debug",
        description="Refactor all existing Polls in this guild and reattach the views.",
    )
    @command_permissions(1, user=["ban_members", "manage_messages"])
    @app_commands.autocomplete(poll_id=poll_id_autocomplete)  # type: ignore
    @app_commands.describe(poll_id="The ID of the Poll to debug.")
    @app_commands.checks.cooldown(1, 15.0, key=lambda i: i.guild_id)
    async def polls_debug(self, interaction: discord.Interaction, poll_id: int):
        """Refactor all existing Polls in this guild and reattach the views."""
        poll = await self.get_guild_poll(interaction.guild.id, poll_id)

        if not poll:
            return await interaction.response.send_message(f"{tick(False)} Poll not found.", ephemeral=True)

        if poll.guild_id != interaction.guild.id:
            return await interaction.response.send_message(f"{tick(False)} Poll not found.", ephemeral=True)

        embed = poll.to_embed()
        view = PollView(self.bot, poll=poll, archived=not poll.kwargs.get('running', True))
        await poll.fetch_message()
        if poll.message:
            await poll.message.edit(embed=embed, view=view)
        await interaction.response.send_message(f"{tick(True)} Poll [`{poll.id}`] debugged.", ephemeral=True)

    @command(
        polls.command,
        name="config",
        description="Shows the current configuration for polls.",
    )
    @command_permissions(1, user=["ban_members", "manage_messages"])
    async def polls_config(
            self, interaction: discord.Interaction,
            poll_channel: discord.TextChannel = None,
            poll_reason_channel: discord.TextChannel = None,
            poll_role: discord.Role = None,
            reset: bool = False
    ):
        config = await self.bot.moderation.get_guild_config(guild_id=interaction.guild.id)

        if all(i is None for i in [poll_channel, poll_reason_channel, poll_role]):
            embed = discord.Embed(title="Poll Configuration",
                                  colour=helpers.Colour.darker_red(),
                                  timestamp=discord.utils.utcnow())
            embed.add_field(name="Poll Channel",
                            value=f"<#{config.poll_channel_id}>" if config.poll_channel_id else 'N/A')
            embed.add_field(name="Poll Reason Channel",
                            value=f"<#{config.poll_reason_channel_id}>" if config.poll_reason_channel_id else 'N/A')
            embed.add_field(name="Poll Role",
                            value=f"<@&{config.poll_ping_role_id}>" if config.poll_ping_role_id else 'N/A')
            embed.set_footer(text=f'Use "/polls config" to change the configuration.')
            return await interaction.response.send_message(embed=embed)
        else:
            if reset:
                kwargs = {
                    "poll_channel": None,
                    "poll_reason_channel": None,
                    "poll_ping_role_id": None
                }
                content = f"{tick(True)} Poll configuration reset."
            else:
                kwargs = {}
                if poll_channel:
                    kwargs["poll_channel"] = poll_channel.id
                if poll_reason_channel:
                    kwargs["poll_reason_channel"] = poll_reason_channel.id
                if poll_role:
                    kwargs["poll_ping_role_id"] = poll_role.id

                content = f"{tick(True)} Poll configuration updated."

            updates = ", ".join(f"{k} = ${i}" for i, k in enumerate(kwargs.keys(), start=2))
            query = f"UPDATE guild_config SET {updates} WHERE id = $1;"
            await self.bot.pool.execute(query, interaction.guild.id, *list(kwargs.values()))
            self.bot.moderation.get_guild_config.invalidate(self.bot.moderation, interaction.guild.id)
            return await interaction.response.send_message(content)

    @commands.Cog.listener()
    async def on_poll_timer_complete(self, timer: Timer):
        await self.bot.wait_until_ready()
        poll_id = timer.kwargs.get("poll_id")

        query = "SELECT * FROM polls WHERE id = $1 LIMIT 1;"
        record = await self.bot.pool.fetchrow(query, poll_id)
        poll = PollItem(self, record=record) if record else None

        if poll:
            await self.end_poll(poll)


async def setup(bot: Percy):
    await bot.add_cog(Polls(bot))
