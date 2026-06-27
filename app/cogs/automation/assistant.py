from __future__ import annotations

import contextlib
import copy
import re
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from app.core import Cog, Context, command, cooldown, describe
from app.core.views import View
from app.services import ModelTier, build_assistant_system
from app.utils import truncate
from config import support_server, website

if TYPE_CHECKING:
    from app.core import Bot

#: Hard ceiling on a single rendered reply (Components V2 text budget).
MAX_REPLY_CHARS = 3900
#: Leading marker on every assistant answer. Lets us recognise our own messages when
#: reconstructing a conversation from a Discord reply chain (replies carry the parent's
#: content, so this is a stable, restart-proof way to thread turns).
ASSISTANT_MARKER = '-# Assistant~'
#: How far up a reply chain we walk. Bounds Discord API fetches and the model's context.
MAX_CONTEXT_TURNS = 30
#: Soft cap on total characters of reconstructed history fed to the model (keeps the most
#: recent turns when a thread grows long, so small models don't overflow their context).
MAX_CONTEXT_CHARS = 6000
#: Most "Invoke" buttons to attach under one answer (one Discord action row holds five).
MAX_ACTION_BUTTONS = 4
#: Backtick-wrapped snippets in an answer are the model's command suggestions.
_BACKTICK_RE = re.compile(r'`([^`\n]{1,60})`')


class AssistantActionView(View):
    """Buttons that run the command(s) Percy named in its answer — for the asking user only.

    The model is told to *name* a command rather than simulate a feature; we surface each
    named, real command as a one-click "Invoke" button. Clicking it runs the command exactly
    as if the user had typed it (same checks, cooldowns, and interactive follow-ups).
    """

    def __init__(
        self,
        bot: Bot,
        *,
        invoker: discord.abc.User,
        source_message: discord.Message,
        actions: list[str],
    ) -> None:
        super().__init__(timeout=180.0, members=invoker, clear_on_timeout=False)
        self.bot = bot
        self._source = source_message
        for content in actions[:MAX_ACTION_BUTTONS]:
            button = discord.ui.Button(style=discord.ButtonStyle.primary, label=truncate(f'Invoke {content}', 80))
            button.callback = self._make_run(content)  # type: ignore[assignment]
            self.add_item(button)

    def _make_run(self, content: str):  # noqa: ANN202 - returns an interaction callback
        async def run(interaction: discord.Interaction) -> None:
            with contextlib.suppress(discord.HTTPException):
                await interaction.response.defer()
            # Prevent double-runs: disable the buttons once one is used.
            self.stop()
            self.disable_all()
            if self.message is not None:
                with contextlib.suppress(discord.HTTPException):
                    await self.message.edit(view=self)

            message = copy.copy(self._source)
            message.content = content
            new_ctx = await self.bot.get_context(message)
            await self.bot.invoke(new_ctx)

        return run


