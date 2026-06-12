from __future__ import annotations

import datetime
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from app.cogs.games.engine.horserace import NUM_HORSES, TRACK_LENGTH
from app.core.views import LayoutView
from app.utils import fnumb, helpers, pluralize
from config import Emojis

if TYPE_CHECKING:
    from app.core.models import Context

__all__ = ("BETTING_SECONDS", "HORSE_COLOURS", "Bet", "HorseRaceView", "Table")

BETTING_SECONDS: int = 45
#: Per-horse identifier emoji (1-indexed display = colour index + 1).
HORSE_COLOURS = ("\N{LARGE RED CIRCLE}", "\N{LARGE ORANGE CIRCLE}", "\N{LARGE YELLOW CIRCLE}",
                 "\N{LARGE GREEN CIRCLE}", "\N{LARGE BLUE CIRCLE}", "\N{LARGE PURPLE CIRCLE}")


@dataclass(frozen=True)
class Bet:
    placed_by: discord.Member
    horse: int  # 1..NUM_HORSES
    amount: int


class Table:
    """Discord-facing horse-race table: owns the message, bets and lane rendering.

    The race itself (and payouts) is run by the cog's timer listener using the pure
    engine; this class only renders the betting board and the animation frames.
    """

    __slots__ = ("bets", "ctx", "message", "open", "start_time", "view")

    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx
        self.start_time = time.time()
        self.message: discord.Message | None = None
        self.bets: list[Bet] = []
        self.open: bool = True
        self.view = HorseRaceView(self)

    def place(self, bet: Bet) -> None:
        self.bets.append(bet)

    def total_on(self, horse: int) -> int:
        return sum(bet.amount for bet in self.bets if bet.horse == horse)

    @property
    def pool(self) -> int:
        return sum(bet.amount for bet in self.bets)

    def _lanes(self, positions: list[int] | None, winner: int | None) -> str:
        lanes = []
        for i in range(NUM_HORSES):
            pos = positions[i] if positions is not None else 0
            track = "\N{MIDDLE DOT}" * pos + "\N{HORSE}" + "\N{MIDDLE DOT}" * (TRACK_LENGTH - pos)
            crown = " \N{TROPHY}" if winner is not None and winner == i else ""
            total = self.total_on(i + 1)
            stake = f" {Emojis.Economy.cash}{fnumb(total)}" if total else ""
            lanes.append(f"{HORSE_COLOURS[i]}`{track}`\N{CHEQUERED FLAG}{crown}{stake}")
        return "\n".join(lanes)

    def build_container(
        self,
        positions: list[int] | None = None,
        winner: int | None = None,
        *,
        racing: bool = False,
    ) -> discord.ui.Container:
        colour = helpers.Colour.white()
        if winner is not None:
            colour = helpers.Colour.lime_green()
        elif racing:
            colour = helpers.Colour.light_orange()

        container = discord.ui.Container(accent_colour=colour)
        container.add_item(discord.ui.TextDisplay("## \N{HORSE} Horse Race"))

        if winner is not None:
            container.add_item(discord.ui.TextDisplay(f"\N{TROPHY} **Horse {winner + 1}** {HORSE_COLOURS[winner]} wins!"))
        elif racing:
            container.add_item(discord.ui.TextDisplay("**And they're off!** \N{HORSE}\N{DASH SYMBOL}"))
        else:
            time_left = datetime.timedelta(seconds=BETTING_SECONDS - (time.time() - self.start_time))
            closes = datetime.datetime.now() + time_left
            container.add_item(discord.ui.TextDisplay(
                f"Place your bets with `horserace <amount> <horse 1-{NUM_HORSES}>`.\n"
                f"*Gates close {discord.utils.format_dt(closes, style='R')}.*"
            ))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(self._lanes(positions, winner)))
        container.add_item(discord.ui.Separator())

        if self.bets:
            lines = [
                f"{HORSE_COLOURS[bet.horse - 1]} {bet.placed_by.mention} • {Emojis.Economy.cash} **{fnumb(bet.amount)}**"
                for bet in self.bets
            ]
            container.add_item(discord.ui.TextDisplay("\n".join(lines)))
        else:
            container.add_item(discord.ui.TextDisplay("*No bets placed yet.*"))

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            f"-# Pool: {Emojis.Economy.cash} {fnumb(self.pool)} • {pluralize(len(self.bets)):bet} placed."
        ))
        return container


class HorseRaceView(LayoutView):
    """Thin Components V2 wrapper around :meth:`Table.build_container` (no buttons —
    bets are placed via the ``horserace`` command)."""

    def __init__(self, table: Table) -> None:
        super().__init__(timeout=None)
        self.table = table
        self.render()

    def render(
        self,
        positions: list[int] | None = None,
        winner: int | None = None,
        *,
        racing: bool = False,
    ) -> HorseRaceView:
        self.clear_items()
        self.add_item(self.table.build_container(positions, winner, racing=racing))
        return self
