"""Parser and answer matching for custom trivia lists.

Accepted JSON shapes (PyYAML is not a dependency, so Red's YAML files must be converted to JSON):

* Red's mapping: ``{"question": ["answer", "answer"]}`` (a bare string answer is allowed;
  Red's ``AUTHOR`` / ``CONFIG`` metadata keys are ignored).
* The built-in shape: ``[{"question": ..., "correct": ..., "incorrect": [...]}]``; only the correct
  answer is kept, since custom rounds are free-text. ``[{"question": ..., "answers": [...]}]`` works too.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

NAME_RE = re.compile(r'[a-z0-9_-]{1,32}')

__all__ = (
    'MAX_ANSWERS',
    'MAX_ANSWER_LENGTH',
    'MAX_FILE_BYTES',
    'MAX_QUESTIONS',
    'MAX_QUESTION_LENGTH',
    'NAME_RE',
    'CustomQuestion',
    'TriviaListError',
    'answer_matches',
    'parse_list',
)

MAX_FILE_BYTES = 256 * 1024
MAX_QUESTIONS = 500
MAX_QUESTION_LENGTH = 300
MAX_ANSWERS = 10
MAX_ANSWER_LENGTH = 100
_RED_METADATA = frozenset({'AUTHOR', 'CONFIG'})


class TriviaListError(ValueError):
    """The uploaded list is invalid; the message is shown to the uploader."""


@dataclass(frozen=True)
class CustomQuestion:
    question: str
    answers: tuple[str, ...]


def _question(index: int, question: object, answers: object) -> CustomQuestion:
    where = f'Question {index}'
    if not isinstance(question, str) or not question.strip():
        raise TriviaListError(f'{where}: the question must be non-empty text.')
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        raise TriviaListError(f'{where}: the question is {len(question)} characters (max {MAX_QUESTION_LENGTH}).')
    if isinstance(answers, (str, int, float)) and not isinstance(answers, bool):
        answers = [answers]
    if not isinstance(answers, list) or not answers:
        raise TriviaListError(f'{where}: needs at least one answer.')
    if len(answers) > MAX_ANSWERS:
        raise TriviaListError(f'{where}: has {len(answers)} answers (max {MAX_ANSWERS}).')
    cleaned: list[str] = []
    for answer in answers:
        if isinstance(answer, bool) or not isinstance(answer, (str, int, float)):
            raise TriviaListError(f'{where}: answers must be text or numbers.')
        text = str(answer).strip()
        if not text:
            raise TriviaListError(f'{where}: answers cannot be empty.')
        if len(text) > MAX_ANSWER_LENGTH:
            raise TriviaListError(f'{where}: an answer is {len(text)} characters (max {MAX_ANSWER_LENGTH}).')
        cleaned.append(text)
    return CustomQuestion(question, tuple(cleaned))


def parse_list(data: bytes) -> list[CustomQuestion]:
    """Validate and parse an uploaded list file; raises :class:`TriviaListError` with a precise reason."""
    if len(data) > MAX_FILE_BYTES:
        raise TriviaListError(f'The file is {len(data) // 1024} KB (max {MAX_FILE_BYTES // 1024} KB).')
    try:
        payload = json.loads(data.decode('utf-8-sig'))
    except RecursionError as exc:
        raise TriviaListError('The file is nested too deeply.') from exc
    except (UnicodeDecodeError, ValueError) as exc:
        raise TriviaListError(f'The file is not valid UTF-8 JSON ({exc}). YAML is not supported.') from exc

    pairs: list[tuple[object, object]]
    if isinstance(payload, dict):
        pairs = [(q, a) for q, a in payload.items() if q not in _RED_METADATA]
    elif isinstance(payload, list):
        pairs = []
        for i, item in enumerate(payload, 1):
            if not isinstance(item, dict) or 'question' not in item:
                raise TriviaListError(f'Entry {i}: expected an object with a "question" key.')
            answers = item['answers'] if 'answers' in item else item.get('correct')
            pairs.append((item['question'], answers))
    else:
        raise TriviaListError('The file must be a mapping of question to answers, or a list of question objects.')

    if not pairs:
        raise TriviaListError('The list has no questions.')
    if len(pairs) > MAX_QUESTIONS:
        raise TriviaListError(f'The list has {len(pairs)} questions (max {MAX_QUESTIONS}).')
    return [_question(i, q, a) for i, (q, a) in enumerate(pairs, 1)]


def answer_matches(guess: str, answers: tuple[str, ...] | list[str]) -> bool:
    """Case- and whitespace-insensitive match against any accepted answer."""
    norm = ' '.join(guess.casefold().split())
    return any(norm == ' '.join(a.casefold().split()) for a in answers)
