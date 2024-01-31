import copy
import enum
from itertools import zip_longest
from typing import Optional, Literal

import discord
import numpy as np

from cogs.economy import Economy, Balance
from cogs.games._classes import BaseCard, BaseHand, Deck, CARD_PEMOJIS, DisplayCard
from cogs.utils import helpers, constants
from cogs.utils.context import Context, tick

cash_emoji = constants.cash_emoji


class WinningType(enum.Enum):
    """Enum for the winning type of a hand"""
    PLAYER_WIN = 'Player Win'
    PLAYER_BUST = 'Player Bust'
    PLAYER_BLACKJACK = 'Player Blackjack'

    DEALER_WIN = 'Dealer Win'
    DEALER_BUST = 'Dealer Bust'
    DEALER_BLACKJACK = 'Dealer Blackjack'

    PUSH = 'Push'


class Card(BaseCard):
    """Represents a card in a deck"""

    def display(self, size: Literal["small", "large"], formatted: bool = False) -> DisplayCard | str:
        if self.hidden:
            # Only need a big hidden card for blackjack
            top = [
                CARD_PEMOJIS['cardback_top1'], CARD_PEMOJIS['cardback_top2']
            ]
            middle = [CARD_PEMOJIS['cardback_middle']] * 2
            bottom = [
                CARD_PEMOJIS['cardback_bottom1'], CARD_PEMOJIS['cardback_bottom2']
            ]

            emojis = ["".join(map(str, top)), "".join(map(str, middle)), "".join(map(str, bottom))]
            return '\n'.join(emojis) if formatted else DisplayCard(top=emojis[0], middle=emojis[1], bottom=emojis[2])
        return super().display(size, formatted)


class Hand(BaseHand[Card]):
    """Represents a hand of cards for a blackjack game"""

    def __init__(self, bet: int):
        super().__init__()
        self.bet: int = bet
        self.message: Optional[discord.Message] = None

        self.finished: bool = False
        self.splitted: bool = False

    @property
    def value(self) -> int:
        """Gets the value of the hand"""
        _sum = sum([card.value for card in self.cards if not card.hidden])

        # Check and adjust for aces
        if _sum > 21:
            for card in self.cards:
                if card.value == 11:
                    card.value = 1
                if _sum <= 21:
                    break

        # Check and adjust for aces after a split
        if len(self) == 2 and _sum < 21:
            for card in self.cards:
                if card.value == 1:
                    card.value = 11

        return sum([card.value for card in self.cards if not card.hidden])

    @property
    def display_text(self) -> str:
        """Gets the display text for the hand"""
        card_list = [
            card.display('large', formatted=True).split('\n') for card in self.cards
        ]
        # Use zip_longest to handle different lengths of display elements in each card
        results = [
            ' '.join(filter(None, elems))  # filter(None) removes empty strings
            for elems in zip_longest(*card_list, fillvalue='')
        ]
        return '\n'.join(results) + f'\n\nValue: `{self.value}`'


