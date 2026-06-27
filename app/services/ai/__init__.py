"""AI service layer: Percy's provider-agnostic inference facade.

Public surface:

* :class:`AIService` — reached as ``self.bot.ai``; the only entry point cogs use.
* :class:`ModelTier` — fast/balanced/smart model selection.
* :class:`AIHealthReport` — health snapshot for the stats/dashboard surface.
* schema helpers — :class:`Parsable`, :class:`SchemaError`, ``require_*`` field extractors.
* prompts — :data:`ASSISTANT_SYSTEM`, :func:`json_instruction`.

Pure and Discord-free: testable with a fake client (see ``tests/test_ai_service.py``).
"""

from app.services.ai.moderation import MODERATION_CATEGORIES, ModerationAssessor, ModerationVerdict
from app.services.ai.prompts import ASSISTANT_SYSTEM, json_instruction
from app.services.ai.router import CommandRouter, RouteCommand, RouteDecision, build_route_system_prompt
from app.services.ai.schemas import (
    Parsable,
    SchemaError,
    require_bool,
    require_float,
    require_int,
    require_str,
)
from app.services.ai.service import AIHealthReport, AIService, ModelTier

__all__ = (
    'ASSISTANT_SYSTEM',
    'MODERATION_CATEGORIES',
    'AIHealthReport',
    'AIService',
    'CommandRouter',
    'ModelTier',
    'ModerationAssessor',
    'ModerationVerdict',
    'Parsable',
    'RouteCommand',
    'RouteDecision',
    'SchemaError',
    'build_route_system_prompt',
    'json_instruction',
    'require_bool',
    'require_float',
    'require_int',
    'require_str',
)
