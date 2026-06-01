# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
poetry install                          # install dependencies (Python 3.12+, Poetry)
poetry run python main.py               # run the bot
poetry run pytest                       # run the full test suite
poetry run pytest tests/test_formats.py # run a single test module
poetry run pytest tests/test_fuzzy.py::test_name  # run a single test
poetry run ruff check .                 # lint
poetry run pyright                      # type check
```

Database management (versioned SQL migrations in `migrations/`, run against the DB in `.env`):

```bash
poetry run python main.py db init                 # apply all pending migrations (first-time setup)
poetry run python main.py db upgrade              # apply newly added migrations
poetry run python main.py db upgrade -r <N>       # upgrade to a specific revision
poetry run python main.py db migrate -r "reason"  # create a new blank migration file
poetry run python main.py db log                  # show migration history
```

`main.py` is a Click CLI: no subcommand runs the bot; the `db` group manages migrations.

Tests use `asyncio_mode = "auto"` (configured in `pyproject.toml`), so `async def` tests need no decorator. CI (`.github/workflows/ci.yml`) gates on pytest; Ruff and Pyright run non-blocking while a pre-existing backlog is cleared (see `pyright_output.txt`).

## Beta mode

`config.beta` is `True` on any non-Linux system (e.g. local Windows dev). In beta mode the bot uses `DISCORD_BETA_TOKEN`, forces the `b.` prefix, and skips the `web_utils` and `comic` cogs (see `Bot._load_extensions`). Keep this in mind when testing prefix- or cog-dependent behavior locally.

## Architecture

Percy is a discord.py bot layered into `core` (framework), `database` (persistence), and `cogs` (features). The big picture spans several files:

### Custom command framework (`app/core/`)

The bot does **not** use vanilla discord.py commands. `app/core/models.py` defines subclasses that everything else builds on, re-exported through `app/core/__init__.py` (import from `app.core`, not the submodule):

- `Command` / `GroupCommand` / `HybridCommand` — extend discord.py commands with custom flag transformation and ANSI signature rendering. Define commands with the `@command` / `@group` decorators from `app.core`, not `@commands.command`.
- `Context` — custom context with helpers like `send_success`, `send_error`, `send_warning`, `send_info` (which swap between custom emojis and unicode based on `use_external_emojis` permission). Use these instead of raw `ctx.send`.
- `Cog` — base cog class (carries an `emoji` class attribute for the help menu). All feature cogs subclass this.
- `PermissionSpec` / `PermissionTemplate`, `EmbedBuilder`, and the flag system (`app/core/flags.py`) also live here.

`Bot` (`app/core/bot.py`) is the central object. It auto-discovers cogs via `app/cogs/__init__.py` (`EXTENSIONS` = every module in the package, populated by `iter_modules`). `Bot.on_command_error` contains the elaborate ANSI back-trace renderer that points at the offending argument in a command signature — this is why commands go through the custom `Command` class. The bot also owns `db`, `timers`, `spam_control`, a JSON-file-backed `blacklist`/`temp_channels`/`doc_links` (via `app.utils.Config`), and member-resolution helpers.

Cogs are loaded by a module-level `async def setup(bot: Bot)` function at the bottom of each cog file (standard discord.py convention).

### Data access (`app/database/`)

`Database` (`app/database/base.py`) wraps an `asyncpg` pool and is reachable as `self.bot.db`. Access patterns:

- **Pool helpers**: `db.execute / fetch / fetchrow / fetchval` and `db.acquire()` for raw SQL and transactions.
- **Config accessors**: `db.get_guild_config(id)`, `db.get_user_config(id)`, `db.get_guild_gatekeeper(id)`, `db.get_user_balance(...)`. The guild/user config getters are `@cache.cache()`-memoized — when you mutate a record you must invalidate (the record `_update` methods already call e.g. `db.get_guild_config.invalidate(id)`).
- **`BaseRecord`** is the lightweight ORM. Subclasses declare `__slots__` matching DB columns; the base maps an `asyncpg.Record` onto attributes and provides `update()`, `add()`, `remove()`, `append()`, `prune()`, `merge()` helpers that build `UPDATE` queries (each subclass implements `_update` with its table name). Many records hold a `bot` reference so properties can resolve Discord objects (e.g. `GuildConfig.guild`, `.mute_role`). Records like `GuildConfig`, `UserConfig`, `Gatekeeper`, `Balance`, and per-cog records (e.g. `Note`) all follow this pattern.

`Gatekeeper` (the captcha verification system) is a stateful `BaseRecord` that owns a background `role_loop` task and a `CancellableQueue` — be careful editing it; the cache invalidation hook cancels its task on refresh.

### Timers (`app/core/timer.py`)

`TimerManager` (`bot.timers`) schedules future actions persisted in the DB. Create a timer with `bot.timers.create(when, 'event_name', **kwargs)`; when it fires the bot dispatches `on_<event_name>_timer_complete(timer)`, which the relevant cog handles (e.g. `on_reminder_timer_complete`, `on_giveaway_timer_complete`, `on_tempban_timer_complete`). This is the mechanism behind reminders, giveaways, temp-bans/mutes, lockdowns, and blacklist expiry.

### Other layers

- `app/rendering/` — Pillow-based image generation (rank cards, music panels). `ASSETS` points at `assets/` (fonts, templates, word lists).
- `app/utils/` — pure helpers; `formats.py`, `fuzzy.py`, `timetools.py` are the currently tested modules. Also `cache.py` (the memoization decorator used on DB getters), `ansi.py` (`AnsiStringBuilder`), `config.py` (JSON file store), `lock.py`, `checks.py`.
- `config.py` (repo root) — tokens, owner/guild IDs, emoji definitions, version, Lavalink nodes; reads from `.env`.

## Conventions

- Use `from __future__ import annotations` and modern typing (Ruff targets py312, line length 125). Pyright runs in `basic` mode with several `reportUnused*` rules as errors.
- Cogs should fetch state through `self.bot.db` accessors and the `BaseRecord` helpers rather than scattering raw SQL where a getter already exists.
- New DB schema changes go through a new `migrations/V<N>__name.sql` file created with `db migrate`, never by editing applied migrations.

## Refactoring brief

`.claude/CLAUDE.md` contains an in-progress, phased refactoring plan (repository pattern, MVVM-lite UI extraction, splitting the large `mod.py`/`models.py`/`polls.py`/game files). Consult it before large structural changes so new work aligns with the target architecture rather than the current one.
