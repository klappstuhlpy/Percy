"""Versioned system-prompt templates, grouped by domain.

Prompts are versioned (``_V1`` suffixes) so the exact-match response cache keys on prompt
*content* implicitly — editing a prompt changes the cached key and avoids serving stale
decisions made under the old instruction. Keep prompts terse: the models are small and a
shorter, sharper instruction routes more reliably than a verbose one.

Treat any user-supplied text injected into a prompt as untrusted data, never as
instructions — never let it redefine the system prompt or the requested JSON shape.
"""

from __future__ import annotations

__all__ = ('ASSISTANT_SYSTEM', 'json_instruction')

#: Free-form conversational assistant (the ``?ask`` command). Not a structured call.
ASSISTANT_SYSTEM = (
    'You are Percy, a helpful and friendly Discord assistant. '
    'Answer concisely — a few short paragraphs at most, since replies are shown in a chat. '
    'Use Discord-flavoured markdown when helpful, and never claim to be able to take actions '
    'in the server (moderation, roles, etc.); you only chat.'
)


def json_instruction(shape: str) -> str:
    """Return a strict trailer appended to structured-call system prompts.

    ``shape`` describes the expected JSON object (keys and value types) in one line. The
    reminder keeps the small models from wrapping JSON in prose or code fences.
    """
    return (
        f'\n\nRespond with ONLY a single valid JSON object of the form: {shape}. '
        'No prose, no explanation, no markdown code fences.'
    )
