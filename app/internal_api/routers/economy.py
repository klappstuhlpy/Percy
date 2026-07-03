"""Economy shop, balances, and lottery endpoints."""
from __future__ import annotations

import datetime

import discord
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from app.services.economy import GuildEconomySettings, validate_item_effect
from app.utils import fnumb, get_asset_url
from config import Emojis

from ..dependencies import BotDep, GuildDep, verify_token

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Economy"], dependencies=[Depends(verify_token)])


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateItemBody(BaseModel):
    name: str
    price: int
    description: str | None = None
    effect: str | None = None
    effect_value: int | None = None
    duration_minutes: int | None = None


class PatchBalanceBody(BaseModel):
    cash: int | None = None
    bank: int | None = None


class CreateLotteryBody(BaseModel):
    ticket_price: int
    duration_minutes: int
    channel_id: int


class PatchSettingsBody(BaseModel):
    """Partial update; omitted fields keep their value. ``max_bet=0`` clears the cap."""

    payout_multiplier: float | None = None
    rob_enabled: bool | None = None
    daily_base: int | None = None
    max_bet: int | None = None


def _settings_payload(settings: GuildEconomySettings) -> dict:
    return {
        'payout_multiplier': settings.payout_multiplier,
        'rob_enabled': settings.rob_enabled,
        'daily_base': settings.daily_base,
        'max_bet': settings.max_bet,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/economy")
async def get_economy(guild: GuildDep, bot: BotDep) -> dict:
    items = await bot.db.economy.get_items(guild.id)
    lottery = await bot.db.economy.get_lottery(guild.id)
    settings = GuildEconomySettings.from_record(await bot.db.economy.get_settings(guild.id))

    shop = [
        {
            'id': r['id'],
            'name': r['name'],
            'description': r.get('description'),
            'price': r['price'],
            'effect': r.get('effect') or 'none',
            'effect_value': r.get('effect_value'),
            'duration_minutes': r.get('duration_minutes'),
        }
        for r in items
    ]
    lottery_data = None
    if lottery:
        lottery_data = {
            'ticket_price': lottery['ticket_price'],
            'jackpot': lottery.get('jackpot', 0),
            'channel_id': str(lottery['channel_id']),
            'ends_at': lottery['ends_at'].isoformat() if lottery.get('ends_at') else None,
        }

    return {'items': shop, 'lottery': lottery_data, 'settings': _settings_payload(settings)}


@router.patch("/economy/settings")
async def patch_economy_settings(guild: GuildDep, bot: BotDep, body: PatchSettingsBody) -> dict:
    updates: dict[str, object] = {}

    if body.payout_multiplier is not None:
        if not 0.1 <= body.payout_multiplier <= 10.0:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail='payout_multiplier must be between 0.1 and 10'
            )
        updates['payout_multiplier'] = body.payout_multiplier

    if body.rob_enabled is not None:
        updates['rob_enabled'] = body.rob_enabled

    if body.daily_base is not None:
        if not 10 <= body.daily_base <= 100_000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail='daily_base must be between 10 and 100000'
            )
        updates['daily_base'] = body.daily_base

    if body.max_bet is not None:
        if not 0 <= body.max_bet <= 100_000_000:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail='max_bet must be between 0 and 100000000'
            )
        # 0 clears the cap, mirroring the `economy-config max-bet` command.
        updates['max_bet'] = body.max_bet or None

    if not updates:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='no fields to update')

    row = await bot.db.economy.update_settings(guild.id, updates)
    return {'ok': True, 'settings': _settings_payload(GuildEconomySettings.from_record(row))}


@router.post("/economy/items")
async def create_economy_item(guild: GuildDep, bot: BotDep, body: CreateItemBody) -> dict:
    name = body.name.strip()
    description = body.description.strip() if body.description else None
    effect = (body.effect or 'none').strip()
    effect_value = body.effect_value
    duration_minutes = body.duration_minutes

    if not name:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='name is required')

    error = validate_item_effect(effect, effect_value, duration_minutes)
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)

    result = await bot.db.economy.create_item(
        guild.id, name, description, body.price, effect, effect_value, duration_minutes
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='an item with that name already exists')
    return {'ok': True}


@router.delete("/economy/items/{name}")
async def delete_economy_item(guild: GuildDep, bot: BotDep, name: str) -> dict:
    result = await bot.db.economy.delete_item(guild.id, name)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='item not found')
    return {'ok': True}


@router.get("/economy/balances")
async def get_economy_balances(
    guild: GuildDep,
    bot: BotDep,
    limit: int = Query(default=25, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    records = await bot.db.users.get_top_balance_records(guild.id, limit + offset)
    entries = []
    for r in records:
        member = guild.get_member(r['user_id'])
        entries.append({
            'user_id': str(r['user_id']),
            'username': str(member) if member else str(r['user_id']),
            'avatar_url': member.display_avatar.url if member else None,
            'cash': r['cash'],
            'bank': r['bank'],
            'total': r['total'],
        })
    total = len(entries)
    entries = entries[offset:offset + limit]
    return {'entries': entries, 'total': total}


@router.patch("/economy/balances/{user_id}")
async def patch_economy_balance(guild: GuildDep, bot: BotDep, user_id: int, body: PatchBalanceBody) -> dict:
    if body.cash is None and body.bank is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='must specify cash or bank')

    balance = await bot.db.get_user_balance(user_id, guild.id)
    if balance is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='user balance not found')

    updates: dict[str, int] = {}
    if body.cash is not None:
        updates['cash'] = body.cash
    if body.bank is not None:
        updates['bank'] = body.bank
    await balance.update(**updates)
    return {'ok': True}


@router.post("/economy/lottery")
async def create_lottery(guild: GuildDep, bot: BotDep, body: CreateLotteryBody) -> dict:
    # economy_lottery.ends_at is a naive TIMESTAMP (UTC); match the bot's own
    # lottery command, which stores when.replace(tzinfo=None).
    ends_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(
        minutes=body.duration_minutes
    )
    result = await bot.db.economy.create_lottery(
        guild.id, body.channel_id, body.ticket_price, body.ticket_price, ends_at
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='a lottery is already active')

    # Deferred import: app.core pulls in Bot -> InternalAPI, so importing it at
    # module load time would create a circular import during startup.
    from app.core.components_v2 import Accent, make_notice

    view = make_notice(
        "Server Lottery",
        "## There has been a lottery started for this server!\n"
        "-# Enter to participate and grab the chance to earn a fortune!\n\n"
        "The pot grows with every ticket sold.\n"
        "You'll need to have the Ticket Price in cash in order to buy a ticket.\n"
        "Buy yourself in with `lottery buy <amount>`.",
        accent=Accent.info,
        thumbnail=get_asset_url(bot.get_guild(guild.id)),
        fields=[
            ("Jackpot", f"{Emojis.Economy.cash} **{fnumb(body.ticket_price)}**"),
            ("Ticket Price", f"{Emojis.Economy.cash} **{fnumb(body.ticket_price)}**"),
            ("Drawing", discord.utils.format_dt(ends_at, "R")),
        ],
    )
    channel = bot.get_channel(body.channel_id)
    if channel is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='channel not found')

    try:
        await channel.send(view=view)
    except discord.HTTPException as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))

    return {'ok': True}


@router.delete("/economy/lottery")
async def delete_lottery(guild: GuildDep, bot: BotDep) -> dict:
    await bot.db.economy.delete_lottery(guild.id)
    return {'ok': True}
