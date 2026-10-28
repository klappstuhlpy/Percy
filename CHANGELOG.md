# Changelog

All notable changes to the Percy Dashboard are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Security
- Requests whose path carries an encoded `/`, `\`, `?` or `#` are refused (400). Path parameters were spliced into Percy URLs decoded, so a crafted member id or trigger could reach another guild's endpoints past the manage check.

### Fixed
- Member and bulk actions: a non-object JSON body returns an error instead of crashing the handler.
- "Sign in to track" keeps the page's query string, so login returns to the same filtered page.

## [0.18.0] - 2026-10-28

### Added
- Polls: "Hide results until the poll ends" and a voting role, on create and edit.
- Giveaways: required role, minimum account age and minimum server age; requirements show in the list.
- Audit log: a warn policy card (expiry days, escalation rules by warn count) and a Pardon button on warn cases.

## [0.17.2] - 2026-10-26

### Changed
- Music: uploaded playlist covers load without signing in, by a link nobody can guess, so Discord shows them in `playlist show`.

## [0.17.1] - 2026-10-26

### Added
- Music: an "Add to playlist" button next to the heart in the player bar.

## [0.17.0] - 2026-10-26

### Added
- Music: upload a playlist cover (or use an image link) from a button on the cover itself, or drop an image onto it.
- Music: drag songs from the queue, search, history or any list onto Liked Songs or a playlist in the sidebar.
- Music: a "Trending this week" section with this server's and Percy's most-picked songs.
- Music: the server picker shows every server where Percy is playing.
- The landing page shows the music app.

### Changed
- Music: 24/7 autoplay can start from the song that is playing, without a search or link.

### Fixed
- Music: "Add to playlist" opens the list of playlists again.
- Music: the sidebar's Manage section has its spacing back.
- Requires kls-core v0.15.0 (percy-client 0.15.0) and Percy 2.17.0.

## [0.16.0] - 2026-10-26

### Added
- Music: create, rename, delete and set a custom cover for playlists, and add any song to a playlist from its menu (or remove it from the one you are viewing).
- Music: liked songs and playlist songs show their artist and cover; playlist cards show their cover.
- Tag preview: links straight to images or videos are shown under the message, like in Discord.

### Changed
- Music: "Jump back in" shows each song once, with its most recent play.
- Music: search results and recommendations are kept much longer, so the app loads faster.
- Tag preview: the modal is wider and can be resized, and the message preview follows the dashboard theme.
- Requires kls-core v0.14.0 (percy-client 0.14.0) and Percy 2.16.0.

## [0.15.1] - 2026-10-26

### Fixed
- The dashboard now builds against the tagged kls-core v0.13.0 (percy-client 0.13.0) instead of a local checkout.
- Tab bars lay out correctly on the restyled pages.

## [0.15.0] - 2026-10-26

### Changed
- The whole dashboard and the public pages share one new look: an app shell with a top bar and sidebar, a server picker as a card grid, restyled settings, data, account and catalogue pages, and toasts that distinguish success, info, warning and error. The light theme is supported everywhere.
- Public leaderboards keep their table layout on phones.

### Fixed
- Member detail stat tiles no longer leave extra space below them, and long top-role names wrap instead of being cut off.
- The trailer button on a watch title is a card again instead of an oversized pill.
- The Notifications settings no longer draw a stray frame around the form.
- The Connect Discord page is styled like the other site pages.

### Added
- A music app: player bar, queue, search, play history, lyrics and your liked songs and playlists, under Music in the sidebar. It adapts to phones and narrow windows.

### Removed
- `automod` feature is no longer available.

## [0.14.0] - 2026-10-23

### Added

- The Economy page's shop lists every item with emoji, rarity, category, price, stock, limit, role gate and sale end, and one dialog adds or edits an item with all of Percy's item options. Percy's built-in items are marked and can't be edited.
- Homepage screenshots open large when clicked, and screenshots of different sizes sit centred next to each other.

### Fixed

- Shop errors from Percy (for example a duplicate name) are shown as they are instead of a generic failure.
- Role ids in the shop no longer lose precision in the browser.

## [0.13.0] - 2026-10-23

### Added

- A new homepage: live stats (servers, users, commands, uptime) that disappear instead of showing zeros when Percy is unreachable, feature showcases with screenshots, a three-step start and a directory of every module linking into the commands page.
- The commands page has a category sidebar with counts, search, a slash/prefix filter, expandable entries with copyable usage and examples, aliases, required permissions, cooldowns, nested subcommands and a link per command. It works without JavaScript.
- Public API at `/api/v1` (economy, items, inventory, leveling) with per-server tokens, scopes, rate limits and idempotency keys; reference at `/api`. Tokens are managed on the new **Developers** tab (Manage Server).

## [0.12.2] - 2026-10-21

### Fixed

- The level-up preview really is laid out like a Discord message now: Percy's avatar sits on the left, level with the name and the text. A form-field rule had been stacking it above.
- The level-up placeholders are small inline buttons beneath the input instead of full-width bars.
- The Notifications tabs use the same size, weight and active colour as the other tab bars.

### Changed

- Level-up and tag previews share one renderer that follows Discord's markdown: headings, subtext, lists, `>>>` quotes, code blocks, masked and bare links, timestamps, spoilers, mentions, custom emoji and backslash escapes, styled after Discord's dark theme.

## [0.12.1] - 2026-10-21

### Changed

- The level-up preview now matches a Discord message: Percy's avatar sits beside the name and text, in Discord's font, and the placeholder buttons are small inline chips.
- Tag previews render as a Discord message, keeping headings, lists and spoilers.
- The Change Log moved into the Notifications page, which now has tabs: Feed, Requests, Change log and Settings. The old Change Log address redirects there.
- The setup status card folds away, opening by itself only when something needs attention.
- The logging quick setup uses selectable cards and the same channel picker as the other pages.
- Requester roles are picked from a filterable checkbox list instead of a cramped multi-select.
- The Notifications nav item shows a red count for unread notifications and pending requests.

### Fixed

- The role menu "Single choice" toggle is framed like the other toggles.

## [0.12.0] - 2026-10-21

### Added

- A Notifications page: the server's notification feed, pending action requests with Approve and Deny, recent moderation cases, and the request channel and requester role settings. The sidebar shows a badge for unread items and pending requests.
- A Discord-style preview of the level-up message, with clickable placeholders.
- A Customize dialog for your rank card on the profile page, with a live preview.

### Changed

- The source is regrouped by domain (guild, account, catalogue, ...). No behaviour change.

### Fixed

- The members row menu opens upward when it would be clipped, and only when it fits above.
- The sidebar keeps its scroll position between pages.
- The music panel options and the starboard toggles are laid out more compactly.

## [0.11.0] - 2026-10-21

### Added

- An AutoMod page: one card per Percy preset with an on/off toggle, exempt roles and channels, and the keyword list where the preset uses one. It warns when Percy lacks Manage Server.
- A one-step leveling setup on the Leveling page while leveling is off: where level-ups are announced, and optionally 12 milestone reward roles.
- A one-step Sentinel setup on the Sentinel tab while Sentinel is not set up: create `#verify` and `Unverified` or pick your own, post the captcha message, and optionally start protection.
- "Send test" buttons next to the log, alert and poll channels.
- An Undo button on the "saved" message after saving settings, for 8 seconds.

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
