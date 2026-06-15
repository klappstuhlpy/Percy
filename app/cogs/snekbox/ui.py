from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING

import discord

from app.core.views import LayoutView
from config import Emojis

from .eval import EvalJob, EvalResult, SupportedPythonVersions
from .formatter import sizeof_fmt

if TYPE_CHECKING:
    from .cog import Snekbox

_BRAND = discord.Colour(0xD97757)
_SUCCESS = discord.Colour(0x166534)
_ERROR = discord.Colour(0xDC2626)

MAX_CODE_DISPLAY = 1000
MAX_OUTPUT_DISPLAY = 1500
MAX_MODAL_LENGTH = 4000

TXT_LIKE_FILES = {".txt", ".csv", ".json"}

SANDBOX_INFO = (
    "### \U0001f512 Sandbox Environment\n"
    "\U0001f4bb **Runtime** — NsJail isolated container\n"
    "\U0001f4c2 **Working dir** — `/home` (tmpfs, writable)\n"
    "\U0001f6ab **Network** — fully disabled (no loopback)\n"
    "\U0001f4be **Filesystem** — read-only (except `/home` and `/dev/shm`)\n"
    "\U0001f4e6 **Packages** — stdlib only\n"
    "\U0001f4c4 **File output** — files in `/home` (no `_` prefix) are returned\n"
    "⏱️ **Time limit** — 6 seconds\n"
    "\U0001f4a1 **Memory** — 70 MiB (no swap)\n"
    "\U0001f9f5 **Processes** — max 15 PIDs\n"
    "\U0001f40d **Interpreters** — 3.12, 3.13, 3.14\n"
    "-# Output truncated at ~1 MB. `input()` is not supported."
)


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_ESCAPE.sub("", text)


def _truncate(text: str, limit: int, newline: str = "\n") -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20] + f"{newline}... (truncated)"


def _format_returncode(code: int | None) -> str:
    if code is None:
        return "Fatal Error"
    if code == 0:
        return "Success (0)"
    if code == 137:
        return "Killed — OOM/Timeout (137)"
    if code == 255:
        return "NsJail Fatal (255)"
    return f"Exit Code {code}"


def _build_error_trace(stdout: str) -> str | None:
    """Extract a Python traceback from stdout if present."""
    lines = stdout.strip().splitlines()
    tb_start = None
    for i, line in enumerate(lines):
        if line.startswith("Traceback (most recent call last):"):
            tb_start = i
            break

    if tb_start is None:
        return None

    return "\n".join(lines[tb_start:])


class CodeEditModal(discord.ui.Modal, title="Edit Code"):
    """Modal for editing and re-running code."""

    code = discord.ui.TextInput(
        label="Python Code",
        style=discord.TextStyle.long,
        max_length=MAX_MODAL_LENGTH,
        placeholder="Enter your Python code here...",
    )

    def __init__(self, current_code: str) -> None:
        super().__init__()
        self.code.default = current_code[:MAX_MODAL_LENGTH]
        self.new_code: str | None = None

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        self.new_code = self.code.value
        await interaction.response.defer()
        self.stop()


