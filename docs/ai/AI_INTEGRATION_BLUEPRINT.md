# Percy AI Integration Blueprint

> **Status:** living design document for the `feature/ai-native` branch.
> **Governance / process rules:** see [`AI_REWRITE_DIRECTIVES.md`](./AI_REWRITE_DIRECTIVES.md).
> This file is the *what & why*; the directives file is the *how we work*.

## 1. Vision

Move Percy from "another multipurpose Discord bot" to a **server-native AI assistant**:
members express intent in natural language and Percy routes it to the right feature —
moderation, the captcha **Sentinel**, music, polls, giveaways, tags, economy, reminders —
instead of memorising rigid command signatures. Existing slash/prefix commands keep
working unchanged; AI is an *additional* surface, never a replacement that can break.

### Non-negotiable principles

1. **Graceful degradation.** If the AI backend is down, disabled, or returns garbage,
   every feature must still work through its existing commands. AI failure = silent
   fallback, never a broken feature.
2. **Schema-enforced output.** Every routing/extraction call uses Ollama's
   `format='json'` and is validated against a Pydantic/`TypedDict` schema before it
   touches a cog. Unvalidated model output never reaches an execution path.
3. **Opt-in, per-guild.** All AI behaviour is gated behind `GuildConfig` flags,
   **default off**. No guild gets AI behaviour it didn't enable.
4. **Architecture-conformant.** AI code obeys the existing Engine/Service/Client/Cog
   layering (see project `CLAUDE.md`). No new patterns invented for AI's sake.

## 2. Inference Backend — Self-hosted Ollama

**Decision:** all inference runs on a **self-hosted Ollama** instance on the IONOS VPS.
The existing **Groq** cloud client (`app/clients/groq.py`, used by
`app/cogs/automation/assistant.py`) is **removed** as part of this rewrite — Ollama
becomes Percy's single inference path. (Migration steps are tracked in the directives
file, Phase 0.)

- **Endpoint:** `http://127.0.0.1:11434` (OpenAI-compatible routes at `/v1`).
- **Primary model:** `qwen2.5-coder:3b` — strong at JSON-structured command routing.
- **Fast fallback:** `qwen2.5:1.5b` — for high load / latency-sensitive paths.
- **Conversational alt:** `llama3.2:3b` — for the free-form `ask` assistant.

> ⚠️ **Known risk (surfaced, accepted):** the VPS is **6 vCore / 8 GB RAM shared with
> Lavalink + PostgreSQL**. CPU-bound local inference on a 3B model will add real latency
> (expect multi-hundred-ms to seconds per call) and memory pressure. The mitigations in
> §6 (thread pinning, swap, concurrency cap of 1, aggressive caching, async offload) are
> *load-bearing*, not optional. If latency/contention proves unacceptable in practice,
> the provider abstraction in §3 means we can re-introduce a cloud tier without touching
> cog code.

## 3. Architecture — where AI code lives

AI integration maps onto Percy's existing layers; **no new top-level concepts**:

| Concern | Lives in | Mirrors existing |
|---|---|---|
| Ollama transport (HTTP, retries, circuit breaker) | `app/clients/ollama.py` | `app/clients/groq.py` on `BaseHTTPClient` |
| Prompt templates, schemas, routing/extraction logic | `app/services/ai/` | `app/services/` (Discord-free, unit-testable) |
| Intent → cog dispatch glue | service layer + a thin hook in `bot.process_commands` | the existing `feature_flags` / `spam_control` checks |
| Per-guild AI toggles | `GuildConfig.AIFlags` + `guilds` repo | `GuildConfig.AutoModFlags` bitfield |
| Dashboard config of AI | `internal_api/guild.py` + klappstuhl_me `percy/types.rs` | every other guild-config field |

### Layering rules (inherited from project standard)

```text
clients/ollama.py      # transport only — no prompts, no domain logic
        │
app/services/ai/       # PURE: prompts, JSON schemas, validation, routing decisions.
        │              #   NEVER imports discord. Unit-testable with a fake client.
        ▼
cog.py / bot hook       # calls self.bot.ai.<method>(...), executes the result
```

The service is reached as **`self.bot.ai`** (mirroring `self.bot.db` and
`self.bot.render`). Cogs never construct an Ollama client or build raw prompts inline.

### Suggested `app/services/ai/` layout

```text
app/services/ai/
├── __init__.py          # exports AIService, schemas
├── service.py           # AIService: model-tier selection, caching, async offload, fallback
├── router.py            # intent classification → structured RouteDecision
├── schemas.py           # TypedDict/pydantic models for every structured call
├── prompts.py           # versioned system-prompt templates per domain
└── extractors/          # per-domain extraction (giveaway args, poll spec, mod verdict…)
```

## 4. The AI Parser pattern (corrected)

