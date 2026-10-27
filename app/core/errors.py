from __future__ import annotations

from discord.ext import commands

__all__ = ("ServiceUnavailableError",)


class ServiceUnavailableError(commands.CommandError):
    """Raised when an external service is unavailable (API down, circuit breaker open)."""

    def __init__(self, service_name: str, retry_after: float | None = None) -> None:
        self.service_name = service_name
        self.retry_after = retry_after
        super().__init__(f"The {service_name} service is currently unavailable.")
