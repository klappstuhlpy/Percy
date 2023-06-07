import asyncio
import traceback
from types import TracebackType
from typing import Union, Type, Optional, ParamSpec, TypeVar, Awaitable, Callable

import discord

from cogs.utils.paginator import TextSource


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
