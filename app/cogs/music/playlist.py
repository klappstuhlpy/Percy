from __future__ import annotations

from discord.ext import commands

from app.core import Context


class PlaylistNameOrID(commands.clean_content):
    """Converts the content to either an integer or string."""

    def __init__(self, *, lower: bool = False, with_id: bool = False) -> None:
        self.lower: bool = lower
        self.with_id: bool = with_id
        super().__init__()

    async def convert(self, ctx: Context, argument: str) -> str | int:
        converted = await super().convert(ctx, argument)
        lower = converted.lower().strip()

        if not lower:
            raise commands.BadArgument("Please enter a valid playlist name" + " or id." if self.with_id else ".")

        if len(lower) > 100:
            raise commands.BadArgument(
                f"Playlist names must be 100 characters or less. (You have *{len(lower)}* characters)"
            )

        if self.with_id and converted and converted.isdigit():
            return int(converted)

        return converted.strip() if not self.lower else lower
