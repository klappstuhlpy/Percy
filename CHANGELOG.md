# Changelog

All notable changes to the Percy Dashboard are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.10.0] - 2026-10-21

### Added

- A setup status at the top of the server settings page: for every feature in use, whether Percy can post in the configured channels and give out the configured roles, with the exact missing permission and a link to the fix.
- A one-step logging setup on the Moderation tab: pick the logs and one channel, or let Percy create a private `#percy-logs`.
- A change log page: who changed what on the dashboard and when, for 90 days. Only the names of changed fields are kept.
- Rank card customization on your profile page (theme, accent colour, progress bar, font) with a live preview, and a server default with a lock on the Leveling page.
- The level-up message shows a live preview, clickable placeholders and a "Send test message" button.
- Role menu, starboard and reminder pages: post and edit role menus, set up the starboard and see the most-starred messages, and cancel your own reminders from your profile.
- Tags can be created and their content edited on the Tags page.
- Channel and role pickers mark channels Percy can't post in and roles it can't give out, and long lists get a filter box.

### Changed

- Every confirmation says what will happen, to what, and whether it can be undone, instead of a browser "Are you sure?". Kicking or banning five or more members at once asks you to type the number.

### Fixed

- The changelog page showed only the first line of longer entries and printed `**` around bold words.

## [0.9.0] - 2026-08-20

### Added

- A page for every film and show, with its cast and crew, where it sits in the watch order, where to stream it, and a trailer that stays a link until you click it — so nothing loads a player or sets a cookie on its own.
- A page for every person, with their filmography grouped by the role they played and linked through to the universes they appear in.
- Read and watched marks on every catalogue surface — the releases page, the comic archive, a watch order and any detail page — each with an unmark beside it. A watch order also shows how far through it you are, and `j`/`k`/`w` step through a list and mark without the mouse.
- `/releases` is now a dated timeline grouped by month, with poster art and a closing bucket for titles announced without a date.
- A news page, filterable by subject, source and search, plus a headline strip on the releases, comics and watch-order pages.
- Comic pages carry the dates a release was first tracked and last updated, and a series shows its most recent issue.
- Universe pages show their key art, and a season now names the series it belongs to and links back to it.

### Changed

- A watch order takes its figures from Percy instead of counting rows itself, so the header and the list can no longer disagree. A multi-season series renders as a heading over its seasons rather than as an entry of its own — the seasons are what you mark.
- Hours in a watch order are the rounded figure Percy already computes, rather than a truncated division.

### Fixed

- The position on a title page counts entries rather than rows, so it no longer reads "5 of 5" on a page whose universe header says four. Stepping through with previous and next no longer stops on a series heading, which was never something you could watch or mark.
- A comic series' "latest issue" on its own page and on the index can legitimately differ — one is per-brand and cached, the other is live and unscoped — and the code no longer claims otherwise.

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
