//! Percy bot internal API client.
//!
//! Proxies dashboard requests to Percy's aiohttp internal API, which owns all
//! guild config mutations and cache invalidation. The client authenticates with
//! a pre-shared bearer token supplied by the caller at construction.
//!
//! Response models live in [`types`].
//!
//! Every endpoint method is a one-liner over three shared helpers: [`PercyClient::send_into`]
//! (deserialize the JSON body), [`PercyClient::send_unit`] (discard the body), and the
//! associated [`PercyClient::check`] (uniform status → [`PercyError`] mapping). `404` maps to
//! [`PercyError::NotFound`]; every other 4xx/5xx maps to [`PercyError::Api`] carrying Percy's
//! response body, so upstream validation errors reach the dashboard.

use reqwest::{Client, RequestBuilder, Response};
use serde::de::DeserializeOwned;

mod types;
pub use types::*;

// Generated from Percy's OpenAPI schema (see GENERATION.md). Only the domains already migrated
// are re-exported, by name, so the hand-written `types` and the generated types never collide and
// the dashboard keeps importing `percy_client::AnalyticsSummary` unchanged.
#[rustfmt::skip]
pub mod generated;
pub use generated::{AnalyticsMetric, AnalyticsPoint, AnalyticsSeries, AnalyticsSummary, AnalyticsTopCommand};
pub use generated::{
    // watchlist / releases / comics / news domains
    CardSubject,
    ComicBrowseFilters,
    ComicBrowsePage,
    ComicCharacter,
    ComicCredit,
    ComicDetail,
    ComicDetailSeries,
    ComicNameEntry,
    ComicNamesResponse,
    ComicRelease,
    ComicSeriesList,
    ComicSeriesPage,
    ComicSeriesSummary,
    NewsFilters,
    NewsItem,
    NewsItemSource,
    NewsPage,
    NewsRelated,
    NewsSource,
    NewsSourcesResponse,
    NewsSubject,
    OverviewWindow,
    ProgressMark,
    ProgressResponse,
    ReleaseCard,
    ReleaseProgressDeleted,
    ReleasesOverview,
    Subject,
    SubjectsResponse,
    Subscription,
    SubscriptionDeleted,
    SubscriptionsResponse,
    WatchCredit,
    WatchEra,
    WatchPath,
    WatchPeopleResponse,
    WatchPerson,
    WatchPersonCredit,
    WatchPersonPage,
    WatchPersonSummary,
    WatchPrefs,
    WatchProgressDeleted,
    WatchProgressEntry,
    WatchProgressResponse,
    WatchProvider,
    WatchStats,
    WatchTitle,
    WatchTitleDetail,
    WatchTrailer,
    WatchUniverse,
    WatchUniversePage,
    WatchUniverseSummary,
    WatchUniversesResponse,
};

/// A typed client for Percy's internal API.
#[derive(Clone)]
pub struct PercyClient {
    client: Client,
    base_url: String,
    token: String,
}

/// Percent-encode one value as a single URL path segment: anything outside the
/// RFC 3986 unreserved set (slash, backslash, `?`, `#`, `%`, `.`, spaces...) is escaped.
/// Without this a caller-supplied id like `../../guilds/9/x` re-routes the request.
fn seg(v: &impl std::fmt::Display) -> String {
    let mut out = String::new();
    for b in v.to_string().bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'~' => out.push(b as char),
            _ => out.push_str(&format!("%{b:02X}")),
        }
    }
    out
}

impl PercyClient {
    /// Creates a new client from a base URL and bearer token. The base URL has
    /// any trailing slash trimmed so path joins stay canonical. Callers that
    /// hold an optional/partial config should gate construction themselves (see
    /// `PercyConfig::build_client` on the klappstuhl_me side).
    pub fn new(client: Client, base_url: impl Into<String>, token: impl Into<String>) -> Self {
        Self {
            client,
            base_url: base_url.into().trim_end_matches('/').to_string(),
            token: token.into(),
        }
    }

    fn url(&self, path: &str) -> String {
        format!("{}{path}", self.base_url)
    }

    // -- Shared request plumbing ---------------------------------------------

    /// Maps Percy's HTTP status into a [`PercyError`], returning the response for
    /// the caller to deserialize on success. `404` → [`PercyError::NotFound`]; any
    /// other 4xx/5xx → [`PercyError::Api`] carrying the response body so Percy's
    /// error text surfaces to the dashboard.
    async fn check(resp: Response) -> Result<Response, PercyError> {
        let status = resp.status();
        if status == reqwest::StatusCode::NOT_FOUND {
            return Err(PercyError::NotFound);
        }
        if status.is_client_error() || status.is_server_error() {
            return Err(PercyError::Api(resp.text().await.unwrap_or_default()));
        }
        Ok(resp)
    }

    /// Applies auth, sends `req`, and discards the body. For mutations / fire-and-forget.
    async fn send_unit(&self, req: RequestBuilder) -> Result<(), PercyError> {
        Self::check(req.bearer_auth(&self.token).send().await?).await?;
        Ok(())
    }

    /// Applies auth, sends `req`, and deserializes the JSON body into `T`.
    async fn send_into<T: DeserializeOwned>(&self, req: RequestBuilder) -> Result<T, PercyError> {
        let resp = Self::check(req.bearer_auth(&self.token).send().await?).await?;
        Ok(resp.json().await?)
    }

    // -- Live event stream ---------------------------------------------------

    /// Opens Percy's server-sent event stream for a guild (`topics` is a comma list, empty = all)
    /// and returns the response un-consumed: read it with `bytes_stream()`.
    ///
    /// The shared client's 30 s total timeout would kill a stream, so this request carries its
    /// own much longer one (the connect timeout still applies). ponytail: the stream is recycled
    /// after 12 h; the browser's `EventSource` reconnects on its own.
    pub async fn events_stream(&self, guild_id: u64, topics: &str) -> Result<Response, PercyError> {
        let mut req = self
            .client
            .get(self.url(&format!("/api/v1/guilds/{}/events", seg(&guild_id))))
            .timeout(std::time::Duration::from_secs(12 * 60 * 60));
        if !topics.is_empty() {
            req = req.query(&[("topics", topics)]);
        }
        Self::check(req.bearer_auth(&self.token).send().await?).await
    }

    // -- Notifications and action requests -----------------------------------