The blueprint's original sketch instantiated `ollama.AsyncClient()` inline. That violates
the client/service split. Instead the transport is a `BaseHTTPClient` subclass and the
service owns prompts + validation:

```python
# app/services/ai/service.py  (sketch — Discord-free, testable)
class AIService:
    def __init__(self, client: OllamaClient) -> None:
        self._client = client

    async def parse(
        self,
        user_prompt: str,
        *,
        schema: type[T],           # the expected JSON shape
        system: str,               # domain system prompt from prompts.py
        tier: ModelTier = ModelTier.FAST,
    ) -> T | None:
        """Return validated structured output, or None on any failure (caller falls back)."""
        cached = self._cache_get(system, user_prompt, tier)
        if cached is not None:
            return cached
        try:
            raw = await self._client.chat(
                model=tier.model,
                messages=[{'role': 'system', 'content': system},
                          {'role': 'user', 'content': user_prompt}],
                temperature=0.0,
                response_format='json',
            )
            parsed = schema.validate(json.loads(raw))   # schema enforcement
        except (HTTPClientError, json.JSONDecodeError, ValidationError):
            return None                                  # graceful degradation
        self._cache_put(system, user_prompt, tier, parsed)
        return parsed
```

Key properties: `temperature=0.0` + `format=json` for determinism, validation before
return, `None` on *any* failure so callers degrade gracefully, and caching keyed on
`(system, prompt, tier)`.

## 5. Answers to the blueprint's open questions

**Q: Optimal way to cache AI responses for repeat/similar prompts.**
Two layers. (1) **Exact-match LRU** keyed on a hash of `(model, system_prompt_version,
normalised_user_prompt)` with a short TTL — reuse Percy's existing `app/utils/cache.py`
memoization decorator rather than a new mechanism. (2) **Semantic cache (optional,
later):** embed prompts with a tiny local embedding model and reuse a cached decision
when cosine-similarity > 0.95. Start with layer 1 only; it kills the dominant cost
(repeated identical mod/router calls) for near-zero complexity. Never cache anything
with per-user/per-message specifics in the key collapsed away.

**Q: Cleanest way to inject the interceptor without breaking slash commands.**
Don't add a second `on_message`. Percy already funnels everything through
`Bot.process_commands` (`app/core/bot.py:357`). There is a **natural seam at line 366**:
when `ctx.command is None` *and* `ctx.invoked_with` is set, the bot currently only calls
`_maybe_suggest_command`. Insert the NL router *there* — i.e. "the user addressed Percy
but matched no command." Slash commands and matched prefix commands never reach that
branch, so they're untouched by construction. Gate the whole branch on the guild's AI
flag. Optionally also trigger on direct @mention of the bot.

**Q: Schema for per-channel vs server-wide AI settings.**
Server-wide toggles live in a new **`GuildConfig.AIFlags`** bitfield (mirrors
`AutoModFlags` exactly: `ai_router`, `ai_moderation`, `ai_music`, `ai_polls`,
`ai_assistant`, …). Per-channel *overrides* go in a small new table
`guild_ai_channel_overrides(guild_id, channel_id, flags_mask, enabled_mask)` queried via
the `guilds` repo and merged over the guild default (channel override wins; absent →
inherit). Resolve with a single cached getter `db.get_guild_ai_config(guild_id)` that
returns the server flags plus the override map, so the hot path is one cache hit.

**Q: Dependency injection for swapping fast/smart models dynamically.**
Model choice is a **`ModelTier` enum** (`FAST = qwen2.5:1.5b`, `BALANCED =
qwen2.5-coder:3b`, `SMART = llama3.2:3b`) passed per-call into `AIService.parse`. The
*caller's domain* picks the tier (routing → FAST, moderation verdict → BALANCED,
free-form chat → SMART), and the service can **auto-downgrade** under load: if the
Ollama health/latency probe (§6) reports pressure, BALANCED requests transparently fall
to FAST. No DI framework needed — it's an enum + a runtime guard inside the one service.

**Q: Best "fallback to default" pattern when JSON parse fails.**
`AIService.parse` returns `None` on *any* failure (transport, non-JSON, schema-invalid).
Callers treat `None` as "AI unavailable" and run the **pre-AI code path** — e.g. the NL
router falls back to `_maybe_suggest_command`, music falls back to a literal string
search, giveaway parsing falls back to the existing flag parser. The user still gets a
working response, just without the AI nicety. One retry at `temperature=0.0` with a
"return ONLY valid JSON" reminder is allowed before giving up.

**Q: Keeping the async event loop unblocked by CPU-bound local inference.**
The HTTP call to Ollama is already `await`-friendly (non-blocking I/O via
`BaseHTTPClient`). The CPU cost lives in the Ollama *process*, not Percy's loop, so the
loop is not directly blocked. Guard against pile-ups with: a process-wide
**`asyncio.Semaphore(1–2)`** in `AIService` (match `OLLAMA_NUM_PARALLEL`), a hard
**per-call timeout** (e.g. 8 s) after which we return `None` and fall back, and **never**
doing JSON/embedding post-processing on large payloads inline — offload any heavy
CPU post-step with `asyncio.to_thread` (the same pattern `RenderingService` uses).

