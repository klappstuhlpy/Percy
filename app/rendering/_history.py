import io
import math
import uuid
from functools import partial
from io import BytesIO

from discord import File
from PIL import Image, ImageDraw

from app.rendering.pillow import ASSETS, FontManager, get_text_dimensions, resize_to_limit
from app.utils import helpers

__all__ = (
    'BarChart',
    'PresenceChart',
    'AvatarCollage',
)


class BarChart:
    """BarChart is a class that generates a bar chart.

    Parameters
    ----------
    data : `dict[str, int]`
        The data to generate the bar chart from.
    title : `str`
        The title of the bar chart.

    Attributes
    ----------
    data : `dict[str, int]`
        The data to generate the bar chart from.
    title : `str`
        The title of the bar chart.
    """

    def __init__(self, data: dict[str, int | float], title: str) -> None:
        self.data: dict[str, int | float] = data
        self.title: str = title

        self._fonts = FontManager()
        self.get_font = partial(self._fonts.get, ASSETS / 'fonts/menlo.ttf')

    def generate_bar_chart(self) -> list[Image.Image]:
        """Generate a bar chart image from a dictionary of data."""
        BAR_HEIGHT = 25
        BAR_COLOR = (227, 38, 54)
        LABEL_FONT_SIZE = 18
        LABEL_PADDING = 20
        CHART_MARGIN = 20
        MAX_WIDTH = 1920
        MAX_HEIGHT = 1080

        num_bars = len(self.data)
        max_keys_per_chart = int(MAX_HEIGHT / (BAR_HEIGHT + LABEL_PADDING)) - 2

        chart_width = max(min(max(self.data.values()), MAX_WIDTH) + LABEL_PADDING * 2, MAX_WIDTH)
        chart_height = (num_bars + 1) * (BAR_HEIGHT + LABEL_PADDING) + CHART_MARGIN * 2

        scale_factor = min(MAX_WIDTH / chart_width, MAX_HEIGHT / chart_height)
        chart_width *= scale_factor
        chart_height *= scale_factor

        image_count = len(self.data) // max_keys_per_chart + 1 if len(self.data) % max_keys_per_chart != 0 else len(
            self.data) // max_keys_per_chart

        images = []
        for i in range(image_count):
            start_index = i * max_keys_per_chart
            end_index = start_index + max_keys_per_chart
            subset_data = dict(list(self.data.items())[start_index:end_index])

            image = Image.new('RGB', (int(chart_width), int(chart_height)),
                              color=helpers.Colour.lighter_black().to_rgb())
            draw = ImageDraw.Draw(image)

            font = self.get_font(int(LABEL_FONT_SIZE * scale_factor))
            max_label_width = max([get_text_dimensions(label, font=font)[0] for label in subset_data])
            max_value_width = max([get_text_dimensions(str(value), font=font)[0] for value in subset_data.values()])

            if self.title:
                title_font = self.get_font(int(LABEL_FONT_SIZE * scale_factor * 1.5))
                title_bbox = draw.textbbox((0, 0), self.title, font=title_font)
                title_width = title_bbox[2] - title_bbox[0]
                title_height = title_bbox[3] - title_bbox[1]
                title_position = ((chart_width - title_width) // 2, CHART_MARGIN)
                draw.text(
                    title_position,
                    self.title,
                    font=title_font,
                    fill=(255, 255, 255)
                )

                y = CHART_MARGIN + (title_height + 5) + LABEL_PADDING * 2
            else:
                y = CHART_MARGIN

            for label, value in subset_data.items():
                _, label_height = get_text_dimensions(label, font=font)
                value_width, value_height = get_text_dimensions(str(value), font=font)

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
                bar_width = int(value / max(self.data.values()) * bar_width)

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

    @classmethod
    def add_images_yaxis(cls, images: list[Image.Image]) -> Image.Image:
        """Add the images together among the y-axis."""
        final_image = Image.new('RGB', (images[0].width, sum(image.height for image in images)))
        y_positions = 0
        for i, image in enumerate(images):
            final_image.paste(image, (0, y_positions))
            y_positions += image.height

        return final_image

    @classmethod
    def _merge_and_render(cls, images: list[Image.Image]) -> File:
        """Merge the images."""
        final_image = cls.add_images_yaxis(images)
        buffer = BytesIO()
        final_image.save(buffer, 'png')
        buffer.seek(0)
        return File(buffer, filename='bar_chart.png')

    def render(self, /, *, byted: bool = True) -> list[io.BytesIO] | list[Image.Image]:
        """Render the bar chart as a list of bytes.

        Returns
        -------
        `list[io.BytesIO]`
            The bar chart as a list of bytes.
        """
        images = self.generate_bar_chart()

        if byted:
            resolved = []
            for image in images:
                buffer = BytesIO()
                image.save(buffer, 'png')
                buffer.seek(0)
                resolved.append(buffer)
            return resolved

        return images

    def create(self, merge: bool = False) -> File | list[File]:
        """Creates the bar chart.

        Parameters
        ----------
        merge : bool, optional
            Whether to merge the images, by default False

        Returns
        -------
        `File`
            The bar chart as a file.
        """
        images = self.generate_bar_chart()
        if merge:
            return self._merge_and_render(images)

        return [File(image, filename=f'bar_chart_{i}.png') for i, image in enumerate(images)]


class PresenceChart:
    """PresenceChart is a class that generates a presence chart."""

    def __init__(self, labels: list[str], values: list[int], colors: list[str]) -> None:
        self.id = str(uuid.uuid4())[0:8]

        self.data = values
        self.labels = labels
        self.colors = colors

        self.scale_ratio: int = 3

        self.inner_radius: int = 130 * self.scale_ratio

        self.width: int = 600 * self.scale_ratio
        self.height: int = 400 * self.scale_ratio
        self.radius: float = min(self.width, self.height) / 2.2
        self.center: tuple[float, float] = (self.radius + 20, self.height / 2)

        self.image: Image.Image | None = None
        self.draw: ImageDraw.ImageDraw | None = None

        self._fonts = FontManager()
        self.font = self._fonts.get(ASSETS / 'fonts/arial.ttf', size=20 * self.scale_ratio)

    def draw_pie_chart(self) -> None:
        """Draws the pie chart."""
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
        """Cleans the inner circle of the pie chart."""
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
        """Draws the cubes."""
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

    def create(self) -> File:
        """Creates the chart.

        Returns
        -------
        `File`
            The chart as a file.
        """
        self.image = Image.new('RGBA', (self.width, self.height), (255, 255, 255, 0))
        self.draw = ImageDraw.Draw(self.image)

        funcs = [
            self.draw_pie_chart,
            self.clean_inner_circle,
            self.draw_cubes,
        ]

        for func in funcs:
            func()

        buffer = BytesIO()
        self.image.save(buffer, format='png')
        buffer.seek(0)

        return File(buffer, filename=f'{self.id}.png')


class AvatarCollage:
    """AvatarCollage is a class that generates an avatar collage.

    Parameters
    ----------
    avatars : `list[bytes]`
        The avatars to generate the collage from.
    """

    def __init__(self, avatars: list[bytes]) -> None:
        self.id: str = str(uuid.uuid4())
        self.avatars: list[bytes] = avatars

        self.image: Image.Image | None = None

    def create(self) -> File:
        """Create a collage of the user's avatars.

        Returns
        -------
        File
            The BytesIO object containing the image.
        """
        self.draw_collage()
        buffer: BytesIO = BytesIO()
        self.image.save(buffer, 'png')
        buffer.seek(0)
        buffer = resize_to_limit(buffer, 8000000)
        return File(buffer, filename=f'{self.id}.png')

    def draw_collage(self) -> None:
        """Draw the collage."""
        xbound = math.ceil(math.sqrt(len(self.avatars)))
        ybound = math.ceil(len(self.avatars) / xbound)
        size = int(2520 / xbound)

        with Image.new('RGBA', size=(xbound * size, ybound * size), color=(0, 0, 0, 0)) as base:
            x, y = 0, 0
            for avy in self.avatars:
                if avy:
                    im = Image.open(BytesIO(avy)).resize((size, size), resample=Image.BICUBIC)
                    base.paste(im, box=(x * size, y * size))
                if x < xbound - 1:
                    x += 1
                else:
                    x = 0
                    y += 1

        self.image: Image = base
