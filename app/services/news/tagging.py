"""News tagging: a headline and excerpt -> the catalogue subject keys the story is about.

Pure: no database, no network, no discord.py. The caller hands in the catalogue's known
surface forms (:class:`Term`) and gets back :class:`Tag` objects carrying a
:class:`~app.services.releases.subjects.Subject` -- the *same* key a subscription is expressed
in -- so news and releases fan out through one matcher and one delivery table.

The tagging rules, and why each one exists
------------------------------------------
The plan's §8 states the trade-off outright: **a false tag notifies the wrong people, which is
worse than a missed tag.** Every rule below is that sentence applied.

1. **Exact and known-alias matches only.** No fuzzy matching, no stemming, no prefix matching.
   ``"Avengers"`` does not match ``"Avenger"`` and never will; if a source consistently uses a
   variant, that variant becomes an *alias* -- a curated fact -- rather than a similarity
   score nobody can audit.
2. **Word-boundary matching, never substring.** Text and terms are both reduced to token
   sequences (runs of letters and digits) and a term matches only as a whole run of tokens.
   This is what stops ``"Loki"`` firing on ``"Lokishvili"`` and ``"DC"`` on half the English
   language: ``loki`` is one token in the term and ``lokishvili`` is one token in the text, and
   they are simply not equal.
3. **Multi-word subjects are token n-grams**, matched in order and adjacently: ``"pedro
   pascal"`` matches ``"Pedro Pascal's next film"`` (the possessive tokenises away) but not
   ``"Pedro and Pascal"``. Because tokenisation drops punctuation, ``"Spider-Man"`` and
   ``"Spider Man"`` are the same term -- which is deliberate, since feeds disagree on the
   hyphen -- and by the same token a hyphen is a word boundary, so ``"Man"`` *would* match
   inside ``"Spider-Man"`` were it not for rule 4.
4. **A single-token term must be at least :data:`MIN_TERM_LENGTH` (4) characters, must not be
   all digits, and must not be a common English word** (:data:`COMMON_WORDS`). This is the
   rule that costs us real matches and is kept anyway: ``Storm``, ``Vision``, ``Cable`` and
   ``Flash`` are all Marvel/DC characters *and* ordinary English words, and a unigram rule for
   them would tag every story containing the word. They are reachable through a multi-token
   alias instead (``"the flash"``, ``"ororo munroe"``), which is specific enough to be safe.
   Multi-token terms bypass both checks -- two adjacent tokens are already specific.
5. **Case and spacing are handled by** :func:`~app.services.releases.subjects.normalise`, the
   same function the subscription tables are written with: casefold, trim, collapse inner
   whitespace. Not slugify -- a tag whose ``subject_value`` does not compare equal to the
   subscription it should match notifies nobody.
6. **One tag per subject**, with the strongest provenance kept: an ``exact`` match beats an
   ``alias`` one, and a headline hit beats an excerpt hit. Nothing downstream can act on two
   contradictory tags for one subject, so the ambiguity is resolved here.
7. **Every tag records which rule fired and the surface form that fired it** (``rule`` and
   ``matched``), which is what makes a bad rule revocable without deleting the story --
   see ``migrations/V43__news.sql``.

Failure modes deliberately rejected: substring/``in`` matching (rule 2), fuzzy or trigram
similarity and stemming (rule 1), matching a subject's *slug* against prose (a slug is an
identifier, not a surface form -- the caller passes display names as terms and the slug rides
along in the :class:`Subject`), and inferring a subject from a co-occurring one ("this mentions
the MCU so tag every MCU actor") which would multiply one weak signal into dozens of wrong DMs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.releases.subjects import Subject, normalise

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

__all__ = (
    'COMMON_WORDS',
    'MIN_TERM_LENGTH',
    'RULES',
    'Tag',
    'Term',
    'TermIndex',
    'build_terms',
    'compile_terms',
    'tag',
    'tags_as_rows',
)

#: Provenance values, strongest first. Stored in ``news_subjects.rule``.
RULES = ('exact', 'alias')

#: Shortest single-token term allowed to match. Four characters keeps ``Loki``, ``Thor`` and
#: ``Hulk`` while dropping ``DC``, ``X`` and every two-letter initialism a headline is full of.
MIN_TERM_LENGTH = 4

#: Single-token terms that are also ordinary English words, and so may never match on their
#: own (rule 4). Two groups: function/filler words a headline is built from, and the genuine
#: character and series names that collide with them. Extend it when a bad tag is reported --
#: that is cheaper than every subscriber of a common-word subject being wrong once a day.
COMMON_WORDS = frozenset({
    # function and filler words
    'about', 'after', 'again', 'against', 'already', 'also', 'another', 'because', 'been',
    'before', 'being', 'between', 'both', 'came', 'cannot', 'come', 'could', 'does', 'done',
    'down', 'during', 'each', 'even', 'ever', 'every', 'from', 'have', 'here', 'into', 'just',
    'like', 'made', 'make', 'many', 'more', 'most', 'much', 'must', 'need', 'next', 'once',
    'only', 'other', 'over', 'said', 'same', 'some', 'such', 'take', 'than', 'that', 'their',
    'them', 'then', 'there', 'these', 'they', 'this', 'those', 'through', 'very', 'want',
    'well', 'were', 'what', 'when', 'where', 'which', 'while', 'will', 'with', 'would', 'your',
    # entertainment-desk vocabulary: every third headline contains one
    'best', 'book', 'books', 'cast', 'comic', 'comics', 'film', 'films', 'first', 'issue',
    'movie', 'movies', 'news', 'part', 'role', 'season', 'series', 'show', 'shows', 'star',
    'stars', 'story', 'team', 'time', 'trailer', 'week', 'year', 'years',
    # subject names that are also common words -- reachable via a multi-token alias instead
    'angel', 'blade', 'cable', 'chase', 'domino', 'echo', 'flash', 'forge', 'hunter', 'legend',
    'legends', 'mirage', 'quake', 'rogue', 'shade', 'shadow', 'siren', 'storm', 'vision',
    'wasp', 'world',
})

#: A token is a run of letters or digits. Everything else -- punctuation, hyphens, apostrophes,
#: the em dashes a headline is littered with -- is a boundary, which is exactly rule 2.
_TOKEN_RE = re.compile(r'[^\W_]+')


@dataclass(frozen=True, slots=True)
class Term:
    """One surface form the catalogue can be recognised by.

    ``subject`` is what gets stored if this fires; ``tokens`` is the normalised token sequence
    actually compared against the text; ``rule`` records why this form is known (its canonical
    name, or a curated alias). Build these with :func:`build_terms` rather than by hand.
    """

    subject: Subject
    tokens: tuple[str, ...]
    rule: str
    surface: str

    @property
    def usable(self) -> bool:
        """Whether this term is specific enough to match on (rules 4)."""
        if not self.tokens:
            return False
        if len(self.tokens) > 1:
            return True
        token = self.tokens[0]
        return len(token) >= MIN_TERM_LENGTH and not token.isdigit() and token not in COMMON_WORDS


@dataclass(frozen=True, slots=True)
class Tag:
    """One subject a story was matched to, with the provenance that produced it.

    ``matched`` is the normalised surface form that fired and ``field`` where it fired, so a
    bad tag can be traced to the exact term and revoked (``news_subjects.matched_term``).
    """

    subject: Subject
    rule: str
    matched: str
    field: str


@dataclass(frozen=True, slots=True)
class TermIndex:
    """Terms compiled into an n-gram lookup, so tagging a batch pays for it once.

    A story is tagged by walking its tokens, not by testing every term -- an ingest run tags a
    few hundred items against a catalogue of thousands of subjects, and term-by-term scanning
    is the shape that gets slow first.
    """

    by_tokens: dict[tuple[str, ...], tuple[Term, ...]]
    max_tokens: int

    def __bool__(self) -> bool:
        return bool(self.by_tokens)


def _tokens(text: str) -> tuple[str, ...]:
    """Normalises then tokenises text into a tuple of letter/digit runs."""
    return tuple(_TOKEN_RE.findall(normalise(text)))


def build_terms(
    media: str,
    subject_type: str,
    value: str,
    name: str,
    aliases: Iterable[str] = (),
) -> list[Term]:
    """Builds the terms for one catalogue subject.

    ``value`` is the subject *key* stored on a subscription -- a slug for ``series`` /
    ``universe`` / ``person``, a name otherwise -- while ``name`` is the display form a
    headline would actually contain. They differ for exactly the types where the key is a slug,
    which is why both are arguments: matching on a slug would never fire, and storing a name
    where a slug belongs would never match a subscription.

    Terms that fail rule 4 are dropped here, so a caller never has to check.
    """
    subject = Subject.of(media, subject_type, value)
    candidates = [(name, 'exact'), *((alias, 'alias') for alias in aliases)]
    terms = [
        Term(subject=subject, tokens=_tokens(surface), rule=rule, surface=normalise(surface))
        for surface, rule in candidates
        if surface
    ]
    return [term for term in terms if term.usable]


def compile_terms(terms: Iterable[Term]) -> TermIndex:
    """Compiles terms into a :class:`TermIndex`, dropping any that fail rule 4."""
    by_tokens: dict[tuple[str, ...], list[Term]] = {}
    longest = 0
    for term in terms:
        if not term.usable:
            continue
        by_tokens.setdefault(term.tokens, []).append(term)
        longest = max(longest, len(term.tokens))
    return TermIndex(by_tokens={key: tuple(value) for key, value in by_tokens.items()}, max_tokens=longest)


def _rank(tag_: Tag) -> tuple[int, int]:
    """Sort key deciding which of two tags for one subject survives: rule, then field."""
    rule = RULES.index(tag_.rule) if tag_.rule in RULES else len(RULES)
    return (rule, 0 if tag_.field == 'headline' else 1)


def _scan(text: str, index: TermIndex, field_name: str) -> list[Tag]:
    """Yields a tag for every term whose token sequence occurs in ``text``."""
    tokens = _tokens(text)
    found: list[Tag] = []
    for start in range(len(tokens)):
        for size in range(1, min(index.max_tokens, len(tokens) - start) + 1):
            found.extend(
                Tag(subject=term.subject, rule=term.rule, matched=term.surface, field=field_name)
                for term in index.by_tokens.get(tokens[start:start + size], ())
            )
    return found


def tag(headline: str, excerpt: str | None, terms: TermIndex | Iterable[Term]) -> list[Tag]:
    """Matches a story against the catalogue, returning at most one tag per subject.

    The headline is scanned before the excerpt so that a subject named in both keeps the
    headline as its provenance (rule 6). Empty input, or a catalogue with no usable terms,
    returns ``[]`` -- an untagged story is stored and simply reaches nobody, which is the
    correct failure direction for §8.
    """
    index = terms if isinstance(terms, TermIndex) else compile_terms(terms)
    if not index:
        return []

    best: dict[Subject, Tag] = {}
    for found in (*_scan(headline or '', index, 'headline'), *_scan(excerpt or '', index, 'excerpt')):
        current = best.get(found.subject)
        if current is None or _rank(found) < _rank(current):
            best[found.subject] = found
    return sorted(best.values(), key=lambda item: (item.subject.media, item.subject.type, item.subject.value))


def tags_as_rows(tags: Sequence[Tag]) -> list[tuple[str, str, str, str, str]]:
    """Flattens tags into the ``news_subjects`` tuple the repository writes.

    ``(media, subject_type, subject_value, rule, matched_term)`` -- the shape
    :meth:`~app.database.repositories.news.NewsRepository.replace_subjects` takes.
    """
    return [(t.subject.media, t.subject.type, t.subject.value, t.rule, t.matched) for t in tags]
