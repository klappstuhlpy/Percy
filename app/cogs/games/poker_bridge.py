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

from app.cogs.games.engine.poker import Card, TableState, TexasHoldem
from app.cogs.games.poker_ui import TableView
from app.utils import helpers, number_suffix
from config import Emojis

if TYPE_CHECKING:
    import numpy as np

    from app.cogs.games import Games
    from app.cogs.games.cards import DisplayCard
    from app.cogs.games.engine.poker import HandResult, Player, Pot
    from app.core import Context

__all__ = (
    'PokerSession',
    'TableState',
    'TexasHoldem',
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

    def __init__(
            self,
            cog: Games,
            ctx: Context,
            *,
            first_buy_in: int,
            decks: int = 1,
            max_players: int = 4
    ) -> None:
        self.cog: Games = cog
        self.ctx: Context = ctx

        self.engine: TexasHoldem = TexasHoldem(first_buy_in=first_buy_in, decks=decks, max_players=max_players)
        self.engine.host = ctx.author

        self.message: discord.Message | None = None
        self.view: TableView = TableView(session=self)

        # Event Loop
        self.loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
        self.running_autoplay_loop: asyncio.Task | None = None

    def __repr__(self) -> str:
        return f'<PokerSession engine={self.engine!r}>'

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

    def add_player(self, member: discord.Member, stack: int) -> None:
        """Adds a player to the underlying engine."""
        self.engine.add_player(member, stack)

    # -- Autoplay timer ---------------------------------------------------

    def cancel_timer(self) -> None:
        """Cancels the running autoplay timer, if any."""
        if self.running_autoplay_loop is not None:
            self.running_autoplay_loop.cancel()

    def restart_timer(self) -> None:
        """(Re)starts the autoplay timer for the current player while the game runs."""
        self.cancel_timer()
        if self.engine.state == TableState.RUNNING:
            self.running_autoplay_loop = self.loop.create_task(self.start_timer(self.engine.current_player))

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
                await self.message.edit(embed=self.build_embed(with_autoplay=True))

        await self.autoplay(player)

    async def autoplay(self, player: Player) -> None:
        """|coro|

        Automatically plays for a player if they take too long for their turn.
        """
        if not self.engine.autoplay_turn(player):
            return

        self.view.update_buttons()
        self.restart_timer()
        if self.message is not None:
            await self.message.edit(embed=self.build_embed(), view=self.view)

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
                    f'\N{LEAF FLUTTERING IN WIND} {player.member.mention} has been removed from the game because they ran out of chips.')

    # -- Embed Builder ----------------------------------------------------

    def build_embed(self, with_autoplay: bool = False) -> discord.Embed:
        """Builds the embed for the table"""
        embed = discord.Embed(title='Poker • Texas Hold\'em', color=helpers.Colour.white())
        embed.description = (
            '*Waiting for players to join...*\n\n' if self.state == TableState.PREPARED else ''
        )

        embed.description += (
            f'**Small Blind:** `{self.small_blind}`\n'
            f'**Big Blind:** `{self.big_blind}`\n\n'
            f'**Pot:** {Emojis.Economy.coin} `{self.pot}`\n'
        )

        for i, side_pot in enumerate(self.side_pots, start=1):
            embed.description += f'**Side Pot *#{i}*:** {Emojis.Economy.coin} `{side_pot}`\n'

        if self.state == TableState.STOPPED:
            self._build_stopped_embed(embed)
        else:
            self._build_running_embed(embed, with_autoplay)

        return embed

    def _build_stopped_embed(self, embed: discord.Embed) -> None:
        embed.colour = discord.Color.lighter_grey()
        embed.description = (
            '*Waiting for players to join...*\n\n'
            f'Poker requires `2-4` players. The small blind and big blind are set to `{self.small_blind}` and `{self.big_blind}` Chips.\n'
            f'The minimum buy-in is `{self.min_buy_in}` Chips and the maximum buy-in is `{self.max_buy_in}` Chips.\n'
            'You can join the game by clicking the **Join** button below or click **Start** as the host to start the game.'
        )
        embed.set_footer(text=f'Players: {len(self.players)}/4')
        self._add_players_raw_to_embed(embed)

    def _build_running_embed(self, embed: discord.Embed, with_autoplay: bool = False) -> None:
        for index, player in enumerate(self.players, 1):
            name_parts = [f'Seat #{index}', player.member.display_name]
            text = f'**Stack:** {Emojis.Economy.coin} `{player.stack}`\n'

            if self.state == TableState.RUNNING:
                if index - 1 == self.player_index:
                    name_parts.insert(0, Emojis.Arrows.right)

                assert self.blind_index is not None
                blind = 'BB' if index == self.blind_index[1] + 1 else 'SB' if index == self.blind_index[0] + 1 else None
                if blind is not None:
                    name_parts.append(blind)

                text += f'**Current Bet:** {Emojis.Economy.coin} `{player.bet}`\n'

                if self.players[self.player_index] == player and with_autoplay:
                    text += f'*\N{ALARM CLOCK} Autoplay {discord.utils.format_dt(
                        discord.utils.utcnow() + datetime.timedelta(seconds=20), 'R')}*\n'

            if player.all_in:
                name_parts.append('All In')

            if player.folded:
                name_parts.append('Folded')
            else:
                if self.state == TableState.FINISHED:
                    won_lost_chips = f'+{sum(pot.amount // len(winners) for winners, pot in self.winners if player in winners)}'
                    if won_lost_chips == '+0':
                        won_lost_chips = f'-{player.bet}'

                    name_parts.append(f'{Emojis.Economy.coin} {won_lost_chips}')

                # Check if there is only one player that has not folded,
                # if there is, he does not need to show his cards and wins automatically
                if len(self.playing_players) != 1:
                    text = self._append_finished_embed_text(player, text)
                else:
                    name_parts.append('👑')

            embed.add_field(name=' • '.join(name_parts), value=text, inline=False)

        self._add_community_cards_to_embed(embed)

    def _add_players_raw_to_embed(self, embed: discord.Embed) -> None:
        for index, player in enumerate(self.players, 1):
            name_parts = [f'Seat #{index}', player.member.display_name]
            text = f'**Stack:** {Emojis.Economy.coin} `{player.stack}`\n'

            embed.add_field(name=' • '.join(name_parts), value=text, inline=False)

    def _append_finished_embed_text(self, player: Player, text: str) -> str:
        if self.state == TableState.FINISHED:
            raw_cards = [card.display('small') for card in player.hand.cards]
            cards = [cast('DisplayCard', c) for c in raw_cards]
            found = discord.utils.find(lambda x: x[0] == player, self.ranks)
            assert found is not None
            _, hand = found
            position = self.ranks.index((player, hand)) + 1

            hand_suffix = (
                f'**{number_suffix(position)} Best Hand** 👑' if position == 1 else f'{number_suffix(position)} Best Hand'
                if not self.tie else '**Tie**'
            )
            text += (
                f'{cards[0].top} {cards[1].top} {hand.name}\n'
                f'{cards[0].bottom} {cards[1].bottom} {hand_suffix}'
            )
        return text

    def _add_community_cards_to_embed(self, embed: discord.Embed) -> None:
        cards = [Card.from_arr(arr) for arr in self.community_arr]
        if len(cards) >= 3:
            card_list = [f'{elem1} {elem2} {elem3}' for elem1, elem2, elem3 in zip(
                *[cast('str', card.display('large', formatted=True)).split('\n') for card in cards[:3]])]
            embed.add_field(
                name='The Flop',
                value='\n'.join(card_list)
            )
        if len(cards) >= 4:
            embed.add_field(
                name='The Turn',
                value=cards[3].display('large', formatted=True)
            )
        if len(cards) == 5:
            embed.add_field(
                name='The River',
                value=cards[4].display('large', formatted=True)
            )
