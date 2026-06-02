"""Pure drawing logic for the data charts: horizontal bar charts, the presence
donut chart and the avatar collage."""

from __future__ import annotations

import math
from functools import partial
from io import BytesIO
from typing import TYPE_CHECKING

from PIL import Image, ImageDraw

from app.rendering.primitives import ASSETS, FontManager, get_text_dimensions, resize_to_limit
from app.utils import helpers

if TYPE_CHECKING:
    from collections.abc import Callable

    from PIL.ImageFont import FreeTypeFont

    from app.rendering.models import BarChartData, PresenceData

__all__ = (
    'draw_avatar_collage',
    'draw_presence_chart',
    'merge_images_vertical',
    'render_bar_chart_images',
)


def render_bar_chart_images(data: BarChartData, fonts: FontManager) -> list[Image.Image]:
    """Generate one or more bar chart images from a dictionary of data."""
    get_font: Callable[[int], FreeTypeFont] = partial(fonts.get, str(ASSETS / 'fonts/menlo.ttf'))

    BAR_HEIGHT = 25
    BAR_COLOR = (227, 38, 54)
    LABEL_FONT_SIZE = 18
    LABEL_PADDING = 20
    CHART_MARGIN = 20
    MAX_WIDTH = 1920
    MAX_HEIGHT = 1080

    values = data.data
    title = data.title

    num_bars = len(values)
    max_keys_per_chart = int(MAX_HEIGHT / (BAR_HEIGHT + LABEL_PADDING)) - 2

    chart_width = max(min(max(values.values()), MAX_WIDTH) + LABEL_PADDING * 2, MAX_WIDTH)
    chart_height = (num_bars + 1) * (BAR_HEIGHT + LABEL_PADDING) + CHART_MARGIN * 2

    scale_factor = min(MAX_WIDTH / chart_width, MAX_HEIGHT / chart_height)
    chart_width *= scale_factor
    chart_height *= scale_factor

    image_count = len(values) // max_keys_per_chart + 1 if len(values) % max_keys_per_chart != 0 else len(
        values) // max_keys_per_chart

    images = []
    for i in range(image_count):
        start_index = i * max_keys_per_chart
        end_index = start_index + max_keys_per_chart
        subset_data = dict(list(values.items())[start_index:end_index])

        image = Image.new('RGB', (int(chart_width), int(chart_height)),
                          color=helpers.Colour.lighter_black().to_rgb())
        draw = ImageDraw.Draw(image)

        font = get_font(int(LABEL_FONT_SIZE * scale_factor))
        max_label_width = max([get_text_dimensions(label, font=font)[0] for label in subset_data])
        max_value_width = max([get_text_dimensions(str(value), font=font)[0] for value in subset_data.values()])

        if title:
            title_font = get_font(int(LABEL_FONT_SIZE * scale_factor * 1.5))
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
            _, label_height = get_text_dimensions(label, font=font)
            _value_width, value_height = get_text_dimensions(str(value), font=font)

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
            bar_width = int(value / max(values.values()) * bar_width)

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

        images.append(image)

    return images


def merge_images_vertical(images: list[Image.Image]) -> Image.Image:
    """Stack the given images along the y-axis into a single image."""
    final_image = Image.new('RGB', (images[0].width, sum(image.height for image in images)))
    y_positions = 0
    for image in images:
        final_image.paste(image, (0, y_positions))
        y_positions += image.height

    return final_image


