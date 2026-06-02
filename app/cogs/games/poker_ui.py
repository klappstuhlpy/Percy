from __future__ import annotations

from contextlib import suppress
from itertools import zip_longest
from typing import TYPE_CHECKING, cast

import discord

from app.cogs.games.engine.poker import TableState
from app.core.views import View
from app.rendering import BarChart
from app.utils import fnumb, helpers
from config import Emojis

if TYPE_CHECKING:
    import PIL.Image

    from app.cogs.games.engine.poker import Player, TexasHoldem
    from app.cogs.games.poker_bridge import PokerSession
    from app.core import Bot
    from app.database.base import Balance

__all__ = (
    'BuyInModal',
    'RaiseBetModal',
    'SetBlindsModal',
    'TableView',
)


class RaiseBetModal(discord.ui.Modal, title='Bet/Raise'):
    amount = discord.ui.TextInput(
        label='Amount', placeholder='Enter the amount you want to raise by', min_length=1, max_length=10)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class BuyInModal(discord.ui.Modal, title='Buy-In'):
    amount = discord.ui.TextInput(label='Amount', min_length=1, max_length=10)

    def __init__(self, engine: TexasHoldem) -> None:
        super().__init__(timeout=100.)
        self.amount.placeholder = f'Enter your buy-in amount. (Min: {engine.min_buy_in}, Max: {engine.max_buy_in})'

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class SetBlindsModal(discord.ui.Modal, title='Set Custom Big Blind'):
    big_blind = discord.ui.TextInput(label='Big Blind', min_length=1, max_length=10)

    def __init__(self, min_blind: int, max_blind: int) -> None:
        super().__init__(timeout=100.)
        self.big_blind.placeholder = f'Enter the big blind amount. (Min: {min_blind}, Max: {max_blind})'

    async def on_submit(self, interaction: discord.Interaction) -> None:
        self.interaction = interaction
        self.stop()


