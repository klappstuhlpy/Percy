"""Folder-layer rules (no bot needed): a plain AST scan of the source tree."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / 'app'

# Existing violations of "no discord in services/engine" that need logic changes, not a move.
ALLOWLIST: set[str] = set()


def imports(path: Path, *, runtime_only: bool = False) -> list[str]:
    """Dotted names imported by a module; ``runtime_only`` skips ``if TYPE_CHECKING:`` blocks."""
    out: list[str] = []

    def walk(nodes: list[ast.stmt]) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                out.extend(a.name for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                out.append(node.module)
                out.extend(f'{node.module}.{a.name}' for a in node.names)
            elif isinstance(node, ast.If) and runtime_only and 'TYPE_CHECKING' in ast.unparse(node.test):
                walk(node.orelse)
                continue
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt):
                    walk([child])

    walk(ast.parse(path.read_text(encoding='utf-8')).body)
    return out


def rel(path: Path) -> str:
    return path.relative_to(APP).as_posix()


def test_no_discord_in_services_or_engines() -> None:
    files = [*APP.glob('services/**/*.py'), *APP.glob('cogs/*/engine/**/*.py'), *APP.glob('cogs/*/engine.py'), *APP.glob('cogs/*/*/engine/**/*.py')]
    bad = {
        rel(f) for f in files
        if any(m == 'discord' or m.startswith('discord.') for m in imports(f, runtime_only=True))
    } - ALLOWLIST
    assert not bad, f'services/engine modules must not import discord: {sorted(bad)}'


def test_internal_api_avoids_cog_and_ui_modules() -> None:
    bad = {
        f'{rel(f)} -> {m}' for f in APP.glob('internal_api/**/*.py') for m in imports(f)
        if m.startswith('app.cogs.') and m.split('.')[3:4] and m.split('.')[3] in ('cog', 'ui')
    }
    assert not bad, f'internal_api must not import a cog.py/ui.py: {sorted(bad)}'


def test_clients_live_only_in_clients() -> None:
    assert not list(APP.glob('cogs/**/client.py')), 'HTTP clients belong in app/clients/'
    bad = [rel(f) for f in APP.glob('services/**/*.py') if 'BaseHTTPClient' in f.read_text(encoding='utf-8')]
    assert not bad, f'services must not define HTTP clients: {bad}'


def test_clients_do_not_import_cogs_and_import_cleanly() -> None:
    mods = sorted(f.stem for f in APP.glob('clients/*.py') if f.stem != '__init__')
    bad = [rel(f) for f in APP.glob('clients/*.py') if any(m.startswith('app.cogs') for m in imports(f))]
    assert not bad, f'clients must not import app.cogs: {bad}'
    code = '; '.join(f'import app.clients.{m}' for m in mods)
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60, cwd=APP.parent)
    assert r.returncode == 0, r.stderr[-800:]
