"""Data access for per-guild command allow/deny rules (``migrations/V12``)."""

from __future__ import annotations

from app.database.repositories.base import BaseRepository
from app.services.command_rules import Rule

__all__ = ('CommandRulesRepository',)


class CommandRulesRepository(BaseRepository):
    async def list_rules(self, guild_id: int) -> tuple[Rule, ...]:
        rows = await self.fetch(
            'SELECT subject, target_type, target_id, allow FROM command_rules WHERE guild_id = $1 '
            'ORDER BY subject, target_type, target_id;',
            guild_id,
        )
        return tuple(Rule(r['subject'], r['target_type'], r['target_id'], r['allow']) for r in rows)

    async def set_rule(self, guild_id: int, subject: str, target_type: str, target_id: int, allow: bool) -> None:
        await self.execute(
            'INSERT INTO command_rules (guild_id, subject, target_type, target_id, allow) '
            'VALUES ($1, $2, $3, $4, $5) '
            'ON CONFLICT (guild_id, subject, target_type, target_id) DO UPDATE SET allow = EXCLUDED.allow;',
            guild_id, subject, target_type, target_id, allow,
        )
        self.invalidate_cache('command_rules_changed', guild_id)

    async def remove_rule(self, guild_id: int, subject: str, target_type: str, target_id: int) -> bool:
        status = await self.execute(
            'DELETE FROM command_rules WHERE guild_id = $1 AND subject = $2 AND target_type = $3 AND target_id = $4;',
            guild_id, subject, target_type, target_id,
        )
        self.invalidate_cache('command_rules_changed', guild_id)
        return not status.endswith('0')
