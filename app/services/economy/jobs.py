"""Pure job-ladder logic for the economy ``work``/``job`` commands (no ``discord`` imports).

Members climb a static career ladder: every job has a pay range and a lifetime
shift requirement to unlock. Working a shift rolls a payout plus a random shift
event (overtime, a tip, a mishap). The cog persists only ``(job_id, shifts)``;
everything else lives here so it can be unit-tested.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

__all__ = (
    'JOB_LADDER',
    'SHIFT_EVENTS',
    'WORK_COOLDOWN',
    'Job',
    'ShiftEvent',
    'ShiftResult',
    'available_jobs',
    'compute_shift',
    'get_job',
    'next_unlock',
)

#: Minimum seconds between ``work`` shifts.
WORK_COOLDOWN = 3600


@dataclass(frozen=True, slots=True)
class Job:
    """A rung on the career ladder."""

    id: str
    name: str
    emoji: str
    pay_min: int
    pay_max: int
    #: Lifetime shifts (across all jobs) required before this job can be applied for.
    shifts_required: int
    #: Shift result lines, formatted with ``coins``; empty means the generic odd-job pool.
    lines: tuple[str, ...] = ()


#: The career ladder, lowest to highest. The first entry is the default for
#: members who never applied anywhere ("unemployed" still gets odd jobs).
JOB_LADDER: tuple[Job, ...] = (
    Job('freelancer', 'Freelancer', '\N{OPEN MAILBOX WITH RAISED FLAG}', 20, 250, 0),
    Job('dog_walker', 'Dog Walker', '\N{DOG FACE}', 50, 260, 2, (
        'Six dogs, one leash tangle, zero regrets. The owners paid {coins}.',
        'A golden retriever dragged you through every puddle in town. You earned {coins}.',
        'You survived the squirrel incident and collected {coins}.',
    )),
    Job('barista', 'Barista', '\N{HOT BEVERAGE}', 80, 300, 5, (
        'You spelled every name wrong and still took home {coins}.',
        'Your latte art finally looked like a leaf. {coins} in pay and tips.',
        'The morning rush nearly broke you, but {coins} landed in your pocket.',
    )),
    Job('courier', 'Bike Courier', '\N{BICYCLE}', 110, 360, 10, (
        'You caught every green light and delivered everything for {coins}.',
        'Forty packages, one flat tyre, {coins} earned.',
        'A customer tipped you for carrying a sofa upstairs. Total: {coins}.',
    )),
    Job('cashier', 'Cashier', '\N{SHOPPING TROLLEY}', 140, 420, 15, (
        'You asked "receipt in the bag?" a thousand times and earned {coins}.',
        'Someone paid in pennies. You kept your smile and earned {coins}.',
        'The self-checkout broke again, so you saved the day for {coins}.',
    )),
    Job('gardener', 'Gardener', '\N{SEEDLING}', 180, 490, 22, (
        'The hedges have never looked this sharp. The client paid {coins}.',
        'You won a long war against the weeds and earned {coins}.',
        'An old lady approved of your roses and handed you {coins}.',
    )),
    Job('line_cook', 'Line Cook', '\N{COOKING}', 220, 560, 30, (
        'Tickets piled up, you yelled "behind!" a lot, and the night paid {coins}.',
        'Not one steak came back to the kitchen. You earned {coins}.',
        'You kept the flat-top alive through the dinner rush for {coins}.',
    )),
    Job('bartender', 'Bartender', '\N{COCKTAIL GLASS}', 270, 650, 42, (
        'You listened to eleven life stories and took home {coins}.',
        'Your signature cocktail had a queue. You earned {coins}.',
        'You closed the bar without a single broken glass and earned {coins}.',
    )),
    Job('electrician', 'Electrician', '\N{ELECTRIC PLUG}', 320, 750, 55, (
        'You found the one bad fuse nobody else could. The invoice said {coins}.',
        'The lights came back on and the client paid {coins}.',
        'You rewired a whole floor without a single spark. {coins} earned.',
    )),
    Job('firefighter', 'Firefighter', '\N{FIRE ENGINE}', 380, 870, 72, (
        'You got the cat down from the tree, again. The shift paid {coins}.',
        'A clean response and a safe crew. You earned {coins}.',
        'You spent the shift polishing the engine and drilling hoses for {coins}.',
    )),
    Job('programmer', 'Programmer', '\N{PERSONAL COMPUTER}', 450, 1000, 90, (
        'You fixed the bug by adding one semicolon and billed {coins}.',
        'It worked on your machine, and now it works on theirs. {coins} earned.',
        'You closed twelve tickets and opened thirteen. Payday: {coins}.',
    )),
    Job('pilot', 'Pilot', '\N{AIRPLANE}', 530, 1170, 115, (
        'You landed so softly that the passengers applauded. The flight paid {coins}.',
        'You made up twenty minutes over the Atlantic and earned {coins}.',
        'Crosswinds, one go-around, a perfect touchdown: {coins}.',
    )),
    Job('surgeon', 'Surgeon', '\N{SYRINGE}', 620, 1350, 140, (
        'Eight hours on your feet and a patient who will be fine. You earned {coins}.',
        'You asked for the scalpel in a very dramatic voice. The shift paid {coins}.',
        'A textbook procedure, and the hospital paid {coins}.',
    )),
    Job('judge', 'Judge', '\N{SCALES}', 730, 1560, 170, (
        'You sustained, overruled and banged the gavel for {coins}.',
        'Your ruling will be cited for years. The court paid {coins}.',
        'You kept a straight face through the whole trial and earned {coins}.',
    )),
    Job('astronaut', 'Astronaut', '\N{ROCKET}', 850, 1800, 200, (
        'You fixed a solar panel with the whole planet below you. Hazard pay: {coins}.',
        'Your experiment floated off, you caught it, and mission control sent {coins}.',
        'You orbited the Earth sixteen times before lunch and earned {coins}.',
    )),
    Job('founder', 'Startup Founder', '\N{UNICORN FACE}', 1050, 2200, 260, (
        'You said "disrupt" nine times in one pitch and the investors wired {coins}.',
        'The product still does not exist, but the runway grew by {coins}.',
        'You pivoted twice before noon and paid yourself {coins}.',
    )),
    Job('president', 'President', '\N{CLASSICAL BUILDING}', 1300, 2700, 350, (
        'You shook four hundred hands and signed what they put in front of you. Salary: {coins}.',
        'You dodged every question at the press conference and earned {coins}.',
        'One state dinner and one small crisis later, you collected {coins}.',
    )),
)

_JOBS_BY_ID: dict[str, Job] = {job.id: job for job in JOB_LADDER}


def get_job(job_id: str | None) -> Job:
    """The job for a stored id, falling back to the default bottom rung."""
    if job_id is None:
        return JOB_LADDER[0]
    return _JOBS_BY_ID.get(job_id, JOB_LADDER[0])


def available_jobs(shifts: int) -> tuple[Job, ...]:
    """Every job a member with ``shifts`` lifetime shifts may apply for."""
    return tuple(job for job in JOB_LADDER if job.shifts_required <= shifts)


def next_unlock(shifts: int) -> Job | None:
    """The next job on the ladder still locked at ``shifts``, or ``None`` at the top."""
    for job in JOB_LADDER:
        if job.shifts_required > shifts:
            return job
    return None


@dataclass(frozen=True, slots=True)
class ShiftEvent:
    """A random event colouring a shift's payout."""

    id: str
    #: Payout multiplier applied to the base roll.
    multiplier: float
    weight: int
    flavor: str


