"""Plain-language text for the interactive custom-command manager (pure: no discord import).

The manager shows a signature as a list of sentences rather than ``<who:member> [n:int=2]``, hints
which placeholders a response can use, and fills a response with sample values for its Test button.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .signature import ENTITY_TYPES

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from .signature import Param

__all__ = (
    'TYPE_WORDS',
    'describe_cooldown',
    'describe_param',
    'describe_signature',
    'param_placeholders',
    'placeholder_help',
    'placeholder_hint',
    'sample_values',
)

#: What each argument type accepts, in words.
TYPE_WORDS: dict[str, str] = {
    'str': 'text',
    'int': 'a whole number',
    'number': 'a number',
    'bool': 'yes or no',
    'member': 'a server member',
    'user': 'any user',
    'role': 'a role',
    'channel': 'a channel',
    'duration': 'a duration like `10m`',
}

#: Text inputs cap their placeholder at 100 characters.
HINT_LIMIT = 100

_SAMPLES: dict[str, str] = {
    'str': 'example', 'int': '3', 'number': '2.5', 'bool': 'yes', 'duration': '10 minutes',
}


def describe_param(param: Param) -> str:
    """One argument as a sentence, e.g. ``"`n` - a whole number, optional (default `2`)"``."""
    if param.kind == 'switch':
        return f'`--{param.name}` - an on/off switch, on when `--{param.name}` is typed'
    what = TYPE_WORDS.get(param.type, param.type)
    if param.kind == 'flag':
        return f'`--{param.name}` - {what}, optional, typed as `--{param.name} value`'
    parts = [what + (' (takes the rest of the message)' if param.greedy else '')]
    parts.append('required' if param.kind == 'required' else 'optional')
    if param.default is not None:
        parts[-1] += f' (default `{param.default}`)'
    return f'`{param.name}` - ' + ', '.join(parts)


def describe_signature(params: Sequence[Param]) -> list[str]:
    """Every argument as a sentence; empty for a command without arguments."""
    return [describe_param(p) for p in params]


def param_placeholders(params: Sequence[Param]) -> list[str]:
    """The placeholders a signature adds: ``{name}`` and, for entities, ``{name.mention}``/``.id``/``.name``."""
    out: list[str] = []
    for param in params:
        out.append(f'{{{param.name}}}')
        if param.type in ENTITY_TYPES:
            out += [f'{{{param.name}.mention}}', f'{{{param.name}.id}}', f'{{{param.name}.name}}']
    return out


def placeholder_hint(params: Sequence[Param]) -> str:
    """A text-input placeholder (at most 100 characters) naming the most useful placeholders first."""
    words = [f'{{{p.name}}}' for p in params] + ['{author}', '{args}', '{choice:a|b}', '{random:1-10}', '{channel}']
    hint = 'Use'
    for word in words:
        if len(hint) + 1 + len(word) > HINT_LIMIT:
            break
        hint += f' {word}'
    return hint


def placeholder_help(params: Sequence[Param]) -> str:
    """Every placeholder a response of this signature can use, grouped, as markdown."""
    lines = []
    if own := param_placeholders(params):
        lines.append('**Your arguments:** ' + ' '.join(f'`{p}`' for p in own))
    lines += [
        '**Always available:** `{author}` `{author.mention}` `{channel}` `{guild}` `{args}` `{0}`..`{9}` `{uses}`',
        '**Random:** `{choice:hug|pat|wave}` picks one, `{random:1-100}` a number',
    ]
    return '\n'.join(lines)


def describe_cooldown(rate: int | None, per: float | None) -> str:
    if not rate or not per:
        return 'none'
    uses = 'use' if rate == 1 else 'uses'
    return f'{rate} {uses} per {per:g} seconds, per member'


def sample_values(params: Sequence[Param], member: Mapping[str, str]) -> tuple[dict[str, str], list[str]]:
    """Sample placeholder values and invocation words for a dry run of a response.

    ``member`` supplies the ``''``/``id``/``name``/``mention`` values used for member and user
    arguments (the person testing). Optional arguments with a default show that default.
    """
    values: dict[str, str] = {}
    tokens: list[str] = []
    for param in params:
        if param.kind == 'switch':
            values[param.name] = 'true'
            tokens.append(f'--{param.name}')
            continue
        if param.type in ('member', 'user'):
            entity = {'': member.get('', ''), 'id': member.get('id', ''), 'name': member.get('name', '')}
            entity['mention'] = member.get('mention', '')
        elif param.type == 'role':
            entity = {'': 'Sample Role', 'id': '0', 'name': 'Sample Role', 'mention': '@Sample Role'}
        elif param.type == 'channel':
            entity = {'': 'sample-channel', 'id': '0', 'name': 'sample-channel', 'mention': '#sample-channel'}
        else:
            entity = {'': param.default if param.default is not None else _SAMPLES.get(param.type, 'example')}
        values[param.name] = entity['']
        values |= {f'{param.name}.{k}': v for k, v in entity.items() if k}
        word = entity.get('mention') or entity['']
        tokens += [f'--{param.name}', word] if param.kind == 'flag' else [word]
    return values, tokens
