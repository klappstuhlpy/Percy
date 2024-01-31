import io
from io import BytesIO
from typing import Optional

import discord
from PIL import Image, ImageDraw, ImageFont
from cogs.utils.tasks import executor
from cogs.utils.formats import shorten_number
from pathlib import Path

PATH = str(Path(__file__).parent.parent.parent.absolute() / 'assets')

GINTO_NORD_HEAVY_48 = ImageFont.truetype(PATH + '/GintoNordHeavy.otf', 48)
GINTO_NORD_HEAVY_36 = ImageFont.truetype(PATH + '/GintoNordHeavy.otf', 36)
GINTO_NORD_HEAVY_28 = ImageFont.truetype(PATH + '/GintoNordHeavy.otf', 28)
GINTO_NORD_HEAVY_22 = ImageFont.truetype(PATH + '/GintoNordHeavy.otf', 22)
GINTO_BOLD_32 = ImageFont.truetype(PATH + '/GintoBold.otf', 32)
GINTO_BOLD_28 = ImageFont.truetype(PATH + '/GintoBold.otf', 28)
GINTO_BOLD_24 = ImageFont.truetype(PATH + '/GintoBold.otf', 24)
GINTO_BOLD_20 = ImageFont.truetype(PATH + '/GintoBold.otf', 20)

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
                text_color = 'white'
            else:
                text_color = 'black'

            _, _, w, h = draw.textbbox((0, 0), text, font=GINTO_BOLD_28)
            draw.text(((256 - w) / 2, (256 - h) / 2), text, font=GINTO_BOLD_28, fill=text_color)

        buffer = BytesIO()
        image.save(buffer, 'png')
        buffer.seek(0)
        return buffer

    @classmethod
    def generate_bar_chart(
            cls,
            data: dict[str, int | float],
            title: Optional[str] = None,
            merge: bool = False,
            to_buffer: bool = True
    ) -> list[Image.Image] | list[bytes] | bytes:
        """Generate a bar chart image from a dictionary of data.

        Parameters
        ----------
        data : dict
            A dictionary of data to generate the bar chart from.
            Data must follow the format of {str: int}.
        title : Optional[str], optional
            The title of the bar chart, by default None
        merge : bool, optional
            Whether to merge the images into one, by default False
        to_buffer : bool, optional
            Whether to return a list of bytes or a list of PIL images, by default True
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

            image = Image.new('RGB', (int(chart_width), int(chart_height)), color=0x1A1A1A)
            draw = ImageDraw.Draw(image)

            font = ImageFont.truetype(PATH + '/GintoBold.otf', int(LABEL_FONT_SIZE * scale_factor))
            max_label_width = max([cls.get_text_dimensions(label, font=font)[0] for label in subset_data.keys()])
            max_value_width = max([cls.get_text_dimensions(str(value), font=font)[0] for value in subset_data.values()])

            if title:
                title_font = ImageFont.truetype(PATH + '/GintoBold.otf', int(LABEL_FONT_SIZE * scale_factor * 1.5))
                title_bbox = draw.textbbox((0, 0), title, font=title_font)
                title_width = title_bbox[2] - title_bbox[0]
                title_height = title_bbox[3] - title_bbox[1]
                title_position = ((chart_width - title_width) // 2, CHART_MARGIN)
                draw.text(
                    title_position,
                    title,
                    font=title_font,
                    fill=(255, 255, 255)
                )

                y = CHART_MARGIN + (title_height + 5) + LABEL_PADDING * 2
            else:
                y = CHART_MARGIN

            for label, value in subset_data.items():
                _, label_height = cls.get_text_dimensions(label, font=font)
                value_width, value_height = cls.get_text_dimensions(str(value), font=font)

                # the label is aligned to the left of the image
                label_position = (LABEL_PADDING, y + (BAR_HEIGHT - label_height) // 2)
                draw.text(
                    label_position,
                    label,
                    font=font,
                    color=(255, 255, 255),
                    LANCZOS=True
                )

                bar_width = chart_width - max_label_width - max_value_width - LABEL_PADDING * 4
                # Calculate the length of the bar by dividing the value
                # by the max value and multiplying it by the max ar width
                bar_width = int(value / max(data.values()) * bar_width)

                # value is the count how often the command was invoked; it's displayed right on the bar
                value_position = (LABEL_PADDING * 3 + bar_width + max_label_width, y + (BAR_HEIGHT - value_height) // 2)
                draw.text(
                    value_position,
                    str(value),
                    font=font,
                    color=(255, 255, 255),
                    LANCZOS=True
                )

                # bar starts after the label and has a width of max_bar_width
                draw.rounded_rectangle(
                    (
                        LABEL_PADDING * 2 + max_label_width,
                        y,
                        LABEL_PADDING * 2 + max_label_width + bar_width,
                        y + BAR_HEIGHT
                    ),
                    10,
                    outline=BAR_COLOR,
                    width=40,
                    fill=BAR_COLOR
                )

                y += BAR_HEIGHT + LABEL_PADDING

            if merge:
                images.append(image)
            else:
                buffer = BytesIO()
                image.save(buffer, 'png')
                buffer.seek(0)
                images.append(buffer.read())

        if merge:
            # Add the images together among the y-axis
            return cls.add_images_yaxis(images, to_buffer=to_buffer)

        return images

    @classmethod
    def add_images_yaxis(cls, images: list[Image.Image], to_buffer: bool = False) -> Image.Image | bytes:
        """Add the images together among the y-axis."""
        final_image = Image.new('RGB', (images[0].width, sum(image.height for image in images)))
        y_positions = 0
        for i, image in enumerate(images):
            final_image.paste(image, (0, y_positions))
            y_positions += image.height

        if to_buffer:
            buffer = BytesIO()
            final_image.save(buffer, 'png')
            buffer.seek(0)
            return buffer.read()

        return final_image

    # Level Card

    @classmethod
    def add_corners(cls, image: Image.Image, rad: int) -> Image.Image:
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

    @classmethod
    def create_rounded_rectangle_mask(cls, size: tuple[int, int], radius: int, alpha: int = 255) -> Image.Image:
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
        image = image.resize(size, Image.LANCZOS)

        return image

    @classmethod
    def create_outlined_rounded_rectangle(
            cls,
            size: tuple[int, int],
            radius: int,
            thickness: int,
            fill: tuple,
            outline: tuple
    ) -> tuple[Image.Image, Image.Image]:
        with Image.new('RGB', (size[0] + thickness, size[1] + thickness), outline) as outline_image:
            with Image.new('RGB', size, fill) as fill_image:
                outline_image.paste(
                    fill_image, (thickness // 2, thickness // 2), cls.create_rounded_rectangle_mask(size, radius))

            return outline_image, cls.create_rounded_rectangle_mask(outline_image.size, radius + (thickness // 2))

    @classmethod
    def get_dominant_color(cls, image: Image.Image | BytesIO, palette_size=16) -> tuple:
        if isinstance(image, BytesIO):
            image = Image.open(image)

        img = image.copy()
        img.thumbnail((100, 100))

        paletted = img.convert('P', palette=Image.ADAPTIVE, colors=palette_size)

        palette = paletted.getpalette()
        color_counts = sorted(paletted.getcolors(), reverse=True)
        palette_index = color_counts[0][1]
        dominant_color = palette[palette_index * 3: palette_index * 3 + 3]  # type: ignore

        return tuple(dominant_color)

    @classmethod
    def get_color_alpha(
            cls, foreground: tuple, alpha: float, background: tuple[int, int, int] = (34, 40, 49)
    ) -> tuple[int, ...] | tuple[int, int, int]:
        color = []
        for f, b in zip(foreground, background):
            color.append(int(f * alpha + b * (1 - alpha)))

        return tuple(color)

    @staticmethod
    def convert_to_circle_image(
            image: Image,
            outline_colour: tuple[int, int, int],
            outline_width: int = 4,
            size: tuple[int, int] = (196, 196)
    ) -> Image:
        circle_image = Image.new('RGBA', image.size, (0, 0, 0, 0))

        mask = Image.new('L', image.size, 0)
        mask_draw = ImageDraw.Draw(mask)
        mask_draw.ellipse((0, 0, image.size[0], image.size[1]), fill=255)

        circle_image.paste(image, (0, 0), mask=mask)
        circle_image = circle_image.resize((196, 196), Image.LANCZOS)

        outline = Image.new('RGBA', circle_image.size, (0, 0, 0, 0))
        outline_draw = ImageDraw.Draw(outline)
        outline_draw.ellipse(
            (
                outline_width,
                outline_width,
                circle_image.size[0] - outline_width,
                circle_image.size[1] - outline_width
            ),
            outline_colour,
            width=10
        )
        image = Image.alpha_composite(outline, circle_image)
        return image.resize(size, Image.LANCZOS)

    @staticmethod
    def get_text_dimensions(text_string: str, font: ImageFont) -> tuple[int, int]:
        # https://stackoverflow.com/a/46220683/9263761
        ascent, descent = font.getmetrics()

        text_width = font.getmask(text_string).getbbox()[2]
        text_height = font.getmask(text_string).getbbox()[3] + descent

        return text_width, text_height

    # Actual Level Card Render

    @executor
    def generate_rank_card(
            self,
            *,
            avatar: bytes,
            user: discord.Member,
            level: int,
            total_xp: int,
            current: int,
            required: int,
            rank: int,
            members: int,
            messages: int
    ) -> BytesIO:
        with Image.open(PATH + '/template.png') as background:
            avatar = Image.open(BytesIO(avatar)).resize((196, 196), Image.BOX)

            mask = Image.new('L', (196, 196), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 196, 196), fill=255)

            round_avatar = Image.new('RGBA', (196, 196))
            round_avatar.paste(avatar, (0, 0), mask=mask)

            background.paste(round_avatar, (38, 38), round_avatar)

            user_canvas = ImageDraw.Draw(background)

            user_canvas.text(
                (252, 62),
                str(user),
                (235, 235, 235),
                font=GINTO_NORD_HEAVY_48,
            )

            total_xp = f'{total_xp:,} XP'
            user_canvas.text(
                (252, 114),
                total_xp,
                self.get_color_alpha((216, 216, 216), 0.8),
                font=GINTO_BOLD_28,
            )

            rank = f'Rank #{rank}'
            rank_width = user_canvas.textlength(rank, font=GINTO_NORD_HEAVY_48)
            user_canvas.text(
                (
                    background.width - rank_width - 38,
                    62
                ),
                rank,
                (235, 235, 235),
                GINTO_NORD_HEAVY_48,
            )

            members = f'of {shorten_number(members)}'
            members_width = user_canvas.textlength(members, font=GINTO_BOLD_28)
            user_canvas.text(
                (
                    background.width - members_width - 38,
                    114
                ),
                members,
                self.get_color_alpha((216, 216, 216), 0.8),
                GINTO_BOLD_28,
            )

            color = self.get_dominant_color(avatar)
            empty_bar, empty_bar_mask = self.create_outlined_rounded_rectangle(
                (862, 38),
                10,
                4,
                self.get_color_alpha(color, 0.3),
                color
            )
            background.paste(empty_bar, (252, 162), empty_bar_mask)

            if multiplier := abs(current / required):
                progress_bar = Image.new('RGB', (round(862 * multiplier), 38), color=color)
                background.paste(
                    progress_bar,
                    (252, 164),
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
            level_text_width, level_text_height = self.get_text_dimensions(level_text, font=GINTO_BOLD_32)
            level_number = str(level)
            level_number_width, level_number_height = self.get_text_dimensions(level_number, font=GINTO_NORD_HEAVY_36)

            text_offset_x = int((192 - (level_text_width + level_number_width + 8)) / 2)
            text_offset_y = int((55 - max(level_text_height, level_number_height)) / 2)

            level_canvas.text(
                (text_offset_x, text_offset_y),
                level_text,
                (216, 216, 216),
                GINTO_BOLD_32
            )
            level_canvas.text(
                (
                    text_offset_x + level_text_width + 8,
                    text_offset_y + level_text_height - level_number_height - 2
                ),
                level_number,
                (235, 235, 235),
                GINTO_NORD_HEAVY_36,
            )

            background.paste(level_bg, (38, 254), level_bg_mask)

            experience_bg, experience_bg_mask = self.create_outlined_rounded_rectangle(
                (260, 60),
                20,
                4,
                (57, 62, 70),
                self.get_color_alpha(color, 0.5)
            )
            experience_canvas = ImageDraw.Draw(experience_bg)
            text = f'{shorten_number(current)} XP / {shorten_number(required)}'

            font, y = EXPERIENCE[False]
            if (text_size := font.getlength(text)) > 190:
                font, y = EXPERIENCE[True]
                text_size = font.getlength(text)

            experience_canvas.text(
                (
                    int((212 - text_size) / 2) + 24,
                    y
                ),
                text,
                (235, 235, 235),
                font,
            )

            background.paste(experience_bg, (252, 254), experience_bg_mask)

            messages_bg, messages_bg_mask = self.create_outlined_rounded_rectangle(
                (268, 60),
                20,
                4,
                (57, 62, 70),
                self.get_color_alpha(color, 0.5)
            )
            message_canvas = ImageDraw.Draw(messages_bg)
            msg_count = shorten_number(messages)

            count_font, text_font, count_offset, text_offset = GINTO_NORD_HEAVY_28, GINTO_BOLD_24, 14, 16
            if (text_size := (count_font.getlength(msg_count) + 12 + text_font.getlength('Messages'))) > 200:
                cfont, tfont, count_offset, text_offset = GINTO_NORD_HEAVY_22, GINTO_BOLD_20, 16, 18
                text_size = cfont.getlength(msg_count) + 12 + tfont.getlength('Messages')

            offset = int((200 - text_size) / 2)

            message_canvas.text(
                (
                    offset + 40,
                    count_offset
                ),
                msg_count,
                (235, 235, 235),
                count_font,
            )
            message_canvas.text(
                (
                    offset + 48 + count_font.getlength(msg_count),
                    text_offset
                ),
                'Messages',
                (216, 216, 216),
                text_font,
            )

            background.paste(messages_bg, (846, 256), messages_bg_mask)

        buffer = BytesIO()
        background.save(buffer, 'PNG')
        buffer.seek(0)
        return buffer

    # Image Grid

    @staticmethod
    def create_image_grid(images: list[Image], grid_size=(2, 2)) -> BytesIO:
        if len(images) > grid_size[0] * grid_size[1]:
            raise ValueError("Number of images doesn't match the grid size")

        if len(images) < grid_size[0] * grid_size[1]:
            images += [Image.new('RGB', images[0].size, (0, 0, 0)) for _ in range(grid_size[0] * grid_size[1] - len(images))]

        grid_width = max(img.width for img in images) * grid_size[0]
        grid_height = max(img.height for img in images) * grid_size[1]

        grid_image = Image.new('RGB', (grid_width, grid_height))

        for i in range(grid_size[0]):
            for j in range(grid_size[1]):
                index = i * grid_size[1] + j
                grid_image.paste(images[index], (j * images[index].width, i * images[index].height))

        buffer = BytesIO()
        grid_image.save(buffer, 'jpeg')
        buffer.seek(0)
        return buffer
