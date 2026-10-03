# Changelog

All notable changes to Percy are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- When Percy failed to start (a rejected Discord token, for example), shutting down
  crashed with a `RecursionError` that hid the actual error, and the process hung
  until Docker killed it. The real error is logged again and Percy exits.

## [2.6.0] - 2026-10-03

### Changed

- Presence history and name/nickname/avatar history are now **opt-in**. Nothing is
  recorded for your account until you switch it on from your `/settings` card.
  Everyone starts switched off, including existing users: tracking that was on was
  the old default, not a choice anyone made, so turn it back on if you want it.
  Requires migration V47.
- The privacy policy now says tracking is off until you turn it on, and describes the
  `/settings` toggles that actually exist instead of commands that never did.

### Fixed

- Percy stored the avatar of a member joining a server even when they had switched
  name and avatar history off.
- Percy no longer downloads the avatar of every member of a server it is added to.

## [2.5.1] - 2026-08-22

### Fixed

- Percy hit Discord's ceiling of 100 global slash commands, which stopped the whole
  user-settings module from loading — `/settings` and everything beside it were gone.
  Twelve commands that take no arguments (`beg`, `dig`, `fish`, `hunt`, `search`,
  `work`, `weekly`, `monthly`, `prestige`, `crime`, `slut`, `cleanupleft`) are now
  text commands only; a slash command earns its slot by having arguments to fill in.
- One command over that ceiling can no longer take its entire module down with it:
  the command stays text-only and says so in the log.
- AniList being unreachable (a 403 from their API) stopped the AniList module from
  loading at all. It now loads and only its autocomplete suggestions stay empty.

## [2.5.0] - 2026-08-20

### Added

- Personal release subscriptions: `/subscribe` follows a comic series, creator, character, film universe or person, and Percy DMs you a digest when something you follow lands.
- `/subscriptions` lists everything you follow with a remove and a per-row updates toggle; `/unsubscribe` removes one directly.
- A **Subscriptions** page on the website for managing what you follow, including muting a follow without losing it.
- News delivery: a subscription set to `news` or `both` now brings recent articles about what you follow into the same hourly digest, each headline credited to its source and linking to the original.
- A **Releases** page on the website: what just landed and what is imminent across comics and film & TV, with the entries you follow lifted to the top when you are signed in.
- A **News** page on the website, filterable by subject, source and search, plus a headline strip on the releases, comics and watch-order pages that leads with what you follow.
- Related news on comic pages: the most recent stories written about that series.
- A page for every film and show on the website, with its cast and crew, where it sits in the watch order, where to stream it and a trailer — a link that only becomes an embedded player once you click it, so nothing starts playing on its own.
- Read/watched marks across the website: mark a comic read or a film watched from the releases page, the comic archive, a watch order or any detail page, with an unmark on every one of them. A watch order also shows how far through it you are, and `j`/`k`/`w` step through a list and mark without the mouse.
- Film and TV release dates are re-checked against TMDB every night, so a digest arrives on the day something is actually out.
- Timeout duration validation helper for the internal API.
- `codeimage`, `chart`, and `mdpdf` render commands via klappstuhl.me integration.
- A multi-season show is now one entry **per season** in a watch order, so "watch Daredevil season 2 here" is an instruction rather than a suggestion. The series itself becomes a heading over its seasons; the seasons are what you mark.
- Every universe has key art — a backdrop behind its header and a wordmark on its tile.
- Streaming rows now carry TMDB's own watch link for the region, which is the attribution their terms ask for wherever provider logos appear.
- Ghost Rider, Nova, Spider-Man: Brand New Day, Avengers: Secret Wars, the untitled X-Men film, Black Panther 3, the Shang-Chi sequel, Your Friendly Neighborhood Spider-Man and The Daily Bugle are all in the MCU order; The Mandalorian and Grogu and Star Wars: Starfighter are in the Star Wars one.

### Changed

- Watch orders no longer count behind-the-scenes material. Assembled making-ofs, Legends recaps, anniversary documentaries, LEGO tie-ins, preschool spin-offs, promo featurettes and "special look" clips are excluded by name, and Star Wars no longer sweeps in ILM documentaries or a robotics film. A watch order is for releases.
- Mainline films that were filed under "Shorts & Specials" now sit in the phase or era they belong to, including the 2008 Clone Wars feature, which is a theatrical film rather than a special.
- Two discovery queries that could never return anything — the Wizarding World's collections and the X-Men's — were removed rather than left looking like coverage that does not exist.

### Fixed

- Following an actor or a character now matches multi-season shows again. Splitting a series into seasons left the cast on the series row while only the seasons could announce a release, so those follows quietly stopped matching anything.
- Two titles that had been silently dropped for years are back: Star Wars: The Clone Wars and 2015's Fantastic Four. A title whose name collides with another is now disambiguated instead of skipped.

