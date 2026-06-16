"""InternalAPI economy endpoints."""
from __future__ import annotations

import discord
from aiohttp import web

from app.services.economy import validate_item_effect
from app.utils import fnumb, get_asset_url
from config import Emojis

from .models import InternalAPIHandlers


class EconomyHandlers(InternalAPIHandlers):
    """Economy-related internal API handlers."""

    async def _get_economy(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        items = await self.bot.db.economy.get_items(guild_id)
        lottery = await self.bot.db.economy.get_lottery(guild_id)

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

        return web.json_response({'items': shop, 'lottery': lottery_data})

    async def _create_economy_item(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        name = body.get('name', '').strip()
        price = body.get('price')
        description = body.get('description', '').strip() or None
        effect = (body.get('effect') or 'none').strip()
        effect_value = body.get('effect_value')
        duration_minutes = body.get('duration_minutes')

        if not name or price is None:
            raise web.HTTPBadRequest(text='name and price are required')

        effect_value = int(effect_value) if effect_value is not None else None
        duration_minutes = int(duration_minutes) if duration_minutes is not None else None
        error = validate_item_effect(effect, effect_value, duration_minutes)
        if error:
            raise web.HTTPBadRequest(text=error)

        result = await self.bot.db.economy.create_item(
            guild_id, name, description, int(price), effect, effect_value, duration_minutes
        )
        if result is None:
            raise web.HTTPConflict(text='an item with that name already exists')
        return web.json_response({'ok': True})

    async def _delete_economy_item(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        name = request.match_info['name']
        result = await self.bot.db.economy.delete_item(guild_id, name)
        if result is None:
            raise web.HTTPNotFound(text='item not found')
        return web.json_response({'ok': True})

    async def _get_economy_balances(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        guild = self.bot.get_guild(guild_id)
        if guild is None:
            raise web.HTTPNotFound(text='guild not found')

        limit = int(request.query.get('limit', '25'))
        # One ordered query returns the actual top balances; member identity is then
        # resolved from the in-memory cache (no per-row DB round-trips).
        records = await self.bot.db.users.get_top_balance_records(guild_id, limit)
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
        return web.json_response({'entries': entries})

    async def _patch_economy_balance(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        user_id = int(request.match_info['user_id'])
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        cash = body.get('cash')
        bank = body.get('bank')
        if cash is None and bank is None:
            raise web.HTTPBadRequest(text='must specify cash or bank')

        balance = await self.bot.db.get_user_balance(user_id, guild_id)
        if balance is None:
            raise web.HTTPNotFound(text='user balance not found')

        updates: dict[str, int] = {}
        if cash is not None:
            updates['cash'] = int(cash)
        if bank is not None:
            updates['bank'] = int(bank)
        await balance.update(**updates)
        return web.json_response({'ok': True})

    async def _create_lottery(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        try:
            body = await request.json()
        except Exception:
            raise web.HTTPBadRequest(text='invalid JSON body')

        ticket_price = body.get('ticket_price')
        duration_minutes = body.get('duration_minutes')
        channel_id = body.get('channel_id')

        if not ticket_price or not duration_minutes or not channel_id:
            raise web.HTTPBadRequest(text='ticket_price, duration_minutes, and channel_id are required')

        import datetime
        # economy_lottery.ends_at is a naive TIMESTAMP (UTC); match the bot's own
        # lottery command, which stores when.replace(tzinfo=None).
        ends_at = datetime.datetime.now(datetime.UTC).replace(tzinfo=None) + datetime.timedelta(
            minutes=int(duration_minutes)
        )
        result = await self.bot.db.economy.create_lottery(guild_id, int(channel_id), int(ticket_price), int(ticket_price), ends_at)
        if result is None:
            raise web.HTTPConflict(text='a lottery is already active')

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
            thumbnail=get_asset_url(self.bot.get_guild(guild_id)),
            fields=[
                ("Jackpot", f"{Emojis.Economy.cash} **{fnumb(int(ticket_price))}**"),
                ("Ticket Price", f"{Emojis.Economy.cash} **{fnumb(int(ticket_price))}**"),
                ("Drawing", discord.utils.format_dt(ends_at, "R")),
            ],
        )
        channel = self.bot.get_channel(int(channel_id))
        if channel is None:
            raise web.HTTPNotFound(text='channel not found')

        try:
            await channel.send(view=view)
        except discord.HTTPException as e:
            raise web.HTTPInternalServerError(text=str(e)) from e

        return web.json_response({'ok': True})

    async def _delete_lottery(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        await self.bot.db.economy.delete_lottery(guild_id)
        return web.json_response({'ok': True})

