from io import BytesIO

from discord import File
from PIL import Image, ImageDraw, ImageFont

from app.rendering.pillow import ASSETS

__all__ = (
    'ColorImage',
)


class ColorImage:
    """ColorImage is a class that generates a color image.

    Parameters
    ----------
    rgb : tuple[int, int, int]
        The RGB color to generate the image from.
    text : str | None, optional
        The text to put on the image, by default None
    """

    def __init__(self, rgb: tuple[int, int, int], text: str | None = None) -> None:
        self.rgb: tuple[int, int, int] = rgb
        self.text: str | None = text

        self.font = ImageFont.truetype(ASSETS / 'fonts/rubrik.ttf', size=28)

    @property
    def is_color_dark(self) -> bool:
        """Check if a color is dark."""
        r, g, b = self.rgb
        brightness = (r * 299 + g * 587 + b * 114) / 1000
        return brightness < 128

    def generate_color_img(self) -> BytesIO:
        """Generate a color image."""
        image = Image.new('RGB', (256, 256), self.rgb)
        draw = ImageDraw.Draw(image)

        if self.text:
            text_color = 'white' if self.is_color_dark else 'black'

            _, _, w, h = draw.textbbox((0, 0), self.text, font=self.font)
            draw.text(((256 - w) / 2, (256 - h) / 2), self.text, font=self.font, fill=text_color)

        buffer = BytesIO()
        image.save(buffer, 'png')
        buffer.seek(0)
        return buffer

    def create(self) -> File:
        """Creates the color image.

        Returns
        -------
        `File`
            The color image as a file.
        """
        buffer = self.generate_color_img()
        return File(buffer, filename='color.png')
