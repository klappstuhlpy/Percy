from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, override

import discord

from app.utils import helpers

if TYPE_CHECKING:
    from collections.abc import Iterable
    from datetime import datetime
    from typing import Self

    from app.core.context import Context

__all__ = (
    'EmbedBuilder',
)


class EmbedBuilder(discord.Embed):
    """A subclass of :class:`discord.Embed` that adds a few more features to it.

    This is used to provide a more fluent interface for creating embeds.
    """

    @override
    def __init__(
            self,
            *,
            colour: helpers.Colour | int | None = helpers.Colour.white(),
            timestamp: datetime | None = None,
            fields: Iterable[tuple[str, str, bool]] | list[dict[str, str | bool]] = (),
            **kwargs: Any,
    ) -> None:
        super().__init__(colour=colour, timestamp=timestamp, **kwargs)
        if fields:
            self.add_fields(fields)

        if 'description' in kwargs:
            self.description = kwargs['description']

    @staticmethod
    def _resolve_field_dicts(
            fields: Iterable[tuple[str, str, bool]] | list[dict[str, str | bool]]
    ) -> Iterable[tuple[str, str, bool]]:
        first_item_checker = type(next(iter(fields), None))
        if first_item_checker is dict:
            dict_fields: list[dict[str, str | bool]] = fields
            return [(str(f['name']), str(f['value']), bool(f['inline'])) for f in dict_fields]
        return fields  # type: ignore[return-value]

    def add_fields(self, fields: Iterable[tuple[str, str, bool]] | list[dict[str, str | bool]]) -> EmbedBuilder:
        """Adds multiple fields to the embed.

        Parameters
        ----------
        fields: tuple[str, str, bool]
            The fields to add to the embed.

        Returns
        -------
        `EmbedBuilder`
            The embed builder.
        """
        for name, value, inline in self._resolve_field_dicts(fields):
            self.add_field(name=name, value=value, inline=inline)
        return self

    @classmethod
    def to_factory(cls, embed: discord.Embed, **kwargs: Any) -> Self:
        """Create a new embed from an existing embed.

        Parameters
        ----------
        embed: `discord.Embed`
            The embed to copy from.
        **kwargs: `Any`
            Additional keyword arguments to pass to the embed builder.

        Returns
        -------
        `EmbedBuilder`
            The new embed builder.
        """
        copied_embed = copy.copy(embed)
        if copied_embed.colour is not None:
            copied_embed.colour = helpers.Colour(copied_embed.colour.value)

        return cls.from_dict(copied_embed.to_dict(), **kwargs)  # type: ignore[arg-type]

    @classmethod
    def from_message(
            cls,
            message: discord.Message,
            **kwargs: Any,
    ) -> Self:
        """Create a new embed from a message.

        Parameters
        ----------
        message: `discord.Message`
            The message to create the embed from.
        **kwargs: `Any`
            Additional keyword arguments to pass to the embed builder.

        Returns
        -------
        `EmbedBuilder`
            The new embed builder.
        """
        if embeds := message.embeds:
            return cls.to_factory(embeds[0], **kwargs)

        author: discord.User | discord.Member = message.author
        instance = cls(**kwargs)

        instance.description = message.content
        instance.set_author(name=author.display_name, icon_url=author.display_avatar)

        if (
                message.attachments
                and message.attachments[0].content_type
                and message.attachments[0].content_type.startswith("image")
        ):
            instance.set_image(url=message.attachments[0].url)

        return instance

    @classmethod
    def factory(cls, ctx: Context | discord.Interaction) -> Self:
        """Factory function to create an embed instance from a context or interaction.

        Parameters
        ----------
        ctx: `Context` | `discord.Interaction`
            The context or interaction to create the embed from.

        Returns
        -------
        `EmbedBuilder`
            The new embed builder.
        """
        if isinstance(ctx, discord.Interaction):
            _origin = ctx.message.embeds[0] if ctx.message and ctx.message.embeds else None
        else:
            if ctx.is_interaction and ctx.interaction and ctx.interaction.message:
                _origin = ctx.interaction.message.embeds[0] if ctx.interaction.message.embeds else None
            else:
                _origin = ctx.message.embeds[0] if ctx.message.embeds else None

        if _origin:
            return cls.to_factory(_origin)
        return cls()

    def build(self) -> Self:
        """Returns a shallow copy of the embed.

        Returns
        -------
        `EmbedBuilder`
            The shallow copy of the embed.
        """
        return copy.copy(self)
