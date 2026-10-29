from __future__ import annotations

import calendar
import datetime
import re
from typing import TYPE_CHECKING, Any, Literal, Self

import discord

from app.core.pagination import BasePaginatorV2
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.clients import AniListClient
    from app.core import Bot, Context

ANILIST_BLUE = discord.Colour.from_str('#02A9FF')

Kind = Literal['media', 'airing', 'character', 'staff', 'studio']


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _footer() -> discord.ui.TextDisplay:
    return discord.ui.TextDisplay('-# Provided by AniList')


def _heading(container: discord.ui.Container, heading: str, image: str | None) -> None:
    if image:
        container.add_item(discord.ui.Section(heading, accessory=discord.ui.Thumbnail(image)))
    else:
        container.add_item(discord.ui.TextDisplay(heading))


def _titles(nodes: list[dict[str, Any]], limit: int) -> str:
    titles = [title for n in nodes if not n.get('isAdult') and (title := media_title(n)) != '?']
    return ' • '.join(titles[:limit])


# ─── Pure formatting ─────────────────────────────────────────────────────────


def media_title(data: dict[str, Any]) -> str:
    title = _mapping(data.get('title'))
    return title.get('romaji') or title.get('english') or '?'


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


def format_status(media_type: str | None, media_status: str | None) -> str:
    airing = media_type == 'ANIME'
    statuses = {
        'FINISHED': 'Finished',
        'RELEASING': 'Airing' if airing else 'Publishing',
        'NOT_YET_RELEASED': 'Not Yet Aired' if airing else 'Not Yet Published',
        'CANCELLED': 'Cancelled',
        'HIATUS': 'Paused',
    }
    return statuses.get(str(media_status), 'N/A')


def format_enum(value: str | None) -> str:
    """``LIGHT_NOVEL`` -> ``Light Novel``; AniList's enums are all upper snake case."""
    return value.replace('_', ' ').title() if value else 'N/A'


def format_media_title(romaji: str | None, english: str | None) -> str | None:
    if english is None or english == romaji:
        return romaji
    return f'{romaji} ({english})'


def format_name(full: str | None, native: str | None) -> str | None:
    if full is None or full == native:
        return native
    if native is None:
        return full
    return f'{full} ({native})'


def sanitize_description(description: str | None, length: int) -> str:
    if description is None:
        return 'N/A'

    sanitized = re.sub('<.*?>', '', description)
    sanitized = sanitized.replace('**', '').replace('__', '').replace('~!', '||').replace('!~', '||').strip()

    if len(sanitized) > length:
        sanitized = sanitized[:length]
        # A cut inside an AniList spoiler would leave Discord's ``||`` open and hide the rest.
        if sanitized.count('||') % 2 != 0:
            return sanitized + '...||'
        return sanitized + '...'
    return sanitized


def format_date(*, year: int | None = None, month: int | None = None, day: int | None = None) -> str:
    """A full date as a Discord timestamp; a partial one (AniList often has only a year) as text."""
    if year and month and day:
        try:
            date = datetime.datetime(year, month, day, tzinfo=datetime.UTC)
        except ValueError:
            pass
        else:
            return discord.utils.format_dt(date, style='D')

    parts: list[str] = []
    if month:
        parts.append(calendar.month_name[month])
    if day:
        parts.append(str(day))
    if year:
        parts.append(str(year))
    return ' '.join(parts) or 'N/A'


