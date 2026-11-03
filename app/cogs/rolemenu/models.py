"""Role-menu types shared by the cog and the internal API."""

from __future__ import annotations


class RoleMenuError(Exception):
    """A role-menu operation was refused; the message is shown to the user as is."""