class Table:
    """Represents a game with the dealer and players and the base blackjack logic."""

    def __init__(self, ctx: Context, bet: int, decks: int = 1, _wake_up: bool = False):
        self.ctx: Context = ctx
        if not _wake_up:
            self.deck: Deck = Deck(game='blackjack', decks=decks, card_cls=Card)

        self.dealer: Hand = Hand(bet=bet)
        self.player_hands: list[Hand] = [Hand(bet=bet)]

        self.active_hand: Hand = self.player_hands[0]

        self.deal()
        self.view: TableView = TableView(table=self)

    def __repr__(self):
        return f'Table(ctx={self.ctx}, decks={self.deck.decks} dealer={self.dealer})'

    def wake_up(self, ctx: Context, bet: int):
        """Clears all Hands, checks the amount of cards in the deck, initializes a new Hand and deals the cards."""

        # Calculate if 25% of the cards are left in the deck
        # if so, create a new deck
        if ((52 * self.deck.decks) - len(self.deck.cards)) / len(self.deck.cards) >= 0.75:
            self.deck = Deck(game='blackjack', decks=self.deck.decks)

        self.__init__(ctx, bet, decks=self.deck.decks, _wake_up=True)

    def deal(self):
        """Deals the cards to the players and the dealer"""
        # Find next two cards with same value:

        for _ in range(2):
            self.active_hand.add(self.deck.draw())
            self.dealer.add(self.deck.draw())

        # Sets the dealers second card to hidden
        self.dealer.cards[1].hidden = True

    def hit(self, hand: Hand):
        """Hits a hand."""
        hand.add(self.deck.draw())

    def stand(self):
        """Stands the active hand."""
        self.active_hand.finished = True

        if self.playing_players:
            return

        self.dealer.cards[1].hidden = False

        if len(self.player_hands) == 1 and self.player_hands[0].value >= 21:
            # Don't draw anymore cards if there is
            # only one hand, and it's already busted or has a blackjack
            return

        while self.dealer.value <= 16:
            self.hit(self.dealer)

    def build_embed(
            self,
            hand: Hand,
            colour: discord.Colour = helpers.Colour.darker_red(),
            text: str = None,
            image_url: str = None
    ) -> discord.Embed:
        """Gets the embed for the game"""
        embed = discord.Embed(title='Blackjack', description=text or f'Your Bet: {cash_emoji} **{hand.bet:,}**',
                              colour=colour)
        if image_url:
            embed.set_image(url=image_url)
            return embed

        name = 'Your Hand'
        if len(self.player_hands) > 1:
            name += f' #{self.player_hands.index(hand) + 1}'

        embed.add_field(name=name, value=hand.display_text)
        embed.add_field(name='Dealer Hand', value=self.dealer.display_text)

        if colour == discord.Colour.blurple():
            embed.set_footer(text=f'Cards remaining: {len(self.deck)}')

        return embed

    def get_winner(self, hand: Hand) -> Optional[WinningType]:
        """Gets a potential winner for the game.

        This implements logic for a player/dealer blackjack, player/dealer win, player/dealer bust.
        Also for events like both having a blackjack, both having the same value etc.
        Also checks that requirements for a blackjack are met.
        """
        player_value = hand.value
        dealer_value = self.dealer.value

        if player_value == 21 and len(hand) == 2:
            if dealer_value == 21 and len(self.dealer) == 2:
                return WinningType.PUSH
            if hand.splitted:
                return WinningType.PLAYER_WIN
            return WinningType.PLAYER_BLACKJACK
        elif dealer_value == 21 and len(self.dealer) == 2:
            return WinningType.DEALER_BLACKJACK

        if player_value > 21:
            return WinningType.PLAYER_BUST
        elif dealer_value > 21:
            return WinningType.DEALER_BUST

        if self.dealer.cards[1].hidden:
            if player_value == 21:
                return WinningType.PLAYER_WIN
        else:
            if player_value == dealer_value:
                return WinningType.PUSH
            elif player_value > dealer_value:
                return WinningType.PLAYER_WIN
            elif player_value < dealer_value:
                return WinningType.DEALER_WIN
        return None

    @property
    def playing_players(self) -> bool:
        """Checks if there are any players that are still playing"""
        return any([not hand.finished for hand in self.player_hands])


