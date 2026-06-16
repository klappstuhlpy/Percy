"""Pure generation logic for the sentinel captcha image."""

from __future__ import annotations

import random
from io import BytesIO

from captcha.image import ImageCaptcha

from app.rendering.primitives import ASSETS

__all__ = ('generate_captcha',)

_CAPTCHA_CHARS: str = 'abcdefghijklmnopqrstuvwxyz1234567890'
_IMAGE_CAPTCHA: ImageCaptcha = ImageCaptcha(
    width=300, height=100, fonts=[str(ASSETS / 'fonts/helvetica.ttf')])


def generate_captcha(*, length: int = 6) -> tuple[str, BytesIO]:
    """Generate a random captcha; returns the solution text and a PNG buffer."""
    text: str = ''.join(random.choices(_CAPTCHA_CHARS, k=length))
    image = _IMAGE_CAPTCHA.generate_image(text)

    buffer = BytesIO()
    image.save(buffer, format='PNG')
    buffer.seek(0)
    return text, buffer
