"""Contract test: every (method, path) the Rust `percy-client` calls must exist in Percy's OpenAPI schema.

The schema is built from the real routers (`_create_app` only stores the bot on `app.state`), so no bot,
database or network is needed. Client calls are scraped from `lib.rs`: each request is
`.<method>(self.url(["&format!("]"/api/v1/..."...)))`.
"""

from __future__ import annotations

import re
import warnings
from pathlib import Path
from unittest.mock import MagicMock

from app.internal_api.server import _create_app

CLIENT = Path(__file__).resolve().parent.parent / 'dashboard' / 'crates' / 'percy-client' / 'src' / 'lib.rs'

# .get(self.url(&format!("/api/v1/x/{}", ...)))  or  .get(self.url("/api/v1/x"))  (whitespace/newlines tolerated)
CALL_RE = re.compile(
    r'\.(get|post|put|patch|delete)\(\s*self\s*\.url\(\s*(?:&format!\(\s*)?"([^"]+)"',
)

# (METHOD, path) calls the scraper cannot see. Keep tiny; one reason each.
ALLOWLIST: set[tuple[str, str]] = {
    # Hidden legacy alias (include_in_schema=False in routers/guild.py) the client still targets on purpose.
    ('PATCH', '/api/v1/guilds/{}/config'),
    # KNOWN DRIFT: the AutoMod feature (and its router) was removed from the bot; the client methods are dead.
    # Delete these two and the client's automod methods, then drop the entries.
    ('GET', '/api/v1/guilds/{}/automod'),
    ('PUT', '/api/v1/guilds/{}/automod/{}'),
}


def _norm(path: str) -> str:
    """Rust `{}` / OpenAPI `{name}` -> `{}`; query strings and trailing slashes dropped."""
    path = path.split('?', 1)[0].rstrip('/') or '/'
    return re.sub(r'\{[^}]*\}', '{}', path)


def _client_calls() -> set[tuple[str, str]]:
    return {(m.upper(), _norm(p)) for m, p in CALL_RE.findall(CLIENT.read_text(encoding='utf-8'))}


def _api_routes() -> set[tuple[str, str]]:
    schema = _create_app(MagicMock()).openapi()
    return {
        (method.upper(), _norm(path))
        for path, ops in schema['paths'].items()
        for method in ops
        if method in {'get', 'post', 'put', 'patch', 'delete'}
    }


def test_client_calls_exist_in_api() -> None:
    calls, routes = _client_calls(), _api_routes()
    assert calls, 'scraper found no client calls; the lib.rs request pattern changed'

    missing = sorted(calls - routes - ALLOWLIST)
    uncalled = sorted(routes - calls)
    print(f'\nclient calls={len(calls)} api routes={len(routes)} missing={len(missing)} uncalled={len(uncalled)}')
    if uncalled:
        warnings.warn('API routes the client never calls:\n  ' + '\n  '.join(f'{m} {p}' for m, p in uncalled), stacklevel=1)
    assert not missing, 'percy-client calls routes the API does not have:\n  ' + '\n  '.join(
        f'{m} {p}' for m, p in missing
    )
