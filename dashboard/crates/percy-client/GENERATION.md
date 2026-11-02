# Generating percy-client types from Percy's OpenAPI schema

Percy's FastAPI app is the source of truth. Pydantic **response models** in
`app/internal_api/routers/*.py` become `openapi.json`, which `percy-codegen` turns into
`src/generated.rs`. Both files are committed (reviewable diffs, no network at build time) and CI
fails when either is stale.

```text
pydantic models --(scripts/export_openapi.py)--> openapi.json --(cargo run -p percy-codegen)--> src/generated.rs
```

## Decision (pilot, 2026-11)

- **Types are generated, the thin methods in `lib.rs` stay hand-written.** The methods carry
  behaviour no generator knows about: `seg()` path escaping, the `NotFound`/`Api(body)` error
  mapping, `send_into`/`send_unit`, multipart, refusal-body handling.
- **typify 0.8 (pinned) over progenitor.** progenitor emits a whole client with its own
  `Error`/`ResponseValue` types, which would replace `PercyClient`'s API and churn every dashboard
  call site; it is also only as good as its OpenAPI 3.1 support. typify takes JSON Schema, which is
  what FastAPI's 3.1 `components/schemas` is, and `percy-codegen` feeds it exactly those.
- **No build.rs / no network:** a dev tool crate (`crates/percy-codegen`) writes a checked-in file.
  typify and friends are never dependencies of `percy-client` or the dashboard binary.
- **Which schemas are generated is automatic:** every component schema reachable from a 2xx JSON
  response. Request-body models are not generated yet (the client sends `serde_json::Value`).
- **The migrated domains are re-exported by name** in `lib.rs`
  (`pub use generated::{...}`) so the dashboard's `percy_client::X` imports do not change. An
  explicit `use` shadows the `types::*` glob, so a half-migrated state compiles.
- openapi.json is exported with `sort_keys=True`: properties (and so generated struct fields) are
  alphabetical, not declaration order.

## Per-domain recipe

