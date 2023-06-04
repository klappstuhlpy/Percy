# -*- coding: utf-8 -*-

"""
MIT License

Copyright (c) 2022-Present Klappstuhl

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND,
EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF
MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT.
IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM,
DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR
OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE
OR OTHER DEALINGS IN THE SOFTWARE.
"""
import asyncio
import logging
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, NamedTuple, AsyncIterator

import discord
import json

import yarl
from discord import HTTPException
from discord.ext import commands, tasks
import datetime

from discord.utils import cached_slot_property, MISSING

from bot import Percy
from cogs.utils import cache

logger = logging.getLogger(__name__)


class TwitchRequestError(HTTPException):
    """A subclass Exception for failed Twitch API requests."""
    pass


GRANT_URL = "https://id.twitch.tv/oauth2/token"
END_URL = "https://api.twitch.tv/helix"
TWITCH_ICON_URL = "https://media.discordapp.net/attachments/1062074624935993427/1101142491450835036/5968819.png"


class config:
    """A class for getting and setting the config.json file."""

    path = Path(__file__).parent.parent / "config.json"

    @classmethod
    def set(cls, **params: dict | Any) -> None:
        payload = cls.get()

        with open(cls.path, "w") as file:
            payload["twitch"].update(params)
            json.dump(payload, file, indent=4)

    @classmethod
    def get(cls) -> Dict[str, Any]:
        with open(cls.path, 'r', encoding='utf8') as f:
            return json.load(f)


class TwitchUser(NamedTuple):
    id: str
    login: str
    display_name: str
    type: str
    broadcaster_type: str
    description: str
    profile_image_url: str
    offline_image_url: str
    view_count: int

    @property
    def url(self) -> str:
        return f"https://twitch.tv/{self.login}"


class TwitchStream(NamedTuple):
    id: str
    user: TwitchUser
    game_id: str
    game_name: str
    type: str
    title: str
    tags: List[str]
    viewer_count: int
    started_at: str
    language: str
    thumbnail_url: str


