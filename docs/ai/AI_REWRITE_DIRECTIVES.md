# Percy AI-Native Rewrite — Directives

> **Branch:** `feature/ai-native` (off `master`). All AI rewrite work happens here and is
> merged to `main`/`master` only when complete and covered. **Never** commit AI-rewrite
> work directly to `master`.
>
> **Companion docs:** [`AI_INTEGRATION_BLUEPRINT.md`](./AI_INTEGRATION_BLUEPRINT.md) (the
> design) and the project/sub-project `CLAUDE.md` files (the existing architecture this
> must obey). This file governs *how* the rewrite proceeds.

## 0. Prime directives

1. **Additive, not destructive.** Existing commands keep working at every commit. AI is a
   new surface layered on top. A user with AI disabled sees today's Percy, unchanged.
2. **Graceful degradation is a hard requirement, not a nice-to-have.** Any AI failure
   (down, disabled, timeout, invalid JSON, schema-invalid) falls back to the pre-AI code
   path. A failing model must never break a feature or spam errors at users.
3. **Default off, per-guild opt-in.** Every AI behaviour is gated behind a `GuildConfig`
   AI flag, default `0`. Ship dark; let guilds turn it on.
4. **Obey the existing architecture.** Transport → `app/clients/`; pure logic →
   `app/services/ai/` (no `discord` imports, unit-testable); SQL → `app/database/repositories/`;
   cogs stay thin controllers calling `self.bot.ai` / `self.bot.db`. No new patterns.
5. **Schema-validate every structured call** before it reaches a cog. Unvalidated model
   output never drives an action.
6. **The branch stays green.** `poetry run pytest`, `poetry run ruff check .`, and
   `poetry run pyright` must pass on every phase boundary before moving on.

## 1. Rollout model — incremental, flag-gated

Build one AI core, then convert features one at a time. Each phase is independently
reviewable and (once the core lands) independently mergeable to `main`. Order:

```text
Phase 0  ✅ DONE   Groq → Ollama; remove Groq; AIService + OllamaClient + health probe + beta SSH tunnel
Phase 1  ✅ DONE   GuildConfig.AIFlags + per-channel overrides (V32) + internal API + dashboard "AI" tab
Phase 2  ✅ DONE   NL command router: process_commands seam → services/ai/router.py, gated on AIFlags.router, confirm-to-run
Phase 3  ✅ DONE   AI moderation verdict (services/ai/moderation.py) → flag-for-review via send_alert, gated on AIFlags.moderation; never auto-punishes
Phase 4  ✅ DONE   Music intent: services/ai/music.py (NL → query + filter) + `vibe` command, gated on AIFlags.music
Phase 5  ✅ DONE   Polls + giveaways: services/ai/events.py (NL → structured fields) + `polls ask` / `giveaway quick`, gated on AIFlags.polls/giveaways
Phase 6  Tags + reminders     semantic retrieval / NL temporal extraction
Phase 7  Assistant + polish   ask → Ollama, caching, semantic cache (optional), docs
```

A phase is **done** only when: behaviour works with the flag on, the feature is unchanged
with the flag off, tests cover both paths, and the three checks in §0.6 pass.

## 2. Phase 0 contract — ✅ DONE (commit `[ai-phase-0]`)

This unblocks everything else and is the riskiest (it removes Groq).

- [x] `app/clients/ollama.py` — `OllamaClient(BaseHTTPClient)`. Talks to Ollama's native
      `/api/chat` (non-streaming) with `format='json'` for structured calls + a `version()`
      reachability probe; inherits the 429/backoff/breaker resilience. Exported from
      `app/clients/__init__.py`. *(Used the native endpoint rather than the OpenAI-compat
      route — cleaner `format`/`options` handling.)*
- [x] `app/services/ai/` — `AIService` (reached as `self.bot.ai`), `ModelTier` enum,
      `schemas.py` (Parsable + `require_*`), `prompts.py`, exact-match caching
      (`ExpiringCache`), per-call `asyncio.timeout`, `asyncio.Semaphore` concurrency cap,
      BALANCED→FAST auto-downgrade. Exported from `app/services/__init__.py`. Wired
      `self.ai = AIService(...)` in `Bot.setup_hook` next to `self.render`.
