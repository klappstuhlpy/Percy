"""Custom trivia list parser and answer matching."""

from __future__ import annotations

import json

import pytest

from app.services.trivia.lists import (
    MAX_ANSWER_LENGTH,
    MAX_ANSWERS,
    MAX_FILE_BYTES,
    MAX_QUESTION_LENGTH,
    MAX_QUESTIONS,
    TriviaListError,
    answer_matches,
    parse_list,
)


def _b(obj: object) -> bytes:
    return json.dumps(obj).encode()


def test_red_mapping_format() -> None:
    qs = parse_list(_b({'AUTHOR': 'x', 'Capital of France?': ['Paris', 'paris'], 'One?': 'uno', 'Num?': 4}))
    assert [(q.question, q.answers) for q in qs] == [
        ('Capital of France?', ('Paris', 'paris')), ('One?', ('uno',)), ('Num?', ('4',)),
    ]


def test_builtin_format_keeps_correct_only() -> None:
    qs = parse_list(_b([{'category': 'a', 'question': 'Q?', 'correct': 'Yes', 'incorrect': ['No']},
                        {'question': 'R?', 'answers': ['a', 'b']}]))
    assert qs[0].answers == ('Yes',) and qs[1].answers == ('a', 'b')


@pytest.mark.parametrize(('payload', 'needle'), [
    ({'q' * (MAX_QUESTION_LENGTH + 1): ['a']}, 'question is'),
    ({'q': ['a'] * (MAX_ANSWERS + 1)}, r'answers \(max'),
    ({'q': ['a' * (MAX_ANSWER_LENGTH + 1)]}, 'answer is'),
    ({f'q{i}': ['a'] for i in range(MAX_QUESTIONS + 1)}, r'questions \(max'),
    ({}, 'no questions'),
    ({'q': []}, 'at least one'),
    ({'q': ['']}, 'empty'),
    ({'q': [True]}, 'text or numbers'),
    ([1], 'Entry 1'),
    ('str', 'mapping'),
])
def test_limits_and_shapes_rejected(payload: object, needle: str) -> None:
    with pytest.raises(TriviaListError, match=needle):
        parse_list(_b(payload))


def test_limits_accept_boundaries() -> None:
    parse_list(_b({'q' * MAX_QUESTION_LENGTH: ['a' * MAX_ANSWER_LENGTH] * MAX_ANSWERS}))
    parse_list(_b({f'q{i}': ['a'] for i in range(MAX_QUESTIONS)}))


def test_file_size_and_encoding() -> None:
    with pytest.raises(TriviaListError, match='KB'):
        parse_list(b' ' * (MAX_FILE_BYTES + 1))
    with pytest.raises(TriviaListError, match='JSON'):
        parse_list(b'question:\n- answer\n')


def test_deep_nesting_is_a_list_error() -> None:
    with pytest.raises(TriviaListError, match='nested'):
        parse_list(b'[' * 100000)


def test_answer_matches() -> None:
    assert answer_matches('  PARIS ', ('paris',))
    assert answer_matches('new  york', ('New York',))
    assert not answer_matches('lyon', ('paris',))
