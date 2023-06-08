from io import BytesIO
from typing import Optional

import discord
from PIL import Image, ImageDraw, ImageFont
from cogs.utils.async_utils import executor
from cogs.utils.formats import shorten_number
from pathlib import Path

PATH = str(Path(__file__).parent.parent.parent.absolute() / "assets")

GINTO_NORD_HEAVY_48 = ImageFont.truetype(PATH + "/GintoNordHeavy.otf", 48)
GINTO_NORD_HEAVY_36 = ImageFont.truetype(PATH + "/GintoNordHeavy.otf", 36)
GINTO_NORD_HEAVY_28 = ImageFont.truetype(PATH + "/GintoNordHeavy.otf", 28)
GINTO_NORD_HEAVY_22 = ImageFont.truetype(PATH + "/GintoNordHeavy.otf", 22)
GINTO_BOLD_32 = ImageFont.truetype(PATH + "/GintoBold.otf", 32)
GINTO_BOLD_28 = ImageFont.truetype(PATH + "/GintoBold.otf", 28)
GINTO_BOLD_24 = ImageFont.truetype(PATH + "/GintoBold.otf", 24)
GINTO_BOLD_20 = ImageFont.truetype(PATH + "/GintoBold.otf", 20)

EXPERIENCE = {False: (GINTO_NORD_HEAVY_28, 12), True: (GINTO_NORD_HEAVY_22, 16)}


