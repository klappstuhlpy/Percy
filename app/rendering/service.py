"""The high-level rendering API.

:class:`RenderingService` is the single entry point cogs use for image generation,
reachable as ``self.bot.render`` (mirroring ``self.bot.db``). It owns the shared,
caching :class:`~app.rendering.primitives.FontManager`, performs all *data
preparation* (turning Discord/domain objects into the plain dataclasses in
:mod:`app.rendering.models`), runs the blocking render work (Pillow or matplotlib)
off the event loop via :func:`asyncio.to_thread`, and returns ready-to-send
:class:`discord.File` objects.

Cogs should never import the ``templates`` package or touch Pillow/matplotlib directly.
"""

from __future__ import annotations

import asyncio
import io
from typing import TYPE_CHECKING, NamedTuple

import discord

from app.rendering import templates
from app.rendering.models import BarChartData, ColorSwatchData, LevelCardData, PresenceData, QuoteData
from app.rendering.primitives import Font, FontManager, get_dominant_color

if TYPE_CHECKING:
    from app.cogs.leveling import LevelConfig

__all__ = ("Captcha", "RenderingService")


class Captcha(NamedTuple):
    """A generated captcha: the solution text and a ready-to-send image file."""

    text: str
    file: discord.File


class RenderingService:
    """High-level, async, event-loop-friendly image rendering."""

    def __init__(self) -> None:
        self._fonts = FontManager()

    # -- Artifact rendering -------------------------------------------------

    async def level_card(
        self, member: discord.Member, level_config: LevelConfig, *, font: Font = Font.RUBIK
    ) -> discord.File:
        """Render a member's rank card."""
        data = LevelCardData(
            avatar=await member.display_avatar.read(),
            name=str(member),
            total_xp=level_config.config.spec.get_total_xp(level_config.level, level_config.xp),
            rank=await level_config.get_rank(),
            member_count=len(member.guild.members),
            level=level_config.level,
            xp=level_config.xp,
            max_xp=level_config.max_xp,
            messages=level_config.messages,
            font=font,
        )
        buffer = await asyncio.to_thread(templates.draw_level_card, data, self._fonts)
        return discord.File(buffer, filename=f"{member.id}.png")

    async def quote(self, member: discord.Member | discord.User, text: str, *, font: Font = Font.GINTO_BOLD) -> discord.File:
        """Render a quote image attributed to ``member``."""
        data = QuoteData(
            avatar=await member.display_avatar.read(),
            text=text,
            author_name=member.display_name,
            font=font,
        )
        buffer = await asyncio.to_thread(templates.draw_quote, data, self._fonts)
        return discord.File(buffer, filename=f"{member.id}-quote.png")

    async def color_swatch(
        self, rgb: tuple[int, int, int], text: str | None = None, *, filename: str = "color.png"
    ) -> discord.File:
        """Render a solid colour swatch with optional caption."""
        buffer = await asyncio.to_thread(templates.draw_color_swatch, ColorSwatchData(rgb=rgb, text=text))
        return discord.File(buffer, filename=filename)

    async def equalizer(self, gains: list[float], *, filename: str = "image.png") -> discord.File:
        """Render the music equalizer band graph."""
        buffer = await asyncio.to_thread(templates.draw_equalizer, gains)
        return discord.File(buffer, filename=filename)

    async def bar_chart(
        self, data: dict[str, int | float], title: str, *, merge: bool = False, filename: str = "bar_chart.png"
    ) -> discord.File | list[discord.File]:
        """Render a horizontal bar chart.

        When the data spans more bars than fit in one image it is split. With
        ``merge=True`` the parts are stacked into a single file, otherwise a list
        of files (one per part) is returned.
        """
        spec = BarChartData(data=data, title=title)
        if merge:
            return await self.merge_bar_charts([spec], filename=filename)

        def _render() -> list[discord.File]:
            files = []
            for i, image in enumerate(templates.render_bar_chart_images(spec)):
                buffer = io.BytesIO()
                image.save(buffer, "png")
                buffer.seek(0)
                files.append(discord.File(buffer, filename=f"bar_chart_{i}.png"))
            return files

        return await asyncio.to_thread(_render)

    async def merge_bar_charts(self, specs: list[BarChartData], *, filename: str = "bar_chart.png") -> discord.File:
        """Render several bar charts and stack all their images into one file."""

        def _render() -> discord.File:
            images = []
            for spec in specs:
                images.extend(templates.render_bar_chart_images(spec))

            merged = templates.merge_images_vertical(images)
            buffer = io.BytesIO()
            merged.save(buffer, "png")
            buffer.seek(0)
            return discord.File(buffer, filename=filename)

        return await asyncio.to_thread(_render)

    async def avatar_collage(self, avatars: list[bytes], *, filename: str = "collage.png") -> discord.File:
        """Render a square collage of the given avatars."""
        buffer = await asyncio.to_thread(templates.draw_avatar_collage, avatars)
        return discord.File(buffer, filename=filename)

    async def presence_chart(
        self,
        *,
        labels: list[str],
        values: list[int],
        colors: list[str],
        title: str = "Presence",
        filename: str = "presence.png",
    ) -> discord.File:
        """Render a presence/activity donut chart."""
        data = PresenceData(labels=labels, values=values, colors=colors, title=title)
        buffer = await asyncio.to_thread(templates.draw_presence_chart, data)
        return discord.File(buffer, filename=filename)

    async def captcha(self, *, length: int = 6, filename: str = "captcha.png") -> Captcha:
        """Generate a random captcha image and its solution text."""
        text, buffer = await asyncio.to_thread(templates.generate_captcha, length=length)
        return Captcha(text=text, file=discord.File(buffer, filename=filename))

    # -- Pure helpers -------------------------------------------------------

    @staticmethod
    def dominant_color(image: io.BytesIO) -> tuple:
        """Return the dominant RGB colour of an image (synchronous, cheap)."""
        return get_dominant_color(image)
