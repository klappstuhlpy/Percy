"""Can Percy actually do what a server configured it to do?

A setting saved on the dashboard says nothing about whether Percy may post in that channel or
assign that role -- those failures only ever surfaced in Discord, after the fact. This module
turns facts about a server (gathered by the internal API from live Discord objects) into one
status per feature plus plain-language problems, each naming the channel or role and the exact
permission involved.

Pure and Discord-free: the caller resolves channels/roles into the small dataclasses below.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

__all__ = ('ChannelState', 'FeatureFacts', 'RoleState', 'assess', 'humanize_permission', 'summarize')

Status = Literal['ok', 'attention', 'off']


@dataclass(frozen=True, slots=True)
class ChannelState:
    """A configured channel: ``name`` is ``None`` when the channel no longer exists."""

    purpose: str
    id: int
    name: str | None
    missing: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RoleState:
    """A configured role: ``name`` is ``None`` when deleted; ``assignable`` = below Percy's top role."""

    purpose: str
    id: int
    name: str | None
    assignable: bool = True


@dataclass(slots=True)
class FeatureFacts:
    key: str
    label: str
    enabled: bool
    channels: list[ChannelState] = field(default_factory=list)
    roles: list[RoleState] = field(default_factory=list)
    guild_missing: tuple[str, ...] = ()
    #: Already-phrased problems the caller detected (e.g. "the logging webhook was deleted").
    notes: list[str] = field(default_factory=list)


_PERMISSION_NAMES = {'view_channel': 'View Channel', 'manage_webhooks': 'Manage Webhooks', 'view_audit_log': 'View Audit Log'}


def humanize_permission(name: str) -> str:
    """``send_messages`` -> ``Send Messages`` (the label Discord's own settings use)."""
    return _PERMISSION_NAMES.get(name) or name.replace('_', ' ').title()


def _join(perms: tuple[str, ...]) -> str:
    return ', '.join(humanize_permission(p) for p in perms)


def assess(facts: FeatureFacts) -> dict:
    """One feature's status and problems. A feature that is off reports nothing."""
    problems: list[str] = []
    if facts.enabled:
        problems.extend(facts.notes)
        if facts.guild_missing:
            problems.append(f'Percy is missing the server permission {_join(facts.guild_missing)}.')
        for ch in facts.channels:
            if ch.name is None:
                problems.append(f'The {ch.purpose} channel was deleted. Pick a new one.')
            elif ch.missing:
                problems.append(f'Percy is missing {_join(ch.missing)} in #{ch.name} ({ch.purpose}).')
        for role in facts.roles:
            if role.name is None:
                problems.append(f'The {role.purpose} role was deleted. Pick a new one.')
            elif not role.assignable:
                problems.append(
                    f"@{role.name} ({role.purpose}) is above Percy's highest role, so Percy can't give it out. "
                    "Move Percy's role above it in Server Settings → Roles."
                )

    status: Status = 'off' if not facts.enabled else ('attention' if problems else 'ok')
    return {'key': facts.key, 'label': facts.label, 'status': status, 'problems': problems}


def summarize(features: list[FeatureFacts]) -> dict:
    """The whole server: every feature (attention first) and how many need attention."""
    order = {'attention': 0, 'ok': 1, 'off': 2}
    items = sorted((assess(f) for f in features), key=lambda item: order[item['status']])
    return {'features': items, 'attention': sum(item['status'] == 'attention' for item in items)}
