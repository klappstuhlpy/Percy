from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

__all__ = ("FeatureFlags",)

log = logging.getLogger(__name__)


class FeatureFlags:
    """Runtime feature flag registry for dynamically enabling/disabling commands and cogs.

    Commands or cogs can be disabled at runtime without redeployment. The check is integrated
    into the bot's command invocation path.

    Flags are stored in-memory (volatile across restarts). Persistent storage can be added
    by serializing to the database on change and loading on startup.
    """

    def __init__(self) -> None:
        self._disabled_commands: set[str] = set()
        self._disabled_cogs: set[str] = set()

    def disable_command(self, qualified_name: str) -> None:
        """Disable a command by its qualified name (e.g. 'leveling config')."""
        self._disabled_commands.add(qualified_name)
        log.info("Feature flag: disabled command %r", qualified_name)

    def enable_command(self, qualified_name: str) -> None:
        """Re-enable a previously disabled command."""
        self._disabled_commands.discard(qualified_name)
        log.info("Feature flag: enabled command %r", qualified_name)

    def disable_cog(self, cog_name: str) -> None:
        """Disable all commands in a cog by its qualified name."""
        self._disabled_cogs.add(cog_name)
        log.info("Feature flag: disabled cog %r", cog_name)

    def enable_cog(self, cog_name: str) -> None:
        """Re-enable a previously disabled cog."""
        self._disabled_cogs.discard(cog_name)
        log.info("Feature flag: enabled cog %r", cog_name)

    def is_command_disabled(self, qualified_name: str) -> bool:
        """Check if a specific command is disabled."""
        return qualified_name in self._disabled_commands

    def is_cog_disabled(self, cog_name: str) -> bool:
        """Check if a cog is disabled."""
        return cog_name in self._disabled_cogs

    def is_disabled(self, qualified_name: str, cog_name: str | None = None) -> bool:
        """Check if a command is disabled either directly or via its cog."""
        if qualified_name in self._disabled_commands:
            return True
        if cog_name and cog_name in self._disabled_cogs:
            return True
        return False

    @property
    def disabled_commands(self) -> set[str]:
        return set(self._disabled_commands)

    @property
    def disabled_cogs(self) -> set[str]:
        return set(self._disabled_cogs)

    def status(self) -> dict[str, list[str]]:
        """Return the current state for API/dashboard consumption."""
        return {
            "disabled_commands": sorted(self._disabled_commands),
            "disabled_cogs": sorted(self._disabled_cogs),
        }
