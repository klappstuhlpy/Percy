"""UI components for the economy cog (search location picker, interactive hub)."""

from __future__ import annotations

import datetime
import logging
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Literal

import discord

from app.core import Accent, LayoutView, make_notice
from app.services.economy import (
    ACHIEVEMENTS,
    DROP_GRAB_TIMEOUT,
    JOB_LADDER,
    WORK_CHALLENGE_TIMEOUT,
    available_jobs,
    compute_pet_claim,
    generate_daily_quests,
    get_job,
    get_species,
    next_unlock,
    prestige_multiplier,
    prestige_requirement,
)
from app.utils import fnumb, get_asset_url, helpers
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Sequence

    from app.cogs.economy.cog import Economy
    from app.core.components_v2 import NoticeView
    from app.core.models import Context
    from app.services.economy import Challenge, Job, SearchLocation

log = logging.getLogger(__name__)

__all__ = (
    'BOOST_LABELS',
    'DropView',
    'EconomyHub',
    'SearchView',
    'WorkChallengeView',
    'boost_display_line',
    'card',
    'card_text',
    'job_line',
    'progress_bar',
)

#: Display labels for active boost kinds.
BOOST_LABELS = {'xp': 'leveling XP', 'loot': 'fishing & hunting payouts'}


def boost_display_line(row: Any) -> str:
    """One display line for an active boost row (shields have no percentage)."""
    expires = discord.utils.format_dt(row['expires_at'].replace(tzinfo=datetime.UTC), 'R')
    if row['kind'] == 'shield':
        return f'\N{SHIELD} **Rob shield** — ends {expires}'
    label = BOOST_LABELS.get(row['kind'], row['kind'])
    return f'\N{HIGH VOLTAGE SIGN} **+{row["multiplier"] - 1.0:.0%} {label}** — ends {expires}'


def progress_bar(progress: int, goal: int, *, width: int = 10) -> str:
    """A progress bar of custom emojis, without an indicator. Emojis don't render inside backticks."""
    emojis = Emojis.ProgressBar
    filled = 0 if goal <= 0 else max(0, min(round(progress / goal * width), width))
    segments = [emojis.full] * filled + [emojis.empty] * (width - filled)
    if filled:
        segments[0] = emojis.leftfull
    if filled < width:
        segments[-1] = emojis.rightempty
    return ''.join(segments)


def _join_notes(notes: Iterable[str | None]) -> str:
    return ' • '.join(note for note in notes if note)


def card_text(title: str, body: str, notes: Iterable[str | None] = ()) -> str:
    """The markdown of a small card: heading, body and a muted line of facts."""
    footer = _join_notes(notes)
    return f'### {title}\n{body}' + (f'\n-# {footer}' if footer else '')


def card(
    title: str, description: str | None = None, *, notes: Iterable[str | None] = (),
    footer: str | None = None, accent: Accent = Accent.brand, **kwargs: Any,
) -> NoticeView:
    """A small display-only economy card; ``notes`` are muted facts joined into its footer."""
    return make_notice(title, description, compact=True, accent=accent, footer=_join_notes(notes) or footer, **kwargs)


def job_line(job: Job, *, current_id: str, shifts: int) -> str:
    """One career-ladder row: employed, unlocked or locked."""
    unlocked = '\N{WHITE HEAVY CHECK MARK}' if job.shifts_required <= shifts else '\N{LOCK}'
    marker = '\N{BRIEFCASE}' if job.id == current_id else unlocked
    return f'{marker} {job.emoji} **{job.name}** • {job.pay_min:,}-{job.pay_max:,} • *{job.shifts_required} shifts*'


