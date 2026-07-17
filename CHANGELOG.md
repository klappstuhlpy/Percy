# Changelog

All notable changes to Percy are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Timeout duration validation helper for the internal API.
- `codeimage`, `chart`, and `mdpdf` render commands via klappstuhl.me integration.

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