#: Weighted shift events; ``normal`` keeps the base roll.
SHIFT_EVENTS: tuple[ShiftEvent, ...] = (
    ShiftEvent('normal', 1.0, 58, 'A quiet shift.'),
    ShiftEvent('overtime', 1.5, 8, '\N{ALARM CLOCK} You pulled overtime and earned extra!'),
    ShiftEvent('tip', 1.25, 8, '\N{WRAPPED PRESENT} A generous customer tipped you!'),
    ShiftEvent('praise', 1.1, 8, '\N{CLAPPING HANDS SIGN} Your boss noticed and added a little extra.'),
    ShiftEvent('bonus', 2.0, 2, '\N{SPORTS MEDAL} Employee of the month: your pay doubled!'),
    ShiftEvent('slow', 0.75, 8, '\N{SNAIL} A slow day: your hours got cut short.'),
    ShiftEvent('mishap', 0.5, 8, '\N{COLLISION SYMBOL} You broke something and half your pay covered the damage.'),
)


@dataclass(frozen=True, slots=True)
class ShiftResult:
    """The resolved outcome of one worked shift."""

    amount: int
    event: ShiftEvent


def compute_shift(job: Job, *, rng: random.Random | None = None) -> ShiftResult:
    """Roll a shift payout for ``job``: a base pay roll times a random shift event."""
    chooser = rng or random
    base = chooser.randint(job.pay_min, job.pay_max)
    event = chooser.choices(SHIFT_EVENTS, weights=[e.weight for e in SHIFT_EVENTS], k=1)[0]
    return ShiftResult(max(round(base * event.multiplier), 1), event)
