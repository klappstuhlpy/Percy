"""Custom-command signatures: ``<name:type> [name:type=default] [rest:str...] --flag:type --switch``.

Pure: parses the signature an admin typed into :class:`Param` objects and binds the tokens of an
invocation to them as raw strings. Turning those strings into members/roles/durations is the cog's
job (it owns the discord.py converters). Every :class:`SignatureError` message is meant to be shown
to the person who typed the signature or the invocation, so it says what to change.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = (
    'DEFAULTABLE_TYPES',
    'ENTITY_TYPES',
    'MAX_PARAMS',
    'MAX_SIGNATURE_LENGTH',
    'RESERVED_NAMES',
    'TYPES',
    'Param',
    'SignatureError',
    'bind',
    'format_signature',
    'parse_bool',
    'parse_signature',
)

#: The closed set of argument types.
TYPES: tuple[str, ...] = ('str', 'int', 'number', 'bool', 'member', 'user', 'role', 'channel', 'duration')
#: Types whose placeholder also has ``.id``/``.name``/``.mention``.
ENTITY_TYPES = frozenset({'member', 'user', 'role', 'channel'})
#: Types a ``[name:type=default]`` may carry a default for (an entity default would go stale).
DEFAULTABLE_TYPES = frozenset({'str', 'int', 'number', 'bool', 'duration'})
#: Names already taken by the built-in placeholders.
RESERVED_NAMES = frozenset({'author', 'channel', 'guild', 'args', 'uses', 'choice', 'random'})
MAX_PARAMS = 10
MAX_SIGNATURE_LENGTH = 500

Kind = Literal['required', 'optional', 'flag', 'switch']

_NAME_RE = re.compile(r'[a-z_][a-z0-9_]{0,31}')
_PART_RE = re.compile(r'\s*(?:<([^<>]*)>|\[([^\[\]]*)\]|--(\S+))')
_TRUE = frozenset({'yes', 'y', 'true', 't', '1', 'on', 'enable', 'enabled'})
_FALSE = frozenset({'no', 'n', 'false', 'f', '0', 'off', 'disable', 'disabled'})


class SignatureError(ValueError):
    """A signature or an invocation that does not fit it; the message is user-facing."""


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    type: str
    kind: Kind
    default: str | None = None
    greedy: bool = False

    @property
    def positional(self) -> bool:
        return self.kind in ('required', 'optional')

    def __str__(self) -> str:
        if self.kind == 'switch':
            return f'--{self.name}'
        if self.kind == 'flag':
            return f'--{self.name}:{self.type}'
        inner = f'{self.name}:{self.type}' + ('...' if self.greedy else '')
        if self.kind == 'required':
            return f'<{inner}>'
        return f'[{inner}' + (f'={self.default}' if self.default is not None else '') + ']'


def parse_bool(raw: str) -> bool:
    lowered = raw.strip().lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    raise SignatureError(f'`{raw}` is not yes or no.')


def _check_default(name: str, type_: str, default: str) -> None:
    if type_ not in DEFAULTABLE_TYPES:
        raise SignatureError(f'`{name}` is a {type_}, which cannot have a default; only str, int, number, bool and duration can.')
    try:
        if type_ == 'int':
            int(default)
        elif type_ == 'number':
            if not math.isfinite(float(default)):
                raise ValueError
        elif type_ == 'bool':
            parse_bool(default)
    except (ValueError, SignatureError):
        raise SignatureError(f'The default `{default}` of `{name}` is not a valid {type_}.') from None


def _name_and_type(spec: str, where: str) -> tuple[str, str]:
    name, _, type_ = spec.partition(':')
    name, type_ = name.strip().lower(), (type_.strip().lower() or 'str')
    if not _NAME_RE.fullmatch(name):
        raise SignatureError(
            f'`{where}`: names are 1-32 lowercase letters, digits or `_` and cannot start with a digit.'
        )
    if name in RESERVED_NAMES:
        raise SignatureError(f'`{name}` is a built-in placeholder; pick another argument name.')
    if type_ not in TYPES:
        raise SignatureError(f'Unknown type `{type_}` for `{name}`; use one of: {", ".join(TYPES)}.')
    return name, type_


def _parse_part(required: str | None, optional: str | None, flag: str | None) -> Param:
    if flag is not None:
        name, has_type, _ = flag.partition(':')
        if not has_type:
            name, _ = _name_and_type(name, f'--{flag}')
            return Param(name, 'bool', 'switch')
        name, type_ = _name_and_type(flag, f'--{flag}')
        return Param(name, type_, 'flag')

    spec = required if required is not None else optional
    assert spec is not None
    where = f'<{spec}>' if required is not None else f'[{spec}]'
    default: str | None = None
    if optional is not None and '=' in spec:
        spec, default = (s.strip() for s in spec.split('=', 1))
    greedy = spec.endswith('...')
    if greedy:
        spec = spec[:-3]
    name, type_ = _name_and_type(spec, where)
    if default is not None:
        _check_default(name, type_, default)
    return Param(name, type_, 'required' if required is not None else 'optional', default, greedy)


def parse_signature(text: str) -> tuple[Param, ...]:
    """Parse a signature string. An empty string is a command without arguments."""
    text = text.strip()
    if len(text) > MAX_SIGNATURE_LENGTH:
        raise SignatureError(f'A signature can be at most {MAX_SIGNATURE_LENGTH} characters.')
    params: list[Param] = []
    pos = 0
    while pos < len(text):
        match = _PART_RE.match(text, pos)
        if match is None:
            raise SignatureError(
                f'Could not read the signature at `{text[pos:pos + 20].strip()}`; '
                'use `<name:type>`, `[name:type]`, `[name:type=default]`, `[name:type...]`, `--flag:type` or `--flag`.'
            )
        params.append(_parse_part(*match.groups()))
        pos = match.end()
        while pos < len(text) and text[pos].isspace():
            pos += 1

    if len(params) > MAX_PARAMS:
        raise SignatureError(f'A command can take at most {MAX_PARAMS} arguments and flags.')
    seen: set[str] = set()
    positional = [p for p in params if p.positional]
    for param in params:
        if param.name in seen:
            raise SignatureError(f'`{param.name}` is used twice.')
        seen.add(param.name)
    for index, param in enumerate(positional):
        if param.greedy and index != len(positional) - 1:
            raise SignatureError(f'Only the last argument can take the rest (`...`), not `{param.name}`.')
        if param.kind == 'required' and index and positional[index - 1].kind == 'optional':
            raise SignatureError(f'The required argument `{param.name}` cannot come after an optional one.')
    return tuple(params)


def format_signature(params: Sequence[Param]) -> str:
    """The canonical form of a parsed signature (what gets stored and shown as usage)."""
    return ' '.join(str(p) for p in params)


def bind(params: Sequence[Param], tokens: Sequence[str]) -> dict[str, str | bool | None]:
    """Bind invocation tokens to ``params``.

    Returns raw strings (``None`` for an absent optional without a default, a bool for switches).
    Extra positional tokens are ignored; they stay reachable through ``{args}``/``{0}``..``{9}``.
    """
    flags = {p.name: p for p in params if not p.positional}
    values: dict[str, str | bool | None] = {p.name: (False if p.kind == 'switch' else None) for p in flags.values()}
    rest: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        param = flags.get(token[2:].lower()) if token.startswith('--') else None
        if param is None:
            rest.append(token)
        elif param.kind == 'switch':
            values[param.name] = True
        else:
            if index + 1 >= len(tokens):
                raise SignatureError(f'`--{param.name}` needs a {param.type} after it.')
            index += 1
            values[param.name] = tokens[index]
        index += 1

    for position, param in enumerate(p for p in params if p.positional):
        single = rest[position] if position < len(rest) else None
        taken = (' '.join(rest[position:]) or None) if param.greedy else single
        if taken is None and param.kind == 'required':
            raise SignatureError(f'Missing the required argument `{param}`.')
        values[param.name] = taken if taken is not None else param.default
    return values