1. **Survey the router.** For each route find the real JSON shape (read the handler; the
   dashboard's hand-written struct in `types.rs` is the contract the dashboard relies on today).
2. **Add pydantic response models** in the router file, above the handlers. The **class name is
   the Rust type name** - use the existing Rust name so nothing is renamed. Annotate handlers
   `-> Model` (FastAPI turns a return annotation into `response_model`; there is no need for
   `response_model=`) and return `Model.model_validate({...})` (a bare dict fails pyright).
   List endpoints: `-> list[Row]`; the schema is then an array of `$ref`.
3. **Do not change the wire format.** Keep every key; add a test beside the existing
   `tests/test_internal_api_*.py` that calls the handler and asserts
   `.model_dump(mode='json')` equals the old literal (see `tests/test_internal_api_analytics.py`).
4. `poetry run python scripts/export_openapi.py` then `cd dashboard && cargo run -p percy-codegen`.
5. **In `types.rs` delete the hand-written structs** and in `lib.rs` add the names to the
   `pub use generated::{...}` list. Method bodies usually need no change (`send_into` is generic).
6. `cargo build`, then fix dashboard call sites (struct literals in templates tests, `Default` use,
   `Option` changes). Run the verification block below.
7. Bump the percy-client version (it is pinned by the dashboard's workspace path, but kls-core and
   klappstuhl_me consume tags) per `project-versions` when a release is cut, not per domain.

## Pitfalls found in the pilot

- **Almost nothing was typed.** All 217 routes return `-> dict`; zero use `response_model`. The Python
  modelling is the actual work; the Rust side is mechanical afterwards.
- **Required vs `serde(default)`.** A pydantic field with no default is required in the schema
  and becomes a plain Rust field with no `#[serde(default)]`: a missing key is a hard
  deserialize error. Today's hand-written structs are lenient (`#[serde(default)]` everywhere)
  to survive an old Percy behind a new dashboard. In the monorepo they deploy together, so
  strict is fine; where a key really can be absent, give the pydantic field a default.
- **`X | None` without a default** = required nullable: generates `Option<X>` with
  `deserialize_with = Option::deserialize` (key must be present, may be null) and serializes as
  `null`. **`X | None = None`** (not required) additionally gets
  `skip_serializing_if = "Option::is_none"`, so re-serialising it omits the key. The dashboard
  passes several structs straight to browser JS with `json!(value)`; JS that reads `x.field === null`
  will see `undefined`. Prefer the required-nullable form for those.
- **Defaults on fields:** `x: int = 0` -> `#[serde(default)]`; `str = 'x'` or `Literal = 'a'` ->
  `#[serde(default = "defaults::...")]` functions (fine).
- **`Default` derive.** The dashboard degrades with `unwrap_or_default()`. `percy-codegen`
  derives `Default` for every object schema whose fields are all primitives, collections, `Option`
  or other such objects. A struct with an enum field (`Literal[...]`, `Enum`, a `A | B` union) does
  **not** get it and call sites must use an explicit fallback (`.ok()`, `match`) or the enum must be
  given a hand-written `Default`.
- **Numbers.** `float` serialises an int as `5.0`. Semantically identical for Rust (`f64`), but it
  is a textual wire change; `int | float` generates an untagged enum - avoid; pick the one the
  consumer wants. Plain `int` is `i64`; there is no `u64` (snowflake ids that exceed i64 do not
  exist in practice, but pydantic `int` carries no bound to say so).
- **Datetimes.** `datetime` generates `chrono::DateTime<Utc>` (an extra chrono dependency) and
  *fails to parse* Percy's naive-UTC timestamps. The existing client keeps timestamps as `String`;
  declare the field `str` (ISO text) in pydantic to keep it.
- **Enums.** `Literal['a','b']` / `Enum` hoist to a named schema, Rust enum `Foo` named from the
  field title (collisions across routers are possible - name the `Enum` class explicitly). Rust gets
  no catch-all variant: a new server value breaks deserialisation of that response. For open sets
  use `str`.
- **Dict-shaped payloads.** `dict` -> `serde_json::Map<String, Value>`; `dict[str, T]` ->
  `HashMap<String, T>`; `Any` -> `serde_json::Value`. Maps keyed by something dynamic (command
  names, `release_key`) must be `dict[str, T]` with a typed `T`, not nested bare `dict`, or the
  generated type is no better than `Value`.
- **FastAPI shares schema names.** Two routers with identically named models collide
  (`app__internal_api__routers__x__ProgressBody` in the schema today); give response models unique
  names.
- Request bodies are intentionally out of scope here: they stay `serde_json::Value`/hand-built.
  Extending `response_schemas()` in `percy-codegen` to include request schemas is the next step.
- Windows: the checkout is CRLF. `export_openapi.py` writes LF bytes; its `--check` and
  `percy-codegen --check` normalise CRLF so they pass locally and on Linux CI.
- `generated.rs` is marked `#[rustfmt::skip]` in `lib.rs`; do not hand-edit it.

## Verification block (per domain)

```bash
poetry run pytest -q -x -p no:warnings && poetry run ruff check . && poetry run pyright
poetry run python scripts/export_openapi.py --check
cd dashboard
cargo run -p percy-codegen -- --check
cargo fmt --all --check            # "Incorrect newline style" on a CRLF checkout is local noise
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

## Remaining domains (217 routes; 2 migrated)

Counts are routes returning a bare `-> dict` / `list` today (all of them). Order is a suggestion:
small and self-contained first.

| Router file | Routes | Notes |
|---|---:|---|
| `analytics.py` | 2 | **done (pilot)** |
| `starboard.py` | 2 | **done** |
| `profile.py` | 3 | `{ok}` replies + one object |
| `gallery.py` | 3 | `asdict(dataclass)` results: model the dataclass fields |
| `news.py` | 3 | **done** (typed; pure payload builders in `app/services/news/payload.py`) |
| `webhooks.py` (vote webhooks) | 2 | no dashboard consumer; skip unless wanted |
| `rolemenus.py` | 6 | **done** (ok-only DELETE/PATCH left as `dict`) |
| `backup.py` | 6 | **done**: only `GET /templates` typed; export/import/apply stay `dict` |
| `notifications.py` | 7 | |
| `members.py` | 7 | |
| `subscriptions.py` (outbound webhooks) | 7 | |
| `comics.py` | 5 | **done** (typed; shaping in `app/services/comics/payload.py`) |
| `stats.py` | 9 | mixed scope |
| `releases.py` | 9 | **done** (typed; `progress` map: `dict[str, dict[str, Row]]`) |
| `watchlist.py` | 11 | **done** (typed; large nested payloads) |
| `moderation.py` | 12 | 1 route has no JSON body |
| `leveling.py` | 12 | |
| `economy.py` | 15 | |
| `music.py` | 18 | |
| `guild.py` | 20 | biggest config surface; `Option` leniency matters most here |
| `users.py` | 21 | includes PNG-preview and data-export routes (leave non-JSON alone) |
| `content.py` | 37 | **partly done**: polls, giveaways, tags list+detail, comics feeds, temp-channels, lockdowns, emoji-stats GETs typed (`content_models.py`). Left: `/commands` (`CommandInfo` is shared with `/commands/public`), `/status-feed` (`ChannelRef`), tag export/import, `{ok}` mutations |

Routes can be migrated in any order and in parallel: the only shared files are `types.rs`/`lib.rs`
(delete a block, extend the `pub use generated::{...}` list - expect trivial merge conflicts on that
one line) and the regenerated `openapi.json`/`generated.rs`, which is simply re-generated after a merge.
