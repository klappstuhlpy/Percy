//! The `/api/v1` endpoints: thin mappings onto Percy's internal API.
//!
//! Every guild handler takes the guild from the authenticated [`Caller`] (the token), never from
//! the path; the gate has already proven the two agree. Percy is reached only through
//! `percy_client`, so the dashboard still never writes Percy's database.

use axum::{
    body::Bytes,
    extract::{Path, State},
    http::{HeaderMap, StatusCode, Uri},
    response::{IntoResponse, Response},
    Extension, Json,
};
use percy_client::PercyClient;
use serde::Deserialize;
use serde_json::{json, Value};

use super::{
    gate::{audit, from_percy, ApiError, Caller},
    tokens::{self, Claim},
};
use crate::AppState;

type ApiResult<T = Response> = Result<T, ApiError>;

/// Largest single balance or delta accepted; Percy re-checks, this keeps typos from reaching it.
const MAX_AMOUNT: i64 = 1_000_000_000_000;
const MAX_BODY: usize = 64 * 1024;

fn percy(app: &AppState) -> ApiResult<&PercyClient> {
    app.percy.as_deref().ok_or_else(|| {
        ApiError::new(
            StatusCode::SERVICE_UNAVAILABLE,
            "unavailable",
            "The API is not connected to Percy right now.",
        )
    })
}

fn snowflake(raw: &str) -> ApiResult<u64> {
    raw.parse::<u64>()
        .ok()
        .filter(|n| *n > 0)
        .ok_or_else(|| ApiError::bad_request("invalid_id", "IDs are positive integers."))
}

fn parse_json<T: for<'de> Deserialize<'de>>(bytes: &Bytes) -> ApiResult<T> {
    if bytes.len() > MAX_BODY {
        return Err(ApiError::new(
            StatusCode::PAYLOAD_TOO_LARGE,
            "body_too_large",
            "Request body is too large.",
        ));
    }
    serde_json::from_slice(bytes)
        .map_err(|_| ApiError::bad_request("invalid_body", "Body must be valid JSON of the documented shape."))
}

/// `?limit=&offset=` with defaults 25/0 and a ceiling of 100.
fn page(uri: &Uri) -> ApiResult<(u32, u32)> {
    let (mut limit, mut offset) = (25u32, 0u32);
    for pair in uri.query().unwrap_or("").split('&').filter(|p| !p.is_empty()) {
        let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
        let bad = || ApiError::bad_request("invalid_query", format!("`{k}` must be a non-negative integer."));
        match k {
            "limit" => limit = v.parse().map_err(|_| bad())?,
            "offset" => offset = v.parse().map_err(|_| bad())?,
            _ => {}
        }
    }
    if !(1..=100).contains(&limit) {
        return Err(ApiError::bad_request(
            "invalid_query",
            "`limit` must be between 1 and 100.",
        ));
    }
    Ok((limit, offset.min(100_000)))
}

fn ok(value: Value) -> ApiResult {
    Ok((StatusCode::OK, Json(value)).into_response())
}

fn ids(path: Vec<String>, want: usize) -> ApiResult<Vec<u64>> {
    if path.len() < want {
        return Err(ApiError::bad_request("invalid_id", "Missing id."));
    }
    path.iter().skip(1).take(want - 1).map(|s| snowflake(s)).collect()
}

// -- Idempotency -------------------------------------------------------------------------

