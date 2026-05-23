# Percy v2

A feature-rich, multipurpose Discord bot built with Python 3.12+ and [discord.py](https://github.com/Rapptz/discord.py).

Percy covers moderation, economy, games, music, leveling, polls, giveaways, custom tags, documentation browsing, and more — all in a single, self-hostable package.

> **Prefer not to run your own instance?**  
> Invite Percy directly: [Add to your server](https://discord.com/api/oauth2/authorize?client_id=1070054930125176923&permissions=1480988813527&scope=bot%20applications.commands)

---

## Features

| Category | Description |
|---|---|
| **Moderation** | Ban, kick, warn, mute, lockdowns, mention-spam detection, audit logging |
| **Auto-moderation** | Configurable automod rules, raid detection, gatekeeper captcha |
| **Economy** | Per-guild wallet system, gambling games (slots, roulette, poker, blackjack) |
| **Leveling** | XP tracking, custom rank cards rendered with Pillow |
| **Music** | Lavalink-backed player with playlists and queues |
| **Polls & Giveaways** | Rich poll system with vote bars; full giveaway management |
| **Tags** | Per-guild custom tag system with aliases and fuzzy search |
| **Games** | Tictactoe, Minesweeper, Hangman, and card games |
| **Utility** | Reminders, notes, temporary voice channels, word highlights |
| **Developer** | Snekbox Python sandbox, documentation browser, AniList & Comic integrations |

---

## Prerequisites

- **Python** ≥ 3.12 — [Download](https://www.python.org/downloads/)
- **PostgreSQL** ≥ 14 — [Download](https://www.postgresql.org/download/)
- **Poetry** — [Install](https://python-poetry.org/docs/)
- **Lavalink** server (required for music features) — [Releases](https://github.com/lavalink-devs/Lavalink/releases)

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

Launch the PostgreSQL CLI and run:

```sql
CREATE ROLE percy WITH LOGIN PASSWORD 'your_password';
CREATE DATABASE percy OWNER percy;
CREATE EXTENSION pg_trgm;
```

### 4. Configure environment variables

Create a `.env` file in the project root (never commit this file):

```env
# Discord
DISCORD_TOKEN=
DISCORD_BETA_TOKEN=
DISCORD_CLIENT_SECRET=

# Database
DATABASE_PASSWORD=
DATABASE_HOST=localhost

# Lavalink
LAVALINK_NODE_1_PASSWORD=

# Optional integrations
STATS_WEBHOOK_TOKEN=
GENIUS_TOKEN=
GITHUB_TOKEN=
DBOTS_TOKEN=
TOPGG_TOKEN=
IMAGES_API_TOKEN=
ANILIST_CLIENT_ID=
ANILIST_CLIENT_SECRET=
MARVEL_API_PUBLIC_KEY=
MARVEL_API_PRIVATE_KEY=
```

### 5. Initialize the database

```bash
poetry run python main.py db init
```

### 6. Run the bot

```bash
poetry run python main.py
```

---

## Database Management

Percy uses versioned SQL migrations. All commands run against the database configured in `.env`.

| Command | Description |
|---|---|
| `python main.py db init` | Apply all pending migrations (first-time setup) |
| `python main.py db upgrade` | Apply any newly added migrations |
| `python main.py db upgrade -r <N>` | Apply a specific migration by revision number |
| `python main.py db migrate -r "reason"` | Create a new blank migration file |
| `python main.py db log` | Show migration history (newest first) |
| `python main.py db log --reverse` | Show migration history (oldest first) |

---

## Docker

A `docker-compose.yml` is included with a `snekbox` service for the Python evaluation sandbox. To use it:

```bash
docker compose up -d
```

The bot itself is not containerised by default — run it directly with Poetry against the Docker-hosted services.

---

## Configuration

After the bot is running, use the `/config` slash command (or `?config` prefix command) in your server to configure per-guild settings such as:

- Custom command prefixes
- Audit log channel
- Automod rules and alert webhooks
- Poll and music panel channels
- Captcha gatekeeper

---

## Development

### Beta mode

The bot automatically enters beta mode when running on a non-Linux system. In beta mode, certain cogs (e.g. `web_utils`, `comic`) are skipped, and the beta Discord token is used instead.

### Code quality

The project uses [Ruff](https://docs.astral.sh/ruff/) for linting and [Pyright](https://github.com/microsoft/pyright) for type checking.

```bash
# Lint
ruff check .

# Type check
pyright
```

---

## Project Structure

```
Percy-v2/
├── main.py               # CLI entry point (bot runner + DB management)
├── config.py             # Tokens, IDs, emoji definitions, version info
├── app/
│   ├── core/             # Bot class, Context, Command, help system, timers, views
│   ├── cogs/             # Feature modules (~30 cogs)
│   │   ├── games/        # Card games, Minesweeper, Tictactoe, Hangman
│   │   ├── music/        # Lavalink music player
│   │   ├── snekbox/      # Python evaluation sandbox
│   │   ├── anilist/      # AniList API integration
│   │   ├── comic/        # Marvel/comic API integration
│   │   └── doc/          # Documentation browser
│   ├── database/         # asyncpg connection pool, ORM base class, migrations runner
│   ├── rendering/        # Pillow image rendering (rank cards, music panels)
│   └── utils/            # Helpers, formatters, pagination, caching, ANSI builder
├── migrations/           # Versioned SQL migration files (V1–V15)
└── assets/               # Fonts, word lists, image templates
```

---

## License

This project is licensed under the [Mozilla Public License 2.0](LICENSE).
