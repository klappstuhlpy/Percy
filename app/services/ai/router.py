"""Natural-language command router.

When a user addresses Percy with the prefix but their text matches no command (the seam
in ``Bot.process_commands``), this maps the message to the most likely command + arguments
so the bot can offer a one-click "run this" confirmation. Pure and Discord-free: it takes a
catalogue of commands (built by the bot) and an :class:`~app.services.ai.AIService`, and
returns a validated :class:`RouteDecision` or ``None`` (caller falls back to fuzzy suggest).

The model never executes anything — it only proposes. Execution stays behind the user's
explicit confirmation and the command's own checks/permissions.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.ai.schemas import SchemaError
from app.services.ai.service import ModelTier

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from app.services.ai.service import AIService

__all__ = ('CommandRouter', 'RouteCommand', 'RouteDecision', 'build_route_system_prompt')

#: Below this model-reported confidence the route is discarded and the caller falls back.
DEFAULT_MIN_CONFIDENCE = 0.6

#: Sentinels the model may emit for "no command" — normalised to ``None``.
_NULL_TOKENS = frozenset({'', 'none', 'null', 'no command', 'unknown'})


@dataclass(slots=True)
class RouteCommand:
    """One command in the catalogue handed to the router (qualified name + short help)."""

    name: str
    description: str


@dataclass(slots=True)
class RouteDecision:
    """The router's structured choice: which command, with what args, how confident."""

    command: str | None
    args: str
    confidence: float

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> RouteDecision:
        raw = payload.get('command')
        if raw is not None and not isinstance(raw, str):
            raise SchemaError(f'expected string/null for "command", got {type(raw).__name__}')
        command: str | None = None
        if isinstance(raw, str) and raw.strip().lower() not in _NULL_TOKENS:
            command = raw.strip()

        args = payload.get('args', '')
        if not isinstance(args, str):
            args = ''

        conf_raw = payload.get('confidence', 0.0)
        try:
            confidence = float(conf_raw)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))

        return cls(command=command, args=args.strip(), confidence=confidence)


def build_route_system_prompt(catalogue: Sequence[RouteCommand]) -> str:
    """Build the router system prompt listing the available commands."""
    lines = [f'- {c.name}: {c.description}' if c.description else f'- {c.name}' for c in catalogue]
    listing = '\n'.join(lines)
    return (
        "You are the command router for Percy, a Discord bot. Given a user's message, choose the "
        'single best-matching command from the list below and extract the arguments to pass it. '
        'Respond with ONLY a JSON object of the form '
        '{"command": <exact command name from the list, or null>, '
        '"args": <argument string, may be empty>, "confidence": <number 0.0-1.0>}. '
        'Use null with confidence 0 when no command clearly fits. Never invent a command name '
        'that is not in the list.\n\n'
        f'Available commands:\n{listing}'
    )


class CommandRouter:
    """Maps natural-language text to a command via :class:`AIService`. Discord-free."""

    def __init__(self, ai: AIService, *, min_confidence: float = DEFAULT_MIN_CONFIDENCE) -> None:
        self._ai = ai
        self._min_confidence = min_confidence

    async def route(self, text: str, catalogue: Sequence[RouteCommand]) -> RouteDecision | None:
        """Return a confident, in-catalogue route for ``text``, or ``None`` to fall back.

        ``None`` is returned for empty input, an unavailable/failed model, an unknown or
        hallucinated command name, or sub-threshold confidence.
        """
        text = text.strip()
        if not text or not catalogue:
            return None

        names = {c.name for c in catalogue}
        system = build_route_system_prompt(catalogue)
        decision = await self._ai.parse(text, schema=RouteDecision, system=system, tier=ModelTier.FAST)

        if decision is None or decision.command is None:
            return None
        if decision.command not in names or decision.confidence < self._min_confidence:
            return None
        return decision
