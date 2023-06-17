from __future__ import annotations

import io
import random
import string
import sys
import datetime
from io import StringIO
from types import TracebackType
from typing import Union, Optional, Protocol, Any, TYPE_CHECKING, Iterable, Sequence, Callable, Generic, TypeVar, AnyStr

import discord
from aiohttp import ClientSession
from asyncpg import Connection, Pool
from discord import Message, Embed, File, GuildSticker, StickerItem, AllowedMentions, MessageReference, PartialMessage
from discord.context_managers import Typing
from discord.ext import commands
from discord.utils import MISSING
from discord.ext.commands.context import DeferTyping
from discord.ui import View

if TYPE_CHECKING:
    from bot import Percy
    from cogs.base import Base


T = TypeVar('T')


class EditTyping(Typing):
    """Custom Typing subclass to support cancelling typing when the message content changed"""

    def __init__(self, context: commands.Context) -> None:
        self.context = context
        super().__init__(context)

    async def __aenter__(self) -> None:
        if self.context.message.id not in self.context.bot.command_cache.keys():
            return await super().__aenter__()

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self.context.message.id not in self.context.bot.command_cache.keys():
            return await super().__aexit__(exc_type, exc, traceback)


class ConnectionContextManager(Protocol):
    async def __aenter__(self) -> Connection:
        ...

    async def __aexit__(
            self,
            exc_type: Optional[type[BaseException]],
            exc_value: Optional[BaseException],
            traceback: Optional[TracebackType],
    ) -> None:
        ...


class DatabaseProtocol(Protocol):
    async def execute(self, query: str, *args: Any, timeout: Optional[float] = None) -> str:
        ...

    async def fetch(self, query: str, *args: Any, timeout: Optional[float] = None) -> list[Any]:
        ...

    async def fetchrow(self, query: str, *args: Any, timeout: Optional[float] = None) -> Optional[Any]:
        ...

    def acquire(self, *, timeout: Optional[float] = None) -> ConnectionContextManager:
        ...

    def release(self, connection: Connection) -> None:
        ...


