"""UI components for the economy cog (search location picker, interactive hub)."""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING, Any

import discord

from app.core import LayoutView
from app.services.economy import (
    ACHIEVEMENTS,
    JOB_LADDER,
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
    from app.services.economy import SearchLocation

__all__ = ('BOOST_LABELS', 'EconomyHub', 'SearchView', 'boost_display_line', 'progress_bar')

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
    ) -> None:
        super().__init__(timeout=30.0)
        self.member = member
        self.resolve = resolve
        self.message: discord.Message | None = None
        for location in options:
            self.add_item(SearchButton(location))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.member.id:
            await interaction.response.send_message('This search is not yours.', ephemeral=True)
            return False
        return True

    async def on_timeout(self) -> None:
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(content='You hesitated too long and went home empty-handed.', view=self)
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
        await self.view.build()
        await interaction.edit_original_response(view=self.view)


class EconomyHub(LayoutView):
    """One interactive card bundling a member's whole economy standing.

    A select menu swaps between pages (overview, quests, career, pet,
    achievements, perks); every switch re-reads the database so the card
    always shows live numbers. Invoker-locked, times out quietly.
    """

    def __init__(self, cog: Economy, ctx: Context) -> None:
        super().__init__(timeout=180.0, members=ctx.author, disable_on_timeout=True)
        self.cog = cog
        self.ctx = ctx
        self.page = 'overview'
        self.message: discord.Message | None = None

    # -- plumbing -----------------------------------------------------------

    async def build(self) -> None:
        """(Re)build the layout for the current page."""
        builder = getattr(self, f'_page_{self.page}')
        container: discord.ui.Container = await builder()
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(_HubSelect(self.page)))
        self.clear_items()
        self.add_item(container)

    def _container(self, body: str) -> discord.ui.Container:
        """A container opened with the member's header section."""
        container = discord.ui.Container(accent_colour=helpers.Colour.brand())
        container.add_item(
            discord.ui.Section(body, accessory=discord.ui.Thumbnail(get_asset_url(self.ctx.author)))
        )
        return container

    # -- pages ----------------------------------------------------------------

    async def _page_overview(self) -> discord.ui.Container:
        guild_id = self.ctx.guild.id  # type: ignore[union-attr]
        user_id = self.ctx.author.id
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
            f'## {Emojis.Economy.cash} {self.ctx.author.display_name}\'s Economy\n'
            f'Everything about your standing on **{self.ctx.guild.name}** in one card.'  # type: ignore[union-attr]
        )
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            f'**Cash** {Emojis.Economy.cash} **{fnumb(balance.cash)}** • '
            f'**Bank** {Emojis.Economy.cash} **{fnumb(balance.bank)}** • '
            f'**Net worth** {Emojis.Economy.cash} **{fnumb(balance.total)}**'
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
        return container

    async def _page_quests(self) -> discord.ui.Container:
        guild_id = self.ctx.guild.id  # type: ignore[union-attr]
        today = datetime.datetime.now(datetime.UTC).date()
        rows = await self.cog._ensure_quests(self.ctx.author.id, guild_id, today)
        board = {q.key: q for q in generate_daily_quests(guild_id, self.ctx.author.id, today)}

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
        row = await self.cog.bot.db.economy.get_job(self.ctx.author.id, guild_id)
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
        container.add_item(discord.ui.TextDisplay('-# Work with `work`, switch with `job apply <name>`.'))
        return container

    async def _page_pet(self) -> discord.ui.Container:
        guild_id = self.ctx.guild.id  # type: ignore[union-attr]
        row = await self.cog.bot.db.economy.get_pet(self.ctx.author.id, guild_id)
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
        container.add_item(discord.ui.TextDisplay('-# Feed with `pet feed`, collect with `pet claim`.'))
        return container

    async def _page_achievements(self) -> discord.ui.Container:
        guild_id = self.ctx.guild.id  # type: ignore[union-attr]
        earned_rows = await self.cog.bot.db.economy.get_achievements(self.ctx.author.id, guild_id)
        earned = {row['achievement'] for row in earned_rows}

        lines = [
            f'{a.emoji} **{a.name}** — {a.description}' if a.id in earned
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
        boosts = await db.economy.get_active_boosts(self.ctx.author.id, guild_id)
        prestige = await db.economy.get_prestige(self.ctx.author.id, guild_id)
        settings = await self.cog._settings(guild_id)

        lines = [boost_display_line(row) for row in boosts] or ['*No active boosts — usable shop items grant them.*']
        if prestige > 0:
            lines.append(f'\N{GLOWING STAR} **Prestige {prestige}** — permanent +{(prestige_multiplier(prestige) - 1) * 100:.0f}% payouts')
        if settings.payout_multiplier != 1.0:
            lines.append(f'\N{CHART WITH UPWARDS TREND} Server payout multiplier: **x{settings.payout_multiplier:.2f}**')

        level = prestige
        requirement = prestige_requirement(level)
        balance = await self.ctx.db.get_user_balance(self.ctx.author.id, guild_id)

        container = self._container('## \N{HIGH VOLTAGE SIGN} Perks & Boosts')
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay('\n'.join(lines)))
        container.add_item(discord.ui.TextDisplay(
            f'-# Next prestige at {Emojis.Economy.cash} {fnumb(requirement)} net worth '
            f'(`{progress_bar(balance.total, requirement)}` {fnumb(balance.total)}/{fnumb(requirement)}).'
        ))
        return container
