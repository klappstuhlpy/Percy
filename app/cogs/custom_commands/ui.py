"""The interactive custom-command manager: one Components V2 card, opened by ``cc`` without a subcommand.

Screens: the command list (paginated), one command's details, its responses, who may use it, a delete
confirmation, and the alias list. Every edit goes through the cog's ``save``/``alias_problem`` (the same
validation the text commands use) and the ``db.custom_commands`` repository. A failed edit re-shows
the screen with the exact validation message and keeps what was typed for the next attempt.

Anyone may browse; edit controls only render for, and only act for, members with Manage Server.
"""

from __future__ import annotations

import datetime
import logging
import random
from contextlib import suppress
from typing import TYPE_CHECKING, Any, NamedTuple

import discord
from discord.ui.select import BaseSelect

from app.core.components_v2 import Accent, make_notice
from app.core.pagination import BasePaginatorV2
from app.services.custom_commands.manager import (
    describe_cooldown,
    describe_signature,
    placeholder_help,
    placeholder_hint,
    sample_values,
)
from app.services.custom_commands.models import (
    MAX_RESPONSE_LENGTH,
    MAX_RESPONSES,
    CustomCommand,
    CustomCommandIndex,
    parse_cooldown,
)
from app.services.custom_commands.signature import MAX_SIGNATURE_LENGTH, Param, SignatureError, parse_signature
from app.services.custom_commands.template import render
from app.utils.formats import truncate
from config import Emojis

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from app.cogs.custom_commands.cog import CustomCommands
    from app.core import Context

__all__ = ('COMMON_PERMISSIONS', 'CustomCommandManager', 'Field', 'FormModal')

log = logging.getLogger(__name__)

#: The permissions offered in the "who can use it" select (flag name, label).
COMMON_PERMISSIONS: tuple[tuple[str, str], ...] = (
    ('manage_guild', 'Manage Server'),
    ('manage_messages', 'Manage Messages'),
    ('manage_roles', 'Manage Roles'),
    ('manage_channels', 'Manage Channels'),
    ('manage_nicknames', 'Manage Nicknames'),
    ('kick_members', 'Kick Members'),
    ('ban_members', 'Ban Members'),
    ('moderate_members', 'Timeout Members'),
    ('mention_everyone', 'Mention @everyone'),
    ('administrator', 'Administrator'),
)
_COMMON_MASK = discord.Permissions(**dict.fromkeys((n for n, _ in COMMON_PERMISSIONS), True)).value

SIGNATURE_EXAMPLE = '<who:member> [reason:str...]  or  [times:int=1] --loud'
ALIASES_PER_PAGE = 10
NO_MENTIONS = discord.AllowedMentions.none()


class Field(NamedTuple):
    key: str
    label: str
    default: str = ''
    description: str | None = None
    placeholder: str | None = None
    paragraph: bool = False
    required: bool = True
    max_length: int | None = None


class FormModal(discord.ui.Modal):
    """A modal of labelled text inputs, with optional helper text on top, that hands its values to a callback."""

    def __init__(
        self,
        title: str,
        fields: Sequence[Field],
        on_submit: Callable[[discord.Interaction, dict[str, str]], Awaitable[None]],
        *,
        intro: str | None = None,
    ) -> None:
        super().__init__(title=truncate(title, 45), timeout=900)
        self._callback = on_submit
        self.inputs: dict[str, discord.ui.TextInput[Any]] = {}
        if intro:
            self.add_item(discord.ui.TextDisplay(intro))
        for field in fields:
            text_input: discord.ui.TextInput[Any] = discord.ui.TextInput(
                style=discord.TextStyle.paragraph if field.paragraph else discord.TextStyle.short,
                default=field.default or None,
                placeholder=field.placeholder and truncate(field.placeholder, 100),
                required=field.required,
                max_length=field.max_length,
            )
            self.inputs[field.key] = text_input
            description = field.description and truncate(field.description, 100)
            self.add_item(discord.ui.Label(text=truncate(field.label, 45), description=description, component=text_input))

    async def on_submit(self, interaction: discord.Interaction, /) -> None:
        await self._callback(interaction, {key: text_input.value for key, text_input in self.inputs.items()})

    async def on_error(self, interaction: discord.Interaction, error: Exception, /) -> None:
        await interaction.client.handle_interaction_error(interaction, error)  # type: ignore[attr-defined]


