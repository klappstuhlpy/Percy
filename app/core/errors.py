from __future__ import annotations

import enum

from discord.ext import commands

__all__ = (
    "ErrorCategory",
    "ServiceUnavailableError",
    "categorize_error",
)


class ErrorCategory(enum.Enum):
    VALIDATION = "validation"
    PERMISSION = "permission"
    SERVICE_OUTAGE = "service_outage"
    INTERNAL = "internal"


class ServiceUnavailableError(commands.CommandError):
    """Raised when an external service is unavailable (API down, circuit breaker open)."""

    def __init__(self, service_name: str, retry_after: float | None = None) -> None:
        self.service_name = service_name
        self.retry_after = retry_after
        super().__init__(f"The {service_name} service is currently unavailable.")


def categorize_error(error: BaseException) -> ErrorCategory:
    """Classify an error into a user-facing category for appropriate messaging."""
    from app.clients.base import CircuitBreakerOpen, HTTPClientError

    if isinstance(error, (commands.MissingPermissions, commands.BotMissingPermissions, commands.CheckFailure)):
        return ErrorCategory.PERMISSION

    if isinstance(error, (
        commands.BadArgument,
        commands.MissingRequiredArgument,
        commands.TooManyArguments,
        commands.FlagError,
        commands.BadLiteralArgument,
        commands.BadUnionArgument,
    )):
        return ErrorCategory.VALIDATION

    if isinstance(error, (CircuitBreakerOpen, ServiceUnavailableError)):
        return ErrorCategory.SERVICE_OUTAGE

    if isinstance(error, HTTPClientError) and error.status >= 500:
        return ErrorCategory.SERVICE_OUTAGE

    return ErrorCategory.INTERNAL


ERROR_CATEGORY_HINTS: dict[ErrorCategory, str] = {
    ErrorCategory.VALIDATION: "Check your command input and try again.",
    ErrorCategory.PERMISSION: "You or the bot lack the required permissions for this action.",
    ErrorCategory.SERVICE_OUTAGE: "An external service is temporarily unavailable. Please try again shortly.",
    ErrorCategory.INTERNAL: "Something unexpected went wrong. This has been logged.",
}