class _ButtonCard(LayoutView):
    """A small card: markdown text above a row of buttons, re-rendered whenever either changes."""

    def __init__(
        self, text: str, *, timeout: float, members: discord.abc.Snowflake | None = None
    ) -> None:
        super().__init__(timeout=timeout, members=members)
        self.text = text
        self.buttons: list[discord.ui.Button] = []

    def render(self) -> None:
        container = discord.ui.Container(discord.ui.TextDisplay(self.text), accent_colour=helpers.Colour.brand())
        if self.buttons:
            container.add_item(discord.ui.ActionRow(*self.buttons))
        self.clear_items()
        self.add_item(container)

    def freeze(self, text: str | None = None) -> None:
        """Disable every button, optionally swapping the text."""
        for button in self.buttons:
            button.disabled = True
        if text is not None:
            self.text = text
        self.render()


class SearchView(_ButtonCard):
    """Presents the ``search`` location options and forwards the pick to the cog.

    The cog supplies ``resolve``, an async callback receiving the interaction and
    the chosen location; it calls :meth:`freeze` with the result and edits the
    message. The view only owns the button plumbing (single-use, invoker-locked,
    disabled once picked or timed out).
    """

    def __init__(
        self,
        member: discord.abc.Snowflake,
        options: Sequence[SearchLocation],
        resolve: Callable[[discord.Interaction, SearchLocation], Awaitable[None]],
        *,
        prompt: str,
        timeout_text: str,
    ) -> None:
        super().__init__(prompt, timeout=30.0, members=member)
        self.timeout_text = timeout_text
        for location in options:
            button: discord.ui.Button = discord.ui.Button(
                style=discord.ButtonStyle.secondary, label=location.name, emoji=location.emoji)
            button.callback = self._picker(button, location, resolve)  # type: ignore[assignment]
            self.buttons.append(button)
        self.render()

    def _picker(
        self, button: discord.ui.Button, location: SearchLocation,
        resolve: Callable[[discord.Interaction, SearchLocation], Awaitable[None]],
    ) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.stop()
            button.style = discord.ButtonStyle.primary
            self.freeze()
            await resolve(interaction, location)
        return callback

    async def on_timeout(self) -> None:
        self.freeze(self.timeout_text)
        if self.message is not None:
            with suppress(discord.HTTPException):
                await self.message.edit(view=self)


class WorkChallengeView(_ButtonCard):
    """The ``work`` challenge: one button per option, invoker-locked and single use.

    The cog awaits :meth:`wait`, reads :attr:`correct` (``None`` = timed out) and
    turns the same message into the shift result with :meth:`finish`.
    """

    def __init__(self, member: discord.abc.Snowflake, challenge: Challenge, text: str) -> None:
        super().__init__(text, timeout=WORK_CHALLENGE_TIMEOUT, members=member)
        self.challenge = challenge
        self.correct: bool | None = None
        for option in challenge.options:
            button: discord.ui.Button = discord.ui.Button(style=discord.ButtonStyle.secondary, label=option)
            button.callback = self._answer(button)  # type: ignore[assignment]
            self.buttons.append(button)
        self.render()

    def _answer(self, button: discord.ui.Button) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.correct = button.label == self.challenge.answer
            if not self.correct:
                button.style = discord.ButtonStyle.danger
            self.stop()
            await interaction.response.defer()  # finish() edits the card once the shift is paid
        return callback

    async def finish(self, text: str) -> None:
        """Mark the right answer and swap the task for the result."""
        for button in self.buttons:
            if button.label == self.challenge.answer:
                button.style = discord.ButtonStyle.success
        self.freeze(text)
        if self.message is not None:
            with suppress(discord.HTTPException):  # the pay already went through; a deleted card must not undo the rest
                await self.message.edit(view=self)


