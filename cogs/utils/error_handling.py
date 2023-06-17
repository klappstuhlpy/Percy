import asyncio
import traceback
from types import TracebackType
from typing import Union, Type, Optional, ParamSpec, TypeVar, Awaitable, Callable

import discord

from cogs.utils.paginator import TextSource


T = TypeVar('T')
P = ParamSpec('P')


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
