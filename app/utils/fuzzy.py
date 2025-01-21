"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""

# help with: http://chairnerd.seatgeek.com/fuzzywuzzy-fuzzy-string-matching-in-python/

from __future__ import annotations

import heapq
import platform
import re
import warnings
from typing import TYPE_CHECKING, Literal, TypeVar, overload

from . import checks

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Sequence

try:
    import Levenshtein.StringMatcher as SM
    SequenceMatcher = SM.StringMatcher
except ImportError:
    if platform.python_implementation() != 'PyPy':
        warnings.warn('Using slow pure-python SequenceMatcher. Install python-Levenshtein to remove this warning')
    from difflib import SequenceMatcher

T = TypeVar('T')

WORD_REGEX = re.compile(r'\W', re.IGNORECASE)


@checks.check_for_none
@checks.check_for_equivalence
@checks.check_empty_string
def ratio(string1: str, string2: str) -> int:
    """Return the ratio of the most similar substring"""
    string1, string2 = checks.make_type_consistent(string1, string2)

    m = SequenceMatcher(None, string1, string2)
    return checks.intr(100 * m.ratio())


@checks.check_for_none
@checks.check_for_equivalence
@checks.check_empty_string
def quick_ratio(string1: str, string2: str) -> int:
    """Return the `quick` ratio of the most similar substring"""
    string1, string2 = checks.make_type_consistent(string1, string2)

    m = SequenceMatcher(None, string1, string2)
    return int(round(100 * m.quick_ratio()))


@checks.check_for_none
@checks.check_for_equivalence
@checks.check_empty_string
def partial_ratio(string1: str, string2: str) -> int:
    """Return the ratio of the most similar substring
    as a number between 0 and 100."""
    string1, string2 = checks.make_type_consistent(string1, string2)

    if len(string1) <= len(string2):
        shorter = string1
        longer = string2
    else:
        shorter = string2
        longer = string1

    m = SequenceMatcher(None, shorter, longer)
    blocks = m.get_matching_blocks()

    scores = []
    for block in blocks:
        long_start = block[1] - block[0] if (block[1] - block[0]) > 0 else 0
        long_end = long_start + len(shorter)
        long_substr = longer[long_start:long_end]

        m2 = SequenceMatcher(None, shorter, long_substr)
        r = m2.ratio()
        if r > .995:
            return 100
        else:
            scores.append(r)

    return checks.intr(100 * max(scores))


def _sorted_tokens(a: str, b: str) -> tuple[str, str]:
    def _sort_tokens(t: str) -> str:
        t = WORD_REGEX.sub(' ', t).lower().strip()
        return ' '.join(sorted(t.split()))

    a = _sort_tokens(a)
    b = _sort_tokens(b)
    return a, b


def token_sort_ratio(a: str, b: str) -> int:
    return ratio(*_sorted_tokens(a, b))


def quick_token_sort_ratio(a: str, b: str) -> int:
    return quick_ratio(*_sorted_tokens(a, b))


def partial_token_sort_ratio(a: str, b: str) -> int:
    return partial_ratio(*_sorted_tokens(a, b))


@overload
def _extraction_generator(
        query: str,
        choices: Sequence[str],
        scorer: Callable[[str, str], int] = ...,
        score_cutoff: int = ...,
) -> Generator[tuple[str, int], None, None]:
    ...


@overload
def _extraction_generator(
        query: str,
        choices: dict[str, T],
        scorer: Callable[[str, str], int] = ...,
        score_cutoff: int = ...,
) -> Generator[tuple[str, int, T], None, None]:
    ...


def _extraction_generator(
        query: str,
        choices: Sequence[str] | dict[str, T],
        scorer: Callable[[str, str], int] = quick_ratio,
        score_cutoff: int = 0,
) -> Generator[tuple[str, int, T] | tuple[str, int], None, None]:
    if isinstance(choices, dict):
        for key, value in choices.items():
            score = scorer(query, key)
            if score >= score_cutoff:
                yield key, score, value
    else:
        for choice in choices:
            score = scorer(query, choice)
            if score >= score_cutoff:
                yield choice, score


@overload
def extract(
        query: str,
        choices: Sequence[str],
        *,
        scorer: Callable[[str, str], int] = ...,
        score_cutoff: int = ...,
        limit: int | None = ...,
) -> list[tuple[str, int]]:
    ...


@overload
def extract(
        query: str,
        choices: dict[str, T],
        *,
        scorer: Callable[[str, str], int] = ...,
        score_cutoff: int = ...,
        limit: int | None = ...,
) -> list[tuple[str, int, T]]:
    ...


def extract(
        query: str,
        choices: dict[str, T] | Sequence[str],
        *,
        scorer: Callable[[str, str], int] = quick_ratio,
        score_cutoff: int = 0,
        limit: int | None = 10,
) -> list[tuple[str, int]] | list[tuple[str, int, T]]:
    def key(t: tuple[str, int, T] | tuple[str, int]) -> int:
        return t[1]

    it = _extraction_generator(query, choices, scorer, score_cutoff)
    if limit is not None:
        return heapq.nlargest(limit, it, key=key)  # type: ignore
    return sorted(it, key=key, reverse=True)  # type: ignore


