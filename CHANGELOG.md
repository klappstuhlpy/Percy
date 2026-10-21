# Changelog

All notable changes to Percy are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed

- Every comic refresh logged a failed database query for each issue whose title clashed with
  another issue's. The refresh tried to reset the issue's link back to the plain title each time.
  An issue's link is now kept once it is stored, so the refresh is quiet and comic links never change.

## [2.8.0] - 2026-10-21

### Added

- Percy's private log channel now gets a card when Percy starts (version, server and
  member counts, latency, startup time, and whether music, AI and the database are up)
  and when it shuts down (uptime), plus a daily summary at 08:00 UTC: servers joined
  and left, commands run and failed, the five most used commands, moderation cases,
  uptime, average latency and memory/CPU use. Counts only, never who.
- The log channel says once when music (Lavalink), the AI engine, the database or a
  news or comic feed goes down or the gateway latency stays high, and once when it
  recovers. Nothing repeats while the state stays the same.

### Changed

- Gateway messages in the log channel are short readable lines such as
  "Reconnected after 12 s" instead of raw log lines with the session id, and a burst of
  reconnects is folded into one line a minute with a count.

### Fixed

- After Percy lost the **Ban Members** permission, got it back and lost it again,
  the second loss went unreported for up to five minutes. Every loss is reported
  again once Percy has had the permission back.
- A member who got past Sentinel and whom Percy could not kick or ban was then
  skipped by raid and mention-spam protection for five minutes, and their failed
  raid ban was never reported. Those checks now handle them as usual.
- Ending Sentinel any way other than **Stand Down** (Deactivate in the menu, the
  dashboard, or deleting the verification channel or message) left the released
  members marked as raiders, so chatting freely could still get them banned. Every
  way of ending Sentinel now clears the mark, for everyone Sentinel flagged, and an
  open Ban All Raiders alert no longer offers those members.
- When raid or mention-spam protection tripped on the server owner, the alert asked
  you to check Percy's role position. It now says the server owner can never be
  banned.
- When Sentinel could not give or take the lockdown role because the role sat above
  Percy's, held members stayed stuck until the next join, verification or restart,
  even after you fixed the role. Percy now retries by itself for about an hour.
  An error on Percy's side there is now reported to your moderators and retried
  the same way, instead of quietly stopping the queue.
- Lockdown-role problems Sentinel ran into right after a restart are reported again;
  before, the alert could be dropped while Percy was still starting up.
- On the dashboard, saving only the Sentinel rate or bypass action failed when the
  unchanged lockdown or starter role had been moved above Percy's role since. The
  save now goes through and the problem is shown as a warning.
- A dashboard save with an invalid Sentinel setting failed as a whole even though
  the settings saved before it in the same request had already been applied. The
  Sentinel part is now reported as failed on its own.
- A dashboard save with an invalid config value (such as a bad mention count) now fails
  before anything is applied, instead of after earlier changes were already saved.
- Picking a new lockdown role on the dashboard while Percy was still loading the
  member list could let through a role members already wear. Percy now asks you to
  try again in a minute.
- A `lockdown for` shorter than a minute still ended on time after `lockdown end`,
  and unlocking one of its channels on the dashboard left the timer pending, so it
  could later end a new lockdown of that channel early. A timed lockdown now only
  ever ends the channels it still covers.
- When `lockdown end` or a timed lockdown could not unlock some channels, those
  channels stayed locked until someone tried again. Percy now retries them by
  itself, and a timed lockdown that ends while Discord has the server unavailable
  is retried instead of dropped.
- Shutting Percy down could hang while it waited on a database connection the
  timer loop kept open.
- In Docker, every log line was printed twice. Each line now prints once, and numbers
  in Docker now use thousands separators.
- The AI moderation flag buttons now follow the same role rules as the commands, and
  Warn no longer requires Percy to outrank the member, as with the `warn` command.
- Long lists of skipped members from `multiban`, `multimute` and `multiunmute` are now
  cut off with an "and N more" count instead of failing to send past 2000 characters.
- A warning with a very long reason no longer fails to reach the member by DM, whether
  it was given by command or from the dashboard.
- On the dashboard, a moderator who was not cached by Percy (common in large servers)
  was refused with "not a member"; Percy now looks them up first.
- On the dashboard, a role change with a malformed or unknown role id now fails with a
  clear error instead of silently ignoring that role.
