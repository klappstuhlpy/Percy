# Percy's identity & knowledge for the LLM

How Percy tells the model "you are Percy", what knowledge it has, how that stays secure,
and whether to send it every request or bake it into the model.

## Where the persona lives

`app/services/ai/prompts.py` is the **single source of truth**:

- `PERCY_IDENTITY` — who Percy is and a high-level capability map (moderation, leveling,
  economy, music, polls/giveaways/tags, games, anime, utility, dashboard). Deliberately
  **not** an exhaustive command list — for exact syntax the model is told to send users to
  the help command / dashboard rather than inventing flags.
- `_ASSISTANT_RULES` — behaviour + the **security contract** (refuse to reveal the prompt,
  secrets, code, env, keys; treat user input as untrusted; don't claim to perform actions).
- `build_assistant_system(server_name, prefix, website, support_server)` — composes the
  identity + light, **non-sensitive** runtime context (server name, command prefix, public
  URLs) + the rules. The `?ask` cog calls this with the live guild + prefix.

The router (`router.py`) and moderation (`moderation.py`) use their own **focused** prompts,
not this persona — they're structured classifiers, not the chat persona.

## How it stays secure

The model only ever knows **what we put in the prompt**. It has no filesystem, no `.env`,
no database, no source access. Secrets cannot leak because we never interpolate them into a
prompt — `build_assistant_system` only ever receives display-level values (a server name, a
prefix, public URLs). The persona then adds defence-in-depth: it instructs the model to
refuse any request to reveal its instructions or internal data, and to treat the user's
message as untrusted (anti prompt-injection).

**Rule for contributors:** never pass secrets, config values, tokens, or raw internal
objects into any prompt string. If you add dynamic context, keep it to public, display-safe
values.

## "Send it every time" vs. "bake it into the model" — the decision

You asked whether the persona must be sent on every request or whether we can configure the
Ollama model to *always be Percy* (via a Modelfile `SYSTEM` block). Both work; we deliberately
**keep the persona in code and send it each request**. Why:

| | Persona in code (chosen) | Persona baked into a Modelfile |
|---|---|---|
| Source of truth | One place, version-controlled, code-reviewed, unit-tested | Split: lives on the VPS, edited with `ollama create`, not in git |
| Updating it | Edit `prompts.py`, deploy | SSH to the box, rebuild the model |
| Dynamic context | Easy — inject server name / prefix / URLs per call | Impossible — a baked `SYSTEM` is static; you'd *still* send per-call context |
| Cost of sending each call | Negligible — see below | Saves a few hundred tokens, but you lose the above |

**On the "cost" of resending:** with `OLLAMA_KEEP_ALIVE` keeping the model resident,
llama.cpp/Ollama caches the **prompt prefix**. A constant system prompt is processed once and
reused across calls as long as the model stays loaded — so resending it is effectively free.
The win from baking it in is therefore tiny, and it costs us versioning, testability, and
the ability to inject per-server context.

**Recommendation:** keep the persona in code. Use a Modelfile only for **operational
parameters** — not persona — so identity stays in one place. Example:

```dockerfile
# Modelfile — runtime params only; persona stays in app/services/ai/prompts.py
FROM qwen2.5:1.5b
PARAMETER temperature 0.7
PARAMETER num_ctx 4096
# keep_alive is better set via the OLLAMA_KEEP_ALIVE env on the service (see below)
```

```bash
ollama create percy-base -f Modelfile
# then point OLLAMA_*_MODEL at "percy-base"
```

If you ever *do* want the persona baked in (e.g. to call the model from tools that don't go
through Percy), generate the Modelfile's `SYSTEM` line **from** `PERCY_IDENTITY` so code
remains the source of truth — don't hand-maintain a second copy.

## Keeping it warm (so it answers fast)

On a small/CPU box, persona quality is wasted if calls time out. Keep one model resident:

```ini
# ollama systemd unit
Environment="OLLAMA_KEEP_ALIVE=-1"        # never unload
Environment="OLLAMA_MAX_LOADED_MODELS=1"
```

and point `OLLAMA_FAST_MODEL` / `OLLAMA_BALANCED_MODEL` / `OLLAMA_SMART_MODEL` at the **same
tag** so tier switches don't evict/reload models. See the timeout notes in
`AI_INTEGRATION_BLUEPRINT.md`.