class DropView(_ButtonCard):
    """A chat drop: the first member to press **Grab** wins, everyone else is told they were too slow.

    ``award(member)`` pays the winner and returns the text shown on the finished card.
    """

    def __init__(self, text: str, award: Callable[[discord.Member], Awaitable[str]]) -> None:
        super().__init__(text, timeout=DROP_GRAB_TIMEOUT)
        self._award = award
        self.winner: discord.abc.User | None = None
        self.grab: discord.ui.Button = discord.ui.Button(
            label='Grab', style=discord.ButtonStyle.success, emoji='\N{RAISED HAND}')
        self.grab.callback = self._grab  # type: ignore[assignment]
        self.buttons.append(self.grab)
        self.render()

    async def _grab(self, interaction: discord.Interaction) -> None:
        if self.winner is not None:  # claimed before any await, so the first click wins
            await interaction.response.send_message('Too slow - someone already grabbed it.', ephemeral=True)
            return
        self.winner = interaction.user
        await interaction.response.defer()  # the award hits the DB; answer within 3 s regardless
        assert isinstance(interaction.user, discord.Member)
        try:
            text = await self._award(interaction.user)
        except Exception:
            log.exception('Chat drop award failed')
            self.winner = None  # let someone else retry
            await interaction.followup.send('Something went wrong paying out the drop - try again.', ephemeral=True)
            return
        self.stop()
        self.grab.label = 'Grabbed'
        self.freeze(text)
        await interaction.edit_original_response(view=self)

    async def on_timeout(self) -> None:
        if self.message is not None and self.winner is None:
            self.freeze(card_text('\N{ALARM CLOCK} Drop missed', 'Nobody grabbed it in time.'))
            with suppress(discord.HTTPException):
                await self.message.edit(view=self)


# -- interactive economy hub ---------------------------------------------------

#: The hub's pages: (value, label, emoji).
_HUB_PAGES: tuple[tuple[str, str, str], ...] = (
    ('overview', 'Overview', '\N{HOUSE BUILDING}'),
    ('quests', 'Daily Quests', '\N{SCROLL}'),
    ('job', 'Career', '\N{BRIEFCASE}'),
    ('pet', 'Pet', '\N{PAW PRINTS}'),
    ('achievements', 'Achievements', '\N{TROPHY}'),
    ('perks', 'Perks & Boosts', '\N{HIGH VOLTAGE SIGN}'),
)

_HUNGER_LABELS = {
    'fed': '\N{SMILING FACE WITH SMILING EYES} Well fed',
    'hungry': '\N{FACE WITH OPEN MOUTH} Hungry (earning half rate)',
    'starving': '\N{FACE SCREAMING IN FEAR} Starving (earning nothing!)',
}