class AssistantMixin:
    """A conversational AI assistant backed by Percy's self-hosted Ollama instance.

    Start a chat with ``?ask <prompt>``. To continue, **reply** to Percy's answer with your
    next message — no command needed. Percy walks the whole reply chain back up, so context
    is preserved across as many follow-up replies as you like.
    """

    bot: Bot

    #: Per-user rate limit for reply-to-continue (the ``?ask`` command has its own cooldown).
    _thread_cooldown = commands.CooldownMapping.from_cooldown(1, 8.0, commands.BucketType.user)

    # -- conversation reconstruction -----------------------------------------

    def _is_assistant_message(self, message: discord.Message) -> bool:
        """True if ``message`` is one of Percy's own assistant answers."""
        return (
            self.bot.user is not None
            and message.author.id == self.bot.user.id
            and message.content.startswith(ASSISTANT_MARKER)
        )

    async def _display_prefix(self, message: discord.Message) -> str:
        """A human-facing command prefix for this context (skips mention prefixes)."""
        with contextlib.suppress(Exception):
            prefixes = await self.bot.get_prefix(message)
            if isinstance(prefixes, str):
                return prefixes
            for prefix in prefixes:
                if not prefix.startswith('<@'):
                    return prefix
        return '?'

    async def _strip_invocation(self, message: discord.Message) -> str:
        """A user message's text without a leading ``?ask``/``?ai``/``?chat`` invocation."""
        content = message.content
        with contextlib.suppress(Exception):
            prefixes = await self.bot.get_prefix(message)
            if isinstance(prefixes, str):
                prefixes = [prefixes]
            for prefix in sorted((p for p in prefixes if p), key=len, reverse=True):
                if content.startswith(prefix):
                    rest = content[len(prefix):].lstrip()
                    lowered = rest.lower()
                    for alias in ('ask', 'ai', 'chat'):
                        if lowered.startswith(alias) and rest[len(alias):len(alias) + 1] in ('', ' ', '\n'):
                            return rest[len(alias):].strip()
                    break
        return content.strip()

    async def _turn_from_message(self, message: discord.Message) -> tuple[str, str] | None:
        """Map a chain message to a ``(role, content)`` turn, or ``None`` to skip it."""
        if self._is_assistant_message(message):
            return 'assistant', message.content[len(ASSISTANT_MARKER):].strip()
        if message.author.bot:
            return None  # some unrelated bot message — not part of this conversation
        return 'user', await self._strip_invocation(message)

    async def _resolve_parent(self, message: discord.Message, *, fetch: bool = True) -> discord.Message | None:
        """The message ``message`` is replying to, or ``None``.

        With ``fetch=False`` only the gateway-provided ``resolved`` message is used (no API
        call) — used in the hot ``on_message`` path so ordinary replies stay cheap.
        """
        ref = message.reference
        if ref is None or ref.message_id is None:
            return None
        if isinstance(ref.resolved, discord.Message):
            return ref.resolved
        if not fetch:
            return None
        with contextlib.suppress(discord.HTTPException):
            return await message.channel.fetch_message(ref.message_id)
        return None

    async def _gather_history(self, message: discord.Message) -> list[dict[str, str]]:
        """Walk the reply chain strictly *above* ``message`` into oldest-first turns."""
        turns: list[dict[str, str]] = []
        budget = MAX_CONTEXT_CHARS
        parent = await self._resolve_parent(message)
        depth = 0
        while parent is not None and depth < MAX_CONTEXT_TURNS:
            turn = await self._turn_from_message(parent)
            if turn is not None and turn[1]:
                role, content = turn
                budget -= len(content)
                if budget < 0:
                    break  # keep the most-recent turns; drop older ones beyond the budget
                turns.append({'role': role, 'content': content})
            parent = await self._resolve_parent(parent)
            depth += 1
        turns.reverse()
        return turns

    # -- command suggestions -------------------------------------------------

    def _command_catalogue(self) -> list[tuple[str, str]]:
        """Visible top-level commands as ``(qualified_name, short_description)`` pairs.

        Injected into the system prompt so the model recommends real commands instead of
        inventing them. Top-level only (groups like ``tag`` cover their subcommands via help).
        """
        seen: set[str] = set()
        catalogue: list[tuple[str, str]] = []
        for cmd in self.bot.commands:
            if cmd.hidden or not cmd.enabled or cmd.qualified_name in seen:
                continue
            seen.add(cmd.qualified_name)
            desc = (cmd.description or cmd.short_doc or '').strip().split('\n', 1)[0]
            catalogue.append((cmd.qualified_name, truncate(desc, 70)))
        catalogue.sort(key=lambda pair: pair[0])
        return catalogue

    async def _extract_commands(self, answer: str, message: discord.Message) -> list[str]:
        """Find real, invokable commands the answer names, as ``<prefix><qualified_name>``.

        Looks at backtick-wrapped snippets (what the persona is told to use) and bare
        prefixed tokens, strips any prefix, and keeps only tokens that resolve to a visible,
        enabled command. De-duplicated and capped — the basis for the "Invoke" buttons.
        """
        prefixes: list[str] = []
        with contextlib.suppress(Exception):
            resolved = await self.bot.get_prefix(message)
            prefixes = [resolved] if isinstance(resolved, str) else list(resolved)
        usable_prefixes = sorted((p for p in prefixes if p and not p.startswith('<@')), key=len, reverse=True)
        display_prefix = await self._display_prefix(message)

        candidates: list[str] = [m.group(1) for m in _BACKTICK_RE.finditer(answer)]
        for prefix in usable_prefixes:
            candidates.extend(m.group(1) for m in re.finditer(re.escape(prefix) + r'([A-Za-z][\w-]*)', answer))

        seen: set[str] = set()
        actions: list[str] = []
        for raw in candidates:
            name = raw.strip().strip('`').strip()
            for prefix in usable_prefixes:
                if name.startswith(prefix):
                    name = name[len(prefix):].strip()
                    break
            name = name.strip('.,!?:;()[]<>').strip()
            if not name:
                continue

            cmd = self.bot.get_command(name) or self.bot.get_command(name.lower())
            if cmd is None or cmd.hidden or not cmd.enabled or cmd.qualified_name in seen:
                continue
            seen.add(cmd.qualified_name)
            actions.append(f'{display_prefix}{cmd.qualified_name}')
            if len(actions) >= MAX_ACTION_BUTTONS:
                break
        return actions

    # -- generation ----------------------------------------------------------

    async def _reply_with_answer(self, message: discord.Message, prompt: str) -> None:
        """Generate an answer using the full reply-chain context and reply with it.

        Replies to ``message`` so the answer links into the chain — the user's next reply
        then threads onto it, and so on without limit.
        """
        async with message.channel.typing():
            history = await self._gather_history(message)
            system = build_assistant_system(
                server_name=message.guild.name if message.guild else None,
                prefix=await self._display_prefix(message),
                website=website,
                support_server=support_server,
                command_catalogue=self._command_catalogue(),
            )
            convo: list[dict[str, str]] = [{'role': 'system', 'content': system}]
            convo.extend(history)
            convo.append({'role': 'user', 'content': prompt})

            # Lower temperature than the default: keep the assistant grounded (fewer invented
            # commands / flows) while still conversational.
            answer = await self.bot.ai.complete(convo, tier=ModelTier.SMART, temperature=0.4)

        if answer is None:
            # Graceful degradation: model down/disabled or timed out.
            await self._safe_reply(message, 'The AI assistant is currently unavailable. Please try again later.')
            return

        # Offer one-click buttons for any real command the answer named (instead of the
        # model role-playing the feature itself).
        actions = await self._extract_commands(answer, message)
        view = (
            AssistantActionView(self.bot, invoker=message.author, source_message=message, actions=actions)
            if actions
            else None
        )

        resp = truncate(f'{ASSISTANT_MARKER}\n{answer}', MAX_REPLY_CHARS)
        sent = await self._safe_reply(message, resp, view=view)
        if view is not None and sent is not None:
            view.message = sent

    async def _safe_reply(
        self, message: discord.Message, content: str, *, view: View | None = None
    ) -> discord.Message | None:
        with contextlib.suppress(discord.HTTPException):
            return await message.reply(content, mention_author=False, view=view)
        return None

    # -- entry points --------------------------------------------------------

    @command(
        'ask',
        aliases=['ai', 'chat'],
        description='Ask the AI assistant a question.',
    )
    @cooldown(1, 12)
    @describe(prompt='What you want to ask.')
    async def ask(self, ctx: Context, *, prompt: str) -> None:
        """Ask the AI assistant a question.

        Reply to the assistant's answer to keep the conversation going — Percy remembers the
        whole thread, no need to type the command again.
        """
        if not self.bot.ai.available:
            await ctx.send_error('The AI assistant is currently unavailable.')
            return

        await self._reply_with_answer(ctx.message, prompt)

    @Cog.listener('on_message')
    async def continue_assistant_thread(self, message: discord.Message) -> None:
        """Continue an ``?ask`` conversation when a user replies to Percy's answer."""
        if message.author.bot or not message.content:
            return

        # Cheap gate: only act on a reply whose (gateway-resolved) parent is our own answer.
        parent = await self._resolve_parent(message, fetch=False)
        if parent is None or not self._is_assistant_message(parent):
            return

        # An explicit `?ask ...` sent as a reply is handled by the command, not here.
        ctx = await self.bot.get_context(message)
        if ctx.command is not None:
            return

        if not self.bot.ai.available:
            return

        bucket = self._thread_cooldown.get_bucket(message)
        if bucket is not None and bucket.update_rate_limit():
            return

        await self._reply_with_answer(message, message.content.strip())