/// Runs `work` once per `Idempotency-Key` (24 h): a retry with the same key and the same request
/// gets the stored answer back instead of paying twice. No header = no behaviour change.
async fn idempotent<F>(app: &AppState, caller: &Caller, headers: &HeaderMap, fingerprint: String, work: F) -> ApiResult
where
    F: std::future::Future<Output = Result<(StatusCode, Value), ApiError>>,
{
    let key = match headers.get("idempotency-key") {
        None => return work.await.map(|(s, v)| (s, Json(v)).into_response()),
        Some(v) => v
            .to_str()
            .ok()
            .filter(|k| {
                (1..=64).contains(&k.len()) && k.bytes().all(|b| b.is_ascii_alphanumeric() || b"-_.:".contains(&b))
            })
            .ok_or_else(|| {
                ApiError::bad_request(
                    "invalid_idempotency_key",
                    "Idempotency-Key must be 1-64 of [A-Za-z0-9-_.:].",
                )
            })?,
    };
    let internal = |e: rusqlite::Error| {
        tracing::error!(error = %e, "idempotency ledger failed");
        ApiError::new(StatusCode::INTERNAL_SERVER_ERROR, "internal_error", "Internal error.")
    };
    match tokens::claim(
        &app.db,
        caller.token_id,
        key,
        &tokens::sha256_hex(fingerprint.as_bytes()),
    )
    .await
    .map_err(internal)?
    {
        Claim::Replay { status, body } => {
            let mut resp = (
                StatusCode::from_u16(status).unwrap_or(StatusCode::OK),
                [(axum::http::header::CONTENT_TYPE, "application/json")],
                body,
            )
                .into_response();
            resp.headers_mut()
                .insert("idempotent-replayed", axum::http::HeaderValue::from_static("true"));
            Ok(resp)
        }
        Claim::Mismatch => Err(ApiError::new(
            StatusCode::UNPROCESSABLE_ENTITY,
            "idempotency_key_reuse",
            "That Idempotency-Key was already used for a different request.",
        )),
        Claim::InFlight => Err(ApiError::new(
            StatusCode::CONFLICT,
            "request_in_progress",
            "A request with that Idempotency-Key is still running.",
        )),
        Claim::Fresh => match work.await {
            Ok((status, value)) => {
                tokens::finish(&app.db, caller.token_id, key, status.as_u16(), &value.to_string()).await;
                Ok((status, Json(value)).into_response())
            }
            // An upstream failure may not have applied; free the key so the retry actually runs.
            Err(e) if e.status.is_server_error() => {
                tokens::release(&app.db, caller.token_id, key).await;
                Err(e)
            }
            Err(e) => {
                tokens::finish(&app.db, caller.token_id, key, e.status.as_u16(), &e.body().to_string()).await;
                Err(e)
            }
        },
    }
}

fn fingerprint(method: &str, uri: &Uri, body: &Bytes) -> String {
    format!("{method} {}\n{}", uri.path(), String::from_utf8_lossy(body))
}

// -- Guild ---------------------------------------------------------------------------------

pub(crate) async fn guild(State(app): State<AppState>, Extension(c): Extension<Caller>) -> ApiResult {
    let p = percy(&app)?;
    let (info, economy) = tokio::join!(p.get_guild_api(c.guild_id), p.get_economy_api(c.guild_id));
    let (info, economy) = (info.map_err(from_percy)?, economy.map_err(from_percy)?);
    let settings = economy.settings.unwrap_or_default();
    ok(json!({
        "id": info.id.to_string(),
        "name": info.name,
        "icon_url": info.icon_url,
        "member_count": info.member_count,
        "economy": {
            "payout_multiplier": settings.payout_multiplier,
            "rob_enabled": settings.rob_enabled,
            "daily_base": settings.daily_base,
            "max_bet": settings.max_bet,
        },
    }))
}

// -- Balances ------------------------------------------------------------------------------

pub(crate) async fn users(State(app): State<AppState>, Extension(c): Extension<Caller>, uri: Uri) -> ApiResult {
    let (limit, offset) = page(&uri)?;
    let res = percy(&app)?
        .get_economy_balances_page(c.guild_id, limit, offset)
        .await
        .map_err(from_percy)?;
    let users: Vec<Value> = res
        .entries
        .iter()
        .enumerate()
        .map(|(i, e)| {
            json!({"user_id": e.user_id, "rank": offset as usize + i + 1, "cash": e.cash, "bank": e.bank, "total": e.total})
        })
        .collect();
    ok(json!({"users": users, "limit": limit, "offset": offset}))
}

pub(crate) async fn user(
    State(app): State<AppState>,
    Extension(c): Extension<Caller>,
    Path(path): Path<Vec<String>>,
) -> ApiResult {
    let uid = ids(path, 2)?[0];
    let b = percy(&app)?
        .get_member_balance(c.guild_id, uid)
        .await
        .map_err(from_percy)?;
    ok(json!({"user_id": uid.to_string(), "cash": b.cash, "bank": b.bank, "total": b.total}))
}