class EvalResultView(LayoutView):
    """Interactive CV2 view for snekbox eval results.

    Displays output, stats, error tracing, and provides buttons
    for re-running, editing code, switching Python versions, and more.
    """

    def __init__(
        self,
        cog: Snekbox,
        author: discord.Member | discord.User,
        job: EvalJob,
        result: EvalResult,
        *,
        execution_time: float,
        paste_link: str | None = None,
    ) -> None:
        super().__init__(timeout=300.0, members=author)
        self.cog = cog
        self.author = author
        self.job = job
        self.result = result
        self.execution_time = execution_time
        self.paste_link = paste_link
        self._show_code = False
        self._show_trace = False
        self._show_sandbox_info = False
        self._run_count = 1

        self._rerun_btn = discord.ui.Button(
            label="Re-run", style=discord.ButtonStyle.green, emoji="\U0001f501"
        )
        self._rerun_btn.callback = self._on_rerun

        self._edit_btn = discord.ui.Button(
            label="Edit & Run", style=discord.ButtonStyle.blurple, emoji="✏️"
        )
        self._edit_btn.callback = self._on_edit

        self._version_btn = discord.ui.Button(
            label=f"Python {job.version}", style=discord.ButtonStyle.grey, emoji="\U0001f40d"
        )
        self._version_btn.callback = self._on_cycle_version

        self._toggle_code_btn = discord.ui.Button(
            label="Show Code", style=discord.ButtonStyle.grey, emoji="\U0001f4c4"
        )
        self._toggle_code_btn.callback = self._on_toggle_code

        self._trace_btn = discord.ui.Button(
            label="Error Trace", style=discord.ButtonStyle.red, emoji="\U0001f41b"
        )
        self._trace_btn.callback = self._on_toggle_trace

        self._sandbox_btn = discord.ui.Button(
            label="Sandbox Info", style=discord.ButtonStyle.grey, emoji="\U0001f512"
        )
        self._sandbox_btn.callback = self._on_toggle_sandbox

        self._delete_btn = discord.ui.Button(
            label="Delete", style=discord.ButtonStyle.red, emoji=Emojis.trash
        )
        self._delete_btn.callback = self._on_delete

        has_trace = _build_error_trace(result.stdout) is not None
        if not has_trace:
            self._trace_btn.disabled = True
            self._trace_btn.style = discord.ButtonStyle.grey

        self._rebuild_layout()

    def _rebuild_layout(self) -> None:
        self.clear_items()
        result = self.result
        job = self.job

        accent = _SUCCESS if result.returncode == 0 else (_ERROR if result.returncode else _BRAND)
        container = discord.ui.Container(accent_colour=accent)

        # --- Header ---
        status = "completed successfully" if result.returncode == 0 else "finished with errors"
        if result.returncode is None:
            status = "failed"
        elif result.returncode == 137:
            status = "timed out or ran out of memory"

        header = f"## {result.status_emoji} Eval Result\n-# Your **{job.name}** job {status}"
        if self._run_count > 1:
            header += f" (run #{self._run_count})"

        container.add_item(discord.ui.TextDisplay(header))
        container.add_item(discord.ui.Separator())

        # --- Stats Section ---
        stats_lines = [
            f"\U0001f40d **Python** `{job.version}`",
            f"⏱️ **Execution** `{self.execution_time:.3f}s`",
            f"\U0001f4e4 **Return Code** `{_format_returncode(result.returncode)}`",
        ]
        if result.files:
            total_size = sum(len(f.content) for f in result.files)
            stats_lines.append(f"\U0001f4ce **Files** `{len(result.files)}` ({sizeof_fmt(total_size)})")
        if self.paste_link:
            stats_lines.append(f"\U0001f4cb **Full Output** [paste]({self.paste_link})")

        stdout_lines = len(result.stdout.strip().splitlines()) if result.stdout.strip() else 0
        stdout_bytes = len(result.stdout.encode())
        stats_lines.append(f"\U0001f4cf **Output Size** `{stdout_lines} lines` / `{sizeof_fmt(stdout_bytes)}`")

        container.add_item(discord.ui.TextDisplay("\n".join(stats_lines)))

        # --- Output ---
        if result.stdout.strip() or not result.has_files:
            container.add_item(discord.ui.Separator())
            output = _strip_ansi(result.stdout.strip()) or "[No output]"

            if self._show_trace:
                trace = _build_error_trace(result.stdout)
                if trace:
                    output = _strip_ansi(trace)

            output = _truncate(output, MAX_OUTPUT_DISPLAY)
            container.add_item(discord.ui.TextDisplay(
                f"### Output\n```py\n{output}\n```"
            ))

        # --- Error Trace Hint ---
        if result.returncode and result.returncode != 0 and not self._show_trace:
            trace = _build_error_trace(result.stdout)
            if trace:
                last_line = _strip_ansi(result.stdout.strip().splitlines()[-1]) if result.stdout.strip() else ""
                if last_line:
                    container.add_item(discord.ui.TextDisplay(
                        f"-# \U0001f41b `{_truncate(last_line, 300, "")}`"
                    ))

        # --- EOF Warning ---
        if result.stdout.rstrip().endswith("EOFError: EOF when reading a line") and result.returncode == 1:
            container.add_item(discord.ui.TextDisplay(
                f"\n{Emojis.warning} **Note:** `input()` is not supported in the sandbox."
            ))

        # --- Files Error ---
        if result.files_error_message:
            container.add_item(discord.ui.TextDisplay(f"\n{result.files_error_message}"))

        # --- Text File Contents ---
        text_files = [f for f in result.files if f.suffix in TXT_LIKE_FILES]
        if text_files:
            container.add_item(discord.ui.Separator())
            file_parts = []
            for f in text_files[:3]:
                content = f.content.decode("utf-8", errors="replace") or "[Empty]"
                content = _truncate(content, 300)
                file_parts.append(f"`{f.name}`\n```\n{content}\n```")
            container.add_item(discord.ui.TextDisplay("\n".join(file_parts)))

        # --- Show Code Section ---
        if self._show_code:
            container.add_item(discord.ui.Separator())
            code = self._get_source_code()
            code_display = _truncate(code, MAX_CODE_DISPLAY)
            container.add_item(discord.ui.TextDisplay(
                f"### Source Code\n```py\n{code_display}\n```"
            ))

        # --- Sandbox Info Section ---
        if self._show_sandbox_info:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(SANDBOX_INFO))

        # --- Action Buttons ---
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(
            self._rerun_btn, self._edit_btn, self._version_btn
        ))
        container.add_item(discord.ui.ActionRow(
            self._toggle_code_btn, self._trace_btn, self._sandbox_btn, self._delete_btn
        ))

        self.add_item(container)

    def _get_source_code(self) -> str:
        for f in self.job.files:
            if f.filename.endswith(".py"):
                return f.content.decode("utf-8", errors="replace")
        if self.job.args:
            return " ".join(self.job.args)
        return "[no source available]"

    def _update_after_run(self, new_result: EvalResult, elapsed: float, paste_link: str | None) -> None:
        self.result = new_result
        self.execution_time = elapsed
        self.paste_link = paste_link
        self._run_count += 1
        self._rerun_btn.disabled = False
        self._rerun_btn.label = "Re-run"

        has_trace = _build_error_trace(new_result.stdout) is not None
        self._trace_btn.disabled = not has_trace
        self._trace_btn.style = discord.ButtonStyle.red if has_trace else discord.ButtonStyle.grey
        self._show_trace = False

    async def _execute_and_update(self, interaction: discord.Interaction) -> None:
        start = time.perf_counter()
        new_result = await self.cog.post_job(self.job)
        elapsed = time.perf_counter() - start

        paste_link = None
        if len(new_result.stdout) > MAX_OUTPUT_DISPLAY:
            paste_link = await self.cog.upload_output(new_result.stdout)

        self._update_after_run(new_result, elapsed, paste_link)
        self._rebuild_layout()
        await interaction.edit_original_response(view=self)

    async def _on_rerun(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        self._rerun_btn.disabled = True
        self._rerun_btn.label = "Running..."
        self._rebuild_layout()
        await interaction.edit_original_response(view=self)
        await self._execute_and_update(interaction)

    async def _on_edit(self, interaction: discord.Interaction) -> None:
        code = self._get_source_code()
        modal = CodeEditModal(code)
        await interaction.response.send_modal(modal)
        if await modal.wait():
            return
        if modal.new_code is None:
            return

        self.job = EvalJob.from_code(modal.new_code).as_version(self.job.version)

        self._rerun_btn.disabled = True
        self._rerun_btn.label = "Running..."
        self._rebuild_layout()
        await interaction.edit_original_response(view=self)
        await self._execute_and_update(interaction)

    async def _on_cycle_version(self, interaction: discord.Interaction) -> None:
        versions = list(SupportedPythonVersions.__args__)  # type: ignore[attr-defined]
        current_idx = versions.index(self.job.version) if self.job.version in versions else 0
        next_version = versions[(current_idx + 1) % len(versions)]
        self.job = self.job.as_version(next_version)
        self._version_btn.label = f"Python {next_version}"

        self._rerun_btn.disabled = True
        self._rerun_btn.label = "Running..."
        self._rebuild_layout()
        await interaction.response.edit_message(view=self)
        await self._execute_and_update(interaction)

    async def _on_toggle_code(self, interaction: discord.Interaction) -> None:
        self._show_code = not self._show_code
        self._toggle_code_btn.label = "Hide Code" if self._show_code else "Show Code"
        self._toggle_code_btn.style = (
            discord.ButtonStyle.blurple if self._show_code else discord.ButtonStyle.grey
        )
        self._rebuild_layout()
        await interaction.response.edit_message(view=self)

    async def _on_toggle_trace(self, interaction: discord.Interaction) -> None:
        self._show_trace = not self._show_trace
        self._trace_btn.label = "Full Output" if self._show_trace else "Error Trace"
        self._rebuild_layout()
        await interaction.response.edit_message(view=self)

    async def _on_toggle_sandbox(self, interaction: discord.Interaction) -> None:
        self._show_sandbox_info = not self._show_sandbox_info
        self._sandbox_btn.label = "Hide Info" if self._show_sandbox_info else "Sandbox Info"
        self._sandbox_btn.style = (
            discord.ButtonStyle.blurple if self._show_sandbox_info else discord.ButtonStyle.grey
        )
        self._rebuild_layout()
        await interaction.response.edit_message(view=self)

    async def _on_delete(self, interaction: discord.Interaction) -> None:
        self.stop()
        if interaction.message:
            await interaction.message.delete()
        else:
            await interaction.response.defer()
