"""Personal-data deletion contract: every column holding a Discord *user* id is either erased or justified.

``DELETED`` is the single source of truth ``UsersRepository.delete_personal_data`` iterates; ``RETAINED`` maps
every other user-id column to a one-line reason. ``tests/test_personal_data_contract.py`` parses
``migrations/*.sql`` and fails when a migration adds a user-id column that is in neither, so a new feature has to
decide at review time. Keys are ``table.column``. A reason starting ``GAP:`` is a pre-existing table the routine
never erased (kept so this contract does not silently change deletion behaviour); fixing a GAP means moving it to
``DELETED``.
"""

from __future__ import annotations

__all__ = ('DELETED', 'RETAINED')

#: ``table.column`` erased by ``DELETE FROM table WHERE column = $1``.
DELETED: tuple[str, ...] = (
    'presence_history.uuid',
    'avatar_history.uuid',
    'item_history.uuid',
    'trivia_scores.user_id',  # purely personal leaderboard score
)

_SAFETY = 'server-safety record; deleting it would let a member escape moderation'
_CONTENT = 'authorship of shared guild content (same treatment as tags: kept, not anonymised)'

RETAINED: dict[str, str] = {
    # -- moderation / safety
    'mod_cases.target_id': _SAFETY,
    'mod_cases.moderator_id': _SAFETY + ' (audit trail of who acted)',
    'member_restrictions.user_id': _SAFETY + ' (sticky roles, voice bans, nickname freeze, channel mutes)',
    'command_rules.target_id': "polymorphic: a user id only when target_type='user' (else role/channel/category/guild "
                               'id); an admin-authored rule, deleting it would silently lift a deny',
    'action_requests.requester_id': _SAFETY + ' (moderation request audit)',
    'action_requests.target_id': _SAFETY + ' (moderation request audit)',
    'action_requests.decided_by': _SAFETY + ' (moderation request audit)',
    'guild_notifications.meta': 'JSON-embedded ids (e.g. reports: reporter_id/target_id); a report is a moderation '
                                'record kept for server safety and cannot be matched by a column scan',
    'timers.metadata': 'JSON-embedded ids (reminders, mutes, bans); active mutes/bans are server-safety state',
    'guild_sentinel_members.user_id': _SAFETY + ' (verification state)',
    # -- shared guild content
    'tags.owner_id': _CONTENT,
    'tag_lookup.owner_id': _CONTENT,
    'tag_transfers.from_id': 'pending 24 h tag transfer; expires on its own',
    'custom_commands.owner_id': _CONTENT,
    'command_aliases.owner_id': _CONTENT,
    'trivia_lists.author_id': _CONTENT,
    'autoresponders.created_by': _CONTENT,
    'guild_templates.author_id': _CONTENT,
    'starboard_entries.author_id': 'record of a public starred message',
    'giveaways.author_id': 'guild event in progress; the host is part of its public record',
    'giveaways.entries': 'guild event integrity; entry arrays are removed when the giveaway ends',
    'polls.entries': 'guild event integrity; votes are part of the poll result',
    'poll_entry.user_id': 'composite type behind polls.entries (see above)',
    # -- GAP: pre-existing personal tables the routine never erased (behaviour unchanged by this contract)
    'user_settings.id': 'GAP: settings row; deleting it resets the tracking opt-in, so kept for now',
    'commands.author_id': 'GAP: command-usage log',
    'levels.user_id': 'GAP: per-guild XP',
    'economy.user_id': 'GAP: per-guild wallet',
    'economy_inventory.user_id': 'GAP: economy state',
    'economy_dailies.user_id': 'GAP: economy state',
    'economy_lottery_entries.user_id': 'GAP: economy state',
    'economy_boosts.user_id': 'GAP: economy state',
    'economy_jobs.user_id': 'GAP: economy state',
    'economy_prestige.user_id': 'GAP: economy state',
    'economy_achievements.user_id': 'GAP: economy state',
    'economy_pets.user_id': 'GAP: economy state',
    'economy_quests.user_id': 'GAP: economy state',
    'economy_passive.user_id': 'GAP: economy state',
    'economy_discoveries.user_id': 'GAP: economy state',
    'economy_profiles.user_id': 'GAP: economy state',
    'game_stats.user_id': 'GAP: game statistics',
    'game_bets.user_id': 'GAP: game bet history',
    'vote_rewards.user_id': 'GAP: vote boosts',
    'playlist.user_id': 'GAP: music playlists',
    'music_plays.user_id': 'GAP: music play history',
    'user_notes.owner_id': 'GAP: personal notes',
    'highlights.user_id': 'GAP: highlight keywords',
    'watch_progress.user_id': 'GAP: watch progress',
    'watch_prefs.user_id': 'GAP: watchlist prefs',
    'release_subscriptions.user_id': 'GAP: release subscriptions',
    'release_deliveries.user_id': 'GAP: release delivery claims',
    'release_progress.user_id': 'GAP: read/watched progress',
    'notification_reads.user_id': 'GAP: notification read state',
}
