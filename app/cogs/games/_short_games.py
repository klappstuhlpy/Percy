from __future__ import annotations

import random
from typing import ClassVar, Final

import discord

from app.core.views import View
from app.utils import helpers, fnumb
from app.utils.helpers import HealthBarBuilder
from config import Emojis


class Hangman:
    """A class to represent a hangman game."""

    IMAGES: Final[ClassVar[list[str]]] = [
        'https://klappstuhl.me/gallery/nPjxrLeBqw.png',  # Hangman 0
        'https://klappstuhl.me/gallery/osVwjXkXzV.png',
        'https://klappstuhl.me/gallery/algiRuPvKR.png',
        'https://klappstuhl.me/gallery/AshgxKkabK.png',
        'https://klappstuhl.me/gallery/CJhofrVhxx.png',
        'https://klappstuhl.me/gallery/ZvmbcoRetA.png',
        'https://klappstuhl.me/gallery/ybxVZsGoCo.png'
    ]

    def __init__(self, player: discord.Member, word: str) -> None:
        self.player: discord.Member = player
        self.word: str = word

        self._tries: int = 6
        self.used: set[str] = set()
        self.letters: set[str] = set(self.word.lower())

        self.finished: bool = False
        self.health_bar: HealthBarBuilder = HealthBarBuilder(self._tries)

        self._last_input: str | None = '`\N{INFORMATION SOURCE} Type a letter or the word you want to guess.`'

    @property
    def tries(self) -> int:
        """:class:`int`: The amount of tries left."""
        return self._tries

    @tries.setter
    def tries(self, value: int) -> None:
        """Set the amount of tries left."""
        if not isinstance(value, int):
            raise TypeError('Tries must be an integer.')

        self._tries = value
        self.health_bar -= 1

    def build_embed(self, won: bool | None = None) -> discord.Embed:
        """Build the embed."""
        guess = ''.join(letter.upper() if letter in self.used else '-' for letter in self.word)
        embed = discord.Embed(
            title='Hangman',
            description=f'**Progress:** {Emojis.empty} {self.health_bar}\n```prolog\n{guess}```',
        )

        if won is True:
            embed.colour = helpers.Colour.lime_green()
        elif won is False:
            embed.colour = helpers.Colour.light_red()
        else:
            embed.colour = helpers.Colour.white()

        if won is not None:
            embed.description += f'\nThe word was: **{self.word.upper()}**'
        else:
            embed.description += f'\nUsed letters: **{', '.join(letter.upper() for letter in self.used) if self.used else '...'}**\n\n'
            if self._last_input:
                embed.description += self._last_input

        embed.set_thumbnail(url=self.IMAGES[6 - self.tries])
        embed.set_footer(text=f'Player: {self.player} | Type "abort" to stop the game.')
        return embed


