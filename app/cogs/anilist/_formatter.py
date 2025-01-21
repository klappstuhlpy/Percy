from __future__ import annotations

import calendar
import datetime
import re
from typing import TYPE_CHECKING, Any

import discord
from bs4 import BeautifulSoup

from app.utils import helpers, pluralize

if TYPE_CHECKING:
    import aiohttp

ANILIST_LOGO = 'https://klappstuhl.me/gallery/QsPdYKTELw.png'
ANILIST_ICON = 'https://klappstuhl.me/gallery/UBBnkPhKID.png'


class AniListEmbed(discord.Embed):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.set_footer(text='Provided by AniList', icon_url=ANILIST_LOGO)


class AniListEmbedBuilder:
    """A class to build AniList embeds."""
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session: aiohttp.ClientSession = session

    async def media(self, data: dict[str, Any]) -> discord.Embed:
        embed = AniListEmbed(
            title=format_media_title(data.get('title').get('romaji'), data.get('title').get('english')),
            description=sanitize_description(data.get('description'), 400),
            color=discord.Color.from_str(data.get('coverImage').get('color') or '#2b2d31'),
            url=data.get('siteUrl'),
        )
        embed.set_author(name=format_media_format(data.get('format')), icon_url=ANILIST_LOGO)

        if data.get('coverImage').get('large'):
            embed.set_thumbnail(url=data.get('coverImage').get('large'))

        if data.get('bannerImage'):
            embed.set_image(url=data.get('bannerImage'))

        if data.get('type') == 'ANIME':
            if data.get('status') == 'RELEASING':
                if data.get('nextAiringEpisode'):
                    if data.get('episodes'):
                        aired_episodes = f'{data.get('nextAiringEpisode').get('episode') - 1}/{data.get('episodes')}'
                    else:
                        aired_episodes = data.get('nextAiringEpisode').get('episode') - 1

                    if data.get('nextAiringEpisode').get('airingAt'):
                        airing_at = discord.utils.format_dt(
                            datetime.datetime.fromtimestamp(data.get('nextAiringEpisode').get('airingAt')), 'R'
                        )
                    else:
                        airing_at = 'N/A'

                    embed.add_field(name='Aired Episodes', value=f'{aired_episodes} (Next {airing_at})', inline=False)

            else:
                embed.add_field(name='Episodes', value=data.get('episodes', 'N/A'), inline=False)
        else:
            embed.add_field(name='Chapters', value=data.get('chapters', 'N/A'), inline=True)
            embed.add_field(name='Volumes', value=data.get('volumes', 'N/A'), inline=True)
            embed.add_field(name='Source', value=format_media_source(data.get('source')), inline=True)

        start_date = format_date(
            year=data.get('startDate').get('year'),
            month=data.get('startDate').get('month'),
            day=data.get('startDate').get('day'),
        )
        end_date = format_date(
            year=data.get('endDate').get('year'),
            month=data.get('endDate').get('month'),
            day=data.get('endDate').get('day'),
        )
        end_date = 'Present' if data.get('status') == 'RELEASING' else end_date
        embed.add_field(name='Running', value=start_date + ' - ' + end_date, inline=False)

        if data.get('type') == 'ANIME':
            status = format_anime_status(data.get('status'))
        else:
            status = format_manga_status(data.get('status'))

        embed.add_field(name='Status', value=status, inline=True)

        if data.get('type') == 'ANIME':
            duration = f'~ {data.get("duration")} min' if data.get('duration') else 'N/A'

            studio_data = data.get('studios').get('nodes')
            studio = f'[{studio_data[0].get('name')}]({studio_data[0].get('siteUrl')})' \
                if data.get('studios').get('nodes') else 'N/A'

            embed.add_field(name='Episode Duration', value=duration, inline=True)
            embed.add_field(name='Studio', value=studio, inline=True)
            embed.add_field(name='Source', value=format_media_source(data.get('source')), inline=True)

        embed.add_field(name='Score', value=f'{data.get('meanScore')}%' or 'N/A', inline=True)
        embed.add_field(name='Popularity', value=data.get('popularity', 'N/A'), inline=True)
        embed.add_field(name='Favourites', value=data.get('favourites', 'N/A'), inline=True)

        hashtag = data.get('hashtag') or ''
        potential_hashtags = list(filter(None, hashtag.split(' ')))
        if potential_hashtags:
            pluralized = f'{pluralize(len(potential_hashtags)):Hashtag}'
            embed.add_field(name=pluralized.split(' ')[1],
                            value=', '.join(
                                [f'[`{hashtag}`](https://twitter.com/search?q={hashtag.replace('#', '%23')}&src=typd)'
                                 for hashtag in potential_hashtags]
                            ), inline=False)

        if data.get('genres'):
            embed.add_field(name='Genres', value=', '.join(
                [f'[`{i}`](https://anilist.co/search/anime/{i.strip().replace(' ', '%20')})' for i in
                 data.get('genres')]), inline=False)

        if data.get('trailer'):
            yt_url = f'https://www.youtube.com/watch?v={data.get('trailer').get('id')}'
            async with self.session.get(yt_url) as resp:
                if resp.status == 200:
                    soup = BeautifulSoup(await resp.text(), 'lxml')
                    title = soup.find_all(name='title')[0].text.replace(' - YouTube', "")
                    embed.add_field(name='Trailer', value=f'[{title if title else "Click Here"}]({yt_url})')

        return embed

    @classmethod
    def character(cls, data: dict[str, Any]) -> discord.Embed:
        embed = AniListEmbed(
            title=format_name(data.get('name').get('full'), data.get('name').get('native')),
            description=sanitize_description(data.get('description'), 1000),
            color=helpers.Colour.white(),
            url=data.get('siteUrl'),
        )

        if data.get('image').get('large'):
            embed.set_thumbnail(url=data.get('image').get('large'))

        birthday = format_date(
            year=data.get('dateOfBirth').get('year'),
            month=data.get('dateOfBirth').get('month'),
            day=data.get('dateOfBirth').get('day'),
        )

        embed.add_field(name='Birthday', value=birthday, inline=True)
        embed.add_field(name='Age', value=data.get('age', 'N/A'), inline=True)
        embed.add_field(name='Gender', value=data.get('gender', 'N/A'), inline=True)

        if synonyms := [f'`{i}`' for i in data.get('name').get('alternative')] + [
            f'||`{i}`||' for i in data.get('name').get('alternativeSpoiler')
        ]:
            embed.add_field(name='Synonyms', value=', '.join(synonyms), inline=False)

        if media := [
            f'[{i.get('title').get('romaji')}]({i.get('siteUrl')})'
            for i in data.get('media').get('nodes')
            if not i.get('isAdult')
        ]:
            embed.add_field(name='Popular Appearances', value=' | '.join(media), inline=False)

        return embed

    @classmethod
    def user(cls, data: dict[str, Any]) -> discord.Embed:
        embed = AniListEmbed(
            title=f'{data.get('name')} (ID: {data.get('id')})',
            description=f'**About:**\n{data.get('about') or '*No description set.*'}',
            color=helpers.Colour.white(),
            url=data.get('siteUrl'),
        )
        embed.set_thumbnail(url=data.get('avatar', {}).get('large'))
        embed.set_image(url=data.get('bannerImage'))

        statistics = data.get('statistics')

        if anime_stats := statistics.get('anime'):
            embed.add_field(name='Anime Statistics',
                            value=f'**Total:** {anime_stats.get('count')}\n'
                                  f'**Minutes Watched:** {anime_stats.get('minutesWatched')}\n'
                                  f'**Episodes Watched:** {anime_stats.get('episodesWatched')}')

        if manga_stats := statistics.get('manga'):
            embed.add_field(name='Manga Statistics',
                            value=f'**Total:** {manga_stats.get('count')}\n'
                                  f'**Volumes Read:** {manga_stats.get('volumesRead')}\n'
                                  f'**Chapters Read:** {manga_stats.get('chaptersRead')}')

        return embed

    @staticmethod
    def short_media(data: dict[str, Any]) -> discord.Embed:
        if data.get('type') == 'ANIME':
            studio_data = data.get('studios').get('nodes')
            studio = f'[{studio_data[0].get('name')}]({studio_data[0].get('siteUrl')})' \
                if data.get('studios').get('nodes') else 'N/A'

            description = (
                    f'**Status:** {format_anime_status(data.get('status'))}\n'
                    f'**Episodes:** {data.get('episodes', 'N/A')}\n'
                    f'**Studio:** {studio}\n'
                    f'**Score:** {str(data.get('meanScore')) + '%' or 'N/A'}'
            )
        else:
            description = (
                f'**Status:** {format_manga_status(data.get('status'))}\n'
                f'**Chapters:** {data.get('chapters', 'N/A')}\n'
                f'**Volumes:** {data.get('volumes', 'N/A')}\n'
                f'**Score:** {str(data.get('meanScore')) + '%' or 'N/A'}'
            )

        embed = AniListEmbed(
            title=data.get('title').get('romaji'),
            description=description,
            color=discord.Color.from_str(data.get('coverImage').get('color') or '#2b2d31'),
            url=data.get('siteUrl'),
        )
        embed.set_author(name=format_media_format(data.get('format')), icon_url=ANILIST_LOGO)

        if data.get('coverImage').get('large'):
            embed.set_thumbnail(url=data.get('coverImage').get('large'))

        return embed


