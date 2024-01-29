from __future__ import annotations

import uuid
from operator import attrgetter

import discord
from PIL import Image
from discord import Message, Interaction

from bot import Percy
from cogs.ai import _image
from cogs.ai._image import TextToImageSettings, ImageJob, StabilityInterface, ImageFile
from cogs.utils import commands, errors
from cogs.utils.context import Context
from cogs.utils.lock import lock_arg
from cogs.utils.render import Render


class PromptFlags(commands.FlagConverter, prefix='--', delimiter=' '):
    prompt: str = commands.Flag(description='The prompt to generate an image from.')
    prompt.__setattr__('without_prefix', True)

    guidance: int = commands.Flag(default=10, aliases=['gi'], description='How strictly the diffusion process adheres to the prompt text.')
    aspect: str = commands.Flag(default='1:1', aliases=['ar'], description='The aspect ratio of the image.')


class RerunButton(discord.ui.Button):
    def __init__(self, job: _image.ImageJob, cog: AITools):
        self.cog = cog
        self.job = job

        super().__init__(emoji='🔄', style=discord.ButtonStyle.gray)

    async def callback(self, interaction: discord.Interaction):
        """Callback for the Rerun button."""

        await interaction.response.defer()
        await self.cog.send_job(interaction, self.job, True)


class UButton(discord.ui.Button):
    def __init__(self, _job: _image.ImageJob, index: int):
        self.index = index
        self.job = _job

        super().__init__(label=f'U{index+1}', style=discord.ButtonStyle.gray)

    async def callback(self, interaction: discord.Interaction):
        """Callback for the U buttons."""

        await interaction.response.defer()

        if self.index >= len(self.job.images):
            return

        await interaction.channel.send(
            content=f'**{self.job.prompt}** - Image #{self.index+1} - {interaction.user.mention}',
            file=self.job.images[self.index].to_file(),
            reference=interaction.message,
            view=ImageEditorView(self.job, self.index, self.view.cog))

        self.style = discord.ButtonStyle.blurple
        self.disabled = True

        await interaction.message.edit(view=self.view)


