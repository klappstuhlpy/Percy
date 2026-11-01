# Dashboard App-Shell Restyle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry the music player's look (rythm.fm-*style* app shell: near-black canvas, elevated rounded surfaces, one vivid accent, pill controls, dense clean typography) across the whole percy-dashboard, in Percy's own code.

**Architecture:** One design-token layer + a small component library (shell, cards, lists, forms, tabs, toasts, empty states) in `static/css/common/`, extracted from the finished music player. Layouts adopt the app shell; pages are migrated group by group, each verified by screenshots.

**Tech Stack:** Rust/Axum/Askama templates, vanilla CSS/JS, Playwright MCP for visual checks.

**Prerequisite:** the music player rebuild (`feat(music): rebuild music player as full-screen app`) is merged on `feature/music-app`; its CSS is the source of truth for tokens and components.

## Global Constraints

- **Original work only.** Match the *style* (layout proportions, density, rounded elevated surfaces, accent use, motion) — never copy rythm.fm/Spotify HTML, CSS, JS, fonts (no "Saans"), icons, images or copy text. Fonts: Plus Jakarta Sans (display/UI) + JetBrains Mono (code/numbers). Icons: `static/icons/sprite.svg` only.
- Do not edit `kls-core/crates/kls-ui/assets/base.css` (klappstuhl_me shares it); all overrides live in percy-dashboard.
- Dark is primary; `[data-theme="light"]` must keep working on every migrated page.
- No behaviour changes: forms, `PercyUnsaved`, `PercyConfirm.ask`, JS-off behaviour of catalogue pages, routes and handlers stay as they are. Templates may change markup/classes only.
- Public catalogue pages (`/releases`, `/news`, `/watchlist*`, `/comics*`) keep SEO/server-rendered content and the news-ticker rules.
- Each task ends with screenshots (1440, 1024, 390; dark + light) of every page it touched, inspected by the implementer; render via the cfg(test) preview-dump approach used for the music player when auth blocks a live render.
- Commits by the user only, no AI trailer; `cargo fmt && cargo clippy --all-targets && cargo test` green.

## Design Tokens (final values, from the music player)

Source of truth today: the `:root` / `[data-theme="light"]` block at the top of `static/css/music/app.css` ("Percy app tokens"); Task 1 lifts it verbatim into `tokens.css`.

**Font:** Figtree (Google Fonts, weights 400/500/600/700/800), `--app-font: "Figtree", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`. Body 14px/1.45, weight 500, letter-spacing -0.005em; headings 700 at -0.02em. Times and counters use `font-variant-numeric: tabular-nums` (no mono face).

| Token | Role | Dark | Light |
|---|---|---|---|
| `--app-bg` | canvas | `#0a0a0b` | `#f5f5f3` |
| `--app-glow` | soft radial highlight at the top of the canvas | `rgba(235,238,245,0.075)` | `rgba(20,20,30,0.05)` |
| `--surface-1` | nav, panels, menus | `#121214` | `#ffffff` |
| `--surface-2` | cards, hovered rows | `#1a1a1d` | `#f0f0ee` |
| `--surface-3` | pressed / skeleton highlight | `#232327` | `#e6e6e3` |
| `--line` | borders, dividers | `#26262a` | `#e1e1dd` |
| `--text` | primary text | `#f0f1f3` | `#141416` |
| `--text-muted` | secondary text | `#a1a1a8` | `#52525a` |
| `--text-faint` | labels, meta | `#7a7a82` | `#71717a` |
| `--accent` | the one accent (Percy orange stays as `--brand` for logo moments) | `#8351ed` | `#6e41cc` |
| `--accent-hover` | accent hover | `#9469f2` | `#5d34b3` |
| `--accent-contrast` | text on accent | `#ffffff` | `#ffffff` |
| `--shadow-pop` | menus, toasts | `0 16px 40px rgba(0,0,0,0.45)` | `0 16px 40px rgba(20,20,30,0.14)` |
| `--radius-sm/md/lg/pill` | radii | 8 / 12 / 16 / 999 px | same |
| `--dur-fast/base` | transitions | 120 / 180 ms | same |
| `--space-1..8` | spacing | 4 px base scale | same |
| `--ring-focus` | focus-visible ring | `0 0 0 2px var(--app-bg), 0 0 0 4px var(--accent)` | same |

Canvas background: `radial-gradient(ellipse 70% 50% at 50% -8%, var(--app-glow), transparent 70%)` plus a faint accent wash top-right (`color-mix(in srgb, var(--accent) 7%, transparent)`), over `--app-bg`. Panels sit on it at `color-mix(in srgb, var(--surface-1) 62–82%, transparent)` so the glow reads through.

