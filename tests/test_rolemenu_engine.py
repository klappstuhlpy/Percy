"""Tests for :mod:`app.cogs.rolemenu.engine`."""

from __future__ import annotations

from app.cogs.rolemenu.engine import resolve_toggle


def test_adds_role_when_not_held() -> None:
    update = resolve_toggle(clicked_role=10, member_role_ids=[1, 2], menu_role_ids=[10, 11], unique=False)
    assert update.add == (10,)
    assert update.remove == ()


def test_removes_role_when_already_held() -> None:
    update = resolve_toggle(clicked_role=10, member_role_ids=[1, 10], menu_role_ids=[10, 11], unique=False)
    assert update.add == ()
    assert update.remove == (10,)


def test_non_unique_keeps_other_menu_roles() -> None:
    update = resolve_toggle(clicked_role=10, member_role_ids=[11], menu_role_ids=[10, 11, 12], unique=False)
    assert update.add == (10,)
    assert update.remove == ()


def test_unique_clears_other_held_menu_roles() -> None:
    update = resolve_toggle(clicked_role=10, member_role_ids=[11, 12, 99], menu_role_ids=[10, 11, 12], unique=True)
    assert update.add == (10,)
    # Only other *menu* roles the member holds are removed — never unrelated roles (99).
    assert set(update.remove) == {11, 12}
    assert 99 not in update.remove


def test_unique_toggle_off_still_just_removes_clicked() -> None:
    update = resolve_toggle(clicked_role=10, member_role_ids=[10, 11], menu_role_ids=[10, 11], unique=True)
    assert update.add == ()
    assert update.remove == (10,)


def test_unique_does_not_remove_clicked_role_from_remove_set() -> None:
    update = resolve_toggle(clicked_role=10, member_role_ids=[20], menu_role_ids=[10, 20], unique=True)
    assert update.add == (10,)
    assert update.remove == (20,)


def test_noop_helper_reflects_empty_update() -> None:
    held = resolve_toggle(clicked_role=10, member_role_ids=[10], menu_role_ids=[10], unique=False)
    assert held.is_noop is False  # removing the role is not a no-op
