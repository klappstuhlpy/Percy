//! The router table, plus the handful of root routes (`/`, `/health`, `/api-info`).

use axum::{
    extract::State,
    routing::{delete, get, patch, post},
    Json, Router,
};

use crate::*;

/// Builds the router. Split out so a test can assert it composes without route
/// conflicts (mirrors klappstuhl_me's `full_router_builds`).
pub(crate) fn build_router(state: AppState) -> Router {
    Router::new()
        .route("/", get(site::percy_home))
        .route("/watchlist", get(watchlist::watchlist_index))
        .route("/watchlist/:slug", get(watchlist::watch_order))
        .route("/watchlist/:universe/:slug", get(watchlist::watch_title))
        .route("/people/:slug", get(watchlist::person_page))
        .route("/comics", get(comics::comic_browse))
        .route("/comics/series", get(comics::comic_series_index))
        .route("/comics/series/:slug", get(comics::comic_series_detail))
        .route("/comics/:brand/:slug", get(comics::comic_detail))
        // The overview across both catalogues. Public (D9): signed out it renders the
        // same page minus the `Following` section.
        .route("/releases", get(releases::releases_page))
        // The news stream, and the ticker's "All news" target. Public (D9): signed out it
        // is the same page, minus the followed stories leading the list.
        .route("/news", get(news::news_page))
        .route("/dashboard", get(guilds::dashboard))
        .route("/dashboard/guild/:guild_id", get(guilds::guild_detail))
        .route("/dashboard/guild/:guild_id/members", get(guilds::guild_members))
        .route("/dashboard/guild/:guild_id/stats", get(pages::guild_stats))
        .route("/dashboard/guild/:guild_id/commands", get(pages::guild_commands))
        .route("/dashboard/guild/:guild_id/leveling", get(pages::guild_leveling))
        .route("/dashboard/guild/:guild_id/economy", get(pages::guild_economy))
        .route("/dashboard/guild/:guild_id/music", get(pages::guild_music))
        // The music app: members of a shared guild, not just its managers.
        .route("/dashboard/music", get(music_app::music_page))
        .route("/dashboard/music/:guild_id/status", get(music_app::music_status))
        .route("/dashboard/music/:guild_id/viewer", get(music_app::music_viewer))
        .route("/dashboard/music/:guild_id/history", get(music_app::music_history))
        .route("/dashboard/music/:guild_id/search", get(music_app::music_search))
        .route("/dashboard/music/:guild_id/artist", get(music_app::music_artist))
        .route("/dashboard/music/:guild_id/album", get(music_app::music_album))
        .route("/dashboard/music/:guild_id/lyrics", get(music_app::music_lyrics))
        .route(
            "/dashboard/music/:guild_id/recommendations",
            get(music_app::music_recommendations),
        )
        .route("/dashboard/music/:guild_id/play", post(music_app::music_play))
        .route("/dashboard/music/:guild_id/control", post(music_app::music_control))
        .route(
            "/dashboard/music/:guild_id/audio",
            get(music_app::music_audio).post(music_app::music_audio_set),
        )
        .route("/dashboard/music/:guild_id/equalizer", post(music_app::music_equalizer))
        .route("/account/music", get(music_app::account_music))
        .route("/account/music/liked", post(music_app::account_music_like))
        .route("/account/music/liked/remove", post(music_app::account_music_unlike))
        .route(
            "/account/music/playlists/:playlist_id",
            get(music_app::account_music_playlist),
        )
        .route(
            "/dashboard/guild/:guild_id/autoresponders",
            get(pages::guild_autoresponders).post(actions::guild_autoresponders_action),
        )
        .route(
            "/dashboard/guild/:guild_id/comics",
            get(pages::guild_comics).post(crud::guild_comic_create),
        )
        .route(
            "/dashboard/guild/:guild_id/comics/:brand",
            patch(crud::guild_comic_patch).delete(crud::guild_comic_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/comics/:brand/push",
            post(crud::guild_comics_push),
        )
        .route(
            "/dashboard/guild/:guild_id/temp-channels",
            get(pages::guild_temp_channels).post(crud::guild_temp_channel_create),
        )
        .route(
            "/dashboard/guild/:guild_id/temp-channels/:channel_id",
            patch(crud::guild_temp_channel_patch).delete(crud::guild_temp_channel_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/autoresponders/:trigger",
            patch(crud::guild_autoresponder_patch).delete(crud::guild_autoresponder_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/economy/items",
            post(crud::guild_economy_items_create),
        )
        .route(
            "/dashboard/guild/:guild_id/economy/items/:item_id",
            patch(crud::guild_economy_item_patch).delete(crud::guild_economy_item_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/economy/settings",
            post(crud::guild_economy_settings_update),
        )
        .route(
            "/dashboard/guild/:guild_id/economy/lottery",
            post(crud::guild_economy_lottery_create).delete(crud::guild_economy_lottery_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/economy/balances/:user_id",
            patch(crud::guild_economy_balance_update),
        )
        .route(
            "/dashboard/guild/:guild_id/status-feed",
            post(crud::guild_status_feed_subscribe).delete(crud::guild_status_feed_unsubscribe),
        )
        .route(
            "/dashboard/guild/:guild_id/highlights/:user_id",
            delete(crud::guild_highlight_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/polls",
            get(pages::guild_polls).post(actions::guild_poll_create),
        )
        .route(
            "/dashboard/guild/:guild_id/polls/:poll_id",
            post(actions::guild_poll_edit),
        )
        .route(
            "/dashboard/guild/:guild_id/polls/:poll_id/end",
            post(actions::guild_poll_end),
        )
        .route("/dashboard/guild/:guild_id/gallery", get(pages::guild_gallery))
        .route(
            "/dashboard/guild/:guild_id/gallery/upload",
            post(actions::guild_gallery_upload),
        )
        .route(
            "/dashboard/guild/:guild_id/gallery/:image_id/delete",
            post(actions::guild_gallery_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/giveaways",
            get(pages::guild_giveaways).post(actions::guild_giveaway_create),
        )
        .route(
            "/dashboard/guild/:guild_id/giveaways/:giveaway_id/end",
            post(actions::guild_giveaway_end),
        )
        .route(
            "/dashboard/guild/:guild_id/giveaways/:giveaway_id",
            delete(actions::guild_giveaway_delete),
        )
        .route("/dashboard/guild/:guild_id/tags", get(pages::guild_tags))
        .route(
            "/dashboard/guild/:guild_id/tags/import",
            post(actions::guild_tags_import),
        )
        .route(
            "/dashboard/guild/:guild_id/tags/export.csv",
            get(actions::guild_tags_export),
        )
        .route(
            "/dashboard/guild/:guild_id/tags/:tag_id",
            get(actions::guild_tag_detail).delete(actions::guild_tag_delete),
        )
        .route("/dashboard/guild/:guild_id/highlights", get(pages::guild_highlights))
        .route("/dashboard/guild/:guild_id/emoji-stats", get(pages::guild_emoji_stats))
        // Analytics: the page + its two JSON feeds (series for the chart, summary
        // for the headline tiles) that the range/metric pickers re-fetch.
        .route("/dashboard/guild/:guild_id/analytics", get(pages::guild_analytics))
        .route(
            "/dashboard/guild/:guild_id/analytics/series",
            get(integrations::guild_analytics_series),
        )
        .route(
            "/dashboard/guild/:guild_id/analytics/summary",
            get(integrations::guild_analytics_summary),
        )
        // Outbound webhooks (event subscriptions): page + CRUD/test/deliveries.
        .route("/dashboard/guild/:guild_id/webhooks", get(pages::guild_webhooks))
        .route(
            "/dashboard/guild/:guild_id/webhooks/create",
            post(integrations::guild_webhook_create),
        )
        .route(
            "/dashboard/guild/:guild_id/webhooks/:sub_id",
            patch(integrations::guild_webhook_patch).delete(integrations::guild_webhook_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/webhooks/:sub_id/test",
            post(integrations::guild_webhook_test),
        )
        .route(
            "/dashboard/guild/:guild_id/webhooks/:sub_id/deliveries",
            get(integrations::guild_webhook_deliveries),
        )
        // Backup / restore + shareable templates.
        .route("/dashboard/guild/:guild_id/backup", get(pages::guild_backup))
        .route(
            "/dashboard/guild/:guild_id/backup/export.json",
            get(integrations::guild_backup_export),
        )
        .route(
            "/dashboard/guild/:guild_id/backup/import",
            post(integrations::guild_backup_import),
        )
        .route(
            "/dashboard/guild/:guild_id/templates",
            post(integrations::guild_template_publish),
        )
        .route(
            "/dashboard/guild/:guild_id/templates/:slug/apply",
            post(integrations::guild_template_apply),
        )
        .route(
            "/dashboard/guild/:guild_id/templates/:slug",
            delete(integrations::guild_template_delete),
        )
        // Members JSON feed + actions.
        .route(
            "/dashboard/guild/:guild_id/members.json",
            get(actions::guild_members_json),
        )
        .route(
            "/dashboard/guild/:guild_id/members/:user_id/action",
            post(actions::guild_member_action),
        )
        .route(
            "/dashboard/guild/:guild_id/members/:user_id/roles",
            patch(actions::guild_member_roles),
        )
        .route(
            "/dashboard/guild/:guild_id/members/:user_id/messages",
            get(actions::guild_member_messages),
        )
        // Leveling mutations.
        .route(
            "/dashboard/guild/:guild_id/leveling/users/:user_id",
            post(actions::guild_leveling_update),
        )
        .route(
            "/dashboard/guild/:guild_id/leveling/config",
            post(actions::guild_leveling_config_update),
        )
        .route(
            "/dashboard/guild/:guild_id/leveling/test-message",
            post(actions::guild_leveling_test_message),
        )
        .route(
            "/dashboard/guild/:guild_id/leveling/roles",
            post(actions::guild_leveling_roles),
        )
        .route(
            "/dashboard/guild/:guild_id/leveling/roles/preset",
            post(actions::guild_leveling_roles_preset),
        )
        .route(
            "/dashboard/guild/:guild_id/leveling/multipliers",
            post(actions::guild_leveling_multipliers),
        )
        .route(
            "/dashboard/guild/:guild_id/leveling/blacklist",
            post(actions::guild_leveling_blacklist),
        )
        // Music controls + live-player JSON.
        .route(
            "/dashboard/guild/:guild_id/music/status",
            get(actions::guild_music_status),
        )
        .route(
            "/dashboard/guild/:guild_id/music/control",
            post(actions::guild_music_control),
        )
        .route(
            "/dashboard/guild/:guild_id/music/lyrics",
            get(actions::guild_music_lyrics),
        )
        .route(
            "/dashboard/guild/:guild_id/music/setup",
            post(actions::guild_music_setup),
        )
        .route(
            "/dashboard/guild/:guild_id/music/reset",
            post(actions::guild_music_reset),
        )
        .route("/dashboard/guild/:guild_id/music/247", post(actions::guild_music_247))
        .route(
            "/dashboard/guild/:guild_id/music/dj-mode",
            patch(actions::guild_music_dj_mode),
        )
        // Moderation audit log + cases, bulk actions, activity/avatars, exports.
        .route("/dashboard/guild/:guild_id/audit-log", get(moderation::guild_audit_log))
        .route(
            "/dashboard/guild/:guild_id/audit-log.json",
            get(moderation::guild_audit_log_json),
        )
        .route(
            "/dashboard/guild/:guild_id/audit-log/recent",
            get(moderation::guild_cases_recent),
        )
        .route("/dashboard/guild/:guild_id/cases", post(moderation::guild_case_create))
        .route(
            "/dashboard/guild/:guild_id/cases/:case_index",
            patch(moderation::guild_case_update).delete(moderation::guild_case_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/members/bulk-action",
            post(moderation::guild_bulk_action),
        )
        .route(
            "/dashboard/guild/:guild_id/members/:user_id/activity",
            get(moderation::guild_member_activity),
        )
        .route(
            "/dashboard/guild/:guild_id/members/:user_id/avatars",
            get(moderation::guild_member_avatars),
        )
        .route(
            "/dashboard/guild/:guild_id/export/leaderboard.csv",
            get(moderation::guild_export_leaderboard),
        )
        .route(
            "/dashboard/guild/:guild_id/export/cases.csv",
            get(moderation::guild_export_cases),
        )
        .route(
            "/dashboard/guild/:guild_id/custom-bot",
            patch(moderation::guild_custom_bot_update),
        )
        .route(
            "/dashboard/guild/:guild_id/custom-bot/reset",
            post(moderation::guild_custom_bot_reset),
        )
        .route("/dashboard/guild/:guild_id/changes", get(changes::guild_changes))
        // Developers tab: API token create / list / revoke (the API itself is `api::routes`).
        .route("/dashboard/guild/:guild_id/developers", get(api::developers_page))
        .route("/dashboard/guild/:guild_id/developers/tokens", post(api::token_create))
        .route(
            "/dashboard/guild/:guild_id/developers/tokens/:token_id",
            delete(api::token_revoke),
        )
        .route(
            "/dashboard/guild/:guild_id/notifications",
            get(notifications::guild_notifications),
        )
        .route(
            "/dashboard/guild/:guild_id/notifications/summary",
            get(notifications::notifications_summary),
        )
        .route(
            "/dashboard/guild/:guild_id/notifications/read",
            post(notifications::notifications_read),
        )
        .route(
            "/dashboard/guild/:guild_id/requests/:rid/decide",
            post(notifications::request_decide),
        )
        .route(
            "/dashboard/guild/:guild_id/request-settings",
            patch(notifications::request_settings_update),
        )
        .route(
            "/dashboard/guild/:guild_id/role-menus",
            get(community::guild_role_menus).post(community::guild_role_menu_create),
        )
        .route(
            "/dashboard/guild/:guild_id/role-menus/:menu_id",
            post(community::guild_role_menu_update),
        )
        .route(
            "/dashboard/guild/:guild_id/role-menus/:menu_id/roles",
            post(community::guild_role_menu_add_role),
        )
        .route(
            "/dashboard/guild/:guild_id/role-menus/:menu_id/roles/:role_id/delete",
            post(community::guild_role_menu_remove_role),
        )
        .route(
            "/dashboard/guild/:guild_id/role-menus/:menu_id/delete",
            post(community::guild_role_menu_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/starboard",
            get(community::guild_starboard).post(community::guild_starboard_update),
        )
        .route(
            "/dashboard/guild/:guild_id/tags/create",
            post(community::guild_tag_create),
        )
        .route(
            "/dashboard/guild/:guild_id/tags/:tag_id/edit",
            post(community::guild_tag_edit),
        )
        .route(
            "/dashboard/guild/:guild_id/me/reminders/:reminder_id/delete",
            post(community::guild_user_reminder_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/setup/logging",
            post(actions::guild_setup_logging),
        )
        .route(
            "/dashboard/guild/:guild_id/test-message",
            post(actions::guild_test_message),
        )
        .route(
            "/dashboard/guild/:guild_id/setup/leveling",
            post(actions::guild_setup_leveling),
        )
        .route(
            "/dashboard/guild/:guild_id/setup/sentinel",
            post(actions::guild_setup_sentinel),
        )
        .route("/dashboard/guild/:guild_id/pickers.json", get(guilds::guild_pickers))
        // The signed-in member's own self / history / data pages.
        .route("/dashboard/guild/:guild_id/me", get(userpages::guild_user_self))
        .route(
            "/dashboard/guild/:guild_id/me/settings",
            post(userpages::guild_user_settings_update),
        )
        .route(
            "/dashboard/guild/:guild_id/me/rank-card",
            post(userpages::guild_user_rank_card_update),
        )
        .route(
            "/dashboard/guild/:guild_id/me/rank-card.png",
            get(userpages::guild_user_rank_card_preview),
        )
        .route(
            "/dashboard/guild/:guild_id/me/history",
            get(userpages::guild_user_history),
        )
        .route(
            "/dashboard/guild/:guild_id/me/data-export",
            get(userpages::guild_user_data_export),
        )
        .route(
            "/dashboard/guild/:guild_id/me/delete-data",
            post(userpages::guild_user_data_delete),
        )
        // The read-only public overview + its shared live-player endpoints.
        .route("/dashboard/guild/:guild_id/overview", get(userpages::guild_overview))
        .route(
            "/dashboard/guild/:guild_id/overview/music",
            get(userpages::guild_overview_music_status),
        )
        .route(
            "/dashboard/guild/:guild_id/overview/music/control",
            post(userpages::guild_overview_music_control),
        )
        .route(
            "/dashboard/guild/:guild_id/overview/music/lyrics",
            get(userpages::guild_overview_music_lyrics),
        )
        // A moderator's read of a single member (bare :user_id under /members).
        .route(
            "/dashboard/guild/:guild_id/members/:user_id",
            get(userpages::guild_user_lookup),
        )
        // Config + AI + sentinel mutations (form/JSON POST).
        .route(
            "/dashboard/guild/:guild_id/config",
            post(mutations::guild_config_update),
        )
        .route("/dashboard/guild/:guild_id/ai", post(mutations::guild_ai_flags_update))
        .route("/dashboard/guild/:guild_id/ai/ask", post(mutations::guild_ai_ask))
        .route(
            "/dashboard/guild/:guild_id/ai/override",
            post(mutations::guild_ai_override_update),
        )
        .route(
            "/dashboard/guild/:guild_id/ai/override/:channel_id/delete",
            post(mutations::guild_ai_override_delete),
        )
        .route(
            "/dashboard/guild/:guild_id/sentinel",
            post(mutations::guild_sentinel_update),
        )
        // Commands / moderation / lockdown mutations (JSON POST/PATCH).
        .route(
            "/dashboard/guild/:guild_id/commands/toggle",
            post(mutations::guild_command_toggle),
        )
        .route(
            "/dashboard/guild/:guild_id/commands/permissions",
            post(mutations::guild_command_permissions),
        )
        .route("/dashboard/guild/:guild_id/plonks", post(mutations::guild_plonk_manage))
        .route(
            "/dashboard/guild/:guild_id/lockdowns/lock",
            post(mutations::guild_lockdown_lock),
        )
        .route(
            "/dashboard/guild/:guild_id/lockdowns/unlock",
            post(mutations::guild_lockdown_unlock),
        )
        .route(
            "/dashboard/guild/:guild_id/moderation/ignore",
            post(mutations::guild_moderation_ignore),
        )
        .route(
            "/dashboard/guild/:guild_id/audit-log-flags",
            patch(mutations::guild_audit_log_flags),
        )
        // Public leaderboard (no login) + vanity slug management.
        .route("/lb/:target", get(leaderboard::public_leaderboard))
        .route(
            "/lb/:guild_id/vanity",
            post(leaderboard::public_leaderboard_vanity_claim).delete(leaderboard::public_leaderboard_vanity_delete),
        )
        // Public marketing / site pages.
        .route("/commands", get(site::percy_commands))
        .route("/changelog", get(site::percy_changelog))
        .route("/support", get(site::percy_support_server))
        .route("/privacy-policy", get(site::percy_privacy))
        .route("/terms-of-service", get(site::percy_terms))
        .route("/dashboard/bot-version", get(site::percy_bot_version))
        .route("/login", get(auth::login))
        .route("/logout", get(auth::logout))
        .route("/auth/discord/callback", get(auth::callback))
        .route("/auth/handoff", get(auth::handoff))
        .route("/health", get(health))
        .route("/account", get(userpages::account_page))
        .route("/account/settings", post(userpages::account_settings_update))
        .route("/account/data-export", get(userpages::account_data_export))
        .route("/account/delete-data", post(userpages::account_data_delete))
        .route("/account/discord/avatar", get(auth::discord_avatar))
        // Release subscriptions: the page, its picker feed, and the three one-off
        // mutations (subscribe / retune / unfollow), each a plain form post.
        .route(
            "/account/subscriptions",
            get(subscriptions::subscriptions_page).post(subscriptions::subscription_create),
        )
        .route(
            "/account/subscriptions/update",
            post(subscriptions::subscription_update),
        )
        .route(
            "/account/subscriptions/remove",
            post(subscriptions::subscription_delete),
        )
        .route("/account/subscriptions/search", get(subscriptions::subscription_search))
        // Read/watched marks (R8). Two plain form posts plus the JSON read `progress.js`
        // reconciles against — the same endpoints whether or not JS is running.
        .route(
            "/account/progress",
            get(progress::progress_list).post(progress::progress_mark),
        )
        .route("/account/progress/remove", post(progress::progress_unmark))
        .route("/api-info", get(api_info))
        // The public developer API (`/api/v1/*`) and its docs (`/api`).
        .merge(api::routes(state.clone()))
        .nest("/kls", kls_ui::routes())
        // The dashboard's own app-specific assets (percy CSS/JS/img), shipped as a
        // `static/` dir next to the binary. The shared design system lives at /kls.
        .nest_service("/static", tower_http::services::ServeDir::new("static"))
        // Every successful guild-scoped mutation lands in the change log (src/changes.rs).
        .layer(axum::middleware::from_fn_with_state(state.clone(), changes::record))
        .with_state(state)
}

/// Liveness probe that also confirms the kernel database is serving queries.
async fn health(State(state): State<AppState>) -> Json<serde_json::Value> {
    // `execute_batch` steps the statement and ignores any returned rows, so a
    // row-yielding probe like `SELECT 1` is fine (plain `execute` would error).
    let db_ok = state.db.execute_batch("SELECT 1;").await.is_ok();
    Json(serde_json::json!({ "status": "ok", "database": db_ok }))
}

/// Reports whether the Percy API client is configured — the dashboard's only
/// hard runtime dependency, and optional at boot.
async fn api_info(State(state): State<AppState>) -> Json<serde_json::Value> {
    Json(serde_json::json!({ "percy_configured": state.percy.is_some() }))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A hermetic in-memory state for tests — never touches the real
    /// `dashboard.db` on disk or the operator's `config.json`.
    async fn test_state() -> AppState {
        build_state_with(Config::test_default(), Path::new(":memory:"))
            .await
            .expect("build state")
    }

    /// The router must compose without panicking (route/state conflicts).
    #[tokio::test]
    async fn router_builds() {
        let _ = build_router(test_state().await);
    }

    /// The database is live and its migrated schema is present through shared state.
    #[tokio::test]
    async fn health_state_is_live() {
        let state = test_state().await;
        assert!(state.db.execute_batch("SELECT 1;").await.is_ok());
        // The migration runner applied the dashboard-owned schema.
        assert!(
            state
                .db
                .execute_batch("SELECT slug FROM percy_leaderboard_vanity LIMIT 0;")
                .await
                .is_ok(),
            "migrated schema not present"
        );
        assert!(state.percy.is_none(), "no Percy client without a configured URL");
    }
}
