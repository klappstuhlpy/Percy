# -*- coding: utf-8 -*-

"""
This Source Code Form is subject to the terms of the Mozilla Public
License, v. 2.0. If a copy of the MPL was not distributed with this
file, You can obtain one at https://mozilla.org/MPL/2.0/.
"""

# help with: http://chairnerd.seatgeek.com/fuzzywuzzy-fuzzy-string-matching-in-python/

from __future__ import annotations

import platform
import re
import heapq
import warnings
from typing import Callable, Iterable, Literal, Optional, Sequence, TypeVar, Generator, overload


try:
    from .StringMatcher import StringMatcher as SequenceMatcher
except ImportError:
    if platform.python_implementation() != "PyPy":
        warnings.warn('Using slow pure-python SequenceMatcher. Install python-Levenshtein to remove this warning')
    from difflib import SequenceMatcher


from cogs.utils import checks

T = TypeVar('T')


@checks.check_for_none
@checks.check_for_equivalence
@checks.check_empty_string
def ratio(s1: str, s2: str) -> int:
    """Return the ratio of the most similar substring"""
    s1, s2 = checks.make_type_consistent(s1, s2)

    m = SequenceMatcher(None, s1, s2)
    return checks.intr(100 * m.ratio())


@checks.check_for_none
@checks.check_for_equivalence
@checks.check_empty_string
def quick_ratio(s1: str, s2: str) -> int:
    """Return the `quick` ratio of the most similar substring"""
    s1, s2 = checks.make_type_consistent(s1, s2)

    m = SequenceMatcher(None, s1, s2)
    return int(round(100 * m.quick_ratio()))


@checks.check_for_none
@checks.check_for_equivalence
@checks.check_empty_string
def partial_ratio(s1: str, s2: str) -> int:
    """Return the ratio of the most similar substring
    as a number between 0 and 100."""
    s1, s2 = checks.make_type_consistent(s1, s2)

    if len(s1) <= len(s2):
        shorter = s1
        longer = s2
    else:
        shorter = s2
        longer = s1

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


_word_regex = re.compile(r'\W', re.IGNORECASE)


def _sort_tokens(a: str) -> str:
    a = _word_regex.sub(' ', a).lower().strip()
    return ' '.join(sorted(a.split()))


def token_sort_ratio(a: str, b: str) -> int:
    a = _sort_tokens(a)
    b = _sort_tokens(b)
    return ratio(a, b)


def quick_token_sort_ratio(a: str, b: str) -> int:
    a = _sort_tokens(a)
    b = _sort_tokens(b)
    return quick_ratio(a, b)


def partial_token_sort_ratio(a: str, b: str) -> int:
    a = _sort_tokens(a)
    b = _sort_tokens(b)
    return partial_ratio(a, b)


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
    limit: Optional[int] = ...,
) -> list[tuple[str, int]]:
    ...


@overload
def extract(
    query: str,
    choices: dict[str, T],
    *,
    scorer: Callable[[str, str], int] = ...,
    score_cutoff: int = ...,
    limit: Optional[int] = ...,
) -> list[tuple[str, int, T]]:
    ...


def extract(
    query: str,
    choices: dict[str, T] | Sequence[str],
    *,
    scorer: Callable[[str, str], int] = quick_ratio,
    score_cutoff: int = 0,
    limit: Optional[int] = 10,
) -> list[tuple[str, int]] | list[tuple[str, int, T]]:
    it = _extraction_generator(query, choices, scorer, score_cutoff)
    key = lambda t: t[1]
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
) -> Optional[tuple[str, int]]:
    ...


@overload
def extract_one(
    query: str,
    choices: dict[str, T],
    *,
    scorer: Callable[[str, str], int] = ...,
    score_cutoff: int = ...,
) -> Optional[tuple[str, int, T]]:
    ...


def extract_one(
    query: str,
    choices: dict[str, T] | Sequence[str],
    *,
    scorer: Callable[[str, str], int] = quick_ratio,
    score_cutoff: int = 0,
) -> Optional[tuple[str, int]] | Optional[tuple[str, int, T]]:
    it = _extraction_generator(query, choices, scorer, score_cutoff)
    key = lambda t: t[1]
    try:
        return max(it, key=key)
    except:
        # iterator could return nothing
        return None


@overload
def extract_or_exact(
    query: str,
    choices: Sequence[str],
    *,
    scorer: Callable[[str, str], int] = ...,
    score_cutoff: int = ...,
    limit: Optional[int] = ...,
) -> list[tuple[str, int]]:
    ...


@overload
def extract_or_exact(
    query: str,
    choices: dict[str, T],
    *,
    scorer: Callable[[str, str], int] = ...,
    score_cutoff: int = ...,
    limit: Optional[int] = ...,
) -> list[tuple[str, int, T]]:
    ...


def extract_or_exact(
    query: str,
    choices: dict[str, T] | Sequence[str],
    *,
    scorer: Callable[[str, str], int] = quick_ratio,
    score_cutoff: int = 0,
    limit: Optional[int] = None,
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
        return [matches[0]]  # type: ignore

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
    key: Optional[Callable[[T], str]] = ...,
    raw: Literal[True],
) -> list[tuple[int, int, T]]:
    ...


@overload
def finder(
    text: str,
    collection: Iterable[T],
    *,
    key: Optional[Callable[[T], str]] = ...,
    raw: Literal[False],
) -> list[T]:
    ...


@overload
def finder(
    text: str,
    collection: Iterable[T],
    *,
    key: Optional[Callable[[T], str]] = ...,
    raw: bool = ...,
) -> list[T]:
    ...


def finder(
    text: str,
    collection: Iterable[T],
    *,
    key: Optional[Callable[[T], str]] = None,
    raw: bool = False,
) -> list[tuple[int, int, T]] | list[T]:
    suggestions: list[tuple[int, int, T]] = []
    text = str(text)
    pat = '.*?'.join(map(re.escape, text))
    regex = re.compile(pat, flags=re.IGNORECASE)
    for item in collection:
        to_search = key(item) if key else str(item)
        r = regex.search(to_search)
        if r:
            suggestions.append((len(r.group()), r.start(), item))

    def sort_key(tup: tuple[int, int, T]) -> tuple[int, int, str | T]:
        if key:
            return tup[0], tup[1], key(tup[2])
        return tup

    if raw:
        return sorted(suggestions, key=sort_key)
    else:
        return [z for _, _, z in sorted(suggestions, key=sort_key)]


def find(text: str, collection: Iterable[str], *, key: Optional[Callable[[str], str]] = None) -> Optional[str]:
    try:
        return finder(text, collection, key=key)[0]
    except IndexError:
        return None
