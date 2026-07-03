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


#: The career ladder, lowest to highest. The first entry is the default for
#: members who never applied anywhere ("unemployed" still gets odd jobs).
JOB_LADDER: tuple[Job, ...] = (
    Job('freelancer', 'Freelancer', '\N{OPEN MAILBOX WITH RAISED FLAG}', 20, 250, 0),
    Job('barista', 'Barista', '\N{HOT BEVERAGE}', 80, 300, 5),
    Job('cashier', 'Cashier', '\N{SHOPPING TROLLEY}', 140, 420, 15),
    Job('line_cook', 'Line Cook', '\N{COOKING}', 220, 560, 30),
    Job('electrician', 'Electrician', '\N{ELECTRIC PLUG}', 320, 750, 55),
    Job('programmer', 'Programmer', '\N{PERSONAL COMPUTER}', 450, 1000, 90),
    Job('surgeon', 'Surgeon', '\N{SYRINGE}', 620, 1350, 140),
    Job('astronaut', 'Astronaut', '\N{ROCKET}', 850, 1800, 200),
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
    ShiftEvent('normal', 1.0, 70, 'A quiet shift.'),
    ShiftEvent('overtime', 1.5, 10, '\N{ALARM CLOCK} You pulled overtime and earned extra!'),
    ShiftEvent('tip', 1.25, 10, '\N{WRAPPED PRESENT} A generous customer tipped you!'),
    ShiftEvent('mishap', 0.5, 10, '\N{COLLISION SYMBOL} You broke something and half your pay covered the damage.'),
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
