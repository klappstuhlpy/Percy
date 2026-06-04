from __future__ import annotations

import contextlib

import discord

from app.clients import GroqClient, GroqResponseError
from app.clients.base import HTTPClientError
from app.core import Accent, Bot, Cog, Context, command, cooldown, describe, make_notice
from app.utils import truncate
from config import groq

#: Steers the model toward short, Discord-appropriate replies.
SYSTEM_PROMPT = (
    'You are Percy, a helpful and friendly Discord assistant. '
    'Answer concisely — a few short paragraphs at most, since replies are shown in a chat. '
    'Use Discord-flavoured markdown when helpful, and never claim to be able to take actions '
    'in the server (moderation, roles, etc.); you only chat.'
)

#: Hard ceiling on a single rendered reply (Components V2 text budget).
MAX_REPLY_CHARS = 3900


class Assistant(Cog):
    """A conversational AI assistant backed by Groq's fast open models."""

    emoji = '\N{ROBOT FACE}'

    def __init__(self, bot: Bot) -> None:
        super().__init__(bot)
        self.client: GroqClient | None = (
            GroqClient(bot.session, api_key=groq.api_key, model=groq.model) if groq.api_key else None
        )

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
        hybrid=True,
    )
    @cooldown(1, 12)
    @describe(prompt='What you want to ask.')
    async def ask(self, ctx: Context, *, prompt: str) -> None:
        """Ask the AI assistant a question.

        Reply to one of the assistant's previous answers to give it that context.
        """
        if self.client is None:
            await ctx.send_error('The AI assistant is not configured on this instance.')
            return

        await ctx.defer()

        messages: list[dict[str, str]] = [{'role': 'system', 'content': SYSTEM_PROMPT}]
        previous = await self._previous_turn(ctx)
        if previous:
            messages.append({'role': 'assistant', 'content': previous})
        messages.append({'role': 'user', 'content': prompt})

        try:
            answer = await self.client.chat(messages)
        except (HTTPClientError, GroqResponseError):
            await ctx.send_error('The assistant is unavailable right now — please try again shortly.')
            return

        view = make_notice(
            'Assistant',
            truncate(answer, MAX_REPLY_CHARS) or '*(no response)*',
            accent=Accent.info,
            thumbnail=self.bot.user.display_avatar.url if self.bot.user else None,
            footer=f'Asked by {ctx.author.display_name} · powered by Groq',
        )
        await ctx.send(view=view)


async def setup(bot: Bot) -> None:
    await bot.add_cog(Assistant(bot))
