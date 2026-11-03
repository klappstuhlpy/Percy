//! Form/JSON plumbing shared by the mutation handlers, ported from the monolith.
//!
//! `build_patch` / `build_sentinel_patch` (the form-field → config-patch mappers)
//! and `ConfigForm` are copied 1:1. `json_or_redirect` replaces the monolith's
//! `json_or_flash`: the dashboard has no flash system, so a non-AJAX request just
//! redirects (the JS submits with `Accept: application/json` and reads the JSON).

use std::collections::HashMap;

use axum::{
    http::HeaderMap,
    response::{IntoResponse, Redirect, Response},
    Json,
};
use serde::Deserialize;

/// The main configuration form: a `_section` discriminator plus arbitrary fields.
#[derive(Deserialize)]
pub struct ConfigForm {
    #[serde(rename = "_section")]
    pub section: String,
    #[serde(flatten)]
    pub fields: HashMap<String, String>,
}

/// AI feature flag names — must match Percy's `GuildConfig.AIFlags` / internal API.
pub const AI_FLAG_NAMES: [&str; 8] = [
    "assistant",
    "moderation",
    "sentinel",
    "music",
    "polls",
    "giveaways",
    "tags",
    "reminders",
];

/// Whether the client wants a JSON response (AJAX form submit) rather than a redirect.
pub fn is_ajax(headers: &HeaderMap) -> bool {
    headers
        .get("accept")
        .and_then(|v| v.to_str().ok())
        .map(|v| v.contains("application/json"))
        .unwrap_or(false)
}

/// AJAX requests get a `{ok, message|error}` JSON body; plain form posts are
/// redirected. (The monolith also flashed a message here; the dashboard has no
/// flash store, so the message is only surfaced on the AJAX path.)
pub fn json_or_redirect(headers: &HeaderMap, ok: bool, msg: &str, redirect: &str) -> Response {
    if is_ajax(headers) {
        if ok {
            Json(serde_json::json!({"ok": true, "message": msg})).into_response()
        } else {
            Json(serde_json::json!({"ok": false, "error": msg})).into_response()
        }
    } else {
        Redirect::to(redirect).into_response()
    }
}

/// A successful save that Percy attached advice to (e.g. "#verify is still visible to
/// @everyone"): the AJAX body carries it as `warning` so the page can show it.
pub fn saved_with_warnings(headers: &HeaderMap, msg: &str, warnings: &[String], redirect: &str) -> Response {
    if warnings.is_empty() || !is_ajax(headers) {
        return json_or_redirect(headers, true, msg, redirect);
    }
    Json(serde_json::json!({"ok": true, "message": msg, "warning": warnings.join(" ")})).into_response()
}

/// The mention-spam threshold field: empty or `0` turns the protection off (`null`), any
/// other number is forwarded for Percy to validate (it accepts 3–100 and says so otherwise).
fn mention_count_value(raw: &str) -> serde_json::Value {
    match raw.trim().parse::<u64>() {
        Ok(n) if n > 0 => serde_json::Value::from(n),
        _ => serde_json::Value::Null,
    }
}

