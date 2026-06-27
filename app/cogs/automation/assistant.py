from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING

import discord

from app.core import Accent, Context, command, cooldown, describe, make_notice
from app.services import ModelTier, build_assistant_system
from app.utils import truncate
from config import support_server, website

if TYPE_CHECKING:
    from app.core import Bot

#: Hard ceiling on a single rendered reply (Components V2 text budget).
MAX_REPLY_CHARS = 3900


class AssistantMixin:
    """A conversational AI assistant backed by Percy's self-hosted Ollama instance."""

    bot: Bot

    async def _previous_turn(self, ctx: Context) -> str | None:
        """If the user replied to one of the bot's answers, return that text for context."""
        if ctx.message is None or ctx.message.reference is None:
            return None
        ref = ctx.message.reference
        if ref.message_id is None:
            return None
        with contextlib.suppress(discord.HTTPException):
            replied = ctx.message.reference.resolved or await ctx.channel.fetch_message(ref.message_id)
            if isinstance(replied, discord.Message) and replied.author.id == self.bot.user.id:  # type: ignore[union-attr]
                # CV2 replies carry no content; fall back to the embed-less component text if present.
                return replied.content or None
        return None

    @command(
        'ask',
        aliases=['ai', 'chat'],
        description='Ask the AI assistant a question.',
    )
    @cooldown(1, 12)
    @describe(prompt='What you want to ask.')
    async def ask(self, ctx: Context, *, prompt: str) -> None:
        """Ask the AI assistant a question.

        Reply to one of the assistant's previous answers to give it that context.
        """
        if not self.bot.ai.available:
            await ctx.send_error('The AI assistant is currently unavailable.')
            return

        async with ctx.typing():
            system = build_assistant_system(
                server_name=ctx.guild.name if ctx.guild else None,
                prefix=ctx.clean_prefix,
                website=website,
                support_server=support_server,
            )
            messages: list[dict[str, str]] = [{'role': 'system', 'content': system}]
            previous = await self._previous_turn(ctx)
            if previous:
                messages.append({'role': 'assistant', 'content': previous})
            messages.append({'role': 'user', 'content': prompt})

            answer = await self.bot.ai.complete(messages, tier=ModelTier.SMART)

        if answer is None:
            # Graceful degradation: the model is down/disabled or timed out.
            await ctx.send_error('The AI assistant is currently unavailable. Please try again later.')
            return

        resp = "-# Assistant~\n" + (answer or "*no response*")
        resp = truncate(resp, MAX_REPLY_CHARS)
        await ctx.send(resp)
