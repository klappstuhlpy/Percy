import asyncio
import inspect
import logging
import subprocess
import time
import traceback
from types import TracebackType
from typing import Union, Type, Optional, ParamSpec, TypeVar, Awaitable, Callable

import discord

from cogs.utils.paginator import TextSource


# jishaku base code


class DebugResponseReactor:
    """Extension of the ReactionProcedureTimer that absorbs errors, sending tracebacks.

    This is used for debugging purposes, and should not be used in production."""

    __slots__ = ('message', 'loop', 'logger', 'start_time', 'handle', 'raised')

    def __init__(self, message: discord.Message, loop: Optional[asyncio.BaseEventLoop] = None,
                 logger: logging.Logger = None):
        self.logger: logging.Logger = logger
        self.message = message
        self.loop = loop or asyncio.get_event_loop()
        self.handle = None
        self.raised = False
        self.start_time: time.time = None

    async def __aenter__(self):
        self.start_time = time.time()
        self.handle = self.loop.create_task(do_after_sleep(2, attempt_add_reaction, self.message,
                                                           "\N{BLACK RIGHT-POINTING TRIANGLE}"))

        if self.logger is None:
            frame = inspect.currentframe().f_back
            filename = frame.f_code.co_filename
            lineno = frame.f_lineno

            self.logger = logging.getLogger(f"DRR:{filename}:{lineno}")
            self.logger.setLevel(logging.DEBUG)

        return self

    async def __aexit__(
        self,
        exc_type: Type[BaseException],
        exc_val: BaseException,
        exc_tb: TracebackType
    ) -> bool:
        if self.handle:
            self.handle.cancel()

        if not exc_val:
            await attempt_add_reaction(self.message, "\N{WHITE HEAVY CHECK MARK}")
            return False

        execution_time = time.time() - self.start_time
        print(f"Execution time: {execution_time} seconds")

        self.raised = True

        if isinstance(exc_val, (SyntaxError, asyncio.TimeoutError, subprocess.TimeoutExpired)):
            destination = self.message.channel

            if destination != self.message.channel:
                await attempt_add_reaction(
                    self.message,
                    "\N{HEAVY EXCLAMATION MARK SYMBOL}" if isinstance(exc_val, SyntaxError) else "\N{ALARM CLOCK}"
                )

            await send_traceback(
                self.message if destination == self.message.channel else destination,
                0, exc_type, exc_val, exc_tb
            )
        else:
            destination = self.message.channel

            if destination != self.message.channel:
                await attempt_add_reaction(self.message, "\N{DOUBLE EXCLAMATION MARK}")

            await send_traceback(
                self.message if destination == self.message.channel else destination,
                8, exc_type, exc_val, exc_tb
            )

        return True


T = TypeVar('T')
P = ParamSpec('P')


async def do_after_sleep(delay: float, coro: Callable[P, Awaitable[T]], *args: P.args, **kwargs: P.kwargs) -> T:
    """Performs an action after a set amount of time.

    This function only calls the coroutine after the delay,
    preventing asyncio complaints about destroyed coros.

    Parameters
    ----------
    delay: float
        The amount of time to wait before calling the coroutine.
    coro: Callable[P, Awaitable[T]]
        The coroutine to call.
    args: P.args
        The arguments to pass to the coroutine.
    kwargs: P.kwargs
        The keyword arguments to pass to the coroutine.
    """
    await asyncio.sleep(delay)
    return await coro(*args, **kwargs)


async def attempt_add_reaction(
    msg: discord.Message,
    reaction: Union[str, discord.Emoji]
) -> Optional[discord.Reaction]:
    """Try to add a reaction to a message, ignoring it if it fails for any reason.

    Parameters
    ----------
    msg: discord.Message
        The message to add the reaction to.
    reaction: Union[str, discord.Emoji]
        The reaction to add.
    """
    try:
        return await msg.add_reaction(reaction)
    except discord.HTTPException:
        pass


async def send_traceback(
    destination: Union[discord.abc.Messageable, discord.Message],
    verbosity: int,
    etype: Type[BaseException],
    value: BaseException,
    trace: TracebackType
):
    """Sends a traceback of an exception to a destination.

    Parameters
    ----------
    destination: Union[discord.abc.Messageable, discord.Message]
        The destination to send the traceback to.
    verbosity: int
        The amount of lines to send.
    etype: Type[BaseException]
        The type of exception.
    value: BaseException
        The exception itself.
    trace: TracebackType
        The traceback.
    """

    traceback_content = "".join(traceback.format_exception(etype, value, trace, verbosity)).replace("``", "`\u200b`")

    paginator = TextSource(prefix='```py')
    for line in traceback_content.split('\n'):
        paginator.add_line(line)

    message = None

    for page in paginator.pages:
        if isinstance(destination, discord.Message):
            message = await destination.reply(page)
        else:
            message = await destination.send(page)

    return message
