"""Pure work-shift challenges for the economy ``work`` command (no ``discord`` imports).

Each challenge is a quick pick-one-of-four question: a generic puzzle, or a
question about the member's own job. The cog times it, and the result only scales the shift payout (:data:`WORK_SUCCESS_BONUS` /
:data:`WORK_FAIL_FACTOR`); the shift always counts toward the career ladder.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

__all__ = (
    'CHALLENGE_KINDS',
    'WORK_CHALLENGE_TIMEOUT',
    'WORK_FAIL_FACTOR',
    'WORK_SUCCESS_BONUS',
    'Challenge',
    'generate_challenge',
)

#: Payout multiplier for a correct answer.
WORK_SUCCESS_BONUS = 1.25
#: Payout multiplier for a wrong answer or a timeout.
WORK_FAIL_FACTOR = 0.5
#: Seconds the member has to answer.
WORK_CHALLENGE_TIMEOUT = 20.0

#: The generic puzzles; a member's job can also serve a ``job`` question (see :data:`_JOB_TASKS`).
CHALLENGE_KINDS: tuple[str, ...] = ('unscramble', 'memory', 'colour', 'math', 'sequence', 'count', 'odd', 'spelling')

#: Chance a shift asks about the member's job instead of a generic puzzle.
_JOB_TASK_CHANCE = 0.5

_WORDS: tuple[str, ...] = (
    'barista', 'cashier', 'cook', 'invoice', 'ledger', 'shift', 'boss', 'office',
    'deadline', 'meeting', 'payroll', 'client', 'wrench', 'laptop', 'scalpel',
    'rocket', 'coffee', 'tickets', 'orders', 'budget', 'report', 'manager', 'uniform',
    'espresso', 'parcel', 'receipt', 'gardener', 'recipe', 'cocktail', 'circuit', 'hydrant',
    'compiler', 'runway', 'verdict', 'orbit', 'investor', 'senator',
)

_EMOJIS: tuple[str, ...] = (
    '\N{RED APPLE}', '\N{BANANA}', '\N{GRAPES}', '\N{CHERRIES}',
    '\N{LEMON}', '\N{WATERMELON}', '\N{STRAWBERRY}', '\N{PEACH}',
)

_COLOURS: tuple[tuple[str, str], ...] = (
    ('red', '\N{LARGE RED SQUARE}'),
    ('blue', '\N{LARGE BLUE SQUARE}'),
    ('green', '\N{LARGE GREEN SQUARE}'),
    ('yellow', '\N{LARGE YELLOW SQUARE}'),
    ('orange', '\N{LARGE ORANGE SQUARE}'),
    ('purple', '\N{LARGE PURPLE SQUARE}'),
)

_ODD_GROUPS: tuple[tuple[str, ...], ...] = (
    _EMOJIS,
    ('\N{HAMMER}', '\N{WRENCH}', '\N{PICK}', '\N{NUT AND BOLT}', '\N{SCREWDRIVER}'),
    ('\N{AUTOMOBILE}', '\N{BUS}', '\N{BICYCLE}', '\N{ROCKET}', '\N{AIRPLANE}'),
    ('\N{DOG FACE}', '\N{CAT FACE}', '\N{MOUSE FACE}', '\N{FROG FACE}', '\N{PANDA FACE}'),
)

#: Questions per job id: ``(question, right answer, three wrong answers)``.
_JOB_TASKS: dict[str, tuple[tuple[str, str, tuple[str, str, str]], ...]] = {
    'freelancer': (
        ('What do you send a client after finishing the work?', 'An invoice', ('A postcard', 'A subpoena', 'A fruit basket')),
        ('A client asks for "one tiny change". What is it?', 'Scope creep', ('A tip', 'A holiday', 'A promotion')),
        ('What should you agree on before you start?', 'The price', ('Your star sign', 'The weather', 'Lunch')),
    ),
    'dog_walker': (
        ('What do you always carry on a walk?', 'Poop bags', ('A ladder', 'A stapler', 'A trumpet')),
        ('Which food is toxic to dogs?', 'Chocolate', ('Carrots', 'Plain rice', 'Blueberries')),
        ('A wagging tail held low usually means the dog is…', 'Unsure', ('Asleep', 'Hungry', 'Sunburnt')),
    ),
    'barista': (
        ('What is a latte made of?', 'Espresso and milk', ('Tea and honey', 'Water and sugar', 'Cocoa and cream')),
        ('Which drink has the least milk?', 'Macchiato', ('Latte', 'Flat white', 'Cappuccino')),
        ('What do you do to the grounds before pulling a shot?', 'Tamp them', ('Salt them', 'Rinse them', 'Freeze them')),
    ),
    'courier': (
        ('A parcel says "fragile". What do you do?', 'Handle it gently', ('Shake it', 'Open it', 'Sit on it')),
        ('What proves that a parcel was delivered?', 'A signature', ('A selfie with a dog', 'A rumour', 'A horn honk')),
        ('What matters most when riding in the rain?', 'Working brakes', ('A nice hat', 'A loud bell', 'Fresh socks')),
    ),
    'cashier': (
        ('An item costs 7 and the customer pays with 10. What is the change?', '3', ('2', '4', '7')),
        ('What do you scan on a product?', 'The barcode', ('The price tag glue', 'The bag', 'The shelf')),
        ('What should you offer after the sale?', 'The receipt', ('A hug', 'A loan', 'A lecture')),
    ),
    'gardener': (
        ('What do plants take in from the air?', 'Carbon dioxide', ('Helium', 'Neon', 'Steam')),
        ('When is the best time to water in summer?', 'Early morning', ('Midday', 'During a storm', 'Never')),
        ('What do you call removing dead flowers?', 'Deadheading', ('Debugging', 'Decanting', 'Defrosting')),
    ),
    'line_cook': (
        ('What does "mise en place" mean?', 'Everything in its place', ('Table for two', 'Well done', 'Kitchen closed')),
        ('What do you put on a grease fire?', 'A lid', ('Water', 'Flour', 'More oil')),
        ('Which knife is the kitchen all-rounder?', 'Chef\'s knife', ('Butter knife', 'Paring knife', 'Bread knife')),
    ),
    'bartender': (
        ('What goes into a classic mojito?', 'Rum and mint', ('Gin and olives', 'Vodka and cream', 'Whisky and cola')),
        ('What does "neat" mean?', 'No ice, no mixer', ('With soda', 'Shaken twice', 'In a clean glass')),
        ('What tool measures a pour?', 'A jigger', ('A muddler', 'A strainer', 'A zester')),
    ),
    'electrician': (
        ('Which unit measures electrical current?', 'Ampere', ('Volt', 'Ohm', 'Watt')),
        ('What do you do before touching a circuit?', 'Turn off the power', ('Lick your finger', 'Ask the wall', 'Wear socks')),
        ('What protects a circuit from overload?', 'A breaker', ('A dimmer', 'A socket', 'A doorbell')),
    ),
    'firefighter': (
        ('What should you do in a smoky room?', 'Stay low', ('Open a beer', 'Stand tall', 'Run upstairs')),
        ('Which number is the emergency line in the EU?', '112', ('411', '123', '555')),
        ('Which three things does a fire need?', 'Heat, fuel, oxygen', ('Heat, water, wind', 'Fuel, salt, sand', 'Oxygen, ice, gas')),
    ),
    'programmer': (
        ('What does HTTP status 404 mean?', 'Not found', ('Server on fire', 'All good', 'Too many cats')),
        ('Which structure works first in, first out?', 'A queue', ('A stack', 'A tree', 'A hash')),
        ('What does `git commit` do?', 'Records changes', ('Deletes the repo', 'Runs the tests', 'Sends an email')),
    ),
    'pilot': (
        ('What does the altimeter show?', 'Altitude', ('Fuel', 'Speed', 'Time')),
        ('What do you call the front of the plane where you sit?', 'The cockpit', ('The galley', 'The hold', 'The tail')),
        ('What is the international distress call?', 'Mayday', ('Payday', 'Heyday', 'Sunday')),
    ),
    'surgeon': (
        ('Which organ pumps blood?', 'The heart', ('The liver', 'The spleen', 'The lung')),
        ('What do you do first before operating?', 'Scrub in', ('Have lunch', 'Take a selfie', 'Call your mum')),
        ('What is the largest organ of the body?', 'The skin', ('The brain', 'The kidney', 'The stomach')),
    ),
    'judge': (
        ('What is a judge\'s little hammer called?', 'A gavel', ('A mace', 'A chisel', 'A pestle')),
        ('What do you call a false statement under oath?', 'Perjury', ('Parole', 'Bail', 'Appeal')),
        ('Who decides the verdict in a jury trial?', 'The jury', ('The bailiff', 'The stenographer', 'The press')),
    ),
    'astronaut': (
        ('Which planet is closest to the Sun?', 'Mercury', ('Venus', 'Mars', 'Earth')),
        ('What keeps the station in orbit around Earth?', 'Gravity', ('Solar wind', 'Magnets', 'Sheer optimism')),
        ('What do you call a spacewalk?', 'An EVA', ('A BLT', 'An MRI', 'A PIN')),
    ),
    'founder': (
        ('What does MVP mean to a startup?', 'Minimum viable product', ('Most valuable player', 'Monthly venture profit', 'Max VC payout')),
        ('What is your runway?', 'Months of cash left', ('An office corridor', 'A fashion show', 'The pitch deck')),
        ('What do you give investors in return for funding?', 'Equity', ('Coupons', 'Your stapler', 'Free snacks')),
    ),
    'president': (
        ('What do you call a formal order from a head of state?', 'A decree', ('A receipt', 'A memo pad', 'A coupon')),
        ('A meeting of two national leaders is called a…', 'Summit', ('Picnic', 'Scrum', 'Stand-up')),
        ('Who writes most of your speeches?', 'A speechwriter', ('The chef', 'The chauffeur', 'A pigeon')),
    ),
}

_OPTION_COUNT = 4


@dataclass(frozen=True, slots=True)
class Challenge:
    """One generated work challenge."""

    kind: str
    #: Text shown to the member (for ``memory`` this is the sequence to remember).
    prompt: str
    #: Exactly four distinct button labels, in display order.
    options: tuple[str, ...]
    #: The correct entry of :attr:`options`.
    answer: str


def _scramble(word: str, rng: random.Random) -> str:
    letters = list(word)
    for _ in range(20):
        rng.shuffle(letters)
        if ''.join(letters) != word:
            return ''.join(letters)
    return word[::-1]  # ponytail: only reachable for a one-letter-repeated word; none ship in _WORDS.


def _unscramble(rng: random.Random) -> Challenge:
    word = rng.choice(_WORDS)
    options = [word, *rng.sample([w for w in _WORDS if w != word], _OPTION_COUNT - 1)]
    rng.shuffle(options)
    return Challenge('unscramble', f'Unscramble: **{_scramble(word, rng).upper()}**', tuple(options), word)


def _memory(rng: random.Random) -> Challenge:
    sequence = tuple(rng.choices(_EMOJIS, k=4))
    options = {sequence}
    while len(options) < _OPTION_COUNT:
        options.add(tuple(rng.choices(_EMOJIS, k=4)))
    labels = [''.join(seq) for seq in options]
    rng.shuffle(labels)
    answer = ''.join(sequence)
    return Challenge('memory', answer, tuple(labels), answer)


def _colour(rng: random.Random) -> Challenge:
    word, _ = rng.choice(_COLOURS)
    square = rng.choice(_COLOURS)[1]  # often a different colour than the word: that is the trap
    options = [word, *rng.sample([c for c, _ in _COLOURS if c != word], _OPTION_COUNT - 1)]
    rng.shuffle(options)
    return Challenge('colour', f'What colour does the **word** say?\n{square} **{word.upper()}**', tuple(options), word)


def _numbers(answer: int, rng: random.Random) -> tuple[str, ...]:
    """The right number and three near misses, shuffled."""
    options = [answer + offset for offset in rng.sample((-10, -5, -2, -1, 1, 2, 5, 10), _OPTION_COUNT - 1)]
    options.append(answer)
    rng.shuffle(options)
    return tuple(str(option) for option in options)


def _math(rng: random.Random) -> Challenge:
    symbol = rng.choice(('+', '-', '\N{MULTIPLICATION SIGN}'))
    if symbol == '\N{MULTIPLICATION SIGN}':
        a, b = rng.randint(12, 30), rng.randint(2, 5)
        answer = a * b
    else:
        a, b = rng.randint(25, 60), rng.randint(2, 11)
        answer = a + b if symbol == '+' else a - b
    return Challenge('math', f'Balance the books: **{a} {symbol} {b}** = ?', _numbers(answer, rng), str(answer))


def _sequence(rng: random.Random) -> Challenge:
    start, step = rng.randint(2, 9), rng.randint(2, 9)
    terms = [start + step * i for i in range(5)] if rng.random() < 0.5 else [start * 2**i for i in range(5)]
    shown = ', '.join(str(term) for term in terms[:-1])
    return Challenge('sequence', f'Finish the pattern: **{shown}, ?**', _numbers(terms[-1], rng), str(terms[-1]))


def _count(rng: random.Random) -> Challenge:
    target, *others = rng.sample(_EMOJIS, 3)
    answer = rng.randint(2, 6)
    row = [target] * answer + rng.choices(others, k=9 - answer)
    rng.shuffle(row)
    options = [answer, *rng.sample([n for n in range(1, 8) if n != answer], _OPTION_COUNT - 1)]
    rng.shuffle(options)
    return Challenge('count', f'Stock check: how many {target}?\n{"".join(row)}', tuple(map(str, options)), str(answer))


def _odd(rng: random.Random) -> Challenge:
    same, other = rng.sample(_ODD_GROUPS, 2)
    answer = rng.choice(other)
    options = [answer, *rng.sample(same, _OPTION_COUNT - 1)]
    rng.shuffle(options)
    return Challenge('odd', 'Sort the delivery: which one does **not** belong?', tuple(options), answer)


def _misspellings(word: str) -> set[str]:
    """Every swap of neighbouring letters and every dropped letter, minus the word itself."""
    swapped = {word[:i] + word[i + 1] + word[i] + word[i + 2:] for i in range(len(word) - 1)}
    dropped = {word[:i] + word[i + 1:] for i in range(len(word))}
    return (swapped | dropped) - {word}


def _spelling(rng: random.Random) -> Challenge:
    word = rng.choice(_WORDS)
    options = [word, *rng.sample(sorted(_misspellings(word)), _OPTION_COUNT - 1)]
    rng.shuffle(options)
    return Challenge('spelling', 'Proofread the memo: which one is spelled **correctly**?', tuple(options), word)


def _job_task(job_id: str, rng: random.Random) -> Challenge:
    question, answer, wrong = rng.choice(_JOB_TASKS[job_id])
    options = [answer, *wrong]
    rng.shuffle(options)
    return Challenge('job', question, tuple(options), answer)


_GENERATORS = {
    'unscramble': _unscramble,
    'memory': _memory,
    'colour': _colour,
    'math': _math,
    'sequence': _sequence,
    'count': _count,
    'odd': _odd,
    'spelling': _spelling,
}


def generate_challenge(rng: random.Random | None = None, *, job_id: str | None = None) -> Challenge:
    """Generate a random challenge; with ``job_id``, half of them are about that job."""
    chooser = rng or random.Random()
    if job_id in _JOB_TASKS and chooser.random() < _JOB_TASK_CHANCE:
        return _job_task(job_id, chooser)
    return _GENERATORS[chooser.choice(CHALLENGE_KINDS)](chooser)