class Render:
    """A class for assets images."""

    @classmethod
    def is_color_dark(cls, rgb_color: tuple[int, int, int]) -> bool:
        """Check if a color is dark."""
        r, g, b = rgb_color
        brightness = (r * 299 + g * 587 + b * 114) / 1000
        return brightness < 128

    @classmethod
    def generate_color_img(cls, rgb: tuple[int, int, int], text: Optional[str] = None) -> BytesIO:
        """Generate a color image.

        Parameters
        ----------
        rgb : tuple[int, int, int]
            The RGB color to generate the image from.
        text : Optional[str], optional
            The text to put on the image, by default None
        """
        image = Image.new('RGB', (256, 256), rgb)
        draw = ImageDraw.Draw(image)

        if text:
            if cls.is_color_dark(rgb):
                text_color = "white"
            else:
                text_color = "black"

            _, _, w, h = draw.textbbox((0, 0), text, font=GINTO_BOLD_28)
            draw.text(((256 - w) / 2, (256 - h) / 2), text, font=GINTO_BOLD_28, fill=text_color)

        buffer = BytesIO()
        image.save(buffer, 'png')
        buffer.seek(0)
        return buffer

    @classmethod
    def generate_bar_chart(cls, data: dict, title: Optional[str] = None) -> list[bytes]:
        """Generate a bar chart image from a dictionary of data.

        Parameters
        ----------
        data : dict
            A dictionary of data to generate the bar chart from.
            Data must follow the format of {str[key]: int[value]}.
        title : Optional[str], optional
            The title of the bar chart, by default None
        """
        BAR_HEIGHT = 25
        BAR_COLOR = (227, 38, 54)
        LABEL_FONT_SIZE = 18
        LABEL_PADDING = 10
        CHART_MARGIN = 20
        MAX_WIDTH = 1360
        MAX_HEIGHT = 675

        num_bars = len(data)
        max_keys_per_chart = int(MAX_HEIGHT / (BAR_HEIGHT + LABEL_PADDING)) - 2

        chart_width = max(min(max(data.values()), MAX_WIDTH) + LABEL_PADDING * 2, MAX_WIDTH)
        chart_height = (num_bars + 1) * (BAR_HEIGHT + LABEL_PADDING) + CHART_MARGIN * 2

        scale_factor = min(MAX_WIDTH / chart_width, MAX_HEIGHT / chart_height)
        chart_width *= scale_factor
        chart_height *= scale_factor

        image_count = len(data) // max_keys_per_chart + 1 if len(data) % max_keys_per_chart != 0 else len(
            data) // max_keys_per_chart

        images = []
        for i in range(image_count):
            start_index = i * max_keys_per_chart
            end_index = start_index + max_keys_per_chart
            subset_data = dict(list(data.items())[start_index:end_index])

            image = Image.new("RGB", (int(chart_width), int(chart_height)), color=0x1A1A1A)  # 0x020202
            # image = Image.new("RGBA", (int(chart_width), int(chart_height)), (0, 0, 0, 0))  # Transparent
            draw = ImageDraw.Draw(image)

            font = ImageFont.truetype(PATH + "/GintoBold.otf", int(LABEL_FONT_SIZE * scale_factor))

            if title:
                title_font = ImageFont.truetype(PATH + "/GintoBold.otf", int(LABEL_FONT_SIZE * scale_factor * 1.5))
                title_bbox = draw.textbbox((0, 0), title, font=title_font)
                title_width = title_bbox[2] - title_bbox[0]
                title_height = title_bbox[3] - title_bbox[1]
                title_position = ((chart_width - title_width) // 2, CHART_MARGIN)
                draw.text(title_position, title, font=title_font, fill=(255, 255, 255))  # 0xFFFFFF  # , 255

                y = CHART_MARGIN + (title_height + 5) + LABEL_PADDING
            else:
                y = CHART_MARGIN

            for label, value in subset_data.items():
                bar_width = int(value / max(data.values()) * (chart_width - LABEL_PADDING * 2))
                _origin_bar_width = bar_width

                label_text = f"{label}: {value}"
                label_width, label_height = font.getbbox(label_text)[2:]
                available_width = chart_width - LABEL_PADDING - bar_width - LABEL_PADDING

                if label_width > available_width:
                    bar_width = (bar_width - label_width) - len(label_text)

                draw.rounded_rectangle(((LABEL_PADDING, y), (LABEL_PADDING + bar_width, y + BAR_HEIGHT)), radius=15,
                                       outline=BAR_COLOR, width=40, fill=BAR_COLOR)

                label_position = (
                    LABEL_PADDING + bar_width + LABEL_PADDING, y + (BAR_HEIGHT - label_height) // 2
                )
                if label_width > available_width:
                    label_position = (_origin_bar_width - label_width, y + (BAR_HEIGHT - label_height) // 2)
                draw.text(label_position, label_text, font=font, fill=(255, 255, 255),
                          antialias=True)  # 0xFFFFFF  # , 255

                y += BAR_HEIGHT + LABEL_PADDING

            buffer = BytesIO()
            image.save(buffer, 'png')
            buffer.seek(0)
            images.append(buffer.read())

        return images

    # Level Card

    @classmethod
    def add_corners(cls, image: Image.Image, rad: int) -> Image.Image:
        with Image.new("L", (rad * 4, rad * 4), 0) as circle:
            draw = ImageDraw.Draw(circle)
            draw.ellipse((0, 0, rad * 4, rad * 4), fill=255)

            alpha = Image.new("L", image.size, "white")

            w, h = image.size
            alpha.paste(circle.crop((0, 0, rad * 2, rad * 2)), (0, 0))
            alpha.paste(circle.crop((0, rad * 2, rad * 2, rad * 4)), (0, h - rad * 2))
            alpha.paste(circle.crop((rad * 2, 0, rad * 4, rad * 2)), (w - rad * 2, 0))
            alpha.paste(circle.crop((rad * 2, rad * 2, rad * 4, rad * 4)), (w - rad * 2, h - rad * 2))
            image.putalpha(alpha)

            return image

    @classmethod
    def create_rounded_rectangle_mask(cls, size: tuple[int, int], radius: int, alpha: int = 255) -> Image.Image:
        factor = 5
        radius = radius * factor
        image = Image.new("RGBA", (size[0] * factor, size[1] * factor), (0, 0, 0, 0))

        corner = Image.new("RGBA", (radius, radius), (0, 0, 0, 0))
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
        image = image.resize(size, Image.ANTIALIAS)

        return image

    @classmethod
    def create_outlined_rounded_rectangle(
            cls, size: tuple[int, int], radius: int, thickness: int, fill: tuple[int, int, int],
            outline: tuple[int, int, int]
    ) -> tuple[Image.Image, Image.Image]:
        with Image.new("RGB", (size[0] + thickness, size[1] + thickness), outline) as outline_image:
            with Image.new("RGB", size, fill) as fill_image:
                outline_image.paste(
                    fill_image, (thickness // 2, thickness // 2), cls.create_rounded_rectangle_mask(size, radius))

            return outline_image, cls.create_rounded_rectangle_mask(outline_image.size, radius + (thickness // 2))

    @classmethod
    def get_dominant_color(cls, image: Image.Image | BytesIO, palette_size=16) -> tuple[int, int, int]:
        if isinstance(image, BytesIO):
            image = Image.open(image)

        img = image.copy()
        img.thumbnail((100, 100))

        paletted = img.convert("P", palette=Image.ADAPTIVE, colors=palette_size)

        palette = paletted.getpalette()
        color_counts = sorted(paletted.getcolors(), reverse=True)
        palette_index = color_counts[0][1]
        dominant_color = palette[palette_index * 3: palette_index * 3 + 3]  # type: ignore

        return tuple(dominant_color)

    @classmethod
    def get_color_alpha(
            cls, foreground: tuple[int, int, int], alpha: float, background: tuple[int, int, int] = (34, 40, 49)
    ) -> tuple[int, ...] | tuple[int, int, int]:
        color = []
        for f, b in zip(foreground, background):
            color.append(int(f * alpha + b * (1 - alpha)))

        return tuple(color)

    @staticmethod
    def convert_to_circle_image(image: Image, outline_colour: tuple[int, int, int], outline_width: int = 4) -> Image:
        circle_image = Image.new('RGBA', image.size, (0, 0, 0, 0))

        mask = Image.new('L', image.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, image.size[0], image.size[1]), fill=255)

        circle_image.paste(image, (0, 0), mask=mask)
        circle_image = circle_image.resize((196, 196), Image.ANTIALIAS)

        outline = Image.new("RGBA", circle_image.size, (0, 0, 0, 0))
        outline_draw = ImageDraw.Draw(outline)
        outline_draw.ellipse(
            (outline_width, outline_width, circle_image.size[0] - outline_width,
             circle_image.size[1] - outline_width),
            outline_colour,
            width=10
        )
        image = Image.alpha_composite(outline, circle_image)
        return image.resize((196, 196), Image.ANTIALIAS)

    # Actual Level Card Render

    @executor
    def generate_rank_card(
            self,
            *,
            avatar: bytes,
            user: discord.Member,
            level: int,
            current: int,
            required: int,
            rank: int,
            members: int,
            messages: int
    ) -> BytesIO:
        with Image.open(PATH + "/template.png") as background:
            avatar = Image.open(BytesIO(avatar)).resize((196, 196), Image.BOX)
            mask = Image.new("L", (196, 196), 0)
            draw = ImageDraw.Draw(mask)
            draw.ellipse((0, 0, 196, 196), fill=255)

            avatar_rounded = Image.new("RGBA", (196, 196))
            avatar_rounded.paste(avatar, (0, 0), mask=mask)

            background.paste(avatar_rounded, (38, 38), avatar_rounded)

            draw = ImageDraw.Draw(background)

            # Text for user's name
            user_name = user.nick or user.name
            draw.text((252, 62), user_name, font=GINTO_NORD_HEAVY_48, fill=(235, 235, 235))

            # Text for user's discriminator
            discriminator_text = f"#{user.discriminator}"
            draw.text((252, 114), discriminator_text, font=GINTO_BOLD_28,
                      fill=self.get_color_alpha((216, 216, 216), 0.8))

            rank_text = f"Rank #{rank}"
            rank_text_width, _ = draw.textsize(rank_text, font=GINTO_NORD_HEAVY_48)
            draw.text(
                (background.width - rank_text_width - 38, 62),
                rank_text,
                font=GINTO_NORD_HEAVY_48,
                fill=(235, 235, 235),
            )

            members_text = f"of {shorten_number(members)}"
            members_text_width, _ = draw.textsize(members_text, font=GINTO_BOLD_28)
            draw.text(
                (background.width - members_text_width - 38, 114),
                members_text,
                font=GINTO_BOLD_28,
                fill=self.get_color_alpha((216, 216, 216), 0.8),
            )

            color = self.get_dominant_color(avatar)
            empty_bar, empty_bar_mask = self.create_outlined_rounded_rectangle(
                (862, 38), radius=10, thickness=4, fill=self.get_color_alpha(color, 0.3), outline=color
            )
            background.paste(empty_bar, (252, 162), empty_bar_mask)

            if multiplier := abs(current / required):
                progress_bar = Image.new("RGB", (round(862 * multiplier), 38), color=color)
                background.paste(
                    progress_bar, (252, 164), self.create_rounded_rectangle_mask(progress_bar.size, 10)
                )

            level_bg, level_bg_mask = self.create_outlined_rounded_rectangle(
                (192, 60), 20, 4, (57, 62, 70), self.get_color_alpha(color, 0.5)
            )
            draw = ImageDraw.Draw(level_bg)

            level_text = "Level"
            level_text_width, _ = draw.textsize(level_text, font=GINTO_BOLD_32)
            draw.text((10, 10), level_text, font=GINTO_BOLD_32, fill=(216, 216, 216))

            level_number = str(level)
            level_number_width, _ = draw.textsize(level_number, font=GINTO_NORD_HEAVY_36)
            draw.text((level_text_width + 8, 8), level_number, font=GINTO_NORD_HEAVY_36, fill=(235, 235, 235))

            background.paste(level_bg, (38, 254), level_bg_mask)

            experience_bg, experience_bg_mask = self.create_outlined_rounded_rectangle(
                (260, 60), 20, 4, (57, 62, 70), self.get_color_alpha(color, 0.5)
            )
            draw = ImageDraw.Draw(experience_bg)
            text = f"{shorten_number(current)} XP / {shorten_number(required)}"

            font, y = EXPERIENCE[False]
            if (text_size := font.getlength(text)) > 190:
                font, y = EXPERIENCE[True]
                text_size = font.getlength(text)

            offset = int((212 - text_size) / 2)

            draw.text((offset + 28, y), text=text, font=font, fill=(235, 235, 235))

            background.paste(experience_bg, (252, 254), experience_bg_mask)

            messages_bg, messages_bg_mask = self.create_outlined_rounded_rectangle(
                (268, 60), 20, 4, (57, 62, 70), self.get_color_alpha(color, 0.5)
            )
            draw = ImageDraw.Draw(messages_bg)
            msg_count = shorten_number(messages)

            count_font, text_font, count_offset, text_offset = GINTO_NORD_HEAVY_28, GINTO_BOLD_24, 14, 18
            if (text_size := (count_font.getlength(msg_count) + 12 + text_font.getlength("Messages"))) > 200:
                cfont, tfont, count_offset, text_offset = GINTO_NORD_HEAVY_22, GINTO_BOLD_20, 18, 20
                text_size = cfont.getlength(msg_count) + 12 + tfont.getlength("Messages")

            offset = int((165 - text_size) / 2)

            draw.text((offset + 33, count_offset), text=msg_count, font=count_font, fill=(235, 235, 235))
            draw.text((offset + 48 + count_font.getlength(msg_count), text_offset), text="Messages", font=text_font,
                      fill=(216, 216, 216))

            background.paste(messages_bg, (846, 256), messages_bg_mask)

        buffer = BytesIO()
        background.save(buffer, "png")
        buffer.seek(0)
        return buffer