class TableView(discord.ui.View):
    """Represents a view for the blackjack game"""

    def __init__(self, table: Table):
        super().__init__(timeout=300)

        self.table: Table = table
        self.economy: Economy = self.table.ctx.bot.get_cog('Economy')  # noqa

    async def finish_winner(self, interaction: discord.Interaction, winner: WinningType) -> tuple[str, discord.Colour]:
        amount: int | None = None
        if winner == WinningType.PLAYER_BLACKJACK:
            amount = int(self.table.active_hand.bet * 1.5)
            result = f'{winner.value}. You won {cash_emoji} **{amount:,}**.'
            color = helpers.Colour.lime_green()
        elif winner in {WinningType.DEALER_BLACKJACK, WinningType.DEALER_WIN, WinningType.PLAYER_BUST}:
            result = f'{winner.value}. You lost {cash_emoji} **{self.table.active_hand.bet:,}**.'
            color = helpers.Colour.light_red()
        elif winner in {WinningType.PLAYER_WIN, WinningType.DEALER_BUST}:
            amount = self.table.active_hand.bet * 2
            result = f'{winner.value}. You won {cash_emoji} **{self.table.active_hand.bet:,}**.'
            color = helpers.Colour.lime_green()
        elif winner == WinningType.PUSH:
            amount = self.table.active_hand.bet
            result = f'{winner.value}. {cash_emoji} **{self.table.active_hand.bet:,}** returned.'
            color = helpers.Colour.light_grey()
        else:
            result = 'Something went wrong.'
            color = helpers.Colour.darker_red()

        if amount:
            user_balance = await self.economy.get_balance(interaction.user.id, interaction.guild_id)
            await user_balance.add(amount, 'cash')

        return result, color

    async def check_for_winner(self, interaction: discord.Interaction | Context) -> bool:
        """Checks if there is a winner and updates the embed accordingly"""
        if not self.table.active_hand.finished:
            if self.table.playing_players and self.table.active_hand.value >= 21:
                # If the player has over 21 or a blackjack, stand automatically
                self.table.stand()
                await self.check_for_winner(interaction)
                return True
            return False

        # Just not get a "Failed Interaction" error displayed
        if isinstance(interaction, Context):
            _send_action = self.table.active_hand.message.edit  # noqa
        else:
            _send_action = interaction.response.edit_message  # noqa
        await _send_action(embed=self.table.build_embed(self.table.active_hand), view=self)

        _disabled_self = copy.copy(self)
        for item in _disabled_self.children:
            item.disabled = True
        await self.table.active_hand.message.edit(view=_disabled_self)

        if self.table.playing_players:
            # Start the next hand
            self.table.active_hand = next(filter(lambda h: not h.finished, self.table.player_hands), None)
            await self.update_buttons(active=True)
            message = await interaction.followup.send(embed=self.table.build_embed(self.table.active_hand), view=self)
            self.table.active_hand.message = message
        else:
            # Ensure to show the dealer's second card regardless of the outcome
            self.table.dealer.cards[1].hidden = False

            await self.update_buttons(active=False)
            for hand in self.table.player_hands:
                winner = self.table.get_winner(hand)
                text, color = await self.finish_winner(interaction, winner)
                await hand.message.edit(embed=self.table.build_embed(hand, color, text), view=self)
        return True

    async def update_buttons(self, *, active: bool):
        """Updates the buttons of the view"""
        for item in self.children:
            item.disabled = not active

        hand = self.table.active_hand
        balance = await self.economy.get_balance(self.table.ctx.user.id, self.table.ctx.guild.id)

        if not hand.finished:
            can_split = (
                    len(hand) == 2
                    and hand.cards[0].value == hand.cards[1].value
                    and hand.bet <= balance.cash
            )

            self.split.disabled = not can_split
            self.double_down.disabled = not (hand.bet <= balance.cash)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Checks if the interaction is valid"""
        if interaction.user.id != self.table.ctx.user.id:
            await interaction.response.send_message(f'{tick(False)} You cannot interact with this game.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Hit', style=discord.ButtonStyle.blurple)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        """Hits the player"""
        self.table.hit(self.table.active_hand)

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(embed=self.table.build_embed(self.table.active_hand), view=self)

    @discord.ui.button(label='Stand', style=discord.ButtonStyle.red)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        """Stands the player"""
        self.table.stand()

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(embed=self.table.build_embed(self.table.active_hand), view=self)

    @discord.ui.button(label='Double Down', style=discord.ButtonStyle.grey)
    async def double_down(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        """Doubles the bet and hits the player"""
        user_balance: Balance = await self.economy.get_balance(interaction.user.id, interaction.guild_id)
        await user_balance.remove(self.table.active_hand.bet, 'cash')

        self.table.active_hand.bet *= 2

        self.table.hit(self.table.active_hand)
        self.table.stand()

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(embed=self.table.build_embed(self.table.active_hand), view=self)

    @discord.ui.button(label='Split', style=discord.ButtonStyle.grey, disabled=True)
    async def split(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        """Splits the hand"""
        new_hand = Hand(bet=self.table.active_hand.bet)
        new_hand.splitted = True
        self.table.active_hand.splitted = True

        # Get the left card and add it to the second hand and draw a new card for both hands
        hand = self.table.active_hand.card_arr
        card = hand[1]
        new_hand.add(np.array([[card[0], card[1]]]))
        self.table.active_hand.card_arr = np.delete(hand, 1, 0)

        self.table.hit(new_hand)
        self.table.hit(self.table.active_hand)

        self.table.player_hands.append(new_hand)

        user_balance: Balance = await self.economy.get_balance(interaction.user.id, interaction.guild_id)
        await user_balance.remove(self.table.active_hand.bet, 'cash')

        if not await self.check_for_winner(interaction):
            await interaction.response.edit_message(embed=self.table.build_embed(self.table.active_hand), view=self)

    @discord.ui.button(label='Help', style=discord.ButtonStyle.grey, emoji='\N{WHITE QUESTION MARK ORNAMENT}', row=1)
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        """Shows the help menu"""
        embed = discord.Embed(title='Blackjack Help', colour=helpers.Colour.blurple())
        embed.set_thumbnail(url='https://i.giphy.com/ZahUvuh70nd6GYmpHi.gif')
        embed.description = (
            'The goal of blackjack is to beat the dealer\'s hand without going over 21.\n'
            'Face cards are worth 10. Aces are worth 1 or 11, whichever makes a better hand.\n'
            'Each player starts with two cards, one of the dealer\'s cards is hidden until the end.\n'
            'If you go over 21 you *bust*, and the dealer wins regardless of the dealer\'s hand.\n'
            'If you are dealt 21 from the start (Ace & 10), you got a *blackjack*.\n'
            'Blackjack usually means you win **1.5** the amount of your bet.\n'
            'Dealer will hit until his/her cards total **17 or higher**.\n'
            'You can only double/split on the first move, or first move of a hand created by a split.\n'
            'You cannot play on two aces after they are split.\n\n'
            'For more information, see [this](https://en.wikipedia.org/wiki/Blackjack) article.'
        )
        embed.add_field(name='Hit', value='Draws a card from the deck.')
        embed.add_field(name='Stand', value='Stands the current hand.')
        embed.add_field(name='Double Down', value='Doubles the bet and hits the current hand.')
        embed.add_field(name='Split', value='Splits the current hand into two hands. Doubles the bet.')

        await interaction.response.send_message(embed=embed, ephemeral=True)
