"""Embed and persistent buttons for action requests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import discord

if TYPE_CHECKING:
    import re

    from app.core import Bot

__all__ = ('RequestDecisionButton', 'request_embed', 'request_view')

_COLORS = {'pending': 0xF0B232, 'approved': 0x23A55A, 'denied': 0xF23F42, 'failed': 0xF23F42, 'cancelled': 0x80848E}


def request_embed(request: Any, *, expired: bool = False) -> discord.Embed:
    status = 'expired' if expired else request['status']
    embed = discord.Embed(
        title=f'Action request #{request["id"]}: {request["action"]}',
        description=request['reason'],
        color=_COLORS.get(status, 0x80848E),
    )
    embed.add_field(name='Requested by', value=f'<@{request["requester_id"]}>')
    embed.add_field(name='Target', value=f'<@{request["target_id"]}>')
    if request['duration']:
        embed.add_field(name='Duration', value=f'{request["duration"]}s')
    if status == 'pending':
        embed.set_footer(text='Needs approval from a moderator with the matching permission.')
    else:
        outcome = status.title()
        if request.get('decided_by'):
            outcome += f' by <@{request["decided_by"]}>'
        if request.get('result') and request['result'] != 'done':
            outcome += f': {request["result"]}'
        embed.add_field(name='Outcome', value=outcome, inline=False)
    return embed


def request_view(request_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    view.add_item(RequestDecisionButton('approve', request_id))
    view.add_item(RequestDecisionButton('deny', request_id))
    return view


async def _decide(interaction: discord.Interaction[Bot], request_id: int, approve: bool, note: str | None) -> None:
    from .decide import decide_request  # decide imports this module

    assert interaction.guild is not None
    assert isinstance(interaction.user, discord.Member)
    decision = await decide_request(
        interaction.client, interaction.guild, request_id, interaction.user, approve=approve, note=note
    )
    await interaction.followup.send(decision.message, ephemeral=True)


class DenyNoteModal(discord.ui.Modal, title='Deny request'):
    note = discord.ui.TextInput(label='Note for the requester (optional)', required=False, max_length=200)

    def __init__(self, request_id: int) -> None:
        super().__init__()
        self.request_id = request_id

    async def on_submit(self, interaction: discord.Interaction[Bot]) -> None:  # type: ignore[override]
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _decide(interaction, self.request_id, False, self.note.value or None)


class RequestDecisionButton(
    discord.ui.DynamicItem[discord.ui.Button], template=r'req:(?P<verdict>approve|deny):(?P<id>[0-9]+)'
):
    def __init__(self, verdict: str, request_id: int) -> None:
        self.verdict = verdict
        self.request_id = request_id
        super().__init__(
            discord.ui.Button(
                label=verdict.title(),
                style=discord.ButtonStyle.green if verdict == 'approve' else discord.ButtonStyle.red,
                custom_id=f'req:{verdict}:{request_id}',
            )
        )

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: Any, match: re.Match[str], /
    ) -> RequestDecisionButton:
        return cls(match['verdict'], int(match['id']))

    async def callback(self, interaction: discord.Interaction[Bot]) -> None:  # type: ignore[override]
        if self.verdict == 'deny':
            await interaction.response.send_modal(DenyNoteModal(self.request_id))
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        await _decide(interaction, self.request_id, True, None)