def format_media_format(media_format: str) -> str:
    formats = {
        'TV': 'TV',
        'TV_SHORT': 'TV Short',
        'MOVIE': 'Movie',
        'SPECIAL': 'Special',
        'OVA': 'OVA',
        'ONA': 'ONA',
        'MUSIC': 'Music',
        'MANGA': 'Manga',
        'NOVEL': 'Novel',
        'ONE_SHOT': 'One Shot',
    }
    return formats.get(media_format, 'N/A')


def format_anime_status(media_status: str) -> str:
    statuses = {
        'FINISHED': 'Finished',
        'RELEASING': 'Airing',
        'NOT_YET_RELEASED': 'Not Yet Aired',
        'CANCELLED': 'Cancelled',
        'HIATUS': 'Paused',
    }
    return statuses.get(media_status, 'N/A')


def format_manga_status(media_status: str) -> str:
    statuses = {
        'FINISHED': 'Finished',
        'RELEASING': 'Publishing',
        'NOT_YET_RELEASED': 'Not Yet Published',
        'CANCELLED': 'Cancelled',
        'HIATUS': 'Paused',
    }
    return statuses.get(media_status, 'N/A')


def format_media_source(media_source: str) -> str:
    sources = {
        'ORIGINAL': 'Original',
        'MANGA': 'Manga',
        'LIGHT_NOVEL': 'Light Novel',
        'VISUAL_NOVEL': 'Visual Novel',
        'VIDEO_GAME': 'Video Game',
        'OTHER': 'Other',
        'NOVEL': 'Novel',
        'DOUJINSHI': 'Doujinshi',
        'ANIME': 'Anime',
        'WEB_NOVEL': 'Web Novel',
        'LIVE_ACTION': 'Live Action',
        'GAME': 'Game',
        'COMIC': 'Comic',
        'MULTIMEDIA_PROJECT': 'Multimedia Project',
        'PICTURE_BOOK': 'Picture Book',
    }
    return sources.get(media_source, 'N/A')