class ConfirmationView(discord.ui.View):
    def __init__(self, *, timeout: float, author_id: int, delete_after: bool) -> None:
        super().__init__(timeout=timeout)
        self.value: Optional[bool] = None
        self.delete_after: bool = delete_after
        self.author_id: int = author_id
        self.message: Optional[discord.Message] = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user and interaction.user.id == self.author_id:
            return True
        else:
            await interaction.response.send_message('This confirmation dialog is not for you.', ephemeral=True)
            return False

    async def on_timeout(self) -> None:
        if self.delete_after and self.message:
            await self.message.delete()

    @discord.ui.button(label='Confirm', style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = True
        await interaction.response.defer()
        if self.delete_after:
            await interaction.delete_original_response()

        self.stop()

    @discord.ui.button(label='Cancel', style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.value = False
        await interaction.response.defer()
        if self.delete_after:
            await interaction.delete_original_response()

        self.stop()


class DisambiguatorView(discord.ui.View, Generic[T]):
    message: discord.Message
    selected: T

    def __init__(self, ctx: Context, data: list[T], entry: Callable[[T], Any]):
        super().__init__()
        self.ctx: Context = ctx
        self.data: list[T] = data

        options = []
        for i, x in enumerate(data):
            opt = entry(x)
            if not isinstance(opt, discord.SelectOption):
                opt = discord.SelectOption(label=str(opt))
            opt.value = str(i)
            options.append(opt)

        select = discord.ui.Select(options=options)

        select.callback = self.on_select_submit
        self.select = select
        self.add_item(select)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.ctx.author.id:
            await interaction.response.send_message('This select menu is not meant for you, sorry.', ephemeral=True)
            return False
        return True

    async def on_select_submit(self, interaction: discord.Interaction):
        index = int(self.select.values[0])
        self.selected = self.data[index]
        await interaction.response.defer()
        if not self.message.flags.ephemeral:
            await self.message.delete()

        self.stop()


class Context(commands.Context):
    channel: Union[discord.VoiceChannel, discord.TextChannel, discord.Thread, discord.DMChannel]
    prefix: str
    command: commands.Command[Any, ..., Any]
    bot: Percy

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.pool: Pool = self.bot.pool

    async def entry_to_code(self, entries: Iterable[tuple[str, str]]) -> None:
        width = max(len(a) for a, b in entries)
        output = ['```']
        for name, entry in entries:
            output.append(f'{name:<{width}}: {entry}')
        output.append('```')
        await self.send('\n'.join(output))

    async def indented_entry_to_code(self, entries: Iterable[tuple[str, str]]) -> None:
        width = max(len(a) for a, b in entries)
        output = ['```']
        for name, entry in entries:
            output.append(f'\u200b{name:>{width}}: {entry}')
        output.append('```')
        await self.send('\n'.join(output))

    def __repr__(self) -> str:
        return '<Context>'

    @discord.utils.cached_property
    def replied_reference(self) -> Optional[discord.MessageReference]:
        ref = self.message.reference
        if ref and isinstance(ref.resolved, discord.Message):
            return ref.resolved.to_reference()
        return None

    @discord.utils.cached_property
    def replied_message(self) -> Optional[discord.Message]:
        ref = self.message.reference
        if ref and isinstance(ref.resolved, discord.Message):
            return ref.resolved
        return None

    @property
    def user(self) -> discord.User:
        """Returns the author of the message as an :class:`discord.User`."""
        return self.author._user  # noqa

    @property
    def client(self) -> 'commands.Bot':
        """Returns the client."""
        return self.bot

    @classmethod
    def tick(cls, opt: Optional[bool], label: Optional[str] = None) -> str:
        """Returns a tick or cross emoji based on the value of `opt`."""
        lookup = {
            True: '<:greenTick:1079249732364406854>',
            False: '<:redTick:1079249771975413910>',
            None: '<:greyTick:1079250082819477634>',
        }
        emoji = lookup.get(opt, '<:redTick:1079249771975413910>')
        if label is not None:
            return f'{emoji} {label}'
        return emoji

    async def disambiguate(self, matches: list[T], entry: Callable[[T], Any], *, ephemeral: bool = False) -> T:
        if len(matches) == 0:
            raise ValueError('No results found.')

        if len(matches) == 1:
            return matches[0]

        if len(matches) > 25:
            raise ValueError('Too many results... sorry.')

        view = DisambiguatorView(self, matches, entry)
        view.message = await self.send(
            '<:discord_info:1113421814132117545> There are too many matches... Please specify your choice by selecting a result.',
            view=view, ephemeral=ephemeral
        )
        await view.wait()
        return view.selected

    async def prompt(
            self,
            message: str,
            *,
            timeout: float = 60.0,
            delete_after: bool = True,
            author_id: Optional[int] = None,
            ephemeral: bool = False,
    ) -> Optional[bool]:
        """An interactive reaction confirmation dialog.
        Parameters
        -----------
        message: str
            The message to show along with the prompt.
        timeout: float
            How long to wait before returning.
        delete_after: bool
            Whether to delete the confirmation message after we're done.
        author_id: Optional[int]
            The member who should respond to the prompt. Defaults to the author of the
            Context's message.
        ephemeral: bool
            Whether the prompt should be ephemeral.
        Returns
        --------
        Optional[bool]
            ``True`` if explicit confirm,
            ``False`` if explicit deny,
            ``None`` if deny due to timeout
        """

        author_id = author_id or self.author.id
        view = ConfirmationView(
            timeout=timeout,
            delete_after=delete_after,
            author_id=author_id,
        )
        view.message = await self.send(embed=discord.Embed(title="Are you sure?",
                                                           description=message,
                                                           colour=discord.Colour(0xF8DB5E)),
                                       view=view, ephemeral=ephemeral)
        await view.wait()
        return view.value

    @property
    def session(self) -> ClientSession:
        return self.bot.session

    @property
    def db(self) -> DatabaseProtocol:
        return self.pool  # type: ignore

    def typing(self, *, ephemeral: bool = False) -> Union[Typing, DeferTyping]:
        """A custom typing method that allows us to defer typing if we want to."""
        if self.interaction is None:
            return EditTyping(self)
        return DeferTyping(self, ephemeral=ephemeral)

    async def post(self, filename: str, *, content: str) -> str | None:
        """Create a GitHub Gist from content."""
        dpy: Optional[Base] = self.bot.get_cog("Exclusives")
        posted_gist_url = await dpy.create_gist(description=str(self.author) + " - " + filename,
                                                content=content, public=True)

        return f"<{posted_gist_url}>"

    async def send(
            self,
            content: Optional[str] = None,
            *,
            tts: bool = False,
            embed: Optional[Embed] = None,
            embeds: Optional[Sequence[Embed]] = None,
            file: Optional[File] = None,
            files: Optional[Sequence[File]] = None,
            stickers: Optional[Sequence[Union[GuildSticker, StickerItem]]] = None,
            delete_after: Optional[float] = None,
            nonce: Optional[Union[str, int]] = None,
            allowed_mentions: Optional[AllowedMentions] = None,
            reference: Optional[Union[Message, MessageReference, PartialMessage]] = None,
            mention_author: Optional[bool] = None,
            view: Optional[View] = None,
            suppress_embeds: bool = False,
            ephemeral: bool = False,
            post: bool = False,
            no_reply: bool = False,
            silent: bool = False,
    ) -> discord.Message:
        """A custom send method that allows us to edit the previous message.

        Parameters
        -----------
        content: Optional[str]
            The content of the message to send.
        tts: bool
            Indicates if the message should be sent using text-to-speech.
        embed: Optional[Embed]
            The rich embed for the content.
        embeds: Optional[Sequence[Embed]]
            A list of embeds to send with the message. Must be a maximum of 10.
        file: Optional[File]
            The file to upload.
        files: Optional[Sequence[File]]
            A list of files to upload. Must be a maximum of 10.
        stickers: Optional[Sequence[Union[GuildSticker, StickerItem]]]
            A list of stickers to send with the message. Must be a maximum of 3.
        delete_after: Optional[float]
            If provided, the number of seconds to wait in the background
            before deleting the message we just sent.
        nonce: Optional[Union[str, int]]
            The nonce to use for sending this message. If the message was successfully sent,
            then the message will have a nonce with this value.
        allowed_mentions: Optional[AllowedMentions]
            Controls the mentions being processed in this message. If this is
            passed, then the object is merged with :attr:`.allowed_mentions`.
        reference: Optional[Union[Message, MessageReference, PartialMessage]]
            A reference to the :class:`Message` to which you are replying, this can be created
            with :meth:`Message.to_reference` or passed directly as a :class:`Message`, :class:`PartialMessage`,
            or :class:`MessageReference`. This allows for providing replies to messages.
        mention_author: Optional[bool]
            If set, overrides the :attr:`.allowed_mentions` attribute to mention the
            author of the message being replied to. If this is set to ``True`` then the
            message reference *must* be set.
        view: Optional[View]
            The view to send with the message.
        suppress_embeds: bool
            Indicates if embeds should be suppressed for this message. If set to ``True``, all
            :class:`Embed` in :attr:`embeds` will be set to :class:`Embed.Empty` and all
            :class:`MessageEmbed` in :attr:`embeds` will be set to ``None``.
        ephemeral: bool
            Indicates if the message should only be visible to the user who started the interaction.
        post: bool
            Indicates if the message should be posted to GitHub Gist.
        no_reply: bool
            Indicates if the message should not be replied to.
        silent: bool
            Indicates if the message should be sent silently.

        Raises
        --------
        ~discord.HTTPException
            Sending the message failed.
        ~discord.Forbidden
            You do not have the proper permissions to send the message.
        ValueError
            The ``files`` list is not of the appropriate size.
        TypeError
            You specified both ``file`` and ``files``,
            or you specified both ``embed`` and ``embeds``,
            or the ``reference`` object is not a :class:`~discord.Message`,
            :class:`~discord.MessageReference` or :class:`~discord.PartialMessage`.

        Returns
        ---------
        :class:`~discord.Message`
            The message that was sent.
        """
        if self.interaction is None or self.interaction.is_expired():
            # noinspection PyArgumentList
            return await super().send(
                content=content,
                tts=tts,
                embed=embed,
                embeds=embeds,
                file=file,
                files=files,
                stickers=stickers,
                delete_after=delete_after,
                nonce=nonce,
                allowed_mentions=allowed_mentions,
                reference=reference,
                mention_author=mention_author,
                view=view,
                suppress_embeds=suppress_embeds,
                ephemeral=ephemeral,
            )

        if content:
            content = str(content)
            for path in sys.path:
                content = content.replace(path, "[PATH]")
            if len(content) >= 2000:
                if post:
                    content = f"Output too long, posted here: {await self.post(filename='output.py', content=content)}"

        if embed:
            if not embed.footer:
                embed.set_footer(
                    text=f"Requested by: {self.author}",
                    icon_url=self.author.display_avatar.url,
                )
                embed.timestamp = datetime.datetime.utcnow()

        if ephemeral:
            no_reply = True

        kwargs: dict[str, Any] = {
            "content": content,
            "tts": tts,
            "embed": embed,
            "embeds": embeds,
            "file": file,
            "files": files,
            "stickers": stickers,
            "delete_after": delete_after,
            "nonce": nonce,
            "allowed_mentions": allowed_mentions,
            "reference": reference,
            "mention_author": mention_author,
            "view": view,
            "suppress_embeds": suppress_embeds,
            "silent": silent,
        }

        if self.interaction is None or self.interaction.is_expired():
            kwargs["reference"] = self.message.to_reference(fail_if_not_exists=False) or reference
            if no_reply:
                kwargs["reference"] = None
            return await self.send(**kwargs)

        kwargs = {
            "content": content,
            "tts": tts,
            "embed": MISSING if embed is None else embed,
            "embeds": MISSING if embeds is None else embeds,
            "file": MISSING if file is None else file,
            "files": MISSING if files is None else files,
            "allowed_mentions": MISSING if allowed_mentions is None else allowed_mentions,
            "view": MISSING if view is None else view,
            "suppress_embeds": suppress_embeds,
            "ephemeral": ephemeral,
            "silent": silent,
        }

        if self.interaction.response.is_done():
            msg = await self.interaction.followup.send(**kwargs, wait=True)
        elif not self.interaction.response.is_done():
            await self.interaction.response.send_message(**kwargs)
            msg = await self.interaction.original_response()
        else:
            msg = await self.send(**kwargs)

        if delete_after is not None:
            await msg.delete(delay=delete_after)

        return msg

    @staticmethod
    async def string_to_file(
            content: AnyStr = None, filename: str = "message.txt"
    ) -> discord.File:
        """Converts a string to a file."""
        if filename == "random":
            filename = "".join(random.choices(string.ascii_letters, k=24))

        if isinstance(content, str):
            buf = StringIO()
        elif isinstance(content, bytes):
            buf = io.BytesIO()
        else:
            raise TypeError("Content must be str or bytes")
        buf.write(content)
        buf.seek(0)
        return discord.File(buf, filename=filename)

    async def send_as_file(
            self,
            content: AnyStr = None,
            message_content: str = None,
            filename: str = "message.txt",
            *args,
            **kwargs,
    ) -> discord.Message:
        file = await self.string_to_file(content, filename=filename)

        return await super().send(
            content=message_content,
            file=file,
            *args,
            **kwargs,
        )

    async def send_help(self, item: Any | None) -> Any:
        if not item:
            return await super().send_help()
        return await super().send_help(item)

    async def show_help(self, command: Any = None) -> None:
        cmd = self.bot.get_command('help')
        command = command or self.command.qualified_name
        await self.invoke(cmd, command=command)  # type: ignore

    async def safe_send(self, content: str, *, escape_mentions: bool = True, **kwargs) -> Message:
        if escape_mentions:
            content = discord.utils.escape_mentions(content)

        if len(content) > 2000:
            fp = io.BytesIO(content.encode())
            kwargs.pop('file', None)
            return await self.send(file=discord.File(fp, filename='message_too_long.txt'), **kwargs)
        else:
            return await self.send(content)


class GuildContext(Context):
    author: discord.Member
    guild: discord.Guild
    channel: Union[discord.VoiceChannel, discord.TextChannel, discord.Thread]
    me: discord.Member
    prefix: str


class EvalContext(Context):
    job_message: discord.Message


async def setup(bot):
    bot.context = Context


async def teardown(bot):
    bot.context = commands.Context