### Removed

- SSH tunnel logic for the database and Ollama (and the `sshtunnel`/`paramiko`
  dependencies). Percy now connects directly to the configured `DATABASE_HOST`
  and `OLLAMA_HOST`; the `SSH_TUNNEL_*` and `OLLAMA_TUNNEL_*` env vars are gone.

## [2.4.0] - 2026-07-08

### Added

- klappstuhl.me image tools: `scan`, `screenshot`, `shorten`, `QR`, `paste`, `preview` commands.
- Per-guild image galleries with poll banners routed to gallery storage.
- Outbound webhook subscriptions (event-driven POSTs with HMAC-SHA256 signing and delivery tracking).
- Analytics API: zero-filled time-series and headline summary with deltas.
- Backup & templates: export/import guild config, shareable portable templates.
- `TransportError` for improved transport-layer error classification in HTTP clients.

### Changed

- klappstuhl.me client split into public and internal variants with typed dataclasses.
- Client pointed at versioned `/api/v1` base URL.

### Fixed

- Multipart url-source handling in klappstuhl.py (bumped to v0.4.2).
- Short link embed description no longer shows redundant `https://` prefix.

## [2.3.0] - 2026-07-03

### Added

- Dynamic command permissions with templates and native slash command gating.
- Per-guild command permission overrides.
- Command visibility filters for dashboard management.
- Economy module with cog setup and domain-specific service layers.
- Migration `reseal` functionality to sync checksums without re-executing SQL.
- Shared error responses for guild-scoped internal API routes.
- Missing-permission notifications to admins during background bot actions.

### Changed

- Presence tracking optimized by refactoring key generation and cache checks.

### Fixed

- Sentinel role state type name typo.
- locg-api error logging uses JSON response for status handling.

## [2.2.0] - 2026-06-27

### Added

- **AI layer** — optional self-hosted Ollama inference (default-off, flag-gated per guild):
  - Phase 0: Ollama inference core (Groq removed).
  - Phase 1: Per-guild AI flags with per-channel overrides, dashboard AI tab.
  - Phase 2: Natural-language command router (confirm-to-run).
  - Phase 3: AI moderation verdicts (flag-for-review, never auto-punishes).
  - Phase 4: Music intent — natural-language "vibe" search with filters.
  - Phase 5: Polls and giveaways from a plain-language description.
  - Phase 6: Semantic tag retrieval (`tag find`).
- Multi-turn `?ask` conversations via Discord reply chains.
- Rich Percy persona/knowledge grounding for the AI assistant.
- AI moderation alert with persistent Delete/Warn/Kick/Ban/Dismiss action buttons.
- Dashboard command-palette assistant endpoint (`/ai/ask`).

### Fixed

- AI assistant no longer invents commands or produces fake setup instructions.
- Typo correction takes priority over AI routing (catches transpositions like `aks` → `ask`).
- AI moderation flags delivered even without an alert webhook configured.
- `?ask` replies bounded so CPU inference stops timing out.
- Giveaway quick: correct winner count, channel, and description extraction.
- Polls ask: uses `poll.id` (not `.index`), supports image and discussion thread.

## [2.1.0] - 2026-06-20

### Added

- Music player session persistence across bot restarts.
- DJ mode for restricting player controls to designated roles.
- 24/7 mode to keep the bot in voice without auto-disconnect.
- Live lyrics display with LRCLib integration for time-synced lyrics.
- Progress bar rendering in the music player panel.
- Music control endpoint for the dashboard (play/pause/skip/seek/volume/loop/shuffle).
- Player resync mechanism for orphaned sessions after Lavalink node loss.
- Custom bot profile endpoints for per-guild live state management.
- Top.gg webhook HMAC signature verification (v1 + legacy v0).
- User data export with comprehensive personal data and improved DM handling.
- Overdue poll reconciliation and cleanup mechanism.
- Autoplay support for YouTube links with duplicate prevention.
- Queue command shows recently played history alongside upcoming tracks.

### Changed

- Track artwork handling normalized across player and API for consistent display.
- Sentinel message handling simplified with default title and body.
- Captcha verification retry logic streamlined.

## [2.0.0] - 2026-06-07

### Added

