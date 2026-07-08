from __future__ import annotations

import io
from typing import TYPE_CHECKING

import discord
from discord.ext import commands
from klappstuhl import File
from klappstuhl.enums import Effect, ImageFormat
from klappstuhl.errors import HTTPError

from app.core.models import command, cooldown, describe, group
from app.utils import helpers

if TYPE_CHECKING:
    from app.core import Bot, Context
    from app.services.klappstuhl_me import KlappstuhlClient


class KlappstuhlToolsMixin:
    """Commands backed by the klappstuhl.me API.

    These wrap Percy's :class:`~app.services.klappstuhl_me.KlappstuhlClient`. They
    are all *account-scoped* (short links and pastes need ``links:*`` / ``pastes:*``;
    the image/render/scan endpoints need ``images:read``), so they require a personal
    key configured via ``KLAPPSTUHL_ME_API_TOKEN``. When it is absent every command
    degrades to a friendly "not configured" message rather than erroring.

    Two families live here:

    * **Text tools** — :meth:`shorten`, :meth:`paste`, :meth:`qr`, :meth:`preview`.
    * **Image tools** — the :meth:`image` group (blur/pixelate/deepfry/invert/grayscale,
      convert, info, palette) plus :meth:`scan` and :meth:`screenshot`.
    """

    bot: Bot

    # -- helpers -------------------------------------------------------------

    def _client(self) -> KlappstuhlClient | None:
        """Return the account-scoped client, or ``None`` when unconfigured."""
        client = self.bot.klappstuhlme_client
        if client is None or not client.available:
            return None
        return client

    def _tools_unavailable(self, ctx: Context) -> bool:
        """Whether the account-scoped hoster features are unusable right now."""
        return self._client() is None

    @staticmethod
    def _format_bytes(size: int) -> str:
        """Render a byte count as a human-readable size (e.g. ``1.4 MiB``)."""
        value = float(size)
        for unit in ('B', 'KiB', 'MiB', 'GiB'):
            if value < 1024 or unit == 'GiB':
                return f'{value:.0f} {unit}' if unit == 'B' else f'{value:.1f} {unit}'
            value /= 1024
        return f'{value:.1f} GiB'

    async def _resolve_image_url(
        self,
        ctx: Context,
        target: discord.Member | discord.User | str | None,
    ) -> str:
        """Resolve an image source to a public URL the API can fetch.

        Resolution order: an explicit URL / a member's avatar, then an attachment
        on the invoking message, then an attachment or embedded image on the
        replied-to message, and finally the invoker's own avatar.
        """
        if isinstance(target, (discord.Member, discord.User)):
            return target.display_avatar.url
        if isinstance(target, str) and target.strip():
            return target.strip().strip('<>')

        if ctx.message.attachments:
            return ctx.message.attachments[0].url

        reference = ctx.message.reference
        resolved = reference.resolved if reference is not None else None
        if isinstance(resolved, discord.Message):
            if resolved.attachments:
                return resolved.attachments[0].url
            for embed in resolved.embeds:
                if embed.image and embed.image.url:
                    return embed.image.url
                if embed.thumbnail and embed.thumbnail.url:
                    return embed.thumbnail.url

        return ctx.author.display_avatar.url

    async def _send_png(self, ctx: Context, data: bytes, *, title: str, filename: str = 'result.png') -> None:
        """Send raw image ``bytes`` as an embedded attachment."""
        file = discord.File(io.BytesIO(data), filename=filename)
        embed = discord.Embed(title=title, colour=helpers.Colour.white())
        embed.set_image(url=f'attachment://{filename}')
        embed.set_footer(text='via klappstuhl.me')
        await ctx.send(embed=embed, file=file)

    async def _run_effect(
        self,
        ctx: Context,
        op: Effect,
        target: discord.Member | discord.User | str | None,
    ) -> None:
        """Apply a visual effect to a resolved image and post the PNG."""
        client = self._client()
        if client is None:
            await ctx.send_error('Image tools are not configured on this instance.')
            return

        url = await self._resolve_image_url(ctx, target)
        async with ctx.typing():
            try:
                png = await client.manipulate(op, url=url)
            except HTTPError as exc:
                await ctx.send_error(f'Could not process that image: {exc.message}')
                return

        await self._send_png(ctx, png, title=str(op).capitalize())

    # -- short links / pastes / qr / unfurl ----------------------------------

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
        client = self._client()
        if client is None:
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
        client = self._client()
        if client is None:
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

    # -- image manipulation & inspection -------------------------------------

    @group(
        'image',
        aliases=['img'],
        invoke_without_command=True,
        description='Manipulate and inspect images via klappstuhl.me.',
    )
    async def image(self, ctx: Context) -> None:
        """Image tools. Every subcommand takes a member (their avatar), an image
        URL, an attachment, or a replied-to image — defaulting to your own avatar.
        """
        await ctx.send_help(ctx.command)

    @image.command('blur', description='Gaussian-blur an image.')
    @describe(target='A member, image URL, or attachment (defaults to your avatar).')
    @cooldown(1, 8, commands.BucketType.user)
    async def image_blur(self, ctx: Context, target: discord.Member | str | None = None) -> None:
        await self._run_effect(ctx, Effect.BLUR, target)

    @image.command('pixelate', aliases=['pixel'], description='Pixelate/mosaic an image.')
    @describe(target='A member, image URL, or attachment (defaults to your avatar).')
    @cooldown(1, 8, commands.BucketType.user)
    async def image_pixelate(self, ctx: Context, target: discord.Member | str | None = None) -> None:
        await self._run_effect(ctx, Effect.PIXELATE, target)

    @image.command('deepfry', aliases=['fry'], description='Deep-fry an image.')
    @describe(target='A member, image URL, or attachment (defaults to your avatar).')
    @cooldown(1, 8, commands.BucketType.user)
    async def image_deepfry(self, ctx: Context, target: discord.Member | str | None = None) -> None:
        await self._run_effect(ctx, Effect.DEEPFRY, target)

    @image.command('invert', description='Invert an image\'s colours.')
    @describe(target='A member, image URL, or attachment (defaults to your avatar).')
    @cooldown(1, 8, commands.BucketType.user)
    async def image_invert(self, ctx: Context, target: discord.Member | str | None = None) -> None:
        await self._run_effect(ctx, Effect.INVERT, target)

    @image.command('grayscale', aliases=['greyscale', 'gray', 'grey'], description='Desaturate an image to gray.')
    @describe(target='A member, image URL, or attachment (defaults to your avatar).')
    @cooldown(1, 8, commands.BucketType.user)
    async def image_grayscale(self, ctx: Context, target: discord.Member | str | None = None) -> None:
        await self._run_effect(ctx, Effect.GRAYSCALE, target)

    @image.command('convert', description='Convert an image to another format.')
    @describe(
        to='Target format: png, jpeg/jpg, webp, gif, bmp, tiff.',
        target='A member, image URL, or attachment (defaults to your avatar).',
    )
    @cooldown(1, 8, commands.BucketType.user)
    async def image_convert(
        self,
        ctx: Context,
        to: str,
        target: discord.Member | str | None = None,
    ) -> None:
        """Transcode an image to a different raster format."""
        client = self._client()
        if client is None:
            await ctx.send_error('Image tools are not configured on this instance.')
            return

        fmt = to.lower().lstrip('.')
        valid = {f.value for f in ImageFormat}
        if fmt not in valid:
            await ctx.send_error(f'Unsupported format `{to}`. Choose one of: {", ".join(sorted(valid))}.')
            return

        url = await self._resolve_image_url(ctx, target)
        async with ctx.typing():
            try:
                data = await client.convert(fmt, url=url)
            except HTTPError as exc:
                await ctx.send_error(f'Could not convert that image: {exc.message}')
                return

        ext = 'jpg' if fmt == 'jpeg' else fmt
        await self._send_png(ctx, data, title=f'Converted to {fmt.upper()}', filename=f'converted.{ext}')

    @image.command('info', aliases=['metadata'], description='Inspect an image\'s dimensions and format.')
    @describe(target='A member, image URL, or attachment (defaults to your avatar).')
    @cooldown(1, 5, commands.BucketType.user)
    async def image_info(self, ctx: Context, target: discord.Member | str | None = None) -> None:
        """Show an image's dimensions, format, and size without storing it."""
        client = self._client()
        if client is None:
            await ctx.send_error('Image tools are not configured on this instance.')
            return

        url = await self._resolve_image_url(ctx, target)
        try:
            info = await client.metadata(url=url)
        except HTTPError as exc:
            await ctx.send_error(f'Could not inspect that image: {exc.message}')
            return

        embed = discord.Embed(title='Image metadata', colour=helpers.Colour.white())
        embed.add_field(name='Dimensions', value=f'{info.width} × {info.height} px')
        embed.add_field(name='Format', value=info.format.upper())
        embed.add_field(name='Colour mode', value=info.color)
        embed.add_field(name='File size', value=self._format_bytes(info.file_size))
        embed.set_thumbnail(url=url)
        await ctx.send(embed=embed)

    @image.command('palette', aliases=['colors', 'colours'], description='Extract an image\'s dominant colours.')
    @describe(target='A member, image URL, or attachment (defaults to your avatar).')
    @cooldown(1, 5, commands.BucketType.user)
    async def image_palette(self, ctx: Context, target: discord.Member | str | None = None) -> None:
        """Extract an image's dominant colours (great for embed accents)."""
        client = self._client()
        if client is None:
            await ctx.send_error('Image tools are not configured on this instance.')
            return

        url = await self._resolve_image_url(ctx, target)
        try:
            palette = await client.color_palette(url=url)
        except HTTPError as exc:
            await ctx.send_error(f'Could not read that image\'s colours: {exc.message}')
            return

        if not palette.colors:
            await ctx.send_warning('No colours could be extracted from that image.')
            return

        top = palette.colors[0]
        embed = discord.Embed(
            title='Colour palette',
            colour=discord.Colour.from_rgb(*top.rgb),
        )
        embed.description = '\n'.join(
            f'`{c.hex}` — {c.proportion:.1%}' for c in palette.colors
        )
        embed.set_thumbnail(url=url)
        await ctx.send(embed=embed)

    # -- scan / screenshot ---------------------------------------------------

    @command(description='Scan an attached file for malware (ClamAV + VirusTotal).')
    @cooldown(1, 15, commands.BucketType.user)
    async def scan(self, ctx: Context) -> None:
        """Scan an attached file for malware.

        Attach a file (or reply to a message with one). Nothing is stored — only
        the file's SHA-256 (never its contents) is sent to VirusTotal.
        """
        client = self._client()
        if client is None:
            await ctx.send_error('The malware scanner is not configured on this instance.')
            return

        attachment: discord.Attachment | None = None
        if ctx.message.attachments:
            attachment = ctx.message.attachments[0]
        else:
            reference = ctx.message.reference
            resolved = reference.resolved if reference is not None else None
            if isinstance(resolved, discord.Message) and resolved.attachments:
                attachment = resolved.attachments[0]

        if attachment is None:
            await ctx.send_error('Attach a file (or reply to one) to scan it.')
            return

        async with ctx.typing():
            try:
                raw = await attachment.read()
                report = await client.scan(File(raw, filename=attachment.filename))
            except HTTPError as exc:
                await ctx.send_error(f'Could not scan that file: {exc.message}')
                return

        if report.is_infected:
            colour = helpers.Colour.error_accent()
            verdict = '⚠️ Infected'
        elif report.is_clean:
            colour = helpers.Colour.success_accent()
            verdict = '✅ Clean'
        else:
            colour = helpers.Colour.warning_accent()
            verdict = f'❔ {report.verdict.capitalize()}'

        embed = discord.Embed(title=f'Scan: {attachment.filename}', colour=colour)
        embed.add_field(name='Verdict', value=verdict, inline=False)
        embed.add_field(name='Size', value=self._format_bytes(report.file_size))
        if report.clamav_clean is not None:
            clam = 'clean' if report.clamav_clean else (report.clamav_virus or 'infected')
            embed.add_field(name='ClamAV', value=clam)
        if report.vt_status is not None:
            if report.vt_positives is not None and report.vt_total is not None:
                vt = f'{report.vt_positives}/{report.vt_total} engines'
            else:
                vt = report.vt_status
            if report.vt_url:
                vt = f'[{vt}]({report.vt_url})'
            embed.add_field(name='VirusTotal', value=vt)
        embed.set_footer(text=f'SHA-256: {report.sha256}')
        await ctx.send(embed=embed)

    @command(description='Screenshot a web page.')
    @describe(url='The public http(s) page to capture.')
    @cooldown(1, 15, commands.BucketType.user)
    async def screenshot(self, ctx: Context, *, url: str) -> None:
        """Render a web page to a PNG via a headless browser."""
        client = self._client()
        if client is None:
            await ctx.send_error('Screenshots are not configured on this instance.')
            return

        async with ctx.typing():
            try:
                png = await client.screenshot(url)
            except HTTPError as exc:
                await ctx.send_error(f'Could not screenshot that page: {exc.message}')
                return

        await self._send_png(ctx, png, title='Screenshot', filename='screenshot.png')
