from __future__ import annotations

import asyncio
import contextlib
import re
from functools import partial
from operator import attrgetter
from textwrap import dedent
from typing import Literal, NamedTuple, Annotated, Optional

import discord
from discord import AllowedMentions, HTTPException, Interaction, Message, NotFound, Reaction, User, enums
from discord.ext import commands

from bot import Percy
from cogs import command
from cogs.base import TrashView
from cogs.snekbox._eval import EvalJob, EvalResult
from cogs.snekbox._formatter import FileAttachment
from cogs.utils.constants import FORMATTED_CODE_REGEX, RAW_CODE_REGEX
from cogs.utils.context import EvalContext
from cogs.utils.lock import lock_arg
from cogs.utils.services import send_to_paste_service, PasteUploadError, PasteTooLongError
from launcher import get_logger

log = get_logger(__name__)

ESCAPE_REGEX = re.compile("[`\u202E\u200B]{3,}")
TXT_LIKE_FILES = {".txt", ".csv", ".json"}


# The following code is used to capture the output of timeit.timeit() and print it when the process exits.
# This is needed because timeit.timeit() prints the output to stdout, which is redirected to a Writer() instance.
TIMEIT_SETUP_WRAPPER = """
import atexit
import sys
from collections import deque

if not hasattr(sys, "_setup_finished"):
    class Writer(deque):
        '''A single-item deque wrapper for sys.stdout that will return the last line when read() is called.'''

        def __init__(self):
            super().__init__(maxlen=1)

        def write(self, string):
            '''Append the line to the queue if it is not empty.'''
            if string.strip():
                self.append(string)

        def read(self):
            '''This method will be called when print() is called.

            The queue is emptied as we don't need the output later.
            '''
            return self.pop()

        def flush(self):
            '''This method will be called eventually, but we don't need to do anything here.'''
            pass

    sys.stdout = Writer()

    def print_last_line():
        if sys.stdout: # If the deque is empty (i.e. an error happened), calling read() will raise an error
            # Use sys.__stdout__ here because sys.stdout is set to a Writer() instance
            print(sys.stdout.read(), file=sys.__stdout__)

    atexit.register(print_last_line) # When exiting, print the last line (hopefully it will be the timeit output)
    sys._setup_finished = None
{setup}
"""

MAX_PASTE_LENGTH = 10_000
MAX_OUTPUT_BLOCK_LINES = 10
MAX_OUTPUT_BLOCK_CHARS = 1000


REDO_EMOJI = "\U0001f501"  # :repeat:
REDO_TIMEOUT = 30

SupportedPythonVersions = Literal["3.11", "3.10"]


class FilteredFiles(NamedTuple):
    allowed: list[FileAttachment]
    blocked: list[FileAttachment]


class CodeblockConverter(commands.Converter):
    """Attempts to extract code from a codeblock, if provided."""

    @classmethod
    async def convert(cls, ctx: EvalContext, code: str) -> list[str]:  # type: ignore[override]
        """
        Extract code from the Markdown, format it, and insert it into the code template.

        If there is any code block, ignore text outside the code block.
        Use the first code block, but prefer a fenced code block.
        If there are several fenced code blocks, concatenate only the fenced code blocks.

        Return a list of code blocks if any, otherwise return a list with a single string of code.
        """
        if match := list(FORMATTED_CODE_REGEX.finditer(code)):
            blocks = [block for block in match if block.group("block")]

            if len(blocks) > 1:
                codeblocks = [block.group("code") for block in blocks]
                info = "several code blocks"
            else:
                match = match[0] if len(blocks) == 0 else blocks[0]
                code, block, lang, delim = match.group("code", "block", "lang", "delim")
                codeblocks = [dedent(code)]
                if block:
                    info = (f"'{lang}' highlighted" if lang else "plain") + " code block"
                else:
                    info = f"{delim}-enclosed inline code"
        else:
            codeblocks = [dedent(RAW_CODE_REGEX.fullmatch(code).group("code"))]
            info = "unformatted or badly formatted code"

        code = "\n".join(codeblocks)
        log.trace(f"Extracted {info} for evaluation:\n{code}")
        return codeblocks


class PythonVersionSwitcherButton(discord.ui.Button):
    """A button that allows users to re-run their eval command in a different Python version."""

    def __init__(
        self,
        version_to_switch_to: SupportedPythonVersions,
        snekbox_cog: Snekbox,
        ctx: EvalContext,
        job: EvalJob,
    ) -> None:
        self.version_to_switch_to = version_to_switch_to
        super().__init__(label=f"Run in {self.version_to_switch_to}", style=enums.ButtonStyle.primary)

        self.snekbox_cog = snekbox_cog
        self.ctx = ctx
        self.job = job

    async def callback(self, interaction: Interaction) -> None:
        """
        Tell snekbox to re-run the user's code in the alternative Python version.

        Use a task calling snekbox, as run_job is blocking while it waits for edit/reaction on the message.
        """
        await interaction.response.defer()

        with contextlib.suppress(NotFound):
            await interaction.message.delete()

        await self.snekbox_cog.run_job(self.ctx, self.job.as_version(self.version_to_switch_to))


