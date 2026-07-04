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
        AutorespondersTemplate, ComicsTemplate, CommandsTemplate, EconomyTemplate, EmojiStatsTemplate,
        GiveawaysTemplate, HighlightsTemplate, MusicTemplate, PollsTemplate, StatsTemplate, TagsTemplate,
        TempChannelsTemplate,
    },
    view::Viewer,
    AppState,
};

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