def month_to_season(month: int) -> str:
    return ('WINTER', 'SPRING', 'SUMMER', 'FALL')[(month - 1) // 3]


def related_media(kind: Kind, data: dict[str, Any]) -> list[tuple[str, str, int]]:
    """``(label, description, media id)`` for every non-adult media a card links to, deduped, at most 25.

    A media card links its relations (prequel, sequel, ...) and then AniList's top recommendations;
    the person and studio cards link the works they are known for.
    """
    found: list[tuple[str, dict[str, Any]]] = []
    if kind in ('media', 'airing'):
        found.extend(
            (format_enum(edge.get('relationType')), _mapping(edge.get('node')))
            for edge in _mapping(data.get('relations')).get('edges') or []
        )
        found.extend(
            ('Recommended', _mapping(_mapping(node).get('mediaRecommendation')))
            for node in _mapping(data.get('recommendations')).get('nodes') or []
        )
    else:
        key = {'character': 'media', 'staff': 'staffMedia', 'studio': 'media'}[kind]
        found.extend(('Known for', _mapping(node)) for node in _mapping(data.get(key)).get('nodes') or [])

    options: list[tuple[str, str, int]] = []
    seen: set[int] = set()
    for reason, node in found:
        media_id = node.get('id')
        if not media_id or node.get('isAdult') or media_id in seen:
            continue
        if kind in ('media', 'airing') and media_id == data.get('id'):
            continue
        seen.add(media_id)
        description = f'{reason} · {format_media_format(node.get("format"))}'
        options.append((media_title(node)[:100], description[:100], int(media_id)))
    return options[:25]


def follow_target(kind: Kind, data: dict[str, Any]) -> tuple[str, str, str] | None:
    """The ``(subject_type, subject_value, label)`` a card's Follow button subscribes to, if any.

    Only what can still produce an episode is followable: an anime that is airing, upcoming or on
    hiatus (a finished show would be a subscription that never fires), and any studio. The keys
    are AniList ids -- the same ones :func:`app.services.releases.anime_releasable` derives.
    """
    if kind == 'studio' and data.get('id'):
        return 'studio', str(data['id']), str(data.get('name') or data['id'])
    if (
        kind in ('media', 'airing') and data.get('id') and data.get('type') == 'ANIME'
        and data.get('status') in ('RELEASING', 'NOT_YET_RELEASED', 'HIATUS')
    ):
        return 'series', str(data['id']), media_title(data)
    return None


# ─── List rows ───────────────────────────────────────────────────────────────


def media_line(data: dict[str, Any]) -> str:
    parts = [f'**{media_title(data)}**', format_media_format(data.get('format'))]
    if year := _mapping(data.get('startDate')).get('year'):
        parts.append(str(year))
    if data.get('meanScore') is not None:
        parts.append(f"{data['meanScore']}%")
    return ' · '.join(parts)


def airing_line(data: dict[str, Any]) -> str:
    when = discord.utils.format_dt(datetime.datetime.fromtimestamp(data['airingAt'], datetime.UTC), 'f')
    return f"{when} **{media_title(_mapping(data.get('media')))}** — Ep {data.get('episode', '?')}"


def person_line(data: dict[str, Any]) -> str:
    name = format_name(_mapping(data.get('name')).get('full'), _mapping(data.get('name')).get('native')) or '?'
    if occupations := data.get('primaryOccupations'):
        return f"**{name}** · {', '.join(occupations[:2])}"
    if known := _titles(_mapping(data.get('media')).get('nodes') or [], 1):
        return f'**{name}** · {known}'
    return f'**{name}**'


def studio_line(data: dict[str, Any]) -> str:
    if known := _titles(_mapping(data.get('media')).get('nodes') or [], 2):
        return f"**{data.get('name', '?')}** · {known}"
    return f"**{data.get('name', '?')}**"


def entry_label(kind: Kind, data: dict[str, Any]) -> str:
    match kind:
        case 'media':
            return media_title(data)
        case 'airing':
            return f"{media_title(_mapping(data.get('media')))} — Ep {data.get('episode', '?')}"
        case 'studio':
            return str(data.get('name', '?'))
        case _:
            return _mapping(data.get('name')).get('full') or '?'


LINES: dict[Kind, Callable[[dict[str, Any]], str]] = {
    'media': media_line,
    'airing': airing_line,
    'character': person_line,
    'staff': person_line,
    'studio': studio_line,
}


# ─── Cards ───────────────────────────────────────────────────────────────────


def media_card(data: dict[str, Any]) -> discord.ui.Container:
    cover = _mapping(data.get('coverImage'))
    next_episode = _mapping(data.get('nextAiringEpisode'))
    start, end = _mapping(data.get('startDate')), _mapping(data.get('endDate'))
    title = _mapping(data.get('title'))
    is_anime = data.get('type') == 'ANIME'

    container = discord.ui.Container(accent_colour=discord.Colour.from_str(cover.get('color') or '#2b2d31'))
    heading = f"## {format_media_title(title.get('romaji'), title.get('english')) or '?'}"
    if (fmt := format_media_format(data.get('format'))) != 'N/A':
        heading = f'-# {fmt}\n{heading}'
    _heading(container, heading, cover.get('large'))

    if (desc := sanitize_description(data.get('description'), 400)) != 'N/A':
        container.add_item(discord.ui.TextDisplay(desc))
    container.add_item(discord.ui.Separator())

    fields: list[str] = []
    if is_anime:
        if data.get('status') == 'RELEASING' and next_episode.get('episode'):
            aired = f"{next_episode['episode'] - 1}/{data.get('episodes') or '?'}"
            if airing_at := next_episode.get('airingAt'):
                when = datetime.datetime.fromtimestamp(airing_at, datetime.UTC)
                aired += f' (next {discord.utils.format_dt(when, "R")})'
            fields.append(f'**Episodes:** {aired}')
        else:
            fields.append(f"**Episodes:** {data.get('episodes') or 'N/A'}")
        if data.get('duration'):
            fields.append(f"**Duration:** ~{data['duration']} min")
        if studios := _mapping(data.get('studios')).get('nodes'):
            fields.append(f"**Studio:** {studios[0].get('name', 'N/A')}")
    else:
        fields.append(f"**Chapters:** {data.get('chapters') or 'N/A'}")
        fields.append(f"**Volumes:** {data.get('volumes') or 'N/A'}")

    fields.append(f"**Status:** {format_status(data.get('type'), data.get('status'))}")
    fields.append(f"**Source:** {format_enum(data.get('source'))}")
    score = f"{data['meanScore']}%" if data.get('meanScore') is not None else 'N/A'
    fields.append(f'**Score:** {score}')

    end_text = 'Present' if data.get('status') == 'RELEASING' else format_date(**end)
    fields.append(f"**{'Aired' if is_anime else 'Published'}:** {format_date(**start)} — {end_text}")
    if data.get('genres'):
        fields.append(f"**Genres:** {', '.join(data['genres'])}")
    if data.get('popularity'):
        fields.append(f"**Popularity:** {data['popularity']:,} users • ❤ {data.get('favourites') or 0:,}")
    container.add_item(discord.ui.TextDisplay('\n'.join(fields)))

    container.add_item(discord.ui.Separator())
    container.add_item(_footer())
    return container


def character_card(data: dict[str, Any]) -> discord.ui.Container:
    name = _mapping(data.get('name'))
    container = discord.ui.Container(accent_colour=ANILIST_BLUE)
    _heading(container, f"## {format_name(name.get('full'), name.get('native')) or 'Unknown'}",
             _mapping(data.get('image')).get('large'))

    if (desc := sanitize_description(data.get('description'), 600)) != 'N/A':
        container.add_item(discord.ui.TextDisplay(desc))
    container.add_item(discord.ui.Separator())

    info: list[str] = []
    if (birthday := format_date(**_mapping(data.get('dateOfBirth')))) != 'N/A':
        info.append(f'**Birthday:** {birthday}')
    if data.get('age'):
        info.append(f"**Age:** {data['age']}")
    if data.get('gender'):
        info.append(f"**Gender:** {data['gender']}")
    synonyms = [f'`{i}`' for i in name.get('alternative') or [] if i]
    synonyms += [f'||`{i}`||' for i in name.get('alternativeSpoiler') or [] if i]
    if synonyms:
        info.append(f"**Synonyms:** {', '.join(synonyms)}")
    if appears := _titles(_mapping(data.get('media')).get('nodes') or [], 5):
        info.append(f'**Appears in:** {appears}')
    if data.get('favourites'):
        info.append(f"**Favourites:** ❤ {data['favourites']:,}")
    if info:
        container.add_item(discord.ui.TextDisplay('\n'.join(info)))

    container.add_item(discord.ui.Separator())
    container.add_item(_footer())
    return container


def staff_card(data: dict[str, Any]) -> discord.ui.Container:
    name = _mapping(data.get('name'))
    container = discord.ui.Container(accent_colour=ANILIST_BLUE)
    heading = f"## {format_name(name.get('full'), name.get('native')) or 'Unknown'}"
    if occupations := data.get('primaryOccupations'):
        heading = f"-# {', '.join(occupations)}\n{heading}"
    _heading(container, heading, _mapping(data.get('image')).get('large'))

    if (desc := sanitize_description(data.get('description'), 500)) != 'N/A':
        container.add_item(discord.ui.TextDisplay(desc))
    container.add_item(discord.ui.Separator())

    info: list[str] = []
    if data.get('languageV2'):
        info.append(f"**Language:** {data['languageV2']}")
    if (birthday := format_date(**_mapping(data.get('dateOfBirth')))) != 'N/A':
        info.append(f'**Birthday:** {birthday}')
    if data.get('age'):
        info.append(f"**Age:** {data['age']}")
    if data.get('gender'):
        info.append(f"**Gender:** {data['gender']}")
    if data.get('homeTown'):
        info.append(f"**Hometown:** {data['homeTown']}")
    if known := _titles(_mapping(data.get('staffMedia')).get('nodes') or [], 5):
        info.append(f'**Known for:** {known}')
    if data.get('favourites'):
        info.append(f"**Favourites:** ❤ {data['favourites']:,}")
    if info:
        container.add_item(discord.ui.TextDisplay('\n'.join(info)))

    container.add_item(discord.ui.Separator())
    container.add_item(_footer())
    return container


def studio_card(data: dict[str, Any]) -> discord.ui.Container:
    container = discord.ui.Container(accent_colour=ANILIST_BLUE)
    kind = 'Animation studio' if data.get('isAnimationStudio') else 'Studio'
    container.add_item(discord.ui.TextDisplay(f"-# {kind}\n## {data.get('name', 'Unknown')}"))
    container.add_item(discord.ui.Separator())

    lines: list[str] = []
    for node in _mapping(data.get('media')).get('nodes') or []:
        if node.get('isAdult'):
            continue
        year = _mapping(node.get('startDate')).get('year')
        suffix = f' ({year})' if year else ''
        lines.append(f"• **{media_title(node)}** · {format_media_format(node.get('format'))}{suffix}")
    container.add_item(discord.ui.TextDisplay('### Known for\n' + '\n'.join(lines) if lines else '*No titles listed.*'))
    if data.get('favourites'):
        container.add_item(discord.ui.TextDisplay(f"**Favourites:** ❤ {data['favourites']:,}"))

    container.add_item(discord.ui.Separator())
    container.add_item(_footer())
    return container


CARDS: dict[Kind, Callable[[dict[str, Any]], discord.ui.Container]] = {
    'media': media_card,
    'airing': media_card,
    'character': character_card,
    'staff': staff_card,
    'studio': studio_card,
}


# ─── Browser ─────────────────────────────────────────────────────────────────


class AniListBrowser(BasePaginatorV2[dict[str, Any]]):
    """One message that is either a paged result list or a single card, switching in place.

    A list row is opened from the page's select; a card's select opens a related media (its
    relations and recommendations, or a person's/studio's works), and Back walks that trail
    back to the list. One view owns the whole message, so there is exactly one timeout.
    ``client``, ``kind`` and ``heading`` ride in :attr:`extras`.
    """

    accent_colour = ANILIST_BLUE

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.current: tuple[Kind, dict[str, Any]] | None = None
        self.history: list[tuple[Kind, dict[str, Any]]] = []

    @property
    def client(self) -> AniListClient:
        return self.extras['client']

    @property
    def kind(self) -> Kind:
        return self.extras['kind']

    @classmethod
    async def browse(
        cls, ctx: Context, *, client: AniListClient, kind: Kind, heading: str,
        entries: list[dict[str, Any]], per_page: int = 10,
    ) -> Self:
        """Send ``entries`` as a list, or straight as a card when there is only one."""
        self = cls(entries=entries, per_page=per_page, members=ctx.author)
        self.extras.update(client=client, kind=kind, heading=heading)
        if len(entries) == 1:
            await self._open(entries[0])
        await self.build()
        self.message = await ctx.send(view=self, allowed_mentions=discord.AllowedMentions.none())
        return self

    async def _open(self, entry: dict[str, Any]) -> bool:
        """Show ``entry`` as a card. Media rows only carry list fields, so their card is fetched by id."""
        if self.kind in ('media', 'airing'):
            media_id = (_mapping(entry.get('media')) if self.kind == 'airing' else entry).get('id')
            data = await self.client.media(int(media_id)) if media_id else None
            if data is None:
                return False
            self.current = ('media', data)
        else:
            self.current = (self.kind, entry)
        return True

    async def format_page(self, entries: list[dict[str, Any]], /) -> list[discord.ui.Item[Any]]:
        line = LINES[self.kind]
        rows = '\n'.join(f'`{i}.` {line(e)}' for i, e in enumerate(entries, self.numerate_start))
        items: list[discord.ui.Item[Any]] = [
            discord.ui.TextDisplay(f"## {self.extras['heading']}"),
            discord.ui.Separator(),
            discord.ui.TextDisplay(rows or '*Nothing found.*'),
        ]
        if entries:
            select: discord.ui.Select[Any] = discord.ui.Select(placeholder='Open one…', options=[
                discord.SelectOption(label=f'{i}. {entry_label(self.kind, e)}'[:100], value=str(i - 1))
                for i, e in enumerate(entries, self.numerate_start)
            ])
            select.callback = self._open_row  # type: ignore[assignment]
            items.append(discord.ui.ActionRow(select))
        return items

    def footer_items(self) -> list[discord.ui.Item[Any]]:
        return [discord.ui.TextDisplay(f'-# Page {self.current_page}/{self.total_pages} • Provided by AniList')]

    async def build(self) -> None:
        if self.current is None:
            await super().build()
            return

        kind, data = self.current
        container = CARDS[kind](data)
        if options := related_media(kind, data):
            select: discord.ui.Select[Any] = discord.ui.Select(
                placeholder='Related and recommended…' if kind == 'media' else 'Known for…',
                options=[discord.SelectOption(label=label, description=desc, value=str(media_id))
                         for label, desc, media_id in options],
            )
            select.callback = self._open_related  # type: ignore[assignment]
            container.add_item(discord.ui.ActionRow(select))

        buttons: list[discord.ui.Button[Any]] = []
        if self.history or len(self.entries) > 1:
            back = discord.ui.Button(label='Back', style=discord.ButtonStyle.grey)
            back.callback = self._back  # type: ignore[assignment]
            buttons.append(back)
        if follow_target(kind, data):
            follow = discord.ui.Button(label='Follow', emoji='🔔', style=discord.ButtonStyle.primary)
            follow.callback = self._follow  # type: ignore[assignment]
            buttons.append(follow)
        if url := data.get('siteUrl'):
            buttons.append(discord.ui.Button(label='View on AniList', style=discord.ButtonStyle.link, url=url))
        if buttons:
            container.add_item(discord.ui.ActionRow(*buttons))

        self.clear_items()
        self.add_item(container)

    @staticmethod
    def _selected(interaction: discord.Interaction) -> int:
        return int(_mapping(interaction.data).get('values', ['0'])[0])

    async def _not_found(self, interaction: discord.Interaction) -> None:
        await interaction.followup.send(f'{Emojis.error} AniList did not return that entry.', ephemeral=True)

    async def _open_row(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        index = self._selected(interaction)
        if not 0 <= index < len(self.entries) or not await self._open(self.entries[index]):
            await self._not_found(interaction)
            return
        await self.refresh(interaction)

    async def _open_related(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        data = await self.client.media(self._selected(interaction))
        if data is None:
            await self._not_found(interaction)
            return
        if self.current is not None:
            self.history.append(self.current)
        self.current = ('media', data)
        await self.refresh(interaction)

    async def _follow(self, interaction: discord.Interaction) -> None:
        target = follow_target(*self.current) if self.current else None
        if target is None:
            return
        subject_type, value, label = target
        bot: Bot = interaction.client  # type: ignore[assignment]
        await bot.db.releases.subscribe(
            interaction.user.id, 'anime', subject_type, value,
            label=label, guild_id=interaction.guild_id,
        )
        what = 'new episodes air' if subject_type == 'series' else 'one of their shows airs a new episode'
        await interaction.response.send_message(
            f'{Emojis.success} Following **{discord.utils.escape_markdown(label)}** — I will DM you when {what}. '
            'Manage it with `/subscriptions`.',
            ephemeral=True,
        )

    async def _back(self, interaction: discord.Interaction) -> None:
        self.current = self.history.pop() if self.history else None
        await self.refresh(interaction)
