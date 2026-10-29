"""Components V2 cards for moderation cases and warnings.

Every builder here is pure: it takes already-resolved users/guilds and returns a ready
:class:`discord.ui.LayoutView`, so the layouts can be built and limit-checked in tests
without a bot. A CV2 message can carry no ``content``/``embeds``, so send these with
``view=`` alone (and ``content=None, embeds=[]`` when editing a legacy embed post).
"""

from __future__ import annotations

import datetime
from contextlib import suppress
from typing import Any

import discord

from app.cogs.modlog.models import CaseType, ModerationCase, summarize_case_counts
from app.core.components_v2 import Accent, NoticeView
from app.core.pagination import BasePaginatorV2
from app.utils import truncate

__all__ = (
    'CaseHistoryView',
    'build_case_view',
    'build_warn_dm_view',
    'build_warn_result_view',
    'case_accent',
    'ordinal',
    'send_warn_dm',
)

#: Reasons are free text; keep one well inside the 4000-character CV2 text budget.
REASON_LIMIT = 1500
#: The history list shows a short excerpt per case; ``case <n>`` shows the whole reason.
EXCERPT_LIMIT = 160

_ACCENTS: dict[CaseType, Accent] = {
    CaseType.WARN: Accent.warning,
    CaseType.BAN: Accent.error,
    CaseType.TEMPBAN: Accent.error,
    CaseType.SOFTBAN: Accent.error,
    CaseType.KICK: Accent.error,
    CaseType.MUTE: Accent.info,
    CaseType.TEMPMUTE: Accent.info,
    CaseType.TIMEOUT: Accent.info,
    CaseType.UNBAN: Accent.success,
    CaseType.UNMUTE: Accent.success,
    CaseType.UNTIMEOUT: Accent.success,
}


def case_accent(case_type: CaseType | None) -> Accent:
    """The container rail colour for a case type (neutral for an unknown action)."""
    return _ACCENTS.get(case_type, Accent.neutral) if case_type else Accent.neutral


def ordinal(n: int) -> str:
    """``1`` -> ``1st``, ``12`` -> ``12th``, ``23`` -> ``23rd``."""
    suffix = 'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suffix}'


def _quote(text: str | None, limit: int = REASON_LIMIT) -> str:
    if not text:
        return '-# *No reason provided.*'
    return '\n'.join(f'> {line}' for line in truncate(text, limit).splitlines() or [''])


def _who(user: discord.abc.User | None, user_id: int | None) -> str:
    if user_id is None:
        return '*Unknown*'
    name = f' · `{discord.utils.escape_markdown(str(user))}`' if user is not None else ''
    return f'<@{user_id}>{name}'


def _ts(dt: datetime.datetime, style: str = 'f') -> str:
    # Case timestamps are naive UTC (asyncpg TIMESTAMP); format_dt would read them as local time.
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return discord.utils.format_dt(dt, style)  # type: ignore[arg-type]


def _header(text: str, thumbnail: str | None) -> discord.ui.Item[Any]:
    if thumbnail:
        return discord.ui.Section(text, accessory=discord.ui.Thumbnail(thumbnail))
    return discord.ui.TextDisplay(text)


def _labels(case: ModerationCase) -> tuple[str, str]:
    case_type = case.type
    if case_type is None:
        return '\N{MEMO}', case.action.title()
    return case_type.emoji, case_type.label


# -- case card ----------------------------------------------------------------


def build_case_view(
    case: ModerationCase,
    *,
    target: discord.abc.User | None = None,
    moderator: discord.abc.User | None = None,
    expires: datetime.datetime | None = None,
) -> NoticeView:
    """The card announcing (modlog channel) or displaying (``case <n>``) one moderation case."""
    emoji, label = _labels(case)
    container = discord.ui.Container(accent_colour=case_accent(case.type).colour)

    heading = (
        f'## {emoji} Case #{case.index} · {label}\n'
        f'**Member** {_who(target, case.target_id)}\n'
        f'**Moderator** {_who(moderator, case.moderator_id)}'
    )
    container.add_item(_header(heading, target.display_avatar.url if target is not None else None))
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(f'**Reason**\n{_quote(case.reason)}'))
    if expires is not None:
        container.add_item(discord.ui.TextDisplay(f'**Expires** {_ts(expires, "R")} · {_ts(expires)}'))
    if case.pardoned_at is not None:
        container.add_item(discord.ui.TextDisplay(f'**Pardoned** {_ts(case.pardoned_at, "R")} · no longer counts'))
    container.add_item(discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.small))
    container.add_item(
        discord.ui.TextDisplay(f'-# Case ID {case.id} · User ID {case.target_id} · {_ts(case.created_at)}')
    )
    return NoticeView(container)


