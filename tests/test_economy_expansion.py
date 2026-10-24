"""Tests for the economy expansion services (jobs, activities, pets, quests, achievements, prestige)."""

from __future__ import annotations

import datetime
import random

from app.services.economy import (
    ACHIEVEMENTS,
    BEG_TABLE,
    DIG_TABLE,
    JOB_LADDER,
    MONTHLY_COOLDOWN,
    PET_SPECIES,
    PRESTIGE_BASE_REQUIREMENT,
    PRESTIGE_MAX_LEVEL,
    PRESTIGE_STEP,
    QUEST_POOL,
    SEARCH_LOCATIONS,
    SHIFT_EVENTS,
    WEEKLY_COOLDOWN,
    EconomySnapshot,
    GuildEconomySettings,
    HungerState,
    available_jobs,
    compute_periodic,
    compute_pet_claim,
    compute_shift,
    evaluate_achievements,
    generate_daily_quests,
    get_achievement,
    get_job,
    get_species,
    hunger_state,
    next_unlock,
    pick_search_options,
    prestige_multiplier,
    prestige_requirement,
    resolve_search,
    validate_item_effect,
)

UTC = datetime.UTC
NOW = datetime.datetime(2026, 7, 3, 12, 0, tzinfo=UTC)
TODAY = NOW.date()


# -- jobs --------------------------------------------------------------------


def test_job_ladder_is_ordered_and_unlockable() -> None:
    requirements = [job.shifts_required for job in JOB_LADDER]
    assert requirements == sorted(requirements)
    assert JOB_LADDER[0].shifts_required == 0
    for job in JOB_LADDER:
        assert 0 < job.pay_min <= job.pay_max


def test_get_job_falls_back_to_bottom_rung() -> None:
    assert get_job(None) is JOB_LADDER[0]
    assert get_job('no_such_job') is JOB_LADDER[0]
    assert get_job(JOB_LADDER[-1].id) is JOB_LADDER[-1]


def test_available_jobs_respects_shift_requirements() -> None:
    assert available_jobs(0) == (JOB_LADDER[0],)
    assert available_jobs(JOB_LADDER[-1].shifts_required) == JOB_LADDER


def test_next_unlock_walks_the_ladder() -> None:
    assert next_unlock(0) == JOB_LADDER[1]
    assert next_unlock(JOB_LADDER[-1].shifts_required) is None


def test_compute_shift_within_event_scaled_bounds() -> None:
    job = JOB_LADDER[0]
    multipliers = [event.multiplier for event in SHIFT_EVENTS]
    low = max(round(job.pay_min * min(multipliers)), 1)
    high = round(job.pay_max * max(multipliers))
    for seed in range(200):
        result = compute_shift(job, rng=random.Random(seed))
        assert low <= result.amount <= high
        assert result.event in SHIFT_EVENTS


def test_compute_shift_is_deterministic_for_a_seed() -> None:
    job = JOB_LADDER[3]
    assert compute_shift(job, rng=random.Random(9)) == compute_shift(job, rng=random.Random(9))


# -- activities (beg / dig / search) ------------------------------------------


def test_beg_and_dig_tables_are_sane() -> None:
    for table in (BEG_TABLE, DIG_TABLE):
        for entry in table:
            assert 0 <= entry.min_value <= entry.max_value
            assert entry.weight > 0


def test_pick_search_options_are_distinct() -> None:
    for seed in range(50):
        options = pick_search_options(rng=random.Random(seed))
        assert len(options) == 3
        assert len({loc.id for loc in options}) == 3


def test_pick_search_options_caps_at_catalogue_size() -> None:
    options = pick_search_options(count=99, rng=random.Random(0))
    assert len(options) == len(SEARCH_LOCATIONS)


def test_resolve_search_safe_location_never_injures() -> None:
    couch = next(loc for loc in SEARCH_LOCATIONS if loc.injury_chance == 0.0)
    for seed in range(100):
        outcome = resolve_search(couch, rng=random.Random(seed))
        assert outcome.injured is False
        assert outcome.fine == 0
        assert outcome.catch is not None


def test_resolve_search_injury_fine_within_bounds() -> None:
    risky = max(SEARCH_LOCATIONS, key=lambda loc: loc.injury_chance)
    injured = 0
    for seed in range(300):
        outcome = resolve_search(risky, rng=random.Random(seed))
        if outcome.injured:
            injured += 1
            assert outcome.catch is None
            assert risky.injury_min <= outcome.fine <= risky.injury_max
            assert outcome.flavor == risky.injury_flavor
    assert injured > 0  # a 35% risk must fire at least once in 300 seeds


