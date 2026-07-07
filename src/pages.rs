//! Per-guild feature pages (the dashboard tabs), ported from the monolith's
//! `dashboard/handlers.rs`. Each follows the same shape: gate on a managed-guild
//! session ([`gate_and_load_guild`]), fan out the page's Percy reads with `join!`,
//! degrade every sub-resource to an empty default, and render the verbatim
//! template. The fetch/fallback/count logic is copied 1:1 from the monolith; only
//! the auth prelude and the `account` view-model are adapted to the dashboard.

use axum::{
    extract::{Path, State},
    response::{IntoResponse, Redirect, Response},
};
use percy_client::*;

use crate::{
    guilds::gate_and_load_guild,
    session::Session,
    templates::{
        AnalyticsTemplate, AutorespondersTemplate, BackupTemplate, BlacklistRow, ComicsTemplate, CommandsTemplate,
        EconomyTemplate, EmojiStatsTemplate, GalleryTemplate, GiveawaysTemplate, HighlightsTemplate, LevelRoleRow,
        LevelingTemplate, MultiplierRow, MusicTemplate, PollsTemplate, SpecialMsgRow, StatsTemplate, TagsTemplate,
        TempChannelsTemplate, WebhooksTemplate,
    },
    view::Viewer,
    AppState,
};

/// Discord role colour (`u32`) to a CSS hex string; the uncoloured default maps
/// to Discord's neutral grey. Copied from the monolith.
fn color_hex(color: u32) -> String {
    if color == 0 {
        "#99aab5".to_string()
    } else {
        format!("#{color:06x}")
    }
}

