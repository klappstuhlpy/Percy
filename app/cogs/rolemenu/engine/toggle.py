"""Pure role-menu toggle logic — no ``discord`` imports.

Given a member's current roles and the role they clicked, decide which roles to add and
remove. Handles "unique" (radio-style) menus where selecting one option clears the
others. Unit-tested without a bot instance.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ('RoleMenuUpdate', 'resolve_toggle')


@dataclass(frozen=True, slots=True)
class RoleMenuUpdate:
    """The set of role ids to add to and remove from a member."""

    add: tuple[int, ...]
    remove: tuple[int, ...]

    @property
    def is_noop(self) -> bool:
        return not self.add and not self.remove


def resolve_toggle(
    *,
    clicked_role: int,
    member_role_ids: Iterable[int],
    menu_role_ids: Iterable[int],
    unique: bool,
) -> RoleMenuUpdate:
    """Resolve a role-menu button press into role additions/removals.

    Parameters
    ----------
    clicked_role:
        The role id behind the pressed button.
    member_role_ids:
        The role ids the member currently has.
    menu_role_ids:
        All role ids offered by this menu.
    unique:
        When ``True``, the menu is radio-style: adding a role removes any other menu
        roles the member holds.

    Returns
    -------
    RoleMenuUpdate
        If the member already has the clicked role, it is removed (toggle off).
        Otherwise it is added; for a ``unique`` menu, the member's other menu roles are
        removed at the same time.
    """
    member_roles = set(member_role_ids)

    if clicked_role in member_roles:
        return RoleMenuUpdate(add=(), remove=(clicked_role,))

    remove: tuple[int, ...] = ()
    if unique:
        remove = tuple(
            role_id for role_id in menu_role_ids if role_id != clicked_role and role_id in member_roles
        )
    return RoleMenuUpdate(add=(clicked_role,), remove=remove)
