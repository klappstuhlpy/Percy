"""Economy shop, balances, and lottery endpoints."""
from __future__ import annotations

import datetime
import logging
from typing import Annotated

import discord
from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from pydantic import BaseModel, Field

from app.services.economy import (
    CATALOGUE,
    GuildEconomySettings,
    builtin_error,
    item_category,
    name_reserved_error,
    validate_item_effect,
    validate_item_meta,
)
from app.utils import fnumb, get_asset_url
from config import Emojis

from ..dependencies import BotDep, GuildDep, verify_token

log = logging.getLogger(__name__)

router = APIRouter(prefix="/guilds/{guild_id}", tags=["Economy"], dependencies=[Depends(verify_token)])

#: Sanity cap on a single delta/grant so a typo cannot overflow the BIGINT columns.
_MAX_DELTA = 10**12
_MAX_ID = 2**63 - 1
#: Path ids: a snowflake fits BIGINT; shop item ids are serial, so INTEGER range.
UserId = Annotated[int, Path(ge=1, le=_MAX_ID)]
ItemId = Annotated[int, Path(ge=1, le=2**31 - 1)]
_Snowflake = Field(default=None, ge=1, le=_MAX_ID)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class CreateItemBody(BaseModel):
    name: str = Field(max_length=200)
    price: int = Field(le=_MAX_DELTA)
    description: str | None = Field(default=None, max_length=1000)
    effect: str | None = Field(default=None, max_length=32)
    effect_value: int | None = Field(default=None, le=_MAX_ID)
    duration_minutes: int | None = Field(default=None, le=10**7)
    emoji: str | None = Field(default=None, max_length=100)
    rarity: str = Field(default='common', max_length=32)
    stock: int | None = Field(default=None, le=_MAX_DELTA)
    max_per_user: int | None = Field(default=None, le=_MAX_DELTA)
    required_role_id: int | None = _Snowflake
    available_until: datetime.datetime | None = None
    remove_role_id: int | None = _Snowflake
    reply_message: str | None = Field(default=None, max_length=2000)


class PatchItemBody(BaseModel):
    """Partial item update; only the fields sent change. Send ``null`` to clear a nullable column."""

    name: str | None = Field(default=None, max_length=200)
    price: int | None = Field(default=None, le=_MAX_DELTA)
    description: str | None = Field(default=None, max_length=1000)
    emoji: str | None = Field(default=None, max_length=100)
    rarity: str | None = Field(default=None, max_length=32)
    stock: int | None = Field(default=None, le=_MAX_DELTA)
    max_per_user: int | None = Field(default=None, le=_MAX_DELTA)
    required_role_id: int | None = _Snowflake
    available_until: datetime.datetime | None = None
    remove_role_id: int | None = _Snowflake
    reply_message: str | None = Field(default=None, max_length=2000)


#: Columns ``PATCH /economy/items/{id}`` may write (the effect is fixed once owned copies exist).
_ITEM_FIELDS = (
    'name', 'price', 'description', 'emoji', 'rarity', 'stock', 'max_per_user', 'required_role_id',
    'available_until', 'remove_role_id', 'reply_message',
)


def _naive_utc(value: datetime.datetime | None) -> datetime.datetime | None:
    """Timestamps are stored naive UTC; an aware value from the API is converted first."""
    if value is not None and value.tzinfo is not None:
        return value.astimezone(datetime.UTC).replace(tzinfo=None)
    return value


class PatchBalanceBody(BaseModel):
    cash: int | None = None
    bank: int | None = None


class AdjustBalanceBody(BaseModel):
    """Signed deltas; the result of either column must stay >= 0."""

    cash: int = 0
    bank: int = 0


class GiveItemBody(BaseModel):
    item_id: int = Field(ge=1, le=2**31 - 1)
    quantity: int = 1


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
    bank_cap: bool | None = None
    drops_enabled: bool | None = None


def _role_error(guild: discord.Guild, field: str, role_id: int | None, *, give: bool = False) -> str | None:
    """A role id from the body must belong to this guild; a role Percy hands out must also be assignable."""
    if role_id is None:
        return None
    role = guild.get_role(role_id)
    if role is None:
        return f'{field} is not a role of this server'
    if give and (role.is_default() or role.managed or role >= guild.me.top_role):
        return f'{field}: I cannot assign that role (it must be below my highest role and not integration-managed)'
    return None


