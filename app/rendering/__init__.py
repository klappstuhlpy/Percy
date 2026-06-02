"""Image generation layer.

Cogs render artifacts through the high-level :class:`RenderingService`
(``self.bot.render``); they never instantiate drawing code or import Pillow
directly. The package is layered:

- ``primitives``  — low-level Pillow toolkit (fonts, masks, colour helpers).
- ``models``      — plain dataclasses carrying prepared render data.
- ``templates``   — pure drawing functions (data in, buffer out; no Discord/DB).
- ``service``     — :class:`RenderingService`, the only public entry point.

``get_dominant_color`` / ``resize_to_limit`` / ``Font`` remain exported as
stateless utilities that are legitimately reused outside artifact rendering.
"""

from app.rendering.primitives import ASSETS, Font, get_dominant_color, resize_to_limit
from app.rendering.service import Captcha, RenderingService

__all__ = (
    'ASSETS',
    'Captcha',
    'Font',
    'RenderingService',
    'get_dominant_color',
    'resize_to_limit',
)