- On the dashboard, you can purge your own messages again.
- Timing out an administrator from the dashboard now says Discord does not allow it,
  rather than claiming Percy lacks permissions.

## [2.7.0] - 2026-10-20

### Added

- The `/moderation sentinel` menu has a **Sync Permissions** button. It hides every
  channel and category from the lockdown role (voice, stage and forum channels
  included) and opens only the verification channel, which @everyone can no longer
  see. Run it again whenever you add channels; channels that are already set up
  correctly are left alone, so a re-run is quick.

### Changed

- Sentinel verification asks for the captcha in a pop-up form: press **Enter Code**
  on the captcha card and type the characters there. Before, the answer had to be
  typed as a chat message, so Percy opened the verification channel to the member
  while they solved it. You now have five minutes instead of 90 seconds.

### Fixed

- Listen-together stopped following the host after their first track change: clearing
  the queue for the new track also forgot whom Percy was listening along with. Percy
  now keeps following the host track after track.
- Raid protection never banned anyone for spamming: the message rate limits behind it
  forgot every message as soon as they recorded it, so the same text posted again and
  again, or a flood of messages from one account, never tripped them. Copy-paste
  raids and message floods are caught again.
- A raid-protection ban now posts an alert in the server's alert channel saying who
  was banned, in which channel and why. Before, the ban was silent unless the audit
  log happened to be set up. The alert never quotes the spam itself.
- Raid protection could ban members who never spammed. When a few people posted the
  same image, sticker or "gg" in a channel, or a busy channel had many new members
  chatting, whoever sent the message that crossed the limit was banned, and so was
  almost every poster after them. A busy channel now only tells Percy a raid may be
  happening: it bans just the members who sent several of those messages themselves,
  and messages without text or with only a few characters are never compared. One raid
  now bans each member once with one alert, instead of re-banning and re-alerting on
  nearly every message.
- When raid or mention-spam protection could not ban someone (Percy is missing the Ban
  Members permission, or the member's role is above Percy's), nothing told the
  moderators. Percy now posts an alert naming who it could not ban and what to fix.
- Raid alerts for large raids were too long for Discord and silently never arrived.
  They are now split across several messages.
- The Massban button on a raid alert could ban members flagged hours earlier in a quiet
  server, because expired flags were still listed. Only current flags are used now.
- A server with alerts on but Sentinel off got no alert at all when dozens of accounts
  joined at once; the join-burst alert only ran while Sentinel was on. It now runs
  whenever alerts are on and no Sentinel auto-trigger is set, and its **Ban All
  Raiders** button bans the members that alert listed. Before, the button found
  nobody, or banned an older group instead. Removing the auto-trigger rate no longer
  keeps these alerts switched off until a restart.
- Members Sentinel held when its auto-trigger fired could talk in any channel the
  lockdown role did not cover, and join voice, without the configured kick or ban.
  They are now treated like every other member waiting to verify.
- Mention-spam protection now follows one rule: a member is banned when one message,
  or all their messages within 15 seconds, mention more than the configured number of
  users. The alert reports how many mentions they actually sent. The dashboard could
  set a threshold of 1 or 2 (or a negative number, which banned every poster), and a
  number above 100 failed with an internal error. The threshold is now 3 to 100, on
  the dashboard and with `moderation mentions` (0 turns it off on the dashboard), and
  a low value saved earlier is treated as 3.
- Moderators who have Manage Messages only in some channels were auto-banned for
  pinging or posting quickly there. They are now exempt in those channels. Threads and
  forum posts inside an ignored channel are ignored too.
- Turning off audit logging or alerts during a raid wiped Percy's raid tracking, so an
  open alert's Ban All Raiders button found nobody. Only turning off raid protection
  or Sentinel resets it now.
- Sentinel could stop giving and taking its lockdown role for good. A held member who
  left or was banned (after **Ban All Raiders**, for example) stopped the role queue,
  so later joiners were never locked and members who verified or were released kept
  the lockdown role. Clicking anything in the setup menu, deactivating Sentinel, or a
  restart after turning Sentinel off could also drop the queued work. Percy now skips
  members who are gone, keeps working through the queue, and finishes interrupted
  work after a restart even when Sentinel is off.
- When Percy cannot give or take the lockdown role (its own role sits below it, or it
  lost Manage Roles), it now alerts the moderators instead of only logging it, and
  retries on the next join or verification.
