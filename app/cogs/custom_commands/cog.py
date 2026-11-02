"""Guild-defined custom commands and command aliases.

The pure rules (signature syntax, response placeholders, alias expansion, caps, the gate) live in
``app/services/custom_commands/``. This cog owns the Discord side: management commands, turning bound
argument strings into members/roles/durations with discord.py's converters, and dispatch.

Dispatch is entered from ``Bot.process_commands`` only when no real command matched, so a built-in
command always wins over a custom one, even one a cog added after the custom command was made.
"""

from __future__ import annotations

import copy
import datetime
import math
import random
import re
from contextlib import suppress
from typing import TYPE_CHECKING, Any, ClassVar

import discord
from discord.ext import commands
from discord.ext.commands.view import StringView

from app.cogs.config import parse_permission_names
from app.cogs.custom_commands.ui import CustomCommandManager
from app.core import Cog, Context, Flags, flag, store_true
from app.core.errors import CommandDisabled
from app.core.models import PermissionTemplate, describe, group
from app.core.pagination import LinePaginator
from app.core.permissions import check_rules
from app.services.custom_commands.aliases import expand
from app.services.custom_commands.models import (
    MAX_ALIASES,
    MAX_COMMANDS,
    CommandAlias,
    CustomCommand,
    alias_problem,
    gate_passes,
    parse_cooldown,
    validate_command,
)
from app.services.custom_commands.signature import ENTITY_TYPES, Param, SignatureError, bind, parse_bool, parse_signature
from app.services.custom_commands.template import render
from app.utils import helpers, timetools

if TYPE_CHECKING:
    from app.core import Bot

__all__ = ('CustomCommands',)

_CLEAR = frozenset({'none', 'clear', 'reset', 'off', 'default'})
_ROLE_MENTION_RE = re.compile(r'<@&(\d+)>')


class CustomCommandFlags(Flags):
    args: str = flag(aliases=('signature',), description='Argument signature, e.g. "<user:member> [reason:str...]".')
    perms: str = flag(aliases=('permissions',), description='Permission names needed to run it, or `none`.')
    roles: str = flag(description='Roles that may run it, or `none`.')
    cooldown: str = flag(description='Uses per seconds, e.g. `3/10`, or `none`.')
    aliases: str = flag(description='Other names for it, or `none`.')
    delete: bool | Any = store_true(description='Delete the message that ran it.')
    keep: bool | Any = store_true(description='Stop deleting the message that ran it.')


def tokenize(text: str) -> list[str]:
    """Quote-aware words, the way discord.py splits command arguments."""
    view = StringView(text)
    tokens: list[str] = []
    while True:
        view.skip_ws()
        if view.eof:
            return tokens
        word = view.get_quoted_word()
        if word is None:
            return tokens
        tokens.append(word)


async def _no_op(ctx: Context) -> None:  # pragma: no cover - never invoked
    """Callback of the synthetic command a custom command runs the bot's global checks as."""