    /// A user's notification feed (newest first); `before` is the id cursor for the next page.
    pub async fn get_notifications(
        &self,
        guild_id: u64,
        user_id: u64,
        limit: u32,
        before: Option<&str>,
    ) -> Result<NotificationList, PercyError> {
        let mut query = vec![("user_id", user_id.to_string()), ("limit", limit.to_string())];
        if let Some(b) = before {
            query.push(("before", b.to_string()));
        }
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/notifications", seg(&guild_id))))
                .query(&query),
        )
        .await
    }

    /// Unread count and pending-request count for the nav badge.
    pub async fn get_notification_summary(
        &self,
        guild_id: u64,
        user_id: u64,
    ) -> Result<NotificationSummary, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/notifications/summary", seg(&guild_id))))
                .query(&[("user_id", user_id)]),
        )
        .await
    }

    /// Mark everything up to and including `up_to_id` read. Returns `{ok, unread}`.
    pub async fn mark_notifications_read(
        &self,
        guild_id: u64,
        user_id: u64,
        up_to_id: &str,
    ) -> Result<serde_json::Value, PercyError> {
        // Percy wants an integer; send a number when the cursor parses, else let Percy 422 it.
        let up_to = up_to_id
            .parse::<i64>()
            .map_or_else(|_| serde_json::Value::from(up_to_id), serde_json::Value::from);
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/notifications/read", seg(&guild_id))))
                .json(&serde_json::json!({ "user_id": user_id, "up_to_id": up_to })),
        )
        .await
    }

    /// Action requests; `status` is `pending` or `all`.
    pub async fn get_action_requests(
        &self,
        guild_id: u64,
        status: &str,
        limit: u32,
    ) -> Result<ActionRequestList, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/requests", seg(&guild_id))))
                .query(&[("status", status.to_string()), ("limit", limit.to_string())]),
        )
        .await
    }

    /// Approve or deny a request. A refusal (400/403/404/409) is returned as `Ok` with
    /// `ok == false` and Percy's `message`, so the dashboard can show why; transport and other
    /// errors stay `Err`.
    pub async fn decide_action_request(
        &self,
        guild_id: u64,
        request_id: &str,
        moderator_id: u64,
        approve: bool,
        note: Option<&str>,
    ) -> Result<RequestDecision, PercyError> {
        let req = self
            .client
            .post(self.url(&format!(
                "/api/v1/guilds/{}/requests/{}/decide",
                seg(&guild_id),
                seg(&request_id)
            )))
            .json(&serde_json::json!({
                "moderator_id": moderator_id,
                "decision": if approve { "approve" } else { "deny" },
                "note": note,
            }));
        let resp = req.bearer_auth(&self.token).send().await?;
        let status = resp.status().as_u16();
        if !matches!(status, 400 | 403 | 404 | 409) {
            return Ok(Self::check(resp).await?.json().await?);
        }
        let body = resp.text().await.unwrap_or_default();
        Ok(refusal_from_body(&body))
    }

    /// Where requests are posted and which roles may file them.
    pub async fn get_request_settings(&self, guild_id: u64) -> Result<RequestSettings, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/requests/settings", seg(&guild_id)))),
        )
        .await
    }

    /// Replace both settings; `body` must carry `channel_id` and `role_ids`.
    pub async fn update_request_settings(
        &self,
        guild_id: u64,
        body: &serde_json::Value,
    ) -> Result<RequestSettings, PercyError> {
        self.send_into(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/requests/settings", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    // -- Guild config --------------------------------------------------------

    /// Fetch full guild config + metadata from Percy.
    pub async fn get_guild(&self, guild_id: u64) -> Result<GuildInfo, PercyError> {
        self.send_into(self.client.get(self.url(&format!("/api/v1/guilds/{}", seg(&guild_id)))))
            .await
    }

    /// Patch guild config fields.
    pub async fn patch_guild_config(&self, guild_id: u64, patch: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/config", seg(&guild_id))))
                .json(patch),
        )
        .await
    }

    /// Apply multiple config mutations atomically in one request.
    pub async fn batch_guild_config(
        &self,
        guild_id: u64,
        operations: &[BatchOperation],
    ) -> Result<BatchResponse, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/batch", seg(&guild_id))))
                .json(&serde_json::json!({"operations": operations})),
        )
        .await
    }

    /// Toggle the server-wide AI feature flags. `patch` is `{ "flags": { name: bool, … } }`.
    pub async fn patch_ai_flags(&self, guild_id: u64, patch: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/ai", seg(&guild_id))))
                .json(patch),
        )
        .await
    }

    /// Create or replace a per-channel AI override. An override controlling nothing is removed.
    pub async fn put_ai_override(
        &self,
        guild_id: u64,
        channel_id: u64,
        body: &serde_json::Value,
    ) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .put(self.url(&format!(
                    "/api/v1/guilds/{}/ai/overrides/{}",
                    seg(&guild_id),
                    seg(&channel_id)
                )))
                .json(body),
        )
        .await
    }

    /// Remove a per-channel AI override.
    pub async fn delete_ai_override(&self, guild_id: u64, channel_id: u64) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/ai/overrides/{}",
            seg(&guild_id),
            seg(&channel_id)
        ))))
        .await
    }

    /// Ask the dashboard AI assistant a natural-language question (command palette).
    /// Percy gates this on the guild's `assistant` AI flag + engine health and never errors on
    /// an unavailable assistant — it returns `available: false` with a reason instead.
    pub async fn ai_ask(&self, guild_id: u64, question: &str) -> Result<AiAskResponse, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/ai/ask", seg(&guild_id))))
                .json(&serde_json::json!({ "question": question })),
        )
        .await
    }

    /// Update audit log event flags.
    pub async fn patch_audit_log_flags(
        &self,
        guild_id: u64,
        flags: &std::collections::HashMap<String, bool>,
    ) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/audit-log-flags", seg(&guild_id))))
                .json(flags),
        )
        .await
    }

    /// Fetch guild roles.
    pub async fn get_guild_roles(&self, guild_id: u64) -> Result<Vec<Role>, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/roles", seg(&guild_id)))),
        )
        .await
    }

    /// Fetch guild channels.
    pub async fn get_guild_channels(&self, guild_id: u64) -> Result<Vec<Channel>, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/channels", seg(&guild_id)))),
        )
        .await
    }

    /// Fetch guilds the given Discord user can manage (has Manage Server or Admin).
    pub async fn get_user_guilds(&self, discord_user_id: &str) -> Result<Vec<UserGuild>, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/users/{}/guilds", seg(&discord_user_id)))),
        )
        .await
    }

    /// Fetch a user's *current* avatar URL straight from the bot. Resolved live so
    /// it never goes stale; the dashboard does not persist avatars.
    pub async fn get_user_avatar(&self, discord_user_id: &str) -> Result<UserAvatar, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/users/{}/avatar", seg(&discord_user_id)))),
        )
        .await
    }

    // -- Members -------------------------------------------------------------

    /// Fetch guild members (paginated, optionally filtered by search term).
    pub async fn get_guild_members(
        &self,
        guild_id: u64,
        limit: u32,
        after: u64,
        search: Option<&str>,
    ) -> Result<MembersResponse, PercyError> {
        let mut query = vec![("limit", limit.to_string()), ("after", after.to_string())];
        if let Some(s) = search {
            if !s.is_empty() {
                query.push(("search", s.to_string()));
            }
        }
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/members", seg(&guild_id))))
                .query(&query),
        )
        .await
    }

    /// Perform a moderation action on a guild member.
    pub async fn member_action(
        &self,
        guild_id: u64,
        user_id: &str,
        action: &str,
        reason: Option<&str>,
        moderator_id: Option<&str>,
    ) -> Result<(), PercyError> {
        let mut body = serde_json::json!({"action": action});
        if let Some(r) = reason {
            body["reason"] = serde_json::Value::String(r.to_string());
        }
        if let Some(m) = moderator_id {
            body["moderator_id"] = serde_json::Value::String(m.to_string());
        }
        self.send_unit(
            self.client
                .post(self.url(&format!(
                    "/api/v1/guilds/{}/members/{}/action",
                    seg(&guild_id),
                    seg(&user_id)
                )))
                .json(&body),
        )
        .await
    }

    /// Perform a moderation action on a guild member, forwarding the caller's body verbatim.
    ///
    /// The richer actions (`timeout`, `mute`, `warn`, `softban`, `purge`, …) carry
    /// per-action parameters — `duration`, `delete_message_days`, `limit` — so the dashboard
    /// passes the whole validated body through rather than growing an argument per action.
    /// Percy owns the action whitelist and rejects anything it does not know.
    pub async fn member_action_json(
        &self,
        guild_id: u64,
        user_id: &str,
        body: &serde_json::Value,
    ) -> Result<serde_json::Value, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!(
                    "/api/v1/guilds/{}/members/{}/action",
                    seg(&guild_id),
                    seg(&user_id)
                )))
                .json(body),
        )
        .await
    }

    /// Fetch a member's recent messages (a live, bounded scan — see [`MemberMessages`]).
    pub async fn get_member_messages(
        &self,
        guild_id: u64,
        user_id: &str,
        limit: u32,
    ) -> Result<MemberMessages, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!(
                    "/api/v1/guilds/{}/members/{}/messages",
                    seg(&guild_id),
                    seg(&user_id)
                )))
                .query(&[("limit", limit.to_string())]),
        )
        .await
    }

    /// Update a member's roles.
    /// Give and take a member's roles as `moderator_id` (the signed-in dashboard user), who
    /// needs Manage Roles and may only touch roles below their own top role.
    pub async fn member_roles(
        &self,
        guild_id: u64,
        user_id: &str,
        add: &[String],
        remove: &[String],
        moderator_id: &str,
    ) -> Result<(), PercyError> {
        let body = serde_json::json!({"add": add, "remove": remove, "moderator_id": moderator_id});
        self.send_unit(
            self.client
                .patch(self.url(&format!(
                    "/api/v1/guilds/{}/members/{}/roles",
                    seg(&guild_id),
                    seg(&user_id)
                )))
                .json(&body),
        )
        .await
    }

    /// Fetch an aggregated member profile (identity, leveling, moderation history).
    pub async fn get_member_detail(&self, guild_id: u64, user_id: &str) -> Result<MemberDetail, PercyError> {
        self.send_into(self.client.get(self.url(&format!(
            "/api/v1/guilds/{}/members/{}/detail",
            seg(&guild_id),
            seg(&user_id)
        ))))
        .await
    }

    /// Fetch a member's own profile (leveling, economy, command stats — no mod history).
    pub async fn get_member_self(&self, guild_id: u64, user_id: &str) -> Result<MemberSelf, PercyError> {
        self.send_into(self.client.get(self.url(&format!(
            "/api/v1/guilds/{}/members/{}/self",
            seg(&guild_id),
            seg(&user_id)
        ))))
        .await
    }

    pub async fn get_user_settings(&self, discord_id: &str) -> Result<UserSettings, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/users/{}/settings", seg(&discord_id)))),
        )
        .await
    }

    pub async fn patch_user_settings(&self, discord_id: &str, patch: &UserSettingsPatch) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!("/api/v1/users/{}/settings", seg(&discord_id))))
                .json(patch),
        )
        .await
    }

    /// A member's rank card style on one server, with the presets to choose from.
    pub async fn get_rank_card(&self, discord_id: &str, guild_id: u64) -> Result<RankCard, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/users/{}/rank-card", seg(&discord_id))))
                .query(&[("guild_id", guild_id.to_string())]),
        )
        .await
    }

    /// Replace a member's (global) rank card style; `{}` follows the server default.
    pub async fn put_rank_card(
        &self,
        discord_id: &str,
        style: &std::collections::HashMap<String, String>,
    ) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .put(self.url(&format!("/api/v1/users/{}/rank-card", seg(&discord_id))))
                .json(&serde_json::json!({ "style": style })),
        )
        .await
    }

    /// Render the member's card in a draft style without storing it; returns PNG bytes.
    pub async fn preview_rank_card(
        &self,
        discord_id: &str,
        guild_id: u64,
        style: &std::collections::HashMap<String, String>,
    ) -> Result<Vec<u8>, PercyError> {
        let req = self
            .client
            .post(self.url(&format!("/api/v1/users/{}/rank-card/preview", seg(&discord_id))))
            .json(&serde_json::json!({ "guild_id": guild_id, "style": style }));
        let resp = Self::check(req.bearer_auth(&self.token).send().await?).await?;
        Ok(resp.bytes().await?.to_vec())
    }

    /// Fetch a user's avatar history (base64 images + timestamps).
    pub async fn get_member_avatars(
        &self,
        guild_id: u64,
        user_id: &str,
        limit: u32,
    ) -> Result<AvatarHistoryResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!(
                    "/api/v1/guilds/{}/members/{}/avatars",
                    seg(&guild_id),
                    seg(&user_id)
                )))
                .query(&[("limit", limit.to_string())]),
        )
        .await
    }

    /// Fetch a user's consent-tracked history (names, avatars, presence).
    pub async fn get_user_history(&self, discord_id: &str) -> Result<UserHistory, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/users/{}/history", seg(&discord_id)))),
        )
        .await
    }

    /// Fetch the full GDPR-style data export for a user (raw JSON passthrough).
    pub async fn export_user_data(&self, discord_id: &str) -> Result<serde_json::Value, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/users/{}/data-export", seg(&discord_id)))),
        )
        .await
    }

    /// Permanently delete a user's stored presence/avatar/name history.
    pub async fn delete_user_personal_data(&self, discord_id: &str) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .delete(self.url(&format!("/api/v1/users/{}/personal-data", seg(&discord_id)))),
        )
        .await
    }

    // -- Sentinel ----------------------------------------------------------

    /// Fetch sentinel configuration for a guild.
    pub async fn get_sentinel(&self, guild_id: u64) -> Result<Option<SentinelInfo>, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/sentinel", seg(&guild_id)))),
        )
        .await
    }

    /// Patch sentinel configuration. Returns Percy's warnings about the saved setup (e.g. the
    /// verification channel is still visible to @everyone), to be shown to the user.
    pub async fn patch_sentinel(&self, guild_id: u64, patch: &serde_json::Value) -> Result<Vec<String>, PercyError> {
        let saved: SentinelSaved = self
            .send_into(
                self.client
                    .patch(self.url(&format!("/api/v1/guilds/{}/sentinel", seg(&guild_id))))
                    .json(patch),
            )
            .await?;
        Ok(saved.warnings)
    }

    /// Send the sentinel verification message to a channel; returns its message id plus Percy's
    /// warnings about the channel (see [`Self::patch_sentinel`]).
    pub async fn send_sentinel_message(
        &self,
        guild_id: u64,
        channel_id: u64,
        title: &str,
        content: &str,
    ) -> Result<(u64, Vec<String>), PercyError> {
        let body = serde_json::json!({
            "channel_id": channel_id,
            "guild_id": guild_id,
            "title": title,
            "content": content,
        });
        let sent: SentinelSaved = self
            .send_into(
                self.client
                    .post(self.url(&format!("/api/v1/guilds/{}/sentinel/message", seg(&guild_id))))
                    .json(&body),
            )
            .await?;
        Ok((sent.message_id, sent.warnings))
    }

    /// Enable or disable the sentinel.
    pub async fn toggle_sentinel(&self, guild_id: u64, enabled: bool) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/sentinel/toggle", seg(&guild_id))))
                .json(&serde_json::json!({"enabled": enabled})),
        )
        .await
    }

    // -- Leveling ------------------------------------------------------------

    /// Per-feature health: can Percy post/assign where this server configured it to (live, uncached).
    pub async fn get_guild_health(&self, guild_id: u64) -> Result<GuildHealth, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/health", seg(&guild_id)))),
        )
        .await
    }

    /// Point the chosen logs (`audit`/`mod`/`message`/`voice`/`alerts`) at one channel; with
    /// `channel_id: None` Percy creates a private `#percy-logs` first.
    pub async fn setup_logging(
        &self,
        guild_id: u64,
        channel_id: Option<u64>,
        logs: &[String],
    ) -> Result<ActionMessage, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/setup/logging", seg(&guild_id))))
                .json(&serde_json::json!({ "channel_id": channel_id, "logs": logs })),
        )
        .await
    }

    /// Post one harmless test embed to a posting feature's saved destination
    /// (`audit_log`/`alerts`/`mod_log`/`message_log`/`voice_log`/`polls`).
    pub async fn send_test_message(&self, guild_id: u64, feature: &str) -> Result<ActionMessage, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/test-message", seg(&guild_id))))
                .json(&serde_json::json!({ "feature": feature })),
        )
        .await
    }

    /// Turn leveling on in one step. `level_up_channel`: 0 none, 1 where they talked, 2 DM, else a text channel id.
    pub async fn setup_leveling(
        &self,
        guild_id: u64,
        level_up_channel: i64,
        preset_roles: bool,
    ) -> Result<ActionMessage, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/setup/leveling", seg(&guild_id))))
                .json(&serde_json::json!({ "level_up_channel": level_up_channel, "preset_roles": preset_roles })),
        )
        .await
    }

    /// `None` channel / role: Percy creates `#verify` / `Unverified`.
    pub async fn setup_sentinel(
        &self,
        guild_id: u64,
        channel_id: Option<u64>,
        role_id: Option<u64>,
        start: bool,
    ) -> Result<ActionMessage, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/setup/sentinel", seg(&guild_id))))
                .json(&serde_json::json!({ "channel_id": channel_id, "role_id": role_id, "start": start })),
        )
        .await
    }

    // -- Role menus ------------------------------------------------------------

    pub async fn get_role_menus(&self, guild_id: u64) -> Result<RoleMenusResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/rolemenus", seg(&guild_id)))),
        )
        .await
    }

    /// Post a new menu. Body: `{channel_id, title, description?, unique?}`.
    pub async fn create_role_menu(&self, guild_id: u64, body: &serde_json::Value) -> Result<ActionMessage, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/rolemenus", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Change title / description / single-choice. Body: any of `{title, description, unique}`.
    pub async fn patch_role_menu(
        &self,
        guild_id: u64,
        menu_id: i64,
        body: &serde_json::Value,
    ) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!(
                    "/api/v1/guilds/{}/rolemenus/{}",
                    seg(&guild_id),
                    seg(&menu_id)
                )))
                .json(body),
        )
        .await
    }

    /// Offer a role on a menu. Body: `{role_id, emoji?, label?}`.
    pub async fn add_role_menu_role(
        &self,
        guild_id: u64,
        menu_id: i64,
        body: &serde_json::Value,
    ) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!(
                    "/api/v1/guilds/{}/rolemenus/{}/roles",
                    seg(&guild_id),
                    seg(&menu_id)
                )))
                .json(body),
        )
        .await
    }

    pub async fn remove_role_menu_role(&self, guild_id: u64, menu_id: i64, role_id: u64) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/rolemenus/{}/roles/{}",
            seg(&guild_id),
            seg(&menu_id),
            seg(&role_id)
        ))))
        .await
    }

    pub async fn delete_role_menu(&self, guild_id: u64, menu_id: i64) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/rolemenus/{}",
            seg(&guild_id),
            seg(&menu_id)
        ))))
        .await
    }

    // -- Starboard -------------------------------------------------------------

    pub async fn get_starboard(&self, guild_id: u64) -> Result<StarboardInfo, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/starboard", seg(&guild_id)))),
        )
        .await
    }

    pub async fn patch_starboard(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/starboard", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    // -- Tags (write) ------------------------------------------------------------

    /// Create a tag owned by `owner_id` (the dashboard user).
    pub async fn create_tag(
        &self,
        guild_id: u64,
        name: &str,
        content: &str,
        owner_id: &str,
    ) -> Result<ActionMessage, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/tags", seg(&guild_id))))
                .json(&serde_json::json!({
                    "name": name, "content": content, "owner_id": owner_id.parse::<u64>().unwrap_or_default(),
                })),
        )
        .await
    }

    /// Replace a tag's content.
    pub async fn edit_tag(&self, guild_id: u64, tag_id: i64, content: &str) -> Result<ActionMessage, PercyError> {
        self.send_into(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/tags/{}", seg(&guild_id), seg(&tag_id))))
                .json(&serde_json::json!({ "content": content })),
        )
        .await
    }

    // -- Reminders ---------------------------------------------------------------

    pub async fn get_user_reminders(&self, discord_id: &str) -> Result<RemindersResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/users/{}/reminders", seg(&discord_id)))),
        )
        .await
    }

    pub async fn delete_user_reminder(&self, discord_id: &str, reminder_id: i64) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/users/{}/reminders/{}",
            seg(&discord_id),
            seg(&reminder_id)
        ))))
        .await
    }

    /// Fetch leveling config for a guild.
    pub async fn get_leveling_config(&self, guild_id: u64) -> Result<LevelingConfig, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/leveling/config", seg(&guild_id)))),
        )
        .await
    }

    /// Post the level-up message once as a test, as it would look for `user_id`.
    pub async fn send_level_up_test(&self, guild_id: u64, user_id: &str) -> Result<ActionMessage, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/leveling/test-message", seg(&guild_id))))
                .json(&serde_json::json!({ "user_id": user_id.parse::<u64>().unwrap_or_default() })),
        )
        .await
    }

    /// Fetch the leveling leaderboard.
    pub async fn get_leveling_leaderboard(&self, guild_id: u64, limit: u32) -> Result<LeaderboardResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/leveling/leaderboard", seg(&guild_id))))
                .query(&[("limit", limit.to_string())]),
        )
        .await
    }

    /// Fetch the daily cumulative-XP history for the leveling chart.
    pub async fn get_leveling_xp_history(&self, guild_id: u64, days: u32) -> Result<XpHistoryResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/leveling/xp-history", seg(&guild_id))))
                .query(&[("days", days.to_string())]),
        )
        .await
    }

    /// Update a user's level/xp.
    pub async fn patch_leveling_user(
        &self,
        guild_id: u64,
        user_id: &str,
        patch: &serde_json::Value,
    ) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!(
                    "/api/v1/guilds/{}/leveling/users/{}",
                    seg(&guild_id),
                    seg(&user_id)
                )))
                .json(patch),
        )
        .await
    }

    /// Patch leveling configuration.
    pub async fn patch_leveling_config(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/leveling/config", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Manage leveling role rewards.
    pub async fn post_leveling_roles(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/leveling/roles", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Create the preset of milestone level-reward roles (levels 5-100).
    pub async fn create_leveling_role_preset(&self, guild_id: u64) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/leveling/roles/preset", seg(&guild_id)))),
        )
        .await
    }

    /// Manage leveling XP multipliers.
    pub async fn post_leveling_multipliers(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/leveling/multipliers", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Manage leveling blacklist (channels/roles excluded from XP).
    pub async fn post_leveling_blacklist(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/leveling/blacklist", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    // -- Polls ---------------------------------------------------------------

    /// Fetch polls for a guild.
    pub async fn get_polls(&self, guild_id: u64) -> Result<PollsResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/polls", seg(&guild_id)))),
        )
        .await
    }

    /// Create a new poll via a single multipart request: text fields plus an
    /// optional inline banner image. Percy's `POST /polls` endpoint accepts the
    /// image inline and routes it into the guild's shared gallery, so there is no
    /// separate upload step. `options` is sent as one field joined by the ASCII
    /// record separator (`\x1e`), which Percy splits on. Optional text fields are
    /// only sent when non-empty. `image` is `(bytes, mime_type)`. Surfaces Percy's
    /// validation error text via [`PercyError::Api`].
    pub async fn create_poll(
        &self,
        guild_id: u64,
        fields: &serde_json::Value,
        image: Option<(&[u8], &str)>,
    ) -> Result<serde_json::Value, PercyError> {
        fn str_field(v: &serde_json::Value, key: &str) -> String {
            v.get(key).and_then(|x| x.as_str()).unwrap_or("").trim().to_string()
        }

        let options = fields
            .get("options")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|o| o.as_str()).collect::<Vec<_>>().join("\x1e"))
            .unwrap_or_default();
        let duration = fields.get("duration_seconds").and_then(|v| v.as_i64()).unwrap_or(0);

        let mut form = reqwest::multipart::Form::new()
            .text("question", str_field(fields, "question"))
            .text("options", options)
            .text("duration_seconds", duration.to_string());

        // Optional text fields — only forwarded when present and non-empty.
        for key in ["channel_id", "description", "color", "image_url", "thread_question"] {
            let val = str_field(fields, key);
            if !val.is_empty() {
                form = form.text(key, val);
            }
        }

        if fields.get("hide_results").and_then(|v| v.as_bool()).unwrap_or(false) {
            form = form.text("hide_results", "true");
        }
        let role = str_field(fields, "role_id");
        if !role.is_empty() {
            form = form.text("role_id", role);
        }

        // Optional inline banner image.
        if let Some((data, mime)) = image {
            let ext = mime.rsplit('/').next().unwrap_or("png");
            let part = reqwest::multipart::Part::bytes(data.to_vec())
                .file_name(format!("banner.{ext}"))
                .mime_str(mime)
                .map_err(|e| PercyError::Api(e.to_string()))?;
            form = form.part("file", part);
        }

        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/polls", seg(&guild_id))))
                .multipart(form),
        )
        .await
    }

    /// Edit a poll.
    pub async fn patch_poll(&self, guild_id: u64, poll_id: i64, patch: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/polls/{}", seg(&guild_id), seg(&poll_id))))
                .json(patch),
        )
        .await
    }

    /// End a running poll.
    pub async fn end_poll(&self, guild_id: u64, poll_id: i64) -> Result<(), PercyError> {
        self.send_unit(self.client.post(self.url(&format!(
            "/api/v1/guilds/{}/polls/{}/end",
            seg(&guild_id),
            seg(&poll_id)
        ))))
        .await
    }

    // -- Giveaways / Tags ----------------------------------------------------

    /// Fetch giveaways for a guild.
    pub async fn get_giveaways(&self, guild_id: u64) -> Result<GiveawaysResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/giveaways", seg(&guild_id)))),
        )
        .await
    }

    /// Create a giveaway (posts the announcement + schedules the draw). Surfaces
    /// Percy's validation error text via [`PercyError::Api`].
    pub async fn create_giveaway(
        &self,
        guild_id: u64,
        body: &serde_json::Value,
    ) -> Result<serde_json::Value, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/giveaways", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// End a giveaway now (draws winners).
    pub async fn end_giveaway(&self, guild_id: u64, giveaway_id: i64) -> Result<(), PercyError> {
        self.send_unit(self.client.post(self.url(&format!(
            "/api/v1/guilds/{}/giveaways/{}/end",
            seg(&guild_id),
            seg(&giveaway_id)
        ))))
        .await
    }

    /// Cancel a giveaway without drawing winners.
    pub async fn delete_giveaway(&self, guild_id: u64, giveaway_id: i64) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/giveaways/{}",
            seg(&guild_id),
            seg(&giveaway_id)
        ))))
        .await
    }

    /// Fetch tags for a guild.
    pub async fn get_tags(&self, guild_id: u64) -> Result<TagsResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/tags", seg(&guild_id)))),
        )
        .await
    }

    /// Fetch a single tag's full content (for the markdown preview).
    pub async fn get_tag_detail(&self, guild_id: u64, tag_id: i64) -> Result<TagDetail, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/tags/{}", seg(&guild_id), seg(&tag_id)))),
        )
        .await
    }

    /// Delete a tag (and its aliases).
    pub async fn delete_tag(&self, guild_id: u64, tag_id: i64) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/tags/{}",
            seg(&guild_id),
            seg(&tag_id)
        ))))
        .await
    }

    /// Fetch every tag's `(name, content)` for export.
    pub async fn export_tags(&self, guild_id: u64) -> Result<TagExport, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/tags/export", seg(&guild_id)))),
        )
        .await
    }

    /// Bulk-create tags from a parsed `(name, content)` list, owned by `owner_id`.
    pub async fn import_tags(
        &self,
        guild_id: u64,
        tags: &[TagExportRow],
        owner_id: &str,
    ) -> Result<TagImportResult, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/tags/import", seg(&guild_id))))
                .json(&serde_json::json!({"tags": tags, "owner_id": owner_id})),
        )
        .await
    }

    // -- Commands ------------------------------------------------------------

    /// Fetch commands and plonk list for a guild.
    pub async fn get_commands(&self, guild_id: u64) -> Result<CommandsResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/commands", seg(&guild_id)))),
        )
        .await
    }

    /// Toggle a command (enable/disable).
    pub async fn toggle_command(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/commands/toggle", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Manage plonks (add/remove ignored entities).
    pub async fn manage_plonk(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/plonks", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Create, replace, or clear a command's per-guild permission override.
    /// A body with `permissions: null` and no `allowed_roles` removes the override.
    pub async fn set_command_permissions(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/commands/permissions", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    // -- Stats ---------------------------------------------------------------

    /// Fetch guild stats.
    pub async fn get_guild_stats(&self, guild_id: u64) -> Result<GuildStats, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/stats", seg(&guild_id)))),
        )
        .await
    }

    /// Fetch bot-wide stats.
    pub async fn get_bot_stats(&self) -> Result<BotStats, PercyError> {
        self.send_into(self.client.get(self.url("/api/v1/bot/stats"))).await
    }

    /// Fetch bot changelog (git log grouped by version tags).
    pub async fn get_changelog(&self) -> Result<ChangelogResponse, PercyError> {
        self.send_into(self.client.get(self.url("/api/v1/bot/changelog"))).await
    }

    /// Composite: fetch guild overview (config summary + stats + bot stats + feature flags)
    /// in a single round-trip.
    pub async fn get_guild_overview(&self, guild_id: u64) -> Result<GuildOverview, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/overview", seg(&guild_id)))),
        )
        .await
    }

    /// Fetch all bot commands (public, no guild context).
    pub async fn get_bot_commands(&self) -> Result<Vec<CommandInfo>, PercyError> {
        let resp: PublicCommandsResponse = self
            .send_into(self.client.get(self.url("/api/v1/commands/public")))
            .await?;
        Ok(resp.commands)
    }

    // -- Public-API gateway support ------------------------------------------
    //
    // These keep Percy's HTTP status (`PercyError::Rejected`) instead of folding it into `Api`, so
    // the public gateway can map a 400/404/409 onto its own error codes.

    async fn send_api<T: DeserializeOwned>(&self, req: RequestBuilder) -> Result<T, PercyError> {
        let resp = req.bearer_auth(&self.token).send().await?;
        let status = resp.status();
        if status.is_client_error() || status.is_server_error() {
            let body = resp.text().await.unwrap_or_default();
            let detail = serde_json::from_str::<serde_json::Value>(&body)
                .ok()
                .and_then(|v| v.get("detail").and_then(|d| d.as_str().map(str::to_string)))
                .unwrap_or(body);
            return Err(PercyError::Rejected {
                status: status.as_u16(),
                detail,
            });
        }
        Ok(resp.json().await?)
    }

    /// One member's wallet (zero wallet when none exists).
    pub async fn get_member_balance(&self, guild_id: u64, user_id: u64) -> Result<MemberBalance, PercyError> {
        self.send_api(self.client.get(self.url(&format!(
            "/api/v1/guilds/{}/economy/balances/{}",
            seg(&guild_id),
            seg(&user_id)
        ))))
        .await
    }

    /// Atomically add signed deltas to cash/bank; Percy refuses (400) a result below zero.
    pub async fn adjust_member_balance(
        &self,
        guild_id: u64,
        user_id: u64,
        cash: i64,
        bank: i64,
    ) -> Result<MemberBalance, PercyError> {
        self.send_api(
            self.client
                .post(self.url(&format!(
                    "/api/v1/guilds/{}/economy/balances/{}/adjust",
                    seg(&guild_id),
                    seg(&user_id)
                )))
                .json(&serde_json::json!({"cash": cash, "bank": bank})),
        )
        .await
    }

    /// Set a member's balance (unset fields are left alone).
    pub async fn set_member_balance(
        &self,
        guild_id: u64,
        user_id: u64,
        cash: Option<i64>,
        bank: Option<i64>,
    ) -> Result<serde_json::Value, PercyError> {
        self.send_api(
            self.client
                .patch(self.url(&format!(
                    "/api/v1/guilds/{}/economy/balances/{}",
                    seg(&guild_id),
                    seg(&user_id)
                )))
                .json(&serde_json::json!({"cash": cash, "bank": bank})),
        )
        .await
    }

    /// Paged balance leaderboard.
    pub async fn get_economy_balances_page(
        &self,
        guild_id: u64,
        limit: u32,
        offset: u32,
    ) -> Result<BalancesResponse, PercyError> {
        self.send_api(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/economy/balances", seg(&guild_id))))
                .query(&[("limit", limit), ("offset", offset)]),
        )
        .await
    }

    /// Shop + settings, with Percy's status preserved.
    pub async fn get_economy_api(&self, guild_id: u64) -> Result<EconomyInfo, PercyError> {
        self.send_api(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/economy", seg(&guild_id)))),
        )
        .await
    }

    /// Create a shop item; answers `{ok, id}`.
    pub async fn create_economy_item_api(
        &self,
        guild_id: u64,
        body: &serde_json::Value,
    ) -> Result<serde_json::Value, PercyError> {
        self.send_api(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/economy/items", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Partial item update by id.
    pub async fn patch_economy_item(
        &self,
        guild_id: u64,
        item_id: u64,
        body: &serde_json::Value,
    ) -> Result<serde_json::Value, PercyError> {
        self.send_api(
            self.client
                .patch(self.url(&format!(
                    "/api/v1/guilds/{}/economy/items/{}",
                    seg(&guild_id),
                    seg(&item_id)
                )))
                .json(body),
        )
        .await
    }

    /// Delete a shop item by id (inventories cascade).
    pub async fn delete_economy_item_by_id(
        &self,
        guild_id: u64,
        item_id: u64,
    ) -> Result<serde_json::Value, PercyError> {
        self.send_api(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/economy/items/by-id/{}",
            seg(&guild_id),
            seg(&item_id)
        ))))
        .await
    }

    pub async fn get_member_inventory(&self, guild_id: u64, user_id: u64) -> Result<MemberInventory, PercyError> {
        self.send_api(self.client.get(self.url(&format!(
            "/api/v1/guilds/{}/economy/inventory/{}",
            seg(&guild_id),
            seg(&user_id)
        ))))
        .await
    }

    /// Grant items for free (respects the item's `max_per_user`).
    pub async fn give_member_item(
        &self,
        guild_id: u64,
        user_id: u64,
        item_id: u64,
        quantity: u32,
    ) -> Result<InventoryChange, PercyError> {
        self.send_api(
            self.client
                .post(self.url(&format!(
                    "/api/v1/guilds/{}/economy/inventory/{}",
                    seg(&guild_id),
                    seg(&user_id)
                )))
                .json(&serde_json::json!({"item_id": item_id, "quantity": quantity})),
        )
        .await
    }

    /// Take items away; Percy refuses (409) when the member owns fewer.
    pub async fn take_member_item(
        &self,
        guild_id: u64,
        user_id: u64,
        item_id: u64,
        quantity: u32,
    ) -> Result<InventoryChange, PercyError> {
        self.send_api(
            self.client
                .delete(self.url(&format!(
                    "/api/v1/guilds/{}/economy/inventory/{}/{}",
                    seg(&guild_id),
                    seg(&user_id),
                    seg(&item_id)
                )))
                .query(&[("quantity", quantity)]),
        )
        .await
    }

    pub async fn get_member_level(&self, guild_id: u64, user_id: u64) -> Result<MemberLevel, PercyError> {
        self.send_api(self.client.get(self.url(&format!(
            "/api/v1/guilds/{}/leveling/users/{}",
            seg(&guild_id),
            seg(&user_id)
        ))))
        .await
    }

    /// Leveling leaderboard page, status preserved.
    pub async fn get_leveling_leaderboard_page(
        &self,
        guild_id: u64,
        limit: u32,
        offset: u32,
    ) -> Result<LeaderboardResponse, PercyError> {
        self.send_api(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/leveling/leaderboard", seg(&guild_id))))
                .query(&[("limit", limit), ("offset", offset)]),
        )
        .await
    }

    /// Guild metadata, status preserved (404 = Percy is not in that guild).
    pub async fn get_guild_api(&self, guild_id: u64) -> Result<GuildInfo, PercyError> {
        self.send_api(self.client.get(self.url(&format!("/api/v1/guilds/{}", seg(&guild_id)))))
            .await
    }

    /// Public bot stats.
    pub async fn get_bot_stats_api(&self) -> Result<BotStats, PercyError> {
        self.send_api(self.client.get(self.url("/api/v1/bot/stats"))).await
    }

    // -- Economy -------------------------------------------------------------

    /// Fetch economy info (shop items + lottery) for a guild.
    pub async fn get_economy(&self, guild_id: u64) -> Result<EconomyInfo, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/economy", seg(&guild_id)))),
        )
        .await
    }

    /// Create a shop item.
    pub async fn create_economy_item(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/economy/items", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Delete a shop item by name.
    pub async fn delete_economy_item(&self, guild_id: u64, name: &str) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/economy/items/{}",
            seg(&guild_id),
            seg(&name)
        ))))
        .await
    }

    /// Fetch economy balances (leaderboard).
    pub async fn get_economy_balances(&self, guild_id: u64, limit: u32) -> Result<BalancesResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/economy/balances", seg(&guild_id))))
                .query(&[("limit", limit.to_string())]),
        )
        .await
    }

    /// Update the guild's economy settings (payout multiplier, rob toggle, daily base, max bet).
    pub async fn patch_economy_settings(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/economy/settings", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Guild-wide game statistics (per-game totals + top players).
    pub async fn get_games_stats(&self, guild_id: u64) -> Result<GamesStats, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/games", seg(&guild_id)))),
        )
        .await
    }

    /// Patch a user's economy balance.
    pub async fn patch_economy_balance(
        &self,
        guild_id: u64,
        user_id: &str,
        body: &serde_json::Value,
    ) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!(
                    "/api/v1/guilds/{}/economy/balances/{}",
                    seg(&guild_id),
                    seg(&user_id)
                )))
                .json(body),
        )
        .await
    }

    /// Create a lottery.
    pub async fn create_lottery(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/economy/lottery", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Delete (cancel) the active lottery.
    pub async fn delete_lottery(&self, guild_id: u64) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .delete(self.url(&format!("/api/v1/guilds/{}/economy/lottery", seg(&guild_id)))),
        )
        .await
    }

    // -- Music ---------------------------------------------------------------

    /// Fetch music/equalizer state for a guild.
    pub async fn get_music(&self, guild_id: u64) -> Result<MusicInfo, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/music", seg(&guild_id)))),
        )
        .await
    }

    /// Fetch time-synced lyrics for the guild's currently-playing track.
    pub async fn get_music_lyrics(&self, guild_id: u64) -> Result<MusicLyrics, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/music/lyrics", seg(&guild_id)))),
        )
        .await
    }

    /// Apply equalizer settings (preset or custom bands).
    pub async fn post_music_equalizer(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/music/equalizer", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Apply a filter action (nightcore, 8d, lowpass, reset).
    pub async fn post_music_filters(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/music/filters", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Set up the music panel (create channel + send panel message).
    pub async fn post_music_setup(
        &self,
        guild_id: u64,
        body: &serde_json::Value,
    ) -> Result<MusicSetupResponse, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/music/setup", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Reset the music configuration (delete channel, clear config).
    pub async fn post_music_reset(&self, guild_id: u64) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/music/reset", seg(&guild_id))))
                .json(&serde_json::json!({})),
        )
        .await
    }

    /// Enable or disable the 24/7 always-on player for a guild.
    pub async fn post_music_247(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/music/247", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Update the DJ mode setting for a guild's music player.
    pub async fn patch_music_dj_mode(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/music/dj-mode", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Control the live player (pause/resume/skip/stop) on behalf of a dashboard
    /// viewer. Percy enforces voice-presence and DJ-mode rules and surfaces a 403
    /// (mapped to [`PercyError::Api`]) when the viewer isn't permitted.
    pub async fn music_control(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/music/control", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Equalizer and every audio filter of the live player (defaults with `active: false` when idle).
    pub async fn get_music_audio(&self, guild_id: u64) -> Result<MusicAudio, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/music/audio", seg(&guild_id)))),
        )
        .await
    }

    /// Apply an effect / fine values / reset on behalf of a viewer (`user_id` in the body); 403 when not permitted.
    pub async fn set_music_audio(&self, guild_id: u64, body: &serde_json::Value) -> Result<MusicAudio, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/music/audio", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// What `user_id` may do with the guild's player (role, DJ rules, voice channels).
    pub async fn get_music_viewer(&self, guild_id: u64, user_id: u64) -> Result<MusicViewer, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/music/viewer", seg(&guild_id))))
                .query(&[("user_id", user_id)]),
        )
        .await
    }

    /// Search tracks for the music app's search box.
    pub async fn search_music(&self, guild_id: u64, q: &str, limit: u8) -> Result<MusicSearch, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/music/search", seg(&guild_id))))
                .query(&[("q", q.to_string()), ("limit", limit.to_string())]),
        )
        .await
    }

    /// An artist's top tracks and the releases they belong to (`url` is the source's artist link, when known).
    pub async fn get_music_artist(
        &self,
        guild_id: u64,
        name: &str,
        url: Option<&str>,
    ) -> Result<MusicArtist, PercyError> {
        let mut q = vec![("name", name.to_string())];
        if let Some(u) = url {
            q.push(("url", u.to_string()));
        }
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/music/artist", seg(&guild_id))))
                .query(&q),
        )
        .await
    }

    /// An album/single/playlist link as a header plus its tracks.
    pub async fn get_music_album(&self, guild_id: u64, url: &str) -> Result<MusicAlbum, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/music/album", seg(&guild_id))))
                .query(&[("url", url)]),
        )
        .await
    }

    /// Queue `query` for `user_id` (`limit` > 1 queues a mix of the top results); Percy starts a player in their voice channel when none runs.
    pub async fn play_music(
        &self,
        guild_id: u64,
        user_id: u64,
        query: &str,
        limit: Option<u8>,
        play_now: bool,
    ) -> Result<MusicPlayResult, PercyError> {
        let mut body = serde_json::json!({ "user_id": user_id, "query": query });
        if let Some(n) = limit {
            body["limit"] = n.into();
        }
        if play_now {
            body["play_now"] = true.into();
        }
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/music/play", seg(&guild_id))))
                .json(&body),
        )
        .await
    }

    /// Recently played tracks in a guild; `before` is the last `played_at` seen.
    pub async fn get_music_history(
        &self,
        guild_id: u64,
        limit: u32,
        before: Option<&str>,
    ) -> Result<MusicHistory, PercyError> {
        let mut query = vec![("limit", limit.to_string())];
        if let Some(b) = before {
            query.push(("before", b.to_string()));
        }
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/music/history", seg(&guild_id))))
                .query(&query),
        )
        .await
    }

    /// The week's most-picked tracks in a guild and across every guild.
    pub async fn get_music_trending(&self, guild_id: u64) -> Result<MusicTrending, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/music/trending", seg(&guild_id)))),
        )
        .await
    }

    /// A user's music library: top tracks, recent plays, liked songs, playlists.
    pub async fn get_user_music(&self, user_id: u64) -> Result<UserMusicLibrary, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/users/{}/music", seg(&user_id)))),
        )
        .await
    }

    /// Add a track to the user's liked songs (idempotent).
    pub async fn like_track(&self, user_id: u64, track: &SaveTrack) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/users/{}/music/liked", seg(&user_id))))
                .json(track),
        )
        .await
    }

    /// Remove a track from the user's liked songs.
    pub async fn unlike_track(&self, user_id: u64, url: &str) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .delete(self.url(&format!("/api/v1/users/{}/music/liked", seg(&user_id))))
                .query(&[("url", url)]),
        )
        .await
    }

    /// One of the user's playlists with its tracks (404 if it isn't theirs).
    pub async fn get_user_playlist(&self, user_id: u64, playlist_id: &str) -> Result<PlaylistDetail, PercyError> {
        self.send_into(self.client.get(self.url(&format!(
            "/api/v1/users/{}/music/playlists/{}",
            seg(&user_id),
            seg(&playlist_id)
        ))))
        .await
    }

    /// Create an empty playlist (409 if the user already has one with that name).
    pub async fn create_user_playlist(&self, user_id: u64, name: &str) -> Result<PlaylistSummary, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/users/{}/music/playlists", seg(&user_id))))
                .json(&serde_json::json!({ "name": name })),
        )
        .await
    }

    /// Rename a playlist and/or set its cover; returns the playlist as it now is.
    pub async fn update_user_playlist(
        &self,
        user_id: u64,
        playlist_id: &str,
        patch: &PlaylistPatch,
    ) -> Result<PlaylistDetail, PercyError> {
        self.send_into(
            self.client
                .patch(self.url(&format!(
                    "/api/v1/users/{}/music/playlists/{}",
                    seg(&user_id),
                    seg(&playlist_id)
                )))
                .json(patch),
        )
        .await
    }

    /// Delete a playlist and its tracks (Liked Songs cannot be deleted).
    pub async fn delete_user_playlist(&self, user_id: u64, playlist_id: &str) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/users/{}/music/playlists/{}",
            seg(&user_id),
            seg(&playlist_id)
        ))))
        .await
    }

    /// Add a track to a playlist; a track already in it is left alone.
    pub async fn add_user_playlist_track(
        &self,
        user_id: u64,
        playlist_id: &str,
        track: &SaveTrack,
    ) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!(
                    "/api/v1/users/{}/music/playlists/{}/tracks",
                    seg(&user_id),
                    seg(&playlist_id)
                )))
                .json(track),
        )
        .await
    }

    /// Remove every copy of `url` from a playlist.
    pub async fn remove_user_playlist_track(
        &self,
        user_id: u64,
        playlist_id: &str,
        url: &str,
    ) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .delete(self.url(&format!(
                    "/api/v1/users/{}/music/playlists/{}/tracks",
                    seg(&user_id),
                    seg(&playlist_id)
                )))
                .query(&[("url", url)]),
        )
        .await
    }

    // -- Comics --------------------------------------------------------------

    /// Fetch comic feeds for a guild.
    pub async fn get_comics(&self, guild_id: u64) -> Result<ComicsResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/comics", seg(&guild_id)))),
        )
        .await
    }

    /// Create a comic feed.
    pub async fn create_comic(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/comics", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Patch a comic feed by brand.
    pub async fn patch_comic(&self, guild_id: u64, brand: &str, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/comics/{}", seg(&guild_id), seg(&brand))))
                .json(body),
        )
        .await
    }

    /// Delete a comic feed by brand.
    pub async fn delete_comic(&self, guild_id: u64, brand: &str) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/comics/{}",
            seg(&guild_id),
            seg(&brand)
        ))))
        .await
    }

    /// Trigger a manual push for a comic feed.
    pub async fn push_comic(&self, guild_id: u64, brand: &str) -> Result<(), PercyError> {
        self.send_unit(self.client.post(self.url(&format!(
            "/api/v1/guilds/{}/comics/{}/push",
            seg(&guild_id),
            seg(&brand)
        ))))
        .await
    }

    // -- Comic catalogue (public) ---------------------------------------------
    //
    // Not guild-scoped: this is the global comic-release catalogue behind
    // percy-dashboard's public comic pages, distinct from the guild-scoped
    // comic *feed* subscriptions above.

    #[allow(clippy::too_many_arguments)]
    fn list_comics_request(&self, query: &ComicBrowseQuery<'_>) -> RequestBuilder {
        let mut req = self.client.get(self.url("/api/v1/comics"));
        for (key, value) in [
            ("brand", query.brand),
            ("series", query.series),
            ("creator", query.creator),
            ("character", query.character),
            ("since", query.since),
            ("until", query.until),
            ("search", query.search),
        ] {
            if let Some(value) = value {
                req = req.query(&[(key, value)]);
            }
        }
        if let Some(page) = query.page {
            req = req.query(&[("page", page.to_string())]);
        }
        if let Some(per_page) = query.per_page {
            req = req.query(&[("per_page", per_page.to_string())]);
        }
        req
    }

    /// List comic releases, filtered and paginated.
    ///
    /// Every filter is optional. `page` is 1-based; Percy caps `per_page` at 100.
    ///
    /// Takes a [`ComicBrowseQuery`] rather than a run of positional arguments: seven of the
    /// filters are `Option<&str>`, so a positional signature lets a caller swap `creator` and
    /// `character` and still compile, silently querying the wrong thing.
    pub async fn list_comics(&self, query: &ComicBrowseQuery<'_>) -> Result<ComicBrowsePage, PercyError> {
        self.send_into(self.list_comics_request(query)).await
    }

    /// Fetch one comic release by brand and slug, with its series neighbours.
    pub async fn get_comic(&self, brand: &str, slug: &str) -> Result<ComicDetail, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/comics/{}/{}", seg(&brand), seg(&slug)))),
        )
        .await
    }

    fn list_comic_series_request(&self, brand: Option<&str>) -> RequestBuilder {
        let query: Vec<(&str, String)> = brand.map(|b| vec![("brand", b.to_string())]).unwrap_or_default();
        self.client.get(self.url("/api/v1/comics/series")).query(&query)
    }

    /// List every comic series, optionally scoped to one brand.
    pub async fn list_comic_series(&self, brand: Option<&str>) -> Result<ComicSeriesList, PercyError> {
        self.send_into(self.list_comic_series_request(brand)).await
    }

    fn get_comic_series_request(&self, slug: &str, brand: Option<&str>) -> RequestBuilder {
        let query: Vec<(&str, String)> = brand.map(|b| vec![("brand", b.to_string())]).unwrap_or_default();
        self.client
            .get(self.url(&format!("/api/v1/comics/series/{}", seg(&slug))))
            .query(&query)
    }

    /// Fetch one series and every issue in it.
    ///
    /// A series slug is derived from a title, so two publishers can collide on one --
    /// pass `brand` whenever the caller knows it, to avoid an ambiguous match.
    pub async fn get_comic_series(&self, slug: &str, brand: Option<&str>) -> Result<ComicSeriesPage, PercyError> {
        self.send_into(self.get_comic_series_request(slug, brand)).await
    }

    fn list_comic_names_request(&self, kind: &str, brand: Option<&str>, limit: Option<u32>) -> RequestBuilder {
        let mut req = self
            .client
            .get(self.url("/api/v1/comics/names"))
            .query(&[("kind", kind)]);
        if let Some(b) = brand {
            req = req.query(&[("brand", b)]);
        }
        if let Some(l) = limit {
            req = req.query(&[("limit", l.to_string())]);
        }
        req
    }

    /// List distinct creator or character names across the catalogue, with appearance counts.
    ///
    /// `kind` is `"creators"` or `"characters"`; anything else is a `400` from Percy.
    pub async fn list_comic_names(
        &self,
        kind: &str,
        brand: Option<&str>,
        limit: Option<u32>,
    ) -> Result<ComicNamesResponse, PercyError> {
        self.send_into(self.list_comic_names_request(kind, brand, limit)).await
    }

    // -- Temp Channels -------------------------------------------------------

    /// Fetch temp channel entries for a guild.
    pub async fn get_temp_channels(&self, guild_id: u64) -> Result<TempChannelsResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/temp-channels", seg(&guild_id)))),
        )
        .await
    }

    /// Create a temp channel config.
    pub async fn create_temp_channel(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/temp-channels", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Patch a temp channel config.
    pub async fn patch_temp_channel(
        &self,
        guild_id: u64,
        channel_id: u64,
        body: &serde_json::Value,
    ) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!(
                    "/api/v1/guilds/{}/temp-channels/{}",
                    seg(&guild_id),
                    seg(&channel_id)
                )))
                .json(body),
        )
        .await
    }

    /// Delete a temp channel config.
    pub async fn delete_temp_channel(&self, guild_id: u64, channel_id: u64) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/temp-channels/{}",
            seg(&guild_id),
            seg(&channel_id)
        ))))
        .await
    }

    // -- Status Feed ---------------------------------------------------------

    /// Fetch status feed info for a guild.
    pub async fn get_status_feed(&self, guild_id: u64) -> Result<StatusFeedInfo, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/status-feed", seg(&guild_id)))),
        )
        .await
    }

    /// Subscribe to or update status feed.
    pub async fn post_status_feed(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/status-feed", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Unsubscribe from status feed.
    pub async fn delete_status_feed(&self, guild_id: u64) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .delete(self.url(&format!("/api/v1/guilds/{}/status-feed", seg(&guild_id)))),
        )
        .await
    }

    // -- Lockdowns -----------------------------------------------------------

    /// Fetch locked-down channels for a guild.
    pub async fn get_lockdowns(&self, guild_id: u64) -> Result<LockdownsResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/lockdowns", seg(&guild_id)))),
        )
        .await
    }

    /// Lock channels (apply lockdown overwrites).
    pub async fn lock_channels(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/lockdowns/lock", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Unlock channels.
    pub async fn unlock_channels(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/lockdowns/unlock", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Add or remove a moderation ignored entity (safe automod entity).
    pub async fn manage_moderation_ignore(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/moderation/ignore", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    // -- Emoji Stats ---------------------------------------------------------

    /// Fetch emoji usage stats for a guild.
    pub async fn get_emoji_stats(&self, guild_id: u64, limit: u32) -> Result<EmojiStatsResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/emoji-stats", seg(&guild_id))))
                .query(&[("limit", limit.to_string())]),
        )
        .await
    }

    // -- Audit log (moderation cases) ----------------------------------------

    /// Fetch paginated, filterable moderation cases.
    #[allow(clippy::too_many_arguments)]
    pub async fn get_cases(
        &self,
        guild_id: u64,
        limit: u32,
        offset: u32,
        action: Option<&str>,
        moderator_id: Option<&str>,
        target_id: Option<&str>,
        after: Option<&str>,
        before: Option<&str>,
    ) -> Result<CasesResponse, PercyError> {
        let mut req = self
            .client
            .get(self.url(&format!("/api/v1/guilds/{}/cases", seg(&guild_id))))
            .query(&[("limit", limit.to_string()), ("offset", offset.to_string())]);
        if let Some(a) = action {
            req = req.query(&[("action", a)]);
        }
        if let Some(m) = moderator_id {
            req = req.query(&[("moderator_id", m)]);
        }
        if let Some(t) = target_id {
            req = req.query(&[("target_id", t)]);
        }
        if let Some(a) = after {
            req = req.query(&[("after", a)]);
        }
        if let Some(b) = before {
            req = req.query(&[("before", b)]);
        }
        self.send_into(req).await
    }

    /// Fetch cases created since a given timestamp (for live notifications).
    pub async fn get_recent_cases(&self, guild_id: u64, since: &str) -> Result<RecentCasesResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/cases/recent", seg(&guild_id))))
                .query(&[("since", since)]),
        )
        .await
    }

    /// Manually open a moderation case (records + announces it; does not perform the action).
    pub async fn create_case(
        &self,
        guild_id: u64,
        action: &str,
        target_id: &str,
        moderator_id: Option<&str>,
        reason: Option<&str>,
    ) -> Result<CaseActionResponse, PercyError> {
        let mut body = serde_json::json!({"action": action, "target_id": target_id});
        if let Some(m) = moderator_id {
            body["moderator_id"] = serde_json::Value::String(m.to_string());
        }
        if let Some(r) = reason {
            body["reason"] = serde_json::Value::String(r.to_string());
        }
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/cases", seg(&guild_id))))
                .json(&body),
        )
        .await
    }

    /// Update a case's reason (also syncs the modlog channel post).
    pub async fn update_case_reason(
        &self,
        guild_id: u64,
        case_index: u64,
        reason: &str,
        moderator_id: &str,
    ) -> Result<CaseActionResponse, PercyError> {
        self.send_into(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/cases/{}", seg(&guild_id), seg(&case_index))))
                .json(&serde_json::json!({"reason": reason, "moderator_id": moderator_id})),
        )
        .await
    }

    /// Close (delete) a case, removing its modlog channel post. Percy enforces Manage Server on `moderator_id`.
    pub async fn delete_case(&self, guild_id: u64, case_index: u64, moderator_id: &str) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .delete(self.url(&format!("/api/v1/guilds/{}/cases/{}", seg(&guild_id), seg(&case_index))))
                .query(&[("moderator_id", moderator_id)]),
        )
        .await
    }

    /// Pardon a warn case (Percy deactivates it for the warn-threshold count).
    pub async fn pardon_case(
        &self,
        guild_id: u64,
        case_index: u64,
        moderator_id: &str,
    ) -> Result<serde_json::Value, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!(
                    "/api/v1/guilds/{}/cases/{}/pardon",
                    seg(&guild_id),
                    seg(&case_index)
                )))
                .json(&serde_json::json!({"moderator_id": moderator_id})),
        )
        .await
    }

    /// Fetch the guild's warn policy (expiry + escalation thresholds).
    pub async fn get_warn_policy(&self, guild_id: u64) -> Result<WarnPolicy, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/warn-policy", seg(&guild_id)))),
        )
        .await
    }

    /// Set (or clear with `None`) warn expiry in days.
    pub async fn set_warn_expiry(&self, guild_id: u64, expiry_days: Option<u32>) -> Result<WarnPolicy, PercyError> {
        self.send_into(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/warn-policy", seg(&guild_id))))
                .json(&serde_json::json!({"expiry_days": expiry_days})),
        )
        .await
    }

    /// Add or replace the escalation rule for `warn_count`.
    pub async fn put_warn_threshold(
        &self,
        guild_id: u64,
        warn_count: u32,
        action: &str,
        duration_seconds: Option<u64>,
    ) -> Result<WarnPolicy, PercyError> {
        self.send_into(
            self.client
                .put(self.url(&format!(
                    "/api/v1/guilds/{}/warn-policy/thresholds/{}",
                    seg(&guild_id),
                    seg(&warn_count)
                )))
                .json(&serde_json::json!({"action": action, "duration_seconds": duration_seconds})),
        )
        .await
    }

    /// Remove the escalation rule for `warn_count`.
    pub async fn delete_warn_threshold(&self, guild_id: u64, warn_count: u32) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/warn-policy/thresholds/{}",
            seg(&guild_id),
            seg(&warn_count)
        ))))
        .await
    }

    /// Perform a bulk moderation action on multiple members.
    pub async fn bulk_member_action(
        &self,
        guild_id: u64,
        body: &serde_json::Value,
    ) -> Result<BulkActionResponse, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/members/bulk-action", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Fetch per-day activity data for a member (heatmap).
    pub async fn get_member_activity(
        &self,
        guild_id: u64,
        user_id: &str,
        days: u32,
    ) -> Result<ActivityResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!(
                    "/api/v1/guilds/{}/members/{}/activity",
                    seg(&guild_id),
                    seg(&user_id)
                )))
                .query(&[("days", days.to_string())]),
        )
        .await
    }

    // -- Custom Bot Profile ---------------------------------------------------

    /// Fetch the bot's per-guild profile customization.
    pub async fn get_custom_bot(&self, guild_id: u64) -> Result<CustomBotProfile, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/custom-bot", seg(&guild_id)))),
        )
        .await
    }

    /// Update the bot's per-guild profile customization.
    pub async fn patch_custom_bot(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/custom-bot", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Reset the bot's per-guild profile to defaults.
    pub async fn reset_custom_bot(&self, guild_id: u64) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/custom-bot/reset", seg(&guild_id))))
                .json(&serde_json::json!({})),
        )
        .await
    }

    // -- Guild image gallery -------------------------------------------------

    /// List a guild's shared image gallery (newest first; expired images omitted).
    pub async fn get_guild_gallery(&self, guild_id: u64) -> Result<GalleryResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/gallery", seg(&guild_id)))),
        )
        .await
    }

    /// Upload one or more images into a guild's shared gallery. Each entry is
    /// `(filename, bytes, mime_type)`. Percy forwards them to the klappstuhl.me
    /// guild endpoint under the `files` multipart field.
    pub async fn upload_guild_gallery(
        &self,
        guild_id: u64,
        files: Vec<(String, Vec<u8>, String)>,
    ) -> Result<GalleryUploadResult, PercyError> {
        let mut form = reqwest::multipart::Form::new();
        for (name, bytes, mime) in files {
            let part = reqwest::multipart::Part::bytes(bytes)
                .file_name(name)
                .mime_str(&mime)
                .map_err(|e| PercyError::Api(e.to_string()))?;
            form = form.part("files", part);
        }

        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/gallery", seg(&guild_id))))
                .multipart(form),
        )
        .await
    }

    /// Delete an image from a guild's shared gallery.
    pub async fn delete_guild_gallery(&self, guild_id: u64, image_id: &str) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/gallery/{}",
            seg(&guild_id),
            seg(&image_id)
        ))))
        .await
    }

    // -- Outbound webhooks (event subscriptions) -----------------------------

    /// The catalogue of events a subscription can listen for.
    pub async fn get_webhook_events(&self, guild_id: u64) -> Result<WebhookEventsResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/webhooks/events", seg(&guild_id)))),
        )
        .await
    }

    /// List every outbound webhook configured for the guild (secrets masked).
    pub async fn get_webhooks(&self, guild_id: u64) -> Result<WebhookSubscriptionsResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/webhooks", seg(&guild_id)))),
        )
        .await
    }

    /// Register a new outbound webhook. The response carries the signing secret
    /// exactly once (masked on every later read) — surface it to the user now.
    pub async fn create_webhook(
        &self,
        guild_id: u64,
        body: &serde_json::Value,
    ) -> Result<WebhookCreateResponse, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/webhooks", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Update a subscription's url, events, label, or enabled state.
    pub async fn patch_webhook(&self, guild_id: u64, sub_id: i64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .patch(self.url(&format!("/api/v1/guilds/{}/webhooks/{}", seg(&guild_id), seg(&sub_id))))
                .json(body),
        )
        .await
    }

    /// Delete a subscription and its delivery log.
    pub async fn delete_webhook(&self, guild_id: u64, sub_id: i64) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/webhooks/{}",
            seg(&guild_id),
            seg(&sub_id)
        ))))
        .await
    }

    /// Send a sample `ping` payload to a subscription and report the result synchronously.
    pub async fn test_webhook(&self, guild_id: u64, sub_id: i64) -> Result<WebhookTestResponse, PercyError> {
        self.send_into(self.client.post(self.url(&format!(
            "/api/v1/guilds/{}/webhooks/{}/test",
            seg(&guild_id),
            seg(&sub_id)
        ))))
        .await
    }

    /// Recent delivery attempts for a subscription (its health log).
    pub async fn get_webhook_deliveries(
        &self,
        guild_id: u64,
        sub_id: i64,
        limit: u32,
    ) -> Result<WebhookDeliveriesResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!(
                    "/api/v1/guilds/{}/webhooks/{}/deliveries",
                    seg(&guild_id),
                    seg(&sub_id)
                )))
                .query(&[("limit", limit.to_string())]),
        )
        .await
    }

    // -- Analytics -----------------------------------------------------------

    /// A zero-filled time-series for one metric (`commands`, `command_failures`,
    /// `xp`, `members`) over a range (`24h`/`7d`/`30d`/`90d`/`1y`), optionally at a
    /// forced granularity (`hour`/`day`/`week`; auto when `None`).
    pub async fn get_analytics_series(
        &self,
        guild_id: u64,
        metric: &str,
        range: &str,
        granularity: Option<&str>,
    ) -> Result<AnalyticsSeries, PercyError> {
        let mut query = vec![("metric", metric.to_string()), ("range", range.to_string())];
        if let Some(g) = granularity {
            query.push(("granularity", g.to_string()));
        }
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/analytics/series", seg(&guild_id))))
                .query(&query),
        )
        .await
    }

    /// Headline numbers for a range, each with a delta vs the preceding window.
    pub async fn get_analytics_summary(&self, guild_id: u64, range: &str) -> Result<AnalyticsSummary, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/analytics/summary", seg(&guild_id))))
                .query(&[("range", range)]),
        )
        .await
    }

    // -- Backup & templates --------------------------------------------------

    /// Export the guild's portable config as a downloadable backup blob (raw JSON passthrough).
    pub async fn export_backup(&self, guild_id: u64) -> Result<serde_json::Value, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/backup/export", seg(&guild_id)))),
        )
        .await
    }

    /// Apply a backup blob to this guild. `dry_run` (default on Percy) previews the
    /// section item counts without writing; pass `false` to actually restore. Returns
    /// Percy's `{ok, dry_run, plan|applied}` report verbatim.
    pub async fn import_backup(
        &self,
        guild_id: u64,
        body: &serde_json::Value,
        dry_run: bool,
    ) -> Result<serde_json::Value, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/backup/import", seg(&guild_id))))
                .query(&[("dry_run", dry_run.to_string())])
                .json(body),
        )
        .await
    }

    /// Public templates (the gallery) plus the ones this guild has published.
    pub async fn get_templates(&self, guild_id: u64) -> Result<TemplatesResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/guilds/{}/templates", seg(&guild_id)))),
        )
        .await
    }

    /// Publish a template from the current guild's config (or a supplied backup blob).
    /// Surfaces Percy's validation / slug-conflict error text via [`PercyError::Api`].
    pub async fn publish_template(&self, guild_id: u64, body: &serde_json::Value) -> Result<(), PercyError> {
        self.send_unit(
            self.client
                .post(self.url(&format!("/api/v1/guilds/{}/templates", seg(&guild_id))))
                .json(body),
        )
        .await
    }

    /// Apply a template (public or your own) to this guild. `dry_run` previews;
    /// `false` restores. Returns Percy's `{ok, dry_run, plan|applied}` report.
    pub async fn apply_template(
        &self,
        guild_id: u64,
        slug: &str,
        dry_run: bool,
    ) -> Result<serde_json::Value, PercyError> {
        self.send_into(
            self.client
                .post(self.url(&format!(
                    "/api/v1/guilds/{}/templates/{}/apply",
                    seg(&guild_id),
                    seg(&slug)
                )))
                .query(&[("dry_run", dry_run.to_string())]),
        )
        .await
    }

    /// Delete a template this guild authored.
    pub async fn delete_template(&self, guild_id: u64, slug: &str) -> Result<(), PercyError> {
        self.send_unit(self.client.delete(self.url(&format!(
            "/api/v1/guilds/{}/templates/{}",
            seg(&guild_id),
            seg(&slug)
        ))))
        .await
    }

    // -- Watchlist -----------------------------------------------------------
    //
    // Not guild-scoped: a watch order is global content and progress is per Discord
    // user. The catalogue endpoints need no session at all, which is what lets the
    // watch-order page render server-side for logged-out visitors.

    /// List every published universe with its title count and total runtime.
    pub async fn list_watch_universes(&self) -> Result<WatchUniversesResponse, PercyError> {
        self.send_into(self.client.get(self.url("/api/v1/watchlist/universes")))
            .await
    }

    /// Fetch one universe's whole page: identity, paths, eras and every title.
    ///
    /// `path` selects which curated route's importance tags are joined in (Percy falls
    /// back to the universe's default); an unknown one is a `404` rather than a silent
    /// fallback. `region` picks the streaming providers; `sort` is `story` or `release`.
    pub async fn get_watch_universe(
        &self,
        slug: &str,
        path: Option<&str>,
        region: &str,
        sort: &str,
    ) -> Result<WatchUniversePage, PercyError> {
        let mut query = vec![("region", region.to_string()), ("sort", sort.to_string())];
        if let Some(path) = path {
            query.push(("path", path.to_string()));
        }
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/watchlist/universes/{}", seg(&slug))))
                .query(&query),
        )
        .await
    }

    /// Totals for one path: counts, importance breakdown, runtime and hours.
    pub async fn get_watch_universe_stats(&self, slug: &str, path: Option<&str>) -> Result<WatchStats, PercyError> {
        let query: Vec<(&str, String)> = path.map(|p| vec![("path", p.to_string())]).unwrap_or_default();
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/watchlist/universes/{}/stats", seg(&slug))))
                .query(&query),
        )
        .await
    }

    /// One title in detail, with its credits parent and its neighbours in `sort` order.
    pub async fn get_watch_title(
        &self,
        universe: &str,
        slug: &str,
        region: &str,
        sort: &str,
    ) -> Result<WatchTitleDetail, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/watchlist/titles/{}/{}", seg(&universe), seg(&slug))))
                .query(&[("region", region), ("sort", sort)]),
        )
        .await
    }

    /// One person and their whole filmography. An unknown slug is a 404, not an empty page.
    pub async fn get_watch_person(&self, slug: &str) -> Result<WatchPersonPage, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/watchlist/people/{}", seg(&slug)))),
        )
        .await
    }

    /// Search people by name, best match first — backs person autocomplete.
    pub async fn search_watch_people(&self, query: &str, limit: u32) -> Result<WatchPeopleResponse, PercyError> {
        self.send_into(
            self.client
                .get(self.url("/api/v1/watchlist/people"))
                .query(&[("q", query.to_string()), ("limit", limit.to_string())]),
        )
        .await
    }

    /// Fetch a user's watch progress, optionally scoped to one universe.
    pub async fn get_watch_progress(
        &self,
        discord_id: u64,
        universe: Option<&str>,
    ) -> Result<WatchProgressResponse, PercyError> {
        let query: Vec<(&str, String)> = universe.map(|u| vec![("universe", u.to_string())]).unwrap_or_default();
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/watchlist/users/{}/progress", seg(&discord_id))))
                .query(&query),
        )
        .await
    }

    /// Merge a batch of progress entries and return the merged set.
    ///
    /// `body` is `{ "entries": [{ "title_id": i64, "status": "watched"|"skipped",
    /// "updated_at": ISO-8601 }] }`. Percy resolves conflicts per title by `updated_at`,
    /// so a stale tab cannot overwrite a newer change made on another device.
    pub async fn put_watch_progress(
        &self,
        discord_id: u64,
        body: &serde_json::Value,
    ) -> Result<WatchProgressResponse, PercyError> {
        self.send_into(
            self.client
                .put(self.url(&format!("/api/v1/watchlist/users/{}/progress", seg(&discord_id))))
                .json(body),
        )
        .await
    }

    /// Reset a user's progress, optionally for one universe only.
    pub async fn delete_watch_progress(&self, discord_id: u64, universe: Option<&str>) -> Result<(), PercyError> {
        let query: Vec<(&str, String)> = universe.map(|u| vec![("universe", u.to_string())]).unwrap_or_default();
        self.send_unit(
            self.client
                .delete(self.url(&format!("/api/v1/watchlist/users/{}/progress", seg(&discord_id))))
                .query(&query),
        )
        .await
    }

    /// Fetch a user's watchlist preferences (Percy returns defaults if they saved none).
    pub async fn get_watch_prefs(&self, discord_id: u64) -> Result<WatchPrefs, PercyError> {
        self.send_into(
            self.client
                .get(self.url(&format!("/api/v1/watchlist/users/{}/prefs", seg(&discord_id)))),
        )
        .await
    }

    /// Update the given preference fields, leaving the rest untouched.
    pub async fn patch_watch_prefs(&self, discord_id: u64, body: &serde_json::Value) -> Result<WatchPrefs, PercyError> {
        self.send_into(
            self.client
                .patch(self.url(&format!("/api/v1/watchlist/users/{}/prefs", seg(&discord_id))))
                .json(body),
        )
        .await
    }

    // -- Release subscriptions -------------------------------------------------
    //
    // Not guild-scoped: a subscription belongs to a Discord user and the DM it
    // produces is theirs. `discord_id` travels as a string because that is what a
    // dashboard session holds; Percy parses the path segment itself.

    fn list_subscriptions_request(&self, discord_id: &str, media: Option<&str>) -> RequestBuilder {
        let query: Vec<(&str, &str)> = media.map(|m| vec![("media", m)]).unwrap_or_default();
        self.client
            .get(self.url(&format!("/api/v1/releases/users/{}/subscriptions", seg(&discord_id))))
            .query(&query)
    }

    /// Everything a user follows, optionally scoped to one medium.
    ///
    /// A user who follows nothing is an empty list, not a `404`.
    pub async fn list_subscriptions(
        &self,
        discord_id: &str,
        media: Option<&str>,
    ) -> Result<SubscriptionsResponse, PercyError> {
        self.send_into(self.list_subscriptions_request(discord_id, media)).await
    }

    /// Follow a subject, or refresh the settings of a follow that already exists.
    ///
    /// Idempotent: Percy upserts, so re-subscribing is not a conflict. Send the picker's
    /// `subject_value` verbatim — see [`NewSubscription`].
    pub async fn put_subscription(
        &self,
        discord_id: &str,
        body: &NewSubscription<'_>,
    ) -> Result<Subscription, PercyError> {
        self.send_into(
            self.client
                .put(self.url(&format!("/api/v1/releases/users/{}/subscriptions", seg(&discord_id))))
                .json(body),
        )
        .await
    }

    /// Change the stream or delivery mode of an existing follow.
    ///
    /// [`PercyError::NotFound`] means "the user does not follow that" — unlike
    /// [`Self::put_subscription`], this never creates one, so a stale toggle in an old tab
    /// cannot resurrect a subscription removed elsewhere.
    pub async fn patch_subscription(
        &self,
        discord_id: &str,
        body: &SubscriptionPatch<'_>,
    ) -> Result<Subscription, PercyError> {
        self.send_into(
            self.client
                .patch(self.url(&format!("/api/v1/releases/users/{}/subscriptions", seg(&discord_id))))
                .json(body),
        )
        .await
    }

    fn delete_subscription_request(
        &self,
        discord_id: &str,
        media: &str,
        subject_type: &str,
        subject_value: &str,
    ) -> RequestBuilder {
        self.client
            .delete(self.url(&format!("/api/v1/releases/users/{}/subscriptions", seg(&discord_id))))
            .query(&[
                ("media", media),
                ("subject_type", subject_type),
                ("subject_value", subject_value),
            ])
    }

    /// Unfollow a subject. `deleted` is false when there was nothing to remove.
    ///
    /// The triple travels as query parameters, not a body: a DELETE body is awkward for
    /// every HTTP layer between here and Percy.
    pub async fn delete_subscription(
        &self,
        discord_id: &str,
        media: &str,
        subject_type: &str,
        subject_value: &str,
    ) -> Result<SubscriptionDeleted, PercyError> {
        self.send_into(self.delete_subscription_request(discord_id, media, subject_type, subject_value))
            .await
    }

    fn search_subjects_request(&self, query: &str, media: Option<&str>, limit: Option<u32>) -> RequestBuilder {
        let mut req = self.client.get(self.url("/api/v1/releases/subjects"));
        if !query.is_empty() {
            req = req.query(&[("q", query)]);
        }
        if let Some(media) = media {
            req = req.query(&[("media", media)]);
        }
        if let Some(limit) = limit {
            req = req.query(&[("limit", limit.to_string())]);
        }
        req
    }

    /// The subject picker: everything a user could follow, ranked against `query`.
    ///
    /// An empty `query` returns the catalogue's popular subjects instead of nothing, which
    /// is what makes the picker useful on first paint.
    pub async fn search_subjects(
        &self,
        query: &str,
        media: Option<&str>,
        limit: Option<u32>,
    ) -> Result<SubjectsResponse, PercyError> {
        self.send_into(self.search_subjects_request(query, media, limit)).await
    }

    fn releases_overview_request(
        &self,
        days: Option<u32>,
        discord_id: Option<&str>,
        limit: Option<u32>,
    ) -> RequestBuilder {
        let mut req = self.client.get(self.url("/api/v1/releases/overview"));
        if let Some(days) = days {
            req = req.query(&[("days", days.to_string())]);
        }
        if let Some(discord_id) = discord_id {
            req = req.query(&[("discord_id", discord_id)]);
        }
        if let Some(limit) = limit {
            req = req.query(&[("limit", limit.to_string())]);
        }
        req
    }

    /// The front page of the catalogue: what just landed and what is imminent.
    ///
    /// `days` is the **half-width** of the window (1–30, Percy defaults to 7), `limit` caps
    /// each list. Passing `discord_id` is what fills `followed`; without it Percy returns an
    /// empty list rather than a `401`, so the same call serves the signed-out page.
    ///
    /// Every parameter is omitted when unset so a plain overview stays a cache-friendly bare
    /// URL, the same rule [`Self::search_subjects`] follows for an empty query.
    pub async fn releases_overview(
        &self,
        days: Option<u32>,
        discord_id: Option<&str>,
        limit: Option<u32>,
    ) -> Result<ReleasesOverview, PercyError> {
        self.send_into(self.releases_overview_request(days, discord_id, limit))
            .await
    }

    // -- Release progress ------------------------------------------------------
    //
    // `release_progress` (V42): read/watched marks for both media, keyed by the
    // card's own `key`. Distinct from [`Self::get_watch_progress`] above, which is
    // V39's `watch_progress`, keyed by title id and untouched by this.

    fn release_progress_request(&self, discord_id: &str, media: Option<&str>) -> RequestBuilder {
        let query: Vec<(&str, &str)> = media.map(|m| vec![("media", m)]).unwrap_or_default();
        self.client
            .get(self.url(&format!("/api/v1/releases/users/{}/progress", seg(&discord_id))))
            .query(&query)
    }

    /// A user's marks as `{media: {release_key: mark}}`, optionally one medium only.
    ///
    /// Both media keys are present either way, and a user who has marked nothing is two
    /// empty maps rather than a `404`.
    pub async fn release_progress(
        &self,
        discord_id: &str,
        media: Option<&str>,
    ) -> Result<ProgressResponse, PercyError> {
        self.send_into(self.release_progress_request(discord_id, media)).await
    }

    /// Merge a batch of marks and get the user's whole set back.
    ///
    /// Last-write-wins per entry on `updated_at`, so a stale tab replaying its
    /// localStorage cannot undo a newer mark made elsewhere. `merged` under
    /// `entries.len()` means the server was already ahead: a success, and the returned
    /// `entries` is what the client should adopt.
    pub async fn put_release_progress(
        &self,
        discord_id: &str,
        entries: &[ProgressEntry<'_>],
    ) -> Result<ProgressResponse, PercyError> {
        self.send_into(
            self.client
                .put(self.url(&format!("/api/v1/releases/users/{}/progress", seg(&discord_id))))
                .json(&serde_json::json!({ "entries": entries })),
        )
        .await
    }

    fn delete_release_progress_request(&self, discord_id: &str, media: &str, release_key: &str) -> RequestBuilder {
        self.client
            .delete(self.url(&format!("/api/v1/releases/users/{}/progress", seg(&discord_id))))
            // Both travel, always: a release key is only unique *within* a medium, and
            // Percy 400s a key without one rather than treating it as a wildcard.
            .query(&[("media", media), ("release_key", release_key)])
    }

    /// Unmark one item. The pair goes as query parameters, as the unsubscribe triple does.
    pub async fn delete_release_progress(
        &self,
        discord_id: &str,
        media: &str,
        release_key: &str,
    ) -> Result<(), PercyError> {
        self.send_unit(self.delete_release_progress_request(discord_id, media, release_key))
            .await
    }

    // -- News ------------------------------------------------------------------
    //
    // Read-only and not guild-scoped. Every item carries its source and the link to
    // the original, because the plan's §9 makes both a condition of showing the
    // story at all — see [`NewsItem`].

    fn news_request(&self, query: &NewsQuery<'_>) -> RequestBuilder {
        let mut req = self.client.get(self.url("/api/v1/news"));
        for (key, value) in [
            ("media", query.media),
            ("subject_type", query.subject_type),
            ("subject_value", query.subject_value),
            ("source", query.source),
            ("since", query.since),
            ("search", query.search),
            ("discord_id", query.discord_id),
        ] {
            if let Some(value) = value {
                req = req.query(&[(key, value)]);
            }
        }
        if let Some(page) = query.page {
            req = req.query(&[("page", page.to_string())]);
        }
        if let Some(per_page) = query.per_page {
            req = req.query(&[("per_page", per_page.to_string())]);
        }
        req
    }

    /// One page of the news stream, newest-first.
    ///
    /// Every filter is optional and every unset one is omitted, so a plain stream
    /// stays a bare, cache-friendly URL. Passing `discord_id` is what makes the
    /// caller's followed stories lead the *same* list — it never splits the payload
    /// in two, and never `401`s a signed-out reader.
    pub async fn news(&self, query: &NewsQuery<'_>) -> Result<NewsPage, PercyError> {
        self.send_into(self.news_request(query)).await
    }

    fn news_related_request(
        &self,
        media: &str,
        subject_type: &str,
        subject_value: &str,
        limit: Option<u32>,
    ) -> RequestBuilder {
        let mut req = self.client.get(self.url("/api/v1/news/related")).query(&[
            ("media", media),
            ("subject_type", subject_type),
            ("subject_value", subject_value),
        ]);
        if let Some(limit) = limit {
            req = req.query(&[("limit", limit.to_string())]);
        }
        req
    }

    /// Recent stories for one subject — the detail pages' related strip.
    ///
    /// The triple is mandatory here (unlike [`Self::news`], where a lone
    /// `subject_type` is simply ignored): Percy validates the *pairing* of medium and
    /// subject type, so `media = "comic"` with `subject_type = "person"` is a `400`
    /// rather than a silent empty list. `limit` defaults to 3 and Percy caps it at 10.
    pub async fn news_related(
        &self,
        media: &str,
        subject_type: &str,
        subject_value: &str,
        limit: Option<u32>,
    ) -> Result<NewsRelated, PercyError> {
        self.send_into(self.news_related_request(media, subject_type, subject_value, limit))
            .await
    }

    fn news_sources_request(&self, enabled_only: bool) -> RequestBuilder {
        let mut req = self.client.get(self.url("/api/v1/news/sources"));
        // Percy's own default is `false`; sending it would be the same request with a
        // cache-hostile extra parameter, the rule every other call here follows.
        if enabled_only {
            req = req.query(&[("enabled_only", "true")]);
        }
        req
    }

    /// Every configured feed, with when it was last polled and what it answered.
    ///
    /// Deliberately uncached on Percy's side: `last_status` exists to tell an operator
    /// a feed is failing *now*.
    pub async fn news_sources(&self, enabled_only: bool) -> Result<NewsSourcesResponse, PercyError> {
        self.send_into(self.news_sources_request(enabled_only)).await
    }
}

