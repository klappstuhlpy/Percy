from __future__ import annotations

import io
import re
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

import discord
from discord.ext import commands
from discord.utils import cached_property

from app.core.flags import FlagNamespace, Flags
from app.core.views import ConfirmationView, DisambiguatorView
from app.utils.progress import ProgressTracker
from app.utils.timetools import ensure_utc, format_user_time, to_user_tz
from config import Emojis

if TYPE_CHECKING:
    import datetime as _dt
    from collections.abc import Callable
    from datetime import datetime

    import aiohttp

    from app.core import Bot
    from app.core.command import Command, GroupCommand
    from app.core.models import Cog
    from app.database import Database
    from app.database.base import UserConfig
    from app.utils import AsyncCallable

T = TypeVar("T")

__all__ = (
    "Context",
    "HybridContext",
    "HybridContextProtocol",
)


@discord.utils.copy_doc(commands.Context)
class Context[CogT: "Cog"](commands.Context):
    if TYPE_CHECKING:
        bot: Bot
        cog: CogT
        command: Command | GroupCommand | None
        invoked_subcommand: Command | GroupCommand | None

    def __init__(self, **attrs: Any) -> None:
        self._message: discord.Message | None = None
        super().__init__(**attrs)

    @property
    def session(self) -> aiohttp.ClientSession:
        """:class:`aiohttp.ClientSession`: The session for the bot"""
        return self.bot.session

    @property
    def user(self) -> discord.User | discord.Member:
        """Alias for :attr:`author`."""
        return self.author

    @property
    def client(self) -> Bot:
        """Alias for :attr:`bot`."""
        return self.bot

    @property
    def guild_id(self) -> int | None:
        """Alias for :attr:`guild.id`."""
        return self.guild.id if self.guild else None

    @property
    def db(self) -> Database:
        """The database instance for the current context."""
        return self.bot.db

    @property
    def now(self) -> datetime:
        """Returns when the message of this context was created at."""
        return self.message.created_at

    @cached_property
    def flags(self) -> Flags | None:
        """The flag arguments passed.

        Only available if the flags were a keyword argument.
        """
        return discord.utils.find(lambda v: isinstance(v, FlagNamespace), self.kwargs.values())

    @staticmethod
    def utcnow() -> datetime:
        """A shortcut for :func:`discord.utils.utcnow`."""
        return discord.utils.utcnow()

    async def get_user_config(self) -> UserConfig | None:
        """Fetch the user config for the context author (cached)."""
        return await self.bot.db.get_user_config(self.author.id)

    async def user_time(self, dt: datetime, fmt: str = "%Y-%m-%d %H:%M") -> str:
        """Format *dt* as plain text in the author's configured timezone.

        Handles UTC normalization and timezone conversion automatically.
        For Discord-native timestamps (``<t:...>``), prefer :func:`discord.utils.format_dt`
        which already localizes in each user's Discord client.
        """
        config = await self.get_user_config()
        return format_user_time(ensure_utc(dt), config, fmt)

    async def to_user_tz(self, dt: datetime) -> _dt.datetime:
        """Convert *dt* to the author's configured timezone (or UTC)."""
        config = await self.get_user_config()
        return to_user_tz(ensure_utc(dt), config)

    @property
    def clean_prefix(self) -> str:
        """This is preferred over the base implementation as I feel like regex, which was used in the base implementation, is simply unnecessary for this."""
        if self.prefix is None:
            return ""

        user = self.bot.user
        if user is None:
            return self.prefix or ""
        MENTIONED_REGEX = re.compile(rf"<@!?{user.id}>")
        return MENTIONED_REGEX.sub(f"@{user.name}", self.prefix)

    @property
    def is_interaction(self) -> bool:
        """Whether an interaction is attached to this context."""
        return self.interaction is not None

    @discord.utils.cached_property
    def replied_reference(self) -> discord.MessageReference | None:
        ref = self.message.reference
        if ref and isinstance(ref.resolved, discord.Message):
            return ref.resolved.to_reference()
        return None

    @discord.utils.cached_property
    def replied_message(self) -> discord.Message | None:
        ref = self.message.reference
        if ref and isinstance(ref.resolved, discord.Message):
            return ref.resolved
        return None

    async def confirm(
        self,
        content: str | None = None,
        *,
        view: ConfirmationView | None = None,
        user: discord.Member | discord.User | None = None,
        timeout: float = 60.0,
        true: str = "Yes",
        false: str = "No",
        interaction: discord.Interaction | None = None,
        hook: AsyncCallable[discord.Interaction, None] | None = None,
        **kwargs: Any,
    ) -> bool | None:
        """|coro|

        Sends a CV2 ConfirmationView (prompt text + buttons in a single card)
        and waits for the user's choice.

        Parameters
        ----------
        content: str
            The prompt text rendered inside the confirmation card.
        view: ConfirmationView
            An already-constructed view (content is set via
            ``view.set_content`` if provided).
        user: discord.Member | discord.User
            The user to send the confirmation to.
        timeout: float
            The timeout for the confirmation.
        true: str
            The label for the confirm button.
        false: str
            The label for the cancel button.
        interaction: discord.Interaction
            The interaction to use for the response.
        hook: Callable[[discord.Interaction], None]
            A hook invoked on confirm — result stored in
            ``view.hook_value``.
        **kwargs
            Additional keyword arguments to pass to the send method.
        """
        author = user or self.author
        view = view or ConfirmationView(
            author, content=content, true=true, false=false,
            hook=hook, timeout=timeout, delete_after=True,
        )
        if content and view._content != content:
            view.set_content(content)

        # LayoutView messages cannot carry content/embeds — only view=
        kwargs.pop("content", None)
        kwargs.pop("embed", None)
        kwargs.pop("embeds", None)

        if interaction is not None:
            await interaction.response.send_message(view=view, **kwargs)
            await view.wait()
            return view.value

        view.message = await self.send(view=view, **kwargs)

        await view.wait()
        with suppress(discord.HTTPException):
            if view.message:
                await view.message.delete()
        return view.value

    async def disambiguate(
        self, matches: list[T], entry: Callable[[T], Any], *, ephemeral: bool = False
    ) -> T:
        if len(matches) == 0:
            raise ValueError("No results found.")

        if len(matches) == 1:
            return matches[0]

        if len(matches) > 25:
            raise ValueError("Too many results... sorry.")

        view = DisambiguatorView(self, matches, entry)
        view.message = await self.send(view=view, ephemeral=ephemeral)
        await view.wait()
        return view.selected

    async def send_success(self, content: str, **kwargs: Any) -> discord.Message:
        """Sends a success message."""
        emoji = Emojis.success if self.bot_permissions.use_external_emojis else "\N{WHITE HEAVY CHECK MARK}"
        return await self.send(f"{emoji} {content}", **kwargs)

    async def send_error(self, content: str, **kwargs: Any) -> discord.Message:
        """Sends an error message."""
        kwargs.setdefault("delete_after", 15)
        kwargs.setdefault("ephemeral", True)
        kwargs.setdefault("reference", self.message)
        emoji = Emojis.error if self.bot_permissions.use_external_emojis else "\N{CROSS MARK}"
        return await self.send(f"{emoji} {content}", **kwargs)

    async def send_info(self, content: str, **kwargs: Any) -> discord.Message:
        """Sends an info message."""
        emoji = Emojis.info if self.bot_permissions.use_external_emojis else "\N{INFORMATION SOURCE}"
        return await self.send(f"{emoji} {content}", **kwargs)

    async def send_warning(self, content: str, **kwargs: Any) -> discord.Message:
        """Sends a warning message."""
        kwargs.setdefault("delete_after", 15)
        kwargs.setdefault("ephemeral", True)
        kwargs.setdefault("reference", self.message)
        emoji = Emojis.warning if self.bot_permissions.use_external_emojis else "\N{WARNING SIGN}"
        return await self.send(f"{emoji} {content}", **kwargs)

    async def send(self, content: Any = None, **kwargs: Any) -> discord.Message:
        if kwargs.get("embed") and kwargs.get("embeds") is not None:
            kwargs["embeds"].append(kwargs["embed"])
            del kwargs["embed"]

        if kwargs.get("file") and kwargs.get("files") is not None:
            kwargs["files"].append(kwargs["file"])
            del kwargs["file"]

        if kwargs.pop("edit", False) and self._message:
            kwargs.pop("files", None)
            kwargs.pop("reference", None)

            await self.maybe_edit(content, **kwargs)
            return self._message

        if (
            self.is_interaction
            and self.interaction
            and not self.interaction.is_expired()
            and not self.interaction.response.is_done()
        ):
            # If there is a pending interaction from maybe a hybrid app command left, we should use that instead
            kwargs.pop("reference", None)
            kwargs.pop("mention_author", None)
            kwargs.pop("nonce", None)
            kwargs.pop("stickers", None)
            await self.interaction.response.send_message(content, **kwargs)
            self._message = result = await self.interaction.original_response()
        else:
            self._message = result = await super().send(content, **kwargs)
        return result  # type: ignore[return-value]

    async def maybe_edit(
        self, message: discord.Message | None = None, content: Any = None, **kwargs: Any
    ) -> discord.Message | None:
        """Edits the message silently."""
        message = message or self._message
        try:
            await message.edit(content=content, **kwargs)  # type: ignore[union-attr]
        except (AttributeError, discord.NotFound):
            if not message or message.channel == self.channel:
                return await self.send(content, **kwargs)

            return await message.channel.send(content, **kwargs)

    async def maybe_delete(self, message: discord.Message | None = None, *args: Any, **kwargs: Any) -> None:
        """Deletes the message silently if it exists."""
        message = message or self._message
        with suppress(AttributeError, discord.NotFound, discord.Forbidden):
            await message.delete(*args, **kwargs)  # type: ignore[union-attr]

    async def defer(self, *, ephemeral: bool = False, typing: bool = False) -> None:
        """Defers the response of the interaction or starts typing if it's a regular message."""
        if (
            self.is_interaction
            and self.interaction
            and not self.interaction.is_expired()
            and not self.interaction.response.is_done()
        ):
            await self.interaction.response.defer(ephemeral=ephemeral)
        else:
            if typing:
                await self.typing()

    def progress(self, initial_status: str, *, ephemeral: bool = False) -> ProgressTracker:
        """Return a :class:`ProgressTracker` context manager for long-running operations.

        Usage::

            async with ctx.progress("Fetching data...") as progress:
                await progress.update("Page 1/3...")
        """
        return ProgressTracker(self, initial_status, ephemeral=ephemeral)

    async def safe_send(self, content: str, *, escape_mentions: bool = True, **kwargs: Any) -> discord.Message:
        if escape_mentions:
            content = discord.utils.escape_mentions(content)

        if len(content) > 2000:
            fp = io.BytesIO(content.encode())
            kwargs.pop("file", None)
            return await self.send(file=discord.File(fp, filename="message_too_long.txt"), **kwargs)
        else:
            return await self.send(content)


ContextT = TypeVar("ContextT", bound=Context, covariant=True)  # type: ignore[misc]


@runtime_checkable
class HybridContextProtocol(Protocol[ContextT]):  # type: ignore[misc]
    """Protocol to match the :class:`.Context` class for hybrid command implementations."""

    async def full_invoke(self, *args: Any, **kwargs: Any) -> Any:
        """|coro|

        Fully invokes the command with the given arguments and keyword arguments.

        Notes
        -----
        The full invoke function for the command, used to invoke the parent command implementation.
        The passed arguments must follow exactly the same signature as the command's hybrid callback.

        `self` and `ctx` parameter are automatically added to the arguments.

        Parameters
        ----------
        args: Any
            The arguments to pass to the command.
        kwargs: Any
            The keyword arguments to pass to the command.
        """
        ...


class HybridContext(Context, HybridContextProtocol):
    """A Context type especially for application command implementations
    that were defined by using the :func:`.define_app_command()` decorator.

    This can only be used on application commands that derive from hybrid commands and are defined separately.

    Attributes
    ----------
    interaction: discord.Interaction
        The interaction that triggered the command.
    """
    interaction: discord.Interaction  # type: ignore[override]
