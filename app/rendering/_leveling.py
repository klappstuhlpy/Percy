from functools import partial
from io import BytesIO
from typing import TYPE_CHECKING, TypeAlias

import discord
from discord import File
from PIL import Image, ImageDraw

from app.rendering.pillow import ASSETS, FontManager, get_dominant_color, get_text_dimensions, Font, FONT_MAPPING
from app.utils import shorten_number

if TYPE_CHECKING:
    from app.cogs.leveling import LevelConfig
else:
    LevelConfig = TypeAlias

__all__ = (
    'LevelCard',
)


class LevelCard:
    """LevelCard is a class that generates a level card.

    Attributes
    ----------
    avatar : `bytes`
        The avatar of the user.
    member : `discord.Member`
        The user to generate the level card for.
    level_config : `LevelConfig`
        The level config of the guild.
    """

    def __init__(
            self,
            avatar: bytes,
            member: discord.Member,
            level_config: LevelConfig,
            font: Font = Font.RUBIK
    ) -> None:
        self.avatar: bytes = avatar
        self.member: discord.Member = member
        self.level_config: LevelConfig = level_config
        self.font: Font = font

        self.total_xp = self.level_config.config.spec.get_total_xp(
            self.level_config.level, self.level_config.xp)

        self._fonts = FontManager()
        self.get_font = partial(self._fonts.get, ASSETS / FONT_MAPPING[self.font.value])

    @property
    def base_image(self) -> Image:
        """:class:`Image` : The base image of the level card."""
        return Image.open(ASSETS / 'rank_card.png')

    @staticmethod
    def add_corners(image: Image.Image, rad: int) -> Image.Image:
        """Adds corners to the image."""
        with Image.new('L', (rad * 4, rad * 4), 0) as circle:
            draw = ImageDraw.Draw(circle)
            draw.ellipse((0, 0, rad * 4, rad * 4), fill=255)

            alpha = Image.new('L', image.size, 'white')

            w, h = image.size
            alpha.paste(circle.crop((0, 0, rad * 2, rad * 2)), (0, 0))
            alpha.paste(circle.crop((0, rad * 2, rad * 2, rad * 4)), (0, h - rad * 2))
            alpha.paste(circle.crop((rad * 2, 0, rad * 4, rad * 2)), (w - rad * 2, 0))
            alpha.paste(circle.crop((rad * 2, rad * 2, rad * 4, rad * 4)), (w - rad * 2, h - rad * 2))
            image.putalpha(alpha)

            return image

    @staticmethod
    def create_rounded_rectangle_mask(size: tuple[int, int], radius: int, alpha: int = 255) -> Image.Image:
        """Creates a rounded rectangle mask."""
        factor = 5
        radius = radius * factor
        image = Image.new('RGBA', (size[0] * factor, size[1] * factor), (0, 0, 0, 0))

        corner = Image.new('RGBA', (radius, radius), (0, 0, 0, 0))
        draw = ImageDraw.Draw(corner)
        draw.pieslice(((0, 0), (radius * 2, radius * 2)), 180, 270, fill=(50, 50, 50, alpha + 55))

        mx, my = (size[0] * factor, size[1] * factor)

        image.paste(corner, (0, 0), corner)
        image.paste(corner.rotate(90), (0, my - radius), corner.rotate(90))
        image.paste(corner.rotate(180), (mx - radius, my - radius), corner.rotate(180))
        image.paste(corner.rotate(270), (mx - radius, 0), corner.rotate(270))

        draw = ImageDraw.Draw(image)
        draw.rectangle((radius, 0, mx - radius, my), fill=(50, 50, 50, alpha))
        draw.rectangle((0, radius, mx, my - radius), fill=(50, 50, 50, alpha))
        image = image.resize(size, Image.Resampling.LANCZOS)

        return image

    def create_outlined_rounded_rectangle(
            self,
            size: tuple[int, int],
            radius: int,
            thickness: int,
            fill: tuple,
            outline: tuple
    ) -> tuple[Image.Image, Image.Image]:
        """Creates an outlined rounded rectangle."""
        with Image.new('RGB', (size[0] + thickness, size[1] + thickness), outline) as outline_image:
            with Image.new('RGB', size, fill) as fill_image:
                outline_image.paste(
                    fill_image, (thickness // 2, thickness // 2), self.create_rounded_rectangle_mask(size, radius))

            return outline_image, self.create_rounded_rectangle_mask(outline_image.size, radius + (thickness // 2))

    @staticmethod
    def get_color_alpha(
            foreground: tuple, alpha: float, background: tuple[int, int, int] = (34, 40, 49)
    ) -> tuple[int, ...] | tuple[int, int, int]:
        color = []
        for f, b in zip(foreground, background):
            color.append(int(f * alpha + b * (1 - alpha)))

        return tuple(color)

    def format_round_avatar(self) -> Image:
        """Formats the avatar to a round image."""
        avatar = Image.open(BytesIO(self.avatar)).resize((196, 196), Image.Resampling.BOX)

        mask = Image.new('L', (196, 196), 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, 196, 196), fill=255)

        round_avatar = Image.new('RGBA', (196, 196))
        round_avatar.paste(avatar, (0, 0), mask=mask)

        return round_avatar

    async def generate_level_card(self) -> BytesIO:
        """Draws the level card."""
        base = self.base_image.copy()
        avatar = self.format_round_avatar()

        base.paste(avatar, (38, 38), avatar)

        user_canvas = ImageDraw.Draw(base)

        user_canvas.text(
            (252, 62),
            str(self.member),
            (235, 235, 235),
            font=self.get_font(48),
        )

        total_xp_text = f'{self.total_xp:,} XP'
        user_canvas.text(
            (252, 114),
            total_xp_text,
            self.get_color_alpha((216, 216, 216), 0.8),
            font=self.get_font(28),
        )

        rank_text = f'Rank #{await self.level_config.get_rank()}'
        rank_width = user_canvas.textlength(rank_text, font=self.get_font(48))
        user_canvas.text(
            (
                base.width - rank_width - 38,
                62
            ),
            rank_text,
            (235, 235, 235),
            font=self.get_font(48),
        )

        members = f'of {shorten_number(len(self.member.guild.members))}'
        members_width = user_canvas.textlength(members, font=self.get_font(28))
        user_canvas.text(
            (
                base.width - members_width - 38,
                114
            ),
            members,
            self.get_color_alpha((216, 216, 216), 0.8),
            font=self.get_font(28),
        )

        color = get_dominant_color(avatar)
        empty_bar, empty_bar_mask = self.create_outlined_rounded_rectangle(
            (862, 42),
            10,
            4,
            self.get_color_alpha(color, 0.3),
            color
        )
        base.paste(empty_bar, (252, 168), empty_bar_mask)

        if multiplier := abs(self.level_config.xp / self.level_config.max_xp):
            progress_bar = Image.new('RGB', (round(862 * multiplier), 44), color=color)
            base.paste(
                progress_bar,
                (252, 168),
                self.create_rounded_rectangle_mask(progress_bar.size, 10)
            )

        level_bg, level_bg_mask = self.create_outlined_rounded_rectangle(
            (192, 60),
            20,
            4,
            (57, 62, 70),
            self.get_color_alpha(color, 0.5)
        )
        level_canvas = ImageDraw.Draw(level_bg)

        level_text = 'Level'
        level_text_width, level_text_height = get_text_dimensions(level_text, font=self.get_font(32))
        level_number = str(self.level_config.level)
        level_number_width, level_number_height = get_text_dimensions(level_number, font=self.get_font(36))

        text_offset_x = int((192 - (level_text_width + level_number_width + 8)) / 2)
        text_offset_y = int((55 - max(level_text_height, level_number_height)) / 2)

        level_canvas.text(
            (text_offset_x, text_offset_y),
            level_text,
            (216, 216, 216),
            font=self.get_font(32),
        )
        level_canvas.text(
            (
                text_offset_x + level_text_width + 8,
                text_offset_y + level_text_height - level_number_height - 2
            ),
            level_number,
            (235, 235, 235),
            font=self.get_font(36),
        )

        base.paste(level_bg, (38, 256), level_bg_mask)

        experience_bg, experience_bg_mask = self.create_outlined_rounded_rectangle(
            (260, 60),
            20,
            4,
            (57, 62, 70),
            self.get_color_alpha(color, 0.5)
        )
        exp_canvas = ImageDraw.Draw(experience_bg)
        exp_text = f'{shorten_number(self.level_config.xp)} XP / {shorten_number(self.level_config.max_xp)}'

        font, y = self.get_font(28), 14
        if (text_size := font.getlength(exp_text)) > 190:
            font, y = self.get_font(24), 18
            text_size = font.getlength(exp_text)

        exp_canvas.text(
            (
                int((212 - text_size) / 2) + 24,
                y
            ),
            exp_text,
            (235, 235, 235),
            font=font,
        )

        base.paste(experience_bg, (252, 256), experience_bg_mask)

        messages_bg, messages_bg_mask = self.create_outlined_rounded_rectangle(
            (268, 60),
            20,
            4,
            (57, 62, 70),
            self.get_color_alpha(color, 0.5)
        )
        message_canvas = ImageDraw.Draw(messages_bg)
        msg_count = shorten_number(self.level_config.messages)
        msg_text = 'Messages'

        count_font, text_font, count_offset, text_offset = self.get_font(28), self.get_font(24), 14, 16
        if (text_size := (count_font.getlength(msg_count) + 12 + text_font.getlength(msg_text))) > 200:
            cfont, tfont, count_offset, text_offset = self.get_font(22), self.get_font(20), 16, 18
            text_size = cfont.getlength(msg_count) + 12 + tfont.getlength(msg_text)

        offset = int((200 - text_size) / 2)

        message_canvas.text(
            (
                offset + 40,
                count_offset
            ),
            msg_count,
            (235, 235, 235),
            font=count_font,
        )
        message_canvas.text(
            (
                offset + 48 + count_font.getlength(msg_count),
                text_offset
            ),
            msg_text,
            (216, 216, 216),
            font=text_font,
        )

        base.paste(messages_bg, (846, 256), messages_bg_mask)

        buffer = BytesIO()
        base.save(buffer, format='png')
        buffer.seek(0)

        return buffer

    async def create(self) -> File:
        """Creates the level card.

        Returns
        -------
        `File`
            The level card as a file.
        """
        buffer = await self.generate_level_card()
        return File(buffer, filename=f'{self.member.id}.png')