class TwitchNotifications(commands.Cog):
    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self._req_lock = asyncio.Lock()
        self._refresh_lock = asyncio.Lock()
        self.online_users: set[str] = set()

    async def cog_load(self) -> None:
        self.refresh_notify_check.start()

    async def cog_unload(self) -> None:
        self.refresh_notify_check.cancel()

    def _expiry(self, *, expiry: Optional[float] = None) -> float:
        if expiry:
            config.set(expiry=expiry)
        return config.get()["twitch"].get("expiry", None)

    def _bearer_token(self, *, bearer_token: Optional[str] = None) -> str:
        if not self._expiry() or (self._expiry() and self._expiry() < time.time()):
            logger.debug("Refreshing bearer token")
            self.bot.loop.create_task(self._get_bearer_token())

        if bearer_token:
            config.set(bearer_token=bearer_token)
        return config.get()["twitch"]["bearer_token"]

    @cached_slot_property(name="_cs_grant_params")
    def grant_params(self) -> dict:
        return {'client_id': config.get()["twitch"]["client_id"],
                'client_secret': config.get()["twitch"]["client_secret"],
                'grant_type': 'client_credentials',
                'Content-Type': 'application/x-www-form-urlencoded'}

    async def _get_bearer_token(self) -> None:
        async with self._refresh_lock:
            data = await self.twitch_request('POST', GRANT_URL, params=self.grant_params, headers=None)
            self._expiry(expiry=(time.time() + (int(data['expires_in']) - 10)))
            self._bearer_token(bearer_token=data['access_token'])

    async def twitch_request(
            self,
            method: str,
            url: str | yarl.URL,
            *,
            params: Optional[dict[str, Any]] = None,
            data: Optional[dict[str, Any]] = None,
            headers: Optional[dict[str, Any]] = MISSING,
    ) -> Optional[Dict]:
        hdrs = {'Accept': 'application/json',
                'Client-Id': config.get()["twitch"]["client_id"],
                'Authorization': f'Bearer {self._bearer_token()}'}

        if headers is not MISSING and isinstance(headers, dict):
            hdrs.update(headers)
        elif headers is None:
            hdrs = None

        async with self._req_lock:
            async with self.bot.session.request(method, url, params=params, json=data, headers=hdrs) as r:
                remaining = r.headers.get('X-Ratelimit-Remaining')
                js = await r.json()
                if r.status == 429 or remaining == '0':
                    delta = discord.utils._parse_ratelimit_header(r)
                    await asyncio.sleep(delta)
                    self._req_lock.release()
                    return await self.twitch_request(method, url, params=params, data=data, headers=headers)
                elif 300 > r.status >= 200:
                    return js
                else:
                    raise TwitchRequestError(r, js['message'])

    @cache.cache()
    async def get_user(self, login: str) -> Optional[TwitchUser]:
        data = await self.twitch_request('GET', yarl.URL(END_URL) / 'users', params={"login": login})
        user_data = data.get("data", [])
        if user_data:
            user = TwitchUser(
                id=user_data[0]["id"],
                login=user_data[0]["login"],
                display_name=user_data[0]["display_name"],
                type=user_data[0]["type"],
                broadcaster_type=user_data[0]["broadcaster_type"],
                description=user_data[0]["description"],
                profile_image_url=user_data[0]["profile_image_url"],
                offline_image_url=user_data[0]["offline_image_url"],
                view_count=user_data[0]["view_count"]
            )
            return user
        return None

    async def get_stream(self, user: TwitchUser) -> Optional[TwitchStream]:
        data = await self.twitch_request('GET', yarl.URL(END_URL) / 'streams', params={"user_id": user.id})
        stream_data = data.get("data", [])
        if stream_data:
            stream = TwitchStream(
                id=stream_data[0]["id"],
                user=user,
                game_id=stream_data[0]["game_id"],
                game_name=stream_data[0]["game_name"],
                type=stream_data[0]["type"],
                title=stream_data[0]["title"],
                tags=stream_data[0]["tags"],
                viewer_count=stream_data[0]["viewer_count"],
                started_at=stream_data[0]["started_at"],
                language=stream_data[0]["language"],
                thumbnail_url=stream_data[0]["thumbnail_url"]
            )
            return stream
        return None

    async def get_notifications(self) -> AsyncIterator[TwitchStream]:
        wl = config.get()["twitch"]["watchlist"]
        users = [await self.get_user(user_name) for user_name in wl]
        streams = [await self.get_stream(user) for user in users]

        for user_name in list(self.online_users):
            stream = discord.utils.get(streams, user__login=user_name)
            if not stream:
                self.online_users.remove(user_name)

        for stream in streams:
            if not stream:
                continue

            if stream.user.login not in self.online_users:
                self.online_users.add(stream.user.login)
                yield stream

    @tasks.loop(minutes=2)
    async def refresh_notify_check(self):
        await self.bot.wait_until_ready()
        channel = self.bot.get_channel(config.get()["twitch"]["channel_id"])
        if not channel:
            raise discord.NotFound(None, "Twitch Notification channel not found.")

        async for stream in self.get_notifications():
            started_at = datetime.datetime.fromisoformat(stream.started_at).astimezone(datetime.timezone.utc)

            embed = discord.Embed(title=stream.title, url=stream.user.url, color=0x6441a5)
            embed.set_author(name=f"{stream.user.display_name} is now Live on Twitch!", url=stream.user.url,
                             icon_url=TWITCH_ICON_URL)
            embed.set_thumbnail(url=stream.user.profile_image_url)
            embed.add_field(name="Started", value=discord.utils.format_dt(started_at, style="R"),
                            inline=False)
            embed.add_field(name="Game", value=stream.game_name or 'Unknown', inline=True)
            embed.add_field(name="Viewers", value=f"{stream.viewer_count:,}", inline=True)
            if tags := stream.tags:
                embed.add_field(name="Tags", value=", ".join(tags), inline=False)
            embed.set_image(url=stream.thumbnail_url.format(width=1920, height=1080))

            try:
                await channel.send(embed=embed)
            except discord.HTTPException as exc:
                logger.warning("Could not send twitch notification due to: %s", exc_info=True)


async def setup(bot):
    await bot.add_cog(TwitchNotifications(bot))
