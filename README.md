# percy-dashboard

The web dashboard for configuring **[Percy](https://percy.klappstuhl.me)** — a standalone [Axum](https://github.com/tokio-rs/axum) binary that runs **on its own out of the box** and, optionally, connects to the [klappstuhl.me](https://github.com/klappstuhlpy/klappstuhl_me) main site for a shared login.

It is a **BFF** (backend-for-frontend): every configuration change is proxied to Percy's internal HTTP API, which owns the database and cache invalidation. The dashboard never touches Percy's PostgreSQL directly.

It shares its design system, signed-cookie crypto, and Percy API client with the main site through the [`klappstuhl_me-shared`](https://github.com/klappstuhlpy/klappstuhl_me-shared) crates (a tag-pinned git dependency), so the two apps stay visually and behaviourally consistent without a common monolith.

```
Browser ──▶ percy-dashboard (this app) ──▶ Percy internal API ──▶ PostgreSQL
              │  Discord OAuth login                 │  cache.invalidate()
              └─ dashboard.db (its own SQLite)
```

---

## Architecture at a glance

| Concern | How it works |
|---|---|
| **Auth** | Discord OAuth2 only. On callback the app mints a **stateless** session — an HMAC-signed cookie (`session`) carrying the Discord user id, verified with the config signing key. No session table, no password accounts. |
| **Access control** | "Can this Discord user manage this guild?" — computed from Percy's `get_user_guilds` (Manage-Server ⇒ full config; shares-a-guild ⇒ read-only member views). Acting user/moderator ids always come from the session, never the request body. |
| **Own database** | `dashboard.db` (SQLite via the shared `kls-web-core` kernel) holds only the two dashboard-owned tables: `user_discord_admin_guilds` (the "add Percy to your server" list) and `percy_leaderboard_vanity` (public-leaderboard vanity slugs). Migrations in `sql/` run on startup. |
| **Percy API** | The one hard runtime dependency. All guild data + mutations flow through it (`percy-client`, paths under `/api/v1`). Optional at *boot* — the server still starts with no Percy configured; pages degrade gracefully. |
| **Design** | `kls-ui` crate — `base.css`, theme bootstrap and relative-time JS, embedded and served at `/kls/*`. The dashboard's own page CSS/JS live in `static/` and are served at `/static/*`. |

---

## Quick start (standalone)

```bash
# 1. Build (needs SSH access to the private klappstuhl_me-shared repo — see Development)
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

| Field | Meaning |
|---|---|
| `bind` | Listen address (default `127.0.0.1:8091`). |
| `base_url` | Public URL the dashboard is served from. Used to derive the OAuth redirect when `redirect_uri` is blank. No trailing slash. |
| `secret_key` | HMAC key for the session + flash cookies. **Auto-generated on first run** — keep it secret and stable (rotating it logs everyone out). |
| `discord.client_id` / `client_secret` | Discord OAuth2 app credentials. Login is disabled until both are set. |
| `discord.redirect_uri` | The OAuth2 redirect. Blank ⇒ derived as `{base_url}/auth/discord/callback`. Must **exactly match** a redirect registered on the Discord app. |
| `percy.api_url` / `api_token` | Percy's internal API base URL + its `INTERNAL_API_TOKEN`. |
| `percy.bot_client_id` | Bot application id for "add Percy" invite links. Falls back to `discord.client_id`. |
| `percy.scan_token` | Optional klappstuhl.me API token (`images:read`) used to virus-scan uploaded poll banners / tag CSVs. Omit to skip scanning. |
| `sso_secret` | Optional shared HMAC key (hex). Set to the **same** value as klappstuhl.me's `sso_secret` to enable single sign-on (see below). Unset ⇒ only the dashboard's own Discord login works. |

### Environment overrides

Every deployment-sensitive value can be injected via env (handy for containers), taking precedence over blank config fields:

| Env var | Overrides |
|---|---|
| `DASHBOARD_CONFIG` | Path to `config.json`. |
| `DASHBOARD_PORT` | The `bind` port. |
| `DASHBOARD_DB` | Path to `dashboard.db` (default `<data-dir>/percy-dashboard/dashboard.db`; `:memory:` for ephemeral). |
| `PERCY_API_URL` / `PERCY_API_TOKEN` | Percy connection. |
| `PERCY_SCAN_TOKEN` | The `images:read` scan token. |
| `PERCY_SCAN_ENDPOINT` | The scan endpoint (default `https://klappstuhl.me/api/v1/scan`). |

### Discord app setup (one-time)

In the [Discord developer portal](https://discord.com/developers/applications) → your app → **OAuth2 → Redirects**, add the **exact** redirect URI the dashboard uses (e.g. `https://percy.klappstuhl.me/auth/discord/callback`). The dashboard requests only the `identify` scope; guild management is resolved through Percy, not the OAuth token.

---

## Hosting: standalone vs. together

The dashboard is designed to run **independently by default, connected by choice.**

### Standalone

The only thing it truly needs is a reachable **Percy internal API**. Point `percy.api_url`/`api_token` at your Percy instance, set up the Discord app, and serve `base_url`. It does not need the klappstuhl.me main site at all. Login is Discord OAuth; identity and guild access come entirely from Discord + Percy.

### Together with klappstuhl.me (connected mode)

When both apps run on the same registrable domain (`klappstuhl.me` + `percy.klappstuhl.me`) they interoperate with **zero shared database and no shared live session store**:

- **Cross-navigation** — the main site links to the dashboard via its `/percy` redirect (URL from *its* config, not hardcoded here); the dashboard can link back using `base_url`.
- **Shared look-and-feel** — both pull `kls-ui` at the same pinned tag, so a design change propagates to both on a version bump.
- **Optional single sign-on** — see below.

Nothing about connected mode is hardcoded: each app derives the other's URL from its own config, and the only shared runtime secret (if you enable SSO) lives in config.

### Single sign-on: what's possible

**Short answer:** a seamless "log in on klappstuhl.me → already logged in on percy.klappstuhl.me" is achievable with a small, dynamic **signed handoff** — *not* by literally sharing one cookie.

Why not one shared cookie? The two apps have deliberately different identity models:

| | klappstuhl.me | percy-dashboard |
|---|---|---|
| Cookie | `token` (DB-backed) | `session` (stateless) |
| Keyed on | `account.id` (i64) | `discord_user_id` |
| Signed with | its own `secret_key` | its own `secret_key` |

A cookie set by one is meaningless to the other (different name, payload, and key), and the main site's account id doesn't identify a Discord user without a lookup. Setting a `Domain=.klappstuhl.me` cookie makes the *browser* send it to both hosts, but the receiving app still can't verify or interpret it. So a single transparent cookie is out of reach without collapsing the two identity models — not worth it.

**The clean, dynamic mechanism** (this is **implemented**) is a one-bounce signed handoff, using the shared `kls_web_core::sso::Handoff` type — HMAC-signed with a key both apps hold:

1. Both apps are configured with **one shared `sso_secret`** (a hex HMAC key) — the only shared secret, and the only extra config.
2. On the main site, the **`/percy` link** — for a user who is logged in there *and* has a linked Discord — signs a short-lived `Handoff { discord_user_id, username, exp: now+60s }` and 302s to `<percy_url>/auth/handoff?t=…` (the percy URL comes from the main site's own config).
3. The dashboard's **`GET /auth/handoff`** verifies the token against `sso_secret`, checks freshness, and mints its own `session` cookie, then lands on `/dashboard`.
4. Any missing/tampered/expired token — or no `sso_secret` — falls straight back to the dashboard's own Discord login. Nothing breaks when SSO is off.

One click, no shared database, no shared live session store, and every URL comes from config — nothing hardcoded. It bridges the two identity models explicitly (main-site account → its linked `discord_user_id`) instead of trying to make one cookie mean two things.

> **Enabling it:** set the same `sso_secret` in both `config.json` files. **Note:** a user who signs in via the handoff (rather than a full Discord OAuth login) won't have the "add Percy to servers Percy isn't in yet" cards populated — those need the Discord guild list from a full login. The guild list itself (servers Percy shares with them) works either way.

---

## Routes

~100 routes across focused modules, all gated behind a `Session` and forwarded to Percy's internal API:

- **`/`, `/login`, `/logout`, `/auth/discord/callback`** — landing + Discord OAuth.
- **`/auth/handoff`** — accepts a signed SSO handoff from the main site (see below).
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

- **Private dependency.** The `klappstuhl_me-shared` crates are pulled over SSH; you need an SSH key with read access to that repo. `.cargo/config.toml` sets `net.git-fetch-with-cli = true` so SSH auth is reliable (especially on Windows). CI/Docker must forward an SSH key (`--mount=type=ssh`).
- **Pinned versions.** The shared crates are pinned by tag (currently `v0.1.3`); bump in lock-step with klappstuhl.me when cutting a new shared release. `Cargo.lock` is committed.
- **Data cutover.** Migrating the two owned tables from the old monolith DB is a separate, gated step — see `docs/DATA_IMPORT_RUNBOOK.md`.

---

## Docker

The repo ships a multi-stage `Dockerfile` and a `docker-compose.yml` (host networking, so the container reaches Percy's internal API at `127.0.0.1:8090` and binds `127.0.0.1:8091`).

```bash
docker compose up --build -d        # build + start
# config + dashboard.db persist under ./data; on first run it writes a default
# config.json to ./data/config/percy-dashboard/ — fill in the Discord OAuth
# creds (and, for SSO, the shared sso_secret), then `docker compose restart`.
```

#### Build prerequisite — SSH access to the private shared crate

The image build fetches the private [`klappstuhl_me-shared`](https://github.com/klappstuhlpy/klappstuhl_me-shared) dependency over SSH, so the build needs an SSH key with **read access to that repo** forwarded in. BuildKit forwards an **ssh-agent socket** — a key sitting in `~/.ssh` is not enough on its own; without an agent you get `invalid empty ssh agent socket: make sure SSH_AUTH_SOCK is set`.

```bash
# Confirm your key can reach the private repo (uses your normal ~/.ssh key):
git ls-remote git@github.com:klappstuhlpy/klappstuhl_me-shared.git >/dev/null && echo OK

# Option A — load the key into an agent, then build in the SAME shell:
eval "$(ssh-agent -s)"
ssh-add ~/.ssh/id_ed25519           # your key with read access to klappstuhl_me-shared
docker compose up --build -d

# Option B — no agent; hand BuildKit the key file directly:
docker compose build --ssh default=$HOME/.ssh/id_ed25519
docker compose up -d
```

Building with `sudo`? Use `sudo -E …` so `SSH_AUTH_SOCK` survives, or run as a `docker`-group user. A passphrase-protected key must use Option A. Front the dashboard with a reverse proxy for the `percy.<domain>` subdomain (klappstuhl.me's own Proxy admin page can map it to `127.0.0.1:8091`).

---

## Deployment notes

- Ship `static/` alongside the binary (it's served relative to the working directory; Askama templates and the SQL migrations are compiled into the binary). The Docker image already copies it in.
- Persist `dashboard.db` (or point `DASHBOARD_DB` at a persistent volume).
- Keep `secret_key` stable across restarts, or every session is invalidated.
- Reverse-proxy `percy.<your-domain>` → the dashboard's `bind` address; set `base_url` to the public URL.