class Tower(View):
    """Represents a tower building game with custom partially animated emojis."""

    GRASS: ClassVar[str] = '<:grass:1322337508381429904>'
    TOWER_BASE_O: ClassVar[str] = '<:tower_base_0:1322337520205168671>'
    TOWER_BASE_1: ClassVar[str] = '<:tower_base_1:1322337546151137391>'
    TOWER_BASE_0_BROKEN: ClassVar[str] = '<:tower_base_0_broken:1322337529084776599>'
    TOWER_BASE_1_BROKEN: ClassVar[str] = '<:tower_base_1_broken:1322337558591705088>'
    TOWER_BASE_0_FALLING: ClassVar[str] = '<a:tower_base_0_falling:1322337537834094712>'
    TOWER_BASE_1_FALLING: ClassVar[str] = '<a:tower_base_1_falling:1322337569865728042>'

    HOUSE: ClassVar[str] = '\N{HOUSE WITH GARDEN}'
    TREE1: ClassVar[str] = '\N{EVERGREEN TREE}'
    TREE2: ClassVar[str] = '\N{DECIDUOUS TREE}'

    def __init__(self, player: discord.Member, bet: int) -> None:
        super().__init__()
        self.player = player
        self.bet = bet

        self._stack = 0
        self.multiplier: float = 1.
        self.finished: bool = False

        self.update_buttons()

    def __str__(self) -> str:
        return self.build()

    def add(self) -> None:
        self._stack += 1
        self.multiplier = (self._stack * .5) + 1

    def reset(self) -> None:
        self._stack = 0
        self.multiplier = 1.
        self.finished = False

    def build(self, failed: bool = False) -> str:
        stacks = []
        parts = ['\n']

        if failed:
            start = self.TOWER_BASE_0_BROKEN + self.TOWER_BASE_1_BROKEN
            self.finished = True
        else:
            start = self.TOWER_BASE_O + self.TOWER_BASE_1
            stacks = [start] * self._stack
            if self._stack != 0:
                stacks[0] = self.TOWER_BASE_0_FALLING + self.TOWER_BASE_1_FALLING

        parts.extend([Emojis.empty * 2 + tower for tower in stacks])
        parts.append(self.HOUSE + Emojis.empty + start + Emojis.empty + self.TREE1 + self.TREE2)
        parts.append(self.GRASS * 7)

        return '\n'.join(parts)

    def build_embed(self, failed: bool = False) -> discord.Embed:
        embed = discord.Embed(
            title='\N{BUILDING CONSTRUCTION} Tower',
            description=self.build(failed),
            colour=helpers.Colour.white()
        )
        embed.add_field(
            name='\u200b',
            value=(
                f'Bet: **{fnumb(self.bet)}** {Emojis.Economy.cash}\n'
                f'Multiplier: **x{self.multiplier}**'
            )
        )

        if failed:
            embed.add_field(name='\u200b', value='`❌ Tower has fallen!`', inline=False)
        if not failed and self.finished:
            embed.add_field(name='\u200b', value=f'`✅ Cashed out successfully with a multiplier of {self.multiplier}!`', inline=False)

        embed.set_footer(text=f'Player: {self.player}')
        return embed

    # View

    def update_buttons(self) -> None:
        self.clear_items()
        if self.finished:
            self.add_item(self.restart)
        else:
            self.add_item(self.stack)
            self.add_item(self.cash_out)

    async def interaction_check(self, interaction: discord.Interaction, /) -> bool:
        if interaction.user.id != self.player.id:
            await interaction.response.send_message(f'{Emojis.error} This isn\'t your game.', ephemeral=True)
            return False
        return True

    @discord.ui.button(label='Restart', style=discord.ButtonStyle.green)
    async def restart(self, interaction: discord.Interaction, _) -> None:
        balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild.id)

        if self.bet > balance.cash:
            return await interaction.response.send_message(
                f'{Emojis.error} You do not have enough money to bet that amount.\n'
                f'You currently have {Emojis.Economy.cash} **{fnumb(balance.cash)}** in **cash**.', ephemeral=True)

        await balance.remove(cash=self.bet)

        self.reset()
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label='Stack Tower (Increase Multiplier by 0.5)', style=discord.ButtonStyle.blurple)
    async def stack(self, interaction: discord.Interaction, _) -> None:
        # Now calculate the probability of the tower crashing or not
        rate = random.uniform(0, 1)
        if rate > 0.7:
            # probability of 30% to crash
            embed = self.build_embed(True)
            self.update_buttons()
            await interaction.response.edit_message(embed=embed, view=self)
            return

        self.add()
        await interaction.response.edit_message(embed=self.build_embed())

    @discord.ui.button(label='Cash Out', style=discord.ButtonStyle.red)
    async def cash_out(self, interaction: discord.Interaction, _) -> None:
        self.finished = True
        balance = await interaction.client.db.get_user_balance(interaction.user.id, interaction.guild.id)
        amount = round(self.bet * self.multiplier)
        await balance.add(cash=amount)

        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

        await interaction.followup.send(
            f'\N{LEAF FLUTTERING IN WIND} You cashed out {Emojis.Economy.cash} **{fnumb(amount)}**.')