# -- pets ----------------------------------------------------------------------


def test_pet_catalogue_lookup() -> None:
    assert get_species('missingno') is None
    for species in PET_SPECIES:
        assert get_species(species.id) is species


def test_hunger_state_thresholds() -> None:
    assert hunger_state(NOW - datetime.timedelta(hours=1), now=NOW) is HungerState.FED
    assert hunger_state(NOW - datetime.timedelta(hours=20), now=NOW) is HungerState.HUNGRY
    assert hunger_state(NOW - datetime.timedelta(hours=40), now=NOW) is HungerState.STARVING


def test_pet_claim_accrues_hourly_rate() -> None:
    cat = get_species('cat')
    assert cat is not None
    claim = compute_pet_claim(cat, NOW - datetime.timedelta(hours=5), NOW, now=NOW)
    assert claim.hunger is HungerState.FED
    assert claim.amount == cat.hourly_rate * 5


def test_pet_claim_caps_at_storage_window() -> None:
    hamster = get_species('hamster')
    assert hamster is not None
    claim = compute_pet_claim(hamster, NOW - datetime.timedelta(days=10), NOW, now=NOW)
    assert claim.hours == hamster.storage_hours
    assert claim.amount == hamster.hourly_rate * hamster.storage_hours


def test_hungry_pet_earns_half_and_starving_none() -> None:
    dog = get_species('dog')
    assert dog is not None
    last_claim = NOW - datetime.timedelta(hours=4)
    hungry = compute_pet_claim(dog, last_claim, NOW - datetime.timedelta(hours=20), now=NOW)
    assert hungry.amount == int(dog.hourly_rate * 4 * 0.5)
    starving = compute_pet_claim(dog, last_claim, NOW - datetime.timedelta(hours=48), now=NOW)
    assert starving.amount == 0


# -- daily quests ----------------------------------------------------------------


def test_quest_board_is_deterministic() -> None:
    a = generate_daily_quests(1, 2, TODAY)
    b = generate_daily_quests(1, 2, TODAY)
    assert a == b


def test_quest_board_varies_by_member_and_day() -> None:
    base = generate_daily_quests(1, 2, TODAY)
    assert base != generate_daily_quests(1, 3, TODAY) or base != generate_daily_quests(
        1, 2, TODAY + datetime.timedelta(days=1)
    )


def test_quest_board_has_distinct_quests_with_valid_goals() -> None:
    specs = {spec.key: spec for spec in QUEST_POOL}
    board = generate_daily_quests(123, 456, TODAY)
    assert len(board) == 3
    assert len({quest.key for quest in board}) == 3
    for quest in board:
        spec = specs[quest.key]
        assert spec.goal_min <= quest.goal <= spec.goal_max
        assert quest.kind == spec.kind
        assert quest.reward > 0


def test_deposit_quest_goal_is_rounded() -> None:
    # Find a board containing the deposit quest; its goal must be a round hundred.
    for user_id in range(200):
        board = generate_daily_quests(1, user_id, TODAY)
        deposit = next((q for q in board if q.key == 'deposit_amount'), None)
        if deposit is not None:
            assert deposit.goal % 100 == 0
            return
    raise AssertionError('deposit quest never appeared in 200 boards')


def test_new_activity_quests_in_pool_and_format() -> None:
    kinds = {spec.kind for spec in QUEST_POOL}
    assert {'casino_win', 'postmeme', 'heist', 'fight_win'} <= kinds
    seen: set[str] = set()
    for user_id in range(300):
        for quest in generate_daily_quests(1, user_id, TODAY):
            seen.add(quest.kind)
            assert '{' not in quest.description
    assert {'casino_win', 'postmeme', 'heist', 'fight_win'} <= seen


# -- achievements -----------------------------------------------------------------


def test_achievement_lookup_matches_catalogue() -> None:
    for achievement in ACHIEVEMENTS:
        assert get_achievement(achievement.id) is achievement
    assert get_achievement('nonexistent') is None


def test_fresh_snapshot_earns_nothing() -> None:
    assert evaluate_achievements(EconomySnapshot()) == ()


