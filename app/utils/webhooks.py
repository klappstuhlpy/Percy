"""Sending through Discord webhooks without Percy's custom emojis.

Percy's emojis (``config.Emojis``) are *application* emojis uploaded in the developer
portal. Only the bot user can render them; a webhook message shows them as ``:name:``
text. Every webhook send therefore goes through :func:`send_webhook`, which removes them
from the content and every embed string.
"""

from __future__ import annotations

import re
from typing import Any

import discord
from discord.utils import MISSING

__all__ = ('send_webhook', 'strip_custom_emojis')

_CUSTOM_EMOJI_RE = re.compile(r'<a?:\w+:\d+>\s?')


def strip_custom_emojis(value: Any) -> Any:
    """Remove custom emoji markup from a string, or from every string in a nested dict/list.

    A string that held nothing but emojis becomes a zero-width space, so a required embed
    field name or value never ends up empty.
    """
    if isinstance(value, str):
        stripped = _CUSTOM_EMOJI_RE.sub('', value).strip(' ')
        return stripped if stripped or not value else '​'
    if isinstance(value, dict):
        return {k: strip_custom_emojis(v) for k, v in value.items()}
    if isinstance(value, list):
        return [strip_custom_emojis(v) for v in value]
    return value


def _clean_embed(embed: discord.Embed) -> discord.Embed:
    return discord.Embed.from_dict(strip_custom_emojis(embed.to_dict()))


async def send_webhook(webhook: discord.Webhook, content: Any = MISSING, **kwargs: Any) -> Any:
    """:meth:`discord.Webhook.send` with custom emojis stripped from content and embeds."""
    if isinstance(content, str):
        content = strip_custom_emojis(content)
    if kwargs.get('embed'):
        kwargs['embed'] = _clean_embed(kwargs['embed'])
    if kwargs.get('embeds'):
        kwargs['embeds'] = [_clean_embed(e) for e in kwargs['embeds']]
    return await webhook.send(content, **kwargs)
