from __future__ import annotations

from contextlib import suppress
from itertools import zip_longest
from typing import TYPE_CHECKING, cast

import discord

from app.cogs.games.engine.poker import OddsMode, TableState
from app.core.views import LayoutView
from app.rendering.models import BarChartData
from app.utils import fnumb, helpers
from config import Emojis

if TYPE_CHECKING:
    from app.cogs.games.engine.poker import Player, TexasHoldem
    from app.cogs.games.poker_bridge import PokerSession
    from app.core import Bot
    from app.database.base import Balance

__all__ = (
    "BuyInModal",
    "RaiseBetModal",
    "SetBlindsModal",
    "TableView",
)


class RaiseBetModal(discord.ui.Modal, title="Bet/Raise"):
    amount = discord.ui.TextInput(
        label="Amount", placeholder="Enter the amount you want to raise by", min_length=1, max_length=10
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class BuyInModal(discord.ui.Modal, title="Buy-In"):
    amount = discord.ui.TextInput(label="Amount", min_length=1, max_length=10)

    def __init__(self, engine: TexasHoldem) -> None:
        super().__init__(timeout=100.0)
        self.amount.placeholder = f"Enter your buy-in amount. (Min: {engine.min_buy_in}, Max: {engine.max_buy_in})"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class SetBlindsModal(discord.ui.Modal, title="Set Custom Big Blind"):
    big_blind = discord.ui.TextInput(label="Big Blind", min_length=1, max_length=10)

    def __init__(self, min_blind: int, max_blind: int) -> None:
        super().__init__(timeout=100.0)
        self.big_blind.placeholder = f"Enter the big blind amount. (Min: {min_blind}, Max: {max_blind})"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class RebuyModal(discord.ui.Modal, title="Rebuy / Add Chips"):
    amount = discord.ui.TextInput(label="Amount", min_length=1, max_length=10)

    def __init__(self, current_stack: int, max_buy_in: int) -> None:
        super().__init__(timeout=100.0)
        max_rebuy = max_buy_in - current_stack
        self.amount.placeholder = f"Enter rebuy amount. (Max: {max_rebuy})"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class EscalationModal(discord.ui.Modal, title="Blind Escalation Settings"):
    hands = discord.ui.TextInput(
        label="Hands per level",
        placeholder="Number of hands before blinds increase (e.g., 10)",
        default="10",
        min_length=1,
        max_length=3,
    )
    multiplier = discord.ui.TextInput(
        label="Increase multiplier",
        placeholder="Multiplier for blind increase (e.g., 1.5 = 50%)",
        default="1.5",
        min_length=1,
        max_length=4,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class TableView(LayoutView):
    """The Components V2 view for a poker table.

    Holds a reference to the :class:`~app.cogs.games.poker_bridge.PokerSession` bridge.
    Button callbacks trigger actions on the pure engine (``self.engine``); :meth:`render`
    recomposes the table card (from the session) plus the state-appropriate control rows.
    """

    def __init__(self, session: PokerSession) -> None:
        self.session: PokerSession = session
        self.engine: TexasHoldem = session.engine
        super().__init__(timeout=500.0, members=[p.member for p in session.players])

        self.join = discord.ui.Button(label="Join", style=discord.ButtonStyle.grey)
        self.join.callback = self._on_join
        self.my_hand = discord.ui.Button(label="My Hand", style=discord.ButtonStyle.blurple)
        self.my_hand.callback = self._on_my_hand
        self.start_next_round = discord.ui.Button(label="Start", style=discord.ButtonStyle.green, disabled=True)
        self.start_next_round.callback = self._on_start_next_round
        self.fold = discord.ui.Button(label="Fold", style=discord.ButtonStyle.red)
        self.fold.callback = self._on_fold
        self.check_call = discord.ui.Button(label="Check", style=discord.ButtonStyle.grey)
        self.check_call.callback = self._on_check_call
        self.raise_bet = discord.ui.Button(label="Raise", style=discord.ButtonStyle.blurple)
        self.raise_bet.callback = self._on_raise_bet
        self.all_in = discord.ui.Button(label="All In", style=discord.ButtonStyle.red)
        self.all_in.callback = self._on_all_in
        self.analysis_button = discord.ui.Button(
            label="Show Analysis", style=discord.ButtonStyle.blurple, emoji="\N{BAR CHART}"
        )
        self.analysis_button.callback = self._on_analysis
        self.leave_button = discord.ui.Button(label="Leave", style=discord.ButtonStyle.red)
        self.leave_button.callback = self._on_leave
        self.set_blinds_button = discord.ui.Button(label="Set Blinds", style=discord.ButtonStyle.blurple)
        self.set_blinds_button.callback = self._on_set_blinds
        self.odds_mode_button = discord.ui.Button(label="Odds: Live", style=discord.ButtonStyle.grey, emoji="\N{BAR CHART}")
        self.odds_mode_button.callback = self._on_toggle_odds_mode
        self.rebuy_button = discord.ui.Button(label="Rebuy", style=discord.ButtonStyle.green, emoji=Emojis.Economy.coin)
        self.rebuy_button.callback = self._on_rebuy
        self.sit_out_button = discord.ui.Button(label="Sit Out", style=discord.ButtonStyle.grey, emoji="\N{PERSON IN LOTUS POSITION}")
        self.sit_out_button.callback = self._on_sit_out
        self.escalation_button = discord.ui.Button(label="Escalation: Off", style=discord.ButtonStyle.grey, emoji="\N{CHART WITH UPWARDS TREND}")
        self.escalation_button.callback = self._on_toggle_escalation
        self.straddle_button = discord.ui.Button(label="Straddle", style=discord.ButtonStyle.blurple, emoji="\N{MONEY BAG}")
        self.straddle_button.callback = self._on_straddle
        self.history_button = discord.ui.Button(label="History", style=discord.ButtonStyle.grey, emoji="\N{SCROLL}")
        self.history_button.callback = self._on_view_history
        self.muck_button = discord.ui.Button(label="Muck", style=discord.ButtonStyle.grey, emoji="\N{NO ENTRY SIGN}")
        self.muck_button.callback = self._on_muck

        self.container: discord.ui.Container = discord.ui.Container(id=1)

        self.render()

    async def on_timeout(self) -> None:
        """Called when the view times out."""
        for player in self.engine.players:
            await self.session.remove_player(player.member)

        if self.session.message is not None:
            with suppress(KeyError):
                del self.session.cog.poker_tables[self.session.message.channel.id]

            with suppress(discord.HTTPException):
                await self.session.message.reply(f"{Emojis.error} The table has been closed due to inactivity.")
                await self.session.message.delete()

    # Composition

    def render(self, with_autoplay: bool = False) -> TableView:
        """Recompose the layout: the table card plus the state-appropriate control rows."""
        self.clear_items()
        self.add_item(self.session.build_container(with_autoplay, self._button_rows()))
        return self

    def update_buttons(self) -> None:
        """Backwards-compatible alias; recomposition now happens in :meth:`render`."""
        self.render()

    def _button_rows(self) -> list[discord.ui.ActionRow]:
        engine = self.engine
        if engine.state != TableState.RUNNING:
            stopped_or_prepared = engine.state in (TableState.STOPPED, TableState.PREPARED)
            self.start_next_round.label = "Start" if stopped_or_prepared else "Next Round"
            self.start_next_round.disabled = len(engine.players) < 2
            self.join.disabled = len(engine.players) == 4

            # Update odds mode button label
            mode_labels = {OddsMode.NONE: "Odds: Off", OddsMode.LIVE: "Odds: Live", OddsMode.FULL: "Odds: Full"}
            self.odds_mode_button.label = mode_labels[engine.odds_mode]

            # Update escalation button label
            if engine.escalation_enabled:
                self.escalation_button.label = f"Esc: Lv{engine.blind_level}"
                self.escalation_button.style = discord.ButtonStyle.green
            else:
                self.escalation_button.label = "Escalation: Off"
                self.escalation_button.style = discord.ButtonStyle.grey

            rows = [discord.ui.ActionRow(self.join, self.start_next_round, self.leave_button, self.sit_out_button)]
            second: list[discord.ui.Button] = []
            third: list[discord.ui.Button] = []
            if stopped_or_prepared:
                second.append(self.set_blinds_button)
                second.append(self.odds_mode_button)
                second.append(self.rebuy_button)
                third.append(self.escalation_button)
                if engine.hand_history:
                    third.append(self.history_button)
            if engine.state == TableState.FINISHED:
                if engine.odds_mode != OddsMode.NONE and engine.analysis:
                    second.append(self.analysis_button)
                second.append(self.odds_mode_button)
                second.append(self.rebuy_button)
                third.append(self.escalation_button)
                if engine.hand_history:
                    third.append(self.history_button)
                # Add muck button (shown but availability checked per-player in callback)
                third.append(self.muck_button)
            if second:
                rows.append(discord.ui.ActionRow(*second))
            if third:
                rows.append(discord.ui.ActionRow(*third))
            return rows

        # running
        self.join.disabled = True
        self.start_next_round.label = "Start" if engine.state == TableState.PREPARED else "Next Round"
        self.start_next_round.disabled = True

        # Raise/Bet is always available (blinds can raise when action comes back to them)
        self.raise_bet.disabled = False
        self.raise_bet.label = "Bet" if all(player.bet <= engine.big_blind for player in engine.playing_players) else "Raise"

        is_check = engine.players[engine.player_index].bet == max(player.bet for player in engine.players)
        call_amount = max(player.bet for player in engine.players) - engine.players[engine.player_index].bet
        self.check_call.label = "Check" if is_check else f"Call ({call_amount} Chips)"
        self.check_call.emoji = None if is_check else Emojis.Economy.coin

        if not is_check and engine.players[engine.player_index].stack < call_amount:
            self.check_call.disabled = True
            self.check_call.style = discord.ButtonStyle.grey
        else:
            self.check_call.disabled = False
            self.check_call.style = discord.ButtonStyle.grey if is_check else discord.ButtonStyle.green

        # Build action rows
        rows = [
            discord.ui.ActionRow(self.join, self.my_hand, self.start_next_round),
            discord.ui.ActionRow(self.fold, self.check_call, self.raise_bet, self.all_in),
        ]

        # Add straddle button if available (only pre-flop, UTG, no community cards yet)
        if (engine.straddle_enabled and
            len(engine.community_arr) == 0 and
            engine.straddle_index is None and
            len(engine.players) >= 3):
            self.straddle_button.label = f"Straddle ({engine.big_blind * 2})"
            rows.append(discord.ui.ActionRow(self.straddle_button))

        return rows

    # Buttons

    async def _on_join(self, interaction: discord.Interaction) -> None:
        """Joins the table"""
        if self.engine.state != TableState.STOPPED:
            await interaction.response.send_message(f"{Emojis.error} The table is already running.", ephemeral=True)
            return

        if interaction.user in [player.member for player in self.engine.players]:
            await interaction.response.send_message(f"{Emojis.error} You are already in the game.", ephemeral=True)
            return

        modal = BuyInModal(engine=self.engine)
        await interaction.response.send_modal(modal)
        await modal.wait()
        with suppress(AttributeError):
            interaction = modal.interaction

        try:
            amount = int(modal.amount.value)
        except ValueError:
            await interaction.response.send_message(f"{Emojis.error} Invalid amount.", ephemeral=True)
            return

        balance: Balance = await cast("Bot", interaction.client).db.get_user_balance(
            interaction.user.id, interaction.guild_id
        )
        if balance.cash < amount:
            await interaction.response.send_message(
                f"{Emojis.error} You don't have enough **cash** money to buy yourself in.\n"
                f"You need at least {Emojis.Economy.coin} **{fnumb(self.engine.min_buy_in)}**.",
                ephemeral=True,
            )
            return

        await balance.remove(cash=amount)
        self.engine.add_player(cast("discord.Member", interaction.user), stack=amount)

        view = self
        if len(self.engine.players) == 4:
            self.engine.start()
            view = TableView(session=self.session)
            self.session.restart_timer()

        self.members = [player.member for player in self.engine.players]
        await interaction.response.edit_message(view=view.render())

    async def _on_my_hand(self, interaction: discord.Interaction) -> None:
        """Shows the player's hand"""
        player = discord.utils.get(self.engine.players, member=interaction.user)
        if not player:
            await interaction.response.send_message(f"{Emojis.error} You are not in the game.", ephemeral=True)
            return

        if self.engine.state != TableState.RUNNING:
            await interaction.response.send_message(f"{Emojis.error} The game has not started yet.", ephemeral=True)
            return

        embed = discord.Embed(title="Your Cards", color=discord.Color.blurple())

        card_list = [
            f"{elem1} {elem2}"
            for elem1, elem2 in zip(
                *[cast("str", card.display("large", formatted=True)).split("\n") for card in player.hand.cards]
            )
        ]
        embed.description = "\n".join(card_list)

        # Returns your best hand
        hand = player.hand.evaluate(self.engine.community_arr)

        card_list = [cast("str", card.display("large", formatted=True)).split("\n") for card in hand.cards]
        # Use zip_longest to handle different lengths of display elements in each card
        results = [
            " ".join(filter(None, elems))  # filter(None) removes empty strings
            for elems in zip_longest(*card_list, fillvalue="")
        ]

        embed.description += f"\n\n**Your Best Hand: *{hand.name}* **\n" + "\n".join(results)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    async def _on_start_next_round(self, interaction: discord.Interaction) -> None:
        """Starts the game"""
        await interaction.response.defer()

        if self.engine.state == TableState.RUNNING:
            await interaction.followup.send(f"{Emojis.error} The table is already running.", ephemeral=True)
            return

        if interaction.user != self.engine.host:
            await interaction.followup.send(
                f"{Emojis.error} You are not the host of this table.\n"
                f"Please aks {self.engine.host.mention} to start the game!",
                ephemeral=True,
            )
            return

        if len(self.engine.players) < 2:
            await interaction.followup.send(f"{Emojis.error} You need at least 2 players to start the game.", ephemeral=True)
            return

        view = self
        if self.start_next_round.label == "Next Round":
            await self.session.prepare_next_game()
        else:
            self.session.view = view = TableView(session=self.session)
            self.engine.start()
            self.session.restart_timer()

        await interaction.edit_original_response(view=view.render())

    async def _on_fold(self, interaction: discord.Interaction) -> None:
        """Folds the player's hand"""
        player = await self.get_player(interaction)
        if not player:
            return

        self.session.cancel_timer()

        self.engine.Fold()
        self.engine.switch_player()
        self.session.restart_timer()

        await interaction.response.edit_message(view=self.render())

    async def _on_check_call(self, interaction: discord.Interaction) -> None:
        """Checks or calls"""
        player = await self.get_player(interaction)
        if not player:
            return

        self.session.cancel_timer()

        max_bet = max(p.bet for p in self.engine.players)
        if player.bet == max_bet:
            self.engine.Check()
        else:
            if player.stack < max_bet - player.bet:
                await interaction.response.send_message(
                    f"{Emojis.error} You don't have enough chips. You'll need to go **All-In**!", ephemeral=True
                )
                return

            self.engine.Call()

        self.engine.switch_player()
        self.session.restart_timer()
        await interaction.response.edit_message(view=self.render())

    async def _on_raise_bet(self, interaction: discord.Interaction) -> None:
        """Raises the bet"""
        player = await self.get_player(interaction)
        if not player:
            return

        self.session.cancel_timer()

        modal = RaiseBetModal()
        await interaction.response.send_modal(modal)
        await modal.wait()
        interaction = modal.interaction

        try:
            amount = int(modal.amount.value)
        except ValueError:
            await interaction.response.send_message(f"{Emojis.error} Invalid amount.", ephemeral=True)
            return

        # Calculate minimum raise
        current_max_bet = max(p.bet for p in self.engine.players)

        is_bet = current_max_bet <= self.engine.big_blind
        if is_bet:
            # First bet of the round: minimum is the big blind
            min_raise = self.engine.big_blind
            if amount < min_raise:
                await interaction.response.send_message(
                    f"You have to bet at least the big blind (**{min_raise}** Chips).", ephemeral=True
                )
                return
        else:
            # Raise: minimum raise increment is the big blind (standard poker rule)
            # The total bet must be at least current_max_bet + big_blind
            min_total_bet = current_max_bet + self.engine.big_blind
            min_raise_amount = min_total_bet - player.bet  # Amount player needs to add

            if amount < min_raise_amount:
                await interaction.response.send_message(
                    f"You have to raise to at least **{min_total_bet}** Chips "
                    f"(add at least **{min_raise_amount}** Chips).", ephemeral=True
                )
                return

        if amount > player.stack:
            await interaction.response.send_message(f"{Emojis.error} You don't have enough chips.", ephemeral=True)
            return

        # check if its all-in
        if amount == player.stack:
            self.engine.AllIn()
        else:
            self.engine.Raise(amount)

        self.engine.switch_player(by_raise=True)
        self.session.restart_timer()

        await interaction.response.edit_message(view=self.render())

    async def _on_all_in(self, interaction: discord.Interaction) -> None:
        """Goes all in"""
        player = await self.get_player(interaction)
        if not player:
            return

        self.session.cancel_timer()

        self.engine.AllIn()
        self.engine.switch_player(by_raise=True)
        self.session.restart_timer()

        await interaction.response.edit_message(view=self.render())

    async def get_player(self, interaction: discord.Interaction) -> Player | None:
        player = self.engine.players[self.engine.player_index]
        if not player:
            await interaction.response.send_message(f"{Emojis.error} You are not in the game.", ephemeral=True)
            return None

        if player.member != interaction.user:
            await interaction.response.send_message(f"{Emojis.error} It's not your turn.", ephemeral=True)
            return None

        return player

    async def _on_analysis(self, interaction: discord.Interaction) -> None:
        """Callback for the analysis button"""
        await interaction.response.defer()

        if self.engine.state != TableState.FINISHED:
            await interaction.followup.send(
                f"{Emojis.error} The table is currently running, please wait till the game is finished.", ephemeral=True
            )
            return

        if not self.engine.analysis:
            await interaction.followup.send(
                f"{Emojis.error} No analysis data available. Odds calculation may have been disabled.", ephemeral=True
            )
            return

        embed = discord.Embed(title="Game Odds Analysis", color=helpers.Colour.white())
        # Data format: (live_odds, full_odds, hand_strength) per street
        data: list[tuple[dict[str, float], dict[str, float], dict[int, dict[str, float]]]] = self.engine.analysis

        # Determine which odds to display based on current mode
        use_live = self.engine.odds_mode == OddsMode.LIVE
        mode_label = "Live" if use_live else "Full"

        embeds, files = [], []
        for index, player in enumerate(self.engine.players):
            embed = embed.copy()
            d_index = index + 1

            embed.set_author(
                name=f"{player.member.display_name} | Seat #{d_index}", icon_url=player.member.display_avatar.url
            )

            # Check when this player folded
            folded_street = player.folded_on_street

            embed.description = (
                f"**Mode:** {mode_label} Odds"
                + (" *(shows real equity among active players)*" if use_live else " *(hypothetical: what if everyone stayed in)*")
                + "\nThe River is not included as the game is already over.\n\n"
            )

            street_names = ["Pre-Flop", "Flop", "Turn"]
            for street_idx, street_name in enumerate(street_names[:len(data)]):
                # Index 0 for live, 1 for full
                odds_idx = 0 if use_live else 1
                odds = data[street_idx][odds_idx]
                win_pct = odds.get(f'Player {d_index} Win', 0.0)
                tie_pct = odds.get(f'Player {d_index} Tie', 0.0)

                # Show fold indicator
                fold_indicator = ""
                if folded_street is not None and street_idx >= folded_street:
                    fold_indicator = " *(folded)*" if use_live else ""

                embed.description += f"{street_name}: Win: **{win_pct}**% | Tie: **{tie_pct}**%{fold_indicator}\n"

            if not data:
                embed.description += "***NO DATA***"

            TITLE_MAP = {0: f"Seat #{d_index} - Hand Strength Analysis | Pre-Flop", 1: "Flop", 2: "Turn"}
            specs = [
                BarChartData(
                    data=dict(data[i][2][d_index].items()),  # Index 2 is hand_strength
                    title=TITLE_MAP.get(i, "---"),
                )
                for i in range(len(data))
            ]
            image = await cast("Bot", interaction.client).render.merge_bar_charts(specs, filename=f"bar_chart-{index}.png")

            embed.set_image(url=f"attachment://bar_chart-{index}.png")
            embeds.append(embed)
            files.append(image)

        await interaction.followup.send(embeds=embeds, files=files, ephemeral=True)

    async def _on_toggle_odds_mode(self, interaction: discord.Interaction) -> None:
        """Callback for the odds mode toggle button (host only)."""
        if interaction.user != self.engine.host:
            await interaction.response.send_message(
                f"{Emojis.error} Only the table host can change the odds mode.", ephemeral=True
            )
            return

        # Cycle through modes: LIVE -> FULL -> NONE -> LIVE
        mode_cycle = {OddsMode.LIVE: OddsMode.FULL, OddsMode.FULL: OddsMode.NONE, OddsMode.NONE: OddsMode.LIVE}
        self.engine.odds_mode = mode_cycle[self.engine.odds_mode]

        mode_descriptions = {
            OddsMode.NONE: "Odds calculation **disabled** - no analysis will be generated.",
            OddsMode.LIVE: "**Live Odds** - shows real equity among active players only.",
            OddsMode.FULL: "**Full Odds** - shows hypothetical odds if everyone stayed in.",
        }

        await interaction.response.edit_message(view=self.render())
        await interaction.followup.send(
            f"\N{BAR CHART} {mode_descriptions[self.engine.odds_mode]}", ephemeral=True
        )

    async def _on_muck(self, interaction: discord.Interaction) -> None:
        """Callback for mucking hand (hiding losing cards)."""
        if not self.engine.can_muck(cast("discord.Member", interaction.user)):
            player = discord.utils.get(self.engine.players, member=interaction.user)
            if player is None:
                await interaction.response.send_message(
                    f"{Emojis.error} You are not in the game.", ephemeral=True
                )
            elif player.mucked:
                await interaction.response.send_message(
                    f"{Emojis.error} You already mucked your hand.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"{Emojis.error} You cannot muck - either the game isn't finished or you won.",
                    ephemeral=True,
                )
            return

        self.engine.muck_hand(cast("discord.Member", interaction.user))
        await interaction.response.edit_message(view=self.render())
        if self.session.message is not None:
            await self.session.message.reply(
                f"\N{NO ENTRY SIGN} {interaction.user.mention} mucks their hand.",
                delete_after=10,
            )

    async def _on_view_history(self, interaction: discord.Interaction) -> None:
        """Callback for viewing hand history."""
        if not self.engine.hand_history:
            await interaction.response.send_message(
                f"{Emojis.error} No hand history available yet.", ephemeral=True
            )
            return

        embeds = []
        # Show last 5 hands (most recent first)
        for entry in reversed(self.engine.hand_history[-5:]):
            embed = discord.Embed(
                title=f"Hand #{entry.hand_number}",
                color=helpers.Colour.white(),
            )
            embed.description = (
                f"**Blinds:** {entry.blinds[0]}/{entry.blinds[1]}\n"
                f"**Pot:** {Emojis.Economy.coin} {fnumb(entry.pot_total)}\n"
                f"**Winner(s):** {', '.join(entry.winners)}\n"
            )
            if entry.winning_hand:
                embed.description += f"**Winning Hand:** {entry.winning_hand}\n"

            # Show community cards
            if entry.community_cards:
                from app.cogs.games.engine.poker import Card
                cards = [Card(value=v, suit=s) for v, s in entry.community_cards]
                card_str = " ".join(f"`{c.display_text_short}`" for c in cards)
                embed.add_field(name="Board", value=card_str, inline=False)

            # Show actions (truncated if too long)
            if entry.actions:
                actions_text = "\n".join(entry.actions[-8:])  # Last 8 actions
                if len(entry.actions) > 8:
                    actions_text = f"*...{len(entry.actions) - 8} earlier actions...*\n" + actions_text
                embed.add_field(name="Actions", value=actions_text, inline=False)

            embed.set_footer(text=entry.timestamp[:19].replace("T", " "))
            embeds.append(embed)

        await interaction.response.send_message(embeds=embeds, ephemeral=True)

    async def _on_straddle(self, interaction: discord.Interaction) -> None:
        """Callback for posting a straddle."""
        if not self.engine.can_straddle(cast("discord.Member", interaction.user)):
            await interaction.response.send_message(
                f"{Emojis.error} You cannot straddle right now. Only UTG can straddle before acting.",
                ephemeral=True,
            )
            return

        success = self.engine.post_straddle(cast("discord.Member", interaction.user))
        if success:
            self.session.cancel_timer()
            self.session.restart_timer()
            await interaction.response.edit_message(view=self.render())
            if self.session.message is not None:
                await self.session.message.reply(
                    f"\N{MONEY BAG} {interaction.user.mention} posts a **straddle** of {Emojis.Economy.coin} **{self.engine.straddle_amount}**!",
                    delete_after=10,
                )
        else:
            await interaction.response.send_message(
                f"{Emojis.error} Failed to post straddle. You may not have enough chips.",
                ephemeral=True,
            )

    async def _on_toggle_escalation(self, interaction: discord.Interaction) -> None:
        """Callback for the blind escalation toggle button (host only)."""
        if interaction.user != self.engine.host:
            await interaction.response.send_message(
                f"{Emojis.error} Only the table host can change escalation settings.", ephemeral=True
            )
            return

        if self.engine.state == TableState.RUNNING:
            await interaction.response.send_message(
                f"{Emojis.error} Cannot change escalation during a hand.", ephemeral=True
            )
            return

        if self.engine.escalation_enabled:
            # Disable escalation
            self.engine.set_escalation(enabled=False)
            await interaction.response.edit_message(view=self.render())
            await interaction.followup.send(
                "\N{CHART WITH UPWARDS TREND} Blind escalation **disabled**.", ephemeral=True
            )
        else:
            # Show modal to configure escalation
            modal = EscalationModal()
            await interaction.response.send_modal(modal)
            await modal.wait()
            with suppress(AttributeError):
                interaction = modal.interaction

            try:
                hands = int(modal.hands.value)
                multiplier = float(modal.multiplier.value)
            except ValueError:
                await interaction.response.send_message(f"{Emojis.error} Invalid values.", ephemeral=True)
                return

            if hands < 1 or hands > 100:
                await interaction.response.send_message(
                    f"{Emojis.error} Hands per level must be between 1 and 100.", ephemeral=True
                )
                return

            if multiplier < 1.1 or multiplier > 3.0:
                await interaction.response.send_message(
                    f"{Emojis.error} Multiplier must be between 1.1 and 3.0.", ephemeral=True
                )
                return

            self.engine.set_escalation(enabled=True, hands=hands, multiplier=multiplier)
            await interaction.response.edit_message(view=self.render())
            await interaction.followup.send(
                f"\N{CHART WITH UPWARDS TREND} Blind escalation **enabled**!\n"
                f"Blinds will increase by **{int((multiplier - 1) * 100)}%** every **{hands}** hands.",
                ephemeral=True,
            )

    async def _on_sit_out(self, interaction: discord.Interaction) -> None:
        """Callback for the sit out / sit in toggle button."""
        player = discord.utils.get(self.engine.players, member=interaction.user)
        if not player:
            await interaction.response.send_message(
                f"{Emojis.error} You are not in the game.", ephemeral=True
            )
            return

        if player.sitting_out:
            # Return to play
            self.engine.sit_in(cast("discord.Member", interaction.user))
            await interaction.response.edit_message(view=self.render())
            if self.session.message is not None:
                await self.session.message.reply(
                    f"\N{PERSON IN LOTUS POSITION} {interaction.user.mention} is back in the game!",
                    delete_after=10,
                )
        else:
            # Sit out
            self.engine.sit_out(cast("discord.Member", interaction.user))
            await interaction.response.edit_message(view=self.render())
            if self.session.message is not None:
                await self.session.message.reply(
                    f"\N{PERSON IN LOTUS POSITION} {interaction.user.mention} is sitting out (will auto-fold until they return).",
                    delete_after=10,
                )

    async def _on_rebuy(self, interaction: discord.Interaction) -> None:
        """Callback for the rebuy button."""
        if self.engine.state == TableState.RUNNING:
            await interaction.response.send_message(
                f"{Emojis.error} Cannot rebuy during a hand. Wait for the hand to finish.", ephemeral=True
            )
            return

        # Check if user is a player
        player = discord.utils.get(self.engine.players, member=interaction.user)
        if not player:
            await interaction.response.send_message(
                f"{Emojis.error} You are not in the game. Use **Join** to enter.", ephemeral=True
            )
            return

        # Check if already at max
        if player.stack >= self.engine.max_buy_in:
            await interaction.response.send_message(
                f"{Emojis.error} You are already at the maximum stack ({fnumb(self.engine.max_buy_in)} chips).", ephemeral=True
            )
            return

        modal = RebuyModal(current_stack=player.stack, max_buy_in=self.engine.max_buy_in)
        await interaction.response.send_modal(modal)
        await modal.wait()
        with suppress(AttributeError):
            interaction = modal.interaction

        try:
            amount = int(modal.amount.value)
        except ValueError:
            await interaction.response.send_message(f"{Emojis.error} Invalid amount.", ephemeral=True)
            return

        if amount <= 0:
            await interaction.response.send_message(f"{Emojis.error} Amount must be positive.", ephemeral=True)
            return

        max_rebuy = self.engine.max_buy_in - player.stack
        if amount > max_rebuy:
            await interaction.response.send_message(
                f"{Emojis.error} Maximum rebuy is {fnumb(max_rebuy)} chips.", ephemeral=True
            )
            return

        # Check balance
        balance: Balance = await cast("Bot", interaction.client).db.get_user_balance(
            interaction.user.id, interaction.guild_id
        )
        if balance.cash < amount:
            await interaction.response.send_message(
                f"{Emojis.error} You don't have enough cash. You have {Emojis.Economy.coin} **{fnumb(balance.cash)}**.",
                ephemeral=True,
            )
            return

        # Deduct and add chips
        await balance.remove(cash=amount)
        success = self.engine.rebuy(cast("discord.Member", interaction.user), amount)

        if success:
            await interaction.response.edit_message(view=self.render())
            if self.session.message is not None:
                await self.session.message.reply(
                    f"{Emojis.Economy.coin} {interaction.user.mention} added **{fnumb(amount)}** chips (now at **{fnumb(player.stack)}**).",
                    delete_after=10,
                )
        else:
            # Refund if engine rejected
            await balance.add(cash=amount)
            await interaction.response.send_message(f"{Emojis.error} Rebuy failed.", ephemeral=True)

    async def _on_leave(self, interaction: discord.Interaction) -> None:
        """Callback for the leave button"""
        if self.engine.state == TableState.RUNNING:
            await interaction.response.send_message(
                f"{Emojis.error} The table is currently running, please wait till the game is finished.", ephemeral=True
            )
            return

        if interaction.user not in [player.member for player in self.engine.players]:
            await interaction.response.send_message(f"{Emojis.error} You are not in the game.", ephemeral=True)
            return

        await self.session.remove_player(cast("discord.Member", interaction.user))

        if len(self.engine.players) == 1:
            self.engine.state = TableState.STOPPED
        elif len(self.engine.players) == 0:
            with suppress(KeyError):
                del self.session.cog.poker_tables[self.session.ctx.channel.id]
            if self.session.message is not None:
                await self.session.message.delete()

            await interaction.response.send_message(
                "\N{LEAF FLUTTERING IN WIND} The Poker Table has been closed due to all players leaving.", delete_after=10
            )
            return

        await interaction.response.edit_message(view=self.render())
        if self.session.message is not None:
            await self.session.message.reply(
                f"\N{LEAF FLUTTERING IN WIND} {interaction.user.mention} has left the table.", delete_after=10
            )

    async def _on_set_blinds(self, interaction: discord.Interaction) -> None:
        """Callback for the set blinds button"""
        if self.engine.state == TableState.RUNNING:
            await interaction.response.send_message(
                f"{Emojis.error} The table is currently running, please wait till the game is finished.", ephemeral=True
            )
            return

        if interaction.user != self.engine.host:
            await interaction.response.send_message(
                f"{Emojis.error} You are not the host of this table.\n"
                f"Please ask {self.engine.host.mention} to set the blinds!",
                ephemeral=True,
            )
            return

        min_blind = max(1, int(self.engine.first_buy_in * 0.005))  # 0.5% of the buy-in
        max_blind = int(self.engine.first_buy_in * 0.05)  # 5% of the buy-in

        modal = SetBlindsModal(min_blind, max_blind)
        await interaction.response.send_modal(modal)
        await modal.wait()
        interaction = modal.interaction

        try:
            big_blind = int(modal.big_blind.value)
        except ValueError:
            await interaction.response.send_message(f"{Emojis.error} Invalid bid/small blind.", ephemeral=True)
            return

        if big_blind < min_blind or big_blind > max_blind:
            await interaction.response.send_message(
                f"{Emojis.error} The big blind must be between **{min_blind}** and **{max_blind}**.", ephemeral=True
            )
            return

        self.engine.big_blind = big_blind
        self.engine.small_blind = big_blind // 2

        await interaction.response.edit_message(view=self.render())