# -- warnings -------------------------------------------------------------------


def build_warn_dm_view(
    guild: discord.Guild,
    reason: str | None,
    *,
    case_index: int | None = None,
    warn_count: int | None = None,
) -> NoticeView:
    """The direct message a warned member receives. Never names the moderator."""
    container = discord.ui.Container(accent_colour=Accent.warning.colour)
    name = discord.utils.escape_markdown(guild.name)
    container.add_item(
        _header(f'## \N{TRIANGULAR FLAG ON POST} You were warned\nA moderator of **{name}** issued you a warning.',
                guild.icon.url if guild.icon else None)
    )
    container.add_item(discord.ui.Separator())
    container.add_item(discord.ui.TextDisplay(f'**Reason**\n{_quote(reason)}'))
    if warn_count:
        container.add_item(
            discord.ui.TextDisplay(f'This is your **{ordinal(warn_count)}** warning in this server.')
        )
    container.add_item(discord.ui.Separator(visible=False, spacing=discord.SeparatorSpacing.small))
    footer = f'Case #{case_index} · ' if case_index is not None else ''
    container.add_item(discord.ui.TextDisplay(f'-# {footer}Please review the server rules. Server ID {guild.id}'))
    return NoticeView(container)


async def send_warn_dm(
    member: discord.Member,
    reason: str | None,
    *,
    case_index: int | None = None,
    warn_count: int | None = None,
) -> bool:
    """DMs the warning card. Returns ``False`` when the member's DMs are closed (never raises)."""
    view = build_warn_dm_view(member.guild, reason, case_index=case_index, warn_count=warn_count)
    with suppress(discord.HTTPException):
        await member.send(view=view)
        return True
    return False


def build_warn_result_view(
    member: discord.abc.User,
    case_index: int,
    *,
    warn_count: int,
    dm_delivered: bool,
) -> NoticeView:
    """The moderator's confirmation after ``warn``."""
    container = discord.ui.Container(accent_colour=Accent.warning.colour)
    container.add_item(
        _header(
            f'### \N{TRIANGULAR FLAG ON POST} Warned {member.mention}\n'
            f'Case #{case_index} · **{ordinal(warn_count)}** warning on record',
            member.display_avatar.url,
        )
    )
    dm = 'Notified by DM.' if dm_delivered else 'Could not DM them (DMs closed) — the case is still recorded.'
    container.add_item(discord.ui.TextDisplay(f'-# {dm}'))
    return NoticeView(container)


# -- history --------------------------------------------------------------------


class CaseHistoryView(BasePaginatorV2[ModerationCase]):
    """Paginated moderation history for one user. Expects ``user`` and ``total`` in :attr:`extras`."""

    accent_colour = Accent.neutral.colour

    async def format_page(self, entries: list[ModerationCase], /) -> list[discord.ui.Item[Any]]:
        user: discord.abc.User = self.extras['user']
        total: int = self.extras.get('total', len(self.entries))
        summary = summarize_case_counts(case.action for case in self.entries)
        shown = f'newest {len(self.entries)} of {total} cases' if total > len(self.entries) else f'{total} cases'

        items: list[discord.ui.Item[Any]] = [
            _header(
                f'## Moderation history\n{_who(user, user.id)}\n**Summary:** {summary}\n'
                f'-# Page {self.current_page}/{self.total_pages} · {shown}',
                user.display_avatar.url,
            )
        ]
        for case in entries:
            emoji, label = _labels(case)
            by = f' · by <@{case.moderator_id}>' if case.moderator_id else ''
            pardoned = ' · `Pardoned`' if case.pardoned_at is not None else ''
            items.append(discord.ui.Separator())
            items.append(
                discord.ui.TextDisplay(
                    f'{emoji} **Case #{case.index} · {label}**{pardoned} · {_ts(case.created_at, "R")}{by}\n'
                    f'{_quote(case.reason, EXCERPT_LIMIT)}'
                )
            )
        return items