- **Internal HTTP API** for dashboard integration (FastAPI, token-authenticated).
- Guild configuration read/write, roles/channels resolution.
- Leveling endpoints: config, leaderboard, XP history, user management.
- Polls, tags, and commands management via API.
- Member detail, avatar history, and action endpoints (kick/ban/mute/timeout/warn/purge).
- Moderation cases CRUD, bulk actions, and member activity tracking.
- Economy endpoints: settings, balances, items, lottery.
- Music internal API endpoints (now-playing, queue, controls, lyrics, equalizer).
- Giveaway and tag management endpoints.
- Lockdown and moderation ignore management.
- Audit log flags exposed via API.
- Dashboard poll creation with Components V2 vote buttons.
- API versioning (`/api/v1` prefix, `X-API-Version` header).
- Mention spam protection flag.
- Chart rendering with matplotlib and theme consistency.
- Redesigned level card with enhanced visuals and gradient progress bar.
- Lottery jackpot tracking and improved notifications.
- Lavalink metrics parsing for bot health reporting.
- AniList OAuth token management with persistent storage.
- Daily XP snapshot functionality.

### Changed

- Internal API migrated from a simple handler to **FastAPI** for performance, validation, and auto-generated OpenAPI docs.
- Internal API reorganized into a package with domain routers.
- Components V2 migration across all UI surfaces (help, moderation config, gatekeeper, polls, music panel, games).
- Help command links to Percy's dashboard.

### Fixed

- Music panel and `/music reset` now respond correctly.
- Poll interaction response handling improved.
- Playlist index retrieval in paginator logic.

## [1.5.0] - 2026-06-04

### Added

- Voice XP, economy progression, autoresponder, translation, stat counters, and AI assistant foundation.
- Anti-spam escalation by frequency and recency.
- Recurring reminders.
- Starboard (repost highly-reacted messages).
- Queryable moderation case log.
- Economy shop with inventory and daily rewards.
- Self-assignable role menus.
- Pure game engines for new mini-games (Russian Roulette, Higher/Lower).
- Insurance and surrender mechanics for Blackjack.
- Configurable odds mode for Poker (live/full/off).
- Blind raises and clearer minimum raise/bet rules in Poker.
- Context menu commands.
- Clickable mentions for slash commands with lazy ID resolution.
- Runtime configurations and monitoring.

### Fixed

- Help categories spread across multiple selects past 25 items.
- Role menu buttons now labelled with their role names.
- Bot permissions no longer leak between commands.

## [1.4.0] - 2026-06-01

### Changed

- **Architecture overhaul**: service-oriented architecture with Engine/Bridge/UI/Cog pattern.
- All SQL routed through dedicated repositories (`app/database/repositories/`).
- Service layer extracted (`app/services/`) — Discord-free, unit-testable business logic.
- `BaseHTTPClient` added; all external API clients standardized with retry/backoff/circuit-breaker.
- Core package split: `bot.py` into focused modules; `models.py` into command, context, embeds, permissions.
- Moderation decomposed into antispam, gatekeeper, lockdown, infraction, and UI modules.
- Games: pure engines extracted (poker, blackjack, roulette, tictactoe, minesweeper).
- Rendering centralized behind `RenderingService`.
- All cogs standardized into `cog/ui/engine/models` structure.

### Added

- pytest harness, CI workflow, and tests for core utilities.
- LOCGClient replaces Marvel and DC clients for comic fetching.

### Removed

- Marvel API client (replaced by League of Comic Geeks integration).
- Dead code, unused imports, and redundant sync/reload commands.

## [1.3.0] - 2026-05-23

### Changed

- Code quality pass: README rewrite, dead code removal, hot path optimizations.
- Python 3.12 type annotation errors resolved across core and cog files.
- Pyright added to the toolchain (basic mode, zero findings).
- Docker build fixed and `poetry.lock` tracked.

### Fixed

- Help command front page and group key handling in pagination.
- Image upload handling and gallery URLs.
- Integer conversion for snowflakes that arrive as strings.

## [1.2.0] - 2026-05-06

### Changed

- Updated to DAVE voice protocol.
- Libraries updated to latest compatible versions.

## [1.1.0] - 2026-01-25

### Changed

- Type hints refined across the codebase (return types, context managers, key definitions).
- Bot event tracking and error handling improved.
- Paginator logic updated to match new discord.py behavior.
- Blackjack embed building and winner check logic reworked.

### Fixed

- Minesweeper: iterative flood-fill prevents `RecursionError`.
- `MissingRequiredArgument` error enhanced for flag parameters.

## [1.0.0] - 2025-01-21

### Added

- Initial Percy v2 release — full rewrite of the Discord bot.
- Docker deployment with multi-service compose (bot, Lavalink, Snekbox, PostgreSQL).
- Music player with Lavalink integration.
- Moderation suite (kick, ban, mute, timeout, warn, purge, auto-mod).
- Leveling system with XP tracking and rank cards.
- Polls with image support.
- Tags system.
- Comics feed (Marvel/DC weekly pulls).
- Code evaluation via Snekbox.
- AniList integration.
- Custom command framework with ANSI signature rendering.
- Help command with categorized navigation.
