"""UI components for the economy cog (search location picker, interactive hub)."""

from __future__ import annotations

import datetime
import logging
from typing import TYPE_CHECKING, Any, Literal

import discord

from app.core import LayoutView
from app.services.economy import (
    ACHIEVEMENTS,
    DROP_GRAB_TIMEOUT,
    JOB_LADDER,
    WORK_CHALLENGE_TIMEOUT,
    compute_pet_claim,
    generate_daily_quests,
    get_job,
    get_species,
    prestige_multiplier,
    prestige_requirement,
)
from app.utils import fnumb, get_asset_url, helpers
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from app.cogs.economy.cog import Economy
    from app.core.models import Context
    from app.services.economy import Challenge, SearchLocation

log = logging.getLogger(__name__)

__all__ = ('BOOST_LABELS', 'DropView', 'EconomyHub', 'SearchView', 'WorkChallengeView', 'boost_display_line', 'progress_bar')

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
    """A small text progress bar like ``█████░░░░░``."""
    filled = 0 if goal <= 0 else min(round(progress / goal * width), width)
    return '█' * filled + '░' * (width - filled)


class SearchButton(discord.ui.Button['SearchView']):
    """One of the location choices offered by :class:`SearchView`."""

    def __init__(self, location: SearchLocation) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label=location.name, emoji=location.emoji)
        self.location = location

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        view = self.view
        view.stop()
        for child in view.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
                child.style = (
                    discord.ButtonStyle.primary if child is self else discord.ButtonStyle.secondary
                )
        await view.resolve(interaction, self.location)