def _settings_payload(settings: GuildEconomySettings) -> dict:
    return {
        'payout_multiplier': settings.payout_multiplier,
        'rob_enabled': settings.rob_enabled,
        'daily_base': settings.daily_base,
        'max_bet': settings.max_bet,
        'bank_cap': settings.bank_cap,
        'drops_enabled': settings.drops_enabled,
    }


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/economy")
async def get_economy(guild: GuildDep, bot: BotDep) -> dict:
    await bot.db.economy.ensure_builtins(guild.id, [i for i in CATALOGUE if i.for_sale])
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
            'emoji': r.get('emoji'),
            'rarity': r.get('rarity') or 'common',
            'category': item_category(r.get('effect')).id,
            'stock': r.get('stock'),
            'max_per_user': r.get('max_per_user'),
            'required_role_id': str(r['required_role_id']) if r.get('required_role_id') else None,
            'available_until': r['available_until'].isoformat() if r.get('available_until') else None,
            'remove_role_id': str(r['remove_role_id']) if r.get('remove_role_id') else None,
            'reply_message': r.get('reply_message'),
            'builtin_key': r.get('builtin_key'),
            'for_sale': r.get('for_sale', True),
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

    if body.bank_cap is not None:
        updates['bank_cap'] = body.bank_cap

    if body.drops_enabled is not None:
        updates['drops_enabled'] = body.drops_enabled

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

    error = name_reserved_error(name) or validate_item_effect(effect, effect_value, duration_minutes) or validate_item_meta(
        name, rarity=body.rarity, stock=body.stock, max_per_user=body.max_per_user, price=body.price,
        reply_message=body.reply_message,
    )
    error = error or _role_error(guild, 'required_role_id', body.required_role_id) or _role_error(
        guild, 'remove_role_id', body.remove_role_id
    ) or (_role_error(guild, 'effect_value', effect_value, give=True) if effect == 'role' else None)
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)

    result = await bot.db.economy.create_item(
        guild.id, name, description, body.price, effect, effect_value, duration_minutes
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='an item with that name already exists')
    meta = {
        key: getattr(body, key)
        for key in ('emoji', 'rarity', 'stock', 'max_per_user', 'required_role_id', 'remove_role_id', 'reply_message')
    }
    meta['available_until'] = _naive_utc(body.available_until)
    await bot.db.economy.update_item(guild.id, result['id'], meta)
    return {'ok': True, 'id': result['id']}


@router.patch("/economy/items/{item_id}")
async def patch_economy_item(guild: GuildDep, bot: BotDep, item_id: ItemId, body: PatchItemBody) -> dict:
    values = {key: getattr(body, key) for key in _ITEM_FIELDS if key in body.model_fields_set}
    if not values:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='no fields to update')
    if values.get('name', '') is None or values.get('price', 1) is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='name and price cannot be cleared')
    if 'available_until' in values:
        values['available_until'] = _naive_utc(values['available_until'])
    if 'rarity' in values and values['rarity'] is None:
        values['rarity'] = 'common'
    error = validate_item_meta(
        values.get('name'), rarity=values.get('rarity'), stock=values.get('stock'),
        max_per_user=values.get('max_per_user'), price=values.get('price'), reply_message=values.get('reply_message'),
    )
    if error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    if error := _role_error(guild, 'required_role_id', values.get('required_role_id')) or _role_error(
        guild, 'remove_role_id', values.get('remove_role_id')
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    existing = await bot.db.economy.get_item_by_id(guild.id, item_id)
    if existing is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='item not found')
    if error := builtin_error(existing, 'edit') or (name_reserved_error(values['name']) if values.get('name') else None):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    if await bot.db.economy.update_item(guild.id, item_id, values) is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='an item with that name already exists')
    return {'ok': True}


@router.delete("/economy/items/{name}")
async def delete_economy_item(guild: GuildDep, bot: BotDep, name: str) -> dict:
    existing = await bot.db.economy.get_item(guild.id, name)
    if existing is not None and (error := builtin_error(existing, 'remove')):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    result = await bot.db.economy.delete_item(guild.id, name)
    if result is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='item not found')
    return {'ok': True}


