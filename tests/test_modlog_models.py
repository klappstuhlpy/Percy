"""Tests for :mod:`app.cogs.modlog.models`."""

from __future__ import annotations

import pytest

from app.cogs.modlog.models import CaseType, summarize_case_counts

# -- CaseType --------------------------------------------------------------


def test_from_action_round_trips_known_actions() -> None:
    for case_type in CaseType:
        assert CaseType.from_action(case_type.value) is case_type


def test_from_action_returns_none_for_unknown() -> None:
    assert CaseType.from_action('flagged') is None
    assert CaseType.from_action('') is None


def test_every_case_type_has_display_metadata() -> None:
    for case_type in CaseType:
        assert case_type.label
        assert isinstance(case_type.colour, int)
        assert case_type.emoji


# -- summarize_case_counts -------------------------------------------------


def test_summary_of_empty_history() -> None:
    assert summarize_case_counts([]) == 'no cases'


def test_summary_singular_and_plural() -> None:
    assert summarize_case_counts(['warn']) == '1 warn'
    assert summarize_case_counts(['warn', 'warn']) == '2 warns'


def test_summary_orders_by_case_type_declaration() -> None:
    # ban precedes kick in the enum, so it should come first regardless of input order.
    summary = summarize_case_counts(['kick', 'ban', 'warn', 'ban'])
    assert summary == '1 warn, 2 bans, 1 kick'


def test_summary_ignores_unknown_actions() -> None:
    assert summarize_case_counts(['warn', 'mystery']) == '1 warn'


@pytest.mark.parametrize('action', [ct.value for ct in CaseType])
def test_summary_counts_each_known_action(action: str) -> None:
    assert summarize_case_counts([action]).endswith(CaseType(action).label.lower())