## File Structure

- Create `static/css/common/tokens.css` (tokens above, dark + light), `static/css/common/components.css` (buttons, inputs/selects/toggles, cards, list rows, tabs, chips, tables, modals, toasts, skeletons, empty states), `static/css/common/shell.css` (top bar, left nav, content area, optional right rail, mobile nav).
- Modify `templates/layout/layout.html`, `templates/layout/dashboard_layout.html` (adopt shell), `static/css/common/theme.css` (becomes a thin alias layer → delete once pages migrate), `static/css/common/dashboard.css` / `layout.css` (remove superseded rules per task).
- Move the music player's shared pieces (toasts, skeletons, empty states, list rows) from `static/css/music/*` into `components.css`; player keeps only player-specific CSS.
- `static/js/common/toast.js` — promote the player's toast system to a shared `PercyToast.show(kind, text)`; pages that use `alert()` or ad-hoc banners switch to it.

## Tasks

### Task 1: Tokens + components extracted from the player
- [ ] Write `tokens.css` with the player's actual values; `components.css` with the components listed above (each a documented class: `.btn`, `.btn--primary|ghost|danger`, `.field`, `.toggle`, `.card`, `.card--interactive`, `.row`, `.tabs`, `.chip`, `.table`, `.modal`, `.toast`, `.skeleton`, `.empty`).
- [ ] Add `PercyToast` (`static/js/common/toast.js`), switch the music player to it.
- [ ] Add a cfg(test) "style guide" render (`target/music-preview/styleguide.html`) showing every component in dark + light; screenshot it.
- [ ] Commit `feat(ui): design tokens and component library from the music player`.

### Task 2: App shell for the guild dashboard
- [ ] `dashboard_layout.html` → shell: top bar (logo, server picker pill reused from the player, command-palette search field, notifications bell with badge, avatar menu), left nav (groups Overview/Features/Community/Integrations as now, icon + label rows, active pill, collapsible to icons, mobile drawer), content area with page header (title, description, primary action slot).
- [ ] Music entry opens the player; the player's top bar and the shell's top bar share one partial.
- [ ] Screenshots of 3 representative guild pages. Commit `feat(ui): app shell for the server dashboard`.

### Task 3: Settings pages (forms)
Configuration, Leveling, Economy, Autoresponders, Comics, Temp Channels, AI, AutoMod, Webhooks, Backup & Templates, Starboard, Role Menus, Highlights.
- [ ] Map each settings card to `.card` + `.field`/`.toggle`; consistent section headers; unsaved banner restyled to match toasts. No logic changes.
- [ ] Screenshots per page. Commit `style(ui): settings pages on the component library`.

### Task 4: Data pages (lists/tables/charts)
Members (+ member detail), Statistics, Analytics, Commands, Polls, Gallery, Giveaways, Tags, Emoji Stats, Notifications (+ change log).
- [ ] Tables → `.table`/`.row` with hover, sticky header, empty state; charts restyled to token colours (Percy's chart palette in `templates/theme.py` on the bot side stays separate).
- [ ] Screenshots per page. Commit `style(ui): data pages on the component library`.

### Task 5: Account + site pages
Server picker (`/dashboard` — artwork-card grid like the player's card rows), `/account*`, `/account/subscriptions`, landing/login/error pages.
- [ ] Screenshots. Commit `style(ui): account and site pages`.

### Task 6: Public catalogue pages
`/releases`, `/news`, `/watchlist*`, `/comics*`, news ticker — card rows and list rows from the library; trailer link/inline-iframe behaviour, progress toggles and JS-off forms unchanged.
- [ ] Screenshots (signed-out and signed-in). Commit `style(ui): catalogue pages on the component library`.

### Task 7: Cleanup + docs
- [ ] Delete superseded rules from `dashboard.css`/`layout.css`/`theme.css` and unused page CSS (grep for references first); delete `static/js/guild/player.js` if the public overview now links to the player instead of embedding it.
- [ ] Update workspace `.claude/CLAUDE.md` dashboard section (component library + `PercyToast` conventions), CHANGELOG, percy-docs screenshots if any.
- [ ] Final full screenshot sweep. Commit `chore(ui): remove superseded styles; document the component library`.

## Review Focus

1. Light theme on every migrated page (contrast of muted text and accent buttons).
2. Long values (server names, track titles, role names) truncate instead of breaking layout at 390 px.
3. Keyboard focus visible on every interactive element after restyle.
4. JS-off catalogue pages still render and their forms still post.
5. Pages with `PercyUnsaved` still raise the unsaved banner and save correctly after markup changes.