def test_wealth_achievements_stack() -> None:
    earned = evaluate_achievements(EconomySnapshot(net_worth=1_000_000))
    assert {'pocket_money', 'entrepreneur', 'six_figures', 'millionaire'} <= set(earned)


def test_specific_achievement_triggers() -> None:
    assert 'first_shift' in evaluate_achievements(EconomySnapshot(shifts=1))
    assert 'top_of_the_ladder' in evaluate_achievements(EconomySnapshot(job_id=JOB_LADDER[-1].id))
    assert 'ascended' in evaluate_achievements(EconomySnapshot(prestige=1))
    assert 'pet_parent' in evaluate_achievements(EconomySnapshot(has_pet=True))
    assert 'quartermaster' in evaluate_achievements(EconomySnapshot(quests_completed=25))
    assert 'collector' in evaluate_achievements(EconomySnapshot(items_owned=50))
    assert 'dedicated' in evaluate_achievements(EconomySnapshot(daily_streak=7))


# -- prestige -----------------------------------------------------------------------


def test_prestige_requirement_scales_linearly() -> None:
    assert prestige_requirement(0) == PRESTIGE_BASE_REQUIREMENT
    assert prestige_requirement(3) == PRESTIGE_BASE_REQUIREMENT * 4


def test_prestige_multiplier_steps_and_caps() -> None:
    assert prestige_multiplier(0) == 1.0
    assert prestige_multiplier(1) == 1.0 + PRESTIGE_STEP
    assert prestige_multiplier(PRESTIGE_MAX_LEVEL + 5) == prestige_multiplier(PRESTIGE_MAX_LEVEL)
    assert prestige_multiplier(-3) == 1.0


# -- weekly / monthly claims -----------------------------------------------------------


def test_periodic_first_claim_succeeds() -> None:
    result = compute_periodic(None, now=NOW, cooldown=WEEKLY_COOLDOWN)
    assert result.claimed is True
    assert result.next_available is None


def test_periodic_claim_within_cooldown_is_refused() -> None:
    last = NOW - datetime.timedelta(days=2)
    result = compute_periodic(last, now=NOW, cooldown=WEEKLY_COOLDOWN)
    assert result.claimed is False
    assert result.next_available == last + WEEKLY_COOLDOWN


def test_periodic_claim_after_cooldown_succeeds() -> None:
    last = NOW - MONTHLY_COOLDOWN
    result = compute_periodic(last, now=NOW, cooldown=MONTHLY_COOLDOWN)
    assert result.claimed is True


# -- guild settings ---------------------------------------------------------------------


def test_settings_default_when_no_row() -> None:
    settings = GuildEconomySettings.from_record(None)
    assert settings.payout_multiplier == 1.0
    assert settings.rob_enabled is True
    assert settings.max_bet is None


def test_settings_built_from_record() -> None:
    settings = GuildEconomySettings.from_record(
        {'payout_multiplier': 2.5, 'rob_enabled': False, 'daily_base': 500, 'max_bet': 10_000}
    )
    assert settings.payout_multiplier == 2.5
    assert settings.rob_enabled is False
    assert settings.daily_base == 500
    assert settings.max_bet == 10_000


# -- rob shield item effect ---------------------------------------------------------------


def test_rob_shield_item_effect_validates() -> None:
    assert validate_item_effect('rob_shield', None, 60) is None
    assert validate_item_effect('rob_shield', None, None) is not None


# -- parse_amount / postmemes ------------------------------------------------


def test_parse_amount_keywords_and_numbers() -> None:
    from app.services.economy import parse_amount

    assert parse_amount('all', 1234) == parse_amount('MAX', 1234) == 1234
    assert parse_amount('half', 5) == 2
    assert parse_amount('1,500', 0) == 1500
    assert parse_amount('2k', 0) == 2000
    assert parse_amount('1.5m', 0) == 1_500_000
    for bad in ('', 'abc', '-5', '0', 'half', 'nan', 'inf', '1e999'):
        assert parse_amount(bad, 1) is None, bad


def test_meme_formats_resolve_like_search() -> None:
    from app.services.economy import MEME_FORMATS, pick_search_options, resolve_search

    rng = random.Random(4)
    assert len({m.id for m in MEME_FORMATS}) == len(MEME_FORMATS)
    for meme in pick_search_options(locations=MEME_FORMATS, rng=rng):
        outcome = resolve_search(meme, rng=rng)
        assert outcome.injured or (outcome.catch is not None and outcome.catch.amount >= 0)
    wholesome = next(m for m in MEME_FORMATS if m.id == 'wholesome')
    assert not any(resolve_search(wholesome, rng=rng).injured for _ in range(200))