def format_media_title(romaji: str, english: str) -> str:
    if english is None or english == romaji:
        return romaji
    else:
        return f'{romaji} ({english})'


def clean_html(text: str) -> str:
    return re.sub('<.*?>', '', text)


def sanitize_description(description: str, length: int) -> str:
    if description is None:
        return 'N/A'

    sanitized = clean_html(description).replace('**', '').replace('__', '').replace('~!', '||').replace('!~', '||')

    if len(sanitized) > length:
        sanitized = sanitized[0:length]

        if sanitized.count('||') % 2 != 0:
            return sanitized + '...||'

        return sanitized + '...'
    return sanitized


def format_date(**kwargs: dict[str, int]) -> str:
    try:
        date = datetime.date(**kwargs)
    except TypeError:
        _kwargs = {k: v for k, v in kwargs.items() if v is not None}
        if _kwargs == {}:
            return 'N/A'
        else:
            date = []
            for key, value in list(_kwargs.items()):
                if key == 'year':
                    date.append(str(_kwargs.pop(key)))
                    continue
                if key == 'month':
                    date.append(calendar.month_name[_kwargs.pop(key)])  # type: ignore
                    continue
                if key == 'day':
                    date.append(str(_kwargs.pop(key)))
            return ' '.join(date)
    if date:
        days = (date - datetime.date.today()).days
        timestamp = datetime.datetime.now() + datetime.timedelta(days=abs(days) if days > 0 else days)
        return discord.utils.format_dt(timestamp, style='D')
    else:
        return 'N/A'


def format_name(full: str, native: str) -> str:
    if full is None or full == native:
        return native
    elif native is None:
        return full
    else:
        return f'{full} ({native})'


def month_to_season(month: int) -> str:
    seasons = {
        1: 'WINTER',
        2: 'WINTER',
        3: 'WINTER',
        4: 'SPRING',
        5: 'SPRING',
        6: 'SPRING',
        7: 'SUMMER',
        8: 'SUMMER',
        9: 'SUMMER',
        10: 'FALL',
        11: 'FALL',
        12: 'FALL',
    }
    return seasons.get(month, 'N/A')