class ImageSamplerView(discord.ui.View):
    """View for ImageSampler."""

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        """Check if the interaction is from the same user as the original message."""

        if interaction.user.id != self.job.author.id:
            raise errors.BadArgument('You are not permitted to interact with this message.')
        return True

    def __init__(self, job: _image.ImageJob, cog: AITools, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.job = job
        self.cog = cog

        for i in range(4):
            self.add_item(UButton(job, i))

        self.add_item(RerunButton(job, cog))


class ImageEditorView(discord.ui.View):
    """View for ImageEditor."""

    def __init__(self, job: _image.ImageJob, index: int, cog: AITools, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.job = job
        self.index = index

        self.cog = cog

        # TODO: For now, uses the original generated image for every new edit,
        # maybe set the edited image as the new original image?

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        """Check if the interaction is from the same user as the original message."""

        if interaction.user.id != self.job.author.id:
            raise errors.BadArgument('You are not permitted to interact with this message.')
        return True

    @discord.ui.button(emoji='⏫', label='Upscale (2x)', style=discord.ButtonStyle.grey)
    async def upscale_2x(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        """Callback for the Upscale (2x) button."""

        await interaction.response.defer()

        self.upscale_2x.style = discord.ButtonStyle.green
        self.upscale_2x.disabled = True
        await interaction.message.edit(view=self)

        image = await self.cog.interface.upscale(self.job, self.index, '2x')

        self.upscale_2x.style = discord.ButtonStyle.blurple
        await interaction.message.edit(attachments=[image.to_file()], view=self)

    @discord.ui.button(emoji='⏫', label='Upscale (4x)', style=discord.ButtonStyle.grey)
    async def upscale_4x(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        """Callback for the Upscale (4x) button."""

        await interaction.response.defer()

        self.upscale_4x.style = discord.ButtonStyle.green
        self.upscale_4x.disabled = True
        await interaction.message.edit(view=self)

        image = await self.cog.interface.upscale(self.job, self.index, '4x')

        self.upscale_4x.style = discord.ButtonStyle.blurple
        await interaction.message.edit(attachments=[image.to_file()], view=self)

    # TODO: Fix vary function

    @discord.ui.button(emoji='🪄', label='Vary (Subtle)', style=discord.ButtonStyle.grey, disabled=True)
    async def vary_subtle(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        """Callback for the Vary (Subtle) button."""

        await interaction.response.defer()

        self.vary_subtle.style = discord.ButtonStyle.green
        self.vary_subtle.disabled = True
        await interaction.message.edit(view=self)

        image = await self.cog.interface.variation(self.job, self.index, 'subtle')

        self.vary_subtle.style = discord.ButtonStyle.blurple
        await interaction.message.edit(attachments=[image.to_file()], view=self)

    @discord.ui.button(emoji='🪄', label='Vary (Strong)', style=discord.ButtonStyle.grey, disabled=True)
    async def vary_strong(self, interaction: discord.Interaction, button: discord.ui.Button):  # noqa
        """Callback for the Vary (Strong) button."""

        await interaction.response.defer()

        self.vary_strong.style = discord.ButtonStyle.green
        self.vary_strong.disabled = True
        await interaction.message.edit(view=self)

        image = await self.cog.interface.variation(self.job, self.index, 'strong')

        self.vary_strong.style = discord.ButtonStyle.blurple
        await interaction.message.edit(attachments=[image.to_file()], view=self)


class AITools(commands.Cog):
    """Tools for AI"""

    def __init__(self, bot: Percy):
        self.bot: Percy = bot

        self.render: Render = Render()
        self.interface = StabilityInterface(self.bot, key=self.bot.config.stability_key)

    @property
    def display_emoji(self) -> discord.PartialEmoji:
        return discord.PartialEmoji(name='\N{FRAME WITH PICTURE}')

    @lock_arg('image.send_job', 'ctx', attrgetter('user.id'), raise_error=True)
    async def send_job(self, ctx: Context | Interaction, job: ImageJob, rerun: bool = False) -> Message:
        """|coro| @locked(func, ctx)

        Run an image job and send the result to the channel.

        Returns
        -------
        discord.Message
            The message sent to the channel.
        """

        content = f'**{job.prompt}** - {ctx.user.mention} (*<a:loading:1072682806360166430> Waiting to finish...*)'
        if rerun:
            message = await job.message.edit(content=content, attachments=[])
        else:
            message = await ctx.reply(content)

        try:
            result = await self.interface.generate(job=job)
        except Exception as e:
            raise errors.BadArgument(f'Failed to generate image: {e}') from e

        if not result:
            raise errors.BadArgument('Failed somehow to generate image. :/')

        grid_image = ImageFile(
            requestor=job.author, prompt=job.prompt, id=uuid.uuid4(),
            content=self.render.create_image_grid([Image.open(image.content) for image in result]).read()
        )

        job.grid = grid_image.to_file()
        job.images = result

        await message.edit(
            content=f'**{job.prompt}** - {ctx.user.mention}',
            attachments=[job.grid],
            view=ImageSamplerView(job, self)
        )

        job.message = message
        return message

    @commands.command(
        name='text-to-image',
        aliases=['tti'],
        description='Generate an image from text.',
    )
    @_image.check_daily_credits()
    async def text_to_image(self, ctx: Context, *, flags: PromptFlags):
        """Generate an image from text."""

        settings = TextToImageSettings(
            guidance=flags.guidance,
            aspect=flags.aspect,
        )

        _job = ImageJob(
            prompt=flags.prompt,
            settings=settings,
            author=ctx.author,
            message=None,
            images=None,
            grid=None
        )

        await self.send_job(ctx, _job)