- Deleting the verification message or channel, or switching to a different lockdown
  role, left every held member locked with no way to verify. Those members now lose
  the old lockdown role. `moderation disable sentinel` now says how many members are
  having it removed, instead of always reporting none.
- A member who passed the captcha could still be treated as unverified, and kicked or
  banned for talking, when the Sentinel settings changed while they solved it.
- Picking an existing role as the Sentinel lockdown role only offered to hide the
  channels that role already could not see, so it usually hid nothing and said nothing
  about it. Percy now always offers to hide every channel and category except the
  verification channel, and creating a new role no longer hides the verification
  channel from it. @everyone and integration-managed roles are refused.
- Anyone who could see a Sentinel raid alert could press **Stand Down** and release
  every held raider. It now needs Ban Members and Manage Messages, the same as the
  sentinel menu.
- A member who still had the lockdown role from an earlier hold and opened the captcha
  without finishing it could be kicked or banned for talking afterwards. Opening the
  captcha no longer holds them again; solving it still removes the role.
- A member Sentinel held during a join burst stayed marked as a raider for up to 45
  minutes after passing the captcha or being released with **Stand Down**, so raid
  protection could still ban them for mentioning someone, Percy ignored their
  commands, and **Ban All Raiders** still offered them. Verifying or being released
  now clears that mark.
- When Percy could not kick or ban a member who bypassed Sentinel, it tried again on
  every message they sent. It now tries once every few minutes, and not at all when it
  lacks the Kick or Ban Members permission; the alert still tells your moderators what
  to fix.
- The dashboard's Sentinel settings now follow the same rules as the Discord menu.
  Lockdown and starter roles are checked (not @everyone, not managed, below Percy's
  role, not the same role), and the lockdown role and verification channel cannot be
  changed while Sentinel is running. A new lockdown role or channel is synced
  automatically, and Percy alerts the moderators when channels could not be hidden.
  Because that sync does not ask first, the dashboard refuses a lockdown role that
  members already have, and it never hides the verification channel from @everyone
  itself: it tells you when that channel is still public.
  The auto-trigger rate must be two numbers above 0, and an invalid rate can no longer
  break moderation for the server. Clearing the rate or the starter role works.
  Saving no longer leaves the old verification card behind, and Sentinel cannot be
  switched on while Sentinel protection is off for the server.
- A muted member who rejoined got the mute back but skipped Sentinel: they were never
  held and never had to verify. They are now held like every other new member.
- Percy's command log no longer records the full text a command was typed with,
  only which command ran.
