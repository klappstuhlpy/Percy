"""FastAPI dependencies: authentication, guild resolution, bot access."""
from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, Header, HTTPException, Path, Request, status

import config

if TYPE_CHECKING:
    # Only used in return annotations, which FastAPI never evaluates —
    # the dependency aliases below are deliberately ``Annotated[Any, ...]``.
    import discord

    from app.core import Bot


def get_bot(request: Request) -> Bot:
    return request.app.state.bot


async def verify_token(authorization: Annotated[str | None, Header()] = None) -> None:
    if not authorization or authorization != f'Bearer {config.internal_api_token}':
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail='invalid or missing token')


async def resolve_guild(
    guild_id: Annotated[int, Path()],
    bot: Annotated[Any, Depends(get_bot)],
) -> discord.Guild:
    guild = bot.get_guild(guild_id)
    if guild is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='guild not found')
    return guild


BotDep = Annotated[Any, Depends(get_bot)]
GuildDep = Annotated[Any, Depends(resolve_guild)]
AuthDep = Annotated[None, Depends(verify_token)]
