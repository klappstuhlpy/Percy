from __future__ import annotations

from app.clients import TranslateClient, TranslationError
from app.clients.base import HTTPClientError
from app.core import Accent, Bot, Cog, Context, command, describe, make_notice
from app.utils import truncate

#: A small alias table so users can type common language names, not just ISO codes.
#: Anything not listed falls through as-is (the endpoint accepts ISO-639-1 codes).
LANGUAGE_ALIASES: dict[str, str] = {
    'english': 'en', 'german': 'de', 'deutsch': 'de', 'french': 'fr', 'spanish': 'es',
    'italian': 'it', 'portuguese': 'pt', 'dutch': 'nl', 'polish': 'pl', 'russian': 'ru',
    'japanese': 'ja', 'korean': 'ko', 'chinese': 'zh-CN', 'arabic': 'ar', 'turkish': 'tr',
    'swedish': 'sv', 'norwegian': 'no', 'danish': 'da', 'finnish': 'fi', 'greek': 'el',
    'hindi': 'hi', 'ukrainian': 'uk', 'czech': 'cs', 'romanian': 'ro', 'hungarian': 'hu',
    'vietnamese': 'vi', 'thai': 'th', 'indonesian': 'id', 'hebrew': 'iw', 'latin': 'la',
}


def resolve_language(value: str) -> str:
    """Maps a language name or code to the ISO code the endpoint expects."""
    return LANGUAGE_ALIASES.get(value.strip().lower(), value.strip())


class Translator(Cog):
    """Translate text between languages, powered by a keyless translation backend."""

    emoji = '\N{GLOBE WITH MERIDIANS}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.client: TranslateClient = TranslateClient(bot.session)

    @command(
        'translate',
        aliases=['tr'],
        description='Translate text into another language.',
        hybrid=True,
    )
    @describe(
        language='Target language — an ISO code (en, de, fr) or a name (german, spanish).',
        text='The text to translate.',
    )
    async def translate(self, ctx: Context, language: str, *, text: str) -> None:
        """Translate text into another language.

        The source language is detected automatically. Examples:
        `?translate de Hello, how are you?` · `?translate spanish good morning`
        """
        target = resolve_language(language)
        await ctx.defer()

        try:
            result = await self.client.translate(text, target=target)
        except (TranslationError, HTTPClientError):
            await ctx.send_error('Could not translate that right now — please try again later.')
            return

        view = make_notice(
            'Translation',
            truncate(result.text, 3900),
            accent=Accent.info,
            fields=[('Original', truncate(text, 1000))],
            footer=f'{result.source_language} → {result.target_language}',
        )
        await ctx.send(view=view)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Translator(bot))