#[derive(Deserialize)]
struct BalanceBody {
    cash: Option<i64>,
    bank: Option<i64>,
}

fn check_amounts(b: &BalanceBody) -> ApiResult<()> {
    if b.cash.is_none() && b.bank.is_none() {
        return Err(ApiError::bad_request("invalid_body", "Send `cash` and/or `bank`."));
    }
    if [b.cash, b.bank].into_iter().flatten().any(|n| n.abs() > MAX_AMOUNT) {
        return Err(ApiError::bad_request(
            "amount_too_large",
            "Amounts are limited to ±1,000,000,000,000.",
        ));
    }
    Ok(())
}

/// `PATCH`: add signed deltas atomically. Rejected (400 `insufficient_funds`) if either would go below 0.
pub(crate) async fn adjust_user(
    State(app): State<AppState>,
    Extension(c): Extension<Caller>,
    Path(path): Path<Vec<String>>,
    headers: HeaderMap,
    uri: Uri,
    body: Bytes,
) -> ApiResult {
    let uid = ids(path, 2)?[0];
    let b: BalanceBody = parse_json(&body)?;
    check_amounts(&b)?;
    if b.cash.unwrap_or(0) == 0 && b.bank.unwrap_or(0) == 0 {
        return Err(ApiError::bad_request(
            "invalid_body",
            "At least one delta must be non-zero.",
        ));
    }
    let p = percy(&app)?;
    let fp = fingerprint("PATCH", &uri, &body);
    idempotent(&app, &c, &headers, fp, async {
        let r = p
            .adjust_member_balance(c.guild_id, uid, b.cash.unwrap_or(0), b.bank.unwrap_or(0))
            .await
            .map_err(from_percy)?;
        audit(&app, &c, "Economy balance", "Adjusted", "bank, cash").await;
        Ok((
            StatusCode::OK,
            json!({"user_id": uid.to_string(), "cash": r.cash, "bank": r.bank, "total": r.total}),
        ))
    })
    .await
}

/// `PUT`: set cash and/or bank to absolute, non-negative values. Idempotent by nature.
pub(crate) async fn set_user(
    State(app): State<AppState>,
    Extension(c): Extension<Caller>,
    Path(path): Path<Vec<String>>,
    body: Bytes,
) -> ApiResult {
    let uid = ids(path, 2)?[0];
    let b: BalanceBody = parse_json(&body)?;
    check_amounts(&b)?;
    if [b.cash, b.bank].into_iter().flatten().any(|n| n < 0) {
        return Err(ApiError::bad_request(
            "invalid_body",
            "Absolute balances cannot be negative.",
        ));
    }
    let p = percy(&app)?;
    p.set_member_balance(c.guild_id, uid, b.cash, b.bank)
        .await
        .map_err(from_percy)?;
    audit(&app, &c, "Economy balance", "Set", "bank, cash").await;
    let r = p.get_member_balance(c.guild_id, uid).await.map_err(from_percy)?;
    ok(json!({"user_id": uid.to_string(), "cash": r.cash, "bank": r.bank, "total": r.total}))
}

// -- Items ---------------------------------------------------------------------------------

/// Item columns a caller may send (matches Percy's create/patch bodies).
const ITEM_FIELDS: &[&str] = &[
    "name",
    "price",
    "description",
    "effect",
    "effect_value",
    "duration_minutes",
    "emoji",
    "rarity",
    "stock",
    "max_per_user",
    "required_role_id",
    "available_until",
    "remove_role_id",
    "reply_message",
    "image_url",
];
/// The effect is fixed once owned copies may exist (Percy ignores it on PATCH anyway).
const ITEM_PATCH_FIELDS: &[&str] = &[
    "name",
    "price",
    "description",
    "emoji",
    "rarity",
    "stock",
    "max_per_user",
    "required_role_id",
    "available_until",
    "remove_role_id",
    "reply_message",
    "image_url",
];

fn only(map: serde_json::Map<String, Value>, allowed: &[&str]) -> ApiResult<serde_json::Map<String, Value>> {
    if let Some(bad) = map.keys().find(|k| !allowed.contains(&k.as_str())) {
        return Err(ApiError::bad_request(
            "unknown_field",
            format!("Unknown field `{bad}`."),
        ));
    }
    Ok(map)
}