class _HubSelect(discord.ui.Select['EconomyHub']):
    """The page switcher at the bottom of the hub."""

    def __init__(self, current: str) -> None:
        super().__init__(
            placeholder='Jump to a section…',
            options=[
                discord.SelectOption(label=label, value=value, emoji=emoji, default=value == current)
                for value, label, emoji in _HUB_PAGES
            ],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        self.view.page = self.values[0]
        await self.view.refresh(interaction)


class EconomyHub(LayoutView):
    """One interactive card bundling a member's whole economy standing.

    A select menu swaps between pages (overview, quests, career, pet,
    achievements, perks); every switch re-reads the database so the card
    always shows live numbers. Invoker-locked, times out quietly.

    ``page`` opens on a given section (the ``quests``/``pet``/... commands do
    this). ``target`` shows another member's data read-only: only the page
    they asked for, with no switcher and no action buttons.
    """

    def __init__(
        self, cog: Economy, ctx: Context, *, page: str = 'overview', target: discord.Member | None = None
    ) -> None:
        super().__init__(timeout=180.0, members=ctx.author, disable_on_timeout=True)
        self.cog = cog
        self.ctx = ctx
        self.user: discord.Member = target or ctx.author  # type: ignore[assignment]
        self.own = self.user.id == ctx.author.id
        self.page = page if page in {p[0] for p in _HUB_PAGES} else 'overview'
        self.message: discord.Message | None = None
        #: One-line feedback from the last button, shown at the bottom of the card.
        self.notice: str | None = None

    # -- plumbing -----------------------------------------------------------

    async def build(self) -> None:
        """(Re)build the layout for the current page."""
        builder = getattr(self, f'_page_{self.page}')
        container: discord.ui.Container = await builder()
        if self.notice is not None:
            container.add_item(discord.ui.TextDisplay(self.notice))
            self.notice = None
        if self.own:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.ActionRow(_HubSelect(self.page)))
        self.clear_items()
        self.add_item(container)

    async def refresh(self, interaction: discord.Interaction) -> None:
        await self.build()
        await interaction.response.edit_message(view=self)

    def _button(
        self, label: str, callback: Callable[[discord.Interaction], Awaitable[None]], *,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary, emoji: str | None = None,
    ) -> discord.ui.Button:
        button: discord.ui.Button = discord.ui.Button(label=label, style=style, emoji=emoji)
        button.callback = callback  # type: ignore[assignment]
        return button

    def _acting(
        self, action: Callable[[discord.Member], Awaitable[tuple[bool, str]]]
    ) -> Callable[[discord.Interaction], Awaitable[None]]:
        """A button callback that runs a cog ``*_action`` and shows its message."""
        async def callback(interaction: discord.Interaction) -> None:
            ok, message = await action(self.user)
            self.notice = f'{Emojis.success if ok else Emojis.error} {message}'
            await self.refresh(interaction)
        return callback

    def _browse(self, mode: Literal['shop', 'inventory']) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            from app.cogs.economy.item_ui import ItemBrowser  # circular at module level

            browser = ItemBrowser(self.cog, self.user, mode=mode)
            await browser.build()
            browser.message = self.message
            self.stop()
            await interaction.response.edit_message(view=browser)
        return callback

    def _container(self, body: str) -> discord.ui.Container:
        """A container opened with the member's header section."""
        container = discord.ui.Container(accent_colour=helpers.Colour.brand())
        container.add_item(
            discord.ui.Section(body, accessory=discord.ui.Thumbnail(get_asset_url(self.user)))
        )
        return container

    # -- pages ----------------------------------------------------------------

    async def _page_overview(self) -> discord.ui.Container:
        guild_id = self.ctx.guild.id  # type: ignore[union-attr]
        user_id = self.user.id
        db = self.cog.bot.db
        today = datetime.datetime.now(datetime.UTC).date()

        balance = await self.ctx.db.get_user_balance(user_id, guild_id)
        daily = await db.economy.get_daily(user_id, guild_id)
        job_row = await db.economy.get_job(user_id, guild_id)
        prestige = await db.economy.get_prestige(user_id, guild_id)
        pet_row = await db.economy.get_pet(user_id, guild_id)
        quest_rows = await self.cog._ensure_quests(user_id, guild_id, today)
        earned = await db.economy.get_achievements(user_id, guild_id)
        boosts = await db.economy.get_active_boosts(user_id, guild_id)
        settings = await self.cog._settings(guild_id)
        items = await db.economy.inventory_value(user_id, guild_id)

        job = get_job(job_row['job_id'] if job_row else None)
        shifts = job_row['shifts'] if job_row else 0
        streak = daily['streak'] if daily else 0
        quests_done = sum(1 for row in quest_rows if row['completed'])

        pet_line = '\N{PAW PRINTS} *No pet yet*'
        if pet_row is not None:
            pet_species = get_species(pet_row['species'])
            if pet_species is not None:
                pet_line = f'{pet_species.emoji} **{pet_row["name"]}** • {pet_species.name}'

        bonus = (prestige_multiplier(prestige) - 1) * 100
        scale = settings.payout_multiplier * prestige_multiplier(prestige)
        scale_line = f'\N{CHART WITH UPWARDS TREND} **x{scale:.2f}** payouts\n' if scale != 1.0 else ''

        container = self._container(
            f'### {self.user.display_name}\n'
            f'-# Economy on **{self.ctx.guild.name}**'  # type: ignore[union-attr]
        )
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            f'{Emojis.Economy.cash} **{fnumb(balance.total + items)}** net worth\n'
            f'-# Cash {fnumb(balance.cash)} • Bank {fnumb(balance.bank)} • Items {fnumb(items)}'
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            f'{scale_line}'
            f'{job.emoji} **{job.name}** • {shifts:,} shifts\n'
            f'{pet_line}\n'
            f'\N{GLOWING STAR} Prestige **{prestige}** • +{bonus:.0f}% payouts\n'
            f'\N{FIRE} Streak **{streak}**\n'
            f'\N{SCROLL} Quests **{quests_done}/{len(quest_rows)}**\n'
            f'\N{TROPHY} Badges **{len(earned)}/{len(ACHIEVEMENTS)}**\n'
            f'\N{HIGH VOLTAGE SIGN} Boosts **{len(boosts)}**'
        ))
        if self.own:
            container.add_item(discord.ui.ActionRow(
                self._button('Inventory', self._browse('inventory'), emoji='\N{SCHOOL SATCHEL}'),
                self._button('Shop', self._browse('shop'), emoji='\N{CONVENIENCE STORE}'),
            ))
        return container

    async def _page_quests(self) -> discord.ui.Container:
        guild_id = self.ctx.guild.id  # type: ignore[union-attr]
        today = datetime.datetime.now(datetime.UTC).date()
        rows = await self.cog._ensure_quests(self.user.id, guild_id, today)
        board = {q.key: q for q in generate_daily_quests(guild_id, self.user.id, today)}

        lines = []
        for row in rows:
            quest = board.get(row['quest'])
            description = quest.description if quest else row['quest']
            reward = f'{Emojis.Economy.cash} **{fnumb(row["reward"])}**'
            if row['completed']:
                lines.append(f'\N{WHITE HEAVY CHECK MARK} ~~{description}~~ • {reward}')
            else:
                lines.append(
                    f'**{description}**\n'
                    f'{progress_bar(row["progress"], row["goal"])} `{row["progress"]:,}/{row["goal"]:,}` • {reward}'
                )

        resets = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time(), tzinfo=datetime.UTC)
        container = self._container(
            f'### \N{SCROLL} Daily Quests\n-# New quests {discord.utils.format_dt(resets, "R")}'
        )
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('\n'.join(lines)))
        return container

    async def _page_job(self) -> discord.ui.Container:
        guild_id = self.ctx.guild.id  # type: ignore[union-attr]
        row = await self.cog.bot.db.economy.get_job(self.user.id, guild_id)
        current = get_job(row['job_id'] if row else None)
        shifts = row['shifts'] if row else 0

        container = self._container(
            f'### \N{BRIEFCASE} Career\n'
            f'{current.emoji} **{current.name}** • {Emojis.Economy.cash} {current.pay_min:,}-{current.pay_max:,} per shift\n'
            f'-# {shifts:,} lifetime shifts'
        )
        container.add_item(discord.ui.Separator())

        nxt = next_unlock(shifts)
        if nxt is not None:
            container.add_item(discord.ui.TextDisplay(
                f'**Next up:** {nxt.emoji} {nxt.name}\n'
                f'{progress_bar(shifts, nxt.shifts_required)} `{shifts}/{nxt.shifts_required}`'
            ))
        # The ladder is long, so the card shows the rungs around the member's job; `job list` has them all.
        index = JOB_LADDER.index(current)
        container.add_item(discord.ui.TextDisplay(
            '\n'.join(job_line(job, current_id=current.id, shifts=shifts) for job in JOB_LADDER[max(index - 2, 0):index + 4])
            + '\n-# All jobs: `job list` • Clock in: `work`'
        ))
        options = [
            discord.SelectOption(
                label=j.name, value=j.id, emoji=j.emoji, description=f'{j.pay_min:,}-{j.pay_max:,} per shift')
            for j in available_jobs(shifts) if j.id != current.id
        ]
        if self.own and options:
            select: discord.ui.Select = discord.ui.Select(placeholder='Switch to an unlocked job…', options=options)

            async def apply(interaction: discord.Interaction) -> None:
                ok, message = await self.cog.job_apply_action(self.user, select.values[0])
                self.notice = f'{Emojis.success if ok else Emojis.error} {message}'
                await self.refresh(interaction)

            select.callback = apply  # type: ignore[assignment]
            container.add_item(discord.ui.ActionRow(select))
        return container

    async def _page_pet(self) -> discord.ui.Container:
        guild_id = self.ctx.guild.id  # type: ignore[union-attr]
        row = await self.cog.bot.db.economy.get_pet(self.user.id, guild_id)
        container = self._container('### \N{PAW PRINTS} Pet')
        container.add_item(discord.ui.Separator())

        if row is None:
            container.add_item(discord.ui.TextDisplay(
                'No pet yet. Pets earn cash while you keep them fed.\n'
                '-# Browse: `pet shop` • Adopt: `pet adopt <species> [name]`'
            ))
            return container

        species = get_species(row['species'])
        if species is None:
            container.add_item(discord.ui.TextDisplay('Your pet\'s species no longer exists.'))
            return container

        now = discord.utils.utcnow()
        claim = compute_pet_claim(
            species,
            row['last_claim'].replace(tzinfo=datetime.UTC),
            row['last_fed'].replace(tzinfo=datetime.UTC),
            now=now,
        )
        container.add_item(discord.ui.TextDisplay(
            f'{species.emoji} **{row["name"]}** • {species.name}\n'
            f'{_HUNGER_LABELS[claim.hunger.value]}\n'
            f'{Emojis.Economy.cash} **{fnumb(claim.amount)}** unclaimed\n'
            f'-# {species.hourly_rate:,}/h • stores {species.storage_hours}h • feeding costs {species.feed_cost:,}'
        ))
        if self.own:
            container.add_item(discord.ui.ActionRow(
                self._button('Feed', self._acting(self.cog.pet_feed_action), emoji='\N{MEAT ON BONE}'),
                self._button('Claim', self._acting(self.cog.pet_claim_action), style=discord.ButtonStyle.success,
                             emoji=Emojis.Economy.cash),
            ))
        return container

    async def _page_achievements(self) -> discord.ui.Container:
        guild_id = self.ctx.guild.id  # type: ignore[union-attr]
        earned_rows = await self.cog.bot.db.economy.get_achievements(self.user.id, guild_id)
        earned = {row['achievement']: row['earned_at'] for row in earned_rows}

        lines = [
            f'{a.emoji} **{a.name}** • {discord.utils.format_dt(earned[a.id].replace(tzinfo=datetime.UTC), "d")}\n-# {a.description}'
            for a in ACHIEVEMENTS if a.id in earned
        ]
        lines += [f'-# \N{LOCK} {a.name}: {a.description}' for a in ACHIEVEMENTS if a.id not in earned]
        container = self._container(
            f'### \N{TROPHY} Achievements\n-# **{len(earned)}/{len(ACHIEVEMENTS)}** unlocked'
        )
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('\n'.join(lines)))
        return container

    async def _page_perks(self) -> discord.ui.Container:
        guild_id = self.ctx.guild.id  # type: ignore[union-attr]
        db = self.cog.bot.db
        boosts = await db.economy.get_active_boosts(self.user.id, guild_id)
        prestige = await db.economy.get_prestige(self.user.id, guild_id)
        settings = await self.cog._settings(guild_id)

        lines = [boost_display_line(row) for row in boosts] or ['*No active boosts. Usable shop items grant them.*']
        if prestige > 0:
            lines.append(f'\N{GLOWING STAR} **Prestige {prestige}** • +{(prestige_multiplier(prestige) - 1) * 100:.0f}% payouts')
        if settings.payout_multiplier != 1.0:
            lines.append(f'\N{CHART WITH UPWARDS TREND} Server payouts **x{settings.payout_multiplier:.2f}**')

        requirement = prestige_requirement(prestige)
        balance = await self.ctx.db.get_user_balance(self.user.id, guild_id)

        container = self._container('### \N{HIGH VOLTAGE SIGN} Perks & Boosts')
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('\n'.join(lines)))
        container.add_item(discord.ui.TextDisplay(
            f'**Next prestige**\n{progress_bar(balance.total, requirement)} `{balance.total:,}/{requirement:,}`'
        ))
        return container
