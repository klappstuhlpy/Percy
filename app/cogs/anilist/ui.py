from __future__ import annotations

import calendar
import datetime
import re
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import discord

from app.core import Bot, Context, LayoutView
from app.utils import helpers
from config import Emojis, anilist

if TYPE_CHECKING:
    import aiohttp

    from .cog import AniList

ANILIST_LOGO = 'https://klappstuhl.me/gallery/raw/ufXiq.png'
ANILIST_ICON = 'https://klappstuhl.me/gallery/raw/sngjJ.png'

ANILIST_BLUE = discord.Colour.from_str('#02A9FF')


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


# ─── Legacy Embed (kept for paginated search results) ────────────────────────


class AniListEmbed(discord.Embed):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.set_footer(text='Provided by AniList', icon_url=ANILIST_LOGO)


# ─── Components V2 Card Builders ─────────────────────────────────────────────


class AniListCardBuilder:
    """Builds Components V2 cards for AniList data."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session: aiohttp.ClientSession = session

    def media_card(self, data: dict[str, Any]) -> discord.ui.Container:
        title = _mapping(data.get('title'))
        cover_image = _mapping(data.get('coverImage'))
        next_episode = _mapping(data.get('nextAiringEpisode'))
        start_date = _mapping(data.get('startDate'))
        end_date = _mapping(data.get('endDate'))
        studios = _mapping(data.get('studios'))

        colour = discord.Colour.from_str(cover_image.get('color') or '#2b2d31')
        container = discord.ui.Container(accent_colour=colour)

        media_title = format_media_title(title.get('romaji'), title.get('english'))
        heading = f'## {media_title}'
        fmt = format_media_format(data.get('format'))
        if fmt != 'N/A':
            heading = f'-# {fmt}\n{heading}'

        if cover_image.get('large'):
            container.add_item(
                discord.ui.Section(heading, accessory=discord.ui.Thumbnail(cover_image['large']))
            )
        else:
            container.add_item(discord.ui.TextDisplay(heading))

        desc = sanitize_description(data.get('description'), 300)
        if desc != 'N/A':
            container.add_item(discord.ui.TextDisplay(desc))

        container.add_item(discord.ui.Separator())

        # Stats line
        fields: list[str] = []

        if data.get('type') == 'ANIME':
            if data.get('status') == 'RELEASING' and next_episode:
                aired = next_episode.get('episode', 1) - 1
                total = data.get('episodes')
                ep_text = f'{aired}/{total}' if total else str(aired)
                if next_episode.get('airingAt'):
                    ts = discord.utils.format_dt(
                        datetime.datetime.fromtimestamp(float(next_episode['airingAt'])), 'R'
                    )
                    fields.append(f'**Episodes:** {ep_text} (next {ts})')
                else:
                    fields.append(f'**Episodes:** {ep_text}')
            else:
                fields.append(f"**Episodes:** {data.get('episodes', 'N/A')}")
        else:
            fields.append(f"**Chapters:** {data.get('chapters', 'N/A')}")
            fields.append(f"**Volumes:** {data.get('volumes', 'N/A')}")

        if data.get('type') == 'ANIME':
            status = format_anime_status(data.get('status'))
        else:
            status = format_manga_status(data.get('status'))
        fields.append(f'**Status:** {status}')

        score = f"{data.get('meanScore')}%" if data.get('meanScore') is not None else 'N/A'
        fields.append(f'**Score:** {score}')

        if data.get('type') == 'ANIME':
            studio_data = studios.get('nodes') or []
            if studio_data:
                fields.append(f"**Studio:** {studio_data[0].get('name', 'N/A')}")

        container.add_item(discord.ui.TextDisplay('\n'.join(fields)))

        # Running dates
        start = format_date(year=start_date.get('year'), month=start_date.get('month'), day=start_date.get('day'))
        end = format_date(year=end_date.get('year'), month=end_date.get('month'), day=end_date.get('day'))
        if data.get('status') == 'RELEASING':
            end = 'Present'
        container.add_item(discord.ui.TextDisplay(f'**Aired:** {start} — {end}'))

        # Genres
        if data.get('genres'):
            container.add_item(discord.ui.TextDisplay(f"**Genres:** {', '.join(data['genres'])}"))

        # Popularity
        pop_parts = []
        if data.get('popularity'):
            pop_parts.append(f"{data['popularity']:,} users")
        if data.get('favourites'):
            pop_parts.append(f"❤ {data['favourites']:,}")
        if pop_parts:
            container.add_item(discord.ui.TextDisplay(f"**Popularity:** {' • '.join(pop_parts)}"))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('-# Provided by AniList'))

        return container

    def character_card(self, data: dict[str, Any]) -> discord.ui.Container:
        name = _mapping(data.get('name'))
        image = _mapping(data.get('image'))
        date_of_birth = _mapping(data.get('dateOfBirth'))
        media = _mapping(data.get('media'))

        container = discord.ui.Container(accent_colour=ANILIST_BLUE)

        char_name = format_name(name.get('full'), name.get('native')) or 'Unknown'
        heading = f'## {char_name}'

        if image.get('large'):
            container.add_item(
                discord.ui.Section(heading, accessory=discord.ui.Thumbnail(image['large']))
            )
        else:
            container.add_item(discord.ui.TextDisplay(heading))

        desc = sanitize_description(data.get('description'), 600)
        if desc != 'N/A':
            container.add_item(discord.ui.TextDisplay(desc))

        container.add_item(discord.ui.Separator())

        # Info fields
        info: list[str] = []
        birthday = format_date(
            year=date_of_birth.get('year'), month=date_of_birth.get('month'), day=date_of_birth.get('day'),
        )
        if birthday != 'N/A':
            info.append(f'**Birthday:** {birthday}')
        if data.get('age'):
            info.append(f"**Age:** {data['age']}")
        if data.get('gender'):
            info.append(f"**Gender:** {data['gender']}")

        if info:
            container.add_item(discord.ui.TextDisplay('\n'.join(info)))

        if synonyms := [f'`{i}`' for i in name.get('alternative', [])] + [
            f'||`{i}`||' for i in name.get('alternativeSpoiler', [])
        ]:
            container.add_item(discord.ui.TextDisplay(f"**Synonyms:** {', '.join(synonyms)}"))

        media_entries: list[str] = [
            title_romaji
            for i in media.get('nodes', [])
            if not i.get('isAdult') and (title_romaji := _mapping(i.get('title')).get('romaji'))
        ]
        if media_entries:
            container.add_item(discord.ui.TextDisplay(f"**Appears in:** {' • '.join(media_entries)}"))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('-# Provided by AniList'))

        return container

    def user_card(self, data: dict[str, Any]) -> discord.ui.Container:
        avatar = _mapping(data.get('avatar'))
        statistics = _mapping(data.get('statistics'))

        container = discord.ui.Container(accent_colour=ANILIST_BLUE)

        heading = f"## {data.get('name')} (ID: {data.get('id')})"

        if avatar.get('large'):
            container.add_item(
                discord.ui.Section(heading, accessory=discord.ui.Thumbnail(avatar['large']))
            )
        else:
            container.add_item(discord.ui.TextDisplay(heading))

        about = data.get('about')
        if about:
            container.add_item(discord.ui.TextDisplay(sanitize_description(about, 400)))

        if data.get('bannerImage'):
            container.add_item(discord.ui.MediaGallery(discord.ui.MediaGalleryItem(data['bannerImage'])))

        container.add_item(discord.ui.Separator())

        if anime_stats := _mapping(statistics.get('anime')):
            days = anime_stats.get('minutesWatched', 0) / 1440
            lines = [
                f"**Anime** — {anime_stats.get('count', 0)} titles",
                f"  {anime_stats.get('episodesWatched', 0):,} episodes • {days:.1f} days watched",
            ]
            if anime_stats.get('meanScore'):
                lines.append(f"  Mean score: {anime_stats['meanScore']:.1f}")
            container.add_item(discord.ui.TextDisplay('\n'.join(lines)))

        if manga_stats := _mapping(statistics.get('manga')):
            lines = [
                f"**Manga** — {manga_stats.get('count', 0)} titles",
                f"  {manga_stats.get('chaptersRead', 0):,} chapters • {manga_stats.get('volumesRead', 0):,} volumes",
            ]
            if manga_stats.get('meanScore'):
                lines.append(f"  Mean score: {manga_stats['meanScore']:.1f}")
            container.add_item(discord.ui.TextDisplay('\n'.join(lines)))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('-# Provided by AniList'))

        return container

    def favourites_card(self, data: dict[str, Any]) -> discord.ui.Container:
        favourites = _mapping(data.get('favourites'))

        container = discord.ui.Container(accent_colour=ANILIST_BLUE)
        container.add_item(discord.ui.TextDisplay(f"## {data.get('name')}'s Favourites"))
        container.add_item(discord.ui.Separator())

        anime_nodes = _mapping(favourites.get('anime')).get('nodes') or []
        if anime_nodes:
            lines = ['**Favourite Anime**']
            for i, entry in enumerate(anime_nodes[:10], 1):
                title = _mapping(entry.get('title')).get('romaji', '?')
                score = f" • {entry['meanScore']}%" if entry.get('meanScore') else ''
                lines.append(f'{i}. {title}{score}')
            container.add_item(discord.ui.TextDisplay('\n'.join(lines)))

        manga_nodes = _mapping(favourites.get('manga')).get('nodes') or []
        if manga_nodes:
            lines = ['**Favourite Manga**']
            for i, entry in enumerate(manga_nodes[:10], 1):
                title = _mapping(entry.get('title')).get('romaji', '?')
                score = f" • {entry['meanScore']}%" if entry.get('meanScore') else ''
                lines.append(f'{i}. {title}{score}')
            container.add_item(discord.ui.TextDisplay('\n'.join(lines)))

        char_nodes = _mapping(favourites.get('characters')).get('nodes') or []
        if char_nodes:
            lines = ['**Favourite Characters**']
            for i, entry in enumerate(char_nodes[:10], 1):
                name = _mapping(entry.get('name')).get('full', '?')
                lines.append(f'{i}. {name}')
            container.add_item(discord.ui.TextDisplay('\n'.join(lines)))

        if not anime_nodes and not manga_nodes and not char_nodes:
            container.add_item(discord.ui.TextDisplay('*No favourites set.*'))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('-# Provided by AniList'))

        return container

    def media_list_card(self, entries: list[dict[str, Any]], *, list_type: str, status: str) -> discord.ui.Container:
        container = discord.ui.Container(accent_colour=ANILIST_BLUE)
        status_label = status.replace('_', ' ').title()
        container.add_item(discord.ui.TextDisplay(f'## {list_type} — {status_label}'))
        container.add_item(discord.ui.Separator())

        if not entries:
            container.add_item(discord.ui.TextDisplay('*No entries found.*'))
        else:
            lines: list[str] = []
            for entry in entries[:20]:
                media = _mapping(entry.get('media'))
                title = _mapping(media.get('title')).get('romaji', '?')
                progress = entry.get('progress', 0)

                if media.get('type') == 'ANIME':
                    total = media.get('episodes') or '?'
                    prog_text = f'{progress}/{total} ep'
                else:
                    total = media.get('chapters') or '?'
                    prog_text = f'{progress}/{total} ch'

                score = f' • {entry["score"]}/10' if entry.get('score') else ''
                lines.append(f'• **{title}** — {prog_text}{score}')

            container.add_item(discord.ui.TextDisplay('\n'.join(lines)))

            if len(entries) > 20:
                container.add_item(discord.ui.TextDisplay(f'-# ...and {len(entries) - 20} more'))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('-# Provided by AniList'))

        return container

    def short_media_card(self, data: dict[str, Any]) -> discord.ui.Container:
        cover_image = _mapping(data.get('coverImage'))
        studios = _mapping(data.get('studios'))

        colour = discord.Colour.from_str(cover_image.get('color') or '#2b2d31')
        container = discord.ui.Container(accent_colour=colour)

        title = _mapping(data.get('title')).get('romaji', '?')
        fmt = format_media_format(data.get('format'))
        heading = f'-# {fmt}\n## {title}'

        if cover_image.get('large'):
            container.add_item(
                discord.ui.Section(heading, accessory=discord.ui.Thumbnail(cover_image['large']))
            )
        else:
            container.add_item(discord.ui.TextDisplay(heading))

        fields: list[str] = []
        if data.get('type') == 'ANIME':
            studio_data = studios.get('nodes') or []
            studio = studio_data[0].get('name') if studio_data else 'N/A'
            fields.append(f'**Status:** {format_anime_status(data.get("status"))}')
            fields.append(f"**Episodes:** {data.get('episodes', 'N/A')}")
            fields.append(f'**Studio:** {studio}')
        else:
            fields.append(f'**Status:** {format_manga_status(data.get("status"))}')
            fields.append(f"**Chapters:** {data.get('chapters', 'N/A')}")
            fields.append(f"**Volumes:** {data.get('volumes', 'N/A')}")

        score = f"{data.get('meanScore')}%" if data.get('meanScore') is not None else 'N/A'
        fields.append(f'**Score:** {score}')

        container.add_item(discord.ui.TextDisplay('\n'.join(fields)))

        return container


# ─── Legacy Embed Builder (kept for paginated results) ───────────────────────


class AniListEmbedBuilder:
    """Kept for compatibility with EmbedPaginator-driven search results."""

    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session: aiohttp.ClientSession = session

    async def media(self, data: dict[str, Any]) -> discord.Embed:
        title = _mapping(data.get('title'))
        cover_image = _mapping(data.get('coverImage'))
        next_episode = _mapping(data.get('nextAiringEpisode'))
        start_date = _mapping(data.get('startDate'))
        end_date = _mapping(data.get('endDate'))
        studios = _mapping(data.get('studios'))

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
                            datetime.datetime.fromtimestamp(float(next_episode.get('airingAt'))), 'R'
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

        start = format_date(year=start_date.get('year'), month=start_date.get('month'), day=start_date.get('day'))
        end = format_date(year=end_date.get('year'), month=end_date.get('month'), day=end_date.get('day'))
        end = 'Present' if data.get('status') == 'RELEASING' else end
        embed.add_field(name='Running', value=start + ' - ' + end, inline=False)

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

        embed.add_field(
            name='Score', value=f"{data.get('meanScore')}%" if data.get('meanScore') is not None else 'N/A', inline=True
        )
        embed.add_field(name='Popularity', value=data.get('popularity', 'N/A'), inline=True)
        embed.add_field(name='Favourites', value=data.get('favourites', 'N/A'), inline=True)

        if data.get('genres'):
            embed.add_field(name='Genres', value=', '.join(
                [f"[`{i}`](https://anilist.co/search/anime/{i.strip().replace(' ', '%20')})" for i in data['genres']]
            ), inline=False)

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
            year=date_of_birth.get('year'), month=date_of_birth.get('month'), day=date_of_birth.get('day'),
        )

        embed.add_field(name='Birthday', value=birthday, inline=True)
        embed.add_field(name='Age', value=data.get('age', 'N/A'), inline=True)
        embed.add_field(name='Gender', value=data.get('gender', 'N/A'), inline=True)

        if synonyms := [f'`{i}`' for i in name.get('alternative', [])] + [
            f'||`{i}`||' for i in name.get('alternativeSpoiler', [])
        ]:
            embed.add_field(name='Synonyms', value=', '.join(synonyms), inline=False)

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
                f'**Status:** {format_anime_status(data.get("status"))}\n'
                f'**Episodes:** {data.get("episodes", "N/A")}\n'
                f'**Studio:** {studio}\n'
                f"**Score:** {str(data.get('meanScore')) + '%' if data.get('meanScore') is not None else 'N/A'}"
            )
        else:
            description = (
                f'**Status:** {format_manga_status(data.get("status"))}\n'
                f'**Chapters:** {data.get("chapters", "N/A")}\n'
                f'**Volumes:** {data.get("volumes", "N/A")}\n'
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


# ─── Interactive Views ────────────────────────────────────────────────────────


class MediaCardView(LayoutView):
    """CV2 view for a media search result with interactive buttons for linked users."""

    def __init__(
        self, container: discord.ui.Container, *, media_data: dict[str, Any],
        cog: AniList, user_id: int,
    ) -> None:
        super().__init__(timeout=180.0, members=discord.Object(user_id))
        self.media_data = media_data
        self.cog = cog
        self.user_id = user_id

        self.add_item(container)

        is_linked = self.cog.is_user_linked(user_id)
        media_type = media_data.get('type', 'ANIME')
        action_row = discord.ui.ActionRow()

        if is_linked:
            action_row.add_item(discord.ui.Button(
                label='Add to List', style=discord.ButtonStyle.primary, custom_id='anilist:add',
            ))
            if media_type == 'ANIME':
                action_row.add_item(discord.ui.Button(
                    label='+1 Episode', style=discord.ButtonStyle.secondary, custom_id='anilist:progress',
                ))
            else:
                action_row.add_item(discord.ui.Button(
                    label='+1 Chapter', style=discord.ButtonStyle.secondary, custom_id='anilist:progress',
                ))
            action_row.add_item(discord.ui.Button(
                label='Set Score', style=discord.ButtonStyle.secondary, custom_id='anilist:score',
            ))
            action_row.add_item(discord.ui.Button(
                label='\N{WHITE HEART} Favourite', style=discord.ButtonStyle.secondary, custom_id='anilist:fav',
            ))

        if data_url := media_data.get('siteUrl'):
            action_row.add_item(discord.ui.Button(
                label='AniList', style=discord.ButtonStyle.link, url=data_url,
            ))

        if action_row.children:
            self.add_item(action_row)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id != self.user_id:
            await interaction.response.send_message('This is not your search result.', ephemeral=True)
            return False
        return True

    async def _handle_button(self, interaction: discord.Interaction) -> None:
        custom_id = interaction.data.get('custom_id', '') if interaction.data else ''

        if custom_id == 'anilist:add':
            await self._add_to_list(interaction)
        elif custom_id == 'anilist:progress':
            await self._increment_progress(interaction)
        elif custom_id == 'anilist:score':
            await interaction.response.send_modal(SetScoreModal(self.cog, self.media_data, self.user_id))
        elif custom_id == 'anilist:fav':
            await self._toggle_favourite(interaction)

    async def _add_to_list(self, interaction: discord.Interaction) -> None:
        media_id = self.media_data.get('id')
        if not media_id:
            return

        headers = await self.cog.bearer_headers(self.user_id)
        if not headers:
            await interaction.response.send_message(
                f'{Emojis.error} Your AniList session has expired. Use `/anilist link` to reconnect.',
                ephemeral=True,
            )
            return

        status = 'CURRENT'
        result = await self.cog.aniclient.save_media_list_entry(
            media_id=media_id, status=status, headers=headers,
        )

        if result:
            title = _mapping(self.media_data.get('title')).get('romaji', 'this title')
            await interaction.response.send_message(
                f'{Emojis.success} Added **{title}** to your list.', ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f'{Emojis.error} Failed to add to your list.', ephemeral=True,
            )

    async def _increment_progress(self, interaction: discord.Interaction) -> None:
        media_id = self.media_data.get('id')
        if not media_id:
            return

        headers = await self.cog.bearer_headers(self.user_id)
        if not headers:
            await interaction.response.send_message(
                f'{Emojis.error} Your AniList session has expired. Use `/anilist link` to reconnect.',
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        # First save with status=CURRENT to ensure the entry exists and get current state
        current = await self.cog.aniclient.save_media_list_entry(
            media_id=media_id, status='CURRENT', headers=headers,
        )
        current_progress = current.get('progress', 0) if current else 0

        # Now increment by 1
        result = await self.cog.aniclient.save_media_list_entry(
            media_id=media_id, progress=current_progress + 1, headers=headers,
        )

        if result:
            new_progress = result.get('progress', '?')
            title = _mapping(self.media_data.get('title')).get('romaji', 'this title')
            media = _mapping(result.get('media'))
            total = media.get('episodes') or media.get('chapters') or '?'
            await interaction.followup.send(
                f'{Emojis.success} **{title}** progress: {new_progress}/{total}', ephemeral=True,
            )
        else:
            await interaction.followup.send(
                f'{Emojis.error} Failed to update progress.', ephemeral=True,
            )

    async def _toggle_favourite(self, interaction: discord.Interaction) -> None:
        media_id = self.media_data.get('id')
        if not media_id:
            return

        headers = await self.cog.bearer_headers(self.user_id)
        if not headers:
            await interaction.response.send_message(
                f'{Emojis.error} Your AniList session has expired. Use `/anilist link` to reconnect.',
                ephemeral=True,
            )
            return

        media_type = self.media_data.get('type', 'ANIME')
        success = await self.cog.aniclient.toggle_favourite(
            media_id=media_id, media_type=media_type, headers=headers,
        )

        if success:
            title = _mapping(self.media_data.get('title')).get('romaji', 'this title')
            await interaction.response.send_message(
                f'{Emojis.success} Toggled **{title}** as favourite.', ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f'{Emojis.error} Failed to toggle favourite.', ephemeral=True,
            )

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type == discord.InteractionType.component:
            await self._handle_button(interaction)


class SetScoreModal(discord.ui.Modal, title='Set Score'):
    score_input = discord.ui.TextInput(
        label='Score (1-10)', placeholder='e.g. 8', max_length=4, min_length=1,
    )

    def __init__(self, cog: AniList, media_data: dict[str, Any], user_id: int) -> None:
        self.cog = cog
        self.media_data = media_data
        self.user_id = user_id
        super().__init__(timeout=60.0)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            score = float(self.score_input.value)
            if not 1 <= score <= 10:
                raise ValueError
        except ValueError:
            await interaction.response.send_message(
                f'{Emojis.error} Score must be a number between 1 and 10.', ephemeral=True,
            )
            return

        media_id = self.media_data.get('id')
        if not media_id:
            return

        headers = await self.cog.bearer_headers(self.user_id)
        if not headers:
            await interaction.response.send_message(
                f'{Emojis.error} Your AniList session has expired.', ephemeral=True,
            )
            return

        result = await self.cog.aniclient.save_media_list_entry(
            media_id=media_id, score=score, headers=headers,
        )

        if result:
            title = _mapping(self.media_data.get('title')).get('romaji', 'this title')
            await interaction.response.send_message(
                f'{Emojis.success} Set **{title}** score to **{score}/10**.', ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f'{Emojis.error} Failed to set score.', ephemeral=True,
            )


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

        await cog.store_token(interaction.user.id, access[0], access[1])

        await interaction.response.send_message(
            f'{Emojis.success} Successfully linked profile.', ephemeral=True)

        self.stop()
        with suppress(discord.HTTPException):
            await interaction.message.delete()


class AniListLinkView(LayoutView):
    def __init__(self, ctx: Context | discord.Interaction, url: str, *, content: str | None = None) -> None:
        super().__init__(timeout=100.0)
        self.ctx: Context | discord.Interaction = ctx

        link_btn = discord.ui.Button(label="Link AniList", style=discord.ButtonStyle.link, url=url)
        code_btn = discord.ui.Button(label="Enter Code", style=discord.ButtonStyle.green)
        code_btn.callback = self._enter_code  # type: ignore[assignment]

        container = discord.ui.Container(accent_colour=helpers.Colour.brand())
        if content:
            container.add_item(discord.ui.TextDisplay(content))
            container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(link_btn, code_btn))
        self.add_item(container)

    async def _enter_code(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(EnterCodeModal(self.ctx.client))


# ─── Formatting Helpers ───────────────────────────────────────────────────────


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
    return sources.get(str(media_source), 'N/A')


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
