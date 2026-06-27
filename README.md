<div align="center">

# Percy v2

**A feature-rich, multipurpose Discord bot built with Python 3.12+ and [discord.py](https://github.com/Rapptz/discord.py).**

Moderation · Auto-moderation · Economy · Casino games · Leveling · Music · Polls · Giveaways · Tags · Reminders · Documentation search · and much more — all in a single, self-hostable package.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7+-5865F2.svg)](https://github.com/Rapptz/discord.py)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg)](LICENSE)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)

[Add Percy to your server](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands) ·
[Website](https://percy.klappstuhl.me/dashboard/) ·
[Documentation](https://percy.klappstuhl.me/docs/) ·
[Support server](https://discord.gg/3jSYQ9VNbA)

</div>

> **The recommended way to use Percy is to [invite the hosted instance](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands).** It's always up-to-date, fully configured, and you can start using it immediately — no setup required.

## Documentation

Percy's documentation lives in a dedicated [Mintlify](https://mintlify.com) site:

- **Read it:** https://percy.klappstuhl.me/docs/
- **Edit it:** [`klappstuhlpy/percy-docs`](https://github.com/klappstuhlpy/percy-docs)

It covers every feature, the optional AI layer, the public **Klappstuhl.me API**, and the internal dashboard API. When you ship a user-facing change — a new command, flag, or API endpoint — update `percy-docs` to match.

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
- [Self-hosting](#self-hosting)
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
| **Sentinel**             | A captcha verification system: new members must solve a generated image captcha before they can participate; supports auto-trigger rate limits and `ban`/`kick` bypass actions. |
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

| Feature              | What it does                                                                                                                                                                                                                                                                                                                                                                               |
|----------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Lavalink player**  | High-quality audio playback backed by a [Lavalink](https://github.com/lavalink-devs/Lavalink) node ([wavelink](https://github.com/PythonistaGuild/Wavelink)). Sources: Spotify, Apple Music, SoundCloud (Amazon Music is unsupported).                                                                                                                                                     |
| **Queue & controls** | Play, pause, skip, seek, loop, shuffle, an equalizer and a full queue.                                                                                                                                                                                                                                                                                                                     |
| **24/7 always-on**   | `/music 247` keeps the bot permanently connected to a voice channel playing a radio/stream URL, a looping playlist (Spotify/Apple/SoundCloud links work directly), or endless autoplay. Ships curated radio presets (`/music radios`, e.g. `lofi`, `chill`, `antenne`) so no stream URL is needed. Sessions persist to PostgreSQL and auto-reconnect/resume after disconnects or restarts. |
| **Resilience**       | Lavalink session resume + self-healing recovery: stuck/failed tracks are skipped instead of dropping the player, so playback doesn't cut off.                                                                                                                                                                                                                                              |
| **Lyrics**           | Fetch song lyrics (Genius API) for the current track.                                                                                                                                                                                                                                                                                                                                      |
| **Playlists**        | Save, load and manage personal playlists (part of `Music` cog), persisted in PostgreSQL.                                                                                                                                                                                                                                                                                                   |
| **Music panel**      | An optional persistent now-playing control panel pinned in a configured channel.                                                                                                                                                                                                                                                                                                           |

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

| Feature                  | What it does                                                                                                                                                                                                                  |
|--------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Documentation search** | Query and render docs (e.g. `docs`/`rtfm`/`rtfd`) for libraries from intersphinx inventories, with a local cache.                                                                                                             |
| **Snekbox**              | Safely evaluate arbitrary Python in a sandboxed [Snekbox](https://github.com/python-discord/snekbox) container (run via Docker, see [Docker](#docker)).                                                                       |
| **AniList**              | Search anime & manga, with OAuth-linked account features.                                                                                                                                                                     |
| **Comics**               | Subscribe to weekly comic releases (Marvel/DC, via a self-hosted League of Comic Geeks API wrapper).                                                                                                                          |
| **AI assistant**         | `ask` the bot a question, answered by a self-hosted open model via [Ollama](https://ollama.com/); supports follow-ups by replying to its answers. Degrades gracefully when the AI host is unreachable (set `OLLAMA_ENABLED=false` to hard-disable). |
| **Discord status feed**  | Relay Discord's own status-page incidents to a channel.                                                                                                                                                                       |
| **Bot stats & meta**     | Uptime, latency, command stats, source links, invite/about, and help. Owner tooling (`admin`) covers sync, hot-reload, an SQL console and task introspection.                                                                 |
| **Bot-list stats**       | Auto-posts the server count to discord.bots.gg, top.gg and discordbotlist.com when those tokens are configured.                                                                                                               |
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
| Configuration | `config …` (per-guild settings), `automod …`, audit-log setup, sentinel setup                                                                           |
| Leveling      | `level` (rank card), `level leaderboard`, `level set`, `level config …`                                                                                 |
| Economy       | `balance`, `deposit`, `withdraw`, `transfer`, `leaderboard`, `daily`, `fish`, `hunt`, `shop …`, `buy`, `sell`, `inventory`, `use`, `perks`, `lottery …` |
| Games         | `poker`, `blackjack`, `roulette`, `slots`, `tower`, `tictactoe`, `minesweeper`, `hangman`                                                               |
| Polls         | `polls create/end/edit/delete/search/history/config`                                                                                                    |
| Music         | `play`, `pause`, `skip`, `queue`, `loop`, `lyrics`, playlist tools                                                                                      |
| Utility       | `remind`, `notes …`, `tag …`, `highlight …`, `tempchannels …`, `emoji …`, `timezone …`, `translate`, `statcounter …`, `autoresponder …`                 |
| Info          | `userinfo`, `serverinfo`, `avatar`, `names`, `lastseen`, `presence`                                                                                     |
| Developer     | `docs`/`rtfm`, snekbox eval, `anilist …`, `comic …`, `ask` (AI assistant)                                                                               |

---

## Self-hosting

> **Self-host at your own risk.** Percy is designed to run as a single hosted instance. The setup involves multiple services, custom emoji IDs, and environment-specific configuration. I'd strongly prefer you just [invite Percy](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands) rather than self-hosting. No support is provided for self-hosted instances.

If you still want to run your own instance, here's the short version:

**Requirements:** Python 3.12+, PostgreSQL 14+ (with `pg_trgm`), Poetry, a Lavalink server (for music).

```bash
git clone https://github.com/klappstuhlpy/Percy-v2.git && cd Percy-v2
poetry install
cp .env.example .env          # fill in your tokens and DB credentials
poetry run python main.py db init
poetry run python main.py
```

You'll need to configure `config.py` (owner IDs, guild IDs, Lavalink nodes, custom emoji IDs) and your `.env` (Discord token, database password/host, and any optional API keys). See [`.env.example`](.env.example) for the full variable list.

A `Dockerfile` and `docker-compose.yml` are included if you prefer containers (`docker compose up -d --build`).

Percy auto-enters **beta mode** on non-Linux systems: uses `DISCORD_BETA_TOKEN`, forces `b.` prefix, and skips some cogs — useful for local development.

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
