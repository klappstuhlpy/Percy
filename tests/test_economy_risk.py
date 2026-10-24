import random

from app.services.economy import DEATH_CHANCES, roll_death


def test_zero_chance_and_unknown_activity_never_kill() -> None:
    rng = random.Random(1)
    assert not any(roll_death('postmemes', rng) for _ in range(2000))
    assert not any(roll_death('nonsense', rng) for _ in range(2000))


def test_death_rate_roughly_matches_chance() -> None:
    rng = random.Random(7)
    deaths = sum(roll_death('search', rng) for _ in range(4000))
    assert abs(deaths / 4000 - DEATH_CHANCES['search']) < 0.03


def test_chances_are_the_specified_values() -> None:
    assert DEATH_CHANCES == {'hunt': 0.04, 'crime': 0.10, 'search': 0.25, 'postmemes': 0.0}
