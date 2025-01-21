from __future__ import annotations

import enum
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
from discord import PartialEmoji

from app.core import View
from app.utils import helpers, pluralize, fnumb
from config import Emojis

if TYPE_CHECKING:
    from app.database.base import Balance


class BoardKind(enum.Enum):
    Empty = 0
    X = -1
    O = 1

    def __repr__(self) -> str:
        if self is self.X:
            return Emojis.cross
        if self is self.O:
            return Emojis.circle
        return '\u200b'

    @property
    def emoji(self) -> PartialEmoji | str:
        return self.__repr__()

    @property
    def style(self) -> discord.ButtonStyle:
        if self is self.X:
            return discord.ButtonStyle.red
        if self is self.O:
            return discord.ButtonStyle.blurple
        return discord.ButtonStyle.grey

    @property
    def colour(self) -> discord.Colour:
        if self is self.X:
            return discord.Colour.red()
        if self is self.O:
            return discord.Colour.blurple()
        return discord.Colour.greyple()


@dataclass()
class Player:
    member: discord.abc.User
    kind: BoardKind
    pieces: set[int]
    current_selection: tuple[int, int] | None = None


class PlayerPromptButton(discord.ui.Button['PlayerPrompt']):
    def __init__(self, style: discord.ButtonStyle, kind: BoardKind, disabled: bool, row: int) -> None:
        super().__init__(style=style, disabled=disabled, label=str(kind), row=row)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        if self.view:
            self.view.stop()

        await interaction.delete_original_response()


class TicTacToeButton(discord.ui.Button['TicTacToe']):
    def __init__(self, x: int, y: int) -> None:
        super().__init__(style=discord.ButtonStyle.grey, label='\u200b', row=y)
        self.x: int = x
        self.y: int = y

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        assert interaction.message is not None

        player = self.view.current_player
        if interaction.user != player.member:
            await interaction.response.send_message(f'{Emojis.error} It\'s not your turn.', ephemeral=True)
            return

        state = self.view.get_board_state(self.x, self.y)
        if player.current_selection is not None:
            await interaction.response.send_message(
                f'{Emojis.error} You\'ve already selected a piece, you can\'t select multiple pieces.', ephemeral=True)
            return

        player.current_selection = (self.x, self.y)

        state.kind = player.kind
        self.label = None
        self.emoji = state.kind.emoji
        self.style = state.kind.style
        self.disabled = True

        next_player = self.view.swap_player()
        player.current_selection = None

        embed = self.view.embed

        winner = self.view.get_winner()
        if winner is not None:
            if winner is not BoardKind.Empty:
                user_balance: Balance = await interaction.client.db.get_user_balance(player.member.id, interaction.guild.id)
                amount: int = random.randint(25, 100)
                await user_balance.add(cash=amount)

                winning_player = next_player if next_player.kind is winner else player
                loser = player if next_player is winning_player else next_player

                embed.colour = winning_player.kind.colour
                embed.description = (
                    f'{winner.emoji} {winning_player.member.mention} won and earned {Emojis.Economy.cash} **{fnumb(amount)}**!\n'
                    f'*Maybe next time, {loser.member.mention}!*'
                )
                embed.set_footer()
            else:
                embed.colour = helpers.Colour.light_red()
                embed.description = 'It\'s a tie!'
                embed.set_footer(text='How boring...')

            self.view.disable_all()
            self.view.stop()

        await interaction.response.edit_message(embed=embed, view=self.view)


@dataclass()
class BoardState:
    kind: BoardKind

    @classmethod
    def empty(cls) -> BoardState:
        return BoardState(kind=BoardKind.Empty)


class TicTacToe(View):
    children: list[TicTacToeButton]

    def __init__(self, players: tuple[Player, ...]) -> None:
        super().__init__(timeout=36000.0, members=[p.member for p in players])
        self.players: tuple[Player, ...] = players
        self.player_index: int = 0
        self.board: list[list[BoardState]] = [[BoardState.empty() for _ in range(3)] for _ in range(3)]

        for x in range(3):
            for y in range(3):
                self.add_item(TicTacToeButton(x, y))

    def disable_all(self) -> None:
        for child in self.children:
            child.disabled = True

    def get_winner(self) -> BoardKind | None:
        for across in self.board:
            value = sum(p.kind.value for p in across)
            if value == 3:
                return BoardKind.O
            elif value == -3:
                return BoardKind.X

        for line in range(3):
            value = self.board[0][line].kind.value + self.board[1][line].kind.value + self.board[2][line].kind.value
            if value == 3:
                return BoardKind.O
            elif value == -3:
                return BoardKind.X

        diag = self.board[0][2].kind.value + self.board[1][1].kind.value + self.board[2][0].kind.value
        if diag == 3:
            return BoardKind.O
        elif diag == -3:
            return BoardKind.X

        diag = self.board[0][0].kind.value + self.board[1][1].kind.value + self.board[2][2].kind.value
        if diag == 3:
            return BoardKind.O
        elif diag == -3:
            return BoardKind.X

        if all(i.kind is not BoardKind.Empty for row in self.board for i in row):
            return BoardKind.Empty

        return None

    def get_board_state(self, x: int, y: int) -> BoardState:
        return self.board[y][x]

    @property
    def embed(self) -> discord.Embed:
        next_player = self.players[(self.player_index + 1) % len(self.players)]
        embed = discord.Embed(
            title='TicTacToe',
            description=f'It is now {self.current_player.kind.emoji} {self.current_player.member.mention}\'s turn with '
                        f'currently {pluralize(self.get_player_fields):field}.',
            colour=helpers.Colour.light_orange(),
        )
        embed.set_footer(text=f'Next Player: {next_player.member.name}')
        return embed

    @property
    def current_player(self) -> Player:
        return self.players[self.player_index]

    def swap_player(self) -> Player:
        self.player_index = (self.player_index + 1) % len(self.players)
        return self.players[self.player_index]

    @property
    def get_player_fields(self) -> int:
        return sum(1 for x in range(3) for y in range(3) if self.board[y][x].kind is self.current_player.kind)


class Prompt(View):
    def __init__(self, first: discord.abc.User, second: discord.abc.User) -> None:
        super().__init__(timeout=180.0, members=second)
        self.first: discord.abc.User = first
        self.second: discord.abc.User = second

        self.confirmed: bool = False

    @discord.ui.button(label='Accept', style=discord.ButtonStyle.green)
    async def accept(self, interaction: discord.Interaction, _) -> None:
        coin = random.randint(0, 1)
        order = (self.first, self.second) if coin == 0 else (self.second, self.first)

        players = (
            Player(member=order[0], kind=BoardKind.X, pieces={1, 2, 3, 4, 5, 6}),
            Player(member=order[1], kind=BoardKind.O, pieces={1, 2, 3, 4, 5, 6}),
        )

        view = TicTacToe(players)
        embed = view.embed
        embed.description = (f'Challenge accepted! {order[0].mention} goes first and {order[1].mention} goes second.\n'
                             + embed.description)

        await interaction.response.send_message(embed=embed, view=view)
        self.confirmed = True
        self.stop()

    @discord.ui.button(label='Decline', style=discord.ButtonStyle.red)
    async def decline(self, interaction: discord.Interaction, _) -> None:
        embed = discord.Embed(
            title='TicTacToe',
            description='Your Challenge was declined.',
            colour=helpers.Colour.light_red(),
        )
        await interaction.response.send_message(embed=embed)
        self.stop()
