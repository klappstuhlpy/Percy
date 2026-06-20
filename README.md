<div align="center">

# Percy v2

**A feature-rich, multipurpose Discord bot built with Python 3.12+ and [discord.py](https://github.com/Rapptz/discord.py).**

Moderation · Auto-moderation · Economy · Casino games · Leveling · Music · Polls · Giveaways · Tags · Reminders · Documentation search · and much more — all in a single, self-hostable package.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7+-5865F2.svg)](https://github.com/Rapptz/discord.py)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg)](LICENSE)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)

[Add Percy to your server](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands) ·
[Website](https://klappstuhl.me/percy/dashboard/) ·
[Support server](https://discord.gg/3jSYQ9VNbA)

</div>

> **Prefer not to self-host?** Just [invite the hosted instance](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands) and skip straight to the [Configuration](#configuration) section.

---

## Table of Contents

- [Highlights](#highlights)
- [Features](#features)
  - [Moderation & Safety](#moderation--safety)
  - [Economy & Casino Games](#economy--casino-games)
  - [Leveling](#leveling)
  - [Music](#music)
  - [Community & Engagement](#community--engagement)
  - [Utility & Productivity](#utility--productivity)
  - [Developer & Information](#developer--information)
- [Commands](#commands)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
  - [Environment variables (`.env`)](#environment-variables-env)
  - [Static configuration (`config.py`)](#static-configuration-configpy)
  - [Per-guild configuration](#per-guild-configuration)
- [Database management](#database-management)
- [Running the bot](#running-the-bot)
- [Docker](#docker)
- [Development](#development)
- [License](#license)

---

## Highlights

- **Hybrid commands everywhere** — almost every command works as both a slash command (`/ban`) and a prefix command (`?ban`), powered by a custom command framework on top of discord.py.
- **Rich, helpful errors** — invalid input is answered with an ANSI-coloured "here's where your command broke" trace that points at the exact offending argument.
- **Per-guild everything** — prefixes, automod, audit logging, leveling, polls and music panels are all configured per server and cached in memory for speed.
- **Layered, testable architecture** — a repository data-access layer, a Discord-free **service layer** for business logic, MVVM-style UI separation in the cogs, and pure game **engines** that are unit-tested in isolation.
- **Resilient external APIs** — every third-party client (AniList, …) shares one HTTP base with 429 handling, exponential backoff and a circuit breaker.
- **Server-side image rendering** — rank cards, casino cards, poker odds charts, presence charts, captchas and music panels are all drawn with Pillow behind a single `RenderingService`.
- **Components V2 UI** — newer features (translation, AI assistant, autoresponder/stat-counter lists, lottery results) render with Discord's Components V2 layouts via a shared `app.core` helper.

---

## Features

### Moderation & Safety

| Feature                  | What it does                                                                                                                                                                    |
|--------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Core moderation**      | `kick`, `ban`, `multiban`, `softban`, `unban`, `mute`/`unmute`, `tempban`, `tempmute`, `selfmute`, `purge` (with rich filters), `slowmode`.                                     |
| **Mute role management** | Create, bind, sync and unbind a mute role; permission overwrites are applied across channels automatically (and kept in sync as channels are created).                          |
| **Lockdowns**            | Lock down individual channels or the whole server (`lockdown` group), with automatic, timed un-locking via the timer system and lockout protection for the bot.                 |
| **Auto-moderation**      | Configurable automod rules linked to Discord's native AutoMod, mention-spam detection, and **raid protection** that auto-bans spammers.                                         |
| **Sentinel**           | A captcha verification system: new members must solve a generated image captcha before they can participate; supports auto-trigger rate limits and `ban`/`kick` bypass actions. |
| **Anti-spam**            | A global `SpamChecker` that throttles command abuse, flags mention spam, and detects rapid-join raids.                                                                          |
| **Audit logging**        | Broadcast a configurable subset of server audit-log events to a channel/webhook.                                                                                                |

> Temporary bans, mutes and lockdowns are all backed by the persistent **timer system**, so they survive restarts and fire exactly when due.

### Economy & Casino Games

| Feature                     | What it does                                                                                                                                                                                             |
|-----------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Wallet & banking**        | Each member has a `cash`/`bank` wallet scoped to the guild: `balance`, `deposit`, `withdraw`, `transfer`, and a per-guild `leaderboard`.                                                                 |
| **Earning & stealing**      | Risk/reward income commands — `work`, `crime`, `slut` — and `rob` to steal from other members.                                                                                                           |
| **Texas Hold'em Poker**     | Full multi-player (2–4) poker tables with blinds, side-pots, all-in handling, an interactive button UI, an autoplay timer for AFK players, and Monte-Carlo **win-odds analysis** rendered as bar charts. |
| **Blackjack**               | Play against the dealer with the standard hit/stand/double flow.                                                                                                                                         |
| **Roulette, Slots & Tower** | Classic casino gambling games with rendered results.                                                                                                                                                     |
| **Mini-games**              | Tic-Tac-Toe, Minesweeper and Hangman.                                                                                                                                                                    |
| **Earning activities**      | `daily` (with streak bonus), plus `fish` and `hunt` — cooldown-gated, weighted risk/reward loot tables from junk to rare jackpots.                                                                       |
| **Shop & items**            | Admins stock a per-guild shop (`shop add/remove`); members `buy`, `sell` and browse their `inventory`. Items can carry a **use-effect**: cash vouchers, lootboxes, role grants, or timed XP/loot boosts. |
| **Item effects**            | `use` consumes an item and applies its effect — boosts run on a timer (extendable by re-using) and multiply leveling XP (message + voice) or `fish`/`hunt` payouts. Item commands autocomplete.          |
| **Server lottery**          | Admins start a timed `lottery`; members buy weighted tickets, the pot grows, and a winner is drawn automatically via the persistent timer system (announced with a Components V2 card).                  |

### Leveling

| Feature                  | What it does                                                                                                                                                                              |
|--------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **XP & ranks**           | Earn XP per message (with cooldowns and configurable gain), level up, and view a **rendered rank card** (`/level`, member optional). Active boost badges (XP/Loot) are shown on the card. |
| **Leaderboard**          | Per-guild Top-10 board (`/level leaderboard`).                                                                                                                                            |
| **Level roles**          | Award roles at configured levels, with optional **role stacking**, managed through an interactive view (`/level config roles`).                                                           |
| **Multipliers**          | Per-role and per-channel XP multipliers (`/level config multiplier`).                                                                                                                     |
| **Voice XP**             | Opt-in XP for time spent active in voice (`/level config voice`); skips members who are alone, AFK or deafened, and honours the same blacklists.                                          |
| **Fine-grained control** | Toggle leveling, set the level-up message and channel (or DM), blacklist roles/channels/users, and optionally delete a member's data when they leave.                                     |

### Music

| Feature              | What it does                                                                                                                                                  |
|----------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Lavalink player**  | High-quality audio playback backed by a [Lavalink](https://github.com/lavalink-devs/Lavalink) node ([wavelink](https://github.com/PythonistaGuild/Wavelink)). Sources: Spotify, Apple Music, SoundCloud (Amazon Music is unsupported). |
| **Queue & controls** | Play, pause, skip, seek, loop, shuffle, an equalizer and a full queue.                                                                                        |
| **24/7 always-on**   | `/music 247` keeps the bot permanently connected to a voice channel playing a radio/stream URL, a looping playlist (Spotify/Apple/SoundCloud links work directly), or endless autoplay. Ships curated radio presets (`/music radios`, e.g. `lofi`, `chill`, `antenne`) so no stream URL is needed. Sessions persist to PostgreSQL and auto-reconnect/resume after disconnects or restarts. |
| **Resilience**       | Lavalink session resume + self-healing recovery: stuck/failed tracks are skipped instead of dropping the player, so playback doesn't cut off.                  |
| **Lyrics**           | Fetch song lyrics (Genius API) for the current track.                                                                                                         |
| **Playlists**        | Save, load and manage personal playlists (part of `Music` cog), persisted in PostgreSQL.                                                                          |
| **Music panel**      | An optional persistent now-playing control panel pinned in a configured channel.                                                                              |

### Community & Engagement

| Feature            | What it does                                                                                                                                                              |
|--------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Polls**          | Rich polls with up to 8 options, animated vote bars, optional vote reasons, role-ping opt-in, threads, scheduling and live odds. Search, edit and inspect existing polls. |
| **Giveaways**      | Create and manage giveaways through modals; entries via a persistent button, automatic winner draw and reroll.                                                            |
| **Tags**           | Per-guild custom tags with aliases, fuzzy search, ownership transfer and usage stats.                                                                                     |
| **Highlights**     | Get a DM when a word or phrase you subscribed to is mentioned.                                                                                                            |
| **Autoresponders** | Canned replies that fire when a message matches a trigger (`contains`/`exact`/`startswith`/`regex`), with placeholders like `{user}` and `{count}` (`autoresponder …`).   |
| **Gimmicks**       | Fun/flavour annotation commands.                                                                                                                                          |

### Utility & Productivity

| Feature                      | What it does                                                                                                                                    |
|------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------|
| **Reminders**                | `remind me in 2h to …` style reminders with natural-language time parsing; backed by the timer system.                                          |
| **Notes**                    | Personal, user-installable notes (works in DMs and any server via a user-install app command).                                                  |
| **Temporary voice channels** | "Join-to-create" hub channels that spin up a personal voice channel on join and clean up when empty.                                            |
| **Emoji management**         | Add, steal, rename and inspect server emojis, with per-guild emoji usage stats.                                                                 |
| **User & server info**       | Profile, avatar, `serverinfo`, `userinfo`, timezone settings, and per-user settings.                                                            |
| **History tracking**         | Username/nickname history (`names`), `lastseen`, avatar history, and a rendered **presence chart**.                                             |
| **Translation**              | `translate` text into any language (ISO code or name), with the source auto-detected; keyless backend.                                          |
| **Stat counters**            | Self-updating voice channels that display a live server statistic — members, humans, bots, online, boosts, roles or channels (`statcounter …`). |

### Developer & Information

| Feature                  | What it does                                                                                                                                                                  |
|--------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Documentation search** | Query and render docs (e.g. `docs`/`rtfm`/`rtfd`) for libraries from intersphinx inventories, with a local cache.                                                             |
| **Snekbox**              | Safely evaluate arbitrary Python in a sandboxed [Snekbox](https://github.com/python-discord/snekbox) container (run via Docker, see [Docker](#docker)).                       |
| **AniList**              | Search anime & manga, with OAuth-linked account features.                                                                                                                     |
| **Comics**               | Subscribe to weekly comic releases (Marvel/DC, via a self-hosted League of Comic Geeks API wrapper).                                                                          |
| **AI assistant**         | `ask` the bot a question, answered by a fast open model via [Groq](https://groq.com/); supports follow-ups by replying to its answers. Disabled unless `GROQ_API_KEY` is set. |
| **Discord status feed**  | Relay Discord's own status-page incidents to a channel.                                                                                                                       |
| **Bot stats & meta**     | Uptime, latency, command stats, source links, invite/about, and help. Owner tooling (`admin`) covers sync, hot-reload, an SQL console and task introspection.                 |
| **Bot-list stats**       | Auto-posts the server count to discord.bots.gg, top.gg and discordbotlist.com when those tokens are configured.                                                               |
| **Vote rewards**         | Voting on top.gg or discordbotlist.com grants a renewable **+10% XP boost for 12 hours**, applied in every shared server. Webhooks land on `/api/webhooks/{topgg,discordbotlist}`; `?vote` shows links and live boost status. |

---

## Commands

Percy ships with **31 feature modules (cogs)**. Most commands are **hybrid** — available as both slash and prefix commands. The default prefix is `?` (configurable per guild), and the bot also responds to a mention.

Use the built-in help to explore everything interactively:

```text
/help              → paginated overview of every category
/help <command>    → detailed help, usage and examples for one command
?help <category>   → list all commands in a category
```

A few representative command groups:

| Group         | Examples                                                                                                                                                |
|---------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
| Moderation    | `kick`, `ban`, `multiban`, `softban`, `mute`, `tempban`, `purge`, `slowmode`, `lockdown start/end`, `moderation …`                                      |
| Configuration | `config …` (per-guild settings), `automod …`, audit-log setup, sentinel setup                                                                         |
| Leveling      | `level` (rank card), `level leaderboard`, `level set`, `level config …`                                                                                 |
| Economy       | `balance`, `deposit`, `withdraw`, `transfer`, `leaderboard`, `daily`, `fish`, `hunt`, `shop …`, `buy`, `sell`, `inventory`, `use`, `perks`, `lottery …` |
| Games         | `poker`, `blackjack`, `roulette`, `slots`, `tower`, `tictactoe`, `minesweeper`, `hangman`                                                               |
| Polls         | `polls create/end/edit/delete/search/history/config`                                                                                                    |
| Music         | `play`, `pause`, `skip`, `queue`, `loop`, `lyrics`, playlist tools                                                                                      |
| Utility       | `remind`, `notes …`, `tag …`, `highlight …`, `tempchannels …`, `emoji …`, `timezone …`, `translate`, `statcounter …`, `autoresponder …`                 |
| Info          | `userinfo`, `serverinfo`, `avatar`, `names`, `lastseen`, `presence`                                                                                     |
| Developer     | `docs`/`rtfm`, snekbox eval, `anilist …`, `comic …`, `ask` (AI assistant)                                                                               |

---

## Prerequisites

- **Python** ≥ 3.12 — [Download](https://www.python.org/downloads/)
- **PostgreSQL** ≥ 14 — [Download](https://www.postgresql.org/download/) (the `pg_trgm` extension is required)
- **Poetry** — [Install](https://python-poetry.org/docs/)
- **Lavalink** server — required for music ([Releases](https://github.com/lavalink-devs/Lavalink/releases))
- **Docker** (optional) — for the Snekbox Python sandbox (see [Docker](#docker))

---

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/klappstuhlpy/Percy-v2.git
cd Percy-v2
```

### 2. Install dependencies

```bash
poetry install
```

### 3. Set up PostgreSQL

Launch the PostgreSQL CLI (`psql`) and run:

```sql
CREATE ROLE percy WITH LOGIN PASSWORD 'your_password';
CREATE DATABASE percy OWNER percy;
CREATE EXTENSION pg_trgm;
```

> The database name (`percy`), user (`percy`) and port (`5432`) are defined in `config.py` under `DatabaseConfig`. Only the **password** and **host** come from the environment (see below). Change `DatabaseConfig` if you use different values.

### 4. Configure your environment

Create a `.env` file in the project root (see the [full template below](#environment-variables-env)) and an entry in `config.py` for your own IDs (see [Static configuration](#static-configuration-configpy)).

### 5. Initialize the database

```bash
poetry run python main.py db init
```

### 6. Run the bot

```bash
poetry run python main.py
```

---

## Configuration

Percy is configured in three layers: **secrets** in `.env`, **deployment constants** in `config.py`, and **runtime, per-guild settings** via the `/config` command.

### Environment variables (`.env`)

Create a `.env` file in the project root. **Never commit this file** (it is git-ignored).

```env
# ── Discord ──────────────────────────────────────────────
# Production token (used when running on Linux).
DISCORD_TOKEN=
# Beta token (used automatically on non-Linux systems / local dev — see "Beta mode").
DISCORD_BETA_TOKEN=
# OAuth2 client secret (needed for OAuth flows, e.g. AniList linking / web features).
DISCORD_CLIENT_SECRET=

# ── Database ─────────────────────────────────────────────
DATABASE_PASSWORD=your_password      # required
DATABASE_HOST=localhost              # required
DATABASE_POOL_MIN_SIZE=10            # optional; warm connections kept open (default 10)
DATABASE_POOL_MAX_SIZE=20            # optional; pool ceiling under load (default 20)
DATABASE_COMMAND_TIMEOUT=300         # optional; per-query timeout in seconds (default 300)
DATABASE_POOL_MAX_IDLE=300           # optional; recycle idle connections after N seconds (default 300)

# ── Lavalink (music) ─────────────────────────────────────
LAVALINK_NODE_1_PASSWORD=            # required for music playback

# ── Optional integrations ────────────────────────────────
ANILIST_CLIENT_ID=                   # AniList OAuth client (anime/manga linking); disabled if blank
ANILIST_CLIENT_SECRET=
STATS_WEBHOOK_TOKEN=                 # webhook token for posting bot stats/errors
GENIUS_TOKEN=                        # Genius API (music lyrics)
GROQ_API_KEY=                        # Groq API key (AI assistant — /ask); disabled if blank
GROQ_MODEL=                          # optional Groq model override (default: llama-3.3-70b-versatile)
GITHUB_TOKEN=                        # GitHub API (source links, gists)
DBOTS_TOKEN=                         # discord.bots.gg stats posting
TOPGG_TOKEN=                         # top.gg stats posting
DISCORDBOTLIST_TOKEN=                # discordbotlist.com stats posting
TOPGG_WEBHOOK_SECRET=                # top.gg vote-webhook Authorization secret (POST /api/webhooks/topgg)
DISCORDBOTLIST_WEBHOOK_SECRET=       # discordbotlist.com vote-webhook Authorization secret (POST /api/webhooks/discordbotlist)
IMAGES_API_TOKEN=                    # image API integrations
LOCG_API_URL=                        # self-hosted League of Comic Geeks API wrapper (locg-api; comic subscriptions). Docker: http://host.docker.internal:8070 (host-gateway); bare metal: http://127.0.0.1:8070
GROQ_API_KEY=                        # Groq API Token for /ai command

# ── Web Dashboard (klappstuhl.me BFF) ───────────────────
INTERNAL_API_TOKEN=                  # pre-shared bearer token for the dashboard BFF
INTERNAL_API_PORT=8090               # port for internal API (default 8090)
```

### Internal API (Web Dashboard)

When `INTERNAL_API_TOKEN` is set, Percy starts an internal aiohttp server (default `127.0.0.1:8090`) exposing guild data to the klappstuhl.me web dashboard. The dashboard proxies user actions through this API so all mutations go through Percy's repository layer and cache invalidation.

> **Endpoints:** For reference, look at /app/internal_api/base.py.

All requests require `Authorization: Bearer <INTERNAL_API_TOKEN>`. The API is disabled (cog is a no-op) when the token is unset.

The `InternalAPI` cog lives in the `app/internal_api/` package: `base.py` owns the aiohttp server lifecycle and the full route table, `auth.py` the bearer-token middleware, and the handlers are grouped into domain mixins (`guild.py`, `members.py`, `leveling.py`, `economy.py`, `content.py`, `stats.py`, `moderation.py`, `music.py`) that compose into the cog. The leveling config endpoint accepts the full set of fields (`enabled`, `voice_enabled`, `role_stack`, `delete_after_leave`, `factor`, `base`, `min_gain`, `max_gain`, `cooldown_per`, `level_up_channel`, `level_up_message`, `special_level_up_messages`), and the `leveling/roles/preset` endpoint creates 12 themed milestone roles (Newcomer → Immortal) with colors, idempotent by role name. The `xp-history` endpoint returns daily cumulative-XP snapshots for the trend chart, and the `members/{uid}/detail` endpoint aggregates identity, leveling rank, moderation cases, and notes into a single profile response.

> **Minimum to boot:** a Discord token (`DISCORD_BETA_TOKEN` on Windows/macOS, `DISCORD_TOKEN` on Linux), `DATABASE_PASSWORD`, and `DATABASE_HOST`. Everything else — including `ANILIST_CLIENT_ID` — gracefully disables the corresponding integration if left blank.

### Static configuration (`config.py`)

`config.py` holds non-secret deployment constants. If you self-host, review and change at least:

| Setting                           | Meaning                                                                                                                                 |
|-----------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------|
| `owners`                          | Your Discord user ID(s) — grants owner-only commands.                                                                                   |
| `default_prefix`                  | The default text-command prefix (`?`).                                                                                                  |
| `test_guild_id` / `main_guild_id` | Guilds used for fast slash-command syncing / owner tooling.                                                                             |
| `lavalink_nodes`                  | Your Lavalink node URI(s); the password comes from `.env`.                                                                              |
| `stats_webhook`                   | `(webhook_id, token)` for stats/error reporting.                                                                                        |
| `DatabaseConfig`                  | DB name/user/port (password & host come from `.env`).                                                                                   |
| `Emojis`                          | Custom emoji IDs — these reference emojis on the developer's servers; replace them with your own if self-hosting for correct rendering. |

### Per-guild configuration

Once the bot is in your server, use the `/config` slash command (or `?config`) — and the dedicated `automod`, audit-log and sentinel setup commands — to configure, per guild:

- Custom command prefixes
- Audit-log channel/webhook and which events to broadcast
- Automod rules, raid protection and alert webhooks
- The captcha **sentinel** (role, channel, bypass action, auto-trigger rate)
- Poll channel, poll-reason channel and ping role
- Music panel channel
- Leveling (via `/level config …`)

All of this is stored in PostgreSQL and cached in memory, with the cache invalidated automatically on every change.

---

## Database management

Percy uses **forward-only versioned SQL migrations** in `migrations/` (`V1__….sql`, `V2__….sql`, …). Applied state is tracked in the `schema_migrations` table in the database itself (version, description, checksum, applied_at) rather than a JSON file. All commands run against the database configured in `.env`/`config.py`.

| Command                                 | Description                                                          |
|-----------------------------------------|---------------------------------------------------------------------|
| `python main.py db init`                | Create the tracking table (backfilling legacy state) + apply all pending. |
| `python main.py db upgrade`             | Apply any pending migrations.                                       |
| `python main.py db upgrade -t <N>`      | Apply pending migrations up to and including version N.             |
| `python main.py db upgrade --sql`       | Print the pending SQL instead of executing it (also `--dry-run`).   |
| `python main.py db migrate -r "reason"` | Create a new, blank migration file (next version) to edit.          |
| `python main.py db status`              | Show current version, pending migrations and any integrity problems.|
| `python main.py db history`             | List applied migrations with apply times, then pending (`--reverse` for oldest-first). |
| `python main.py db verify`              | Validate files and detect drift; exits non-zero on problems.        |

> Always create schema changes via a **new** `migrations/V<N>__name.sql` file (`db migrate`); never edit a migration that has already been applied — `db verify` flags such drift via checksums. The connection pool also applies any pending migrations automatically on startup. Each migration runs in its own transaction; add `-- migration: no-transaction` to a file's header to run it outside one (e.g. `CREATE INDEX CONCURRENTLY`).

---

## Running the bot

`main.py` is a [Click](https://click.palletsprojects.com/) CLI. Running it with no subcommand starts the bot; the `db` group manages migrations.

```bash
poetry run python main.py        # run the bot
poetry run python main.py db ... # database management (see above)
```

Logs are written to `percy.log` (a rotating file handler, 32 MiB × 5 backups) and printed to the console with colour-coded levels.

### Beta mode

Percy automatically enters **beta mode** when running on a **non-Linux** system (e.g. local development on Windows/macOS). In beta mode it:

- uses `DISCORD_BETA_TOKEN` instead of `DISCORD_TOKEN`,
- forces the `b.` command prefix, and
- skips the `web_utils` and `comic` cogs.

This lets you develop against a separate beta bot without touching production.

---

## Docker

A `docker-compose.yml` is included with a **Snekbox** service for the Python evaluation sandbox:

```bash
docker compose up -d
```

The bot itself is not containerised by default — run it directly with Poetry against the Docker-hosted services (Snekbox, and optionally your own Lavalink/PostgreSQL).

---

## Development

### Code quality

The project uses [Ruff](https://docs.astral.sh/ruff/) for linting and [Pyright](https://github.com/microsoft/pyright) for type checking (configured in `pyproject.toml`, targeting Python 3.12 with a 125-char line length).

```bash
poetry run ruff check .     # lint
poetry run pyright          # type check
```

### Tests

The test suite lives in `tests/` and uses [pytest](https://docs.pytest.org/). It covers the pure helper modules (`formats`, `fuzzy`, `timetools`), the **service layer** (`bot_health`, `char_info`, `code_stats`, `gateway_stats`, `presence_stats`, `purge`), the **HTTP client** base, the **repository layer**, and the **pure poker engine** — and grows as more logic is extracted from the cogs.

```bash
poetry run pytest                                   # run the whole suite
poetry run pytest tests/test_poker_engine.py        # a single module
poetry run pytest tests/test_poker_engine.py::test_all_in_empties_stack_and_sets_flags  # a single test
```

`pytest` is configured with `asyncio_mode = "auto"`, so `async def` tests run without any extra decorator.

### Continuous integration

`.github/workflows/ci.yml` runs the test suite on every push and pull request. Ruff and Pyright also run there in informational (non-blocking) mode while their pre-existing backlog is worked down; each will be promoted to a required check once clean.

---

## License

This project is licensed under the [Mozilla Public License 2.0](LICENSE).