def _button(label: str, callback: Any, *, style: discord.ButtonStyle = discord.ButtonStyle.grey,
            disabled: bool = False, emoji: str | None = None) -> discord.ui.Button[Any]:
    button: discord.ui.Button[Any] = discord.ui.Button(label=label, style=style, disabled=disabled, emoji=emoji)
    button.callback = callback  # type: ignore[assignment]
    return button


def _params(cc: CustomCommand) -> tuple[tuple[Param, ...], str | None]:
    """The parsed signature, or no params and the error for a row whose signature no longer parses."""
    try:
        return parse_signature(cc.signature), None
    except SignatureError as exc:
        return (), str(exc)


def _same_responses(shown: CustomCommand, fresh: CustomCommand) -> None:
    """Refuse a by-number response edit when the list changed since it was shown (another edit landed)."""
    if shown.responses != fresh.responses:
        raise ValueError('The responses changed in the meantime; check the list and pick again.')


def _quote(text: str, limit: int) -> str:
    """A response shown as plain text: markdown and mentions escaped, newlines folded, truncated."""
    flat = ' '.join(text.split())
    return discord.utils.escape_mentions(discord.utils.escape_markdown(truncate(flat, limit)))


class CustomCommandManager(BasePaginatorV2[CustomCommand]):
    """List, inspect, create, edit and delete a guild's custom commands (and aliases) in one card."""

    def __init__(
        self,
        cog: CustomCommands,
        *,
        author: discord.abc.User,
        guild_id: int,
        prefix: str,
        index: CustomCommandIndex,
        can_manage: bool,
        timeout: float = 600,
    ) -> None:
        super().__init__(entries=index.commands, per_page=5, timeout=timeout, members=author)
        self.cog = cog
        self.author = author
        self.guild_id = guild_id
        self.prefix = prefix
        self.index = index
        self.can_manage = can_manage
        self.screen = 'list'
        self.selected: CustomCommand | None = None
        self.notice: str | None = None
        #: form id -> the values a rejected submit carried, offered again when the form reopens.
        self.drafts: dict[str, dict[str, str]] = {}
        self.alias_page = 0

    @classmethod
    async def open(cls, cog: CustomCommands, ctx: Context) -> CustomCommandManager:
        assert ctx.guild is not None
        perms = ctx.channel.permissions_for(ctx.author)  # type: ignore[arg-type]
        self = cls(
            cog, author=ctx.author, guild_id=ctx.guild.id, prefix=ctx.clean_prefix,
            index=await cog.bot.db.get_custom_command_index(ctx.guild.id), can_manage=perms.manage_guild,
        )
        await self.build()
        self.message = await ctx.send(view=self, allowed_mentions=NO_MENTIONS)
        return self

    # -- rendering ----------------------------------------------------------

    async def build(self) -> None:
        if self.screen == 'list':
            await super().build()
        else:
            accent = Accent.error.colour if self.screen == 'delete' else self.accent_colour
            container = discord.ui.Container(accent_colour=accent)
            for item in getattr(self, f'_screen_{self.screen}')():
                container.add_item(item)
            self.clear_items()
            self.add_item(container)
        self.notice = None  # shown once

    def _head(self, text: str) -> list[discord.ui.Item[Any]]:
        items: list[discord.ui.Item[Any]] = [discord.ui.TextDisplay(text)]
        if self.notice:
            items.append(discord.ui.TextDisplay(self.notice))
        items.append(discord.ui.Separator())
        return items

    def _usage(self, cc: CustomCommand) -> str:
        return f'`{self.prefix}{cc.name}{" " + cc.signature if cc.signature else ""}`'

    async def format_page(self, entries: list[CustomCommand], /) -> list[discord.ui.Item[Any]]:
        hint = 'Pick a command below to see it, test it' + (' or change it.' if self.can_manage else '.')
        items = self._head(f'## Custom commands\n-# {hint}')
        if not entries:
            more = ' Press **New command** to make the first one.' if self.can_manage else (
                ' Someone with Manage Server can add some.'
            )
            items.append(discord.ui.TextDisplay(f'*This server has no custom commands yet.*{more}'))
            return items
        lines = []
        for number, cc in enumerate(entries, self.numerate_start):
            responses = f'{len(cc.responses)} response' + ('s' if len(cc.responses) != 1 else '')
            lines.append(f'**{number}.** {self._usage(cc)}\n-# {responses} \N{BULLET} used {cc.uses}x')
        items.append(discord.ui.TextDisplay('\n'.join(lines)))
        return items

    def footer_items(self) -> list[discord.ui.Item[Any]]:
        items: list[discord.ui.Item[Any]] = []
        page = self.pages[self._current_page]
        if page:
            select: discord.ui.Select[Any] = discord.ui.Select(
                placeholder='Open a command...',
                options=[
                    discord.SelectOption(
                        label=truncate(f'{self.prefix}{cc.name}', 100), value=str(cc.id),
                        description=truncate(cc.signature or 'no arguments', 100),
                    )
                    for cc in page
                ],
            )
            select.callback = lambda i: self.open_command(i, int(select.values[0]))  # type: ignore[assignment]
            items.append(discord.ui.ActionRow(select))
        buttons = [_button('Aliases', self._go('aliases')), _button('Close', self.close)]
        if self.can_manage:
            buttons.insert(0, _button('New command', self.new_command, style=discord.ButtonStyle.green))
        items.append(discord.ui.ActionRow(*buttons))
        count = len(self.entries)
        items.append(discord.ui.TextDisplay(
            f'-# Page {self.current_page}/{self.total_pages} \N{BULLET} {count} command' + ('s' if count != 1 else '')
        ))
        return items

    def _screen_detail(self) -> list[discord.ui.Item[Any]]:
        cc = self.selected
        assert cc is not None
        items = self._head(f'## {self.prefix}{cc.name}\n-# Usage: {self._usage(cc)}')

        params, error = _params(cc)
        args = '\n'.join(f'- {a}' for a in describe_signature(params))
        if error:
            args = f'*Signature invalid: {error}* Fix it with **Arguments**.'
        preview = max(30, 1500 // max(len(cc.responses), 1))
        responses = truncate('\n'.join(f'`{i}.` {_quote(r, preview)}' for i, r in enumerate(cc.responses, 1)), 1800)
        body = [
            '**Arguments**\n' + (truncate(args, 900) or '*None: it takes no arguments.*'),
            f'**Responses** ({len(cc.responses)}, one picked at random)\n{responses}',
            f'**Who can use it**\n{self._gate_text(cc)}',
            f'**Cooldown:** {describe_cooldown(cc.cooldown_rate, cc.cooldown_per)}',
            f'**Deletes the message that ran it:** {"yes" if cc.delete_invocation else "no"}',
            '**Aliases:** ' + (' '.join(f'`{a}`' for a in cc.aliases) or 'none'),
        ]
        footer = f'-# Used {cc.uses}x'
        if cc.owner_id:
            footer += f' \N{BULLET} made by <@{cc.owner_id}>'
        if cc.created_at:
            footer += f' \N{BULLET} created {discord.utils.format_dt(cc.created_at.replace(tzinfo=datetime.UTC), "R")}'
        items.append(discord.ui.TextDisplay('\n\n'.join(body) + '\n' + footer))
        items.append(discord.ui.Separator())

        test = _button('Test', self.test, emoji='\N{BLACK RIGHT-POINTING TRIANGLE}')
        back = _button('Back', self._go('list'))
        if not self.can_manage:
            items.append(discord.ui.ActionRow(test, back))
            return items
        items.append(discord.ui.ActionRow(
            _button('Name & aliases', self.edit_names),
            _button('Arguments', self.edit_signature),
            _button('Responses', self._go('responses')),
            _button('Who can use it', self._go('permissions')),
            _button('Cooldown', self.edit_cooldown),
        ))
        toggle = 'Stop deleting the trigger' if cc.delete_invocation else 'Delete the trigger message'
        items.append(discord.ui.ActionRow(
            _button(toggle, self.toggle_delete),
            test,
            _button('Delete', self._go('delete'), style=discord.ButtonStyle.red),
            back,
        ))
        return items

    @staticmethod
    def _gate_text(cc: CustomCommand) -> str:
        perms = [n.replace('_', ' ').replace('guild', 'server').title() for n, v in discord.Permissions(cc.required_permissions or 0) if v]
        roles = ' '.join(f'<@&{r}>' for r in cc.allowed_role_ids)
        if not perms and not roles:
            return 'Everyone.'
        parts = []
        if perms:
            parts.append('members with ' + ', '.join(f'**{p}**' for p in perms))
        if roles:
            parts.append(f'members with one of {roles}')
        text = ' or '.join(parts)
        return text[0].upper() + text[1:] + '. Administrators always can.'

    def _screen_responses(self) -> list[discord.ui.Item[Any]]:
        cc = self.selected
        assert cc is not None
        items = self._head(
            f'## Responses of {self.prefix}{cc.name}\n'
            '-# Each run picks one at random. Placeholders like `{author}` are filled in when it runs; '
            'use **Test** on the previous screen to preview.'
        )
        limit = max(60, 2600 // max(len(cc.responses), 1))
        items.append(discord.ui.TextDisplay(
            truncate('\n'.join(f'`{i}.` {_quote(r, limit)}' for i, r in enumerate(cc.responses, 1)), 3000)
        ))
        options = [
            discord.SelectOption(label=f'Response {i}', value=str(i - 1), description=truncate(' '.join(r.split()), 100))
            for i, r in enumerate(cc.responses, 1)
        ]
        edit: discord.ui.Select[Any] = discord.ui.Select(placeholder='Edit a response...', options=options)
        edit.callback = lambda i: self.edit_response(i, int(edit.values[0]))  # type: ignore[assignment]
        remove: discord.ui.Select[Any] = discord.ui.Select(
            placeholder='Remove a response...' if len(options) > 1 else 'The only response cannot be removed',
            options=options, disabled=len(options) < 2,
        )
        remove.callback = lambda i: self.remove_response(i, int(remove.values[0]))  # type: ignore[assignment]
        items += [discord.ui.ActionRow(edit), discord.ui.ActionRow(remove)]
        full = len(cc.responses) >= MAX_RESPONSES
        items.append(discord.ui.ActionRow(
            _button(f'Add response ({len(cc.responses)}/{MAX_RESPONSES})', self.add_response,
                    style=discord.ButtonStyle.green, disabled=full),
            _button('Back', self._go('detail')),
        ))
        return items

    def _screen_permissions(self) -> list[discord.ui.Item[Any]]:
        cc = self.selected
        assert cc is not None
        items = self._head(
            f'## Who can use {self.prefix}{cc.name}\n'
            '-# Members need **all** selected permissions **or one** of the selected roles. '
            'Leave both empty so everyone can use it.'
        )
        items.append(discord.ui.TextDisplay(f'**Right now:** {self._gate_text(cc)}'))
        current = discord.Permissions(cc.required_permissions or 0)
        perms: discord.ui.Select[Any] = discord.ui.Select(
            placeholder='Required permissions (none)', min_values=0, max_values=len(COMMON_PERMISSIONS),
            options=[
                discord.SelectOption(label=label, value=name, default=getattr(current, name))
                for name, label in COMMON_PERMISSIONS
            ],
        )
        perms.callback = lambda i: self.set_permissions(i, perms.values)  # type: ignore[assignment]
        roles: discord.ui.RoleSelect[Any] = discord.ui.RoleSelect(
            placeholder='Allowed roles (none)', min_values=0, max_values=25,
            default_values=[discord.Object(r) for r in cc.allowed_role_ids[:25]],
        )
        roles.callback = lambda i: self.set_roles(i, [r.id for r in roles.values])  # type: ignore[assignment]
        items += [discord.ui.ActionRow(perms), discord.ui.ActionRow(roles)]
        items.append(discord.ui.ActionRow(
            _button('Let everyone use it', self.clear_gate), _button('Back', self._go('detail'))
        ))
        return items

    def _screen_delete(self) -> list[discord.ui.Item[Any]]:
        cc = self.selected
        assert cc is not None
        items = self._head(
            f'## Delete {self.prefix}{cc.name}?\n'
            f'This removes the command and its {len(cc.responses)} response(s). **It cannot be undone.**\n'
            '-# Command rules or `config` disables set for this name stay; they apply again if the name is reused.'
        )
        items.append(discord.ui.ActionRow(
            _button('Delete for good', self.delete, style=discord.ButtonStyle.red), _button('Cancel', self._go('detail'))
        ))
        return items

    def _screen_aliases(self) -> list[discord.ui.Item[Any]]:
        aliases = list(self.index.aliases.values())
        pages = max(1, -(-len(aliases) // ALIASES_PER_PAGE))
        self.alias_page = max(0, min(self.alias_page, pages - 1))
        page = aliases[self.alias_page * ALIASES_PER_PAGE:(self.alias_page + 1) * ALIASES_PER_PAGE]
        items = self._head(
            '## Aliases\n-# A short name that runs another command, e.g. `sb` runs `ban {0} spamming`. '
            '`{args}` is everything typed after it, `{0}`..`{9}` single words; without them the words are appended.'
        )
        lines = [f'`{self.prefix}{a.alias}` \N{RIGHTWARDS ARROW} {_quote(a.expansion, 80)}' for a in page]
        items.append(discord.ui.TextDisplay('\n'.join(lines) or '*No aliases yet.*'))
        if pages > 1:
            items.append(discord.ui.ActionRow(
                _button('Previous', self._alias_turn(-1), disabled=self.alias_page == 0),
                _button(f'Page {self.alias_page + 1}/{pages}', self._alias_turn(0), disabled=True),
                _button('Next', self._alias_turn(1), disabled=self.alias_page >= pages - 1),
            ))
        back = _button('Back', self._go('list'))
        if not self.can_manage:
            items.append(discord.ui.ActionRow(back))
            return items
        if page:
            remove: discord.ui.Select[Any] = discord.ui.Select(
                placeholder='Remove an alias...',
                options=[discord.SelectOption(label=a.alias, description=truncate(a.expansion, 100)) for a in page],
            )
            remove.callback = lambda i: self.remove_alias(i, remove.values[0])  # type: ignore[assignment]
            items.append(discord.ui.ActionRow(remove))
        items.append(discord.ui.ActionRow(_button('Add alias', self.add_alias, style=discord.ButtonStyle.green), back))
        return items

    # -- navigation -----------------------------------------------------------

    def _go(self, screen: str) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            if screen in ('responses', 'permissions', 'delete') and not await self._guard(interaction):
                return
            self.screen = screen
            if screen in ('list', 'detail'):
                self.drafts.clear()
            await self.refresh(interaction)

        return callback

    def _alias_turn(self, step: int) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.alias_page += step
            await self.refresh(interaction)

        return callback

    async def open_command(self, interaction: discord.Interaction, command_id: int) -> None:
        await interaction.response.defer()
        fresh = await self.cog.bot.db.custom_commands.get_command(self.guild_id, command_id)  # fresh `uses`
        if fresh is None:
            await self._reload()
            self.notice = f'{Emojis.error} That command was deleted in the meantime.'
        else:
            self.selected, self.screen = fresh, 'detail'
        await self.refresh(interaction)

    async def close(self, interaction: discord.Interaction) -> None:
        self.stop()
        await interaction.response.defer()
        if interaction.message is not None:
            with suppress(discord.HTTPException):
                await interaction.message.delete()

    async def on_timeout(self) -> None:
        if self.message is None:
            return
        for child in self.walk_children():
            if isinstance(child, (discord.ui.Button, BaseSelect)):
                child.disabled = True
        with suppress(discord.HTTPException):
            await self.message.edit(view=self)

    # -- writes ---------------------------------------------------------------

    async def _guard(self, interaction: discord.Interaction) -> bool:
        """Manage Server, checked again at the moment of the edit (the session can outlive a role change)."""
        if self.can_manage and interaction.permissions.manage_guild:
            return True
        await interaction.response.send_message(
            f'{Emojis.error} You need the **Manage Server** permission to change custom commands.', ephemeral=True
        )
        return False

    async def _reload(self) -> None:
        self.index = await self.cog.bot.db.get_custom_command_index(self.guild_id)
        self.set_entries(self.index.commands)

    async def _run(
        self, interaction: discord.Interaction, action: Callable[[], Awaitable[str]], *,
        form: str | None = None, values: dict[str, str] | None = None,
    ) -> None:
        """Defer, run an edit, then re-render with its success line or its exact validation error."""
        if not await self._guard(interaction):
            return
        await interaction.response.defer()
        try:
            done = await action()
        except ValueError as exc:
            if form is not None and values is not None:
                self.drafts[form] = values
                self.notice = f'{Emojis.error} {exc}\n-# Nothing was saved. Open the same form again; what you typed is kept.'
            else:
                self.notice = f'{Emojis.error} {exc}'
        except Exception:
            log.exception('Custom-command manager edit failed (guild=%s, user=%s)', self.guild_id, self.author.id)
            self.notice = f'{Emojis.error} Something went wrong, so nothing was saved. Please try again.'
        else:
            if form is not None:
                self.drafts.pop(form, None)
            self.notice = f'{Emojis.success} {done}'
        await self.refresh(interaction)

    async def _update(self, change: dict[str, Any] | Callable[[CustomCommand], dict[str, Any]]) -> CustomCommand:
        """Apply ``change`` (or ``change(fresh_row)``) onto the row as stored now, not the screen's snapshot,
        so two open managers do not overwrite each other's edits."""
        assert self.selected is not None
        fresh = await self.cog.bot.db.custom_commands.get_command(self.guild_id, self.selected.id)
        saved = None
        if fresh is not None:
            changes = change(fresh) if callable(change) else change
            saved = await self.cog.save(self.guild_id, self.author.id, fresh.editable() | changes, command_id=fresh.id)
        await self._reload()
        if saved is None:
            self.selected, self.screen = None, 'list'
            raise ValueError('That command was deleted in the meantime.')
        self.selected = saved
        return saved

    async def _form(
        self, interaction: discord.Interaction, form: str, title: str, fields: Sequence[Field],
        apply: Callable[[dict[str, str]], Awaitable[str]], *, intro: str | None = None,
    ) -> None:
        """Open a modal (pre-filled with a rejected draft, if any) whose submit runs ``apply``."""
        if not await self._guard(interaction):
            return
        draft = self.drafts.get(form, {})
        fields = [f._replace(default=draft.get(f.key, f.default)) for f in fields]

        async def submit(modal_interaction: discord.Interaction, values: dict[str, str]) -> None:
            await self._run(modal_interaction, lambda: apply(values), form=form, values=values)

        await interaction.response.send_modal(FormModal(title, fields, submit, intro=intro))

    # create

    async def new_command(self, interaction: discord.Interaction) -> None:
        async def apply(values: dict[str, str]) -> str:
            merged = {
                'name': values['name'].strip().lower(),
                'signature': values['signature'],
                'responses': [values['response']],
            }
            cc = await self.cog.save(self.guild_id, self.author.id, merged, command_id=None)
            assert cc is not None
            await self._reload()
            self.selected, self.screen = cc, 'detail'
            return f'Created {self._usage(cc)}. Try it with **Test**, or refine it with the buttons below.'

        await self._form(interaction, 'new', 'New custom command', [
            Field('name', 'Name', description='What people type after the prefix. Lowercase letters, digits, _ or -.',
                  placeholder='hug', max_length=32),
            Field('signature', 'Arguments (optional)', required=False, max_length=MAX_SIGNATURE_LENGTH,
                  description='<name:type> required, [name:type] optional. You can add these later.',
                  placeholder=SIGNATURE_EXAMPLE),
            Field('response', 'Response', paragraph=True, max_length=MAX_RESPONSE_LENGTH,
                  description='What the bot answers. Placeholders are filled in when it runs.',
                  placeholder='{author} hugs {who}! ({choice:aww|so sweet})'),
        ], apply, intro=placeholder_help(()) + '\nArgument types: `str` `int` `number` `bool` `member` `user` `role` '
                                              '`channel` `duration`. An argument `<who:member>` becomes `{who}`.')

    # edit

    async def edit_names(self, interaction: discord.Interaction) -> None:
        cc = self.selected
        assert cc is not None

        async def apply(values: dict[str, str]) -> str:
            name = values['name'].strip().lower()
            saved = await self._update({'name': name, 'aliases': values['aliases'].replace(',', ' ').lower().split()})
            note = ' Command rules and disables are keyed by name; re-add them for the new name.' if name != cc.name else ''
            return f'Saved the name and aliases of {self._usage(saved)}.{note}'

        await self._form(interaction, 'names', f'Name & aliases of {cc.name}', [
            Field('name', 'Name', cc.name, max_length=32, description='Lowercase letters, digits, _ or -.'),
            Field('aliases', 'Other names (optional)', ' '.join(cc.aliases), required=False, max_length=400,
                  description='Up to 10, separated by spaces. They run this same command.', placeholder='cuddle snuggle'),
        ], apply)

    async def edit_signature(self, interaction: discord.Interaction) -> None:
        cc = self.selected
        assert cc is not None

        async def apply(values: dict[str, str]) -> str:
            saved = await self._update({'signature': values['signature']})
            return f'Saved. Usage is now {self._usage(saved)}.'

        await self._form(interaction, 'signature', f'Arguments of {cc.name}', [
            Field('signature', 'Arguments', cc.signature, required=False, max_length=MAX_SIGNATURE_LENGTH,
                  paragraph=True, placeholder=SIGNATURE_EXAMPLE, description='Leave empty for a command without arguments.'),
        ], apply, intro=(
            '`<name:type>` required \N{BULLET} `[name:type]` optional \N{BULLET} `[name:type=default]` with a default '
            '\N{BULLET} `[name:type...]` takes the rest \N{BULLET} `--name:type` a flag \N{BULLET} `--name` an on/off switch\n'
            'Types: `str` `int` `number` `bool` `member` `user` `role` `channel` `duration`\n'
            '-# Each argument becomes a placeholder: `<who:member>` gives `{who}` and `{who.mention}`.'
        ))

    async def edit_cooldown(self, interaction: discord.Interaction) -> None:
        cc = self.selected
        assert cc is not None
        current = f'{cc.cooldown_rate}/{cc.cooldown_per:g}' if cc.cooldown_rate and cc.cooldown_per else ''

        async def apply(values: dict[str, str]) -> str:
            raw = values['cooldown'].strip()
            rate, per = parse_cooldown(raw) if raw else (None, None)
            saved = await self._update({'cooldown_rate': rate, 'cooldown_per': per})
            return f'Cooldown: {describe_cooldown(saved.cooldown_rate, saved.cooldown_per)}.'

        await self._form(interaction, 'cooldown', f'Cooldown of {cc.name}', [
            Field('cooldown', 'Uses / seconds', current, required=False, max_length=20, placeholder='3/10',
                  description='3/10 = each member may use it 3 times per 10 seconds. Empty = no cooldown.'),
        ], apply)

    async def toggle_delete(self, interaction: discord.Interaction) -> None:
        cc = self.selected
        assert cc is not None

        async def apply() -> str:
            saved = await self._update(lambda fresh: {'delete_invocation': not fresh.delete_invocation})
            return 'The message that runs it will now be deleted.' if saved.delete_invocation else (
                'The message that runs it is kept now.'
            )

        await self._run(interaction, apply)

    # responses

    def _response_field(self, default: str = '') -> Field:
        assert self.selected is not None
        params, _ = _params(self.selected)
        return Field('response', 'Response', default, paragraph=True, max_length=MAX_RESPONSE_LENGTH,
                     placeholder=placeholder_hint(params), description='Placeholders are filled in when the command runs.')

    def _response_intro(self) -> str:
        assert self.selected is not None
        params, error = _params(self.selected)
        help_text = placeholder_help(params)
        return f'**Signature invalid:** {error}\n{help_text}' if error else help_text

    async def add_response(self, interaction: discord.Interaction) -> None:
        cc = self.selected
        assert cc is not None

        async def apply(values: dict[str, str]) -> str:
            saved = await self._update(lambda fresh: {'responses': [*fresh.responses, values['response']]})
            return f'Added response {len(saved.responses)}.'

        await self._form(interaction, 'response:add', f'New response for {cc.name}', [self._response_field()], apply,
                         intro=self._response_intro())

    async def edit_response(self, interaction: discord.Interaction, position: int) -> None:
        cc = self.selected
        assert cc is not None and position < len(cc.responses)

        async def apply(values: dict[str, str]) -> str:
            def change(fresh: CustomCommand) -> dict[str, Any]:
                _same_responses(cc, fresh)
                responses = list(fresh.responses)
                responses[position] = values['response']
                return {'responses': responses}

            await self._update(change)
            return f'Saved response {position + 1}.'

        await self._form(interaction, f'response:{position}', f'Response {position + 1} of {cc.name}',
                         [self._response_field(cc.responses[position])], apply, intro=self._response_intro())

    async def remove_response(self, interaction: discord.Interaction, position: int) -> None:
        cc = self.selected
        assert cc is not None

        async def apply() -> str:
            def change(fresh: CustomCommand) -> dict[str, Any]:
                _same_responses(cc, fresh)
                if len(fresh.responses) < 2:
                    raise ValueError('That is the only response; delete the command instead.')
                return {'responses': [r for i, r in enumerate(fresh.responses) if i != position]}

            await self._update(change)
            return f'Removed response {position + 1}.'

        await self._run(interaction, apply)

    # who can use it

    async def set_permissions(self, interaction: discord.Interaction, names: Sequence[str]) -> None:
        cc = self.selected
        assert cc is not None
        chosen = discord.Permissions(**dict.fromkeys(names, True)).value

        async def apply() -> str:
            # bits set with `cc edit --perms` that the select does not offer are kept
            await self._update(lambda fresh: {
                'required_permissions': ((fresh.required_permissions or 0) & ~_COMMON_MASK) | chosen or None
            })
            return 'Saved the required permissions.'

        await self._run(interaction, apply)

    async def set_roles(self, interaction: discord.Interaction, role_ids: list[int]) -> None:
        async def apply() -> str:
            await self._update({'allowed_role_ids': role_ids})
            return 'Saved the allowed roles.'

        await self._run(interaction, apply)

    async def clear_gate(self, interaction: discord.Interaction) -> None:
        async def apply() -> str:
            await self._update({'required_permissions': None, 'allowed_role_ids': []})
            return 'Everyone can use it now.'

        await self._run(interaction, apply)

    # delete

    async def delete(self, interaction: discord.Interaction) -> None:
        cc = self.selected
        assert cc is not None

        async def apply() -> str:
            await self.cog.bot.db.custom_commands.delete_command(self.guild_id, cc.id)
            await self._reload()
            self.selected, self.screen = None, 'list'
            return f'Deleted `{self.prefix}{cc.name}`.'

        await self._run(interaction, apply)

    # aliases

    async def add_alias(self, interaction: discord.Interaction) -> None:
        async def apply(values: dict[str, str]) -> str:
            name, expansion = values['name'].strip().lower(), values['expansion'].strip()
            if problem := await self.cog.alias_problem(self.guild_id, name, expansion):
                raise ValueError(problem)
            await self.cog.bot.db.custom_commands.put_alias(self.guild_id, name, expansion, self.author.id)
            await self._reload()
            return f'`{self.prefix}{name}` now runs `{self.prefix}{expansion}`.'

        await self._form(interaction, 'alias', 'New alias', [
            Field('name', 'Alias', max_length=32, placeholder='sb', description='Reusing one of the aliases below replaces it. Command names are refused.'),
            Field('expansion', 'Runs', max_length=2000, placeholder='ban {0} spamming',
                  description='A command without the prefix. {args} = everything typed, {0}..{9} = single words.'),
        ], apply)

    async def remove_alias(self, interaction: discord.Interaction, name: str) -> None:
        async def apply() -> str:
            await self.cog.bot.db.custom_commands.delete_alias(self.guild_id, name)
            await self._reload()
            return f'Removed the alias `{name}`.'

        await self._run(interaction, apply)

    # test (read-only, so open to everyone)

    async def test(self, interaction: discord.Interaction) -> None:
        cc = self.selected
        assert cc is not None
        user = interaction.user
        params, _ = _params(cc)  # a bad stored signature just means no sample arguments
        member = {'': user.display_name, 'id': str(user.id), 'name': user.name, 'mention': user.mention}
        values, tokens = sample_values(params, member)
        raw = ' '.join(tokens)
        values |= self.cog.context_values(user, interaction.channel, interaction.guild, raw, tokens, cc.uses + 1)  # type: ignore[arg-type]
        limit = 3400 // len(cc.responses)
        rng = random.Random()
        rendered = '\n\n'.join(
            (f'**Response {i}**\n' if len(cc.responses) > 1 else '') + truncate(render(r, values, rng=rng), limit)
            for i, r in enumerate(cc.responses, 1)
        )
        card = make_notice(
            f'Test of {self.prefix}{cc.name}', rendered, accent=Accent.info,
            footer=f'As if you typed: {self.prefix}{cc.name} {raw}'.strip()[:300] + '. Nothing was sent to the channel.',
        )
        await interaction.response.send_message(view=card, ephemeral=True, allowed_mentions=NO_MENTIONS)
