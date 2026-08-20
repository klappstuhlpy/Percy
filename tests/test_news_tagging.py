"""Tests for conservative news tagging (``app/services/news/tagging.py``).

Everything here is pure -- terms are hand-built from the shapes the catalogue produces and the
stories are plain strings. No database, no bot, no network.

The plan's §8 says a false tag is worse than a missed tag, so the rejection cases (the
``Lokishvili`` substring, the common-word subject, the two-letter brand) carry as much weight
as the matches and get named tests rather than being folded into one scenario.
"""

from __future__ import annotations

import pytest

from app.services.news import COMMON_WORDS, MIN_TERM_LENGTH, Tag, build_terms, compile_terms, tag, tags_as_rows
from app.services.releases import Subject

LOKI = build_terms('title', 'character', 'Loki', 'Loki', aliases=('God of Mischief',))
PASCAL = build_terms('title', 'person', 'pedro-pascal', 'Pedro Pascal')
SPIDER = build_terms('comic', 'series', 'amazing-spider-man', 'Amazing Spider-Man', aliases=('ASM',))


def only(tags: list[Tag]) -> Tag:
    assert len(tags) == 1, tags
    return tags[0]


# -- build_terms ----------------------------------------------------------

def test_build_terms_keeps_the_subject_key_not_the_display_name() -> None:
    """A ``person`` subject is keyed by slug; the *name* is only the surface form to match."""
    term = PASCAL[0]
    assert term.subject == Subject('title', 'person', 'pedro-pascal')
    assert term.tokens == ('pedro', 'pascal')


def test_build_terms_produces_one_term_per_alias_with_its_rule() -> None:
    assert [term.rule for term in LOKI] == ['exact', 'alias']
    assert [term.surface for term in LOKI] == ['loki', 'god of mischief']


@pytest.mark.parametrize('name', ['DC', 'X', 'ab'])
def test_build_terms_drops_short_single_tokens(name: str) -> None:
    """Rule 4: a two-letter brand would fire on half the English language."""
    assert len(name) < MIN_TERM_LENGTH
    assert build_terms('comic', 'brand', name, name) == []


def test_build_terms_drops_common_word_subjects() -> None:
    """``Storm`` is a real character *and* an ordinary word -- the tag is given up deliberately."""
    assert 'storm' in COMMON_WORDS
    assert build_terms('comic', 'character', 'Storm', 'Storm') == []


def test_build_terms_drops_all_digit_tokens() -> None:
    assert build_terms('title', 'franchise', '2049', '2049') == []


def test_a_common_word_is_still_reachable_as_a_multi_word_alias() -> None:
    """The escape hatch for rule 4: two adjacent tokens are specific enough to be safe."""
    terms = build_terms('comic', 'character', 'Storm', 'Storm', aliases=('Ororo Munroe',))
    assert [term.surface for term in terms] == ['ororo munroe']
    assert only(tag('Ororo Munroe joins the cast', None, terms)).rule == 'alias'


def test_build_terms_ignores_empty_surfaces() -> None:
    assert build_terms('comic', 'creator', 'Jane Doe', '', aliases=('', '   ')) == []


# -- matching -------------------------------------------------------------

def test_exact_match_on_the_headline() -> None:
    found = only(tag('Loki returns for a third season', None, LOKI))
    assert found.subject == Subject('title', 'character', 'loki')
    assert (found.rule, found.matched, found.field) == ('exact', 'loki', 'headline')


def test_alias_match_records_the_alias_rule() -> None:
    found = only(tag('The God of Mischief is back', None, LOKI))
    assert (found.rule, found.matched) == ('alias', 'god of mischief')


def test_word_boundary_rejects_a_substring_hit() -> None:
    """The case the whole design exists for: ``Loki`` must not fire on ``Lokishvili``."""
    assert tag('Nika Lokishvili signs on', 'Lokishvili was cast', LOKI) == []


def test_word_boundary_rejects_a_prefix_hit() -> None:
    assert tag('Lokis everywhere', None, LOKI) == []


def test_no_stemming_or_fuzzy_matching() -> None:
    """Rule 1: a variant is a curated alias or it is nothing."""
    assert tag('The Amazing Spider-Men assemble', None, SPIDER) == []


def test_case_and_spacing_are_normalised_not_slugified() -> None:
    found = only(tag('PEDRO   PASCAL joins the cast', None, PASCAL))
    assert found.subject.value == 'pedro-pascal'
    assert found.matched == 'pedro pascal'


def test_multi_word_subject_matches_across_a_possessive() -> None:
    assert only(tag("Pedro Pascal's next film lands", None, PASCAL)).rule == 'exact'


def test_multi_word_subject_requires_adjacent_tokens() -> None:
    assert tag('Pedro and Pascal are unrelated', None, PASCAL) == []


def test_punctuation_between_words_is_a_boundary_not_a_break() -> None:
    """``Spider-Man`` and ``Spider Man`` are one term: feeds disagree on the hyphen."""
    assert only(tag('Amazing Spider Man #1 announced', None, SPIDER)).rule == 'exact'
    assert only(tag('Amazing Spider-Man #1 announced', None, SPIDER)).rule == 'exact'


def test_excerpt_is_scanned_too() -> None:
    found = only(tag('A big week for Marvel', 'Loki appears in the finale', LOKI))
    assert found.field == 'excerpt'


def test_headline_provenance_wins_over_the_excerpt() -> None:
    """Rule 6: one tag per subject, and the stronger provenance is the one kept."""
    found = only(tag('Loki returns', 'Loki also appears here', LOKI))
    assert found.field == 'headline'


def test_exact_provenance_wins_over_an_alias() -> None:
    found = only(tag('Loki, the God of Mischief, returns', None, LOKI))
    assert (found.rule, found.field) == ('exact', 'headline')


def test_one_tag_per_subject_even_with_repeated_mentions() -> None:
    assert len(tag('Loki, Loki, Loki', 'Loki again', LOKI)) == 1


def test_several_subjects_are_tagged_independently() -> None:
    tags = tag('Pedro Pascal meets Loki', None, [*LOKI, *PASCAL])
    assert {(t.subject.type, t.subject.value) for t in tags} == {
        ('character', 'loki'), ('person', 'pedro-pascal')}


@pytest.mark.parametrize(('headline', 'excerpt'), [('', None), ('', ''), ('   ', '   ')])
def test_empty_input_tags_nothing(headline: str, excerpt: str | None) -> None:
    assert tag(headline, excerpt, LOKI) == []


def test_empty_catalogue_tags_nothing() -> None:
    assert tag('Loki returns', None, []) == []


def test_compiled_index_and_raw_terms_agree() -> None:
    index = compile_terms([*LOKI, *PASCAL])
    assert tag('Loki and Pedro Pascal', None, index) == tag('Loki and Pedro Pascal', None, [*LOKI, *PASCAL])


def test_tags_are_ordered_deterministically() -> None:
    tags = tag('Loki, Pedro Pascal and the Amazing Spider-Man', None, [*PASCAL, *SPIDER, *LOKI])
    assert [(t.subject.media, t.subject.type) for t in tags] == [
        ('comic', 'series'), ('title', 'character'), ('title', 'person')]


# -- provenance -----------------------------------------------------------

def test_tags_as_rows_carries_the_provenance_the_migration_stores() -> None:
    rows = tags_as_rows(tag('The God of Mischief returns', None, LOKI))
    assert rows == [('title', 'character', 'loki', 'alias', 'god of mischief')]
