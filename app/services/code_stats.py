"""Pure source-tree statistics.

Extracted from the ``stats`` cog's ``project_stats_counter`` god-method so the
counting logic lives free of Discord and presentation concerns and can be unit
tested directly. The cog keeps only the ANSI formatting and delegates the walk
here.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

__all__ = (
    "CodeStats",
    "count_code_stats",
)


@dataclass(slots=True)
class CodeStats:
    """Aggregate counts for a tree of Python source files."""

    files: int = 0
    classes: int = 0
    functions: int = 0
    comments: int = 0
    lines: int = 0
    characters: int = 0


def count_code_stats(root: Path, *, ignored: Iterable[Path] = ()) -> CodeStats:
    """Walk ``root`` recursively and tally statistics for every ``*.py`` file.

    A file is skipped when any of its parent directories is listed in ``ignored``.
    The counting is intentionally line-prefix based (matching the original cog
    behaviour): a line counts as a class/function when its stripped form starts
    with ``class``/``def``/``async def``, and as a comment when it contains ``#``.

    This is blocking I/O; callers on the event loop should run it in a thread
    (e.g. via the ``@executor`` decorator).
    """
    ignored = set(ignored)
    stats = CodeStats()

    for file in root.rglob("*.py"):
        if ignored and any(parent in ignored for parent in file.parents):
            continue

        stats.files += 1
        text = file.read_text(encoding="utf8", errors="ignore")
        stats.characters += len(text)

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if line.startswith("class"):
                stats.classes += 1
            if line.startswith(("def", "async def")):
                stats.functions += 1
            if "#" in line:
                stats.comments += 1
            stats.lines += 1

    return stats