class SearchView(discord.ui.View):
    """Presents the ``search`` location options and forwards the pick to the cog.

    The cog supplies ``resolve``, an async callback receiving the interaction and
    the chosen location; the view only owns the button plumbing (single-use,
    invoker-locked, disabled once picked or timed out).
    """

    def __init__(
        self,
        member: discord.abc.User,
        options: Sequence[SearchLocation],
        resolve: Callable[[discord.Interaction, SearchLocation], Awaitable[None]],
        *,
        timeout_text: str = 'You hesitated too long and went home empty-handed.',
    ) -> None:
        super().__init__(timeout=30.0)
        self.member = member
        self.timeout_text = timeout_text
        self.resolve = resolve
        self.message: discord.Message | None = None
        for location in options:
            self.add_item(SearchButton(location))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message('This choice is not yours.', ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(content=self.timeout_text, view=self)
            except discord.HTTPException:
                pass


class _ChallengeButton(discord.ui.Button['WorkChallengeView']):
    """One answer option of a :class:`WorkChallengeView`."""

    def __init__(self, option: str) -> None:
        super().__init__(style=discord.ButtonStyle.secondary, label=option)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        view = self.view
        view.correct = self.label == view.challenge.answer
        view.stop()
        for child in view.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
                if child.label == view.challenge.answer:
                    child.style = discord.ButtonStyle.success
        if not view.correct:
            self.style = discord.ButtonStyle.danger
        await interaction.response.edit_message(view=view)


class WorkChallengeView(discord.ui.View):
    """The ``work`` challenge: one button per option, invoker-locked and single use.

    The cog awaits :meth:`wait`, then reads :attr:`correct` (``None`` = timed out).
    """

    def __init__(self, member: discord.abc.User, challenge: Challenge) -> None:
        super().__init__(timeout=WORK_CHALLENGE_TIMEOUT)
        self.member = member
        self.challenge = challenge
        self.correct: bool | None = None
        self.message: discord.Message | None = None
        for option in challenge.options:
            self.add_item(_ChallengeButton(option))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message('This is not your shift.', ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class DropView(discord.ui.View):
    """A chat drop: the first member to press **Grab** wins, everyone else is told they were too slow.

    ``award(member)`` pays the winner and returns the text shown on the finished card.
    """

    def __init__(self, award: Callable[[discord.Member], Awaitable[str]]) -> None:
        super().__init__(timeout=DROP_GRAB_TIMEOUT)
        self._award = award
        self.winner: discord.abc.User | None = None
        self.message: discord.Message | None = None

    @discord.ui.button(label='Grab', style=discord.ButtonStyle.success, emoji='\N{RAISED HAND}')
    async def grab(self, interaction: discord.Interaction, button: discord.ui.Button[DropView]) -> None:
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
        button.disabled = True
        button.label = 'Grabbed'
        await interaction.edit_original_response(content=text, view=self)

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None and self.winner is None:
            try:
                await self.message.edit(content='\N{ALARM CLOCK} Nobody grabbed the drop.', view=self)
            except discord.HTTPException:
                pass


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

        job = get_job(job_row['job_id'] if job_row else None)
        shifts = job_row['shifts'] if job_row else 0
        streak = daily['streak'] if daily else 0
        quests_done = sum(1 for row in quest_rows if row['completed'])

        pet_line = 'No pet — adopt one on the Pet page.'
        if pet_row is not None:
            pet_species = get_species(pet_row['species'])
            if pet_species is not None:
                pet_line = f'{pet_species.emoji} **{pet_row["name"]}** ({pet_species.name})'

        scale = settings.payout_multiplier * prestige_multiplier(prestige)
        scale_line = f'\N{CHART WITH UPWARDS TREND} Payout multiplier: **x{scale:.2f}**\n' if scale != 1.0 else ''

        container = self._container(
            f'## {Emojis.Economy.cash} {self.user.display_name}\'s Economy\n'
            f'Everything about your standing on **{self.ctx.guild.name}** in one card.'  # type: ignore[union-attr]
        )
        container.add_item(discord.ui.Separator())
        items = await db.economy.inventory_value(self.user.id, guild_id)
        container.add_item(discord.ui.TextDisplay(
            f'**Cash** {Emojis.Economy.cash} **{fnumb(balance.cash)}** • '
            f'**Bank** {Emojis.Economy.cash} **{fnumb(balance.bank)}** • '
            f'**Items** {Emojis.Economy.cash} **{fnumb(items)}** • '
            f'**Net worth** {Emojis.Economy.cash} **{fnumb(balance.total + items)}**'
        ))
        container.add_item(discord.ui.TextDisplay(
            f'{scale_line}'
            f'\N{FIRE} Daily streak: **{streak}**\n'
            f'\N{BRIEFCASE} Job: {job.emoji} **{job.name}** ({fnumb(shifts)} lifetime shifts)\n'
            f'\N{GLOWING STAR} Prestige: **{prestige}** (+{(prestige_multiplier(prestige) - 1) * 100:.0f}% payouts)\n'
            f'\N{PAW PRINTS} Pet: {pet_line}\n'
            f'\N{SCROLL} Quests today: **{quests_done}/{len(quest_rows)}** complete\n'
            f'\N{TROPHY} Badges: **{len(earned)}/{len(ACHIEVEMENTS)}** unlocked\n'
            f'\N{HIGH VOLTAGE SIGN} Active boosts: **{len(boosts)}**'
        ))
        if self.own:
            container.add_item(discord.ui.ActionRow(
                self._button('Open inventory', self._browse('inventory'), emoji='\N{SCHOOL SATCHEL}'),
                self._button('Open shop', self._browse('shop'), emoji='\N{CONVENIENCE STORE}'),
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
            if row['completed']:
                lines.append(f'\N{WHITE HEAVY CHECK MARK} ~~{description}~~ — {Emojis.Economy.cash} **{fnumb(row["reward"])}**')
            else:
                lines.append(
                    f'\N{SCROLL} **{description}**\n'
                    f'`{progress_bar(row["progress"], row["goal"])}` {fnumb(row["progress"])}/{fnumb(row["goal"])} '
                    f'— {Emojis.Economy.cash} **{fnumb(row["reward"])}**'
                )

        resets = datetime.datetime.combine(today + datetime.timedelta(days=1), datetime.time(), tzinfo=datetime.UTC)
        container = self._container(
            f'## \N{SCROLL} Daily Quests\nNew quests {discord.utils.format_dt(resets, "R")} (UTC).'
        )
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('\n'.join(lines)))
        return container

    async def _page_job(self) -> discord.ui.Container:
        guild_id = self.ctx.guild.id  # type: ignore[union-attr]
        row = await self.cog.bot.db.economy.get_job(self.user.id, guild_id)
        current = get_job(row['job_id'] if row else None)
        shifts = row['shifts'] if row else 0

        nxt = next((j for j in JOB_LADDER if j.shifts_required > shifts), None)
        if nxt is not None:
            unlock_line = (
                f'Next unlock: {nxt.emoji} **{nxt.name}** — '
                f'`{progress_bar(shifts, nxt.shifts_required)}` {shifts}/{nxt.shifts_required} shifts'
            )
        else:
            unlock_line = 'You\'ve reached the top of the ladder. \N{ROCKET}'

        ladder_lines = []
        for ladder_job in JOB_LADDER:
            unlocked = ladder_job.shifts_required <= shifts
            marker = '\N{BRIEFCASE}' if ladder_job.id == current.id else (
                '\N{WHITE HEAVY CHECK MARK}' if unlocked else '\N{LOCK}')
            ladder_lines.append(
                f'{marker} {ladder_job.emoji} **{ladder_job.name}** — '
                f'{fnumb(ladder_job.pay_min)}-{fnumb(ladder_job.pay_max)}/shift *(needs {ladder_job.shifts_required})*'
            )

        container = self._container(
            f'## \N{BRIEFCASE} Career\n'
            f'{current.emoji} **{current.name}** — {Emojis.Economy.cash} {fnumb(current.pay_min)}-'
            f'{fnumb(current.pay_max)} per shift • **{fnumb(shifts)}** lifetime shifts\n{unlock_line}'
        )
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('\n'.join(ladder_lines)))
        container.add_item(discord.ui.TextDisplay('-# Work with `work`.'))
        options = [
            discord.SelectOption(
                label=j.name, value=j.id, emoji=j.emoji, description=f'{fnumb(j.pay_min)}-{fnumb(j.pay_max)} per shift')
            for j in JOB_LADDER if j.shifts_required <= shifts and j.id != current.id
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
        container = self._container('## \N{PAW PRINTS} Pet')
        container.add_item(discord.ui.Separator())

        if row is None:
            container.add_item(discord.ui.TextDisplay(
                'You don\'t have a pet yet. Pets earn cash passively while you keep them fed.\n'
                'Browse the species with `pet shop` and adopt with `pet adopt <species> [name]`.'
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
            f'{species.emoji} **{row["name"]}** ({species.name})\n'
            f'{_HUNGER_LABELS[claim.hunger.value]}\n'
            f'{Emojis.Economy.cash} **{fnumb(claim.amount)}** unclaimed '
            f'*(rate {fnumb(species.hourly_rate)}/h, stores up to {species.storage_hours}h)*\n'
            f'Feeding costs {Emojis.Economy.cash} **{fnumb(species.feed_cost)}**'
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
            f'{a.emoji} **{a.name}** — {a.description} '
            f'*(earned {discord.utils.format_dt(earned[a.id].replace(tzinfo=datetime.UTC), "d")})*' if a.id in earned
            else f'\N{LOCK} *{a.name}* — {a.description}'
            for a in ACHIEVEMENTS
        ]
        container = self._container(
            f'## \N{TROPHY} Achievements\nUnlocked **{len(earned)}/{len(ACHIEVEMENTS)}** badges.'
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

        lines = [boost_display_line(row) for row in boosts] or ['*No active boosts — usable shop items grant them.*']
        if prestige > 0:
            lines.append(f'\N{GLOWING STAR} **Prestige {prestige}** — permanent +{(prestige_multiplier(prestige) - 1) * 100:.0f}% payouts')
        if settings.payout_multiplier != 1.0:
            lines.append(f'\N{CHART WITH UPWARDS TREND} Server payout multiplier: **x{settings.payout_multiplier:.2f}**')

        level = prestige
        requirement = prestige_requirement(level)
        balance = await self.ctx.db.get_user_balance(self.user.id, guild_id)

        container = self._container('## \N{HIGH VOLTAGE SIGN} Perks & Boosts')
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('\n'.join(lines)))
        container.add_item(discord.ui.TextDisplay(
            f'-# Next prestige at {Emojis.Economy.cash} {fnumb(requirement)} net worth '
            f'(`{progress_bar(balance.total, requirement)}` {fnumb(balance.total)}/{fnumb(requirement)}).'
        ))
        return container
