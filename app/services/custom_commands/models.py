"""Custom commands and aliases: the stored shapes, the caps and the rules both the cog and the API apply."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .aliases import first_word
from .signature import SignatureError, format_signature, parse_signature

if TYPE_CHECKING:
    import datetime
    from collections.abc import Collection, Iterable, Mapping

__all__ = (
    'ADMINISTRATOR',
    'EDITABLE_FIELDS',
    'MAX_ALIASES',
    'MAX_ALIASES_PER_COMMAND',
    'MAX_COMMANDS',
    'MAX_COOLDOWN_PER',
    'MAX_COOLDOWN_RATE',
    'MAX_EXPANSION_LENGTH',
    'MAX_RESPONSES',
    'MAX_RESPONSE_LENGTH',
    'CommandAlias',
    'CustomCommand',
    'CustomCommandIndex',
    'alias_problem',
    'check_name',
    'check_responses',
    'gate_passes',
    'name_conflict',
    'parse_cooldown',
    'validate_command',
)

MAX_COMMANDS = 200
MAX_ALIASES = 100
MAX_RESPONSES = 20
MAX_RESPONSE_LENGTH = 2000
MAX_EXPANSION_LENGTH = 2000
MAX_ALIASES_PER_COMMAND = 10
MAX_COOLDOWN_RATE = 100
MAX_COOLDOWN_PER = 86400.0
#: discord.Permissions.administrator's bit (kept here so this module stays Discord-free).
ADMINISTRATOR = 1 << 3

#: The columns a create/edit sets (also the repository's column whitelist).
EDITABLE_FIELDS = (
    'name', 'aliases', 'signature', 'responses', 'required_permissions', 'allowed_role_ids',
    'cooldown_rate', 'cooldown_per', 'delete_invocation',
)

_NAME_RE = re.compile(r'[a-z0-9_-]{1,32}')


@dataclass(frozen=True, slots=True)
class CustomCommand:
    id: int
    guild_id: int
    name: str
    aliases: tuple[str, ...] = ()
    signature: str = ''
    responses: tuple[str, ...] = ()
    required_permissions: int | None = None
    allowed_role_ids: tuple[int, ...] = ()
    cooldown_rate: int | None = None
    cooldown_per: float | None = None
    delete_invocation: bool = False
    owner_id: int | None = None
    uses: int = 0
    created_at: datetime.datetime | None = None

    def editable(self) -> dict[str, Any]:
        """The :data:`EDITABLE_FIELDS` as a dict, to merge an edit onto before :func:`validate_command`."""
        return {k: getattr(self, k) for k in EDITABLE_FIELDS}


@dataclass(frozen=True, slots=True)
class CommandAlias:
    guild_id: int
    alias: str
    expansion: str
    owner_id: int | None = None


@dataclass(slots=True)
class CustomCommandIndex:
    """One guild's custom commands and aliases, keyed for dispatch."""

    commands: tuple[CustomCommand, ...] = ()
    aliases: dict[str, CommandAlias] = field(default_factory=dict)
    lookup: dict[str, CustomCommand] = field(default_factory=dict)

    @classmethod
    def build(cls, commands: Iterable[CustomCommand], aliases: Iterable[CommandAlias]) -> CustomCommandIndex:
        commands = tuple(commands)
        lookup: dict[str, CustomCommand] = {}
        for cc in commands:
            for alias in cc.aliases:
                lookup.setdefault(alias, cc)
        lookup.update({cc.name: cc for cc in commands})  # a name beats another command's alias
        return cls(commands, {a.alias: a for a in aliases}, lookup)


def check_name(name: str) -> str | None:
    """Why ``name`` is not a valid custom command/alias name, or ``None``."""
    if not _NAME_RE.fullmatch(name):
        return f'`{name[:40]}` is not a valid name: use 1-32 lowercase letters, digits, `_` or `-`.'
    return None


def name_conflict(
    name: str,
    index: CustomCommandIndex,
    real_names: Collection[str],
    *,
    exclude_command_id: int | None = None,
    exclude_alias: str | None = None,
) -> str | None:
    """Why ``name`` cannot be used for a new command/alias in this guild, or ``None``.

    ``real_names`` is every built-in command name and alias: those always win.
    """
    if name in real_names:
        return f'`{name}` is already a built-in command.'
    owner = index.lookup.get(name)
    if owner is not None and owner.id != exclude_command_id:
        return f'`{name}` is already used by the custom command `{owner.name}`.'
    if name in index.aliases and name != exclude_alias:
        return f'`{name}` is already an alias.'
    return None


