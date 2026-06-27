# AI Moderation — what it does, where, and when it acts

> **TL;DR:** AI moderation is a **signal, not an enforcer**. When enabled, Percy asks the
> local model whether a message looks harmful and, if so, posts a **review alert** for a
> human moderator. It **never** deletes, mutes, kicks, or bans on its own. It is **off by
> default** and gated per-guild (and per-channel).

## Where the code lives

| Concern              | File                                                   | What it does                                                                                                                                     |
|----------------------|--------------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------|
| Verdict logic (pure) | `app/services/ai/moderation.py`                        | `ModerationVerdict` schema + `ModerationAssessor`. Classifies text via `self.bot.ai`, returns only verdicts worth surfacing. No Discord imports. |
| Integration          | `app/cogs/moderation/cog.py`                           | `_schedule_ai_moderation()` and `_maybe_ai_moderate()`, invoked from the `on_message` listener. Owns gating, cooldown, and the alert.            |
| Gate flag            | `app/database/base.py`                                 | `GuildConfig.AIFlags.moderation` (+ per-channel overrides via `db.get_guild_ai_config`).                                                         |
| Inference            | `app/services/ai/service.py` → `app/clients/ollama.py` | Runs the model (BALANCED tier) with timeout + graceful `None` on any failure.                                                                    |

## What it does, step by step

1. The existing `on_message` listener already filters out messages that should never be
   moderated (see "When it runs" below).
2. For a surviving message it calls `_schedule_ai_moderation(message, config)`, which spawns
   a **background task** so the AI call never blocks the spam/raid checks.
3. The task (`_maybe_ai_moderate`) re-checks the gates, then calls
   `ModerationAssessor.assess(content)`.
4. `assess` sends the message text to the model (system prompt = a strict content-moderation
   classifier, BALANCED tier) and parses a JSON verdict:
   `{flagged: bool, category: str, reason: str, confidence: float}`.
5. If — and only if — the verdict is **flagged**, the category is **not `none`**, and the
   **confidence ≥ 0.7**, Percy posts a review alert through the guild's existing alert flow
   (`GuildConfig.send_alert`): author, channel, category, confidence, the model's short
   reason, and a jump link. The alert text explicitly says *"review and action manually if
   warranted."*
6. Anything else — not flagged, low confidence, model down/disabled/timeout, invalid JSON —
   results in **no action at all** (graceful degradation; the pre-AI behaviour is unchanged).

## When it takes action — exact criteria

An alert is posted **only when every one of these holds**:

| Gate        | Condition                                                                                   | Where                                  |
|-------------|---------------------------------------------------------------------------------------------|----------------------------------------|
| Feature on  | `AIFlags.moderation` enabled for the guild (and not disabled for the channel via overrides) | `is_enabled('moderation', channel_id)` |
| Engine up   | `self.bot.ai.available` (enabled, not circuit-broken)                                       | `_schedule_ai_moderation`              |
| Real target | Not a bot, not the owner, not staff with `manage_messages`, not a system message            | `on_message` pre-filters               |
| Not exempt  | Channel / author / author roles are **not** in `safe_automod_entity_ids`                    | `on_message` pre-filters               |
| Substantial | Message content length ≥ `AI_MOD_MIN_LENGTH` (16 chars)                                     | `_maybe_ai_moderate`                   |
| Not spammy  | Passes a per-**member** cooldown (1 alert / 15s)                                            | `_ai_mod_cooldown`                     |
| Harmful     | Verdict `flagged == true` **and** `category != "none"`                                      | `ModerationAssessor.assess`            |
| Confident   | Verdict `confidence ≥ 0.7` (`DEFAULT_MIN_CONFIDENCE`)                                       | `ModerationAssessor.assess`            |

**The only action ever taken is posting an alert for human review.** There is no code path
from an AI verdict to a delete/mute/kick/ban. This is the hard guardrail from the rewrite
directives (§4): *AI produces signals/verdicts, not autonomous irreversible actions.*

### Categories

`none` (not harmful), `harassment`, `hate`, `sexual`, `violence`, `self_harm`, `spam`,
`other`. A `none` verdict — even if the model also set `flagged: true` — never alerts.

## Tuning

- **Confidence threshold:** `DEFAULT_MIN_CONFIDENCE = 0.7` in `app/services/ai/moderation.py`.
  Raise it to alert less (fewer false positives), lower it to catch more.
- **Min length / cooldown:** `MgmtMixin.AI_MOD_MIN_LENGTH` and `_ai_mod_cooldown` in the cog.
- **Model tier:** BALANCED. On a constrained CPU box, point all tiers at one model
  (see `docs/ai/PERSONA.md`) so it stays warm and the assessment lands quickly.
- **Where alerts go** (`_ai_alert_channel`): a normal **bot message** (so it can carry action
  buttons) in the first usable mod channel — the **alert channel**, then the **audit-log
  channel**, then the **mod-log channel** — that the bot can post embeds in. It never posts to
  the public system channel. If none is configured/usable, the flag is not sent and a
  **warning is logged** (so it's never silently lost). Enabling AI moderation is only useful
  alongside one of those channels.

## The alert message (`app/cogs/moderation/ai_alert.py`)

The flag is a rich embed (user, channel, category, confidence, reason, message excerpt, jump
link) with four moderator action buttons — **Delete · Warn · Kick · Ban** (`AIModerationAlertView`):

- Any moderator may act; each button **re-checks the clicker's own permission** at click time
  (Delete → Manage Messages, Warn/Kick → Kick Members, Ban → Ban Members) plus the self /
  owner / role-hierarchy guards (matching the equivalent commands). Unauthorized clicks get an
  ephemeral refusal and nothing happens.
- The action runs as that moderator and is recorded as a **mod case** via the standard
  `mod_action` dispatch (same path the `kick`/`ban`/`warn` commands use), so it shows up in the
  case log identically. Ban works even if the user already left; Warn/Kick require them present.
- Delete only removes the message (buttons stay); Warn/Kick/Ban resolve the flag — the embed
  turns green with a "Resolved by @mod" note and the buttons disable.

The buttons are a 3-day timed view (not persistent across a bot restart).

## Privacy

Message content is sent to the **self-hosted** Ollama instance for classification and is
**not persisted** for this purpose — the call is transient and nothing is stored for
training. See `PRIVACY_POLICY.md`.
