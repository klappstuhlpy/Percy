from __future__ import annotations

from discord.ext import commands

__all__ = ("CommandDisabled", "ServiceUnavailableError")


class ServiceUnavailableError(commands.CommandError):
    """Raised when an external service is unavailable (API down, circuit breaker open)."""

    def __init__(self, service_name: str, retry_after: float | None = None) -> None:
        self.service_name = service_name
        self.retry_after = retry_after
        super().__init__(f"The {service_name} service is currently unavailable.")


class CommandDisabled(commands.CheckFailure):
    """Raised when a command that ``config`` disabled here is invoked and the server set a reply for it."""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)