def check_responses(responses: Collection[str]) -> str | None:
    if not responses:
        return 'A custom command needs at least one response.'
    if len(responses) > MAX_RESPONSES:
        return f'A custom command can have at most {MAX_RESPONSES} responses.'
    if any(not r.strip() for r in responses):
        return 'A response cannot be empty.'
    if any(len(r) > MAX_RESPONSE_LENGTH for r in responses):
        return f'A response can be at most {MAX_RESPONSE_LENGTH} characters.'
    return None


def parse_cooldown(raw: str) -> tuple[int, float]:
    """``"3/10"`` -> 3 uses per 10 seconds. Raises :class:`ValueError` with a user-facing message."""
    rate, sep, per = raw.strip().partition('/')
    try:
        if not sep:
            raise ValueError
        rate_i, per_f = int(rate), float(per)
    except ValueError:
        raise ValueError(f'`{raw}` is not a cooldown; write uses/seconds, e.g. `3/10`.') from None
    if not (1 <= rate_i <= MAX_COOLDOWN_RATE and 1 <= per_f <= MAX_COOLDOWN_PER):
        raise ValueError(f'A cooldown allows 1-{MAX_COOLDOWN_RATE} uses per 1-{int(MAX_COOLDOWN_PER)} seconds.')
    return rate_i, per_f


def gate_passes(
    permissions: int,
    role_ids: Collection[int],
    required_permissions: int | None,
    allowed_role_ids: Collection[int],
) -> bool:
    """Whether a member passes a custom command's own gate.

    Administrator always passes. With both a permission set and roles, either one is enough
    (a listed role bypasses the permission requirement, as command overrides do).
    """
    if not required_permissions and not allowed_role_ids:
        return True
    if permissions & ADMINISTRATOR:
        return True
    if required_permissions and permissions & required_permissions == required_permissions:
        return True
    return bool(allowed_role_ids) and any(r in allowed_role_ids for r in role_ids)


def validate_command(
    values: Mapping[str, Any],
    index: CustomCommandIndex,
    real_names: Collection[str],
    *,
    command_id: int | None = None,
) -> dict[str, Any]:
    """Check a custom command's full set of columns; return them normalised (canonical signature,
    lowercased de-duplicated aliases). Raises :class:`ValueError` with a user-facing message.

    The cog and the API both merge an edit onto the stored row first, then call this.
    """
    name = values['name']
    aliases = tuple(dict.fromkeys(a.lower() for a in values.get('aliases', ()) if a != name))
    if len(aliases) > MAX_ALIASES_PER_COMMAND:
        raise ValueError(f'A custom command can have at most {MAX_ALIASES_PER_COMMAND} aliases.')
    for candidate in (name, *aliases):
        problem = check_name(candidate) or name_conflict(candidate, index, real_names, exclude_command_id=command_id)
        if problem:
            raise ValueError(problem)
    responses = tuple(values.get('responses', ()))
    if problem := check_responses(responses):
        raise ValueError(problem)
    try:
        signature = format_signature(parse_signature(values.get('signature') or ''))
    except SignatureError as exc:
        raise ValueError(str(exc)) from None
    rate, per = values.get('cooldown_rate'), values.get('cooldown_per')
    if (rate is None) != (per is None):
        raise ValueError('A cooldown needs both a rate and a period.')
    if rate is not None and per is not None:
        parse_cooldown(f'{rate}/{per}')
    required = values.get('required_permissions')
    if required is not None and required < 0:
        raise ValueError('required_permissions must be a permission bitmask.')
    return {
        'name': name,
        'aliases': list(aliases),
        'signature': signature,
        'responses': list(responses),
        'required_permissions': required or None,
        'allowed_role_ids': list(dict.fromkeys(values.get('allowed_role_ids', ()))),
        'cooldown_rate': rate,
        'cooldown_per': per,
        'delete_invocation': bool(values.get('delete_invocation', False)),
    }


def alias_problem(name: str, expansion: str, index: CustomCommandIndex, real_names: Collection[str]) -> str | None:
    """Why the alias ``name`` -> ``expansion`` cannot be saved, or ``None``.

    The expansion must start with a built-in command (``real_names``: top-level names and aliases)
    or one of the guild's custom commands, never with another alias.
    """
    problem = check_name(name) or name_conflict(name, index, real_names, exclude_alias=name)
    if problem:
        return problem
    expansion = expansion.strip()
    if not expansion or len(expansion) > MAX_EXPANSION_LENGTH:
        return f'An expansion needs 1-{MAX_EXPANSION_LENGTH} characters.'
    target = first_word(expansion)
    if target in index.aliases:
        return f'`{target}` is an alias; an alias cannot point to another alias.'
    if target not in real_names and target not in index.lookup:
        return f'`{target}` is not a command.'
    return None