- An error raised by a command after its arguments were read ("I currently am unable
  to join your voice channel", for example) crashed the error handler, which then
  posted a Python traceback into the channel. The error is shown as a normal message
  again.
- When Percy failed to start (a rejected Discord token, for example), shutting down
  crashed with a `RecursionError` that hid the actual error, and the process hung
  until Docker killed it. The real error is logged again and Percy exits.
- After a brief network drop, Percy could lose its connection to Lavalink without
  noticing, and every attempt to play music failed with "I currently am unable to join
  your voice channel" until a restart. Percy now checks the connection every 30
  seconds and reconnects on its own.
- `lockdown end` only worked when run from inside a locked channel, so ending a
  lockdown from a mod channel said "There is no active lockdown". It now ends every
  lockdown in the server from anywhere, and also cancels a pending `lockdown for`
  timer, which used to end a later lockdown of the same channels early.
- A channel Percy could not unlock (from the command, the timer or the dashboard) lost
  its saved permissions and vanished from the locked list, so it stayed read-only for
  good. It now stays listed as locked with its original permissions kept, ending the
  lockdown again retries it, and a timed lockdown that fails to unlock a channel
  alerts the moderators. A locked channel missing from Percy's cache is unlocked too,
  instead of being skipped.
- Locking a voice channel now stops people joining and speaking in it; before it only
  locked the voice channel's text chat. The lockdown summary counts the channels you
  asked for ("2/3 channels"), not the failures, and the dashboard answers a malformed
  channel id with a clear error instead of a server error.
- `purge --require any` deleted every unpinned message in range, whatever filters you
  gave it, because "not pinned" was counted as one of the filters. It now deletes only
  messages matching one of your filters, and a `purge` with no filters asks for
  confirmation again. A purge that runs out of time now says so instead of failing
  with an error.
- `/kick`, `/softban` and `/tempban` always failed as slash commands, and banning by
  ID (`ban`, `tempban`, `multiban`) failed for everyone except the server owner. Both
  work again. `kick` and `softban` refuse someone who is not in the server, so a
  softban can no longer be used to lift an existing ban.
- Every moderation action now follows the same hierarchy rule: you cannot act on
  yourself, Percy or the server owner, and your top role must be strictly higher than
  the member's (the server owner is exempt). Bans and kicks also need Percy's top role
  above the member's; mutes only need the mute role below Percy's top role, so a
  low-ranked Percy can still mute. Before, `mute`, `unmute` and `warn` checked no
  hierarchy at all, so a helper could mute a senior moderator or mute Percy, members
  with the same top role could act on each other, and the raid alert's **Ban All
  Raiders** button banned raiders who outranked the person clicking it. A muted
  moderator can no longer unmute themselves. Members you cannot act on are listed as
  skipped.
- `mute` did nothing at all when your top role was not above the mute role. It now
  says why, and the server owner can always mute. A mute role level with Percy's own
  top role is now refused with a clear message instead of failing with a permission
  error. `slowmode` no longer reports a successful change as an error.
- An old tempban or tempmute could undo a newer action. Unbanning someone early
  (by command or from the dashboard) left their tempban timer running, so if they
  were banned for good later, the old timer lifted that ban on its original date. The
  same happened to an indefinite mute applied after a tempmute whose role had been
  removed by hand. Banning, unbanning, muting or unmuting by command or from the
  dashboard now cancels the old timer.
- A tempban or tempmute that could not end on time (Discord outage, missing
  permission) used to stay in place for good with no trace. Percy now retries it for
  about a day and tells your alert channel when a permission is missing. After the
  mute role is changed with **Merge**, a tempmute also takes the new role away when
  it ends, instead of leaving the member muted.
- Members muted in the last few seconds before Percy restarted could leave and rejoin
  without the mute role. Pending mutes are now saved before Percy shuts down.
- When the mute role was deleted while Percy was offline, `mute`, `unmute`, `tempmute`
  and `selfmute` now say it no longer exists and to set up a new one with `muterole`,
  instead of failing with an error or saying it was never set up. `tempmute` only needs Percy
  to have Manage Roles, like `mute`. A `tempban` DM no longer names the moderator, and
  if the ban then fails the member is told to disregard it. Unbinding the mute role
  now says plainly that muted members keep the role.
- Many moderation actions left no case, so `cases <user>` showed a clean record for
  members who had been banned. Bans from `multiban` and the Sentinel **Ban All
  Raiders** button, automatic raid and mention-spam bans, kicks and bans for bypassing
  Sentinel, and tempbans and tempmutes ending on their own are now all recorded.
  Automatic ones are filed under Percy's name with the reason it acted on. When Percy
  cannot kick or ban a member who bypassed Sentinel, your alert channel is told once
  instead of the failure going unnoticed.
- A softban whose second step (lifting the ban) failed reported failure while the
  member stayed banned, with no case. Percy now says the member is still banned, by
  command and on the dashboard, and records the ban.
- A warning or reason longer than about 1,000 characters broke that case: its modlog
  post never appeared and `case N` failed every time. The reason is now shortened in
  the case card. Deleting the newest case no longer hands its number to the next one,
  so old modlog posts never point at the wrong case. Requires migration V48.
- The dashboard let anyone with Manage Server use every moderation action, whatever
  their own permissions and rank. Someone without Ban Members could ban an
  Administrator ranked above them, kick themselves, or give themselves any role below
  Percy's, including an admin role. Dashboard actions now need the same permission as
  the matching command (Ban Members to ban, Manage Roles to mute or change roles, and
  so on) and follow the same hierarchy rule, and the dashboard only lets you give or
  take roles below your own top role. Roles managed by an integration and @everyone
  are refused with a clear message instead of an internal error.
- A dashboard mute with a duration out of range applied the mute and then failed,
  leaving a permanent mute with no case; a zero or negative duration lifted the mute
  again within seconds. Durations must now be between one minute and a year, checked
  before anything changes. Dashboard tempmutes are no longer mistaken for self-mutes.
- New cases never appeared live on the dashboard's audit log page, and the server
  overview's recent-case count failed to load. Both work now, and the live feed no
  longer repeats the last case on every refresh.
- A bulk action with a malformed id failed halfway, after some members had already
  been banned or kicked, without saying who. Every id is now checked first. Purging
  the messages of someone who has left the server now works too.

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
