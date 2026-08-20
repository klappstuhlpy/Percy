"""Presentation for personal release subscriptions.

Three surfaces:

* the **subject key codec** -- how a ``(media, subject_type, subject_value)`` triple travels
  inside an autocomplete choice or a select option. It lives here rather than in ``cog.py``
  because the constraint that shapes it is a Discord one (a choice/option value is capped at
  100 characters), and ``cog.py -> ui.py`` is the direction imports are allowed to run;
* the **digest embed** one user receives by DM per dispatch run;
* the ``/subscriptions`` panel: the list embed plus :class:`SubscriptionsView`.

Nothing here touches the database directly -- the view calls back into the cog's repository.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands

from app.clients.tmdb import image_url
from app.core.views import View
from app.services.releases import COMIC_SUBJECT_TYPES, TITLE_SUBJECT_TYPES
from app.utils import helpers, truncate

if TYPE_CHECKING:
    import asyncpg

    from app.cogs.releases.cog import Releases
    from app.core import Context
    from app.services.releases import Digest, DigestGroup, Releasable

__all__ = (
    'MAX_VALUE_LEN',
    'MEDIA_LABELS',
    'STREAM_CYCLE',
    'STREAM_LABELS',
    'SUBJECT_TYPES',
    'SubscriptionsView',
    'build_digest_embed',
    'build_subscriptions_embed',
    'decode_subject',
    'encode_subject',
    'subject_choice',
)

#: Discord's cap on an application-command choice value and a select-option value. The *label*
#: may be truncated to fit; the value never may -- a truncated key is a key the fan-out can
#: never match, so an over-long candidate is dropped instead.
MAX_VALUE_LEN = 100

#: Which subject types each medium accepts, taken from the service that derives them so the two
#: cannot drift. Mirrors ``app/internal_api/routers/releases.py``'s ``SUBJECT_TYPES``.
SUBJECT_TYPES: dict[str, tuple[str, ...]] = {'comic': COMIC_SUBJECT_TYPES, 'title': TITLE_SUBJECT_TYPES}

#: Display names for the media a digest can group by. ``news`` is not a subscribable medium --
#: it is the pseudo-medium news travels under in ``release_deliveries`` (see
#: ``app/services/news/dispatch.py``) -- but a digest groups by exactly this key, so it needs a
#: heading like the other two.
MEDIA_LABELS = {'comic': 'Comics', 'title': 'Film & TV', 'news': 'News'}

#: What each ``streams`` value promises, in the second person.
STREAM_LABELS = {
    'releases': 'releases only',
    'news': 'news only',
    'both': 'releases and news',
}

#: The order the per-row toggle walks through. One select cannot both pick a row *and* pick a
#: value, so selecting a row advances it one step and re-renders.
STREAM_CYCLE = {'releases': 'both', 'both': 'news', 'news': 'releases'}


# -- Subject key codec --------------------------------------------------------


def encode_subject(media: str, subject_type: str, subject_value: str) -> str:
    """Packs a subject triple into the single string a Discord choice/option can carry."""
    return f'{media}:{subject_type}:{subject_value}'


def decode_subject(key: str) -> tuple[str, str, str] | None:
    """Unpacks a subject key, or ``None`` if it is not one.

    Split from the left twice, so a subject value containing a colon survives the round trip.
    The medium/type pairing is validated here rather than trusted: a hand-typed key must never
    be able to create a subscription the fan-out can never match.
    """
    media, _, rest = key.partition(':')
    subject_type, separator, value = rest.partition(':')
    if not separator or not value or subject_type not in SUBJECT_TYPES.get(media, ()):
        return None
    return media, subject_type, value


def subject_choice(
    media: str, subject_type: str, subject_value: str, label: str,
) -> app_commands.Choice[str] | None:
    """One autocomplete row, or ``None`` when the key would not fit Discord's 100-char cap.

    Dropping the candidate is the point: truncating the *value* would store a corrupted subject
    key, which looks like a subscription and silently never matches anything.
    """
    key = encode_subject(media, subject_type, subject_value)
    if len(key) > MAX_VALUE_LEN:
        return None
    return app_commands.Choice(name=truncate(label or subject_value, MAX_VALUE_LEN), value=key)


# -- Digest embed -------------------------------------------------------------


def _cover(item: Releasable) -> str | None:
    """The item's artwork as a URL.

    Only ``title`` stores a relative path (TMDB's ``poster_path``) and needs the CDN prefix;
    comics and news both carry an absolute URL from their own source, and prefixing one of
    those would produce a broken thumbnail rather than no thumbnail.
    """
    return image_url(item.cover_url) if item.media == 'title' else item.cover_url


def _line(item: Releasable) -> str:
    """One digest line: the item, linked when it has a URL, credited when it has a source.

    The source suffix is what makes this embed legal for news: the plan's §9 requires every
    story to name its publisher and link the original *in the DM*, not only on the web. Comics
    and titles carry no per-item source, so the suffix simply never appears for them.
    """
    name = discord.utils.escape_markdown(item.name)
    line = f'· [{name}]({item.url})' if item.url else f'· {name}'
    return f'{line} — {discord.utils.escape_markdown(item.source)}' if item.source else line


def _heading(group: DigestGroup) -> str:
    media = MEDIA_LABELS.get(group.media, group.media.title())
    if group.date is None:
        return f'{media} · date to be announced'
    return f'{media} · {group.date:%d %B %Y}'


def build_digest_embed(digest: Digest, news: Digest | None = None) -> discord.Embed:
    """Renders one user's digest as the embed they get DMed.

    The two halves are separate arguments because they are capped separately (§4.4: news is far
    higher-frequency than releases, and a busy news week must never crowd out the release
    someone actually subscribed for). They render into one embed, releases first, so a
    subscriber still gets exactly one DM per run.

    A digest with no groups still produces a readable embed rather than an empty one; the cog
    never sends that case, but an embed with neither description nor fields is a Discord API
    error waiting to happen, not a display quirk.
    """
    embed = discord.Embed(
        title='New from what you follow',
        colour=helpers.Colour.brand(),
        timestamp=discord.utils.utcnow(),
    )
    groups = [*digest.groups, *(news.groups if news is not None else ())]
    for group in groups:
        embed.add_field(
            name=_heading(group),
            value=truncate('\n'.join(_line(item) for item in group.items), 1024),
            inline=False,
        )

    if not groups:
        embed.description = 'Nothing new right now.'
    overflow = digest.overflow + (news.overflow if news is not None else 0)
    if overflow:
        embed.add_field(name='​', value=f'…and {overflow} more.', inline=False)

    cover = next((url for url in (_cover(item) for group in groups for item in group.items) if url), None)
    if cover:
        embed.set_thumbnail(url=cover)

    embed.set_footer(text='Manage these with /subscriptions')
    return embed


# -- /subscriptions panel -----------------------------------------------------


def _row_label(row: asyncpg.Record) -> str:
    return row['subject_label'] or row['subject_value']


def build_subscriptions_embed(user: discord.abc.User, rows: list[asyncpg.Record]) -> discord.Embed:
    """The list of everything one user follows, grouped by medium.

    Zero subscriptions is a real empty state pointing at ``/subscribe``, not a blank embed --
    the whole feature is invisible until someone follows their first thing.
    """
    embed = discord.Embed(title='Your subscriptions', colour=helpers.Colour.brand())
    embed.set_author(name=str(user), icon_url=user.display_avatar.url)

    if not rows:
        embed.description = (
            'You are not following anything yet.\n'
            'Use **/subscribe** to follow a comic series, a creator, a character, '
            'a film universe or a person — I will DM you when something lands.'
        )
        return embed

    for media in ('comic', 'title'):
        lines = [
            f'· **{_row_label(row)}** — {row["subject_type"]} · '
            f'{STREAM_LABELS.get(row["streams"], row["streams"])}'
            f'{" · muted" if row["delivery"] == "off" else ""}'
            for row in rows if row['media'] == media
        ]
        if lines:
            embed.add_field(name=MEDIA_LABELS[media], value=truncate('\n'.join(lines), 1024), inline=False)

    embed.set_footer(text='Pick a row below to remove it or change which updates it sends.')
    return embed


class SubscriptionsView(View):
    """The ``/subscriptions`` panel: a remove select and a per-row stream toggle."""

    def __init__(self, cog: Releases, ctx: Context, rows: list[asyncpg.Record]) -> None:
        super().__init__(timeout=180.0, members=ctx.author)
        self.cog = cog
        self.user = ctx.author
        self.rows = rows
        self._sync()

    def _sync(self) -> None:
        """Rebuilds both selects from :attr:`rows` (Discord shows at most 25 options)."""
        options = [
            discord.SelectOption(
                label=truncate(_row_label(row), MAX_VALUE_LEN),
                value=encode_subject(row['media'], row['subject_type'], row['subject_value']),
                description=f'{MEDIA_LABELS.get(row["media"], row["media"])} · '
                            f'{STREAM_LABELS.get(row["streams"], row["streams"])}',
            )
            for row in self.rows[:25]
            if len(encode_subject(row['media'], row['subject_type'], row['subject_value'])) <= MAX_VALUE_LEN
        ]
        if not options:
            # Discord rejects a select with zero options outright, so a user whose every key is
            # too long to encode gets the embed alone rather than a panel that fails to send.
            self.clear_items()
            return
        self.remove_subscription.options = options
        self.cycle_streams.options = list(options)

    async def _refresh(self, interaction: discord.Interaction) -> None:
        """Re-reads the user's subscriptions and re-renders the panel."""
        self.rows = await self.cog.bot.db.releases.list_subscriptions(self.user.id)
        if self.rows:
            self._sync()
        else:
            self.clear_items()
        await interaction.response.edit_message(
            embed=build_subscriptions_embed(self.user, self.rows),
            view=self,
        )

    @discord.ui.select(placeholder='Remove a subscription…', row=0)
    async def remove_subscription(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        decoded = decode_subject(select.values[0])
        if decoded is not None:
            await self.cog.bot.db.releases.unsubscribe(self.user.id, *decoded)
        await self._refresh(interaction)

    @discord.ui.select(placeholder='Change which updates a row sends…', row=1)
    async def cycle_streams(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        decoded = decode_subject(select.values[0])
        if decoded is not None:
            current = next(
                (row['streams'] for row in self.rows
                 if (row['media'], row['subject_type'], row['subject_value']) == decoded),
                'releases',
            )
            await self.cog.bot.db.releases.set_streams(self.user.id, *decoded, STREAM_CYCLE[current])
        await self._refresh(interaction)
