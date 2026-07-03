"""Pure logic for the light earning activities: ``beg``, ``dig`` and ``search``.

These mirror :data:`~app.services.economy.core.FISHING_TABLE` / ``HUNTING_TABLE``:
weighted loot tables the cog rolls against, plus the ``search`` location catalogue
where the member picks one of three random spots — some of which can go wrong and
cost cash instead.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.economy.core import Catch, LootEntry, roll_loot

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = (
    'BEG_COOLDOWN',
    'BEG_DONORS',
    'BEG_FAIL_LINES',
    'BEG_SUCCESS_LINES',
    'BEG_TABLE',
    'DIG_COOLDOWN',
    'DIG_TABLE',
    'SEARCH_COOLDOWN',
    'SEARCH_LOCATIONS',
    'SearchLocation',
    'SearchOutcome',
    'pick_search_options',
    'resolve_search',
)

#: Minimum seconds between ``beg`` attempts.
BEG_COOLDOWN = 45
#: Minimum seconds between ``dig`` attempts.
DIG_COOLDOWN = 90
#: Minimum seconds between ``search`` attempts.
SEARCH_COOLDOWN = 75

#: Begging outcomes — small change most of the time, the occasional windfall.
BEG_TABLE: tuple[LootEntry, ...] = (
    LootEntry('nothing', '\N{PENSIVE FACE}', 0, 0, 25),
    LootEntry('pocket change', '\N{COIN}', 5, 40, 40),
    LootEntry('a crumpled note', '\N{BANKNOTE WITH DOLLAR SIGN}', 40, 110, 25),
    LootEntry('a generous donation', '\N{SMILING FACE WITH HALO}', 120, 250, 9),
    LootEntry('a mysterious benefactor', '\N{TOP HAT}', 300, 600, 1),
)

#: Who tossed you the coins — pure flavour for the beg command.
BEG_DONORS: tuple[str, ...] = (
    'your landlord',
    'a friendly capybara',
    'the guy from the gym',
    'a passing streamer',
    'your old math teacher',
    'a suspiciously wealthy pigeon',
    'grandma',
    'a tired barista',
    'someone who mistook you for a busker',
    'the mayor',
)

#: Flavour lines when begging pays out; formatted with ``donor`` and ``coins``.
BEG_SUCCESS_LINES: tuple[str, ...] = (
    '{donor} felt generous and gave you {coins}.',
    '{donor} tossed {coins} into your cup without breaking stride.',
    'You put on your saddest face and {donor} handed over {coins}.',
    '{donor} said "get yourself something nice" and gave you {coins}.',
)

#: Flavour lines when begging yields nothing.
BEG_FAIL_LINES: tuple[str, ...] = (
    'People pretended not to see you. Rough day.',
    'Someone gave you advice instead of money. Thanks.',
    'A pigeon stole the one coin you had. Devastating.',
    'You got a coupon that expired last week.',
)

#: Digging outcomes — junk to buried treasure, on a mid-length cooldown.
DIG_TABLE: tuple[LootEntry, ...] = (
    LootEntry('a bottle cap', '\N{NUT AND BOLT}', 0, 8, 22),
    LootEntry('a rusty spoon', '\N{SPOON}', 10, 45, 34),
    LootEntry('an old coin pouch', '\N{COIN}', 60, 140, 26),
    LootEntry('a silver ring', '\N{RING}', 160, 320, 12),
    LootEntry('a buried strongbox', '\N{CLOSED LOCK WITH KEY}', 450, 850, 5),
    LootEntry('an ancient relic', '\N{AMPHORA}', 1000, 1800, 1),
)


@dataclass(frozen=True, slots=True)
class SearchLocation:
    """A spot the member can rummage through, with its own loot table and risk."""

    id: str
    name: str
    emoji: str
    table: tuple[LootEntry, ...]
    #: Chance the search goes wrong (0..1); the member then loses ``injury_min..injury_max``.
    injury_chance: float
    injury_min: int
    injury_max: int
    injury_flavor: str


#: The search catalogue. Riskier spots carry better tables.
SEARCH_LOCATIONS: tuple[SearchLocation, ...] = (
    SearchLocation(
        'couch', 'Your Couch', '\N{COUCH AND LAMP}',
        (
            LootEntry('lint', '\N{CLOUD}', 0, 5, 30),
            LootEntry('loose change', '\N{COIN}', 15, 60, 55),
            LootEntry('a forgotten wallet', '\N{PURSE}', 80, 160, 15),
        ),
        0.0, 0, 0, '',
    ),
    SearchLocation(
        'mailbox', 'The Mailbox', '\N{OPEN MAILBOX WITH RAISED FLAG}',
        (
            LootEntry('spam mail', '\N{WASTEBASKET}', 0, 5, 35),
            LootEntry('a birthday card with cash', '\N{BIRTHDAY CAKE}', 40, 120, 50),
            LootEntry('a tax refund', '\N{BANKNOTE WITH DOLLAR SIGN}', 150, 300, 15),
        ),
        0.0, 0, 0, '',
    ),
    SearchLocation(
        'park', 'The Park', '\N{NATIONAL PARK}',
        (
            LootEntry('a lost frisbee', '\N{FLYING DISC}', 5, 25, 40),
            LootEntry('coins in the fountain', '\N{FOUNTAIN}', 40, 130, 45),
            LootEntry('a dropped money clip', '\N{BANKNOTE WITH DOLLAR SIGN}', 150, 350, 15),
        ),
        0.05, 10, 60, 'You slipped chasing a squirrel and paid for a bandage.',
    ),
    SearchLocation(
        'dumpster', 'The Dumpster', '\N{WASTEBASKET}',
        (
            LootEntry('smelly cardboard', '\N{ROLLED-UP NEWSPAPER}', 0, 10, 35),
            LootEntry('returnable bottles', '\N{BOTTLE WITH POPPING CORK}', 50, 150, 40),
            LootEntry('a barely used blender', '\N{HIGH VOLTAGE SIGN}', 180, 400, 25),
        ),
        0.15, 30, 120, 'A raccoon defended its territory. You lost the fight and some cash.',
    ),
    SearchLocation(
        'sewer', 'The Sewer', '\N{HOLE}',
        (
            LootEntry('a soggy sock', '\N{SOCKS}', 0, 10, 30),
            LootEntry('a dropped phone', '\N{MOBILE PHONE}', 100, 260, 45),
            LootEntry("someone's stash", '\N{MONEY BAG}', 300, 650, 25),
        ),
        0.25, 60, 200, 'You slipped into the muck and paid for a very thorough shower.',
    ),
    SearchLocation(
        'mall_fountain', 'The Mall Fountain', '\N{FOUNTAIN}',
        (
            LootEntry('wet pennies', '\N{COIN}', 10, 50, 55),
            LootEntry('a handful of wishes', '\N{GLOWING STAR}', 60, 160, 35),
            LootEntry('a diamond earring', '\N{GEM STONE}', 250, 500, 10),
        ),
        0.1, 20, 100, 'Security caught you knee-deep in the fountain. The fine stung.',
    ),
    SearchLocation(
        'abandoned_house', 'The Abandoned House', '\N{DERELICT HOUSE BUILDING}',
        (
            LootEntry('creaky floorboards', '\N{DOOR}', 0, 15, 30),
            LootEntry('antique silverware', '\N{FORK AND KNIFE}', 120, 280, 45),
            LootEntry('a hidden safe', '\N{CLOSED LOCK WITH KEY}', 400, 900, 25),
        ),
        0.3, 100, 300, 'The floor gave way. The hospital bill did too — right through your wallet.',
    ),
    SearchLocation(
        'bank_lobby', 'The Bank Lobby', '\N{BANK}',
        (
            LootEntry('a free lollipop', '\N{LOLLIPOP}', 0, 5, 30),
            LootEntry('an unclaimed envelope', '\N{ENVELOPE}', 150, 350, 45),
            LootEntry('a briefcase someone forgot', '\N{BRIEFCASE}', 500, 1100, 25),
        ),
        0.35, 150, 400, 'Security did not buy your "just looking" excuse. You paid the fine.',
    ),
    SearchLocation(
        'grandmas_kitchen', "Grandma's Kitchen", '\N{COOKIE}',
        (
            LootEntry('fresh cookies (priceless)', '\N{COOKIE}', 5, 20, 40),
            LootEntry('the cookie-jar fund', '\N{JAR}', 60, 180, 45),
            LootEntry('an heirloom brooch', '\N{GEM STONE}', 200, 450, 15),
        ),
        0.05, 5, 40, 'Grandma caught you elbow-deep in the cookie jar. Guilt tax applied.',
    ),
)


@dataclass(frozen=True, slots=True)
class SearchOutcome:
    """The result of rummaging through one location: either loot or an injury fine."""

    injured: bool
    #: The loot on success; ``None`` when injured.
    catch: Catch | None
    #: Cash lost on injury (0 on success).
    fine: int
    flavor: str


def pick_search_options(
    *, count: int = 3, rng: random.Random | None = None,
    locations: Sequence[SearchLocation] = SEARCH_LOCATIONS,
) -> tuple[SearchLocation, ...]:
    """Draw ``count`` distinct locations for the member to choose between."""
    chooser = rng or random
    return tuple(chooser.sample(list(locations), k=min(count, len(locations))))


def resolve_search(location: SearchLocation, *, rng: random.Random | None = None) -> SearchOutcome:
    """Roll one search at ``location``: injury first, then its loot table."""
    chooser = rng or random
    if location.injury_chance > 0 and chooser.random() < location.injury_chance:
        fine = chooser.randint(location.injury_min, location.injury_max)
        return SearchOutcome(True, None, fine, location.injury_flavor)
    return SearchOutcome(False, roll_loot(location.table, rng=rng), 0, '')
