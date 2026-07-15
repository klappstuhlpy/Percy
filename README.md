<div align="center">

<img src="/assets/percy_banner_short.png" height="300" alt="Percy banner" style="margin-top: 1rem; border-radius: 0.25rem;">

# Percy v2

**A feature-rich, multipurpose Discord bot built with Python 3.12+
and [discord.py](https://github.com/Rapptz/discord.py).**

Moderation · Auto-moderation · Economy · Casino games · Leveling · Music · Polls · Giveaways · Tags · Reminders ·
Documentation search · and much more — all in a single, self-hostable package.

[![Python](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![discord.py](https://img.shields.io/badge/discord.py-2.7+-5865F2.svg)](https://github.com/Rapptz/discord.py)
[![License: MPL 2.0](https://img.shields.io/badge/License-MPL_2.0-brightgreen.svg)](LICENSE)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://docs.astral.sh/ruff/)

[Add Percy to your server](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands) ·
[Website](https://percy.klappstuhl.me/) ·
[Documentation](https://percy.klappstuhl.me/docs/) ·
[Support server](https://percy.klappstuhl.me/support/)

</div>

> The **recommended** way to use Percy is
> to [invite the hosted instance](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands).
> ** It's always up-to-date, fully configured, and you can start using it immediately — no setup required.

## Documentation

Percy's documentation lives in a dedicated [Mintlify](https://mintlify.com) site:

- **Read it:** https://percy.klappstuhl.me/docs/
- **Edit it:** [`klappstuhlpy/percy-docs`](https://github.com/klappstuhlpy/percy-docs)

It covers every feature and the optional AI layer. When you ship a user-facing change — a new command or flag —
update `percy-docs` to match.

The **internal dashboard API** is not documented on the public site. Its always-current interactive reference is the
self-hosted [Scalar](https://scalar.com/) page the bot serves at `http://127.0.0.1:8090/docs` when running with
`INTERNAL_API_TOKEN` set. There is no third-party public API, and none is currently planned — the concept is parked
in `PUBLIC_API_IDEA.md` in the workspace root.

---

## Table of Contents

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

> Temporary bans, mutes and lockdowns are all backed by the persistent **timer system**, so they survive restarts and
> fire exactly when due.

### Economy & Casino Games

| Feature                     | What it does                                                                                                                                                                                                            |
|-----------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Wallet & banking**        | Each member has a `cash`/`bank` wallet scoped to the guild: `balance`, `deposit`, `withdraw`, `transfer`, and a per-guild `leaderboard`.                                                                                |
| **Earning & stealing**      | Risk/reward income commands — `work` (job-aware shifts), `crime`, `slut`, `beg`, `dig`, interactive `search` — and `rob` to steal from other members (blockable with a rob-shield item, admin-toggleable).              |
| **Texas Hold'em Poker**     | Full multi-player (2–4) poker tables with blinds, side-pots, all-in handling, an interactive button UI, an autoplay timer for AFK players, and Monte-Carlo **win-odds analysis** rendered as bar charts.                |
| **Blackjack**               | Play against the dealer with the standard hit/stand/double flow.                                                                                                                                                        |
| **Roulette, Slots & Tower** | Classic casino gambling games with rendered results.                                                                                                                                                                    |
| **Mini-games**              | Tic-Tac-Toe, Minesweeper, Hangman, Wordle, Trivia and Coinflip duels — plus an interactive `games` catalogue with per-member records.                                                                                   |
| **Earning activities**      | `daily` (with streak bonus), `weekly`/`monthly` claims, plus `fish` and `hunt` — cooldown-gated, weighted risk/reward loot tables from junk to rare jackpots.                                                           |
| **Progression**             | An 8-rung job ladder (`job`), passive-earning pets (`pet`), a deterministic daily quest board (`quests`), 13 achievement badges, and `prestige` for permanent payout bonuses — bundled in an interactive `economy` hub. |
| **Guild tuning**            | `economy-config` (and the dashboard) set a payout multiplier, daily base, casino max-bet cap, and a rob on/off toggle per guild.                                                                                        |
| **Shop & items**            | Admins stock a per-guild shop (`shop add/remove`); members `buy`, `sell` and browse their `inventory`. Items can carry a **use-effect**: cash vouchers, lootboxes, role grants, or timed XP/loot boosts.                |
| **Item effects**            | `use` consumes an item and applies its effect — boosts run on a timer (extendable by re-using) and multiply leveling XP (message + voice) or `fish`/`hunt` payouts. Item commands autocomplete.                         |
| **Server lottery**          | Admins start a timed `lottery`; members buy weighted tickets, the pot grows, and a winner is drawn automatically via the persistent timer system (announced with a Components V2 card).                                 |

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

| Feature                      | What it does                                                                                                                                                           |
|------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Reminders**                | `remind me in 2h to …` style reminders with natural-language time parsing; backed by the timer system.                                                                 |
| **Notes**                    | Personal, user-installable notes (works in DMs and any server via a user-install app command).                                                                         |
| **Temporary voice channels** | "Join-to-create" hub channels that spin up a personal voice channel on join and clean up when empty.                                                                   |
| **Emoji management**         | Add, steal, rename and inspect server emojis, with per-guild emoji usage stats.                                                                                        |
| **User & server info**       | Profile, avatar, `serverinfo`, `userinfo`, timezone settings, and per-user settings.                                                                                   |
| **History tracking**         | Username/nickname history (`names`), `lastseen`, avatar history, and a rendered **presence chart**.                                                                    |
| **Translation**              | `translate` text into any language (ISO code or name), with the source auto-detected; keyless backend.                                                                 |
| **Stat counters**            | Self-updating voice channels that display a live server statistic — members, humans, bots, online, boosts, roles or channels (`statcounter …`).                        |
| **Link & paste tools**       | `shorten` a URL, generate a `qr` code, host a `paste`, or `preview` a link's Open Graph embed — powered by the self-hosted [klappstuhl.me](https://klappstuhl.me) API. |

### Developer & Information

| Feature                  | What it does                                                                                                                                                                                                                                        |
|--------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **Documentation search** | Query and render docs (e.g. `docs`/`rtfm`/`rtfd`) for libraries from intersphinx inventories, with a local cache.                                                                                                                                   |
| **Snekbox**              | Safely evaluate arbitrary Python in a sandboxed [Snekbox](https://github.com/python-discord/snekbox) container (run via Docker, see [Self-hosting](#self-hosting)).                                                                                 |
| **AniList**              | Search anime & manga, with OAuth-linked account features.                                                                                                                                                                                           |
| **Comics**               | Subscribe to weekly comic releases (Marvel/DC, via a self-hosted League of Comic Geeks API wrapper).                                                                                                                                                |
| **AI assistant**         | `ask` the bot a question, answered by a self-hosted open model via [Ollama](https://ollama.com/); supports follow-ups by replying to its answers. Degrades gracefully when the AI host is unreachable (set `OLLAMA_ENABLED=false` to hard-disable). |
| **Discord status feed**  | Relay Discord's own status-page incidents to a channel.                                                                                                                                                                                             |
| **Bot stats & meta**     | Uptime, latency, command stats, source links, invite/about, and help. Owner tooling (`admin`) covers sync, hot-reload, an SQL console and task introspection.                                                                                       |
| **Bot-list stats**       | Auto-posts the server count to discord.bots.gg, top.gg and discordbotlist.com when those tokens are configured.                                                                                                                                     |
| **Vote rewards**         | Voting on top.gg or discordbotlist.com grants a renewable **+10% XP boost for 12 hours**, applied in every shared server. Webhooks land on `/api/webhooks/{topgg,discordbotlist}`; `?vote` shows links and live boost status.                       |

---

## Commands

Percy ships with **28 feature modules (cogs)**. Most commands are **hybrid** — available as both slash and prefix
commands. The default prefix is `?` (configurable per guild), and the bot also responds to a mention.

---

## Self-hosting

> **Self-host at your own risk.** Percy is designed to run as a single hosted instance. The setup involves multiple
> services, custom emoji IDs, and environment-specific configuration.

> I'd strongly prefer you just [invite Percy](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands)
> rather than self-hosting. No support is provided for self-hosted instances.

If you still want to run your own instance, here's the short version:

**Requirements:** Python 3.12+, PostgreSQL 14+ (with `pg_trgm`), Poetry, a Lavalink server (for music).

```bash
git clone https://github.com/klappstuhlpy/Percy-v2.git && cd Percy-v2
poetry install
cp .env.example .env          # fill in your tokens and DB credentials
poetry run python main.py db init
poetry run python main.py
```

You'll need to configure `config.py` (owner IDs, guild IDs, Lavalink nodes, custom emoji IDs) and your `.env` (Discord
token, database password/host, and any optional API keys). See [`.env.example`](.env.example) for the full variable
list.

A `Dockerfile` and `docker-compose.yml` are included if you prefer containers (`docker compose up -d --build`).

Percy auto-enters **beta mode** on non-Linux systems: uses `DISCORD_BETA_TOKEN`, forces `b.` prefix, skips some cogs,
and connects to a separate `percy_beta` database (override with `DATABASE_NAME`) so local testing never touches
production data — useful for local development. Run `python main.py db upgrade` on your dev machine once to migrate
`percy_beta`.

---

## Development

### Code quality

The project uses [Ruff](https://docs.astral.sh/ruff/) for linting and [Pyright](https://github.com/microsoft/pyright)
for type checking (configured in `pyproject.toml`, targeting Python 3.12 with a 125-char line length).

### Tests

The test suite lives in `tests/` and uses [pytest](https://docs.pytest.org/). It covers the pure helper modules (
`formats`, `fuzzy`, `timetools`), the **service layer** (`bot_health`, `char_info`, `code_stats`, `gateway_stats`,
`presence_stats`, `purge`), the **HTTP client** base, the **repository layer**, and the **pure poker engine** — and
grows as more logic is extracted from the cogs.

`pytest` is configured with `asyncio_mode = "auto"`, so `async def` tests run without any extra decorator.

### Continuous integration

`.github/workflows/ci.yml` runs the test suite on every push and pull request (the required gate). Ruff and Pyright
also run there in non-blocking mode, but the codebase is kept at **zero findings** for both — so they're candidates to
promote to required checks.

---

## License

This project is licensed under the [Mozilla Public License 2.0](LICENSE).
