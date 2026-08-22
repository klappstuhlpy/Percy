"""Discord caps a bot at 100 *global* slash commands; going over kills the whole
extension whose command tipped the scale (that is how ``app.cogs.user`` once failed
to load).  Counting the decorators statically keeps the ceiling visible without
booting a bot: every top-level ``@command(..., hybrid=True)`` / ``@group(..., hybrid=True)``
claims one slot, unless it is pinned to specific guilds via ``@guilds(...)``.
"""

from __future__ import annotations

import ast
from pathlib import Path

COGS = Path(__file__).resolve().parent.parent / 'app' / 'cogs'
LIMIT = 100


def _decorator_name(node: ast.expr) -> str:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Attribute):
        return f'{ast.unparse(target.value)}.{target.attr}'
    return getattr(target, 'id', '')


def global_slash_commands() -> list[str]:
    found: list[str] = []
    for path in COGS.rglob('*.py'):
        tree = ast.parse(path.read_text(encoding='utf-8'), str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            names = [_decorator_name(d) for d in node.decorator_list]
            if 'guilds' in names:  # guild-scoped commands live in a per-guild bucket
                continue
            for deco in node.decorator_list:
                if not isinstance(deco, ast.Call) or _decorator_name(deco) not in {'command', 'group'}:
                    continue  # an attribute decorator (@group.command) is a subcommand: no own slot
                if any(kw.arg == 'hybrid' and getattr(kw.value, 'value', False) is True for kw in deco.keywords):
                    found.append(f'{path.name}:{node.name}')
    return found


def test_stays_under_the_global_slash_limit() -> None:
    commands = global_slash_commands()
    assert len(commands) < LIMIT, (
        f'{len(commands)} global slash commands (limit {LIMIT}). '
        f'Drop hybrid=True from a command that works fine as a text command.'
    )
