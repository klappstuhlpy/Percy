"""Dump the internal API's OpenAPI schema to ``dashboard/crates/percy-client/openapi.json``.

Builds the FastAPI app without a running bot (``_create_app`` only stores the bot on
``app.state``) and writes stable, key-sorted JSON so the committed file diffs cleanly.
``--check`` exits non-zero when the committed file is stale. See
``dashboard/crates/percy-client/GENERATION.md``.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / 'dashboard' / 'crates' / 'percy-client' / 'openapi.json'


def render() -> str:
    sys.path.insert(0, str(ROOT))
    from app.internal_api.server import _create_app

    schema = _create_app(None).openapi()  # type: ignore[arg-type]
    return json.dumps(schema, indent=2, sort_keys=True, ensure_ascii=False) + '\n'


def main() -> int:
    text = render()
    if '--check' in sys.argv:
        if not OUT.exists() or OUT.read_bytes().replace(b'\r\n', b'\n') != text.encode():
            print(f'{OUT.relative_to(ROOT)} is stale: run `poetry run python scripts/export_openapi.py`')
            return 1
        return 0
    OUT.write_bytes(text.encode())  # bytes: keep LF on Windows
    print(f'wrote {OUT.relative_to(ROOT)} ({len(text) // 1024} KiB)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
