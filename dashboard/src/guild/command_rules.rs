//! Permission rules, custom commands and aliases: one page, plus JSON proxies to
//! Percy's `/command-rules`, `/custom-commands` and `/aliases` endpoints. Percy owns
//! validation (its 400 message is forwarded to the toast) and cache invalidation.

use axum::{
    extract::{Path, Query, State},
    http::StatusCode,
    response::{IntoResponse, Response},
    Json,
};
use percy_client::*;
use serde::Deserialize;

use crate::{
    guilds::gate_and_load_guild,
    mutations::{gate_json, ok_or_err},
    session::Session,
    templates::{CommandRulesTemplate, RuleRow},
    view::Viewer,
    AppState,
};

fn bad_request(message: &str) -> Response {
    (
        StatusCode::BAD_REQUEST,
        Json(serde_json::json!({"ok": false, "error": message})),
    )
        .into_response()
}

/// Resolve a rule's target id to a name the viewer recognises. Users have no cheap
/// lookup, so they stay an id (the bot's `config rules` shows the same).
fn target_name(rule: &CommandRule, guild_name: &str, channels: &[Channel], roles: &[Role]) -> String {
    let found = match rule.target_type.as_str() {
        "guild" => Some(guild_name.to_string()),
        "role" => roles.iter().find(|r| r.id == rule.target_id).map(|r| r.name.clone()),
        "channel" | "category" => channels
            .iter()
            .find(|c| c.id == rule.target_id)
            .map(|c| format!("#{}", c.name)),
        _ => None,
    };
    found.unwrap_or_else(|| rule.target_id.clone())
}

/// `GET /dashboard/guild/:id/command-rules`
pub async fn guild_command_rules(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");
    let (rules, custom, aliases, commands, channels, roles) = tokio::join!(
        percy.get_command_rules(guild_id),
        percy.get_custom_commands(guild_id),
        percy.get_command_aliases(guild_id),
        percy.get_commands(guild_id),
        percy.get_guild_channels(guild_id),
        percy.get_guild_roles(guild_id),
    );
    let channels = channels.unwrap_or_default();
    let mut roles = roles.unwrap_or_default();
    roles.retain(|r| r.id != guild_id.to_string() && !r.managed);
    roles.sort_by_key(|r| std::cmp::Reverse(r.position));
    let rules = rules.map(|r| r.rules).unwrap_or_default();
    // User targets: Percy lists members by id, so `after = id - 1, limit 1` is an exact lookup.
    let user_ids: Vec<u64> = rules
        .iter()
        .filter(|r| r.target_type == "user")
        .filter_map(|r| r.target_id.parse().ok())
        .collect();
    let mut user_names = std::collections::HashMap::new();
    for id in user_ids {
        if let Some(m) = percy
            .get_guild_members(guild_id, 1, id.saturating_sub(1), None)
            .await
            .ok()
            .and_then(|r| r.members.into_iter().next())
        {
            user_names.insert(m.id, m.display_name);
        }
    }
    let rules: Vec<RuleRow> = rules
        .into_iter()
        .map(|r| RuleRow {
            target_name: user_names
                .get(&r.target_id)
                .cloned()
                .unwrap_or_else(|| target_name(&r, &guild.name, &channels, &roles)),
            rule: r,
        })
        .collect();

    // Subjects the picker offers: every command, one `cog:` entry per category.
    let commands = commands.map(|c| c.commands).unwrap_or_default();
    let mut cogs: Vec<String> = commands
        .iter()
        .map(|c| c.category.clone())
        .filter(|c| !c.is_empty() && c != "Uncategorized")
        .collect();
    cogs.sort();
    cogs.dedup();
    let mut names: Vec<String> = commands.into_iter().map(|c| c.name).collect();
    names.sort();

    let custom = custom.unwrap_or_default();
    let mut placeholders: Vec<(String, String)> = custom.placeholders.clone().into_iter().collect();
    placeholders.sort();
    let aliases = aliases.unwrap_or_default();

    CommandRulesTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "command-rules",
        page_title: "Rules & Custom Commands",
        rules,
        subject_commands: names,
        subject_cogs: cogs,
        custom_commands: custom.commands,
        custom_types: custom.types,
        max_commands: custom.max_commands,
        placeholders,
        aliases: aliases.aliases,
        max_aliases: aliases.max_aliases,
        channels,
        roles,
    }
    .into_response()
}

/// `PUT /dashboard/guild/:id/command-rules` — body `{subject, target_type, target_id, allow}`.
pub async fn guild_command_rule_put(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    let text = |k: &str| body.get(k).and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    let (subject, target_type, target_id) = (text("subject"), text("target_type"), text("target_id"));
    let Some(allow) = body.get("allow").and_then(|v| v.as_bool()) else {
        return bad_request("Choose allow or deny.");
    };
    if !["user", "role", "channel", "category", "guild"].contains(&target_type.as_str()) {
        return bad_request("Unknown target type.");
    }
    if subject.is_empty() || target_id.is_empty() {
        return bad_request("Pick a command and a target.");
    }
    ok_or_err(
        percy
            .put_command_rule(guild_id, &subject, &target_type, &target_id, allow)
            .await
            .map(|_| ()),
    )
}

#[derive(Deserialize)]
pub struct RuleKey {
    subject: String,
    target_type: String,
    target_id: String,
}

/// `DELETE /dashboard/guild/:id/command-rules?subject=&target_type=&target_id=`
pub async fn guild_command_rule_delete(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Query(key): Query<RuleKey>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(
        percy
            .delete_command_rule(guild_id, &key.subject, &key.target_type, &key.target_id)
            .await,
    )
}

/// `POST /dashboard/guild/:id/custom-commands` — the session user becomes the owner.
pub async fn guild_custom_command_create(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
    Json(mut body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    if let Some(obj) = body.as_object_mut() {
        obj.insert("owner_id".into(), session.discord_user_id.clone().into());
    }
    ok_or_err(percy.create_custom_command(guild_id, &body).await.map(|_| ()))
}

/// `PATCH /dashboard/guild/:id/custom-commands/:command_id`
pub async fn guild_custom_command_patch(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, command_id)): Path<(u64, i64)>,
    Json(mut body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    if let Some(obj) = body.as_object_mut() {
        obj.remove("owner_id"); // not patchable
    }
    ok_or_err(
        percy
            .patch_custom_command(guild_id, command_id, &body)
            .await
            .map(|_| ()),
    )
}

/// `DELETE /dashboard/guild/:id/custom-commands/:command_id`
pub async fn guild_custom_command_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, command_id)): Path<(u64, i64)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.delete_custom_command(guild_id, command_id).await)
}

/// `PUT /dashboard/guild/:id/aliases/:alias` — body `{expansion}`.
pub async fn guild_alias_put(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, alias)): Path<(u64, String)>,
    Json(body): Json<serde_json::Value>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    let expansion = body.get("expansion").and_then(|v| v.as_str()).unwrap_or("").trim();
    ok_or_err(
        percy
            .put_command_alias(guild_id, &alias, expansion, Some(&session.discord_user_id))
            .await
            .map(|_| ()),
    )
}

/// `DELETE /dashboard/guild/:id/aliases/:alias`
pub async fn guild_alias_delete(
    State(app): State<AppState>,
    session: Session,
    Path((guild_id, alias)): Path<(u64, String)>,
) -> Response {
    let percy = match gate_json(&app, &session, guild_id).await {
        Ok(percy) => percy,
        Err(response) => return response,
    };
    ok_or_err(percy.delete_command_alias(guild_id, &alias).await)
}