class Snekbox(commands.Cog):
    """Safe evaluation of Python code using Snekbox."""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self.jobs: dict[int, int] = {}

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name="sandbox", id=1117191504973275257)

    async def post_job(self, job: EvalJob) -> EvalResult:
        """|coro|

        Send a POST request to the Snekbox API to evaluate code and return the results.

        Parameters
        ----------
        job: :class:`EvalJob`
            The job to evaluate.

        Returns
        -------
        :class:`EvalResult`
            The result of the evaluation.
        """
        data = job.to_dict()

        async with self.bot.session.post('http://localhost:8060/eval', json=data, raise_for_status=True) as resp:
            return EvalResult.from_dict(await resp.json())

    async def upload_output(self, output: str) -> Optional[str]:
        """|coro|

        Upload the job's output to a paste service and return a URL to it if successful.

        Return None if the output is empty.

        Raise a :exc:`PasteTooLongError` if the output is too long to upload.
        Raise a :exc:`PasteUploadError` if the upload fails.
        """
        log.trace("Uploading full output to paste service...")

        try:
            return await send_to_paste_service(self.bot, output, extension="txt", max_length=MAX_PASTE_LENGTH)
        except PasteTooLongError:
            return "too long to upload"
        except PasteUploadError:
            return "unable to upload"

    @staticmethod
    def prepare_timeit_input(codeblocks: list[str]) -> list[str]:
        """Join the codeblocks into a single string, then return the arguments in a list.

        If there are multiple codeblocks, insert the first one into the wrapped setup code.
        """
        args = ["-m", "timeit"]
        setup_code = codeblocks.pop(0) if len(codeblocks) > 1 else ""
        code = "\n".join(codeblocks)

        args.extend(["-s", TIMEIT_SETUP_WRAPPER.format(setup=setup_code), code])
        return args

    async def format_output(
        self,
        output: str,
        max_lines: int = MAX_OUTPUT_BLOCK_LINES,
        max_chars: int = MAX_OUTPUT_BLOCK_CHARS,
        line_nums: bool = True,
        output_default: str = "[No output]",
    ) -> tuple[str, Optional[str]]:
        """|coro|
        Format the output and return a tuple of the formatted output and a URL to the full output.

        Prepend each line with a line number. Truncate if there are over 10 lines or 1000 characters
        and upload the full output to a paste service.

        Parameters
        ----------
        output: :class:`str`
            The output to format.
        max_lines: :class:`int`
            The maximum number of lines to output.
        max_chars: :class:`int`
            The maximum number of characters to output.
        line_nums: :class:`bool`
            Whether to prepend each line with a line number.
        output_default: :class:`str`
            The default output if there is no output.

        Returns
        -------
        :class:`tuple`[:class:`str`, Optional[:class:`str`]]
            The formatted output and a URL to the full output if it was uploaded to a paste service.
        """
        output = output.rstrip("\n")
        original_output = output  # To be uploaded to a pasting service if needed
        paste_link = None

        if "<@" in output:
            output = output.replace("<@", "<@\u200B")  # Zero-width space

        if "<!@" in output:
            output = output.replace("<!@", "<!@\u200B")  # Zero-width space

        if ESCAPE_REGEX.findall(output):
            paste_link = await self.upload_output(original_output)
            return "Code block escape attempt detected; will not output result", paste_link

        truncated = False
        lines = output.splitlines()

        if len(lines) > 1:
            if line_nums:
                lines = [f"{i:03d} | {line}" for i, line in enumerate(lines, 1)]
            lines = lines[:max_lines+1]  # Limiting to max+1 lines
            output = "\n".join(lines)

        if len(lines) > max_lines:
            truncated = True
            if len(output) >= max_chars:
                output = f"{output[:max_chars]}\n... (truncated - too long, too many lines)"
            else:
                output = f"{output}\n... (truncated - too many lines)"
        elif len(output) >= max_chars:
            truncated = True
            output = f"{output[:max_chars]}\n... (truncated - too long)"

        if truncated:
            paste_link = await self.upload_output(original_output)

        if output_default and not output:
            output = output_default

        return output, paste_link

    @staticmethod
    def _filter_files(ctx: EvalContext, files: list[FileAttachment], blocked_exts: set[str]) -> FilteredFiles:
        """Filter to restrict files to allowed extensions. Return a named tuple of allowed and blocked files lists."""
        blocked = []
        allowed = []
        for file in files:
            if file.suffix in blocked_exts:
                blocked.append(file)
            else:
                allowed.append(file)

        if blocked:
            blocked_str = ", ".join(f.suffix for f in blocked)
            log.info(
                f"User '{ctx.author}' ({ctx.author.id}) uploaded blacklisted file(s) in eval: {blocked_str}",
                extra={"attachment_list": [f.filename for f in files]}
            )

        return FilteredFiles(allowed, blocked)

    @lock_arg("snekbox.send_job", "ctx", attrgetter("author.id"), raise_error=True)
    async def send_job(self, ctx: EvalContext, job: EvalJob) -> Message:
        """|coro| @locked(func, ctx)

        Evaluate code, format it, and send the output to the corresponding channel.

        Parameters
        ----------
        ctx: EvalContext
            The context of the eval command.
        job: EvalJob
            The job to evaluate.

        Returns
        -------
        discord.Message
            The message sent to the channel.
        """
        async with ctx.typing():
            result = await self.post_job(job)
            msg = result.get_message(job)
            error = result.error_message

            if error:
                output, paste_link = error, None
            else:
                log.trace("Formatting output...")
                output, paste_link = await self.format_output(result.stdout)

            msg = f"{ctx.author.mention}, {result.status_emoji} {msg}\n"

            if result.stdout.rstrip().endswith("EOFError: EOF when reading a line") and result.returncode == 1:
                msg += "\n:warning: Note: `input` is not supported by the bot :warning:\n"

            if result.stdout or not result.has_files:
                msg += f"\n```py\n{output}\n```"

            if paste_link:
                msg += f"\nFull output pasted here: <{paste_link}>"

            if files_error := result.files_error_message:
                msg += f"\n{files_error}"

            text_files = [f for f in result.files if f.suffix in TXT_LIKE_FILES]
            budget_lines = MAX_OUTPUT_BLOCK_LINES - (output.count("\n") + 1)
            budget_chars = MAX_OUTPUT_BLOCK_CHARS - len(output)
            for file in text_files:
                file_text = file.content.decode("utf-8", errors="replace") or "[Empty]"
                if len(file_text) <= 50 and not file_text.count("\n"):
                    msg += f"\n`{file.name}`\n```\n{file_text}\n```"
                else:
                    format_text, link_text = await self.format_output(
                        file_text,
                        budget_lines,
                        budget_chars,
                        line_nums=False,
                        output_default="[Empty]"
                    )
                    if link_text:
                        msg += f"\n`{file.name}`\n{link_text}"
                    else:
                        msg += f"\n`{file.name}`\n```\n{format_text}\n```"
                        budget_lines -= format_text.count("\n") + 1
                        budget_chars -= len(file_text)

            files = [f.to_file() for f in result.files if f not in text_files]
            allowed_mentions = AllowedMentions(everyone=False, roles=False, users=[ctx.author])
            await ctx.job_message.edit(
                content=msg, allowed_mentions=allowed_mentions, view=TrashView(ctx.author), attachments=files
            )

            log.info(f"{ctx.author}'s {job.name} job had a return code of {result.returncode}")
        return ctx.job_message

    async def continue_job(self, ctx: EvalContext, response: Message, job_name: str) -> Optional[EvalJob]:
        """|coro|
        
        Check if the job's session should continue.

        If the code is to be re-evaluated, return the new EvalJob.
        Otherwise, return None if the job's session should be terminated.
        
        Parameters
        ----------
        ctx: EvalContext
            The context of the eval command.
        response: discord.Message
            The message to check for reactions.
        job_name: str
            The name of the job.
        
        Returns
        -------
        Optional[EvalJob]
            The new EvalJob if the code is to be re-evaluated, else None.
        """
        _predicate_message_edit = partial(predicate_message_edit, ctx)
        _predicate_emoji_reaction = partial(predicate_emoji_reaction, ctx)

        with contextlib.suppress(NotFound):
            try:
                _, new_message = await self.bot.wait_for(
                    "message_edit",
                    check=_predicate_message_edit,
                    timeout=REDO_TIMEOUT
                )
                await ctx.message.add_reaction(REDO_EMOJI)
                await self.bot.wait_for(
                    "reaction_add",
                    check=_predicate_emoji_reaction,
                    timeout=10
                )

                if self.jobs[ctx.message.id] != response.id:
                    return None

                code = await self.get_code(new_message, ctx.command)
                with contextlib.suppress(HTTPException):
                    await ctx.message.clear_reaction(REDO_EMOJI)
                    await response.delete()

                if code is None:
                    return None

            except asyncio.TimeoutError:
                with contextlib.suppress(HTTPException):
                    await ctx.message.clear_reaction(REDO_EMOJI)
                return None

            codeblocks = await CodeblockConverter.convert(ctx, code)

            if job_name == "timeit":
                return EvalJob(self.prepare_timeit_input(codeblocks))
            return EvalJob.from_code("\n".join(codeblocks))

        return None

    async def get_code(self, message: Message, command: commands.Command) -> Optional[str]:  # noqa
        """|coro|
        
        Return the code from `message` to be evaluated.

        If the message is an invocation of the command, return the first argument or None if it
        doesn't exist. Otherwise, return the full content of the message.
        
        Parameters
        ----------
        message: discord.Message
            The message to get the code from.
        command: commands.Command
            The command to check for invocation.
        """
        log.trace(f"Getting context for message {message.id}.")
        new_ctx = await self.bot.get_context(message)

        if new_ctx.command is command:
            log.trace(f"Message {message.id} invokes {command} command.")
            split = message.content.split(maxsplit=1)
            code = split[1] if len(split) > 1 else None
        else:
            log.trace(f"Message {message.id} does not invoke {command} command.")
            code = message.content

        return code

    async def run_job(self, ctx: EvalContext, job: EvalJob):
        """|coro|

        Handles checks, stats and re-evaluation of a snekbox job.

        Parameters
        ----------
        ctx: EvalContext
            The context of the eval command.
        job: EvalJob
            The job to run.
        """
        log.info(f"Received code from {ctx.author} for evaluation:\n{job}")

        ctx.job_message = await ctx.send(f"{ctx.author.mention}, <a:loading:1072682806360166430> *Processing **{job.name}** job...*")

        while True:
            try:
                response = await self.send_job(ctx, job)
            except ValueError:
                return await ctx.send(
                    f"{ctx.author.mention}, <:warning:1113421726861238363> You've already got a job running - "
                    "please wait for it to finish!"
                )

            self.jobs[ctx.message.id] = response.id

            job = await self.continue_job(ctx, response, job.name)
            if not job:
                break
            log.info(f"Re-evaluating code from message {ctx.message.id}:\n{job}")

    @command(name="eval", aliases=["e"], usage="[python_version] <code...>")
    @commands.guild_only()
    async def eval_command(
        self,
        ctx: EvalContext,
        python_version: Optional[SupportedPythonVersions],
        *,
        code: Annotated[list[str], CodeblockConverter]
    ) -> None:
        """Run Python code and get the results.

        This command supports multiple lines of code, including formatted code blocks.
        Code can be re-evaluated by editing the original message within 10 seconds and
        clicking the reaction that subsequently appears.

        The starting working directory `/home`, is a writeable temporary file system.
        Files created, excluding names with leading underscores, will be uploaded in the response.

        If multiple codeblocks are in a message, all of them will be joined and evaluated,
        ignoring the text outside them.

        By default, your code is run on Python 3.11. A `python_version` arg of `3.10` can also be specified.

        We've done our best to make this sandboxed, but do let us know if you manage to find an
        issue with it!
        """
        python_version = python_version or "3.11"
        job = EvalJob.from_code("\n".join(code)).as_version(python_version)
        await self.run_job(ctx, job)

    @command(name="timeit", aliases=["ti"], usage="[python_version] [setup_code] <code...>")
    @commands.guild_only()
    async def timeit_command(
        self,
        ctx: EvalContext,
        python_version: Optional[SupportedPythonVersions],
        *,
        code: Annotated[list[str], CodeblockConverter]
    ) -> None:
        """Profile Python Code to find execution time.

        This command supports multiple lines of code, including code wrapped inside a formatted code
        block. Code can be re-evaluated by editing the original message within 10 seconds and
        clicking the reaction that subsequently appears.

        If multiple formatted codeblocks are provided, the first one will be the setup code, which will
        not be timed. The remaining codeblocks will be joined together and timed.

        By default, your code is run on Python 3.11. A `python_version` arg of `3.10` can also be specified.

        We've done our best to make this sandboxed, but do let us know if you manage to find an
        issue with it!
        """
        python_version = python_version or "3.11"
        args = self.prepare_timeit_input(code)
        job = EvalJob(args, version=python_version, name="timeit")
        await self.run_job(ctx, job)


def predicate_message_edit(ctx: EvalContext, old_msg: Message, new_msg: Message) -> bool:
    """Return True if the edited message is the context message and the content was indeed modified."""
    return new_msg.id == ctx.message.id and old_msg.content != new_msg.content


def predicate_emoji_reaction(ctx: EvalContext, reaction: Reaction, user: User) -> bool:
    """Return True if the reaction REDO_EMOJI was added by the context message author on this message."""
    return reaction.message.id == ctx.message.id and user.id == ctx.author.id and str(reaction) == REDO_EMOJI