fn field_names(map: &serde_json::Map<String, Value>) -> String {
    let mut keys: Vec<&str> = map.keys().map(String::as_str).collect();
    keys.sort_unstable();
    keys.join(", ")
}

pub(crate) async fn items(State(app): State<AppState>, Extension(c): Extension<Caller>) -> ApiResult {
    let eco = percy(&app)?.get_economy_api(c.guild_id).await.map_err(from_percy)?;
    ok(json!({"items": eco.items}))
}

pub(crate) async fn item(
    State(app): State<AppState>,
    Extension(c): Extension<Caller>,
    Path(path): Path<Vec<String>>,
) -> ApiResult {
    let id = ids(path, 2)?[0];
    let eco = percy(&app)?.get_economy_api(c.guild_id).await.map_err(from_percy)?;
    match eco.items.into_iter().find(|i| i.id == id) {
        Some(i) => ok(json!(i)),
        None => Err(ApiError::new(
            StatusCode::NOT_FOUND,
            "item_not_found",
            "Item not found.",
        )),
    }
}

pub(crate) async fn create_item(
    State(app): State<AppState>,
    Extension(c): Extension<Caller>,
    body: Bytes,
) -> ApiResult {
    let map = only(parse_json(&body)?, ITEM_FIELDS)?;
    if !map.contains_key("name") || !map.contains_key("price") {
        return Err(ApiError::bad_request(
            "invalid_body",
            "`name` and `price` are required.",
        ));
    }
    let names = field_names(&map);
    let res = percy(&app)?
        .create_economy_item_api(c.guild_id, &Value::Object(map))
        .await
        .map_err(from_percy)?;
    audit(&app, &c, "Economy items", "Created", &names).await;
    Ok((StatusCode::CREATED, Json(json!({"id": res.get("id")}))).into_response())
}

pub(crate) async fn patch_item(
    State(app): State<AppState>,
    Extension(c): Extension<Caller>,
    Path(path): Path<Vec<String>>,
    body: Bytes,
) -> ApiResult {
    let id = ids(path, 2)?[0];
    let map = only(parse_json(&body)?, ITEM_PATCH_FIELDS)?;
    if map.is_empty() {
        return Err(ApiError::bad_request(
            "invalid_body",
            "Send at least one field to change.",
        ));
    }
    let names = field_names(&map);
    let p = percy(&app)?;
    p.patch_economy_item(c.guild_id, id, &Value::Object(map))
        .await
        .map_err(from_percy)?;
    audit(&app, &c, "Economy items", "Updated", &names).await;
    item_after(p, c.guild_id, id).await
}

async fn item_after(p: &PercyClient, guild: u64, id: u64) -> ApiResult {
    let eco = p.get_economy_api(guild).await.map_err(from_percy)?;
    match eco.items.into_iter().find(|i| i.id == id) {
        Some(i) => ok(json!(i)),
        None => Err(ApiError::new(
            StatusCode::NOT_FOUND,
            "item_not_found",
            "Item not found.",
        )),
    }
}

pub(crate) async fn delete_item(
    State(app): State<AppState>,
    Extension(c): Extension<Caller>,
    Path(path): Path<Vec<String>>,
) -> ApiResult {
    let id = ids(path, 2)?[0];
    percy(&app)?
        .delete_economy_item_by_id(c.guild_id, id)
        .await
        .map_err(from_percy)?;
    audit(&app, &c, "Economy items", "Removed", "").await;
    Ok(StatusCode::NO_CONTENT.into_response())
}

// -- Inventory -----------------------------------------------------------------------------

pub(crate) async fn inventory(
    State(app): State<AppState>,
    Extension(c): Extension<Caller>,
    Path(path): Path<Vec<String>>,
) -> ApiResult {
    let uid = ids(path, 2)?[0];
    let inv = percy(&app)?
        .get_member_inventory(c.guild_id, uid)
        .await
        .map_err(from_percy)?;
    ok(json!({"user_id": uid.to_string(), "items": inv.items}))
}

#[derive(Deserialize)]
struct GiveBody {
    item_id: u64,
    #[serde(default = "one")]
    quantity: u32,
}

