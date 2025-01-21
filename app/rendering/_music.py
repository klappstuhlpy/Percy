from io import BytesIO

from discord import File
from PIL import Image, ImageDraw, ImageFont

from app.rendering.pillow import ASSETS

__all__ = (
    'Music',
)


class Music:
    """Music is a class that generates music images."""

    def __init__(self) -> None:
        self.font = ImageFont.truetype(ASSETS / 'fonts/rubik.ttf', size=28)

    @staticmethod
    def _get_gain_y(
            gain: float, *, max_gain: float = +1.0, min_gain: float = -0.25, top_margin: int = 0, band_height: int = 0
    ) -> int:
        """Get the y position of the gain."""
        gain_range = max_gain - min_gain

        if gain > 0:
            y = top_margin + int((max_gain - gain) / gain_range * band_height)
        elif gain < 0:
            y = top_margin + band_height + int(gain / min_gain * band_height)
        else:
            y = top_margin + band_height
        return y

    def generate_eq_image(self, payload: list[float]) -> File:
        reference_image = Image.open(ASSETS / 'eq_template.png')

        image = Image.new('RGB', reference_image.size, 'white')
        draw = ImageDraw.Draw(image)

        image.paste(reference_image, (0, 0))

        num_bands = len(payload)
        width = image.width
        height = image.height + 35
        band_width = (width - 130) // num_bands
        band_height = (height - 280) // 2
        top_margin = (height - (2 * band_height)) // 2

        # Draw the Dots for the Gains
        for i, gain in enumerate(payload):
            x = 90 + i * band_width
            y = self._get_gain_y(gain, top_margin=top_margin, band_height=band_height)

            draw.ellipse([(x + band_width // 2 - 2, y - 2), (x + band_width // 2 + 2, y + 2)], fill='white')

        # Draw the Lines for the Gains
        for i in range(num_bands - 1):
            x1 = 90 + (i + 0.5) * band_width
            gain = payload[i]
            y1 = self._get_gain_y(gain, top_margin=top_margin, band_height=band_height)

            x2 = 90 + (i + 1.5) * band_width
            future_gain = payload[i + 1]
            y2 = self._get_gain_y(future_gain, top_margin=top_margin, band_height=band_height)

            draw.line([(x1, y1), (x2, y2)], fill='white', width=1, joint='curve')

        eq_text = 'EQ'
        x = 356 - len(eq_text) * (len(eq_text) // 2)
        draw.text((x, 29), eq_text, font=self.font, fill='white')

        buffer = BytesIO()
        image.save(buffer, 'png')
        buffer.seek(0)

        return File(buffer, filename='eq.png')
