from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Annotated, NamedTuple

from aiohttp import ClientConnectorError
from discord import AllowedMentions, Message, app_commands

from app.core import Bot, Cog, Context, Flags, command, flag
from app.core.converter import CodeblockConverter
from app.utils.lock import lock_arg
from config import Emojis

from .eval import EvalJob, EvalResult, SupportedPythonVersions
from .ui import EvalResultView

if TYPE_CHECKING:
    from .formatter import FileAttachment

    class EvalContext(Context):
        job_message: Message


log = logging.getLogger(__name__)

ESCAPE_REGEX = re.compile("[`‮​]{3,}")
TXT_LIKE_FILES = {".txt", ".csv", ".json"}


# The following code is used to capture the output of timeit.timeit() and print it when the process exits.
# This is needed because timeit.timeit() prints the output to stdout, which is redirected to a Writer() instance.
TIMEIT_SETUP_WRAPPER = """
import atexit
import sys
from collections import deque

if not hasattr(sys, '_setup_finished'):
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

MAX_OUTPUT_PASTE_LENGTH = 10_000


class EvalFlags(Flags):
    version: SupportedPythonVersions = flag(
        short="v", alias="ver", description="The Python version to use.", default="3.12"
    )


class FilteredFiles(NamedTuple):
    allowed: list[FileAttachment]
    blocked: list[FileAttachment]


class Snekbox(Cog):
    """Safe evaluation of Python code using Snekbox."""

    emoji = "<:sandbox:1322355113192456222>"

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.jobs: dict[int, int] = {}

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

        async with self.bot.session.post("https://snekbox.klappstuhl.me/eval", json=data, raise_for_status=True) as resp:
            return EvalResult.from_dict(await resp.json())

    async def upload_output(self, output: str) -> str | None:
        """|coro|

        Upload the job's output to a paste service and return a URL to it if successful.

        Return None if the output is empty.

        Raise a :exc:`PasteTooLongError` if the output is too long to upload.
        Raise a :exc:`PasteUploadError` if the upload fails.
        """
        log.debug("Uploading full output to paste service...")

        try:
            return await send_to_paste_service(self.bot, output, extension="txt", max_length=MAX_OUTPUT_PASTE_LENGTH)
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
                'User "%s" (%r) uploaded blacklisted file(s) in eval: %s',
                ctx.author,
                ctx.author.id,
                blocked_str,
                extra={"attachment_list": [f.filename for f in files]},
            )

        return FilteredFiles(allowed, blocked)

    @lock_arg("snekbox.send_job", "ctx", lambda ctx: ctx.author.id, raise_error=True)
    async def send_job(self, ctx: EvalContext, job: EvalJob) -> Message:
        """|coro| @locked(func, ctx)

        Evaluate code, format it, and send the output via the interactive EvalResultView.
        """
        start = time.perf_counter()
        result = await self.post_job(job)
        elapsed = time.perf_counter() - start

        paste_link = None
        if len(result.stdout) > 1500:
            paste_link = await self.upload_output(result.stdout)

        files = [f.to_file() for f in result.files if f.suffix not in TXT_LIKE_FILES]

        view = EvalResultView(
            cog=self,
            author=ctx.author,
            job=job,
            result=result,
            execution_time=elapsed,
            paste_link=paste_link,
        )

        allowed_mentions = AllowedMentions(everyone=False, roles=False, users=[ctx.author])
        await ctx.job_message.edit(
            content=None,
            allowed_mentions=allowed_mentions,
            view=view,
            attachments=files,
        )
        view.message = ctx.job_message

        log.info("%s's %s job had a return code of %r", ctx.author, job.name, result.returncode)
        return ctx.job_message

    async def run_job(self, ctx: EvalContext, job: EvalJob) -> None:
        """|coro|

        Handles checks, stats and evaluation of a snekbox job.
        """
        log.info("Received code from %s for evaluation:\n%s", ctx.author, job)

        ctx.job_message = await ctx.send(f"{ctx.author.mention}, {Emojis.loading} *Processing **{job.name}** job...*")

        try:
            response = await self.send_job(ctx, job)
        except ValueError:
            await ctx.send(
                f"{ctx.author.mention}, {Emojis.warning} You've already got a job running - "
                "please wait for it to finish!"
            )
            return

        self.jobs[ctx.message.id] = response.id

    @command(name="eval", description="Run Python code and get the results.", aliases=["e"], guild_only=True)
    @app_commands.describe(code="The Python code to run.")
    async def eval_command(
        self, ctx: EvalContext, *, code: Annotated[list[str], CodeblockConverter], flags: EvalFlags
    ) -> None:
        """Run Python code and get the results.

        This command supports multiple lines of code, including formatted code blocks.
        Code can be re-evaluated interactively using the buttons on the result panel.

        The starting working directory `/home`, is a writeable temporary file system.
        Files created, excluding names with leading underscores, will be uploaded in the response.

        If multiple codeblocks are in a message, all of them will be joined and evaluated,
        ignoring the text outside them.

        We've done our best to make this sandboxed, but do let us know if you manage to find an
        issue with it!
        """
        job = EvalJob.from_code("\n".join(code)).as_version(flags.version)
        await self.run_job(ctx, job)

    @command(name="timeit", description="Profile Python Code to find execution time.", aliases=["ti"], guild_only=True)
    @app_commands.describe(code="The Python code to run.")
    async def timeit_command(
        self, ctx: EvalContext, *, code: Annotated[list[str], CodeblockConverter], flags: EvalFlags
    ) -> None:
        """Profile Python Code to find execution time.

        This command supports multiple lines of code, including code wrapped inside a formatted code
        block. Code can be re-evaluated interactively using the buttons on the result panel.

        If multiple formatted codeblocks are provided, the first one will be the setup code, which will
        not be timed. The remaining codeblocks will be joined together and timed.

        We've done our best to make this sandboxed, but do let us know if you manage to find an
        issue with it!
        """
        args = self.prepare_timeit_input(code)
        job = EvalJob(args, version=flags.version, name="timeit")
        await self.run_job(ctx, job)


FAILED_REQUEST_ATTEMPTS = 3
MAX_PASTE_LENGTH = 100_000
PASTE_URL = "https://paste.pythondiscord.com/{key}"


class PasteUploadError(Exception):
    """Raised when an error is encountered uploading to the paste service."""


class PasteTooLongError(Exception):
    """Raised when content is too large to upload to the paste service."""


async def send_to_paste_service(bot: Bot, contents: str, *, extension: str = "", max_length: int = MAX_PASTE_LENGTH) -> str:
    """
    Upload `contents` to the paste service.

    Add `extension` to the output URL. Use `max_length` to limit the allowed contents length
    to lower than the maximum allowed by the paste service.

    Raise `ValueError` if `max_length` is greater than the maximum allowed by the paste service.
    Raise `PasteTooLongError` if `contents` is too long to upload, and `PasteUploadError` if uploading fails.

    Return the generated URL with the extension.
    """
    if max_length > MAX_PASTE_LENGTH:
        raise ValueError(f"`max_length` must not be greater than {MAX_PASTE_LENGTH}")

    extension = extension and f".{extension}"

    contents_size = len(contents.encode())
    if contents_size > max_length:
        log.info("Contents too large to send to paste service.")
        raise PasteTooLongError(f"Contents of size {contents_size} greater than maximum size {max_length}")

    log.debug("Sending contents of size %r bytes to paste service.", contents_size)
    paste_url = PASTE_URL.format(key="documents")
    for attempt in range(1, FAILED_REQUEST_ATTEMPTS + 1):
        try:
            async with bot.session.post(paste_url, data=contents) as response:
                response_json = await response.json()
        except ClientConnectorError:
            log.warning(
                "Failed to connect to paste service at url %s, trying again (%r/%r).",
                paste_url,
                attempt,
                FAILED_REQUEST_ATTEMPTS,
            )
            continue
        except Exception:
            log.exception(
                "An unexpected error has occurred during handling of the request, trying again (%r/%r).",
                attempt,
                FAILED_REQUEST_ATTEMPTS,
            )
            continue

        if "message" in response_json:
            log.warning(
                "Paste service returned error %s with status code %r, trying again (%r/%r).",
                response_json["message"],
                response.status,
                attempt,
                FAILED_REQUEST_ATTEMPTS,
            )
            continue
        if "key" in response_json:
            log.info("Successfully uploaded contents to paste service behind key %s.", response_json["key"])

            paste_link = PASTE_URL.format(key=response_json["key"]) + extension

            if extension == ".py":
                return paste_link

            return paste_link + "?noredirect"

        log.warning(
            "Got unexpected JSON response from paste service: %s\ntrying again (%r/%r).",
            response_json,
            attempt,
            FAILED_REQUEST_ATTEMPTS,
        )

    raise PasteUploadError("Failed to upload contents to paste service")


async def setup(bot: Bot) -> None:
    try:
        async with bot.session.get("https://snekbox.klappstuhl.me/") as resp:
            if resp.status == 502:
                log.warning("Cannot connect to Snekbox API. Failed to load Snekbox cog...")
            else:
                log.info("Successfully connected to Snekbox API.")
                await bot.add_cog(Snekbox(bot))
    except ClientConnectorError:
        log.warning("Cannot connect to Snekbox API. Failed to load Snekbox cog...")
