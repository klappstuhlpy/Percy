# Changelog

All notable changes to the Percy Dashboard are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.6.0] - 2026-07-17

### Added

- Account page (`/account`) for managing global user settings and personal data independent of any guild.
- Data export and deletion are now correctly scoped as account-level operations, accessible from any session without requiring guild membership.

### Changed

- The "Your Data" section on the per-guild profile page now links to the account page instead of embedding guild-scoped data controls that acted globally.

### Fixed

- Data export and deletion no longer require guild membership to access — previously these account-wide operations were gated behind a specific guild, which was misleading and could block legitimate GDPR requests.

## [0.5.3] - 2026-07-12

### Added

- Global unsaved-changes tracker — one floating banner saves or discards all dirty sections at once, replacing per-card Save buttons.
- User page template now carries navigation and title fields for consistent page chrome.

### Changed

- Percy API error handling centralized through `percy_message` for consistent, user-friendly error reporting across all endpoints.

## [0.5.2] - 2026-07-09

### Fixed

- Logout now clears session cookies at all scopes (host-only and domain-scoped), preventing stale domain-scoped cookies from keeping users signed in after logout.

## [0.5.1] - 2026-07-09

### Added

- Dashboard sidebar with view transitions, responsive collapsing, and smooth scroll behavior.
- Guild pages now use a dedicated dashboard layout with a persistent sidebar navigation.

### Fixed

- Sidebar no longer drifts on scroll or overlaps the footer separator.
- Sidebar height no longer overflows viewport on pages with short content.

## [0.5.0] - 2026-07-07

### Added

- Analytics page with time-series charts (commands, XP, members) and headline summary tiles.
- Outbound webhooks page — create, edit, test, and view delivery history for event subscriptions.
- Backup & Templates page — export/import guild config and browse/publish shareable templates.

## [0.4.0] - 2026-07-07

### Added

- Complete standalone dashboard binary, decoupled from the klappstuhl_me monolith.
- Discord OAuth2 login with stateless HMAC-signed cookie sessions.
- Guild list with managed, public (read-only), and "Add Percy" cards.
- Full guild configuration UI: all feature tabs (leveling, economy, music, commands, polls, giveaways, tags, autoresponders, comics, temp-channels, highlights, emoji stats).
- Members tab with search, role filter, and per-member action modals (kick, ban, mute, timeout, warn, purge).
- Moderation audit log with case creation, editing, deletion, bulk actions, and CSV exports.
- Config, AI, and Sentinel mutation forms proxied through Percy's internal API.
- Public overview page for members without manage access (server stats, leaderboard, music player, polls, economy).
- Personal profile page (`/me`) with settings, presence timeline, name/avatar history, and data export/delete.
- Public leaderboard page with vanity slug support.
- Public site pages: commands reference, changelog, privacy policy, terms of service.
- Single sign-on handoff from klappstuhl.me (optional, session sharing via signed token).
- Guild image gallery with drag-and-drop upload.
- Client-side table sorting across all data tables.
- Open Graph and Twitter card metadata for rich link previews.
- Docker build and compose configuration.

### Fixed

- Poll creation now uses a single multipart request (previously failed with 405 on image upload).
- Discord API pinned to v10 with tolerant permissions decoding (handles both string and integer formats).
