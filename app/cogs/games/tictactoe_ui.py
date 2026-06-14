from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord
from discord import PartialEmoji

from app.cogs.games.engine.tictactoe import Board, BoardKind
from app.cogs.games.models import Game, GameResult
from app.core import LayoutView
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
    return "\u200b"


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


class PlayerPromptButton(discord.ui.Button["Prompt"]):
    def __init__(self, style: discord.ButtonStyle, kind: BoardKind, disabled: bool, row: int) -> None:
        super().__init__(style=style, disabled=disabled, label=str(kind), row=row)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()

        if self.view:
            self.view.stop()

        await interaction.delete_original_response()


class TicTacToeButton(discord.ui.Button["TicTacToe"]):
    def __init__(self, x: int, y: int) -> None:
        super().__init__(style=discord.ButtonStyle.grey, label="\u200b", row=y)
        self.x: int = x
        self.y: int = y

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        assert isinstance(self.view, TicTacToe)
        assert interaction.message is not None

        player = self.view.current_player
        if interaction.user != player.member:
            await interaction.response.send_message(f"{Emojis.error} It's not your turn.", ephemeral=True)
            return

        if player.current_selection is not None:
            await interaction.response.send_message(
                f"{Emojis.error} You've already selected a piece, you can't select multiple pieces.", ephemeral=True
            )
            return

        player.current_selection = (self.x, self.y)

        self.view.engine.place(self.x, self.y, player.kind)
        self.label = None
        self.emoji = kind_emoji(player.kind)
        self.style = kind_style(player.kind)
        self.disabled = True

        next_player = self.view.swap_player()
        player.current_selection = None

        winner = self.view.engine.winner()
        if winner is not None:
            assert interaction.guild is not None
            game_stats = interaction.client.db.game_stats
            guild_id = interaction.guild.id

            if winner is not BoardKind.Empty:
                user_balance: Balance = await interaction.client.db.get_user_balance(player.member.id, interaction.guild.id)
                amount: int = random.randint(25, 100)
                await user_balance.add(cash=amount)

                winning_player = next_player if next_player.kind is winner else player
                losing_player = player if winning_player is next_player else next_player

                self.view.build_container(winner=winning_player, amount=amount)

                await game_stats.record_result(guild_id, winning_player.member.id, Game.TICTACTOE, GameResult.WIN, profit=amount)
                await game_stats.record_result(guild_id, losing_player.member.id, Game.TICTACTOE, GameResult.LOSS)
            else:
                self.view.build_container(winner=[player, next_player])

                for tied_player in (player, next_player):
                    await game_stats.record_result(guild_id, tied_player.member.id, Game.TICTACTOE, GameResult.PUSH)

            self.view.disable_all()
            self.view.stop()

        await interaction.response.edit_message(view=self.view)


class TicTacToe(LayoutView):
    children: list[TicTacToeButton]

    def __init__(self, players: tuple[Player, ...]) -> None:
        super().__init__(timeout=36000.0, members=[p.member for p in players])
        self.players: tuple[Player, ...] = players
        self.player_index: int = 0
        self.engine: Board = Board()

        self.items: list[TicTacToeButton] = []

        for x in range(3):
            for y in range(3):
                self.items.append(TicTacToeButton(x, y))

        self.container: discord.ui.Container = self.build_container(initial=True)

    def disable_all(self) -> None:
        for child in self.container.walk_children():
            child.disabled = True

    @property
    def current_player(self) -> Player:
        return self.players[self.player_index]

    @property
    def get_player_fields(self) -> int:
        return self.engine.count(self.current_player.kind)

    def swap_player(self) -> Player:
        self.player_index = (self.player_index + 1) % len(self.players)
        return self.players[self.player_index]

    def build_container(self, winner: list[Player] | Player | None = None, amount: int | None = None, initial: bool = False) -> discord.ui.Container:
        self.clear_items()

        next_player = self.players[(self.player_index + 1) % len(self.players)]
        last_player = self.players[(self.player_index - 1) % len(self.players)]

        container = discord.ui.Container(accent_color=helpers.Colour.light_orange())
        container.add_item(discord.ui.TextDisplay("## TicTacToe"))

        description = (
            f"It is now {kind_emoji(self.current_player.kind)} {self.current_player.member.mention}'s turn with "
            f"currently {pluralize(self.get_player_fields):field}."
        )

        if initial:
            description = f"Challenge accepted! {next_player.member.mention} goes first and {last_player.member.mention} goes second.\n\n" + description

        if winner and amount:
            if isinstance(winner, Player):
                loser = self.players[(self.player_index - 1) % len(self.players)]

                container.accent_colour = kind_colour(winner.kind)
                description = (
                    f"{kind_emoji(winner.kind)} {winner.member.mention} won and earned {Emojis.Economy.cash} **{fnumb(amount)}**!\n"
                    f"*Maybe next time, {loser.member.mention}!*"
                )
            else:
                description = "It's a tie! No one wins, but at least no one loses!"

        container.add_item(discord.ui.TextDisplay(description))
        container.add_item(discord.ui.Separator())

        for x in range(3):
            row = discord.ui.ActionRow()
            for y in range(3):
                row.add_item(self.items[x * 3 + y])
            container.add_item(row)

        if not initial:
            container.add_item(discord.ui.Separator())
            container.add_item(discord.ui.TextDisplay(f"-# Player: {self.current_player.member} | Next: {next_player.member}"))

        self.container = container
        self.add_item(container)
        return container


class Prompt(LayoutView):
    def __init__(self, first: discord.abc.User, second: discord.abc.User) -> None:
        super().__init__(members=second)
        self.first: discord.abc.User = first
        self.second: discord.abc.User = second
        self.confirmed: bool = False

        accept_btn = discord.ui.Button(label="Accept", style=discord.ButtonStyle.green)
        accept_btn.callback = self._accept  # type: ignore[assignment]

        decline_btn = discord.ui.Button(label="Decline", style=discord.ButtonStyle.red)
        decline_btn.callback = self._decline  # type: ignore[assignment]

        container = discord.ui.Container(accent_colour=helpers.Colour.brand())
        container.add_item(discord.ui.TextDisplay(
            f"## TicTacToe\n"
            f"{second.mention} has been challenged to a TicTacToe party by {first.mention}.\n"
            f"Do you accept this party, {second.mention}?"
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(accept_btn, decline_btn))
        self.add_item(container)

    async def _accept(self, interaction: discord.Interaction) -> None:
        coin = random.randint(0, 1)
        order = (self.first, self.second) if coin == 0 else (self.second, self.first)

        players = (
            Player(member=order[0], kind=BoardKind.X, pieces={1, 2, 3, 4, 5, 6}),
            Player(member=order[1], kind=BoardKind.O, pieces={1, 2, 3, 4, 5, 6}),
        )

        view = TicTacToe(players)
        await interaction.response.send_message(view=view)
        self.confirmed = True
        self.stop()

    async def _decline(self, interaction: discord.Interaction) -> None:
        from app.core.components_v2 import Accent, make_notice
        notice = make_notice("TicTacToe", "Your challenge was declined.", accent=Accent.error)
        await interaction.response.send_message(view=notice)
        self.stop()