fn one() -> u32 {
    1
}

pub(crate) async fn give_item(
    State(app): State<AppState>,
    Extension(c): Extension<Caller>,
    Path(path): Path<Vec<String>>,
    headers: HeaderMap,
    uri: Uri,
    body: Bytes,
) -> ApiResult {
    let uid = ids(path, 2)?[0];
    let b: GiveBody = parse_json(&body)?;
    if !(1..=10_000).contains(&b.quantity) {
        return Err(ApiError::bad_request(
            "invalid_body",
            "`quantity` must be between 1 and 10000.",
        ));
    }
    let p = percy(&app)?;
    let fp = fingerprint("POST", &uri, &body);
    idempotent(&app, &c, &headers, fp, async {
        let r = p
            .give_member_item(c.guild_id, uid, b.item_id, b.quantity)
            .await
            .map_err(from_percy)?;
        audit(&app, &c, "Inventory", "Gave item", "item_id, quantity").await;
        Ok((StatusCode::OK, json!({"item_id": r.item_id, "quantity": r.quantity})))
    })
    .await
}

pub(crate) async fn take_item(
    State(app): State<AppState>,
    Extension(c): Extension<Caller>,
    Path(path): Path<Vec<String>>,
    headers: HeaderMap,
    uri: Uri,
) -> ApiResult {
    let got = ids(path, 3)?;
    let (uid, item_id) = (got[0], got[1]);
    let mut quantity = 1u32;
    for pair in uri.query().unwrap_or("").split('&') {
        if let Some(v) = pair.strip_prefix("quantity=") {
            quantity = v
                .parse()
                .ok()
                .filter(|q| (1..=10_000).contains(q))
                .ok_or_else(|| ApiError::bad_request("invalid_query", "`quantity` must be between 1 and 10000."))?;
        }
    }
    let p = percy(&app)?;
    let fp = fingerprint("DELETE", &uri, &Bytes::new());
    idempotent(&app, &c, &headers, fp, async {
        let r = p
            .take_member_item(c.guild_id, uid, item_id, quantity)
            .await
            .map_err(from_percy)?;
        audit(&app, &c, "Inventory", "Took item", "item_id, quantity").await;
        Ok((StatusCode::OK, json!({"item_id": r.item_id, "quantity": r.quantity})))
    })
    .await
}

// -- Leveling ------------------------------------------------------------------------------

pub(crate) async fn leaderboard(State(app): State<AppState>, Extension(c): Extension<Caller>, uri: Uri) -> ApiResult {
    let (limit, offset) = page(&uri)?;
    let res = percy(&app)?
        .get_leveling_leaderboard_page(c.guild_id, limit, offset)
        .await
        .map_err(from_percy)?;
    let users: Vec<Value> = res
        .entries
        .iter()
        .enumerate()
        .map(|(i, e)| json!({"user_id": e.user_id, "rank": offset as usize + i + 1, "level": e.level, "xp": e.xp}))
        .collect();
    ok(json!({"users": users, "limit": limit, "offset": offset}))
}

pub(crate) async fn level_user(
    State(app): State<AppState>,
    Extension(c): Extension<Caller>,
    Path(path): Path<Vec<String>>,
) -> ApiResult {
    // Route is /leveling/users/:u, so the user is the second captured segment.
    let uid = ids(path, 2)?[0];
    let l = percy(&app)?
        .get_member_level(c.guild_id, uid)
        .await
        .map_err(from_percy)?;
    ok(json!({"user_id": uid.to_string(), "level": l.level, "xp": l.xp, "messages": l.messages, "rank": l.rank}))
}

// -- Public --------------------------------------------------------------------------------

pub(crate) async fn bot_stats(State(app): State<AppState>) -> ApiResult {
    let s = percy(&app)?.get_bot_stats_api().await.map_err(from_percy)?;
    ok(json!({
        "version": s.version,
        "guild_count": s.guild_count,
        "user_count": s.user_count,
        "command_count": s.command_count,
        "total_commands_used": s.total_commands_used,
        "latency_ms": s.latency_ms,
        "uptime_seconds": s.uptime_seconds,
    }))
}
