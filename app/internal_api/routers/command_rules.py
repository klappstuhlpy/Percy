"""Command allow/deny rules (``command_rules``): who may run which command, where.

Writes go through ``bot.db.command_rules`` whose repository busts the cached rule list, so the
Config cog's ``bot_check`` sees a change on the next invocation. Resolution order is documented in
``app/services/command_rules.py``.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.core.permissions import custom_command_name, normalize_rule_subject
from app.services.command_rules import TARGET_TYPES, canonical_target_type

from ..dependencies import BotDep, GuildDep, verify_token
from ..helpers import parse_id

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Command rules"], dependencies=[Depends(verify_token)])


class CommandRule(BaseModel):
    subject: str
    target_type: str
    target_id: str
    allow: bool


class CommandRulesResponse(BaseModel):
    rules: list[CommandRule]


class PutCommandRuleBody(BaseModel):
    subject: str
    target_type: str
    target_id: str
    allow: bool


def _target(
    bot, guild, subject: str, target_type: str, target_id: object, *, strict: bool = True, custom: str | None = None
) -> tuple[str, str, int]:
    canonical = normalize_rule_subject(bot, subject) or custom
    if canonical is None and not strict:
        canonical = subject  # a rule for a since-removed command must stay deletable
    if canonical is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='unknown command, cog or subject')
    if target_type not in TARGET_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'target_type must be one of {TARGET_TYPES}')
    tid = parse_id(target_id, 'target_id')
    if target_type == 'guild' and tid != guild.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='the guild target must be this server')
    if target_type == 'role' and guild.get_role(tid) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='target_id is not a role in this server')
    if target_type in ('channel', 'category') and guild.get_channel(tid) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'target_id is not a {target_type} in this server')
    return canonical, canonical_target_type(target_type, tid, guild.id), tid


@router.get("/command-rules")
async def list_command_rules(guild: GuildDep, bot: BotDep) -> CommandRulesResponse:
    rules = await bot.db.command_rules.list_rules(guild.id)
    return CommandRulesResponse(
        rules=[CommandRule(subject=r.subject, target_type=r.target_type, target_id=str(r.target_id), allow=r.allow) for r in rules]
    )


@router.put("/command-rules")
async def put_command_rule(guild: GuildDep, bot: BotDep, body: PutCommandRuleBody) -> CommandRulesResponse:
    custom = None if normalize_rule_subject(bot, body.subject) else await custom_command_name(bot.db, guild, body.subject)
    subject, target_type, tid = _target(bot, guild, body.subject, body.target_type, body.target_id, custom=custom)
    await bot.db.command_rules.set_rule(guild.id, subject, target_type, tid, body.allow)
    return await list_command_rules(guild, bot)


@router.delete("/command-rules", status_code=204)
async def delete_command_rule(
    guild: GuildDep,
    bot: BotDep,
    subject: str = Query(...),
    target_type: str = Query(...),
    target_id: str = Query(...),
) -> None:
    canonical, target_type, tid = _target(bot, guild, subject, target_type, target_id, strict=False)
    if not await bot.db.command_rules.remove_rule(guild.id, canonical, target_type, tid):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='no such rule')
