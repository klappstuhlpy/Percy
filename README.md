<div align="center">

# Percy v2

**A feature-rich, multipurpose Discord bot built with Python 3.12+ and [discord.py](https://github.com/Rapptz/discord.py).**

Moderation · Auto-moderation · Economy · Casino games · Leveling · Music · Polls · Giveaways · Tags · Reminders · Documentation search · and much more — all in a single, self-hostable package.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7+-5865F2.svg)](https://github.com/Rapptz/discord.py)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg)](LICENSE)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)

[Add Percy to your server](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands) ·
[Website](https://percy.klappstuhl.me/) ·
[Support server](https://discord.gg/eKwMtGydqh)

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
- [Architecture](#architecture)
- [Development](#development)
- [Project structure](#project-structure)
- [License](#license)

---

## Highlights

- **Hybrid commands everywhere** — almost every command works as both a slash command (`/ban`) and a prefix command (`?ban`), powered by a custom command framework on top of discord.py.
- **Rich, helpful errors** — invalid input is answered with an ANSI-coloured "here's where your command broke" trace that points at the exact offending argument.
- **Per-guild everything** — prefixes, automod, audit logging, leveling, polls and music panels are all configured per server and cached for speed.
- **Layered, testable architecture** — a repository data-access layer, MVVM-style UI separation in the cogs, and pure (Discord-free) game engines that can be unit-tested in isolation.
- **Image rendering** — rank cards, casino cards, poker odds bar charts and music panels are drawn server-side with Pillow.

---

## Features

### Moderation & Safety

| Feature                  | What it does                                                                                                                                                                    |
|--------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Core moderation**      | `kick`, `ban`, `multiban`, `softban`, `unban`, `mute`/`unmute`, `selfmute`, `purge` (with rich filters), `slowmode`.                                                            |
| **Mute role management** | Create, bind, sync and unbind a mute role; permission overwrites are applied across channels automatically.                                                                     |
| **Lockdowns**            | Lock down individual channels or the whole server (`lockdown` group), with automatic, timed un-locking via the timer system and lockout protection for the bot.                 |
| **Auto-moderation**      | Configurable automod rules linked to Discord's native AutoMod, mention-spam detection, and **raid protection** that auto-bans spammers.                                         |
| **Gatekeeper**           | A captcha verification system: new members must solve a generated image captcha before they can participate; supports auto-trigger rate limits and `ban`/`kick` bypass actions. |
| **Anti-spam**            | A global `SpamChecker` that throttles command abuse and flags mention spam.                                                                                                     |
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

### Leveling

| Feature                  | What it does                                                                                                                                          |
|--------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| **XP & ranks**           | Earn XP per message (with cooldowns and configurable gain), level up, and view a **rendered rank card** (`/level`).                                   |
| **Leaderboard**          | Per-guild Top-10 board (`/level leaderboard`).                                                                                                        |
| **Level roles**          | Award roles at configured levels, with optional **role stacking**, managed through an interactive view (`/level config roles`).                       |
| **Multipliers**          | Per-role and per-channel XP multipliers (`/level config multiplier`).                                                                                 |
| **Fine-grained control** | Toggle leveling, set the level-up message and channel (or DM), blacklist roles/channels/users, and optionally delete a member's data when they leave. |

### Music

| Feature              | What it does                                                                                                                                                  |
|----------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Lavalink player**  | High-quality audio playback backed by a [Lavalink](https://github.com/lavalink-devs/Lavalink) node ([wavelink](https://github.com/PythonistaGuild/Wavelink)). |
| **Queue & controls** | Play, pause, skip, seek, loop, shuffle and a full queue.                                                                                                      |
| **Playlists**        | Save, load and manage personal playlists (`PlaylistTools`).                                                                                                   |
| **Music panel**      | An optional persistent now-playing control panel pinned in a configured channel.                                                                              |

### Community & Engagement

| Feature        | What it does                                                                                                                                                              |
|----------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Polls**      | Rich polls with up to 8 options, animated vote bars, optional vote reasons, role-ping opt-in, threads, scheduling and live odds. Search, edit and inspect existing polls. |
| **Giveaways**  | Create and manage giveaways through modals; entries via a persistent button, automatic winner draw and reroll.                                                            |
| **Tags**       | Per-guild custom tags with aliases, fuzzy search, ownership transfer and usage stats.                                                                                     |
| **Highlights** | Get a DM when a word or phrase you subscribed to is mentioned.                                                                                                            |
| **Gimmicks**   | Fun/flavour annotation commands.                                                                                                                                          |

### Utility & Productivity

| Feature                      | What it does                                                                                           |
|------------------------------|--------------------------------------------------------------------------------------------------------|
| **Reminders**                | `remind me in 2h to …` style reminders with natural-language time parsing; backed by the timer system. |
| **Notes**                    | Personal, user-installable notes (works in DMs and any server via a user-install app command).         |
| **Temporary voice channels** | "Join-to-create" hub channels that spin up a personal voice channel on join and clean up when empty.   |
| **Emoji management**         | Add, steal, rename and inspect server emojis.                                                          |
| **User & server info**       | Profile, avatar, server info, timezone settings, presence tracking.                                    |

### Developer & Information

| Feature                  | What it does                                                                                                                                            |
|--------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Documentation search** | Query and render docs (e.g. `docs`/`rtfm`/`rtfd`) for libraries from intersphinx inventories.                                                           |
| **Snekbox**              | Safely evaluate arbitrary Python in a sandboxed [Snekbox](https://github.com/python-discord/snekbox) container (run via Docker, see [Docker](#docker)). |
| **AniList**              | Search anime & manga, with OAuth-linked account features.                                                                                               |
| **Comics**               | Subscribe to weekly comic releases (Marvel/DC, via the Marvel API).                                                                                     |
| **Discord status feed**  | Relay Discord's own status-page incidents to a channel.                                                                                                 |
| **Bot stats & meta**     | Uptime, latency, command stats, source links, invite/about, and help.                                                                                   |

---

## Commands

Percy ships with **27 feature modules (cogs)**. Most commands are **hybrid** — available as both slash and prefix commands. The default prefix is `?` (configurable per guild), and the bot also responds to a mention.

Use the built-in help to explore everything interactively:

```text
/help              → paginated overview of every category
/help <command>    → detailed help, usage and examples for one command
?help <category>   → list all commands in a category
```

A few representative command groups:

| Group         | Examples                                                                                                |
|---------------|---------------------------------------------------------------------------------------------------------|
| Moderation    | `kick`, `ban`, `multiban`, `softban`, `mute`, `purge`, `slowmode`, `lockdown start/end`, `moderation …` |
| Configuration | `config …` (per-guild settings), `automod …`, audit-log setup, gatekeeper setup                         |
| Leveling      | `level` (rank card), `level leaderboard`, `level set`, `level config …`                                 |
| Economy       | `balance`, `deposit`, `withdraw`, `transfer`, `leaderboard`, `work`, `crime`, `rob`                     |
| Games         | `poker`, `blackjack`, `roulette`, `slots`, `tower`, `tictactoe`, `minesweeper`, `hangman`               |
| Polls         | `polls create/end/edit/delete/search/history/config`                                                    |
| Music         | `play`, `pause`, `skip`, `queue`, `loop`, playlist tools                                                |
| Utility       | `remind`, `notes …`, `tag …`, `highlight …`, `tempchannels …`, `emoji …`                                |
| Developer     | `docs`/`rtfm`, snekbox eval, `anilist …`, `comic …`                                                     |

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

# ── Lavalink (music) ─────────────────────────────────────
LAVALINK_NODE_1_PASSWORD=            # required for music playback

# ── AniList (required at startup) ────────────────────────
# config.py parses ANILIST_CLIENT_ID as an int at import time, so it MUST be set
# to a valid integer or the bot will fail to start.
ANILIST_CLIENT_ID=
ANILIST_CLIENT_SECRET=

# ── Optional integrations ────────────────────────────────
STATS_WEBHOOK_TOKEN=                 # webhook token for posting bot stats/errors
GENIUS_TOKEN=                        # Genius API (music lyrics)
GITHUB_TOKEN=                        # GitHub API (source links, gists)
DBOTS_TOKEN=                         # discord.bots.gg stats posting
TOPGG_TOKEN=                         # top.gg stats posting
IMAGES_API_TOKEN=                    # image API integrations
MARVEL_API_PUBLIC_KEY=               # Marvel API (comic subscriptions)
MARVEL_API_PRIVATE_KEY=
```

> **Minimum to boot:** a Discord token (`DISCORD_BETA_TOKEN` on Windows/macOS, `DISCORD_TOKEN` on Linux), `DATABASE_PASSWORD`, `DATABASE_HOST`, and a valid integer `ANILIST_CLIENT_ID`. Everything else gracefully disables the corresponding integration if left blank.

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

Once the bot is in your server, use the `/config` slash command (or `?config`) — and the dedicated `automod`, audit-log and gatekeeper setup commands — to configure, per guild:

- Custom command prefixes
- Audit-log channel/webhook and which events to broadcast
- Automod rules, raid protection and alert webhooks
- The captcha **gatekeeper** (role, channel, bypass action, auto-trigger rate)
- Poll channel, poll-reason channel and ping role
- Music panel channel
- Leveling (via `/level config …`)

All of this is stored in PostgreSQL and cached in memory, with the cache invalidated automatically on every change.

---

## Database management

Percy uses **versioned SQL migrations** in `migrations/` (`V1__….sql` … `V15__….sql`). All commands run against the database configured in `.env`/`config.py`.

| Command                                 | Description                                           |
|-----------------------------------------|-------------------------------------------------------|
| `python main.py db init`                | Apply all pending migrations (first-time setup).      |
| `python main.py db upgrade`             | Apply any newly added migrations.                     |
| `python main.py db upgrade -r <N>`      | Upgrade to a specific revision number.                |
| `python main.py db upgrade --sql`       | Print the SQL that would run instead of executing it. |
| `python main.py db migrate -r "reason"` | Create a new, blank migration file to edit.           |
| `python main.py db log`                 | Show migration history (newest first).                |
| `python main.py db log --reverse`       | Show migration history (oldest first).                |

> Always create schema changes via a **new** `migrations/V<N>__name.sql` file (`db migrate`); never edit a migration that has already been applied. The connection pool also applies any pending migrations automatically on startup.

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

## Architecture

Percy is layered to keep data access, business logic and presentation (Discord UI) separate. The big picture:

```
main.py                     CLI entry point (bot runner + DB migration commands)
config.py                   Tokens, IDs, emoji definitions, version info
app/
├── core/                   Framework layer
│   ├── bot.py              The Bot class: extension loading, error handling, prefix resolution
│   ├── models.py           Custom Command / Context / Cog, EmbedBuilder, permissions
│   ├── flags.py            Flag-based command argument system
│   ├── help.py             Paginated help command
│   ├── timer.py            Persistent TimerManager (reminders, temp-bans, lockdowns, …)
│   ├── spam.py             Global spam control
│   ├── tree.py / views.py / pagination.py
│
├── database/               Persistence layer
│   ├── base.py             asyncpg pool wrapper + BaseRecord "mini-ORM" + domain records
│   ├── migrations.py       Versioned SQL migration runner
│   └── repositories/       Data-access layer (Repository pattern)
│       ├── base.py         BaseRepository (pool helpers)
│       ├── guilds.py · users.py · polls.py · leveling.py · moderation.py
│
├── cogs/                   Feature modules (~27 cogs)
│   ├── polls/              models.py · ui.py · cog.py  (MVVM-style split)
│   ├── leveling/           models.py · ui.py · cog.py
│   ├── games/              card games + poker_ui.py (Discord views)
│   ├── music/ · anilist/ · comic/ · doc/ · snekbox/
│   └── mod.py · automod.py · config.py · economy.py · … (single-file cogs)
│
├── games/                  Pure, framework-agnostic game logic (NO discord imports)
│   └── engine/poker.py     TexasHoldem state machine, hand ranking, odds simulation
│
├── rendering/              Pillow image generation (rank cards, cards, charts, panels)
└── utils/                  Helpers: formats, fuzzy, timetools, caching, ANSI builder, …
```

Key design decisions:

- **Custom command framework.** Commands subclass a custom `Command`/`Context`/`Cog` (in `app/core`) rather than vanilla discord.py. This is what powers hybrid commands, the flag system, and the ANSI argument-error renderer in `Bot.on_command_error`. Define commands with the `@command`/`@group` decorators from `app.core` and reply with `ctx.send_success`/`send_error`/`send_info`.
- **Repository data-access layer.** Cogs never write raw SQL where a repository method exists; they call e.g. `self.bot.db.moderation.clear_lockdowns(...)` or `self.bot.db.leveling.get_leaderboard(...)`. The cached config getters (`db.get_guild_config`, `db.get_user_config`, …) remain on the `Database` object and delegate to the repositories, so caching and `.invalidate()` are handled in one place.
- **MVVM-style cogs.** Larger features are packages split into `models.py` (records + pure helpers), `ui.py` (Views/Modals) and `cog.py` (command routing/orchestration), with dependencies flowing one way: `cog → ui → models`. UI components receive their dependencies (bot, records, engines) through their constructors.
- **Pure game engines.** Game rules live under `app/games/engine/` with **no `discord` imports**, returning plain data. The cogs feed them user input and map their output back to embeds/views — which also makes the engines unit-testable without Discord (see `tests/test_poker_engine.py`).
- **Persistent timers.** The `TimerManager` schedules future work in the database and dispatches `on_<event>_timer_complete` when due — the mechanism behind reminders, giveaways, temp-bans/mutes, lockdowns and blacklist expiry.

For a contributor-oriented summary, see [`CLAUDE.md`](CLAUDE.md).

---

## Development

### Code quality

The project uses [Ruff](https://docs.astral.sh/ruff/) for linting and [Pyright](https://github.com/microsoft/pyright) for type checking (configured in `pyproject.toml`).

```bash
poetry run ruff check .     # lint
poetry run pyright          # type check
```

### Tests

The test suite lives in `tests/` and uses [pytest](https://docs.pytest.org/). It covers the pure helper modules (`formats`, `fuzzy`, `timetools`), the **repository layer**, and the **pure poker engine** — and is designed to grow as more logic is extracted from the cogs.

```bash
poetry run pytest                                   # run the whole suite
poetry run pytest tests/test_poker_engine.py        # a single module
poetry run pytest tests/test_poker_engine.py::test_all_in_empties_stack_and_sets_flags  # a single test
```

`pytest` is configured with `asyncio_mode = "auto"`, so `async def` tests run without any extra decorator.

### Continuous integration

`.github/workflows/ci.yml` runs the test suite on every push and pull request. Ruff and Pyright also run there in informational (non-blocking) mode while their pre-existing backlog is worked down; each will be promoted to a required check once clean.

---

## Project structure

```
Percy-v2/
├── main.py                # CLI entry point (bot runner + DB management)
├── config.py              # Tokens, IDs, emoji definitions, version info
├── pyproject.toml         # Poetry project, Ruff/Pyright/pytest config
├── docker-compose.yml     # Snekbox sandbox service
├── app/                   # Application package (see Architecture)
├── migrations/            # Versioned SQL migrations (V1–V15)
├── tests/                 # pytest suite
└── assets/                # Fonts, word lists, image templates
```

---

## License

This project is licensed under the [Mozilla Public License 2.0](LICENSE).
