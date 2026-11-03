"""Pure rules for the moderation switches, the disabled-command reply and sticky roles. Discord-free."""

from __future__ import annotations

from app.services.guild.level_message import render_level_up

__all__ = (
    'DISABLED_MESSAGE_LIMIT',
    'DISABLED_MESSAGE_VARIABLES',
    'MOD_SWITCHES',
    'REASON_REQUIRED_ACTIONS',
    'action_notice',
    'check_disabled_message',
    'reason_missing',
    'render_disabled_message',
    'sticky_to_restore',
)

#: Actions ``mod_require_reason`` applies to.
REASON_REQUIRED_ACTIONS = frozenset({'kick', 'ban', 'softban', 'tempban', 'warn', 'mute', 'tempmute'})

DISABLED_MESSAGE_LIMIT = 200

#: `moderation settings toggle` name -> ``guild_config`` column.
MOD_SWITCHES = {'dm': 'mod_dm_on_action', 'reason': 'mod_require_reason', 'reinvite': 'mod_reinvite_on_unban'}

#: placeholder -> what it becomes (shown beside the dashboard input).
DISABLED_MESSAGE_VARIABLES: dict[str, str] = {
    'command': 'the command that was invoked',
    'channel': 'a mention of the channel it was invoked in',
}

_PAST = {
    'kick': 'kicked from', 'ban': 'banned from', 'softban': 'soft-banned from', 'tempban': 'temporarily banned from',
    'mute': 'muted in', 'tempmute': 'temporarily muted in', 'warn': 'warned in',
}


def reason_missing(action: str, reason: str | None, required: bool) -> bool:
    """Whether ``action`` must be refused for lacking a reason under ``mod_require_reason``."""
    return required and action in REASON_REQUIRED_ACTIONS and not (reason and reason.strip())


def check_disabled_message(text: str | None) -> str | None:
    """The trimmed message to store (``None`` = silent). Raises ``ValueError`` when too long."""
    text = (text or '').strip()
    if len(text) > DISABLED_MESSAGE_LIMIT:
        raise ValueError(f'the message is at most {DISABLED_MESSAGE_LIMIT} characters')
    return text or None


def render_disabled_message(template: str, command: str, channel_mention: str) -> str:
    """Substitutes only ``{command}`` and ``{channel}``; anything else stays as typed."""
    return render_level_up(template, {'command': command, 'channel': channel_mention})


def action_notice(guild_name: str, action: str, reason: str | None) -> str:
    """The one-line DM telling a member what was done to them."""
    text = f'You were {_PAST.get(action, action)} **{guild_name}**.'
    return f'{text} Reason: {reason.strip()}' if reason and reason.strip() else text


def sticky_to_restore(stored: set[int], held: set[int], assignable: set[int]) -> list[int]:
    """Sticky role ids the member lacks and Percy can still hand out (deleted/too-high roles are skipped)."""
    return sorted((stored & assignable) - held)