@router.get("/economy/balances")
async def get_economy_balances(
    guild: GuildDep,
    bot: BotDep,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> dict:
    records = await bot.db.users.get_top_balance_records(guild.id, limit + offset)
    total = await bot.db.economy.count_balances(guild.id)
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
    entries = entries[offset:offset + limit]
    return {'entries': entries, 'total': total}


@router.patch("/economy/balances/{user_id}")
async def patch_economy_balance(guild: GuildDep, bot: BotDep, user_id: UserId, body: PatchBalanceBody) -> dict:
    if body.cash is None and body.bank is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='must specify cash or bank')

    # A negative wallet breaks every spend check; past BIGINT asyncpg answers with a 500.
    if any(v is not None and not 0 <= v <= _MAX_DELTA for v in (body.cash, body.bank)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'cash and bank must be between 0 and {_MAX_DELTA}')

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
    # Same limits as `lottery start`, checked before the row exists so a refusal leaves no lottery behind.
    if body.ticket_price < 1 or body.ticket_price > _MAX_DELTA:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f'ticket_price must be between 1 and {_MAX_DELTA}')
    if not 5 <= body.duration_minutes <= 30 * 24 * 60:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='duration must be between 5 minutes and 30 days')
    # Guild-scoped: bot.get_channel would accept another server's channel.
    channel = guild.get_channel(body.channel_id)
    if not isinstance(channel, discord.abc.Messageable):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='channel not found')

    # economy_lottery.ends_at is a naive TIMESTAMP (UTC); match the bot's own
    # lottery command, which stores when.replace(tzinfo=None).
    when = datetime.datetime.now(datetime.UTC) + datetime.timedelta(minutes=body.duration_minutes)
    ends_at = when.replace(tzinfo=None)
    result = await bot.db.economy.create_lottery(
        guild.id, channel.id, body.ticket_price, body.ticket_price, ends_at
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='a lottery is already active')
    # The draw is timer-driven (on_lottery_timer_complete); without this the lottery never ends.
    await bot.timers.create(when, "lottery", guild.id, result["ends_at"].isoformat())

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
    try:
        await channel.send(view=view)
    except discord.HTTPException as e:
        log.warning("Lottery announcement failed in guild %s: %s", guild.id, e)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail='the lottery started but the announcement could not be posted'
        ) from None

    return {'ok': True}


@router.delete("/economy/lottery")
async def delete_lottery(guild: GuildDep, bot: BotDep) -> dict:
    await bot.db.economy.delete_lottery(guild.id)
    return {'ok': True}


# ---------------------------------------------------------------------------
# Public-API support: single-member reads, atomic adjust, inventory, delete-by-id
# ---------------------------------------------------------------------------


def _balance_payload(user_id: int, row) -> dict:
    cash, bank = (row['cash'], row['bank']) if row else (0, 0)
    return {'user_id': str(user_id), 'cash': cash, 'bank': bank, 'total': cash + bank}


@router.get("/economy/balances/{user_id}")
async def get_economy_balance(guild: GuildDep, bot: BotDep, user_id: UserId) -> dict:
    """One member's wallet; a member with no row is a zero wallet (and no row is created)."""
    return _balance_payload(user_id, await bot.db.users.find_balance_record(user_id, guild.id))


@router.post("/economy/balances/{user_id}/adjust")
async def adjust_economy_balance(guild: GuildDep, bot: BotDep, user_id: UserId, body: AdjustBalanceBody) -> dict:
    if body.cash == 0 and body.bank == 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='must specify cash or bank')
    if abs(body.cash) > _MAX_DELTA or abs(body.bank) > _MAX_DELTA:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='amount too large')
    row = await bot.db.users.adjust_balance(user_id, guild.id, body.cash, body.bank)
    if row is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='balance cannot go negative')
    return _balance_payload(user_id, row)


@router.get("/economy/inventory/{user_id}")
async def get_economy_inventory(guild: GuildDep, bot: BotDep, user_id: UserId) -> dict:
    rows = await bot.db.economy.get_inventory(user_id, guild.id)
    return {
        'user_id': str(user_id),
        'items': [
            {
                'item_id': r['item_id'],
                'name': r['name'],
                'quantity': r['quantity'],
                'emoji': r.get('emoji'),
                'rarity': r.get('rarity') or 'common',
                'effect': r.get('effect') or 'none',
            }
            for r in rows
        ],
    }


@router.post("/economy/inventory/{user_id}")
async def give_economy_item(guild: GuildDep, bot: BotDep, user_id: UserId, body: GiveItemBody) -> dict:
    if not 1 <= body.quantity <= 10_000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail='quantity must be between 1 and 10000')
    result, owned = await bot.db.economy.give_item(user_id, guild.id, body.item_id, body.quantity)
    if result == 'missing':
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='item not found')
    if result == 'limit':
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='per-user item limit reached')
    return {'ok': True, 'item_id': body.item_id, 'quantity': owned}


@router.delete("/economy/inventory/{user_id}/{item_id}")
async def take_economy_item(
    guild: GuildDep, bot: BotDep, user_id: UserId, item_id: ItemId, quantity: int = Query(default=1, ge=1, le=10_000)
) -> dict:
    left = await bot.db.economy.consume_item(user_id, guild.id, item_id, quantity)
    if left is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail='member does not own that many')
    return {'ok': True, 'item_id': item_id, 'quantity': left}


@router.delete("/economy/items/by-id/{item_id}")
async def delete_economy_item_by_id(guild: GuildDep, bot: BotDep, item_id: ItemId) -> dict:
    existing = await bot.db.economy.get_item_by_id(guild.id, item_id)
    if existing is not None and (error := builtin_error(existing, 'remove')):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error)
    if await bot.db.economy.delete_item_by_id(guild.id, item_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='item not found')
    return {'ok': True}