def test_work_challenges_invariants() -> None:
    from app.services.economy import CHALLENGE_KINDS, generate_challenge
    from app.services.economy.minigames import _WORDS

    assert all(4 <= len(w) <= 8 for w in _WORDS) and len(_WORDS) >= 20
    seen = set()
    for seed in range(300):
        rng = random.Random(seed)
        c = generate_challenge(rng)
        seen.add(c.kind)
        assert c.answer in c.options
        assert len(c.options) == 4 and len(set(c.options)) == 4
        if c.kind == 'unscramble':
            shown = c.prompt.split('**')[1].lower()
            assert shown != c.answer and sorted(shown) == sorted(c.answer)
    assert seen == set(CHALLENGE_KINDS)


# -- bank space --------------------------------------------------------------


def test_bank_room_never_negative() -> None:
    from app.services.economy import BANK_SPACE_DEFAULT, bank_room

    assert bank_room(0, BANK_SPACE_DEFAULT) == 10_000
    assert bank_room(9_000, 10_000) == 1_000
    assert bank_room(12_000, 10_000) == 0


def test_roll_bank_space_in_range() -> None:
    from app.services.economy import BANK_SPACE_GROWTH, roll_bank_space

    rng = random.Random(1)
    assert all(BANK_SPACE_GROWTH[0] <= roll_bank_space(rng=rng) <= BANK_SPACE_GROWTH[1] for _ in range(200))


def test_bank_space_effect_validates_and_describes() -> None:
    from app.services.economy import describe_effect, get_catalogue_item

    assert validate_item_effect('bank_space', 5_000, None) is None
    assert validate_item_effect('bank_space', None, None) is not None
    assert validate_item_effect('bank_space', 0, None) is not None
    assert '5,000' in (describe_effect('bank_space', 5_000, None) or '')
    banknote = get_catalogue_item('banknote')
    assert banknote is not None and banknote.for_sale and banknote.price == 2_500
    assert validate_item_effect(banknote.effect, banknote.effect_value, None) is None


def test_settings_bank_cap_defaults_on() -> None:
    assert GuildEconomySettings.from_record(None).bank_cap is True
    row = {'payout_multiplier': 1.0, 'rob_enabled': True, 'daily_base': 250, 'max_bet': None}
    assert GuildEconomySettings.from_record(row).bank_cap is True
    assert GuildEconomySettings.from_record({**row, 'bank_cap': False}).bank_cap is False


def test_collection_summary_hides_undiscovered_drops() -> None:
    from app.services.economy import DROP_KEYS, collection_summary

    assert {g.activity for g in collection_summary([])} == {'fish', 'hunt', 'dig'}
    groups = collection_summary(['sardine', 'fishing_rod', 'nope'])  # tools / unknown keys never count
    assert sum(g.found for g in groups) == 1
    assert sum(g.total for g in groups) == len(DROP_KEYS)
    fish = next(g for g in groups if g.activity == 'fish')
    assert fish.entries[0] == ('\N{FISH} Sardine', True)
    assert fish.entries[1] == ('???', False)


def test_discovery_achievements() -> None:
    from app.services.economy import DROP_KEYS

    assert 'naturalist' not in evaluate_achievements(EconomySnapshot(discoveries=9))
    assert 'naturalist' in evaluate_achievements(EconomySnapshot(discoveries=10))
    assert 'completionist' not in evaluate_achievements(EconomySnapshot(discoveries=len(DROP_KEYS) - 1))
    assert 'completionist' in evaluate_achievements(EconomySnapshot(discoveries=len(DROP_KEYS)))


def test_best_game_and_match_achievement() -> None:
    from app.services.economy import best_game, match_achievement

    assert best_game([]) is None
    assert best_game([{'game': 'a', 'profit': -5}]) is None
    assert best_game([{'game': 'a', 'profit': 5}, {'game': 'b', 'profit': 9}])['game'] == 'b'  # type: ignore[index]
    first = ACHIEVEMENTS[0]
    assert match_achievement(first.id, ACHIEVEMENTS) is first
    assert match_achievement(first.name.upper(), ACHIEVEMENTS) is first
    assert match_achievement('zzzzqqqq', ACHIEVEMENTS) is None
    assert match_achievement(first.name, []) is None