// -- Error type --------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
pub enum PercyError {
    #[error("guild not found")]
    NotFound,
    #[error("HTTP error: {0}")]
    Http(#[from] reqwest::Error),
    #[error("{0}")]
    Api(String),
    /// A refusal that keeps Percy's HTTP status (only the public-API methods return this), with the
    /// `detail` text FastAPI sent, so the gateway can map 400/404/409 onto its own error codes.
    #[error("Percy refused ({status}): {detail}")]
    Rejected { status: u16, detail: String },
}

/// Turns a refused decide response into a [`RequestDecision`]: Percy's own envelope when it is
/// one, else FastAPI's `{"detail": ...}`, else the raw text.
fn refusal_from_body(body: &str) -> RequestDecision {
    if let Ok(mut d) = serde_json::from_str::<RequestDecision>(body) {
        d.ok = false;
        return d;
    }
    let message = serde_json::from_str::<serde_json::Value>(body)
        .ok()
        .and_then(|v| v.get("detail").and_then(|d| d.as_str().map(str::to_string)))
        .unwrap_or_else(|| body.to_string());
    RequestDecision {
        ok: false,
        status: "error".into(),
        message,
        request: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn refusal_body_keeps_envelope_or_detail() {
        let d = refusal_from_body(r#"{"ok":false,"status":"denied","message":"already denied","request":null}"#);
        assert!(!d.ok && d.message == "already denied" && d.status == "denied");
        let d = refusal_from_body(r#"{"detail":"moderator not in server"}"#);
        assert!(!d.ok && d.message == "moderator not in server");
    }

    /// The generated analytics types read the exact body `tests/test_internal_api_analytics.py` pins on the Python side.
    #[test]
    fn generated_analytics_summary_reads_the_wire_shape() {
        let s: AnalyticsSummary = serde_json::from_str(
            r#"{"range":"7d","commands":{"value":10,"delta":6},"new_members":{"value":1,"delta":1},
                "member_count":null,"top_commands":[{"command":"ping","uses":7}]}"#,
        )
        .unwrap();
        assert_eq!((s.commands.delta, s.member_count, s.top_commands[0].uses), (6, None, 7));
        assert_eq!(AnalyticsSummary::default().range, "");
    }

    fn client() -> PercyClient {
        PercyClient::new(Client::new(), "http://localhost", "token")
    }

    fn query_pairs(req: RequestBuilder) -> Vec<(String, String)> {
        req.build()
            .unwrap()
            .url()
            .query_pairs()
            .map(|(k, v)| (k.into_owned(), v.into_owned()))
            .collect()
    }

    #[test]
    fn list_comics_omits_unset_filters() {
        let pairs = query_pairs(client().list_comics_request(&ComicBrowseQuery {
            brand: Some("marvel"),
            search: Some("spider"),
            page: Some(2),
            ..Default::default()
        }));
        assert!(pairs.contains(&("brand".to_string(), "marvel".to_string())));
        assert!(pairs.contains(&("search".to_string(), "spider".to_string())));
        assert!(pairs.contains(&("page".to_string(), "2".to_string())));
        assert!(!pairs.iter().any(|(k, _)| k == "series"));
        assert!(!pairs.iter().any(|(k, _)| k == "creator"));
        assert!(!pairs.iter().any(|(k, _)| k == "character"));
        assert!(!pairs.iter().any(|(k, _)| k == "since"));
        assert!(!pairs.iter().any(|(k, _)| k == "until"));
        assert!(!pairs.iter().any(|(k, _)| k == "per_page"));
    }

    #[test]
    fn list_comics_keeps_creator_and_character_distinct() {
        let pairs = query_pairs(client().list_comics_request(&ComicBrowseQuery {
            creator: Some("jane-doe"),
            character: Some("spider-man"),
            ..Default::default()
        }));
        assert!(pairs.contains(&("creator".to_string(), "jane-doe".to_string())));
        assert!(pairs.contains(&("character".to_string(), "spider-man".to_string())));
    }

    #[test]
    fn list_comics_with_no_filters_has_no_query() {
        let pairs = query_pairs(client().list_comics_request(&ComicBrowseQuery::default()));
        assert!(pairs.is_empty());
    }

    #[test]
    fn list_comic_series_brand_is_optional() {
        assert!(query_pairs(client().list_comic_series_request(None)).is_empty());
        assert_eq!(
            query_pairs(client().list_comic_series_request(Some("dc"))),
            vec![("brand".to_string(), "dc".to_string())]
        );
    }

    #[test]
    fn get_comic_series_brand_is_optional() {
        assert!(query_pairs(client().get_comic_series_request("spider-man", None)).is_empty());
        assert_eq!(
            query_pairs(client().get_comic_series_request("spider-man", Some("marvel"))),
            vec![("brand".to_string(), "marvel".to_string())]
        );
    }

    #[test]
    fn subject_search_omits_an_empty_query() {
        // Percy treats a missing `q` as "show me the popular subjects"; sending `q=`
        // would be the same request with a cache-hostile extra parameter.
        assert!(query_pairs(client().search_subjects_request("", None, None)).is_empty());

        let pairs = query_pairs(client().search_subjects_request("loki", Some("title"), Some(10)));
        assert!(pairs.contains(&("q".to_string(), "loki".to_string())));
        assert!(pairs.contains(&("media".to_string(), "title".to_string())));
        assert!(pairs.contains(&("limit".to_string(), "10".to_string())));
    }

    #[test]
    fn deleting_a_subscription_sends_the_whole_triple() {
        // All three identify the row; dropping one would delete a different follow.
        assert_eq!(
            query_pairs(client().delete_subscription_request("42", "comic", "series", "amazing-spider-man")),
            vec![
                ("media".to_string(), "comic".to_string()),
                ("subject_type".to_string(), "series".to_string()),
                ("subject_value".to_string(), "amazing-spider-man".to_string()),
            ]
        );
    }

    #[test]
    fn listing_subscriptions_scopes_by_media_only_when_asked() {
        assert!(query_pairs(client().list_subscriptions_request("42", None)).is_empty());
        assert_eq!(
            query_pairs(client().list_subscriptions_request("42", Some("title"))),
            vec![("media".to_string(), "title".to_string())]
        );
    }

    #[test]
    fn unset_subscription_fields_are_omitted_from_the_body() {
        // Percy defaults `streams`/`delivery` itself, and a patch with neither set is a
        // 400 — so an unset field must not serialise as null.
        let body = serde_json::to_value(NewSubscription {
            media: "title",
            subject_type: "person",
            subject_value: "pedro-pascal",
            ..Default::default()
        })
        .unwrap();
        assert_eq!(
            body,
            serde_json::json!({"media": "title", "subject_type": "person", "subject_value": "pedro-pascal"})
        );

        let patch = serde_json::to_value(SubscriptionPatch {
            media: "comic",
            subject_type: "brand",
            subject_value: "marvel",
            delivery: Some("off"),
            ..Default::default()
        })
        .unwrap();
        assert_eq!(
            patch,
            serde_json::json!({"media": "comic", "subject_type": "brand", "subject_value": "marvel", "delivery": "off"})
        );
    }

    #[test]
    fn the_overview_omits_every_unset_parameter() {
        // A signed-out page is the bare URL: Percy's own defaults, nothing to key a cache on.
        assert!(query_pairs(client().releases_overview_request(None, None, None)).is_empty());

        let pairs = query_pairs(client().releases_overview_request(Some(30), Some("42"), Some(12)));
        assert!(pairs.contains(&("days".to_string(), "30".to_string())));
        // Personalisation is what turns `followed` from an empty list into a section.
        assert!(pairs.contains(&("discord_id".to_string(), "42".to_string())));
        assert!(pairs.contains(&("limit".to_string(), "12".to_string())));
    }

    #[test]
    fn progress_scopes_by_media_only_when_asked() {
        assert!(query_pairs(client().release_progress_request("42", None)).is_empty());
        assert_eq!(
            query_pairs(client().release_progress_request("42", Some("comic"))),
            vec![("media".to_string(), "comic".to_string())]
        );
    }

    #[test]
    fn unmarking_always_sends_both_halves_of_the_identity() {
        // Percy 400s a `release_key` without a `media`: the key is only unique within
        // one medium, so honouring it unscoped could delete a comic's row because a film
        // happened to share its slug.
        assert_eq!(
            query_pairs(client().delete_release_progress_request("42", "title", "mcu/iron-man")),
            vec![
                ("media".to_string(), "title".to_string()),
                ("release_key".to_string(), "mcu/iron-man".to_string()),
            ]
        );
    }

    #[test]
    fn a_progress_entry_omits_an_unset_timestamp_and_never_renames_its_key() {
        let body = serde_json::to_value(ProgressEntry {
            media: "comic",
            // The card's `key` verbatim, brand casing and all.
            release_key: "MARVEL/amazing-spider-man-1",
            status: "read",
            ..Default::default()
        })
        .unwrap();
        assert_eq!(
            body,
            serde_json::json!({"media": "comic", "release_key": "MARVEL/amazing-spider-man-1", "status": "read"})
        );

        let stamped = serde_json::to_value(ProgressEntry {
            media: "title",
            release_key: "mcu/iron-man",
            status: "watched",
            updated_at: Some("2026-08-05T10:00:00Z"),
        })
        .unwrap();
        assert_eq!(stamped["updated_at"], "2026-08-05T10:00:00Z");
    }

    #[test]
    fn a_progress_payload_keeps_both_media_and_reports_a_partial_merge() {
        let response: ProgressResponse = serde_json::from_value(serde_json::json!({
            "merged": 1,
            "entries": {
                "comic": {"MARVEL/asm-12": {"status": "read", "updated_at": "2026-08-05T10:00:00"}},
                "title": {}
            }
        }))
        .unwrap();

        // `merged` under what was sent is a success in which the server was newer.
        assert_eq!(response.merged, 1);
        assert_eq!(response.entries["comic"]["MARVEL/asm-12"].status, "read");
        // Both media always present, so a renderer indexes without a guard.
        assert!(response.entries["title"].is_empty());

        // A GET carries no `merged`; it must still deserialise.
        let listed: ProgressResponse =
            serde_json::from_value(serde_json::json!({"entries": {"comic": {}, "title": {}}})).unwrap();
        assert_eq!(listed.merged, 0);
    }

    #[test]
    fn the_overview_carries_progress_beside_the_cards() {
        let card = serde_json::json!({
            "media": "comic", "key": "MARVEL/asm-12", "name": "ASM #12", "subtitle": null,
            "release_date": null, "cover_url": null, "url": null, "subjects": []
        });
        let overview: ReleasesOverview = serde_json::from_value(serde_json::json!({
            "window": {"start": "2026-10-25", "end": "2026-11-08"},
            "comics": [card], "titles": [], "followed": [],
            "progress": {"comic": {"MARVEL/asm-12": {"status": "read", "updated_at": null}}, "title": {}}
        }))
        .unwrap();
        assert_eq!(overview.progress["comic"]["MARVEL/asm-12"].status, "read");
        // `updated_at` is nullable in the database, so a mark may carry none.
        assert!(overview.progress["comic"]["MARVEL/asm-12"].updated_at.is_none());
    }

    #[test]
    fn news_omits_every_unset_filter() {
        // The ticker's call: latest, nothing else. A bare URL is what makes it
        // cacheable across every signed-out reader.
        assert!(query_pairs(client().news_request(&NewsQuery::default())).is_empty());

        let pairs = query_pairs(client().news_request(&NewsQuery {
            media: Some("comic"),
            search: Some("spider"),
            page: Some(2),
            ..Default::default()
        }));
        assert!(pairs.contains(&("media".to_string(), "comic".to_string())));
        assert!(pairs.contains(&("search".to_string(), "spider".to_string())));
        assert!(pairs.contains(&("page".to_string(), "2".to_string())));
        for absent in [
            "subject_type",
            "subject_value",
            "source",
            "since",
            "per_page",
            "discord_id",
        ] {
            assert!(!pairs.iter().any(|(k, _)| k == absent), "{absent} should be omitted");
        }
    }

    #[test]
    fn news_sends_the_subject_pair_and_the_viewer() {
        let pairs = query_pairs(client().news_request(&NewsQuery {
            subject_type: Some("series"),
            // The opaque key, not the label — posting "Amazing Spider-Man" here would
            // filter on a subject Percy has never stored.
            subject_value: Some("amazing-spider-man"),
            discord_id: Some("42"),
            per_page: Some(20),
            ..Default::default()
        }));
        assert!(pairs.contains(&("subject_type".to_string(), "series".to_string())));
        assert!(pairs.contains(&("subject_value".to_string(), "amazing-spider-man".to_string())));
        // Personalisation is what makes followed stories lead the same single list.
        assert!(pairs.contains(&("discord_id".to_string(), "42".to_string())));
        assert!(pairs.contains(&("per_page".to_string(), "20".to_string())));
    }

    #[test]
    fn news_related_always_sends_the_whole_triple_and_limit_only_when_set() {
        assert_eq!(
            query_pairs(client().news_related_request("comic", "series", "amazing-spider-man", None)),
            vec![
                ("media".to_string(), "comic".to_string()),
                ("subject_type".to_string(), "series".to_string()),
                ("subject_value".to_string(), "amazing-spider-man".to_string()),
            ]
        );
        let pairs = query_pairs(client().news_related_request("title", "person", "pedro-pascal", Some(3)));
        assert!(pairs.contains(&("limit".to_string(), "3".to_string())));
    }

    #[test]
    fn news_sources_sends_enabled_only_when_it_differs_from_percys_default() {
        assert!(query_pairs(client().news_sources_request(false)).is_empty());
        assert_eq!(
            query_pairs(client().news_sources_request(true)),
            vec![("enabled_only".to_string(), "true".to_string())]
        );
    }

    /// A complete title as Percy sends it (every key present, nullable ones null).
    fn title_json(slug: &str, trailer: serde_json::Value) -> serde_json::Value {
        serde_json::json!({
            "id": 1, "universe": "mcu", "tmdb_type": "movie", "tmdb_id": 1726, "slug": slug, "title": "Iron Man",
            "kind": "movie", "runtime": 126, "episodes": null, "release_date": null, "poster_path": null,
            "backdrop_path": null, "overview": null, "tmdb_rating": null, "tmdb_votes": null, "era": null,
            "era_order": 0, "story_order": 1, "release_order": 1, "milestone": false, "instruction": null,
            "context": null, "spoiler": null, "credits_of": null, "sub_universe": null, "importance": null,
            "providers": [], "trailer": trailer, "season_number": null, "season_name": null, "parent_id": null
        })
    }

    #[test]
    fn a_title_detail_carries_its_place_in_the_ordering_and_its_trailer() {
        // The four fields the detail page's "position N of M" strip is made of, plus the
        // V44 trailer — both URLs arrive pre-built, so the renderer never assembles one.
        let trailer = serde_json::json!({
            "site": "YouTube",
            "key": "8ugaeA-nMTc",
            "name": "Official Trailer",
            "url": "https://www.youtube.com/watch?v=8ugaeA-nMTc",
            "embed_url": "https://www.youtube.com/embed/8ugaeA-nMTc"
        });
        let detail: WatchTitleDetail = serde_json::from_value(serde_json::json!({
            "title": title_json("iron-man", trailer),
            "credits_of": null, "parent": null, "seasons": [], "previous": null, "next": null,
            "credits": [],
            "position": 3,
            "total": 42,
            "path": "complete",
            "sort": "story",
            "universe": {
                "slug": "mcu", "name": "Marvel Cinematic Universe", "short_name": "MCU", "tagline": null,
                "accent": "#E62429", "logo_url": null, "backdrop_url": null, "published": true, "synced_at": null
            }
        }))
        .unwrap();

        assert_eq!((detail.position, detail.total), (3, 42));
        assert_eq!(detail.path.as_deref(), Some("complete"));
        assert_eq!(detail.sort, "story");
        let trailer = detail.title.trailer.expect("the trailer deserialises");
        assert_eq!(trailer.site, "YouTube");
        assert_eq!(trailer.embed_url, "https://www.youtube.com/embed/8ugaeA-nMTc");
        // The embed URL carries no playback parameters: autoplay is forbidden (§6.2).
        assert!(!trailer.embed_url.contains("autoplay"));
    }

    #[test]
    fn a_title_without_a_trailer_is_none_rather_than_a_deserialisation_failure() {
        // `null` is the common case (an unreleased film), not an error. The key itself is
        // required now that the schema is generated from Percy's response model.
        let with_null: WatchTitle =
            serde_json::from_value(title_json("avengers-doomsday", serde_json::Value::Null)).unwrap();
        assert!(with_null.trailer.is_none());
    }

    #[test]
    fn list_comic_names_always_sends_kind_and_optionally_brand_and_limit() {
        let pairs = query_pairs(client().list_comic_names_request("creators", None, None));
        assert_eq!(pairs, vec![("kind".to_string(), "creators".to_string())]);

        let pairs = query_pairs(client().list_comic_names_request("characters", Some("marvel"), Some(25)));
        assert!(pairs.contains(&("kind".to_string(), "characters".to_string())));
        assert!(pairs.contains(&("brand".to_string(), "marvel".to_string())));
        assert!(pairs.contains(&("limit".to_string(), "25".to_string())));
    }
}

#[cfg(test)]
mod seg_tests {
    use super::seg;

    #[test]
    fn encodes_one_segment() {
        assert_eq!(
            seg(&"../../guilds/9/x?a#b%"),
            "%2E%2E%2F%2E%2E%2Fguilds%2F9%2Fx%3Fa%23b%25"
        );
        assert_eq!(seg(&"a\\b"), "a%5Cb");
        assert_eq!(seg(&123456789012345678u64), "123456789012345678");
        assert_eq!(seg(&"ok-name_1~"), "ok-name_1~");
    }
}
