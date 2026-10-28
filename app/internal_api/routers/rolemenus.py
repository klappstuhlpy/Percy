"""Role menus: self-assignable role buttons, managed from the dashboard.

Every write goes through the ``RoleMenus`` cog's operations, so the dashboard and the
``rolemenu`` commands enforce the same rules (role hierarchy, 25-role cap, duplicates) and the
posted message is re-rendered after each change.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ..dependencies import BotDep, GuildDep, verify_token
from ..helpers import resolve_channel, resolve_moderator

if TYPE_CHECKING:
    from app.cogs.rolemenu.cog import RoleMenus
    from app.core import Bot

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Role menus"], dependencies=[Depends(verify_token)])


class CreateRoleMenuBody(BaseModel):
    channel_id: int
    title: str = Field(min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    unique: bool = False


class PatchRoleMenuBody(BaseModel):
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    unique: bool | None = None


class AddRoleMenuRoleBody(BaseModel):
    role_id: int
    emoji: str | None = Field(default=None, max_length=64)
    label: str | None = Field(default=None, max_length=80)
    #: The signed-in dashboard user (set by the BFF, never the browser); older dashboards omit it.
    user_id: str | int | None = None


def _cog(bot: Bot) -> RoleMenus:
    cog = bot.get_cog('RoleMenus')
    if cog is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="role menus are not loaded")
    return cog  # type: ignore[return-value]


def _refused(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.get("/rolemenus")
async def list_role_menus(guild: GuildDep, bot: BotDep) -> dict:
    """Every menu with its roles, each role resolved and flagged when Percy can no longer give it."""
    menus = []
    for menu in await bot.db.rolemenu.get_guild_menus(guild.id):
        roles = []
        for entry in await bot.db.rolemenu.get_entries(menu['id']):
            role = guild.get_role(entry['role_id'])
            roles.append({
                "role_id": str(entry['role_id']),
                "name": role.name if role else None,
                "color": role.color.value if role else 0,
                "emoji": entry['emoji'],
                "label": entry['label'],
                "assignable": bool(role and role < guild.me.top_role and not role.managed),
            })
        jump = (
            f"https://discord.com/channels/{guild.id}/{menu['channel_id']}/{menu['message_id']}"
            if menu['message_id'] else None
        )
        menus.append({
            "id": menu['id'],
            "title": menu['title'],
            "description": menu['description'],
            "unique": menu['unique_roles'],
            "channel": resolve_channel(guild, menu['channel_id']),
            "jump_url": jump,
            "roles": roles,
        })
    return {"menus": menus, "max_roles": 25}


@router.post("/rolemenus")
async def create_role_menu(guild: GuildDep, bot: BotDep, body: CreateRoleMenuBody) -> dict:
    from app.cogs.rolemenu.cog import RoleMenuError

    channel = guild.get_channel(body.channel_id)
    if not isinstance(channel, discord.TextChannel):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Pick a text channel.")
    try:
        menu = await _cog(bot).create_menu(channel, body.title.strip(), body.description or None, unique=body.unique)
    except RoleMenuError as exc:
        raise _refused(exc) from None
    return {"ok": True, "id": menu['id'], "message": f"Role menu posted in #{channel.name}. Add roles to it next."}


@router.patch("/rolemenus/{menu_id}")
async def patch_role_menu(guild: GuildDep, bot: BotDep, menu_id: int, body: PatchRoleMenuBody) -> dict:
    from app.cogs.rolemenu.cog import RoleMenuError

    try:
        await _cog(bot).edit_menu(guild, menu_id, title=body.title, description=body.description, unique=body.unique)
    except RoleMenuError as exc:
        raise _refused(exc) from None
    return {"ok": True}


@router.post("/rolemenus/{menu_id}/roles")
async def add_role_menu_role(guild: GuildDep, bot: BotDep, menu_id: int, body: AddRoleMenuRoleBody) -> dict:
    from app.cogs.rolemenu.cog import RoleMenuError

    role = guild.get_role(body.role_id)
    if role is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="That role no longer exists.")
    if body.user_id not in (None, ''):
        # Manage Server gates the command; like it, never offer a role above your own top role.
        actor = await resolve_moderator(guild, body.user_id, 'manage_guild')
        if actor.id != guild.owner_id and role >= actor.top_role:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f'{role.name} is not below your highest role.')
    try:
        await _cog(bot).add_role(guild, menu_id, role, body.emoji, body.label)
    except RoleMenuError as exc:
        raise _refused(exc) from None
    return {"ok": True, "message": f"Added @{role.name}."}


@router.delete("/rolemenus/{menu_id}/roles/{role_id}")
async def remove_role_menu_role(guild: GuildDep, bot: BotDep, menu_id: int, role_id: int) -> dict:
    from app.cogs.rolemenu.cog import RoleMenuError

    try:
        await _cog(bot).remove_role(guild, menu_id, role_id)
    except RoleMenuError as exc:
        raise _refused(exc) from None
    return {"ok": True}


@router.delete("/rolemenus/{menu_id}")
async def delete_role_menu(guild: GuildDep, bot: BotDep, menu_id: int) -> dict:
    from app.cogs.rolemenu.cog import RoleMenuError

    try:
        await _cog(bot).delete_menu(guild, menu_id)
    except RoleMenuError as exc:
        raise _refused(exc) from None
    return {"ok": True}
