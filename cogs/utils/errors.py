from typing import Any, Optional

from discord.ext import commands

from cogs.utils import formats, helpers
from cogs.utils.context import tick, Context


async def send_error(ctx: Context, error: commands.CommandError, message: Optional[str] = None) -> None:
    """Send an error message to the context."""
    ansi = helpers.ANSI(True)

    command = ctx.command
    signature = ctx.bot.help_command.get_command_signature(command, ansi=True, with_prefix=True)
    raw_signature = ctx.bot.help_command.get_command_signature(command, with_prefix=True)

    # Add a space to the signature
    signature = ' ' * 5 + signature
    raw_signature = ' ' * 5 + raw_signature

    # exc = ''.join(traceback.format_exception(type(error), error, error.__traceback__, chain=False))
    error_message = message or getattr(error, 'message', str(error))

    argument = ctx.current_argument
    parameter = ctx.current_parameter
    param_name = getattr(parameter, 'displayed_name', None) or parameter.name

    # Find the parameter in the signature (position and line)
    _, start_column, end_column = formats.find_word(raw_signature, param_name)

    signature = ansi.white('Attempted to parse command signature:') + '\n\n' + signature

    if not signature.endswith('\n'):
        signature += '\n'

    if start_column and end_column:
        signature += ' ' * (start_column - 2) + '^' * (end_column - start_column + 3) + ' Error occurred here\n\n'
    else:
        signature += '\n'

    signature += ansi.error(f'Error: {error_message}')

    if argument:
        message = f'Could not parse input {argument!r} properly:\n'
    else:
        message = f'Could not parse command signature properly:\n'
    await ctx.stick(False, message + f'```ansi\n{signature}```')


class BadArgument(commands.BadArgument):
    """Custom Class with added functionality for prefix.

    Exception raised when a parsing or conversion failure is encountered
    on an argument to pass into a command.

    This inherits from :exc:`UserInputError`
    """

    def __init__(self, message: Optional[str] = None, *args: Any) -> None:
        if message is not None:
            # clean-up @everyone and @here mentions
            m = message.replace('@everyone', '@\u200beveryone').replace('@here', '@\u200bhere')
            # Add a Tick Emoji to the message
            super().__init__(tick(False, m), *args)
        else:
            super().__init__(*args)


class CommandError(commands.CommandError):
    """Custom Class with added functionality for prefix.

    Exception raised when the command being invoked raised an exception.

    This inherits from :exc:`Exception`
    """

    def __init__(self, message: Optional[str] = None, *args: Any) -> None:
        if message is not None:
            # clean-up @everyone and @here mentions
            m = message.replace('@everyone', '@\u200beveryone').replace('@here', '@\u200bhere')
            # Add a Tick Emoji to the message
            super().__init__(tick(False, m), *args)
        else:
            super().__init__(*args)
