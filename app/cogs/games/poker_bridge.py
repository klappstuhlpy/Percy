"""Discord bridge for the pure Texas Hold'em engine.

:class:`PokerSession` owns the Discord-facing concerns the engine deliberately
does not: the message and view, embed rendering from the engine's state, the
autoplay timer, and the economy chip refund. The cog and UI talk to the engine
through this bridge (which exposes the engine as ``self.engine`` and proxies the
read-only state the renderer needs).
"""

from __future__ import annotations

import asyncio
import datetime
from typing import TYPE_CHECKING, cast

import discord

from app.cogs.games.engine.poker import Card, OddsMode, TableState, TexasHoldem
from app.cogs.games.models import Game, GameResult
from app.cogs.games.poker_ui import TableView
from app.utils import helpers, number_suffix
from config import Emojis

if TYPE_CHECKING:
    import numpy as np

    from app.cogs.games import Games
    from app.cogs.games.engine.cards import DisplayCard
    from app.cogs.games.engine.poker import HandResult, Player, Pot
    from app.core import Context

__all__ = (
    "OddsMode",
    "PokerSession",
    "TableState",
    "TexasHoldem",
)


class PokerSession:
    """Bridges a :class:`~app.cogs.games.engine.poker.TexasHoldem` engine to Discord.

    Parameters
    ----------
    cog : Games
        The Games cog (used for the ``poker_tables`` registry).
    ctx : Context
        The invoking context.
    first_buy_in : int
        The first buy-in for the game.
    decks : int
        The number of decks to use.
    max_players : int
        The maximum number of players allowed in the game.
    """

    def __init__(self, cog: Games, ctx: Context, *, first_buy_in: int, decks: int = 1, max_players: int = 4) -> None:
        self.cog: Games = cog
        self.ctx: Context = ctx

        self.engine: TexasHoldem = TexasHoldem(first_buy_in=first_buy_in, decks=decks, max_players=max_players)
        self.engine.host = ctx.author

        self.message: discord.Message | None = None
        self.view: TableView = TableView(session=self)

        # Event Loop
        self.loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        self.running_autoplay_loop: asyncio.Task | None = None

        # Guards :meth:`settle_round_stats` so each finished round is recorded once.
        self._round_settled: bool = True

    def __repr__(self) -> str:
        return f"<PokerSession engine={self.engine!r}>"

    # -- Engine state proxies (read-only, for the renderer and cog) -------

    @property
    def state(self) -> TableState:
        return self.engine.state

    @property
    def players(self) -> list[Player]:
        return self.engine.players

    @property
    def playing_players(self) -> list[Player]:
        return self.engine.playing_players

    @property
    def pot(self) -> Pot:
        return self.engine.pot

    @property
    def side_pots(self) -> list[Pot]:
        return self.engine.side_pots

    @property
    def small_blind(self) -> int:
        return self.engine.small_blind

    @property
    def big_blind(self) -> int:
        return self.engine.big_blind

    @property
    def min_buy_in(self) -> int:
        return self.engine.min_buy_in

    @property
    def max_buy_in(self) -> int:
        return self.engine.max_buy_in

    @property
    def player_index(self) -> int:
        return self.engine.player_index

    @property
    def blind_index(self) -> tuple[int, int] | None:
        return self.engine.blind_index

    @property
    def dealer_index(self) -> int | None:
        return self.engine.dealer_index

    @property
    def community_arr(self) -> np.ndarray:
        return self.engine.community_arr

    @property
    def winners(self) -> list[tuple[list[Player], Pot]]:
        return self.engine.winners

    @property
    def ranks(self) -> list[tuple[Player, HandResult]]:
        return self.engine.ranks

    @property
    def tie(self) -> bool:
        return self.engine.tie

    @property
    def first_buy_in(self) -> int:
        return self.engine.first_buy_in

    @property
    def analysis(self) -> list:
        return self.engine.analysis

    def add_player(self, member: discord.Member, stack: int) -> None:
        """Adds a player to the underlying engine."""
        self.engine.add_player(member, stack)

    # -- Autoplay timer ---------------------------------------------------

    def cancel_timer(self) -> None:
        """Cancels the running autoplay timer, if any."""
        if self.running_autoplay_loop is not None:
            self.running_autoplay_loop.cancel()

    def restart_timer(self) -> None:
        """(Re)starts the autoplay timer for the current player while the game runs.

        Doubles as the single chokepoint for round-end detection: every player
        action and autoplay turn funnels through here, so a transition to
        ``FINISHED`` settles the round's win/loss stats exactly once.
        """
        self.cancel_timer()
        if self.engine.state == TableState.RUNNING:
            self._round_settled = False
            self.running_autoplay_loop = self.loop.create_task(self.start_timer(self.engine.current_player))
        elif self.engine.state == TableState.FINISHED and not self._round_settled:
            self._round_settled = True
            self.loop.create_task(self.settle_round_stats())

    async def settle_round_stats(self) -> None:
        """|coro|

        Records the per-player outcome of the just-finished round. Everyone dealt
        into the round counts as a played round; members who took a share of any
        pot are credited a win, everyone else (including folders) a loss.
        """
        if self.ctx.guild is None:
            return

        winner_ids = {player.member.id for group, _ in self.winners for player in group}
        for player in self.players:
            result = GameResult.WIN if player.member.id in winner_ids else GameResult.LOSS
            await self.ctx.bot.db.game_stats.record_result(self.ctx.guild.id, player.member.id, Game.POKER, result)

    async def start_timer(self, player: Player) -> None:
        """A timer that runs out if the current player takes too long. (120 seconds)"""
        timer: int = 0
        while timer < 120:
            if self.engine.state != TableState.RUNNING:
                return

            await asyncio.sleep(1)
            timer += 1

            if self.engine.players[self.engine.player_index] != player:
                return

            if timer == 100 and self.message is not None:
                await self.message.edit(view=self.view.render(with_autoplay=True))

        await self.autoplay(player)

    async def autoplay(self, player: Player) -> None:
        """|coro|

        Automatically plays for a player if they take too long for their turn.
        """
        if not self.engine.autoplay_turn(player):
            return

        self.restart_timer()
        if self.message is not None:
            await self.message.edit(view=self.view.render())

    # -- Player management with side effects ------------------------------

    async def remove_player(self, member: discord.Member) -> None:
        """|coro|

        Removes a player from the engine and refunds their leftover chips.
        """
        stack_left = self.engine.remove_player(member)
        if stack_left > 0:
            assert self.ctx.guild is not None
            await self.ctx.bot.db.users.add_cash(member.id, self.ctx.guild.id, stack_left)

    async def prepare_next_game(self) -> None:
        """|coro|

        Prepares the next round and announces players who ran out of chips.
        """
        removed = self.engine.prepare_next_game()
        for player in removed:
            if self.message is not None:
                await self.message.reply(
                    f"\N{LEAF FLUTTERING IN WIND} {player.member.mention} has been removed from the game because they ran out of chips."
                )

    # -- Embed Builder ----------------------------------------------------

    def build_container(self, with_autoplay: bool = False, rows: list[discord.ui.ActionRow] | None = None) -> discord.ui.Container:
        """Build the Components V2 card for the table.

        Reuses the embed-field helpers by passing them a tiny collector that records
        ``add_field``/``set_footer``/``description``/``colour`` calls, then renders the
        collected fields as text displays inside a container.
        """
        container = discord.ui.Container(id=1)

        if self.state == TableState.STOPPED:
            self._build_stopped_container(container)
        else:
            self._build_running_container(container, with_autoplay)

        if rows:
            container.add_item(discord.ui.Separator())
            for row in rows:
                container.add_item(row)

        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(f"-# Players: {len(self.players)}/4"))
        return container

    def _build_stopped_container(self, container: discord.ui.Container) -> None:
        container.accent_colour = discord.Color.lighter_grey()

        container.add_item(discord.ui.TextDisplay("## Poker • Texas Hold'em"))
        container.add_item(discord.ui.Separator())

        container.add_item(discord.ui.TextDisplay(
            "*Waiting for players to join...*\n\n"
            f"Poker requires `2-4` players. The small blind and big blind are set to `{self.small_blind}` and `{self.big_blind}` Chips.\n"
            f"The minimum buy-in is `{self.min_buy_in}` Chips and the maximum buy-in is `{self.max_buy_in}` Chips.\n"
            "You can join the game by clicking the **Join** button below or click **Start** as the host to start the game."
        ))

        container.add_item(discord.ui.Separator())
        self._add_players_raw_to_container(container)

    def _build_running_container(self, container: discord.ui.Container, with_autoplay: bool = False) -> None:
        container.accent_colour = helpers.Colour.white()

        container.add_item(discord.ui.TextDisplay("## Poker • Texas Hold'em"))
        container.add_item(discord.ui.Separator())

        description = (
            f"**Small Blind:** `{self.small_blind}`\n"
            f"**Big Blind:** `{self.big_blind}`\n"
        )
        if self.engine.escalation_enabled:
            hands_left = self.engine.escalation_hands - self.engine.hands_at_level
            description += f"**Blind Level:** `{self.engine.blind_level}` ({hands_left} hands until increase)\n"
        description += f"\n**Pot:** {Emojis.Economy.coin} `{self.pot}`\n"

        for i, side_pot in enumerate(self.side_pots, start=1):
            description += f"**Side Pot *#{i}*:** {Emojis.Economy.coin} `{side_pot}`\n"

        container.add_item(discord.ui.TextDisplay(description))
        container.add_item(discord.ui.Separator())

        for index, player in enumerate(self.players, 1):
            name_parts = [f"Seat #{index}", player.member.display_name]
            text = f"**Stack:** {Emojis.Economy.coin} `{player.stack}`\n"

            if self.state == TableState.RUNNING:
                if index - 1 == self.player_index:
                    name_parts.insert(0, Emojis.Arrows.right)

                name_parts[0] = f"### {name_parts[0]}"
                assert self.blind_index is not None

                # Show dealer button (D), small blind (SB), big blind (BB), straddle (STR)
                position_tags = []
                if self.dealer_index is not None and index - 1 == self.dealer_index:
                    position_tags.append("D")
                if index - 1 == self.blind_index[0]:
                    position_tags.append("SB")
                if index - 1 == self.blind_index[1]:
                    position_tags.append("BB")
                if self.engine.straddle_index is not None and index - 1 == self.engine.straddle_index:
                    position_tags.append("STR")
                if position_tags:
                    name_parts.append("/".join(position_tags))

                text += f"**Current Bet:** {Emojis.Economy.coin} `{player.bet}`\n"

                if self.players[self.player_index] == player and with_autoplay:
                    text += f"*\N{ALARM CLOCK} Autoplay {
                        discord.utils.format_dt(discord.utils.utcnow() + datetime.timedelta(seconds=20), 'R')
                    }*\n"

            if player.sitting_out:
                name_parts.append("Away")

            if player.all_in:
                name_parts.append("All In")

            if player.folded:
                name_parts.append("Folded")
            else:
                if self.state == TableState.FINISHED:
                    won_lost_chips = (
                        f"+{sum(pot.amount // len(winners) for winners, pot in self.winners if player in winners)}"
                    )
                    if won_lost_chips == "+0":
                        won_lost_chips = f"-{player.bet}"

                    name_parts.append(f"{Emojis.Economy.coin} {won_lost_chips}")

                # Check if there is only one player that has not folded,
                # if there is, he does not need to show his cards and wins automatically
                if len(self.playing_players) != 1:
                    text = self._append_finished_container_text(player, text)
                else:
                    name_parts.append("👑")

            container.add_item(
                discord.ui.TextDisplay(
                    " • ".join(name_parts) + "\n" + text
                )
            )

        self._add_community_cards_to_container(container)

    def _add_players_raw_to_container(self, container: discord.ui.Container) -> None:
        for index, player in enumerate(self.players, 1):
            name = f"### Seat #{index} • {player.member.display_name}"
            text = f"**Stack:** {Emojis.Economy.coin} `{player.stack}`"

            container.add_item(discord.ui.TextDisplay(f"{name}\n{text}"))

    def _append_finished_container_text(self, player: Player, text: str) -> str:
        if self.state == TableState.FINISHED:
            # Check if player mucked their hand
            if player.mucked:
                text += "*Hand mucked*"
            else:
                raw_cards = [card.display("small") for card in player.hand.cards]
                cards = [cast("DisplayCard", c) for c in raw_cards]
                found = discord.utils.find(lambda x: x[0] == player, self.ranks)
                assert found is not None
                _, hand = found
                position = self.ranks.index((player, hand)) + 1

                hand_suffix = (
                    f"**{number_suffix(position)} Best Hand** 👑"
                    if position == 1
                    else f"{number_suffix(position)} Best Hand"
                    if not self.tie
                    else "**Tie**"
                )
                text += f"{cards[0].top} {cards[1].top} {hand.name}\n{cards[0].bottom} {cards[1].bottom} {hand_suffix}"
        return text

    def _add_community_cards_to_container(self, container: discord.ui.Container) -> None:
        cards = [Card.from_arr(arr) for arr in self.community_arr]

        if len(cards) >= 3:
            container.add_item(discord.ui.Separator())

            card_list = [
                f"{elem1} {elem2} {elem3}"
                for elem1, elem2, elem3 in zip(
                    *[cast("str", card.display("large", formatted=True)).split("\n") for card in cards[:3]]
                )
            ]
            container.add_item(discord.ui.TextDisplay('### The Flop\n' + '\n'.join(card_list)))

        if len(cards) >= 4:
            container.add_item(discord.ui.TextDisplay('### The Turn\n' + cards[3].display('large', formatted=True)))

        if len(cards) == 5:
            container.add_item(discord.ui.TextDisplay('### The River\n' + cards[4].display('large', formatted=True)))