- [x] **Remove Groq:** deleted `app/clients/groq.py`, dropped it from
      `app/clients/__init__.py`, removed the `groq` namespace from `config.py` and the
      `GROQ_*` entries from `.env.example`/README/PRIVACY_POLICY/SELF_HOSTING_GUIDE, and
      re-pointed `app/cogs/automation/assistant.py` to `self.bot.ai` (Ollama smart tier).
- [x] Config: `ollama` namespace in `config.py` (`OLLAMA_ENABLED`/`OLLAMA_HOST`/
      `OLLAMA_*_MODEL`/`OLLAMA_TIMEOUT`/`OLLAMA_MAX_CONCURRENCY`) + `.env.example`.
- [x] Health: `AIService.health()` probe (cached ≤30 s) surfaced in the `ai` block of
      `/api/internal/bot/stats`. *(Probe lives on the service, which owns the client and
      counters — a network probe doesn't belong in the pure `bot_health.py`.)*
- [x] Tests: `tests/test_ai_service.py` — 20 tests over a fake client (parse-success,
      invalid-JSON→`None`, schema-invalid→`None`, non-object→`None`, timeout/transport→`None`,
      cache hit, auto-downgrade, health states). Full `pytest` green; `ruff`/`pyright` clean.

## 3. Config & schema rules

- New AI toggles go in a **`GuildConfig.AIFlags`** bitfield mirroring `AutoModFlags`
  (`app/database/base.py:893`). Follow the same `@flag_value` pattern.
- Per-channel overrides: new migration `migrations/V<N>__ai_channel_overrides.sql`
  (`db migrate -r "ai channel overrides"`; never edit an applied migration). Reads go
  through the `guilds` repo; expose a cached `db.get_guild_ai_config(guild_id)` and
  invalidate it on every mutation (the repo method does this internally).
- Cross-project: any new guild-config field is mirrored in `internal_api/guild.py`
  (`_get_guild_config` allow-list + `_patch_guild_config`) **and** klappstuhl_me
  `percy/types.rs` (`#[serde(default)]`) + `guild.html` + `dashboard/handlers.rs`. Run
  both test suites (`poetry run pytest`, `cargo test`) after contract changes.

## 4. Safety guardrails (moderation especially)

- AI produces **signals/verdicts**, not autonomous irreversible actions. Ban/kick stays
  behind the existing thresholds and human-reviewable mod-log flow.
- Every structured call has a confidence field; low confidence → no action, log only.
- No message content is persisted for training. AI calls are transient.
- Prompt-injection awareness: treat user message text as untrusted data inside prompts;
  never let it redefine the system instruction or the JSON schema.

## 5. Commit / PR discipline

- Conventional-commit messages, one logical change per commit, phase tag in the body
  (e.g. `[ai-phase-0]`). Co-author trailer as per repo convention.
- Open a **draft PR** from `feature/ai-native` → `main` early; check off the §1 phases in
  its description as they land. Keep it rebased on `master`.
- Do not merge to `main` until: all in-scope phases done, both test suites green, the
  dashboard "AI" tab works end-to-end, and graceful degradation is verified (flag off =
  identical behaviour; backend killed = clean fallback).

## 6. Definition of done (whole rewrite)

- [ ] Every feature in Blueprint §7 has an AI path **and** a verified fallback.
- [ ] All AI behaviour is per-guild flag-gated, default off, dashboard-configurable.
- [ ] Groq fully removed; Ollama is the sole inference path.
- [ ] `pytest` / `ruff` / `pyright` green; new services/clients have tests mirroring the
      nearest existing test module.
- [ ] VPS runbook (Blueprint §6) applied: Ollama pinned, swap configured, health probe live.
- [ ] Blueprint and both `CLAUDE.md` files updated to describe the shipped AI layer.
