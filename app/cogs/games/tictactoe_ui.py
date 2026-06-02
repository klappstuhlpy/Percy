from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
from discord import PartialEmoji

from app.core import View
from app.games.engine.tictactoe import Board, BoardKind
from app.utils import fnumb, helpers, pluralize
from config import Emojis

if TYPE_CHECKING:
    from app.database.base import Balance


def kind_emoji(kind: BoardKind) -> PartialEmoji | str:
    """Discord presentation for a board mark."""
    if kind is BoardKind.X:
        return Emojis.cross
    if kind is BoardKind.O:
        return Emojis.circle
    return '\u200b'


def kind_style(kind: BoardKind) -> discord.ButtonStyle:
    if kind is BoardKind.X:
        return discord.ButtonStyle.red
    if kind is BoardKind.O:
        return discord.ButtonStyle.blurple
    return discord.ButtonStyle.grey


def kind_colour(kind: BoardKind) -> discord.Colour:
    if kind is BoardKind.X:
        return discord.Colour.red()
    if kind is BoardKind.O:
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

        if player.current_selection is not None:
            await interaction.response.send_message(
                f'{Emojis.error} You\'ve already selected a piece, you can\'t select multiple pieces.', ephemeral=True)
            return

        player.current_selection = (self.x, self.y)

        self.view.engine.place(self.x, self.y, player.kind)
        self.label = None
        self.emoji = kind_emoji(player.kind)
        self.style = kind_style(player.kind)
        self.disabled = True

        next_player = self.view.swap_player()
        player.current_selection = None

        embed = self.view.embed

        winner = self.view.engine.winner()
        if winner is not None:
            if winner is not BoardKind.Empty:
                user_balance: Balance = await interaction.client.db.get_user_balance(player.member.id, interaction.guild.id)
                amount: int = random.randint(25, 100)
                await user_balance.add(cash=amount)

                winning_player = next_player if next_player.kind is winner else player
                loser = player if next_player is winning_player else next_player

                embed.colour = kind_colour(winning_player.kind)
                embed.description = (
                    f'{kind_emoji(winner)} {winning_player.member.mention} won and earned {Emojis.Economy.cash} **{fnumb(amount)}**!\n'
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


class TicTacToe(View):
    children: list[TicTacToeButton]

    def __init__(self, players: tuple[Player, ...]) -> None:
        super().__init__(timeout=36000.0, members=[p.member for p in players])
        self.players: tuple[Player, ...] = players
        self.player_index: int = 0
        self.engine: Board = Board()

        for x in range(3):
            for y in range(3):
                self.add_item(TicTacToeButton(x, y))

    def disable_all(self) -> None:
        for child in self.children:
            child.disabled = True

    @property
    def embed(self) -> discord.Embed:
        next_player = self.players[(self.player_index + 1) % len(self.players)]
        embed = discord.Embed(
            title='TicTacToe',
            description=f'It is now {kind_emoji(self.current_player.kind)} {self.current_player.member.mention}\'s turn with '
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
        return self.engine.count(self.current_player.kind)


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
