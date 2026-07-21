<div align="center">

# Percy Dashboard

**The web dashboard for configuring [Percy](https://percy.klappstuhl.me)** — a standalone
[Axum](https://github.com/tokio-rs/axum) binary that runs on its own out of the box, and
optionally connects to [klappstuhl.me](https://github.com/klappstuhlpy/klappstuhl_me) for a shared login.

[![Rust](https://img.shields.io/badge/Rust-2021-CE422B?logo=rust&logoColor=white)](https://www.rust-lang.org/)
[![Axum](https://img.shields.io/badge/Axum-web-000000?logo=rust&logoColor=white)](https://github.com/tokio-rs/axum)
[![Pattern](https://img.shields.io/badge/pattern-BFF-6E56CF)](#overview)
[![License](https://img.shields.io/badge/License-AGPL--3.0-blue.svg)](https://www.gnu.org/licenses/agpl-3.0)

[Percy](https://percy.klappstuhl.me) · [Documentation](https://percy.klappstuhl.me/docs/) · [Main site](https://klappstuhl.me)

</div>

```
Browser ──▶ percy-dashboard (this app) ──▶ Percy internal API ──▶ PostgreSQL
              │  Discord OAuth login                 │  cache.invalidate()
              └─ dashboard.db (its own SQLite)
```

---

## Table of Contents

- [Overview](#overview)
- [Architecture at a glance](#architecture-at-a-glance)
- [Quick start (standalone)](#quick-start-standalone)
- [Configuration](#configuration)
- [Hosting: standalone vs. together](#hosting-standalone-vs-together)
- [Routes](#routes)
- [Development](#development)
- [Docker](#docker)
- [Deployment notes](#deployment-notes)
- [License](#license)

---

## Overview

- **BFF (backend-for-frontend).** Every configuration change is proxied to Percy's internal
  HTTP API, which owns the database and cache invalidation — the dashboard **never** touches
  Percy's PostgreSQL directly.
- **Shared foundation.** Its design system, signed-cookie crypto, and Percy API client come
  from the tag-pinned [`kls-core`](https://github.com/klappstuhlpy/kls-core)
  crates, so it stays visually and behaviourally consistent with the main site — without a common monolith.

---

## Architecture at a glance

| Concern            | How it works                                                                                                                                                                                                                                                                     |
|--------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Auth**           | Discord OAuth2 only. On callback the app mints a **stateless** session — an HMAC-signed cookie (`session`) carrying the Discord user id, verified with the config signing key. No session table, no password accounts.                                                           |
| **Access control** | "Can this Discord user manage this guild?" — computed from Percy's `get_user_guilds` (Manage-Server ⇒ full config; shares-a-guild ⇒ read-only member views). Acting user/moderator ids always come from the session, never the request body.                                     |
| **Own database**   | `dashboard.db` (SQLite via the shared `kls-web-core` kernel) holds only the two dashboard-owned tables: `user_discord_admin_guilds` (the "add Percy to your server" list) and `percy_leaderboard_vanity` (public-leaderboard vanity slugs). Migrations in `sql/` run on startup. |
| **Percy API**      | The one hard runtime dependency. All guild data + mutations flow through it (`percy-client`, paths under `/api/v1`). Optional at *boot* — the server still starts with no Percy configured; pages degrade gracefully.                                                            |
| **Design**         | `kls-ui` crate — `base.css`, theme bootstrap and relative-time JS, embedded and served at `/kls/*`. The dashboard's own page CSS/JS live in `static/` and are served at `/static/*`.                                                                                             |

---

## Quick start (standalone)

```bash
cargo run
# → writes a default config with a freshly generated signing key on first run, then
#   serves on http://127.0.0.1:8091
```

On first run with no config file the app writes one (with a generated `secret_key`) and logs where. Fill in the Discord OAuth credentials and Percy connection, then restart.

> **Working directory matters.** Static assets and templates are served relative to the current directory (`static/`, `templates/`). Run from the repo root, or ship those two directories next to the binary in a deployment.

---

## Configuration

Config is `config.json`, resolved from **`$DASHBOARD_CONFIG`**, else `<user-config-dir>/percy-dashboard/config.json` (e.g. `%APPDATA%\percy-dashboard\config.json` on Windows, `~/.config/percy-dashboard/config.json` on Linux).

```json
{
  "bind": "127.0.0.1:8091",
  "base_url": "https://percy.klappstuhl.me",
  "secret_key": "<generated on first run — hex HMAC key>",
  "discord": {
    "client_id": "…",
    "client_secret": "…",
    "redirect_uri": "https://percy.klappstuhl.me/auth/discord/callback"
  },
  "percy": {
    "api_url": "http://127.0.0.1:8090",
    "api_token": "<same as Percy's INTERNAL_API_TOKEN>",
    "bot_client_id": "…",
    "scan_token": "<klappstuhl.me API token, images:read scope — optional>"
  },
  "sso_secret": "<optional — same value as klappstuhl.me's sso_secret; enables SSO>"
}
```

| Field                                 | Meaning                                                                                                                                                                                |
|---------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `bind`                                | Listen address (default `127.0.0.1:8091`).                                                                                                                                             |
| `base_url`                            | Public URL the dashboard is served from. Used to derive the OAuth redirect when `redirect_uri` is blank. No trailing slash.                                                            |
| `secret_key`                          | HMAC key for the session + flash cookies. **Auto-generated on first run** — keep it secret and stable (rotating it logs everyone out).                                                 |
| `discord.client_id` / `client_secret` | Discord OAuth2 app credentials. Login is disabled until both are set.                                                                                                                  |
| `discord.redirect_uri`                | The OAuth2 redirect. Blank ⇒ derived as `{base_url}/auth/discord/callback`. Must **exactly match** a redirect registered on the Discord app.                                           |
| `percy.api_url` / `api_token`         | Percy's internal API base URL + its `INTERNAL_API_TOKEN`.                                                                                                                              |
| `percy.bot_client_id`                 | Bot application id for "add Percy" invite links. Falls back to `discord.client_id`.                                                                                                    |
| `percy.scan_token`                    | Optional klappstuhl.me API token (`images:read`) used to virus-scan uploaded poll banners / tag CSVs. Omit to skip scanning.                                                           |
| `sso_secret`                          | Optional shared HMAC key (hex). Set to the **same** value as klappstuhl.me's `sso_secret` to enable single sign-on (see below). Unset ⇒ only the dashboard's own Discord login works.  |

### Environment overrides

Every deployment-sensitive value can be injected via env (handy for containers), taking precedence over blank config fields:

| Env var                             | Overrides                                                                                             |
|-------------------------------------|-------------------------------------------------------------------------------------------------------|
| `DASHBOARD_CONFIG`                  | Path to `config.json`.                                                                                |
| `DASHBOARD_PORT`                    | The `bind` port.                                                                                      |
| `DASHBOARD_DB`                      | Path to `dashboard.db` (default `<data-dir>/percy-dashboard/dashboard.db`; `:memory:` for ephemeral). |
| `PERCY_API_URL` / `PERCY_API_TOKEN` | Percy connection.                                                                                     |
| `PERCY_SCAN_TOKEN`                  | The `images:read` scan token.                                                                         |
| `PERCY_SCAN_ENDPOINT`               | The scan endpoint (default `https://klappstuhl.me/api/v1/scan`).                                      |

### Discord app setup (one-time)

In the [Discord developer portal](https://discord.com/developers/applications) → your app → **OAuth2 → Redirects**, add the **exact** redirect URI the dashboard uses (e.g. `https://percy.klappstuhl.me/auth/discord/callback`). The dashboard requests only the `identify` scope; guild management is resolved through Percy, not the OAuth token.

---

## Hosting: standalone vs. together

**Independent by default, connected by choice.** Standalone, the dashboard only needs a reachable
Percy internal API — point `percy.api_url`/`api_token` at your instance, set up the Discord app, and
serve `base_url`. It doesn't need the klappstuhl.me main site; identity and guild access come from
Discord + Percy.

Run alongside klappstuhl.me on the same registrable domain and the two interoperate with **zero shared
database and no shared session store**, each deriving the other's URL from its own config:

- **Cross-navigation** — the main site links here via its `/percy` redirect; the dashboard links back via `base_url`.
- **Shared look-and-feel** — both pull `kls-ui` at the same pinned tag.
- **Optional single sign-on** — see below.

### Single sign-on

Implemented as a one-bounce **signed handoff** (the shared `kls_web_core::sso::Handoff` type), not a
shared cookie. Set the same `sso_secret` (a hex HMAC key) in both apps: the main site's `/percy` link
signs a short-lived `Handoff { discord_user_id, username, exp }` and 302s to the dashboard's
`GET /auth/handoff`, which verifies it and mints its own `session` cookie. A missing/expired token — or
no `sso_secret` — falls back to the dashboard's own Discord login.

---

## Routes

~100 routes across focused modules, all gated behind a `Session` and forwarded to Percy's internal API:

- **`/`, `/login`, `/logout`, `/auth/discord/callback`** — landing + Discord OAuth.
- **`/auth/handoff`** — accepts a signed SSO handoff from the main site (see [Single sign-on](#single-sign-on)).
- **`/dashboard`** — guild list (managed / public / "add Percy" cards).
- **`/dashboard/guild/:id/...`** — the full config surface: members, leveling, economy, autoresponders, comics, temp-channels, polls, giveaways, tags, commands, highlights, emoji-stats, audit log, music, AI, sentinel, moderation cases. Plus the JSON/mutation endpoints the page JS calls.
- **Public** — `/dashboard/guild/:id/overview`, `/lb/:target` (public leaderboard by guild id or vanity slug), commands catalogue, changelog, privacy/terms.
- **`/account/discord/avatar`** — live avatar via Percy.
- **`/health`, `/api-info`** — liveness + Percy-configured status.
- **`/kls/*`, `/static/*`** — shared + own assets.

Two flows require the optional scan token: **poll banner upload** and **tag CSV import** (they forward the file to klappstuhl.me's `/api/v1/scan`; if no token is set, scanning is skipped and the upload proceeds).

---

## Development

```bash
cargo build && cargo test     # build + run the test suite
cargo fmt && cargo clippy     # format (width 120) + lint (kept clean)
```

---

## Docker

The repo ships a multi-stage `Dockerfile` and a `docker-compose.yml` (host networking, so the container reaches Percy's internal API at `127.0.0.1:8090` and binds `127.0.0.1:8091`).

```bash
docker compose up --build -d        # build + start
# config + dashboard.db persist under ./data; on first run it writes a default
# config.json to ./data/config/percy-dashboard/ — fill in the Discord OAuth
# creds (and, for SSO, the shared sso_secret), then `docker compose restart`.
```
---

## Deployment notes

- Ship `static/` alongside the binary (it's served relative to the working directory; Askama templates and the SQL migrations are compiled into the binary). The Docker image already copies it in.
- Persist `dashboard.db` (or point `DASHBOARD_DB` at a persistent volume).
- Keep `secret_key` stable across restarts, or every session is invalidated.
- Reverse-proxy `percy.<your-domain>` → the dashboard's `bind` address; set `base_url` to the public URL.

---

## License

[AGPL-3.0](https://www.gnu.org/licenses/agpl-3.0) (per `Cargo.toml`).