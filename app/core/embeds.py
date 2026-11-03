from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self, override

import discord

from app.utils import helpers

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime

__all__ = ("EmbedBuilder",)

type FieldSpec = tuple[str, str, bool] | Mapping[str, Any]


class EmbedBuilder(discord.Embed):
    """A :class:`discord.Embed` that defaults to white and accepts its fields up front.

    ``fields`` takes ``(name, value, inline)`` tuples or ``{"name", "value", "inline"}`` mappings
    (``inline`` optional, default ``True``), in any iterable — generators included.
    """

    @override
    def __init__(
        self,
        *,
        colour: helpers.Colour | int | None = helpers.Colour.white(),
        timestamp: datetime | None = None,
        fields: Iterable[FieldSpec] = (),
        **kwargs: Any,
    ) -> None:
        super().__init__(colour=colour, timestamp=timestamp, **kwargs)
        self.add_fields(fields)

    def add_fields(self, fields: Iterable[FieldSpec]) -> Self:
        """Add every field in ``fields``; chainable."""
        for field in fields:
            if isinstance(field, tuple):
                name, value, inline = field
            else:
                name, value, inline = field["name"], field["value"], field.get("inline", True)
            self.add_field(name=str(name), value=str(value), inline=bool(inline))
        return self
