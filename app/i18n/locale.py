from __future__ import annotations

import gettext
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass

__all__ = ("I18n", "t")

log = logging.getLogger(__name__)

LOCALES_DIR = Path(__file__).parent / "locales"
DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "de", "fr", "es", "pt", "ja", "ko")


class I18n:
    """Per-guild internationalization manager using gettext.

    The translation files live in ``app/i18n/locales/<lang>/LC_MESSAGES/percy.mo``.
    Generate .pot with ``xgettext``, compile with ``msgfmt``.

    Usage from cogs::

        from app.i18n import t

        # In a command:
        locale = await self.bot.i18n.get_guild_locale(ctx.guild.id)
        msg = t("command.success", locale)
    """

    def __init__(self) -> None:
        self._guild_locales: dict[int, str] = {}
        self._translations: dict[str, gettext.GNUTranslations | gettext.NullTranslations] = {}
        self._load_translations()

    def _load_translations(self) -> None:
        """Load all available .mo files from the locales directory."""
        for locale in SUPPORTED_LOCALES:
            try:
                self._translations[locale] = gettext.translation(
                    "percy", localedir=str(LOCALES_DIR), languages=[locale]
                )
            except FileNotFoundError:
                self._translations[locale] = gettext.NullTranslations()

        log.info("i18n: loaded translations for %s", ", ".join(SUPPORTED_LOCALES))

    def get_translation(self, locale: str) -> gettext.GNUTranslations | gettext.NullTranslations:
        """Get the translation object for a given locale."""
        return self._translations.get(locale, self._translations.get(DEFAULT_LOCALE, gettext.NullTranslations()))

    def set_guild_locale(self, guild_id: int, locale: str) -> None:
        """Set the preferred locale for a guild."""
        if locale not in SUPPORTED_LOCALES:
            raise ValueError(f"Unsupported locale: {locale}. Supported: {SUPPORTED_LOCALES}")
        self._guild_locales[guild_id] = locale
        log.info("i18n: set guild %d locale to %s", guild_id, locale)

    def get_guild_locale(self, guild_id: int | None) -> str:
        """Get the locale for a guild, defaulting to English."""
        if guild_id is None:
            return DEFAULT_LOCALE
        return self._guild_locales.get(guild_id, DEFAULT_LOCALE)

    def translate(self, message: str, locale: str | None = None) -> str:
        """Translate a message string using the given locale."""
        loc = locale or DEFAULT_LOCALE
        trans = self.get_translation(loc)
        return trans.gettext(message)

    @property
    def supported_locales(self) -> tuple[str, ...]:
        return SUPPORTED_LOCALES


_global_i18n: I18n | None = None


def _get_i18n() -> I18n:
    global _global_i18n
    if _global_i18n is None:
        _global_i18n = I18n()
    return _global_i18n


def t(message: str, locale: str | None = None) -> str:
    """Translate a message string. Shorthand for I18n.translate()."""
    return _get_i18n().translate(message, locale)