class TableView(View):
    """Represents a view for a poker table.

    Holds a reference to the :class:`~app.cogs.games._poker.PokerSession` bridge.
    Button callbacks trigger actions on the pure engine (``self.engine``) and ask
    the session (``self.session``) to render embeds and drive the autoplay timer.
    """

    def __init__(self, session: PokerSession) -> None:
        self.session: PokerSession = session
        self.engine: TexasHoldem = session.engine
        super().__init__(timeout=500.)

        self.update_buttons()

    async def on_timeout(self) -> None:
        """Called when the view times out."""
        for player in self.engine.players:
            await self.session.remove_player(player.member)

        if self.session.message is not None:
            with suppress(KeyError):
                del self.session.cog.poker_tables[self.session.message.channel.id]

            with suppress(discord.HTTPException):
                await self.session.message.reply(f'{Emojis.error} The table has been closed due to inactivity.')
                await self.session.message.delete()

    # Button Updating

    def update_buttons(self) -> None:
        if self.engine.state != TableState.RUNNING:
            self._update_buttons_not_running()
            return

        self._update_buttons_running()

    def _update_buttons_not_running(self) -> None:
        """Updates the buttons when the table is not running"""
        engine = self.engine
        self.clear_items()

        self.add_item(self.join)
        self.add_item(self.start_next_round)
        self.add_item(self.leave_button)

        stopped_or_prepared = engine.state in (TableState.STOPPED, TableState.PREPARED)

        if stopped_or_prepared:
            self.add_item(self.set_blinds_button)

        self.start_next_round.label = 'Start' if stopped_or_prepared else 'Next Round'

        if engine.state == TableState.FINISHED:
            self.add_item(self.analysis_button)
        if engine.state == TableState.PREPARED:
            self.remove_item(self.analysis_button)

        if len(engine.players) < 2:
            self.start_next_round.disabled = True
        else:
            self.start_next_round.disabled = False

        if len(engine.players) == 4:
            self.join.disabled = True
        else:
            self.join.disabled = False

    def _update_buttons_running(self) -> None:
        """Updates the buttons when the table is running"""
        engine = self.engine

        RUNNING_BUTTONS = [
            self.join,  # disabled
            self.my_hand,
            self.start_next_round,  # disabled
            self.fold,
            self.check_call,
            self.raise_bet,
            self.all_in
        ]

        # check if buttons are in the view
        if any(button not in self.children for button in RUNNING_BUTTONS):
            self.clear_items()
            for button in RUNNING_BUTTONS:
                self.add_item(button)

        self.join.disabled = True
        if engine.state == TableState.PREPARED:
            self.start_next_round.label = 'Start'
        else:
            self.start_next_round.label = 'Next Round'
        self.start_next_round.disabled = True

        # Big/Small Blind can't raise/bet in the first round
        is_first_round_and_blind = len(engine.community_arr) == 0 and engine.blind_index is not None and engine.player_index in engine.blind_index
        self.raise_bet.disabled = is_first_round_and_blind
        self.raise_bet.label = 'Bet' if all(player.bet <= engine.big_blind for player in engine.playing_players) else 'Raise'

        # Setting the check/call button
        is_check = engine.players[engine.player_index].bet == max([player.bet for player in engine.players])
        call_amount = max([player.bet for player in engine.players]) - engine.players[engine.player_index].bet
        self.check_call.label = 'Check' if is_check else f'Call ({call_amount} Chips)'
        self.check_call.emoji = None if is_check else Emojis.Economy.coin

        if not is_check and engine.players[engine.player_index].stack < call_amount:
            self.check_call.disabled = True
            self.check_call.style = discord.ButtonStyle.grey
        else:
            self.check_call.disabled = False
            self.check_call.style = discord.ButtonStyle.grey if is_check else discord.ButtonStyle.green

    # Buttons

    @discord.ui.button(label='Join', style=discord.ButtonStyle.grey)
    async def join(self, interaction: discord.Interaction, _) -> None:
        """Joins the table"""
        if self.engine.state != TableState.STOPPED:
            await interaction.response.send_message(f'{Emojis.error} The table is already running.', ephemeral=True)
            return

        if interaction.user in [player.member for player in self.engine.players]:
            await interaction.response.send_message(f'{Emojis.error} You are already in the game.', ephemeral=True)
            return

        modal = BuyInModal(engine=self.engine)
        await interaction.response.send_modal(modal)
        await modal.wait()
        with suppress(AttributeError):
            interaction = modal.interaction

        try:
            amount = int(modal.amount.value)
        except ValueError:
            await interaction.response.send_message(f'{Emojis.error} Invalid amount.', ephemeral=True)
            return

        balance: Balance = await cast('Bot', interaction.client).db.get_user_balance(interaction.user.id, interaction.guild_id)
        if balance.cash < amount:
            await interaction.response.send_message(
                f'{Emojis.error} You don\'t have enough **cash** money to buy yourself in.\n'
                f'You need at least {Emojis.Economy.coin} **{fnumb(self.engine.min_buy_in)}**.',
                ephemeral=True)
            return

        await balance.remove(cash=amount)
        self.engine.add_player(cast('discord.Member', interaction.user), stack=amount)

        if len(self.engine.players) == 4:
            self.engine.start()
            self = TableView(session=self.session)
            self.session.restart_timer()

        self.update_buttons()
        await interaction.response.edit_message(embed=self.session.build_embed(), view=self)

    @discord.ui.button(label='My Hand', style=discord.ButtonStyle.blurple)
    async def my_hand(self, interaction: discord.Interaction, _) -> None:
        """Shows the player's hand"""
        player = discord.utils.get(self.engine.players, member=interaction.user)
        if not player:
            await interaction.response.send_message(f'{Emojis.error} You are not in the game.', ephemeral=True)
            return

        if self.engine.state != TableState.RUNNING:
            await interaction.response.send_message(
                f'{Emojis.error} The game has not started yet.', ephemeral=True)
            return

        embed = discord.Embed(title='Your Cards', color=discord.Color.blurple())

        card_list = [f'{elem1} {elem2}' for elem1, elem2 in zip(
            *[cast('str', card.display('large', formatted=True)).split('\n') for card in player.hand.cards])]
        embed.description = '\n'.join(card_list)

        # Returns your best hand
        hand = player.hand.evaluate(self.engine.community_arr)

        card_list = [
            cast('str', card.display('large', formatted=True)).split('\n') for card in hand.cards
        ]
        # Use zip_longest to handle different lengths of display elements in each card
        results = [
            ' '.join(filter(None, elems))  # filter(None) removes empty strings
            for elems in zip_longest(*card_list, fillvalue='')
        ]

        embed.description += f'\n\n**Your Best Hand: *{hand.name}* **\n' + '\n'.join(results)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label='Start', style=discord.ButtonStyle.green, disabled=True)
    async def start_next_round(self, interaction: discord.Interaction, _) -> None:
        """Starts the game"""
        await interaction.response.defer()

        if self.engine.state == TableState.RUNNING:
            await interaction.followup.send(f'{Emojis.error} The table is already running.', ephemeral=True)
            return

        if interaction.user != self.engine.host:
            await interaction.followup.send(
                f'{Emojis.error} You are not the host of this table.\n'
                f'Please aks {self.engine.host.mention} to start the game!', ephemeral=True)
            return

        if len(self.engine.players) < 2:
            await interaction.followup.send(
                f'{Emojis.error} You need at least 2 players to start the game.', ephemeral=True)
            return

        if self.start_next_round.label == 'Next Round':
            await self.session.prepare_next_game()
        else:
            self.session.view = self = TableView(session=self.session)
            self.engine.start()
            self.session.restart_timer()

        self.update_buttons()
        await interaction.edit_original_response(embed=self.session.build_embed(), view=self)

    @discord.ui.button(label='Fold', style=discord.ButtonStyle.red, row=1)
    async def fold(self, interaction: discord.Interaction, _) -> None:
        """Folds the player's hand"""
        player = await self.get_player(interaction)
        if not player:
            return

        self.session.cancel_timer()

        self.engine.Fold()
        self.engine.switch_player()
        self.session.restart_timer()

        self.update_buttons()
        await interaction.response.edit_message(embed=self.session.build_embed(), view=self)

    @discord.ui.button(label='Check', style=discord.ButtonStyle.grey, row=1)
    async def check_call(self, interaction: discord.Interaction, _) -> None:
        """Checks or calls"""
        player = await self.get_player(interaction)
        if not player:
            return

        self.session.cancel_timer()

        max_bet = max([p.bet for p in self.engine.players])
        if player.bet == max_bet:
            self.engine.Check()
        else:
            if player.stack < max_bet - player.bet:
                await interaction.response.send_message(
                    f'{Emojis.error} You don\'t have enough chips. You\'ll need to go **All-In**!', ephemeral=True)
                return

            self.engine.Call()

        self.engine.switch_player()
        self.session.restart_timer()
        self.update_buttons()
        await interaction.response.edit_message(embed=self.session.build_embed(), view=self)

    @discord.ui.button(label='Raise', style=discord.ButtonStyle.blurple, row=1)
    async def raise_bet(self, interaction: discord.Interaction, _) -> None:
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
            await interaction.response.send_message(f'{Emojis.error} Invalid amount.', ephemeral=True)
            return

        if amount > player.stack:
            await interaction.response.send_message(
                f'{Emojis.error} You don\'t have enough chips.', ephemeral=True)
            return

        is_bet = all(player.bet <= self.engine.big_blind for player in self.engine.playing_players)
        if is_bet:
            if amount < self.engine.big_blind:
                await interaction.response.send_message(
                    f'You have to raise by at least the big blind (**{self.engine.big_blind}** Chips).', ephemeral=True)
                return
        else:
            # Raise must be at least twice the current bet
            previous_bet = max([player.bet for player in self.engine.players])
            if amount < previous_bet * 2:
                await interaction.response.send_message(
                    f'You have to raise by at least twice the current bet (**{previous_bet * 2}** Chips).',
                    ephemeral=True)
                return

            if (previous_bet + amount) > player.stack:
                await interaction.response.send_message(
                    f'{Emojis.error} You don\'t have enough chips.', ephemeral=True)
                return

        # check if its all-in
        if amount == player.stack:
            self.engine.AllIn()
        else:
            self.engine.Raise(amount)

        self.engine.switch_player(by_raise=True)
        self.session.restart_timer()

        self.update_buttons()
        await interaction.response.edit_message(embed=self.session.build_embed(), view=self)

    @discord.ui.button(label='All In', style=discord.ButtonStyle.red, row=1)
    async def all_in(self, interaction: discord.Interaction, _) -> None:
        """Goes all in"""
        player = await self.get_player(interaction)
        if not player:
            return

        self.session.cancel_timer()

        self.engine.AllIn()
        self.engine.switch_player(by_raise=True)
        self.session.restart_timer()

        self.update_buttons()
        await interaction.response.edit_message(embed=self.session.build_embed(), view=self)

    async def get_player(self, interaction: discord.Interaction) -> Player | None:
        player = self.engine.players[self.engine.player_index]
        if not player:
            await interaction.response.send_message(f'{Emojis.error} You are not in the game.', ephemeral=True)
            return None

        if player.member != interaction.user:
            await interaction.response.send_message(f'{Emojis.error} It\'s not your turn.', ephemeral=True)
            return None

        return player

    @discord.ui.button(label='Show Analysis', style=discord.ButtonStyle.blurple, emoji='\N{BAR CHART}', row=2)
    async def analysis_button(self, interaction: discord.Interaction, _) -> None:
        """Callback for the analysis button"""
        await interaction.response.defer()

        if self.engine.state != TableState.FINISHED:
            await interaction.followup.send(
                f'{Emojis.error} The table is currently running, please wait till the game is finished.',
                ephemeral=True)
            return

        embed = discord.Embed(title='Game Odds Analysis', color=helpers.Colour.white())
        data: list[tuple[dict[str, float], dict[int, dict[str, float]]]] = self.engine.analysis

        embeds, files = [], []
        for index, player in enumerate(self.engine.players):
            embed = embed.copy()
            d_index = index + 1

            embed.set_author(name=f'{player.member.display_name} | Seat #{d_index}', icon_url=player.member.display_avatar.url)
            embed.description = (
                'This Analyis shows the odds of winning for each player at each stage of the game.'
                'The River is not included as the game is already over and nothing more to predict.\n\n'
            )

            match len(data):
                case 1:
                    embed.description += f'Pre-Flop: Win: **{data[0][0][f"Player {d_index} Win"]}**% | Tie: **{data[0][0][f"Player {d_index} Tie"]}**%\n'
                case 2:
                    embed.description += f'Pre-Flop: Win: **{data[0][0][f"Player {d_index} Win"]}**% | Tie: **{data[0][0][f"Player {d_index} Tie"]}**%\n'
                    embed.description += f'Flop: Win: **{data[1][0][f"Player {d_index} Win"]}**% | Tie: **{data[1][0][f"Player {d_index} Tie"]}**%\n'
                case 3:
                    embed.description += f'Pre-Flop: Win: **{data[0][0][f"Player {d_index} Win"]}**% | Tie: **{data[0][0][f"Player {d_index} Tie"]}**%\n'
                    embed.description += f'Flop: Win: **{data[1][0][f"Player {d_index} Win"]}**% | Tie: **{data[1][0][f"Player {d_index} Tie"]}**%\n'
                    embed.description += f'Turn: Win: **{data[2][0][f"Player {d_index} Win"]}**% | Tie: **{data[2][0][f"Player {d_index} Tie"]}**%'
                case _:
                    embed.description += '***NO DATA***'

            TITLE_MAP = {
                0: f'Seat #{d_index} - Hand Strength Analysis | Pre-Flop',
                1: 'Flop',
                2: 'Turn'
            }
            images: list[PIL.Image.Image] = []
            for i in range(len(data)):
                chart = BarChart(
                    data=dict(dict((data[i][1][d_index]).items()).items()),
                    title=TITLE_MAP.get(i, '---'),
                )
                images.extend(cast('list[PIL.Image.Image]', chart.render(byted=False)))

            image = BarChart._merge_and_render(images)

            embed.set_image(url=f'attachment://bar_chart-{index}.png')
            embeds.append(embed)
            files.append(image)

        await interaction.followup.send(embeds=embeds, files=files, ephemeral=True)

    @discord.ui.button(label='Leave', style=discord.ButtonStyle.red)
    async def leave_button(self, interaction: discord.Interaction, _) -> None:
        """Callback for the leave button"""
        if self.engine.state == TableState.RUNNING:
            await interaction.response.send_message(
                f'{Emojis.error} The table is currently running, please wait till the game is finished.',
                ephemeral=True)
            return

        if interaction.user not in [player.member for player in self.engine.players]:
            await interaction.response.send_message(f'{Emojis.error} You are not in the game.', ephemeral=True)
            return

        await self.session.remove_player(cast('discord.Member', interaction.user))

        if len(self.engine.players) == 1:
            self.engine.state = TableState.STOPPED
        elif len(self.engine.players) == 0:
            with suppress(KeyError):
                del self.session.cog.poker_tables[self.session.ctx.channel.id]
            if self.session.message is not None:
                await self.session.message.delete()

            await interaction.response.send_message(
                '\N{LEAF FLUTTERING IN WIND} The Poker Table has been closed due to all players leaving.',
                delete_after=10)
            return

        self.update_buttons()
        await interaction.response.edit_message(embed=self.session.build_embed(), view=self)
        if self.session.message is not None:
            await self.session.message.reply(
                f'\N{LEAF FLUTTERING IN WIND} {interaction.user.mention} has left the table.', delete_after=10)

    @discord.ui.button(label='Set Blinds', style=discord.ButtonStyle.blurple, row=1)
    async def set_blinds_button(self, interaction: discord.Interaction, _) -> None:
        """Callback for the set blinds button"""
        if self.engine.state == TableState.RUNNING:
            await interaction.response.send_message(
                f'{Emojis.error} The table is currently running, please wait till the game is finished.',
                ephemeral=True)
            return

        if interaction.user != self.engine.host:
            await interaction.response.send_message(
                f'{Emojis.error} You are not the host of this table.\n'
                f'Please ask {self.engine.host.mention} to set the blinds!', ephemeral=True)
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
            await interaction.response.send_message(f'{Emojis.error} Invalid bid/small blind.', ephemeral=True)
            return

        if big_blind < min_blind or big_blind > max_blind:
            await interaction.response.send_message(
                f'{Emojis.error} The big blind must be between **{min_blind}** and **{max_blind}**.', ephemeral=True)
            return

        self.engine.big_blind = big_blind
        self.engine.small_blind = big_blind // 2

        self.update_buttons()
        await interaction.response.edit_message(embed=self.session.build_embed(), view=self)