class CustomCommands(Cog):
    """Server-made commands with typed arguments, and aliases for existing commands."""

    emoji = '\N{SPEECH BALLOON}'

    #: Help-menu sections (qualified command names; a claim covers subcommands).
    help_sections: ClassVar[dict[str, tuple[str, ...]]] = {
        'Custom Commands': ('customcommand',),
        'Aliases': ('alias',),
    }

    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._cooldowns: dict[tuple[int, int, int, float], commands.CooldownMapping] = {}

    # -- dispatch ---------------------------------------------------------

    async def try_dispatch(self, ctx: Context, *, allow_alias: bool = True) -> bool:
        """Run the guild alias or custom command named ``ctx.invoked_with``; whether there was one."""
        if ctx.guild is None or not ctx.invoked_with or not isinstance(ctx.author, discord.Member):
            return False
        word = ctx.invoked_with.lower()
        if word in self.bot.all_commands:  # a real command always wins
            return False
        index = await self.bot.db.get_custom_command_index(ctx.guild.id)
        alias = index.aliases.get(word)
        if alias is not None:
            if allow_alias:  # depth 1: an alias never expands into another alias
                await self._run_alias(ctx, alias)
            return True
        cc = index.lookup.get(word)
        if cc is None:
            return False
        await self._run_custom(ctx, cc)
        return True

    @staticmethod
    def _raw_args(ctx: Context) -> str:
        return ctx.message.content[len(ctx.prefix or '') + len(ctx.invoked_with or ''):].strip()

    async def _run_alias(self, ctx: Context, alias: CommandAlias) -> None:
        raw = self._raw_args(ctx)
        try:
            tokens = tokenize(raw)
        except commands.ArgumentParsingError as exc:
            await ctx.send_error(str(exc))
            return
        message = copy.copy(ctx.message)
        message.content = f'{ctx.prefix or ""}{expand(alias.expansion, raw, tokens)}'
        new_ctx = await self.bot.get_context(message)
        if new_ctx.command is not None:
            await self.bot.invoke_matched(new_ctx, message)
        else:
            await self.try_dispatch(new_ctx, allow_alias=False)

    async def _passes_global_checks(self, ctx: Context, cc: CustomCommand) -> bool:
        """The bot-wide checks (ignore list, `config` disable, command rules, spam flags) for ``cc``'s name."""
        ctx.command = commands.Command(_no_op, name=cc.name)
        try:
            return await self.bot.can_run(ctx, call_once=True) and await self.bot.can_run(ctx)
        except CommandDisabled as exc:
            await ctx.send(exc.message, delete_after=10, allowed_mentions=discord.AllowedMentions.none())
            return False
        except commands.CheckFailure:
            return False
        finally:
            ctx.command = None

    def _retry_after(self, ctx: Context, cc: CustomCommand) -> float | None:
        if not cc.cooldown_rate or not cc.cooldown_per:
            return None
        key = (cc.guild_id, cc.id, cc.cooldown_rate, cc.cooldown_per)
        mapping = self._cooldowns.get(key)
        if mapping is None:
            mapping = self._cooldowns[key] = commands.CooldownMapping.from_cooldown(
                cc.cooldown_rate, cc.cooldown_per, commands.BucketType.member
            )
        bucket = mapping.get_bucket(ctx.message)
        return bucket.update_rate_limit() if bucket else None

    async def _run_custom(self, ctx: Context, cc: CustomCommand) -> None:
        assert ctx.guild is not None and isinstance(ctx.author, discord.Member)
        if await self.bot.spam_control.is_spam(ctx, ctx.message):
            return
        if not await self._passes_global_checks(ctx, cc):
            return

        # The guild owner passes a deny in `_passes_global_checks`; only an explicit allow skips the gate.
        decision = await check_rules(self.bot.db, ctx.guild, ctx.author, ctx.channel, cc.name)  # type: ignore[arg-type]
        perms = ctx.channel.permissions_for(ctx.author)  # type: ignore[union-attr]
        if not decision.allowed and not gate_passes(
            perms.value, [r.id for r in ctx.author.roles], cc.required_permissions, cc.allowed_role_ids
        ):
            await ctx.send_error(f'You are not allowed to use `{cc.name}`.')
            return

        retry_after = self._retry_after(ctx, cc)
        if retry_after:
            await ctx.send_warning(
                f"Slow down, you're on cooldown. Retry again in **{timetools.humanize_duration(retry_after)}**."
            )
            return

        params = parse_signature(cc.signature)
        usage = f'`{ctx.clean_prefix}{cc.name}{" " + cc.signature if cc.signature else ""}`'
        raw = self._raw_args(ctx)
        try:
            tokens = tokenize(raw)
            values = await self._convert(ctx, params, bind(params, tokens))
        except (SignatureError, commands.BadArgument, commands.ArgumentParsingError) as exc:
            await ctx.send_error(f'{exc}\nUsage: {usage}')
            return

        uses = await self.bot.db.custom_commands.bump_uses(cc.id)
        values |= self.context_values(ctx.author, ctx.channel, ctx.guild, raw, tokens, uses)
        content = render(random.choice(cc.responses), values)
        if content.strip():
            await ctx.send(content, allowed_mentions=self._allowed_mentions(ctx.guild, content))
        if cc.delete_invocation:
            with suppress(discord.HTTPException):
                await ctx.message.delete()

    @staticmethod
    def _allowed_mentions(guild: discord.Guild, content: str) -> discord.AllowedMentions:
        """Users may be pinged; roles only if mentionable; never @everyone/@here."""
        roles = [
            role for rid in dict.fromkeys(_ROLE_MENTION_RE.findall(content))
            if (role := guild.get_role(int(rid))) is not None and role.mentionable
        ]
        return discord.AllowedMentions(everyone=False, users=True, roles=roles, replied_user=False)

    @staticmethod
    def context_values(
        author: discord.abc.User, channel: Any, guild: discord.Guild, raw: str, tokens: list[str], uses: int
    ) -> dict[str, str]:
        """The built-in placeholders (author/channel/guild/args/uses/0-9); shared with the manager's Test."""
        values = {
            'author': author.display_name, 'author.id': str(author.id), 'author.name': author.name,
            'author.mention': author.mention,
            'channel': getattr(channel, 'name', ''), 'channel.id': str(channel.id),
            'channel.name': getattr(channel, 'name', ''), 'channel.mention': f'<#{channel.id}>',
            'guild': guild.name, 'guild.id': str(guild.id), 'guild.name': guild.name,
            'args': raw, 'uses': str(uses),
        }
        values |= {str(i): tokens[i] if i < len(tokens) else '' for i in range(10)}
        return values

    async def _convert(self, ctx: Context, params: tuple[Param, ...], bound: dict[str, str | bool | None]) -> dict[str, str]:
        """Bound raw strings -> placeholder values, through discord.py's converters."""
        values: dict[str, str] = {}
        for param in params:
            raw = bound.get(param.name)
            if param.type in ENTITY_TYPES:
                values |= dict.fromkeys((f'{param.name}.id', f'{param.name}.name', f'{param.name}.mention'), '')
            if isinstance(raw, bool):
                values[param.name] = 'true' if raw else 'false'
                continue
            if raw is None:
                values[param.name] = ''
                continue
            values |= await self._convert_one(ctx, param, raw)
        return values

    @staticmethod
    async def _convert_one(ctx: Context, param: Param, raw: str) -> dict[str, str]:
        name, kind = param.name, param.type
        try:
            if kind == 'str':
                return {name: raw}
            if kind == 'int':
                return {name: str(int(raw))}
            if kind == 'number':
                number = float(raw)
                if not math.isfinite(number):
                    raise ValueError
                return {name: str(int(number)) if number.is_integer() else str(number)}
            if kind == 'bool':
                return {name: 'true' if parse_bool(raw) else 'false'}
            if kind == 'duration':
                now = ctx.message.created_at
                delta = timetools.ShortTime(raw, now=now).dt - now
                return {name: timetools.humanize_duration(delta)}
        except (ValueError, SignatureError):
            raise commands.BadArgument(f'`{raw}` is not a valid {kind} for `{name}`.') from None

        converter: type[commands.Converter[Any]] = {
            'member': commands.MemberConverter,
            'user': commands.UserConverter,
            'role': commands.RoleConverter,
            'channel': commands.GuildChannelConverter,
        }[kind]
        entity = await converter().convert(ctx, raw)
        display = getattr(entity, 'display_name', None) or entity.name
        return {
            name: display, f'{name}.id': str(entity.id), f'{name}.name': entity.name, f'{name}.mention': entity.mention
        }

    # -- management -------------------------------------------------------

    def _real_names(self) -> set[str]:
        return {n.lower() for n in self.bot.all_commands}

    async def _find(self, ctx: Context, name: str) -> CustomCommand | None:
        index = await self.bot.db.get_custom_command_index(ctx.guild.id)
        cc = index.lookup.get(name.lower())
        if cc is None:
            await ctx.send_error(f'There is no custom command called `{name}`.')
        return cc

    async def _flag_changes(self, ctx: Context, flags: CustomCommandFlags) -> dict[str, Any]:
        """Columns set by the management flags. Raises :class:`ValueError` with a user-facing message."""
        changes: dict[str, Any] = {}
        if flags.args is not None:
            changes['signature'] = '' if flags.args.strip().lower() in _CLEAR else flags.args
        if flags.perms is not None:
            value, invalid = parse_permission_names(flags.perms)
            if invalid:
                raise ValueError(f'Unknown permission(s): {", ".join(f"`{i}`" for i in invalid)}.')
            changes['required_permissions'] = value
        if flags.roles is not None:
            role_ids: list[int] = []
            if flags.roles.strip().lower() not in _CLEAR:
                for token in tokenize(flags.roles):
                    try:
                        role_ids.append((await commands.RoleConverter().convert(ctx, token)).id)
                    except commands.BadArgument:
                        raise ValueError(f'`{token}` is not a role.') from None
            changes['allowed_role_ids'] = role_ids
        if flags.cooldown is not None:
            if flags.cooldown.strip().lower() in _CLEAR:
                changes['cooldown_rate'] = changes['cooldown_per'] = None
            else:
                changes['cooldown_rate'], changes['cooldown_per'] = parse_cooldown(flags.cooldown)
        if flags.aliases is not None:
            changes['aliases'] = [] if flags.aliases.strip().lower() in _CLEAR else tokenize(flags.aliases.lower())
        if flags.delete:
            changes['delete_invocation'] = True
        elif flags.keep:
            changes['delete_invocation'] = False
        return changes

    async def save(
        self, guild_id: int, author_id: int, merged: dict[str, Any], *, command_id: int | None
    ) -> CustomCommand | None:
        """Validate and store a command (``command_id=None`` creates). Raises :class:`ValueError` (user-facing).

        Shared by the text commands and the interactive manager; ``None`` means the edited command is gone.
        """
        if command_id is None and await self.bot.db.custom_commands.count_commands(guild_id) >= MAX_COMMANDS:
            raise ValueError(f'This server already has the maximum of {MAX_COMMANDS} custom commands.')
        index = await self.bot.db.get_custom_command_index(guild_id)
        values = validate_command(merged, index, self._real_names(), command_id=command_id)
        if command_id is None:
            return await self.bot.db.custom_commands.create_command(guild_id, author_id, **values)
        return await self.bot.db.custom_commands.update_command(guild_id, command_id, **values)

    async def alias_problem(self, guild_id: int, name: str, expansion: str) -> str | None:
        """Why the alias cannot be saved (rules and the server cap), or ``None``. Shared with the manager."""
        index = await self.bot.db.get_custom_command_index(guild_id)
        problem = alias_problem(name, expansion, index, self._real_names())
        if problem is None and name not in index.aliases and len(index.aliases) >= MAX_ALIASES:
            problem = f'This server already has the maximum of {MAX_ALIASES} aliases.'
        return problem

    @group(
        'customcommand',
        alias='cc',
        description='Make server-only commands with arguments, permissions and cooldowns.',
        guild_only=True,
        invoke_without_command=True,
    )
    async def customcommand(self, ctx: Context) -> None:
        """Custom commands answer with one of their responses.

        Responses may use placeholders: `{param}` (and `.id`/`.name`/`.mention` for members, users, roles
        and channels), `{author}`, `{channel}`, `{guild}`, `{args}`, `{0}`..`{9}`, `{uses}`,
        `{choice:a|b|c}` and `{random:1-100}`. Signatures: `<name:type>` required, `[name:type]`
        optional, `[name:type=default]`, `[name:type...]` takes the rest, `--flag:type`, `--flag`.
        Types: str, int, number, bool, member, user, role, channel, duration.

        Run it without a subcommand to open the interactive manager.
        """
        await CustomCommandManager.open(self, ctx)

    @customcommand.command(
        'create', aliases=['add'], description='Create a custom command.', guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(name='The command name.', response='What it answers (placeholders allowed).')
    async def cc_create(self, ctx: Context, name: str, *, response: str, flags: CustomCommandFlags) -> None:
        """Create a custom command, e.g. `cc create hug {author} hugs {who}! --args "<who:member>"`."""
        try:
            merged = {'name': name.lower(), 'responses': [response]} | await self._flag_changes(ctx, flags)
            cc = await self.save(ctx.guild.id, ctx.author.id, merged, command_id=None)
        except ValueError as exc:
            await ctx.send_error(str(exc))
            return
        assert cc is not None
        await ctx.send_success(f'Created `{cc.name}`. Usage: `{ctx.clean_prefix}{cc.name} {cc.signature}`'.rstrip())

    @customcommand.command(
        'edit', description='Change a custom command.', guild_only=True, user_permissions=PermissionTemplate.manager
    )
    @describe(name='The command to edit.', response='A new response (replaces all responses).')
    async def cc_edit(self, ctx: Context, name: str, *, response: str | None = None, flags: CustomCommandFlags) -> None:
        """Change flags, and with a response replace every response with that one."""
        cc = await self._find(ctx, name)
        if cc is None:
            return
        try:
            merged = cc.editable() | await self._flag_changes(ctx, flags)
            if response:
                merged['responses'] = [response]
            await self.save(ctx.guild.id, ctx.author.id, merged, command_id=cc.id)
        except ValueError as exc:
            await ctx.send_error(str(exc))
            return
        await ctx.send_success(f'Updated `{cc.name}`.')

    @customcommand.command(
        'addresponse', description='Add a random response.', guild_only=True, user_permissions=PermissionTemplate.manager
    )
    @describe(name='The command.', response='Another response; one is picked at random.')
    async def cc_addresponse(self, ctx: Context, name: str, *, response: str) -> None:
        """Add a response; each use picks one at random."""
        cc = await self._find(ctx, name)
        if cc is None:
            return
        try:
            await self.save(ctx.guild.id, ctx.author.id, cc.editable() | {'responses': [*cc.responses, response]}, command_id=cc.id)
        except ValueError as exc:
            await ctx.send_error(str(exc))
            return
        await ctx.send_success(f'`{cc.name}` now has {len(cc.responses) + 1} responses.')

    @customcommand.command(
        'removeresponse', description='Remove a response by number.', guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(name='The command.', index='The response number from `cc raw`.')
    async def cc_removeresponse(self, ctx: Context, name: str, index: int) -> None:
        """Remove response number `index` (see `cc raw`); the last response cannot be removed."""
        cc = await self._find(ctx, name)
        if cc is None:
            return
        if not 1 <= index <= len(cc.responses):
            await ctx.send_error(f'`{cc.name}` has responses 1-{len(cc.responses)}.')
            return
        if len(cc.responses) == 1:
            await ctx.send_error('That is the only response; delete the command instead.')
            return
        responses = [r for i, r in enumerate(cc.responses, start=1) if i != index]
        await self.bot.db.custom_commands.update_command(ctx.guild.id, cc.id, responses=responses)
        await ctx.send_success(f'Removed response {index} of `{cc.name}`.')

    @customcommand.command(
        'rename', description='Rename a custom command.', guild_only=True, user_permissions=PermissionTemplate.manager
    )
    @describe(name='The command.', new_name='Its new name.')
    async def cc_rename(self, ctx: Context, name: str, new_name: str) -> None:
        """Rename a custom command (rules and `config` disables follow the name, so re-add them)."""
        cc = await self._find(ctx, name)
        if cc is None:
            return
        try:
            await self.save(ctx.guild.id, ctx.author.id, cc.editable() | {'name': new_name.lower()}, command_id=cc.id)
        except ValueError as exc:
            await ctx.send_error(str(exc))
            return
        await ctx.send_success(f'Renamed `{cc.name}` to `{new_name.lower()}`.')

    @customcommand.command(
        'delete', aliases=['remove'], description='Delete a custom command.', guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(name='The command.')
    async def cc_delete(self, ctx: Context, name: str) -> None:
        """Delete a custom command."""
        cc = await self._find(ctx, name)
        if cc is None:
            return
        await self.bot.db.custom_commands.delete_command(ctx.guild.id, cc.id)
        await ctx.send_success(f'Deleted `{cc.name}`.')

    @customcommand.command('list', description="Lists this server's custom commands.", guild_only=True)
    async def cc_list(self, ctx: Context) -> None:
        """Lists every custom command with its usage."""
        index = await self.bot.db.get_custom_command_index(ctx.guild.id)
        if not index.commands:
            await ctx.send_info('This server has no custom commands yet.')
            return
        entries = [f'`{cc.name}{" " + cc.signature if cc.signature else ""}`' for cc in index.commands]
        embed = discord.Embed(title='Custom Commands', color=helpers.Colour.white())
        await LinePaginator.start(ctx, entries=entries, per_page=15, embed=embed, location='description', numerate=True)

    @customcommand.command('info', description='Shows how a custom command is set up.', guild_only=True)
    @describe(name='The command.')
    async def cc_info(self, ctx: Context, name: str) -> None:
        """Shows a custom command's usage, gate, cooldown, owner and use count."""
        found = await self._find(ctx, name)
        if found is None:
            return
        cc = await self.bot.db.custom_commands.get_command(ctx.guild.id, found.id) or found
        perms = discord.Permissions(cc.required_permissions or 0)
        perm_names = ', '.join(n.replace('_', ' ').title() for n, v in perms if v) or 'none'
        embed = discord.Embed(title=f'{ctx.clean_prefix}{cc.name} {cc.signature}'.strip(), color=helpers.Colour.white())
        embed.add_field(name='Aliases', value=', '.join(f'`{a}`' for a in cc.aliases) or 'none')
        embed.add_field(name='Responses', value=str(len(cc.responses)))
        embed.add_field(name='Uses', value=str(cc.uses))
        embed.add_field(name='Permissions', value=perm_names)
        embed.add_field(name='Roles', value=', '.join(f'<@&{r}>' for r in cc.allowed_role_ids) or 'anyone')
        cooldown = f'{cc.cooldown_rate} per {timetools.humanize_duration(cc.cooldown_per)}' if cc.cooldown_rate else 'none'
        embed.add_field(name='Cooldown', value=cooldown)
        embed.add_field(name='Deletes invocation', value='yes' if cc.delete_invocation else 'no')
        embed.add_field(name='Owner', value=f'<@{cc.owner_id}>' if cc.owner_id else 'unknown')
        if cc.created_at:
            embed.timestamp = cc.created_at.replace(tzinfo=datetime.UTC)
        await ctx.send(embed=embed)

    @customcommand.command('raw', description="Shows a custom command's responses unrendered.", guild_only=True)
    @describe(name='The command.')
    async def cc_raw(self, ctx: Context, name: str) -> None:
        """Shows every response exactly as written, numbered for `cc removeresponse`."""
        cc = await self._find(ctx, name)
        if cc is None:
            return
        entries = [discord.utils.escape_mentions(discord.utils.escape_markdown(r)) for r in cc.responses]
        embed = discord.Embed(title=f'Responses of {cc.name}', color=helpers.Colour.white())
        await LinePaginator.start(ctx, entries=entries, per_page=5, embed=embed, location='description', numerate=True)

    # -- aliases ----------------------------------------------------------

    @group(
        'alias',
        description='Short names that expand into a longer command.',
        guild_only=True,
        invoke_without_command=True,
    )
    async def alias(self, ctx: Context) -> None:
        """An alias expands into a command: `{args}` is everything typed after it and `{0}`..`{9}` single
        words. Without a placeholder the arguments are appended. An alias cannot point to another alias.
        """
        await ctx.send_help('alias')

    @alias.command('add', description='Add or replace an alias.', guild_only=True, user_permissions=PermissionTemplate.manager)
    @describe(name='The alias.', expansion='What it expands to, e.g. `ban {0} spamming`.')
    async def alias_add(self, ctx: Context, name: str, *, expansion: str) -> None:
        """Add an alias, e.g. `alias add spamban ban {0} spamming`."""
        if problem := await self.alias_problem(ctx.guild.id, name.lower(), expansion):
            await ctx.send_error(problem)
            return
        await self.bot.db.custom_commands.put_alias(ctx.guild.id, name.lower(), expansion.strip(), ctx.author.id)
        await ctx.send_success(f'`{ctx.clean_prefix}{name.lower()}` now runs `{ctx.clean_prefix}{expansion.strip()}`.')

    @alias.command(
        'remove', aliases=['delete'], description='Remove an alias.', guild_only=True,
        user_permissions=PermissionTemplate.manager,
    )
    @describe(name='The alias.')
    async def alias_remove(self, ctx: Context, name: str) -> None:
        """Remove an alias."""
        if not await self.bot.db.custom_commands.delete_alias(ctx.guild.id, name.lower()):
            await ctx.send_error(f'There is no alias called `{name}`.')
            return
        await ctx.send_success(f'Removed the alias `{name.lower()}`.')

    @alias.command('list', description="Lists this server's aliases.", guild_only=True)
    async def alias_list(self, ctx: Context) -> None:
        """Lists every alias and what it expands to."""
        index = await self.bot.db.get_custom_command_index(ctx.guild.id)
        if not index.aliases:
            await ctx.send_info('This server has no aliases yet.')
            return
        entries = [
            f'`{a.alias}` -> `{discord.utils.escape_markdown(a.expansion)[:200]}`' for a in index.aliases.values()
        ]
        embed = discord.Embed(title='Aliases', color=helpers.Colour.white())
        await LinePaginator.start(ctx, entries=entries, per_page=15, embed=embed, location='description', numerate=True)


async def setup(bot: Bot) -> None:
    await bot.add_cog(CustomCommands(bot))
