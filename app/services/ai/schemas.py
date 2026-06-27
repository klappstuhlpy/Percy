"""Structured-output schema infrastructure for AI calls.

Every structured (JSON) AI call validates the model's output against a schema before it
reaches a cog, so unvalidated/hallucinated output never drives an action. A schema is any
class exposing a ``from_payload`` classmethod that maps a decoded JSON object onto a
typed result and raises :class:`SchemaError` (or a stdlib ``ValueError``/``KeyError``/
``TypeError``) on anything it cannot honour.

This module holds only the *infrastructure* (the protocol + field helpers). Concrete
per-domain schemas (route decisions, giveaway specs, moderation verdicts, …) live with
their feature in later phases.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, TypeVar, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ('Parsable', 'SchemaError', 'require_bool', 'require_float', 'require_int', 'require_str')

T = TypeVar('T')


class SchemaError(ValueError):
    """Raised when decoded AI output does not satisfy a schema's contract."""


@runtime_checkable
class Parsable(Protocol):
    """A structured-result type that can be built from a decoded JSON object.

    Implementations validate as they go and raise :class:`SchemaError` on bad input.
    """

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> Any:
        """Validate ``payload`` and return an instance, raising on invalid input."""
        ...


# -- field helpers ----------------------------------------------------------------
#
# Small, defensive extractors so concrete schemas read declaratively. Each raises
# SchemaError (a ValueError) on a missing/mistyped field, which AIService treats as a
# parse failure and degrades from.


def require_str(payload: Mapping[str, Any], key: str, *, allow_empty: bool = False) -> str:
    """Return ``payload[key]`` as a non-empty string, or raise :class:`SchemaError`."""
    value = payload.get(key)
    if not isinstance(value, str):
        raise SchemaError(f'expected string for {key!r}, got {type(value).__name__}')
    if not allow_empty and not value.strip():
        raise SchemaError(f'expected non-empty string for {key!r}')
    return value


def require_int(payload: Mapping[str, Any], key: str) -> int:
    """Return ``payload[key]`` coerced to ``int``, or raise :class:`SchemaError`."""
    value = payload.get(key)
    # Reject bools explicitly: in Python ``isinstance(True, int)`` is True.
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SchemaError(f'expected int for {key!r}, got {type(value).__name__}')
    try:
        return int(value)
    except (ValueError, TypeError) as exc:
        raise SchemaError(f'invalid int for {key!r}: {value!r}') from exc


def require_float(payload: Mapping[str, Any], key: str) -> float:
    """Return ``payload[key]`` coerced to ``float``, or raise :class:`SchemaError`."""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise SchemaError(f'expected number for {key!r}, got {type(value).__name__}')
    try:
        return float(value)
    except (ValueError, TypeError) as exc:
        raise SchemaError(f'invalid number for {key!r}: {value!r}') from exc


def require_bool(payload: Mapping[str, Any], key: str) -> bool:
    """Return ``payload[key]`` as a ``bool``, or raise :class:`SchemaError`."""
    value = payload.get(key)
    if not isinstance(value, bool):
        raise SchemaError(f'expected bool for {key!r}, got {type(value).__name__}')
    return value