/// Maps a config form section + fields to the JSON patch Percy's internal API expects.
pub fn build_patch(section: &str, fields: &HashMap<String, String>) -> serde_json::Value {
    match section {
        "flags" => {
            let flags = serde_json::json!({
                "audit_log": fields.contains_key("audit_log"),
                "raid": fields.contains_key("raid"),
                "alerts": fields.contains_key("alerts"),
                "sentinel": fields.contains_key("sentinel"),
                "mentions": fields.contains_key("mentions"),
            });
            serde_json::json!({ "flags": flags })
        }
        "moderation" => {
            let mut patch = serde_json::Map::new();
            if let Some(v) = fields.get("audit_log_channel_id") {
                patch.insert("audit_log_channel_id".into(), parse_id_or_null(v));
            }
            if let Some(v) = fields.get("alert_channel_id") {
                patch.insert("alert_channel_id".into(), parse_id_or_null(v));
            }
            if let Some(v) = fields.get("mute_role_id") {
                patch.insert("mute_role_id".into(), parse_id_or_null(v));
            }
            if let Some(v) = fields.get("mention_count") {
                patch.insert("mention_count".into(), mention_count_value(v));
            }
            if let Some(v) = fields.get("mod_log_channel_id") {
                patch.insert("mod_log_channel_id".into(), parse_id_or_null(v));
            }
            if let Some(v) = fields.get("message_log_channel_id") {
                patch.insert("message_log_channel_id".into(), parse_id_or_null(v));
            }
            if let Some(v) = fields.get("voice_log_channel_id") {
                patch.insert("voice_log_channel_id".into(), parse_id_or_null(v));
            }
            for key in ["mod_dm_on_action", "mod_require_reason", "mod_reinvite_on_unban"] {
                patch.insert(key.into(), serde_json::Value::Bool(fields.contains_key(key)));
            }
            if let Some(v) = fields.get("mod_default_ban_delete_days") {
                // Percy validates 0-7; an unparseable box means "none" rather than a failed save.
                patch.insert(
                    "mod_default_ban_delete_days".into(),
                    v.trim().parse::<u8>().unwrap_or(0).into(),
                );
            }
            if let Some(v) = fields.get("disabled_command_message") {
                let text = v.trim();
                patch.insert(
                    "disabled_command_message".into(),
                    if text.is_empty() {
                        serde_json::Value::Null
                    } else {
                        text.into()
                    },
                );
            }
            serde_json::Value::Object(patch)
        }
        "polls" => {
            let mut patch = serde_json::Map::new();
            if let Some(v) = fields.get("poll_channel_id") {
                patch.insert("poll_channel_id".into(), parse_id_or_null(v));
            }
            if let Some(v) = fields.get("poll_reason_channel_id") {
                patch.insert("poll_reason_channel_id".into(), parse_id_or_null(v));
            }
            if let Some(v) = fields.get("poll_ping_role_id") {
                patch.insert("poll_ping_role_id".into(), parse_id_or_null(v));
            }
            serde_json::Value::Object(patch)
        }
        "music" => {
            let mut patch = serde_json::Map::new();
            patch.insert(
                "use_music_panel".into(),
                serde_json::Value::Bool(fields.contains_key("use_music_panel")),
            );
            if let Some(v) = fields.get("music_panel_channel_id") {
                patch.insert("music_panel_channel_id".into(), parse_id_or_null(v));
            }
            serde_json::Value::Object(patch)
        }
        "prefixes" => {
            let prefixes: Vec<&str> = fields
                .get("prefixes")
                .map(|s| s.split(',').map(str::trim).filter(|s| !s.is_empty()).collect())
                .unwrap_or_default();
            serde_json::json!({ "prefixes": prefixes })
        }
        _ => serde_json::json!({}),
    }
}

/// Maps the sentinel form to the patch Percy's `/sentinel` endpoint expects.
pub fn build_sentinel_patch(fields: &HashMap<String, String>) -> serde_json::Value {
    let mut patch = serde_json::Map::new();
    if let Some(v) = fields.get("channel_id") {
        patch.insert("channel_id".into(), parse_id_or_null(v));
    }
    if let Some(v) = fields.get("role_id") {
        patch.insert("role_id".into(), parse_id_or_null(v));
    }
    if let Some(v) = fields.get("starter_role_id") {
        patch.insert("starter_role_id".into(), parse_id_or_null(v));
    }
    if let Some(v) = fields.get("bypass_action") {
        if v == "ban" || v == "kick" {
            patch.insert("bypass_action".into(), serde_json::Value::String(v.clone()));
        }
    }
    if let Some(v) = fields.get("rate") {
        if v.is_empty() {
            patch.insert("rate".into(), serde_json::Value::Null);
        } else {
            patch.insert("rate".into(), serde_json::Value::String(v.clone()));
        }
    }
    serde_json::Value::Object(patch)
}

/// Parses a Discord snowflake, mapping empty/invalid input to JSON `null`.
fn parse_id_or_null(s: &str) -> serde_json::Value {
    if s.is_empty() {
        serde_json::Value::Null
    } else {
        s.parse::<u64>()
            .map(serde_json::Value::from)
            .unwrap_or(serde_json::Value::Null)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn moderation_section_carries_switches_days_and_message() {
        let fields: HashMap<String, String> = [
            ("mod_require_reason", "on"),
            ("mod_default_ban_delete_days", " 3 "),
            ("disabled_command_message", "  "),
        ]
        .into_iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect();
        let patch = build_patch("moderation", &fields);
        assert_eq!(patch["mod_require_reason"], true);
        assert_eq!(patch["mod_dm_on_action"], false); // an unticked box is absent from the form
        assert_eq!(patch["mod_default_ban_delete_days"], 3);
        assert!(patch["disabled_command_message"].is_null());
    }

    #[test]
    fn mention_count_off_or_forwarded() {
        assert_eq!(mention_count_value(""), serde_json::Value::Null);
        assert_eq!(mention_count_value("0"), serde_json::Value::Null);
        assert_eq!(mention_count_value("abc"), serde_json::Value::Null);
        assert_eq!(mention_count_value(" 5 "), serde_json::json!(5));
        // Out of range is Percy's to refuse with a message, not ours to silently turn off.
        assert_eq!(mention_count_value("2"), serde_json::json!(2));
        assert_eq!(mention_count_value("5000000000"), serde_json::json!(5_000_000_000u64));
    }
}
