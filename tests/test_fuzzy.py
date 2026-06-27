"""Tests for the fuzzy string matching helpers in :mod:`app.utils.fuzzy`.

Scores depend on whether python-Levenshtein or the pure-python difflib backend
is used, so these tests assert on robust properties (identity, ordering, ranges)
rather than exact numeric scores.
"""

from app.utils import fuzzy


class TestOsaDistance:
    def test_identical_is_zero(self) -> None:
        assert fuzzy.osa_distance('ask', 'ask') == 0

    def test_case_insensitive(self) -> None:
        assert fuzzy.osa_distance('ASK', 'ask') == 0

    def test_adjacent_transposition_is_one(self) -> None:
        # The motivating case: "aks" -> "ask" is a single transposition (ratio rates it ~67).
        assert fuzzy.osa_distance('aks', 'ask') == 1

    def test_single_substitution_insertion_deletion(self) -> None:
        assert fuzzy.osa_distance('can', 'ban') == 1  # substitution
        assert fuzzy.osa_distance('bann', 'ban') == 1  # deletion
        assert fuzzy.osa_distance('bn', 'ban') == 1  # insertion

    def test_empty_strings(self) -> None:
        assert fuzzy.osa_distance('', 'ask') == 3
        assert fuzzy.osa_distance('ask', '') == 3
        assert fuzzy.osa_distance('', '') == 0

    def test_unrelated_words_exceed_one(self) -> None:
        assert fuzzy.osa_distance('aks', 'userinfo') > 1


class TestRatio:
    def test_identical_is_100(self) -> None:
        assert fuzzy.ratio('hello', 'hello') == 100

    def test_completely_different_is_low(self) -> None:
        assert fuzzy.ratio('abc', 'xyz') == 0

    def test_in_range(self) -> None:
        assert 0 <= fuzzy.ratio('hello', 'hallo') <= 100

    def test_closer_scores_higher(self) -> None:
        assert fuzzy.ratio('hello', 'hello!') > fuzzy.ratio('hello', 'world')


class TestQuickRatio:
    def test_identical_is_100(self) -> None:
        assert fuzzy.quick_ratio('hello', 'hello') == 100

    def test_in_range(self) -> None:
        assert 0 <= fuzzy.quick_ratio('kitten', 'sitting') <= 100


class TestPartialRatio:
    def test_substring_is_perfect(self) -> None:
        assert fuzzy.partial_ratio('hello', 'hello world') == 100

    def test_in_range(self) -> None:
        assert 0 <= fuzzy.partial_ratio('abc', 'xyz') <= 100


class TestTokenSortRatio:
    def test_reordered_tokens_match(self) -> None:
        assert fuzzy.token_sort_ratio('new york mets', 'mets new york') == 100

    def test_case_insensitive(self) -> None:
        assert fuzzy.token_sort_ratio('Hello World', 'world hello') == 100


class TestExtract:
    def test_returns_sorted_by_score(self) -> None:
        results = fuzzy.extract('app', ['apple', 'apply', 'banana', 'grape'])
        # Highest scoring match comes first.
        scores = [score for _, score in results]
        assert scores == sorted(scores, reverse=True)

    def test_respects_limit(self) -> None:
        results = fuzzy.extract('a', ['apple', 'apply', 'banana', 'grape'], limit=2)
        assert len(results) == 2

    def test_score_cutoff_filters(self) -> None:
        results = fuzzy.extract('zzzzz', ['apple', 'banana'], score_cutoff=50)
        assert results == []

    def test_dict_choices_return_values(self) -> None:
        results = fuzzy.extract('app', {'apple': 1, 'banana': 2})
        # Each tuple carries the mapped value as a third element.
        assert all(len(item) == 3 for item in results)


class TestExtractOne:
    def test_picks_best(self) -> None:
        match = fuzzy.extract_one('apple', ['apple', 'banana', 'grape'])
        assert match is not None
        assert match[0] == 'apple'
        assert match[1] == 100

    def test_empty_choices_returns_none(self) -> None:
        assert fuzzy.extract_one('apple', []) is None


class TestFinder:
    def test_finds_subsequence_matches(self) -> None:
        results = fuzzy.finder('ab', ['cab', 'aXb', 'xyz', 'ba'])
        assert 'aXb' in results
        assert 'cab' in results
        assert 'xyz' not in results
        assert 'ba' not in results

    def test_case_insensitive(self) -> None:
        assert 'ABC' in fuzzy.finder('abc', ['ABC', 'xyz'])

    def test_with_key(self) -> None:
        items = [{'name': 'apple'}, {'name': 'banana'}]
        results = fuzzy.finder('app', items, key=lambda d: d['name'])
        assert results == [{'name': 'apple'}]

    def test_respects_limit(self) -> None:
        results = fuzzy.finder('a', ['a1', 'a2', 'a3'], limit=2)
        assert len(results) == 2


class TestFind:
    def test_returns_best_single(self) -> None:
        assert fuzzy.find('app', ['banana', 'apple', 'grape']) == 'apple'

    def test_no_match_returns_none(self) -> None:
        assert fuzzy.find('zzz', ['apple', 'banana']) is None


class TestExtractOrExact:
    def test_exact_match_short_circuits(self) -> None:
        # A perfect (100) match should be returned alone.
        results = fuzzy.extract_or_exact('apple', ['apple', 'apples', 'applet'])
        assert len(results) == 1
        assert results[0][0] == 'apple'

    def test_single_choice(self) -> None:
        results = fuzzy.extract_or_exact('xyz', ['apple'])
        assert len(results) == 1
