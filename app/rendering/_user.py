import textwrap
from functools import partial
from io import BytesIO

import discord
from PIL import Image, ImageDraw
from discord import File

from app.rendering.pillow import ASSETS, FontManager, Font, FONT_MAPPING, get_text_dimensions


class Quote:
    """Creates an image that quotes a given text by a given author."""

    def __init__(
            self,
            avatar: bytes,
            text: str,
            member: discord.Member,
            font: Font = Font.GINTO_BOLD,
    ) -> None:
        self.avatar = avatar
        self.text = text
        self.member = member
        self.font = font

        self._fonts = FontManager()
        self.get_font = partial(self._fonts.get, ASSETS / FONT_MAPPING[self.font.value])

        self._avatar_last_x: int = 0

    def base_image(self) -> Image.Image:
        """Creates the base image for the quote."""
        image = Image.new('RGB', (1250, 500), 'black')
        avatar = Image.open(BytesIO(self.avatar)).convert('RGBA')

        aspect_ratio = avatar.width / avatar.height
        new_height = image.height
        new_width = int(new_height * aspect_ratio)

        avatar = avatar.resize((new_width, new_height))

        mask = Image.new('L', avatar.size)
        mask_data = []
        for y in range(avatar.height):
            for x in range(avatar.width):
                mask_data.append(int(255 * (1 - (x / avatar.width))))
                self._avatar_last_x = x

        mask.putdata(mask_data)
        avatar.putalpha(mask)
        image.paste(avatar, (0, 0), avatar)

        return image

    def draw_text(self, image: Image.Image) -> Image.Image:
        """Draws the text on the image."""
        draw = ImageDraw.Draw(image)

        QUOTE_FONT_SIZE = 46
        NAME_FONT_SIZE = 34
        quote_font = self.get_font(QUOTE_FONT_SIZE)
        name_font = self.get_font(NAME_FONT_SIZE)

        left_boundary = self._avatar_last_x + 40
        right_boundary = image.width - 40
        max_width = (right_boundary - left_boundary) // QUOTE_FONT_SIZE * 2

        lines = textwrap.wrap(self.text, width=max_width)
        total_text_height = sum(get_text_dimensions(line, quote_font)[1] for line in lines)

        start_y = (image.height - total_text_height) // 2
        center_x = left_boundary + (right_boundary - left_boundary) // 2

        for i, line in enumerate(lines):
            line_width, line_height = get_text_dimensions(line, quote_font)
            y = start_y + i * line_height
            if i == len(lines) - 1:
                y += total_text_height - len(lines) * line_height
            draw.text((center_x, y), line, font=quote_font, fill='white', anchor='mm')

        name_text = f'- {self.member.display_name}'
        name_y = start_y + total_text_height + 10
        draw.text((center_x, name_y), name_text, font=name_font, fill='white', anchor='mm')

        return image

    def build(self) -> BytesIO:
        """Builds the image."""
        image = self.base_image()
        image = self.draw_text(image)

        buffer = BytesIO()
        image.save(buffer, format='PNG')
        buffer.seek(0)
        return buffer

    def create(self) -> File:
        """Creates the level card.

        Returns
        -------
        `File`
            The level card as a file.
        """
        buffer = self.build()
        return File(buffer, filename=f'{self.member.id}-quote.png')