## 6. VPS / deployment tuning (Ollama)

These are operational requirements for the IONOS box, captured for the deploy runbook
(they live in infra, not the Python repo):

**Pin Ollama to 4 of 6 cores, single concurrency** (`sudo systemctl edit ollama.service`):

```ini
[Service]
# Reserve 2 vCores for Lavalink + Postgres + the bot
CPUAffinity=0 1 2 3
Environment="OLLAMA_NUM_PARALLEL=1"
Environment="OLLAMA_MAX_LOADED_MODELS=1"
Environment="OLLAMA_KEEP_ALIVE=10m"
```

> Note: the original blueprint put `numactl --physcpubind=0-3` in an `Environment=` line —
> that's not how affinity is set for a systemd unit. Use `CPUAffinity=` (above) or wrap
> `ExecStart` with `numactl`.

**4 GB swap to keep the OOM-killer off Lavalink/Postgres:**

```bash
sudo fallocate -l 4G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
sudo sysctl vm.swappiness=10   # prefer RAM; swap only under real pressure
```

**Health monitoring:** add an Ollama reachability + latency probe to the bot's health
surface (`app/services/bot_health.py` → exposed via `/api/internal/bot/stats` and the
dashboard). A red "AI engine unreachable" indicator drives the auto-downgrade and tells
operators when local inference is the bottleneck.

## 7. Feature-by-feature target behaviour

Terminology correction up front: in Percy, **"Sentinel" is the captcha/verify gatekeeper**
(`app/cogs/moderation/sentinel.py` + `GuildConfig.AutoModFlags.sentinel`), **not** the
automod. Spam/automod live in `app/core/spam.py` (`bot.spam_control`),
`app/cogs/moderation/antispam.py`, and `app/cogs/automation/automod.py`. The blueprint's
"Sentinal (Automod)" actually spans those. Each feature below names the real module.

| Feature | Module(s) | AI behaviour | Fallback |
|---|---|---|---|
| **Moderation / spam** | `core/spam.py`, `automation/automod.py`, `moderation/antispam.py` | Semantic spam/abuse verdict `{action, reason, confidence}`; AI as a *signal* feeding the existing penalty service, not an autonomous banhammer | Current heuristic spam control |
| **Sentinel (captcha)** | `moderation/sentinel.py` | Optional NL "why do you want to join" screening signal; risk score | Standard captcha flow |
| **Music** | `music/cog.py`, `music/player.py` | Map intent → search/filters (`"something chill for studying"` → query + EQ/filter preset) | Literal string search |
| **Polls** | `polls/` | Extract `{question, options[], duration}` from a sentence | Existing poll flags |
| **Giveaways** | `giveaway.py` | Extract `{prize, winners, duration, requirements}` | Existing flag parser |
| **Tags** | `tags.py` | Fuzzy/semantic tag retrieval + draft tag content | Existing fuzzy match |
| **Reminders** | `reminder.py` | NL temporal extraction (already partly fuzzy) | `timetools` parser |
| **Assistant** | `automation/assistant.py` | Re-point from Groq → Ollama `llama3.2:3b` | "AI unavailable" notice |
| **NL command router** | `core/bot.py` hook + `services/ai/router.py` | "Percy do X" with no command match → route to the right command | `_maybe_suggest_command` |

Moderation guardrail: **AI never takes an irreversible action (ban/kick) autonomously.**
It produces a verdict that feeds the existing human-reviewable penalty/mod-log flow.
High-confidence destructive actions require the same thresholds/escalation as today.

## 8. Dashboard contract

Every per-guild AI toggle is configurable from the klappstuhl_me dashboard, following the
existing "add a configurable field" recipe (project `CLAUDE.md`):

1. `AIFlags` surfaced in `_get_guild_config` / `_patch_guild_config`
   (`app/internal_api/guild.py`).
2. Rust `GuildInfo` gains the fields (`#[serde(default)]`) in `percy/types.rs`.
3. An **"AI" tab** in `templates/percy/guild.html` with per-feature switches and the
   per-channel override editor.
4. `build_patch()` in `dashboard/handlers.rs` handles them; Percy handler invalidates
   `get_guild_config` / `get_guild_ai_config`.

A read-only **AI health panel** (model, latency, cache hit-rate, unreachable flag) is
added to the bot-stats view via `/api/internal/bot/stats`.

## 9. Out of scope / explicit non-goals

- No autonomous destructive moderation actions (see §7 guardrail).
- No training/fine-tuning — prompt-engineering against stock Ollama models only.
- No storing message content for model training; AI calls are transient.
- No replacing the command framework — AI is additive.
