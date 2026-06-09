"""InternalAPI economy endpoints."""
from __future__ import annotations

import discord
from aiohttp import web

from config import Emojis
from .base import InternalAPIHandlers
from ..core import make_notice, Accent
from ..utils import get_asset_url, fnumb


class EconomyHandlers(InternalAPIHandlers):
    """Economy-related internal API handlers."""

    async def _get_economy(self, request: web.Request) -> web.Response:
        guild_id = int(request.match_info['guild_id'])
        items = await self.bot.db.economy.get_items(guild_id)
        lottery = await self.bot.db.economy.get_lottery(guild_id)

        shop = [
            {'id': r['id'], 'name': r['name'], 'description': r.get('description'), 'price': r['price']}
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

        if not name or price is None:
            raise web.HTTPBadRequest(text='name and price are required')

        result = await self.bot.db.economy.create_item(guild_id, name, description, int(price))
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
        entries = []
        for member in sorted(guild.members, key=lambda m: m.id)[:limit * 3]:
            balance = await self.bot.db.get_user_balance(member.id, guild_id)
            if balance and balance.total > 0:
                entries.append({
                    'user_id': str(member.id),
                    'username': str(member),
                    'avatar_url': member.display_avatar.url,
                    'cash': balance.cash,
                    'bank': balance.bank,
                    'total': balance.total,
                })
        entries.sort(key=lambda e: e['total'], reverse=True)
        return web.json_response({'entries': entries[:limit]})

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

        if cash is not None:
            await balance.update(cash=int(cash))
        if bank is not None:
            await balance.update(bank=int(bank))
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

