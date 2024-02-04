from __future__ import annotations

import datetime
from typing import List, Dict, Any, NamedTuple, Optional

import aiohttp
import discord
from discord.ext import commands, tasks

from bot import Percy
from cogs.utils.converters import utcparse
from launcher import get_logger

log = get_logger(__name__)


class YouTubeRequestError(discord.HTTPException):
    """A subclass Exception for failed YouTube API requests."""

    def __init__(self, response: aiohttp.ClientResponse, data: Dict[str, Any], message: Optional[str]):
        reason = data['error']['errors'][0]['reason']
        message = f'Reason: {reason or 'Unknown'}: "{message}"'

        super().__init__(response=response, message=message)


BASE_URL = 'https://www.googleapis.com/youtube/v3/{endpoint}'
YOUTUBE_ICON_URL = 'https://images.klappstuhl.me/gallery/vXIDExSNYa.png'
YOUTUBE_VIDEO_URL = 'https://www.youtube.com/watch?v={video_id}'


class YouTubeChannel(NamedTuple):
    id: str
    name: str
    icon_url: str

    @property
    def url(self) -> str:
        return f'https://www.youtube.com/channel/{self.id}'


class YouTubeStream(NamedTuple):
    channel: YouTubeChannel
    video_id: str
    started_at: datetime.datetime
    title: str
    description: str
    thumbnail_url: str

    @property
    def url(self) -> str:
        return YOUTUBE_VIDEO_URL.format(video_id=self.video_id)


class YouTubeNotifications(commands.Cog):
    def __init__(self, bot: Percy):
        self.bot: Percy = bot
        self.session: aiohttp.ClientSession = aiohttp.ClientSession()

        self.running_streams: List[YouTubeStream] = []

        # self.config: config_file = config_file('youtube')

    async def cog_load(self) -> None:
        self.refresh_notify_check.start()

    async def cog_unload(self) -> None:
        if not self.session.closed:
            await self.session.close()
        self.refresh_notify_check.cancel()

    @discord.utils.cached_property
    def api_key(self) -> Optional[str]:
        return self.bot.media_config.get('youtube').get('api_key')

    @property
    def bearer_headers(self) -> dict:
        return {'Accept': 'application/json'}

    def payload(self, **params: Any) -> Dict[str, Any]:
        payload = {'key': self.api_key, **params}
        return payload

    async def get_channels(self, channel_names: List[str]) -> Optional[List[YouTubeChannel]]:
        cache = []

        for name in channel_names:
            payload = self.payload(forUsername=name, part='id,snippet')
            async with self.session.get(BASE_URL.format(endpoint='channels'), params=payload,
                                        headers=self.bearer_headers) as resp:
                data = await resp.json()

                if resp.status != 200:
                    match data['error']['errors'][0]['reason']:
                        case 'quotaExceeded':
                            log.trace('YouTube API quota exceeded.')  # Just debug this error. (Request Limit of YouTube API)
                        case _:
                            raise YouTubeRequestError(resp, await resp.json(), f'Could not get channel "{name}".')
                    return

                if not data.get('items', None):
                    continue

                channel = data['items'][0]
                cache.append(
                    YouTubeChannel(
                        id=channel['id'],
                        name=channel['snippet']['title'],
                        icon_url=channel['snippet']['thumbnails']['default']['url']
                    )
                )

        return cache

    async def get_streams(self, channels: List[YouTubeChannel]):
        cache = []

        if not channels:
            return

        for channel in channels:
            if not channel:
                continue

            payload = self.payload(part='snippet', channelId=channel.id, type='video', eventType='live',
                                   maxResults=1, order='date')
            async with self.session.get(BASE_URL.format(endpoint='search'), params=payload,
                                        headers=self.bearer_headers) as resp:
                data = await resp.json()

                if resp.status != 200:
                    match data['error']['errors'][0]['reason']:
                        case 'quotaExceeded':
                            log.debug(
                                'YouTube API quota exceeded.')  # Just debug this error. (Request Limit of YouTube API)
                        case _:
                            raise YouTubeRequestError(resp, data, f'Could not get stream for channel "{channel.id}".')
                    return

                if not data.get('items', None):
                    continue

                stream = data['items'][0]
                cache.append(
                    YouTubeStream(
                        channel=channel,
                        video_id=stream['id']['videoId'],
                        started_at=utcparse(stream['snippet']['publishedAt']),
                        title=stream['snippet']['title'],
                        description=stream['snippet']['description'],
                        thumbnail_url=stream['snippet']['thumbnails']['high']['url']
                    )
                )

        return cache

    async def get_notifications(self) -> List[YouTubeStream]:
        wl = self.bot.media_config.get('youtube').get('watchlist')
        channels = await self.get_channels(wl)
        streams = await self.get_streams(channels)

        if streams is None:
            return []

        cache = []
        for stream in self.running_streams:
            if stream not in streams:
                self.running_streams.remove(stream)

        for stream in streams:
            if stream in self.running_streams:
                continue

            cache.append(stream)
            self.running_streams.append(stream)

        return cache

    @property
    def channel(self) -> Optional[discord.TextChannel]:
        channel_id = self.bot.media_config.get('youtube', {}).get('channel_id')
        if not channel_id:
            return
        return self.bot.get_channel(channel_id)

    @tasks.loop(minutes=20)
    async def refresh_notify_check(self):
        await self.bot.wait_until_ready()
        if not self.channel:
            return

        streams = await self.get_notifications()
        for stream in streams:
            embed = discord.Embed(title=stream.title, url=stream.url, color=0xFF0000)
            embed.set_author(name=f'{stream.channel.name} is now Live on YouTube!', url=stream.channel.url,
                             icon_url=YOUTUBE_ICON_URL)
            embed.set_thumbnail(url=stream.channel.icon_url)
            embed.add_field(name='Started', value=discord.utils.format_dt(stream.started_at, style='R'),
                            inline=False)
            embed.set_image(url=stream.thumbnail_url)

            try:
                await self.channel.send(embed=embed)
            except discord.HTTPException:
                log.warning('Could not send twitch notification due to: %s', exc_info=True)


async def setup(bot):
    await bot.add_cog(YouTubeNotifications(bot))