/// `GET /dashboard/guild/{id}/leveling`
pub async fn guild_leveling(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    // Fan out the five remaining independent reads concurrently.
    let (config, leaderboard, xp_history, roles, channels) = tokio::join!(
        percy.get_leveling_config(guild_id),
        percy.get_leveling_leaderboard(guild_id, 25),
        percy.get_leveling_xp_history(guild_id, 30),
        percy.get_guild_roles(guild_id),
        percy.get_guild_channels(guild_id),
    );

    let config = config.unwrap_or_default();
    let leaderboard = leaderboard.unwrap_or(LeaderboardResponse {
        entries: Vec::new(),
        total: 0,
    });

    // Daily cumulative-XP snapshots for the chart; serialize for the inline uPlot script.
    let xp_history = xp_history.unwrap_or_default();
    let xp_history_json = serde_json::to_string(&xp_history.points).unwrap_or_else(|_| "[]".to_string());

    let roles = roles.unwrap_or_default();
    let channels = channels.unwrap_or_default();

    // Resolve Discord IDs to display names from the fetched role/channel lists.
    let role_name = |id: &str| -> String {
        roles
            .iter()
            .find(|r| r.id == id)
            .map(|r| r.name.clone())
            .unwrap_or_else(|| format!("Role {id}"))
    };
    let channel_name = |id: &str| -> String {
        channels
            .iter()
            .find(|c| c.id == id)
            .map(|c| c.name.clone())
            .unwrap_or_else(|| format!("Channel {id}"))
    };

    // Level-reward roles, sorted by their level threshold (resolve color too).
    let mut level_roles: Vec<LevelRoleRow> = config
        .level_roles
        .iter()
        .map(|(rid, lvl)| {
            let role = roles.iter().find(|r| &r.id == rid);
            LevelRoleRow {
                role_id: rid.clone(),
                role_name: role.map(|r| r.name.clone()).unwrap_or_else(|| format!("Role {rid}")),
                color_hex: role
                    .map(|r| color_hex(r.color))
                    .unwrap_or_else(|| "#99aab5".to_string()),
                level: *lvl,
            }
        })
        .collect();
    level_roles.sort_by_key(|r| r.level);

    // XP multipliers for roles and channels, sorted by display name.
    let mut multiplier_roles: Vec<MultiplierRow> = config
        .multiplier_roles
        .iter()
        .map(|(rid, m)| MultiplierRow {
            id: rid.clone(),
            name: role_name(rid),
            multiplier: *m,
        })
        .collect();
    multiplier_roles.sort_by(|a, b| a.name.cmp(&b.name));
    let mut multiplier_channels: Vec<MultiplierRow> = config
        .multiplier_channels
        .iter()
        .map(|(cid, m)| MultiplierRow {
            id: cid.clone(),
            name: channel_name(cid),
            multiplier: *m,
        })
        .collect();
    multiplier_channels.sort_by(|a, b| a.name.cmp(&b.name));

    // Blacklists (roles/channels resolve to names; users only have an ID here).
    let blacklisted_roles: Vec<BlacklistRow> = config
        .blacklisted_roles
        .iter()
        .map(|id| BlacklistRow {
            id: id.to_string(),
            name: role_name(&id.to_string()),
        })
        .collect();
    let blacklisted_channels: Vec<BlacklistRow> = config
        .blacklisted_channels
        .iter()
        .map(|id| BlacklistRow {
            id: id.to_string(),
            name: channel_name(&id.to_string()),
        })
        .collect();
    let blacklisted_users: Vec<BlacklistRow> = config
        .blacklisted_users
        .iter()
        .map(|id| BlacklistRow {
            id: id.to_string(),
            name: format!("User {id}"),
        })
        .collect();

    // Custom per-level messages, sorted by numeric level.
    let mut special_messages: Vec<SpecialMsgRow> = config
        .special_level_up_messages
        .iter()
        .map(|(lvl, msg)| SpecialMsgRow {
            level: lvl.clone(),
            message: msg.clone(),
        })
        .collect();
    special_messages.sort_by_key(|r| r.level.parse::<i64>().unwrap_or(0));

    // Text channels for the level-up message destination; all channels for multiplier/blacklist pickers.
    let text_channels: Vec<Channel> = channels
        .iter()
        .filter(|c| c.channel_type == "text" || c.channel_type == "news")
        .cloned()
        .collect();
    let all_channels = channels.clone();

    LevelingTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "leveling",
        page_title: "Leveling",
        config,
        leaderboard,
        roles,
        text_channels,
        all_channels,
        level_roles,
        multiplier_roles,
        multiplier_channels,
        blacklisted_roles,
        blacklisted_channels,
        blacklisted_users,
        special_messages,
        xp_history_json,
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/stats`
pub async fn guild_stats(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let (stats, bot_stats) = tokio::join!(percy.get_guild_stats(guild_id), percy.get_bot_stats());

    let stats = match stats {
        Ok(s) => s,
        Err(_) => return Redirect::to(&format!("/dashboard/guild/{guild_id}")).into_response(),
    };
    let bot_stats = bot_stats.unwrap_or(BotStats {
        version: None,
        guild_count: 0,
        user_count: 0,
        channel_count: 0,
        total_commands_used: 0,
        cog_count: 0,
        command_count: 0,
        latency_ms: 0.0,
        uptime_seconds: 0.0,
    });

    StatsTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "stats",
        page_title: "Stats",
        stats,
        bot_stats,
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/autoresponders`
pub async fn guild_autoresponders(
    State(app): State<AppState>,
    session: Session,
    Path(guild_id): Path<u64>,
) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let data = percy
        .get_autoresponders(guild_id)
        .await
        .unwrap_or(AutorespondersResponse {
            entries: Vec::new(),
            total: 0,
        });

    AutorespondersTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "autoresponders",
        page_title: "Autoresponders",
        data,
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/economy`
pub async fn guild_economy(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let (economy, balances, channels, roles) = tokio::join!(
        percy.get_economy(guild_id),
        percy.get_economy_balances(guild_id, 25),
        percy.get_guild_channels(guild_id),
        percy.get_guild_roles(guild_id),
    );
    let economy = economy.unwrap_or(EconomyInfo {
        items: Vec::new(),
        lottery: None,
        settings: None,
    });
    let balances = balances.unwrap_or(BalancesResponse {
        entries: Vec::new(),
        total: 0,
    });
    let eco_settings = economy.settings.clone().unwrap_or_default();

    EconomyTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "economy",
        page_title: "Economy",
        economy,
        eco_settings,
        balances,
        channels: channels.unwrap_or_default(),
        roles: roles.unwrap_or_default(),
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/music`
pub async fn guild_music(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let (music, channels) = tokio::join!(percy.get_music(guild_id), percy.get_guild_channels(guild_id));
    let music = music.unwrap_or(MusicInfo {
        active: false,
        equalizer: vec![0.0; 15],
        filters: MusicFiltersState {
            nightcore: false,
            eight_d: false,
            lowpass: None,
        },
        presets: vec![],
        now_playing: None,
        queue: Vec::new(),
        history: Vec::new(),
        channel: None,
        channel_name: None,
        setup: None,
        always_on: Default::default(),
        listeners: Vec::new(),
    });

    MusicTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name.clone(),
        nav_active: "music",
        page_title: "Music",
        music,
        guild,
        channels: channels.unwrap_or_default(),
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/comics`
pub async fn guild_comics(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let (data, channels, roles) = tokio::join!(
        percy.get_comics(guild_id),
        percy.get_guild_channels(guild_id),
        percy.get_guild_roles(guild_id),
    );

    ComicsTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "comics",
        page_title: "Comics",
        data: data.unwrap_or(ComicsResponse { feeds: Vec::new() }),
        channels: channels.unwrap_or_default(),
        roles: roles.unwrap_or_default(),
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/temp-channels`
pub async fn guild_temp_channels(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let (data, channels) = tokio::join!(percy.get_temp_channels(guild_id), percy.get_guild_channels(guild_id));

    TempChannelsTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "temp-channels",
        page_title: "Temp Channels",
        data: data.unwrap_or(TempChannelsResponse { entries: Vec::new() }),
        channels: channels.unwrap_or_default(),
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/highlights`
pub async fn guild_highlights(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let data = percy.get_highlights(guild_id).await.unwrap_or(HighlightsResponse {
        entries: Vec::new(),
        total: 0,
    });

    HighlightsTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "highlights",
        page_title: "Highlights",
        data,
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/emoji-stats`
pub async fn guild_emoji_stats(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let data = percy.get_emoji_stats(guild_id, 50).await.unwrap_or(EmojiStatsResponse {
        total_uses: 0,
        distinct_emojis: 0,
        entries: Vec::new(),
        total: 0,
    });

    EmojiStatsTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "emoji-stats",
        page_title: "Emoji Stats",
        data,
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/polls`
pub async fn guild_polls(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let (channels, roles, polls) = tokio::join!(
        percy.get_guild_channels(guild_id),
        percy.get_guild_roles(guild_id),
        percy.get_polls(guild_id),
    );
    let polls = polls.unwrap_or(PollsResponse {
        polls: Vec::new(),
        total: 0,
    });
    let active_count = polls.polls.iter().filter(|p| !p.ended).count();
    let ended_count = polls.polls.iter().filter(|p| p.ended).count();

    PollsTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name.clone(),
        guild,
        channels: channels.unwrap_or_default(),
        roles: roles.unwrap_or_default(),
        nav_active: "polls",
        page_title: "Polls",
        polls,
        active_count,
        ended_count,
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/gallery`
pub async fn guild_gallery(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    // Degrade to an empty gallery when the hoster is unreachable — the page still
    // renders with the upload form so the owner can retry.
    let gallery = percy.get_guild_gallery(guild_id).await.unwrap_or_default();

    GalleryTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "gallery",
        page_title: "Image Gallery",
        gallery,
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/giveaways`
pub async fn guild_giveaways(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let (giveaways, channels) = tokio::join!(percy.get_giveaways(guild_id), percy.get_guild_channels(guild_id));
    let giveaways = giveaways.unwrap_or(GiveawaysResponse {
        giveaways: Vec::new(),
        total: 0,
    });
    let active_count = giveaways.giveaways.iter().filter(|g| !g.ended).count();
    let ended_count = giveaways.giveaways.iter().filter(|g| g.ended).count();

    GiveawaysTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "giveaways",
        page_title: "Giveaways",
        giveaways,
        channels: channels.unwrap_or_default(),
        active_count,
        ended_count,
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/tags`
pub async fn guild_tags(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let tags = percy.get_tags(guild_id).await.unwrap_or(TagsResponse {
        total: 0,
        total_uses: 0,
        tags: Vec::new(),
        top_creators: Vec::new(),
    });

    TagsTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "tags",
        page_title: "Tags",
        tags,
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/commands`
pub async fn guild_commands(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let (commands, channels, roles) = tokio::join!(
        percy.get_commands(guild_id),
        percy.get_guild_channels(guild_id),
        percy.get_guild_roles(guild_id),
    );
    let commands = commands.unwrap_or(CommandsResponse {
        commands: Vec::new(),
        plonks: Vec::new(),
    });
    // Drop the @everyone role and managed (bot/integration) roles — they can't be
    // granted, so they'd only clutter the allow-list picker.
    let mut roles = roles.unwrap_or_default();
    roles.retain(|r| r.id != guild_id.to_string() && !r.managed);
    roles.sort_by_key(|r| std::cmp::Reverse(r.position));

    let disabled_count = commands.commands.iter().filter(|c| c.globally_disabled).count();
    let partial_count = commands
        .commands
        .iter()
        .filter(|c| !c.globally_disabled && !c.disabled_in.is_empty())
        .count();
    let override_count = commands
        .commands
        .iter()
        .filter(|c| c.permission_override.is_some())
        .count();

    CommandsTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "commands",
        page_title: "Commands",
        commands,
        channels: channels.unwrap_or_default(),
        roles,
        disabled_count,
        partial_count,
        override_count,
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/analytics`
pub async fn guild_analytics(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    // The page opens on the 30-day window: server-render the headline tiles and
    // paint the initial commands series. Metric/range switches re-fetch via JSON.
    let (summary, series) = tokio::join!(
        percy.get_analytics_summary(guild_id, "30d"),
        percy.get_analytics_series(guild_id, "commands", "30d", None),
    );

    let series = series.unwrap_or_default();
    let initial_series_json = serde_json::to_string(&series.points).unwrap_or_else(|_| "[]".to_string());

    AnalyticsTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "analytics",
        page_title: "Analytics",
        summary: summary.unwrap_or_default(),
        range: "30d",
        initial_series_json,
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/webhooks`
pub async fn guild_webhooks(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    let (subs, events) = tokio::join!(percy.get_webhooks(guild_id), percy.get_webhook_events(guild_id));

    WebhooksTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "webhooks",
        page_title: "Webhooks",
        subscriptions: subs.map(|r| r.subscriptions).unwrap_or_default(),
        events: events.map(|r| r.events).unwrap_or_default(),
    }
    .into_response()
}

/// `GET /dashboard/guild/{id}/backup`
pub async fn guild_backup(State(app): State<AppState>, session: Session, Path(guild_id): Path<u64>) -> Response {
    let guild = match gate_and_load_guild(&app, &session, guild_id).await {
        Ok(guild) => guild,
        Err(response) => return response,
    };
    let percy = app.percy.as_ref().expect("gate ensured Percy is configured");

    // The template gallery degrades to empty when Percy is unreachable — the
    // export/import controls still render.
    let templates = percy.get_templates(guild_id).await.unwrap_or_default();

    BackupTemplate {
        account: Some(Viewer::from_session(&session)),
        guild_id,
        guild_name: guild.name,
        nav_active: "backup",
        page_title: "Backup & Templates",
        public_templates: templates.public,
        own_templates: templates.own,
    }
    .into_response()
}
