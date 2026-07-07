from __future__ import annotations

import io
from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from klappstuhl.errors import HTTPError

from app.core.models import command, cooldown, describe
from app.utils import helpers

if TYPE_CHECKING:
    from app.core import Bot, Context


class KlappstuhlToolsMixin:
    """Commands backed by the klappstuhl.me API: short links, pastes, QR, unfurl.

    These use Percy's :class:`~app.services.klappstuhl_me.KlappstuhlMeClient`. The
    account-scoped ones (shorten, paste) need a personal key configured via
    ``KLAPPSTUHL_ME_API_TOKEN`` — when it is absent the commands degrade to a
    friendly "not configured" message rather than erroring.
    """

    bot: Bot

    def _tools_unavailable(self, ctx: Context) -> bool:
        """Whether the account-scoped hoster features are unusable right now."""
        client = self.bot.klappstuhlme_client
        return client is None or not client.account_available

    @command(description='Shorten a URL into a klappstuhl.me short link.')
    @describe(url='The URL to shorten.', alias='An optional custom alias for the short link.')
    @cooldown(1, 10, commands.BucketType.user)
    async def shorten(self, ctx: Context, url: str, *, alias: str | None = None) -> None:
        """Create a short link (``r.klappstuhl.me/<code>``) for a URL."""
        if self._tools_unavailable(ctx):
            await ctx.send_error('The URL shortener is not configured on this instance.')
            return

        try:
            link = await self.bot.klappstuhlme_client.shorten(url, code=alias)
        except HTTPError as exc:
            await ctx.send_error(f'Could not shorten that URL: {exc.message}')
            return

        embed = discord.Embed(title='Short link created', colour=helpers.Colour.white())
        embed.description = f'[`{link.short_url}`]({link.short_url})\n-# → {link.target_url}'
        await ctx.send(embed=embed)

    @command(description='Generate a QR code for text or a URL.')
    @describe(data='The text or URL to encode.')
    @cooldown(1, 10, commands.BucketType.user)
    async def qr(self, ctx: Context, *, data: str) -> None:
        """Render ``data`` as a QR code image."""
        client = self.bot.klappstuhlme_client
        if client is None or not client.account_available:
            await ctx.send_error('QR rendering is not configured on this instance.')
            return

        try:
            png = await client.render_qr(data, format='png', size=512)
        except HTTPError as exc:
            await ctx.send_error(f'Could not generate a QR code: {exc.message}')
            return

        file = discord.File(io.BytesIO(png), filename='qr.png')
        embed = discord.Embed(title='QR code', colour=helpers.Colour.white())
        embed.set_image(url='attachment://qr.png')
        await ctx.send(embed=embed, file=file)

    @command(description='Host a text/code snippet as a paste and get a link.')
    @describe(content='The text/code to paste.')
    @cooldown(1, 10, commands.BucketType.user)
    async def paste(self, ctx: Context, *, content: str) -> None:
        """Create a hosted paste, viewable at ``/p/<id>`` (highlighted) and ``.txt`` (raw)."""
        if self._tools_unavailable(ctx):
            await ctx.send_error('The paste host is not configured on this instance.')
            return

        try:
            paste = await self.bot.klappstuhlme_client.create_paste(content)
        except HTTPError as exc:
            await ctx.send_error(f'Could not create the paste: {exc.message}')
            return

        embed = discord.Embed(title='Paste created', colour=helpers.Colour.white())
        embed.description = f'[View highlighted]({paste.url}) · [Raw]({paste.raw_url})'
        await ctx.send(embed=embed)

    @command(description='Preview a link (Open Graph / metadata).')
    @describe(url='The URL to unfurl.')
    @cooldown(1, 10, commands.BucketType.user)
    async def preview(self, ctx: Context, *, url: str) -> None:
        """Fetch a URL's Open Graph / link-preview metadata."""
        client = self.bot.klappstuhlme_client
        if client is None or not client.account_available:
            await ctx.send_error('Link previews are not configured on this instance.')
            return

        try:
            data = await client.unfurl(url)
        except HTTPError as exc:
            await ctx.send_error(f'Could not preview that URL: {exc.message}')
            return

        if not (data.title or data.description or data.image):
            await ctx.send_warning('No preview metadata was found for that URL.')
            return

        embed = discord.Embed(
            title=data.title or url,
            description=data.description,
            url=url,
            colour=helpers.Colour.white(),
        )
        if data.site_name:
            embed.set_author(name=data.site_name, icon_url=data.favicon or discord.utils.MISSING)
        if data.image:
            embed.set_image(url=data.image)
        await ctx.send(embed=embed)