class _PresenceChart:
    """Internal drawing state for the presence donut chart."""

    def __init__(self, data: PresenceData, fonts: FontManager) -> None:
        self.data = data.values
        self.labels = data.labels
        self.colors = data.colors

        self.scale_ratio: int = 3
        self.inner_radius: int = 130 * self.scale_ratio

        self.width: int = 600 * self.scale_ratio
        self.height: int = 400 * self.scale_ratio
        self.radius: float = min(self.width, self.height) / 2.2
        self.center: tuple[float, float] = (self.radius + 20, self.height / 2)

        self.image: Image.Image = Image.new('RGBA', (self.width, self.height), (255, 255, 255, 0))
        self.draw: ImageDraw.ImageDraw = ImageDraw.Draw(self.image)

        self.font = fonts.get(str(ASSETS / 'fonts/arial.ttf'), size=20 * self.scale_ratio)

    def draw_pie_chart(self) -> None:
        data = self.data
        total = sum(data) or 1
        start_angle = 0

        for i, d in enumerate(data):
            angle = 360 * d / total
            self.draw.pieslice(
                (  # type: ignore
                    self.center[0] - self.radius,
                    self.center[1] - self.radius,
                    self.center[0] + self.radius,
                    self.center[1] + self.radius,
                ),
                start_angle,
                start_angle + angle,
                fill=self.colors[i],
            )
            start_angle += angle

    def clean_inner_circle(self) -> None:
        self.draw.ellipse(
            (
                self.center[0] - self.inner_radius,
                self.center[1] - self.inner_radius,
                self.center[0] + self.inner_radius,
                self.center[1] + self.inner_radius,
            ),
            fill=(255, 255, 255, 0),
        )

    def draw_cubes(self) -> None:
        total = sum(self.data) or 1
        TEXT_PADDING = 150 * self.scale_ratio
        CUBE_1st_PADDING = 180 * self.scale_ratio
        CUBE_2nd_PADDING = 162.5 * self.scale_ratio
        MULTIPLIER = 70 * self.scale_ratio

        for i, (color, label) in enumerate(zip(self.colors, self.labels)):
            i_multi = i * MULTIPLIER

            self.draw.rectangle(
                (
                    self.width - CUBE_1st_PADDING,
                    (65 * self.scale_ratio) + i_multi,
                    self.width - CUBE_2nd_PADDING,
                    (82.5 * self.scale_ratio) + i_multi
                ),
                fill=color,
            )

            self.draw.text(
                (self.width - TEXT_PADDING, (45 * self.scale_ratio) + i_multi),
                label,
                font=self.font,
                align='right',
            )
            self.draw.text(
                (self.width - TEXT_PADDING, (65 * self.scale_ratio) + i_multi),
                f'{round(self.data[i] / 3600, 2)} Hours',
                font=self.font,
                align='right',
            )
            self.draw.text(
                (self.width - TEXT_PADDING, (85 * self.scale_ratio) + i_multi),
                f'{round(self.data[i] / total * 100, 2)}%',
                font=self.font,
                align='right',
            )

        # Align the "Total: ..." label to the right
        total_text = f'Total: {round(total / 3600, 2)} Hours'
        self.draw.text(
            (self.width - TEXT_PADDING - (50 * self.scale_ratio), (55 * self.scale_ratio) + len(self.colors) * MULTIPLIER),
            total_text,
            font=self.font,
            align='right',
        )

    def render(self) -> BytesIO:
        for func in (self.draw_pie_chart, self.clean_inner_circle, self.draw_cubes):
            func()

        buffer = BytesIO()
        self.image.save(buffer, format='png')
        buffer.seek(0)
        return buffer


def draw_presence_chart(data: PresenceData, fonts: FontManager) -> BytesIO:
    """Draws a presence donut chart for the given prepared data and returns a PNG buffer."""
    return _PresenceChart(data, fonts).render()


def draw_avatar_collage(avatars: list[bytes]) -> BytesIO:
    """Draws a square collage of the given avatars and returns a PNG buffer
    resized to stay under Discord's attachment limit."""
    xbound = math.ceil(math.sqrt(len(avatars)))
    ybound = math.ceil(len(avatars) / xbound)
    size = int(2520 / xbound)

    with Image.new('RGBA', size=(xbound * size, ybound * size), color=(0, 0, 0, 0)) as base:
        x, y = 0, 0
        for avy in avatars:
            if avy:
                im = Image.open(BytesIO(avy)).resize((size, size), resample=Image.Resampling.BICUBIC)
                base.paste(im, box=(x * size, y * size))
            if x < xbound - 1:
                x += 1
            else:
                x = 0
                y += 1

        buffer = BytesIO()
        base.save(buffer, 'png')
        buffer.seek(0)

    return resize_to_limit(buffer, 8000000)
