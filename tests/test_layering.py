"""Folder-layer rules (no bot needed): a plain AST scan of the source tree."""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / 'app'


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
    }
    assert not bad, f'services/engine modules must not import discord: {sorted(bad)}'


def _forbidden_cog_module(m: str) -> bool:
    """``app.cogs.<x>.cog`` / ``.ui``, a flat cog module (``app/cogs/<name>.py``) or ``app.cogs.misc.*``."""
    parts = m.split('.')
    if parts[:2] != ['app', 'cogs'] or len(parts) < 3:
        return False
    return (parts[2] == 'misc' and len(parts) > 3) or (APP / 'cogs' / f'{parts[2]}.py').is_file() or parts[3:4] in (['cog'], ['ui'])


def test_internal_api_avoids_cog_and_ui_modules() -> None:
    """Runtime imports only: ``if TYPE_CHECKING:`` imports create no edge."""
    bad = {
        f'{rel(f)} -> {m}' for f in APP.glob('internal_api/**/*.py') for m in imports(f, runtime_only=True)
        if _forbidden_cog_module(m)
    }
    assert not bad, f'internal_api must not import a cog.py/ui.py/flat cog: {sorted(bad)}'


def test_importing_routers_loads_no_cog_or_ui_module() -> None:
    code = """
import pathlib, sys
import app.internal_api.routers
flat = {p.stem for p in pathlib.Path('app/cogs').glob('*.py') if p.stem != '__init__'}
bad = sorted(
    m for m in sys.modules
    if m.startswith('app.cogs.') and (m.endswith(('.cog', '.ui')) or m.startswith('app.cogs.misc.') or m.split('.')[2] in flat)
)
print(*bad, sep=chr(10))
sys.exit(1 if bad else 0)
"""
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60, cwd=APP.parent)
    assert r.returncode == 0, f'routers import loads cog/ui modules: {r.stdout} {r.stderr[-800:]}'


def test_lazy_cog_packages_resolve_on_first_use() -> None:
    code = """
import inspect, sys
import app.cogs.moderation.member_actions, app.cogs.notifications.decide
assert 'app.cogs.moderation.cog' not in sys.modules
assert 'app.cogs.notifications.cog' not in sys.modules
from app.cogs.moderation import FlaggedMember, Moderation, setup as moderation_setup
from app.cogs.notifications import Notifications, setup as notifications_setup
assert inspect.iscoroutinefunction(moderation_setup) and inspect.iscoroutinefunction(notifications_setup)
assert Moderation.__module__ == 'app.cogs.moderation.cog' and Notifications.__module__ == 'app.cogs.notifications.cog'
"""
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60, cwd=APP.parent)
    assert r.returncode == 0, r.stderr[-800:]


def test_clients_live_only_in_clients() -> None:
    bad = [
        rel(f) for f in APP.glob('**/*.py')
        if not rel(f).startswith('clients/') and any(
            isinstance(n, ast.ClassDef) and any('BaseHTTPClient' in ast.unparse(b) for b in n.bases)
            for n in ast.walk(ast.parse(f.read_text(encoding='utf-8')))
        )
    ]
    assert not bad, f'HTTP clients (BaseHTTPClient subclasses) belong in app/clients/: {bad}'


def test_clients_do_not_import_cogs_and_import_cleanly() -> None:
    mods = sorted(f.stem for f in APP.glob('clients/*.py') if f.stem != '__init__')
    bad = [rel(f) for f in APP.glob('clients/*.py') if any(m.startswith('app.cogs') for m in imports(f))]
    assert not bad, f'clients must not import app.cogs: {bad}'
    code = '; '.join(f'import app.clients.{m}' for m in mods)
    r = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60, cwd=APP.parent)
    assert r.returncode == 0, r.stderr[-800:]
