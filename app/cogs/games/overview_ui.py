"""UI for the interactive games overview (`games`): catalogue + personal records."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord

from app.cogs.games.models import Game
from app.core import LayoutView
from app.utils import fnumb, get_asset_url, helpers
from config import Emojis

if TYPE_CHECKING:
    from app.cogs.games.cog import Games
    from app.core.models import Context

__all__ = ('GamesHub',)


#: Usage string and one-line tagline per game, keyed by the stable enum.
GAME_DETAILS: dict[Game, tuple[str, str]] = {
    Game.BLACKJACK: ('blackjack <bet>', 'Beat the dealer to 21 — hit, stand or double down.'),
    Game.SLOTS: ('slots <bet>', 'Spin the reels for multiplied payouts.'),
    Game.ROULETTE: ('roulette <bet> <space>', 'Bet on numbers, colours or ranges at a shared table.'),
    Game.POKER: ('poker <stack>', "Texas Hold'em at a shared table for up to 4 players."),
    Game.TOWER: ('tower [bet]', 'Climb the tower and cash out before a wrong tile.'),
    Game.HIGHERLOWER: ('higherlower <bet>', 'Call higher or lower for a rising multiplier.'),
    Game.DICE: ('dice <bet> <target>', 'Bet on the total of two dice — rarer totals pay more.'),
    Game.MINES: ('mines <bet> [mines]', 'Reveal gems, dodge mines, cash out anytime.'),
    Game.COINFLIP: ('coinflip <bet> [side] [opponent]', 'Double or nothing — or duel another member for the pot.'),
    Game.HORSERACE: ('horserace <bet> <horse>', 'Back a horse; parimutuel payouts after the race.'),
    Game.RUSSIAN_ROULETTE: ('russianroulette [ante]', 'Players ante in — the last one standing takes the pot.'),
    Game.TICTACTOE: ('tictactoe <member>', 'Classic three-in-a-row against another member.'),
    Game.MINESWEEPER: ('minesweeper [mines]', 'Clear the board without setting off a mine.'),
    Game.HANGMAN: ('hangman', 'Guess the word letter by letter before the tries run out.'),
    Game.TRIVIA: ('trivia', 'First member to answer correctly wins coins.'),
    Game.WORDLE: ('wordle', "Solve the guild's daily 5-letter word in six tries."),
}

#: Games that take a stake, shown first.
CASINO_GAMES: tuple[Game, ...] = (
    Game.BLACKJACK, Game.SLOTS, Game.ROULETTE, Game.POKER, Game.TOWER, Game.HIGHERLOWER,
    Game.DICE, Game.MINES, Game.COINFLIP, Game.HORSERACE, Game.RUSSIAN_ROULETTE,
)

#: Free-to-play party games.
PARTY_GAMES: tuple[Game, ...] = (
    Game.TICTACTOE, Game.MINESWEEPER, Game.HANGMAN, Game.TRIVIA, Game.WORDLE,
)


class _GameSelect(discord.ui.Select['GamesHub']):
    """The game picker at the bottom of the hub."""

    def __init__(self, current: str) -> None:
        options = [
            discord.SelectOption(
                label='Overview', value='overview', emoji='\N{VIDEO GAME}', default=current == 'overview'
            )
        ]
        options += [
            discord.SelectOption(label=game.label, value=game.value, emoji=game.icon, default=current == game.value)
            for game in (*CASINO_GAMES, *PARTY_GAMES)
        ]
        super().__init__(placeholder='Inspect a game…', options=options)

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.view is not None
        self.view.page = self.values[0]
        await self.view.build()
        await interaction.response.edit_message(view=self.view)


class GamesHub(LayoutView):
    """One interactive card for the whole games catalogue.

    The overview lists every game with its start command plus the member's
    all-time record; picking a game from the select shows how to play it and
    the member's per-game stats. Invoker-locked, times out quietly.
    """

    def __init__(self, cog: Games, ctx: Context) -> None:
        super().__init__(timeout=180.0, members=ctx.author)
        self.cog = cog
        self.ctx = ctx
        self.page = 'overview'
        self.message: discord.Message | None = None
        self._rows: dict[str, Any] = {}
        self._totals: Any | None = None

    async def prepare(self) -> None:
        """Fetch the member's stats once and build the first page."""
        assert self.ctx.guild is not None
        stats = self.cog.bot.db.game_stats
        rows = await stats.get_member_games(self.ctx.guild.id, self.ctx.author.id)
        self._rows = {row['game']: row for row in rows}
        self._totals = await stats.get_member_totals(self.ctx.guild.id, self.ctx.author.id)
        await self.build()

    async def build(self) -> None:
        """(Re)build the layout for the current page."""
        container = self._page_overview() if self.page == 'overview' else self._page_game(Game(self.page))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.ActionRow(_GameSelect(self.page)))
        self.clear_items()
        self.add_item(container)

    async def on_timeout(self) -> None:
        if self.message is not None:
            for child in self.walk_children():
                if isinstance(child, discord.ui.Select):
                    child.disabled = True
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    # -- pages ----------------------------------------------------------------

    @staticmethod
    def _catalogue_line(game: Game) -> str:
        usage, tagline = GAME_DETAILS[game]
        return f'{game.icon} **{game.label}** `{usage.split(" ")[0]}` — {tagline}'

    def _page_overview(self) -> discord.ui.Container:
        container = discord.ui.Container(accent_colour=helpers.Colour.brand())
        container.add_item(discord.ui.Section(
            '## \N{VIDEO GAME} Games Arcade\n'
            'Every game on this server — pick one below for rules and your personal record.',
            accessory=discord.ui.Thumbnail(get_asset_url(self.ctx.author)),
        ))
        container.add_item(discord.ui.Separator())
        container.add_item(discord.ui.TextDisplay(
            '**\N{GAME DIE} Casino** *(play for coins)*\n' + '\n'.join(map(self._catalogue_line, CASINO_GAMES))
        ))
        container.add_item(discord.ui.TextDisplay(
            '**\N{PARTY POPPER} Party** *(free to play)*\n' + '\n'.join(map(self._catalogue_line, PARTY_GAMES))
        ))
        container.add_item(discord.ui.Separator())

        totals = self._totals
        if totals is None or not totals['played']:
            record = '*You have no recorded rounds yet — go play something!*'
        else:
            profit = totals['profit']
            record = (
                f'**Your record:** {fnumb(totals["played"])} rounds • '
                f'{fnumb(totals["won"])}W / {fnumb(totals["lost"])}L / {fnumb(totals["tied"])}T • '
                f'net {Emojis.Economy.cash} **{"+" if profit >= 0 else ""}{fnumb(profit)}**'
            )
        container.add_item(discord.ui.TextDisplay(record))
        return container

    def _page_game(self, game: Game) -> discord.ui.Container:
        usage, tagline = GAME_DETAILS[game]
        container = discord.ui.Container(accent_colour=helpers.Colour.brand())
        container.add_item(discord.ui.TextDisplay(
            f'## {game.icon} {game.label}\n{tagline}\nStart with: `{usage}`'
        ))
        container.add_item(discord.ui.Separator())

        row = self._rows.get(game.value)
        if row is None or not row['played']:
            container.add_item(discord.ui.TextDisplay(
                "*You haven't played this yet — your stats will show up here.*"
            ))
            return container

        winrate = row['won'] / row['played'] if row['played'] else 0.0
        profit = row['profit']
        lines = [
            f'Rounds: **{fnumb(row["played"])}** ({fnumb(row["won"])}W / {fnumb(row["lost"])}L / {fnumb(row["tied"])}T '
            f'— {winrate:.0%} winrate)',
            f'Net profit: {Emojis.Economy.cash} **{"+" if profit >= 0 else ""}{fnumb(profit)}** '
            f'(wagered {fnumb(row["wagered"])})',
            f'Biggest win: {Emojis.Economy.cash} **{fnumb(row["biggest_win"])}**',
            f'Best streak: **{fnumb(row["best_streak"])}** • Current: **{fnumb(row["current_streak"])}**',
        ]
        container.add_item(discord.ui.TextDisplay('\n'.join(lines)))
        return container
