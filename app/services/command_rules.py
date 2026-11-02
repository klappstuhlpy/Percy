"""Pure resolver for per-guild command allow/deny rules (``command_rules``, no Discord imports).

Precedence: subject specificity first (exact command > parent groups, nearest first > ``cog:<Cog>`` > ``*``);
within one subject: user > role (the highest role holding a rule wins) > channel > category > guild.
The first matching rule decides; no match means ``allowed is None`` and the caller falls through to the
existing checks (enable/disable, ignore list, overrides). Owner bypass is the caller's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = ('TARGET_TYPES', 'Decision', 'Invocation', 'Rule', 'resolve', 'subjects_for')

TARGET_TYPES = ('user', 'role', 'channel', 'category', 'guild')


@dataclass(frozen=True)
class Rule:
    subject: str
    target_type: str
    target_id: int
    allow: bool


@dataclass(frozen=True)
class Invocation:
    """The facts a rule is matched against."""

    qualified_name: str
    user_id: int
    guild_id: int
    cog_name: str | None = None
    role_ids: Sequence[int] = ()  # top-down by position (highest role first)
    channel_id: int | None = None
    category_id: int | None = None


@dataclass(frozen=True)
class Decision:
    allowed: bool | None
    rule: Rule | None = None
    reason: str = 'No rule applies.'


def subjects_for(qualified_name: str, cog_name: str | None) -> list[str]:
    """Subjects consulted for a command, most specific first."""
    words = qualified_name.split()
    subjects = [' '.join(words[:i]) for i in range(len(words), 0, -1)]
    if cog_name:
        subjects.append(f'cog:{cog_name}')
    subjects.append('*')
    return subjects


def _targets(inv: Invocation) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = [('user', inv.user_id)]
    out.extend(('role', role_id) for role_id in inv.role_ids)
    if inv.channel_id is not None:
        out.append(('channel', inv.channel_id))
    if inv.category_id is not None:
        out.append(('category', inv.category_id))
    out.append(('guild', inv.guild_id))
    return out


def describe_rule(rule: Rule) -> str:
    return f'{"allow" if rule.allow else "deny"} `{rule.subject}` for {rule.target_type} {rule.target_id}'


def resolve(rules: Iterable[Rule], inv: Invocation) -> Decision:
    index = {(r.subject, r.target_type, r.target_id): r for r in rules}
    targets = _targets(inv)
    for subject in subjects_for(inv.qualified_name, inv.cog_name):
        for target_type, target_id in targets:
            rule = index.get((subject, target_type, target_id))
            if rule is not None:
                return Decision(rule.allow, rule, f'Rule: {describe_rule(rule)}.')
    return Decision(None)
