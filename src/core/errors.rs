//! Human-readable error text for Percy API failures.
//!
//! Percy's internal API is a FastAPI app, so it reports a failure as
//! `{"detail": "…"}` — or, when request validation trips, as
//! `{"detail": [{"loc": [...], "msg": "…"}, …]}`. [`percy_client::PercyError::Api`]
//! carries that response body verbatim, so handing `e.to_string()` to the
//! dashboard's `{"ok": false, "error": …}` envelope put raw JSON in the user's
//! toast:
//!
//! ```text
//! {"detail":"I am missing the permissions to kick this member"}
//! ```
//!
//! Every handler that reports a Percy failure to the browser routes it through
//! [`percy_message`], which unwraps the envelope down to the sentence Percy
//! actually wrote ("I am missing the permissions to kick this member.") and
//! replaces the two variants that carry no user-facing text — a transport
//! failure or a 404 — with something a server admin can act on.

use percy_client::PercyError;
use serde_json::Value;

/// Longest message we'll pass through from Percy. Anything past this is a
/// stack trace or an HTML error page rather than a sentence, so it gets cut
/// rather than dumped into a toast.
const MAX_LEN: usize = 300;

const GENERIC: &str = "Percy could not complete that request. Please try again.";

/// Turns a [`PercyError`] into a sentence that can be shown to a user as-is.
pub(crate) fn percy_message(e: &PercyError) -> String {
    match e {
        PercyError::NotFound => "Percy could not find that — it may have already been deleted.".to_string(),
        PercyError::Http(err) => {
            // reqwest's Display is a transport chain ("error sending request for
            // url (http://127.0.0.1:8090/…)"), which leaks the internal API's
            // address and tells the user nothing. Log it, show a plain sentence.
            tracing::warn!(error = %err, "could not reach Percy's internal API");
            "Could not reach Percy. It may be restarting — please try again in a moment.".to_string()
        }
        PercyError::Api(body) => sentence(&unwrap_envelope(body)),
        PercyError::Rejected { detail, .. } => sentence(detail),
    }
}

/// Peels a Percy/FastAPI error envelope down to its innermost human sentence.
///
/// Percy also nests envelopes when it proxies an upstream failure, e.g.
/// `{"detail":"upload failed: HTTP 400: {\"error\":\"No valid files\",\"code\":1}"}`,
/// so we recurse through the `detail` / `error` / `message` fields *and* the JSON
/// objects embedded in their string values to reach `No valid files`. Falls back
/// to the raw body when it isn't a recognisable envelope.
fn unwrap_envelope(raw: &str) -> String {
    fn deepest(s: &str) -> Option<String> {
        let start = s.find('{')?;
        let end = s.rfind('}')?;
        if end <= start {
            return None;
        }
        let obj: Value = serde_json::from_str(&s[start..=end]).ok()?;
        for key in ["error", "detail", "message"] {
            match obj.get(key) {
                // The common case: a plain sentence, possibly wrapping another envelope.
                Some(Value::String(v)) => return Some(deepest(v).unwrap_or_else(|| v.clone())),
                // FastAPI request validation (422).
                Some(Value::Array(items)) => {
                    if let Some(msg) = validation_errors(items) {
                        return Some(msg);
                    }
                }
                _ => {}
            }
        }
        None
    }

    let msg = deepest(raw).unwrap_or_else(|| raw.trim().to_string());
    // A body with no envelope and no prose — an empty 500, or an HTML error page
    // from a reverse proxy in front of Percy — has nothing worth showing.
    if msg.is_empty() || msg.starts_with('<') {
        return GENERIC.to_string();
    }
    msg
}

/// Renders FastAPI's 422 `detail` array as `field: message; field: message`.
///
/// Each item looks like `{"loc": ["body", "duration"], "msg": "Input should be a
/// valid integer", "type": "int_parsing"}`. The first `loc` element is the request
/// part (`body` / `query` / `path`), which means nothing to a dashboard user, so
/// only the field path after it is kept.
fn validation_errors(items: &[Value]) -> Option<String> {
    let mut out: Vec<String> = Vec::new();
    for item in items {
        let msg = item.get("msg").and_then(Value::as_str)?;
        let field = item
            .get("loc")
            .and_then(Value::as_array)
            .map(|loc| {
                loc.iter()
                    .skip(1) // "body" / "query" / "path"
                    .filter_map(|p| {
                        p.as_str()
                            .map(str::to_string)
                            .or_else(|| p.as_u64().map(|i| i.to_string()))
                    })
                    .collect::<Vec<_>>()
                    .join(".")
            })
            .unwrap_or_default();
        out.push(if field.is_empty() {
            msg.to_string()
        } else {
            format!("{field}: {msg}")
        });
    }
    if out.is_empty() {
        None
    } else {
        Some(out.join("; "))
    }
}

/// Normalises a message into one displayable sentence: trimmed, capitalised,
/// terminated, and bounded in length.
fn sentence(msg: &str) -> String {
    let msg = msg.trim();
    if msg.is_empty() {
        return GENERIC.to_string();
    }

    let mut chars = msg.chars();
    let mut out: String = match chars.next() {
        Some(c) => c.to_uppercase().chain(chars).collect(),
        None => return GENERIC.to_string(),
    };

    if out.chars().count() > MAX_LEN {
        out = out.chars().take(MAX_LEN).collect::<String>().trim_end().to_string();
        out.push('…');
        return out;
    }
    if !out.ends_with(['.', '!', '?', '…', ':']) {
        out.push('.');
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn api(body: &str) -> String {
        percy_message(&PercyError::Api(body.to_string()))
    }

    #[test]
    fn unwraps_fastapi_detail() {
        assert_eq!(
            api(r#"{"detail":"I am missing the permissions to kick this member"}"#),
            "I am missing the permissions to kick this member."
        );
    }

    #[test]
    fn capitalises_and_terminates() {
        assert_eq!(api(r#"{"detail":"member not found"}"#), "Member not found.");
    }

    #[test]
    fn keeps_existing_terminator() {
        assert_eq!(api(r#"{"detail":"Are you sure?"}"#), "Are you sure?");
    }

    #[test]
    fn unwraps_nested_envelope() {
        assert_eq!(
            api(r#"{"detail":"upload failed: HTTP 400: {\"error\":\"No valid files were provided\",\"code\":1}"}"#),
            "No valid files were provided."
        );
    }

    #[test]
    fn renders_validation_errors() {
        let body = r#"{"detail":[
            {"loc":["body","duration"],"msg":"Input should be a valid integer","type":"int_parsing"},
            {"loc":["body","reason"],"msg":"Field required","type":"missing"}
        ]}"#;
        assert_eq!(
            api(body),
            "Duration: Input should be a valid integer; reason: Field required."
        );
    }

    #[test]
    fn falls_back_for_unparseable_bodies() {
        assert_eq!(api(""), GENERIC);
        assert_eq!(api("<html><body>502 Bad Gateway</body></html>"), GENERIC);
    }

    #[test]
    fn passes_through_plain_text() {
        assert_eq!(api("Internal Server Error"), "Internal Server Error.");
    }

    #[test]
    fn truncates_runaway_bodies() {
        let long = "a".repeat(400);
        let msg = api(&format!(r#"{{"detail":"{long}"}}"#));
        assert_eq!(msg.chars().count(), MAX_LEN + 1); // + the ellipsis
        assert!(msg.ends_with('…'));
    }

    #[test]
    fn not_found_is_human() {
        let msg = percy_message(&PercyError::NotFound);
        assert!(!msg.contains('{'), "{msg}");
        assert!(msg.starts_with("Percy could not find that"), "{msg}");
    }
}