@overload
def extract_one(
        query: str,
        choices: Sequence[str],
        *,
        scorer: Callable[[str, str], int] = ...,
        score_cutoff: int = ...,
) -> tuple[str, int] | None:
    ...


@overload
def extract_one(
        query: str,
        choices: dict[str, T],
        *,
        scorer: Callable[[str, str], int] = ...,
        score_cutoff: int = ...,
) -> tuple[str, int, T] | None:
    ...


def extract_one(
        query: str,
        choices: dict[str, T] | Sequence[str],
        *,
        scorer: Callable[[str, str], int] = quick_ratio,
        score_cutoff: int = 0,
) -> tuple[str, int] | tuple[str, int, T] | None:
    def key(t: tuple[str, int, T] | tuple[str, int]) -> int:
        return t[1]

    it = _extraction_generator(query, choices, scorer, score_cutoff)
    try:
        return max(it, key=key)
    except ValueError:
        return None


@overload
def extract_or_exact(
        query: str,
        choices: Sequence[str],
        *,
        scorer: Callable[[str, str], int] = ...,
        score_cutoff: int = ...,
        limit: int | None = ...,
) -> list[tuple[str, int]]:
    ...


@overload
def extract_or_exact(
        query: str,
        choices: dict[str, T],
        *,
        scorer: Callable[[str, str], int] = ...,
        score_cutoff: int = ...,
        limit: int | None = ...,
) -> list[tuple[str, int, T]]:
    ...


def extract_or_exact(
        query: str,
        choices: dict[str, T] | Sequence[str],
        *,
        scorer: Callable[[str, str], int] = quick_ratio,
        score_cutoff: int = 0,
        limit: int | None = None,
) -> list[tuple[str, int]] | list[tuple[str, int, T]]:
    matches = extract(query, choices, scorer=scorer, score_cutoff=score_cutoff, limit=limit)
    if len(matches) == 0:
        return []

    if len(matches) == 1:
        return matches

    top = matches[0][1]
    second = matches[1][1]

    # check if the top one is exact or more than 30% more correct than the top
    if top == 100 or top > (second + 30):
        return [matches[0]]

    return matches


@overload
def extract_matches(
        query: str,
        choices: Sequence[str],
        *,
        scorer: Callable[[str, str], int] = ...,
        score_cutoff: int = ...,
) -> list[tuple[str, int]]:
    ...


@overload
def extract_matches(
        query: str,
        choices: dict[str, T],
        *,
        scorer: Callable[[str, str], int] = ...,
        score_cutoff: int = ...,
) -> list[tuple[str, int, T]]:
    ...


def extract_matches(
        query: str,
        choices: dict[str, T] | Sequence[str],
        *,
        scorer: Callable[[str, str], int] = quick_ratio,
        score_cutoff: int = 0,
) -> list[tuple[str, int]] | list[tuple[str, int, T]]:
    matches = extract(query, choices, scorer=scorer, score_cutoff=score_cutoff, limit=None)
    if len(matches) == 0:
        return []

    top_score = matches[0][1]
    to_return = []
    index = 0
    while True:
        try:
            match = matches[index]
        except IndexError:
            break
        else:
            index += 1

        if match[1] != top_score:
            break

        to_return.append(match)
    return to_return


@overload
def finder(
        text: str,
        collection: Iterable[T],
        *,
        key: Callable[[T], str] | None = ...,
        raw: Literal[True],
        limit: int | None = ...,
) -> list[tuple[int, int, T]]:
    ...


@overload
def finder(
        text: str,
        collection: Iterable[T],
        *,
        key: Callable[[T], str] | None = ...,
        raw: Literal[False],
        limit: int | None = ...,
) -> list[T]:
    ...


@overload
def finder(
        text: str,
        collection: Iterable[T],
        *,
        key: Callable[[T], str] | None = ...,
        raw: bool = ...,
        limit: int | None = ...,
) -> T:
    ...


def finder(
        text: str,
        collection: Iterable[T],
        *,
        key: Callable[[T], str] | None = None,
        raw: bool = False,
        limit: int | None = None,
) -> list[tuple[int, int, T]] | list[T] | T:
    suggestions: list[tuple[int, int, T]] = []
    text = str(text)
    pat = '.*?'.join(map(re.escape, text))
    regex = re.compile(pat, flags=re.IGNORECASE)
    for item in collection:
        if limit is not None and len(suggestions) >= limit:
            break

        to_search = key(item) if key else str(item)
        r = regex.search(to_search)
        if r:
            suggestions.append((len(r.group()), r.start(), item))

    def sort_key(tup: tuple[int, int, T]) -> tuple[int, int, str | T]:
        if key:
            return tup[0], tup[1], key(tup[2])
        return tup

    result = sorted(suggestions, key=sort_key) if raw else [z for _, _, z in sorted(suggestions, key=sort_key)]
    return result


def find(text: str, collection: Iterable[str], *, key: Callable[[str], str] | None = None) -> str | None:
    try:
        return finder(text, collection, key=key)[0]
    except IndexError:
        return None
