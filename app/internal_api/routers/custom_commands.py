"""Guild custom commands and command aliases (``custom_commands`` / ``command_aliases``, V73).

Validation is the same pure code the ``customcommand``/``alias`` commands use
(``app/services/custom_commands/``), so a signature or name the bot would refuse is a 400 here with the
same message. Writes go through ``bot.db.custom_commands``, which busts the cached dispatch index.
Ids and bitmasks are strings on the wire (snowflakes and permission values exceed 2**53).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.services.custom_commands.models import (
    MAX_ALIASES,
    MAX_COMMANDS,
    CustomCommand,
    alias_problem,
    validate_command,
)
from app.services.custom_commands.signature import TYPES
from app.services.custom_commands.template import PLACEHOLDERS

from ..dependencies import BotDep, GuildDep, verify_token
from ..helpers import parse_id

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Custom commands"], dependencies=[Depends(verify_token)])


class CustomCommandEntry(BaseModel):
    id: int
    name: str
    aliases: list[str]
    signature: str
    responses: list[str]
    required_permissions: str | None
    allowed_role_ids: list[str]
    cooldown_rate: int | None
    cooldown_per: float | None
    delete_invocation: bool
    owner_id: str | None
    uses: int
    created_at: str | None


class CustomCommandsResponse(BaseModel):
    commands: list[CustomCommandEntry]
    types: list[str]
    placeholders: dict[str, str]
    max_commands: int


class CommandAliasEntry(BaseModel):
    alias: str
    expansion: str
    owner_id: str | None


class CommandAliasesResponse(BaseModel):
    aliases: list[CommandAliasEntry]
    max_aliases: int


class CustomCommandBody(BaseModel):
    name: str
    responses: list[str]
    aliases: list[str] = []
    signature: str = ''
    required_permissions: str | None = None
    allowed_role_ids: list[str] = []
    cooldown_rate: int | None = None
    cooldown_per: float | None = None
    delete_invocation: bool = False
    owner_id: str | None = None


class PatchCustomCommandBody(BaseModel):
    name: str | None = None
    responses: list[str] | None = None
    aliases: list[str] | None = None
    signature: str | None = None
    required_permissions: str | None = None
    allowed_role_ids: list[str] | None = None
    cooldown_rate: int | None = None
    cooldown_per: float | None = None
    delete_invocation: bool | None = None


class PutAliasBody(BaseModel):
    expansion: str
    owner_id: str | None = None


def _entry(cc: CustomCommand) -> CustomCommandEntry:
    return CustomCommandEntry(
        id=cc.id,
        name=cc.name,
        aliases=list(cc.aliases),
        signature=cc.signature,
        responses=list(cc.responses),
        required_permissions=str(cc.required_permissions) if cc.required_permissions else None,
        allowed_role_ids=[str(r) for r in cc.allowed_role_ids],
        cooldown_rate=cc.cooldown_rate,
        cooldown_per=cc.cooldown_per,
        delete_invocation=cc.delete_invocation,
        owner_id=str(cc.owner_id) if cc.owner_id else None,
        uses=cc.uses,
        created_at=cc.created_at.isoformat() if cc.created_at else None,
    )


def _bad(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _columns(guild, raw: dict) -> dict:
    """Wire values -> column values (ids/bitmask parsed, roles checked against the guild)."""
    out = dict(raw)
    if 'required_permissions' in out:
        value = out['required_permissions']
        try:
            out['required_permissions'] = int(value) if value not in (None, '') else None
        except (TypeError, ValueError):
            raise _bad('required_permissions must be a permission bitmask') from None
    if 'allowed_role_ids' in out:
        role_ids = [parse_id(r, 'allowed_role_ids') for r in out['allowed_role_ids'] or ()]
        if any(guild.get_role(r) is None for r in role_ids):
            raise _bad('allowed_role_ids contains a role that is not in this server')
        out['allowed_role_ids'] = role_ids
    if out.get('name') is not None:
        out['name'] = out['name'].lower()
    return out


async def _validated(bot, guild, merged: dict, command_id: int | None) -> dict:
    index = await bot.db.get_custom_command_index(guild.id)
    try:
        return validate_command(merged, index, {n.lower() for n in bot.all_commands}, command_id=command_id)
    except ValueError as exc:
        raise _bad(str(exc)) from None


@router.get("/custom-commands")
async def list_custom_commands(guild: GuildDep, bot: BotDep) -> CustomCommandsResponse:
    commands = await bot.db.custom_commands.list_commands(guild.id)
    return CustomCommandsResponse(
        commands=[_entry(cc) for cc in commands],
        types=list(TYPES),
        placeholders=PLACEHOLDERS,
        max_commands=MAX_COMMANDS,
    )


@router.post("/custom-commands", status_code=201)
async def create_custom_command(guild: GuildDep, bot: BotDep, body: CustomCommandBody) -> CustomCommandEntry:
    if await bot.db.custom_commands.count_commands(guild.id) >= MAX_COMMANDS:
        raise _bad(f'this server already has the maximum of {MAX_COMMANDS} custom commands')
    raw = body.model_dump(exclude={'owner_id'})
    values = await _validated(bot, guild, _columns(guild, raw), None)
    owner_id = parse_id(body.owner_id, 'owner_id') if body.owner_id else None
    return _entry(await bot.db.custom_commands.create_command(guild.id, owner_id, **values))


@router.patch("/custom-commands/{command_id}")
async def patch_custom_command(
    guild: GuildDep, bot: BotDep, command_id: int, body: PatchCustomCommandBody
) -> CustomCommandEntry:
    current = await bot.db.custom_commands.get_command(guild.id, command_id)
    if current is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='no such custom command')
    changes = body.model_dump(exclude_unset=True)
    for key in ('name', 'responses', 'aliases', 'signature', 'allowed_role_ids', 'delete_invocation'):
        if key in changes and changes[key] is None:
            raise _bad(f'{key} cannot be null')
    values = await _validated(bot, guild, current.editable() | _columns(guild, changes), command_id)
    updated = await bot.db.custom_commands.update_command(guild.id, command_id, **values)
    if updated is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='no such custom command')
    return _entry(updated)


@router.delete("/custom-commands/{command_id}", status_code=204)
async def delete_custom_command(guild: GuildDep, bot: BotDep, command_id: int) -> None:
    if not await bot.db.custom_commands.delete_command(guild.id, command_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='no such custom command')


@router.get("/aliases")
async def list_aliases(guild: GuildDep, bot: BotDep) -> CommandAliasesResponse:
    aliases = await bot.db.custom_commands.list_aliases(guild.id)
    return CommandAliasesResponse(
        aliases=[
            CommandAliasEntry(alias=a.alias, expansion=a.expansion, owner_id=str(a.owner_id) if a.owner_id else None)
            for a in aliases
        ],
        max_aliases=MAX_ALIASES,
    )


@router.put("/aliases/{alias}")
async def put_alias(guild: GuildDep, bot: BotDep, alias: str, body: PutAliasBody) -> CommandAliasesResponse:
    alias = alias.lower()
    index = await bot.db.get_custom_command_index(guild.id)
    if problem := alias_problem(alias, body.expansion, index, {n.lower() for n in bot.all_commands}):
        raise _bad(problem)
    if alias not in index.aliases and len(index.aliases) >= MAX_ALIASES:
        raise _bad(f'this server already has the maximum of {MAX_ALIASES} aliases')
    owner_id = parse_id(body.owner_id, 'owner_id') if body.owner_id else None
    await bot.db.custom_commands.put_alias(guild.id, alias, body.expansion.strip(), owner_id)
    return await list_aliases(guild, bot)


@router.delete("/aliases/{alias}", status_code=204)
async def delete_alias(guild: GuildDep, bot: BotDep, alias: str) -> None:
    if not await bot.db.custom_commands.delete_alias(guild.id, alias.lower()):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='no such alias')
