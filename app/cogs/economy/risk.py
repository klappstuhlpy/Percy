"""Death handling shared by the risky activities (``hunt``, ``search``, ``crime``)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.economy import LIFESAVER_KEY, get_catalogue_item
from app.utils import fnumb
from config import Emojis

if TYPE_CHECKING:
    from app.core import Bot

__all__ = ('resolve_death',)


async def resolve_death(bot: Bot, user_id: int, guild_id: int) -> str:
    """Apply a death: consume a Lifesaver if owned, otherwise lose all cash. Returns the text to append."""
    db = bot.db.economy
    lifesaver = get_catalogue_item(LIFESAVER_KEY)
    assert lifesaver is not None
    row = await db.ensure_builtin(guild_id, lifesaver)
    if await db.consume_item(user_id, guild_id, row['id']) is not None:
        return f'\n{lifesaver.emoji} You almost died, but your **{lifesaver.name}** saved you. It is used up.'
    lost = await db.wipe_cash(user_id, guild_id)
    return f'\n\N{SKULL} **You died!** You lost all your cash ({Emojis.Economy.cash} **{fnumb(lost)}**).'
