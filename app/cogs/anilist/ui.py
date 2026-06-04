from __future__ import annotations

import calendar
import datetime
import re
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import discord
from bs4 import BeautifulSoup

from app.core import Bot, Context, View
from app.utils import helpers, pluralize
from config import Emojis, anilist

if TYPE_CHECKING:
    import aiohttp

    from .cog import AniList

ANILIST_LOGO = 'https://klappstuhl.me/gallery/raw/ufXiq.png'
ANILIST_ICON = 'https://klappstuhl.me/gallery/raw/sngjJ.png'


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


class AniListEmbed(discord.Embed):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.set_footer(text='Provided by AniList', icon_url=ANILIST_LOGO)


class AniListEmbedBuilder:
    """A class to build AniList embeds."""
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session: aiohttp.ClientSession = session

    async def media(self, data: dict[str, Any]) -> discord.Embed:
        title = _mapping(data.get('title'))
        cover_image = _mapping(data.get('coverImage'))
        next_episode = _mapping(data.get('nextAiringEpisode'))
        start_date = _mapping(data.get('startDate'))
        end_date = _mapping(data.get('endDate'))
        studios = _mapping(data.get('studios'))
        trailer = _mapping(data.get('trailer'))

        embed = AniListEmbed(
            title=format_media_title(title.get('romaji'), title.get('english')),
            description=sanitize_description(data.get('description'), 400),
            color=discord.Color.from_str(cover_image.get('color') or '#2b2d31'),
            url=data.get('siteUrl'),
        )
        embed.set_author(name=format_media_format(data.get('format')), icon_url=ANILIST_LOGO)

        if cover_image.get('large'):
            embed.set_thumbnail(url=cover_image.get('large'))

        if data.get('bannerImage'):
            embed.set_image(url=data.get('bannerImage'))

        if data.get('type') == 'ANIME':
            if data.get('status') == 'RELEASING':
                if data.get('nextAiringEpisode'):
                    if data.get('episodes'):
                        aired_episodes = f"{next_episode.get('episode', 0) - 1}/{data.get('episodes')}"
                    else:
                        aired_episodes = next_episode.get('episode', 0) - 1

                    if next_episode.get('airingAt'):
                        airing_at = discord.utils.format_dt(
                            datetime.datetime.fromtimestamp(float(next_episode.get('airingAt'))), 'R'  # type: ignore
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

        start_date = format_date(year=start_date.get('year'), month=start_date.get('month'), day=start_date.get('day'))
        end_date = format_date(year=end_date.get('year'), month=end_date.get('month'), day=end_date.get('day'))
        end_date = 'Present' if data.get('status') == 'RELEASING' else end_date
        embed.add_field(name='Running', value=start_date + ' - ' + end_date, inline=False)

        if data.get('type') == 'ANIME':
            status = format_anime_status(data.get('status'))
        else:
            status = format_manga_status(data.get('status'))

        embed.add_field(name='Status', value=status, inline=True)

        if data.get('type') == 'ANIME':
            duration = f'~ {data.get("duration")} min' if data.get('duration') else 'N/A'

            studio_data = studios.get('nodes') or []
            studio = f"[{studio_data[0].get('name')}]({studio_data[0].get('siteUrl')})" if studio_data else 'N/A'

            embed.add_field(name='Episode Duration', value=duration, inline=True)
            embed.add_field(name='Studio', value=studio, inline=True)
            embed.add_field(name='Source', value=format_media_source(data.get('source')), inline=True)

        embed.add_field(name='Score', value=f"{data.get('meanScore')}%" if data.get('meanScore') is not None else 'N/A', inline=True)
        embed.add_field(name='Popularity', value=data.get('popularity', 'N/A'), inline=True)
        embed.add_field(name='Favourites', value=data.get('favourites', 'N/A'), inline=True)

        hashtag = data.get('hashtag') or ''
        potential_hashtags = list(filter(None, hashtag.split(' ')))
        if potential_hashtags:
            pluralized = f'{pluralize(len(potential_hashtags)):Hashtag}'
            embed.add_field(name=pluralized.split(' ')[1],
                            value=', '.join(
                                [f"[`{hashtag}`](https://twitter.com/search?q={hashtag.replace('#', '%23')}&src=typd)"
                                 for hashtag in potential_hashtags]
                            ), inline=False)

        if data.get('genres'):
            embed.add_field(name='Genres', value=', '.join(
                [f"[`{i}`](https://anilist.co/search/anime/{i.strip().replace(' ', '%20')})" for i in
                 data.get('genres')]), inline=False)  # type: ignore[union-attr]

        if data.get('trailer'):
            yt_url = f'https://www.youtube.com/watch?v={data.get('trailer').get('id')}'  # type: ignore[union-attr]
            async with self.session.get(yt_url) as resp:
                if resp.status == 200:
                    soup = BeautifulSoup(await resp.text(), 'lxml')
                    title = soup.find_all(name='title')[0].text.replace(' - YouTube', "")
                    embed.add_field(name='Trailer', value=f'[{title if title else "Click Here"}]({yt_url})')

        return embed

    @classmethod
    def character(cls, data: dict[str, Any]) -> discord.Embed:
        name = _mapping(data.get('name'))
        image = _mapping(data.get('image'))
        date_of_birth = _mapping(data.get('dateOfBirth'))
        media = _mapping(data.get('media'))

        embed = AniListEmbed(
            title=format_name(name.get('full'), name.get('native')),
            description=sanitize_description(data.get('description'), 1000),
            color=helpers.Colour.white(),
            url=data.get('siteUrl'),
        )

        if image.get('large'):
            embed.set_thumbnail(url=image.get('large'))

        birthday = format_date(
            year=date_of_birth.get('year'),
            month=date_of_birth.get('month'),
            day=date_of_birth.get('day'),
        )

        embed.add_field(name='Birthday', value=birthday, inline=True)
        embed.add_field(name='Age', value=data.get('age', 'N/A'), inline=True)
        embed.add_field(name='Gender', value=data.get('gender', 'N/A'), inline=True)

        if synonyms := [f'`{i}`' for i in name.get('alternative', [])] + [
            f'||`{i}`||' for i in name.get('alternativeSpoiler', [])
        ]:
            embed.add_field(name='Synonyms', value=', '.join(synonyms), inline=False)

        media_entries = []
        if media_entries := [
            f"[{_mapping(i.get('title')).get('romaji')}]({i.get('siteUrl')})"
            for i in media.get('nodes', [])
            if not i.get('isAdult')
        ]:
            embed.add_field(name='Popular Appearances', value=' | '.join(media_entries), inline=False)

        return embed

    @classmethod
    def user(cls, data: dict[str, Any]) -> discord.Embed:
        avatar = _mapping(data.get('avatar'))
        statistics = _mapping(data.get('statistics'))

        embed = AniListEmbed(
            title=f"{data.get('name')} (ID: {data.get('id')})",
            description=f"**About:**\n{data.get('about') or '*No description set.*'}",
            color=helpers.Colour.white(),
            url=data.get('siteUrl'),
        )
        embed.set_thumbnail(url=avatar.get('large'))
        embed.set_image(url=data.get('bannerImage'))

        if anime_stats := _mapping(statistics.get('anime')):
            embed.add_field(name='Anime Statistics',
                            value=f"**Total:** {anime_stats.get('count')}\n"
                                  f"**Minutes Watched:** {anime_stats.get('minutesWatched')}\n"
                                  f"**Episodes Watched:** {anime_stats.get('episodesWatched')}")

        if manga_stats := _mapping(statistics.get('manga')):
            embed.add_field(name='Manga Statistics',
                            value=f"**Total:** {manga_stats.get('count')}\n"
                                  f"**Volumes Read:** {manga_stats.get('volumesRead')}\n"
                                  f"**Chapters Read:** {manga_stats.get('chaptersRead')}")

        return embed

    @staticmethod
    def short_media(data: dict[str, Any]) -> discord.Embed:
        cover_image = _mapping(data.get('coverImage'))
        studios = _mapping(data.get('studios'))

        if data.get('type') == 'ANIME':
            studio_data = studios.get('nodes') or []
            studio = f"[{studio_data[0].get('name')}]({studio_data[0].get('siteUrl')})" if studio_data else 'N/A'

            description = (
                    f'**Status:** {format_anime_status(data.get('status'))}\n'
                    f'**Episodes:** {data.get('episodes', 'N/A')}\n'
                    f'**Studio:** {studio}\n'
                    f"**Score:** {str(data.get('meanScore')) + '%' if data.get('meanScore') is not None else 'N/A'}"
            )
        else:
            description = (
                f'**Status:** {format_manga_status(data.get('status'))}\n'
                f'**Chapters:** {data.get('chapters', 'N/A')}\n'
                f'**Volumes:** {data.get('volumes', 'N/A')}\n'
                f"**Score:** {str(data.get('meanScore')) + '%' if data.get('meanScore') is not None else 'N/A'}"
            )

        embed = AniListEmbed(
            title=_mapping(data.get('title')).get('romaji'),
            description=description,
            color=discord.Color.from_str(cover_image.get('color') or '#2b2d31'),
            url=data.get('siteUrl'),
        )
        embed.set_author(name=format_media_format(data.get('format')), icon_url=ANILIST_LOGO)

        if cover_image.get('large'):
            embed.set_thumbnail(url=cover_image.get('large'))

        return embed


def format_media_format(media_format: str | None) -> str:
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
    return formats.get(str(media_format), 'N/A')


def format_anime_status(media_status: str | None) -> str:
    statuses = {
        'FINISHED': 'Finished',
        'RELEASING': 'Airing',
        'NOT_YET_RELEASED': 'Not Yet Aired',
        'CANCELLED': 'Cancelled',
        'HIATUS': 'Paused',
    }
    return statuses.get(str(media_status), 'N/A')


def format_manga_status(media_status: str | None) -> str:
    statuses = {
        'FINISHED': 'Finished',
        'RELEASING': 'Publishing',
        'NOT_YET_RELEASED': 'Not Yet Published',
        'CANCELLED': 'Cancelled',
        'HIATUS': 'Paused',
    }
    return statuses.get(str(media_status), 'N/A')


def format_media_source(media_source: str | None) -> str:
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


def format_media_title(romaji: str | None, english: str | None) -> str | None:
    if english is None or english == romaji:
        return romaji
    else:
        return f'{romaji} ({english})'


def clean_html(text: str) -> str:
    return re.sub('<.*?>', '', text)


def sanitize_description(description: str | None, length: int) -> str:
    if description is None:
        return 'N/A'

    sanitized = clean_html(description).replace('**', '').replace('__', '').replace('~!', '||').replace('!~', '||')

    if len(sanitized) > length:
        sanitized = sanitized[0:length]

        if sanitized.count('||') % 2 != 0:
            return sanitized + '...||'

        return sanitized + '...'
    return sanitized


def format_date(**kwargs: int | None) -> str:
    filtered = {k: v for k, v in kwargs.items() if v is not None}
    if not filtered:
        return 'N/A'
    try:
        date = datetime.date(**filtered)
    except TypeError:
        parts: list[str] = []
        if year := filtered.get('year'):
            parts.append(str(year))
        if month := filtered.get('month'):
            parts.append(calendar.month_name[month])
        if day := filtered.get('day'):
            parts.append(str(day))
        return ' '.join(parts) if parts else 'N/A'
    if date:
        days = (date - datetime.date.today()).days
        timestamp = datetime.datetime.now() + datetime.timedelta(days=abs(days) if days > 0 else days)
        return discord.utils.format_dt(timestamp, style='D')
    else:
        return 'N/A'


def format_name(full: str | None, native: str | None) -> str | None:
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


class EnterCodeModal(discord.ui.Modal, title='Enter AniList Code'):
    code = discord.ui.TextInput(
        label='Code', placeholder='e.g. def50202deda18...',
        max_length=4000, min_length=1, style=discord.TextStyle.long)

    def __init__(self, bot: Bot) -> None:
        self.bot: Bot = bot
        super().__init__(timeout=90.0)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog: AniList | None = self.bot.get_cog('AniList')  # type: ignore
        if cog is None or not anilist:
            return

        access = await cog.get_access_token(self.code.value)
        if access is None:
            return await interaction.response.send_message(
                f'{Emojis.error} Invalid code provided.', ephemeral=True
            )

        await cog.access_tokens.set(interaction.user.id, access[0], access[1])

        await interaction.response.send_message(
            f'{Emojis.success} Successfully linked profile.', ephemeral=True)

        self.stop()
        with suppress(discord.HTTPException):
            await interaction.message.delete()


class AniListLinkView(View):
    def __init__(self, ctx: Context | discord.Interaction, url: str) -> None:
        super().__init__(timeout=100.0)
        self.ctx: Context | discord.Interaction = ctx

        self.add_item(discord.ui.Button(label="Link AniList", style=discord.ButtonStyle.link, url=url))

    @discord.ui.button(label="Enter Code", style=discord.ButtonStyle.green)
    async def enter_code(self, interaction: discord.Interaction, _) -> None:
        await interaction.response.send_modal(EnterCodeModal(self.ctx.client))
