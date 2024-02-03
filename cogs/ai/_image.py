from __future__ import annotations

import base64
import uuid
from dataclasses import dataclass
from io import BytesIO
from typing import List, Literal
from uuid import UUID

import discord
import param

from bot import Percy
from cogs.utils import commands
from cogs.utils.formats import valid_filename
from launcher import get_logger

log = get_logger(__name__)


@dataclass
class ImageJob:
    """Job to be processed by Stability."""

    prompt: str
    settings: TextToImageSettings | ImageToImageSettings
    author: discord.User | None

    message: discord.Message | None

    grid: ImageFile | None
    images: List[ImageFile] | None


@dataclass(frozen=True)
class ImageFile:
    """File Attachment from generated Image."""

    requestor: discord.User
    prompt: str
    id: UUID
    content: bytes

    def __repr__(self) -> str:
        """Return the content as a string."""
        content = f'{self.content[:10]}...' if len(self.content) > 10 else self.content
        return f'FileAttachment(path={self.filename!r}, content={content})'

    def to_file(self) -> discord.File:
        """Convert to a discord.File."""
        return discord.File(BytesIO(self.content), filename=self.filename)

    @property
    def filename(self) -> str:
        """Return the filename of the image at the given index."""
        clean_prompt = self.prompt.split(' --', 1)[0]
        return f'{self.requestor.name}_{valid_filename(clean_prompt)}_{self.id}.png'


class TextToImageSettings(param.Parameterized):
    """Settings for the text-to-image generator."""

    guidance: int = param.Integer(
        default=10,
        doc='The influence the prompt has on the generated image.',
    )
    aspect: str = param.String(
        default="1:1",
        doc='The aspect ratio of the generated image.',
    )


class ImageToImageSettings(param.Parameterized):
    """Settings for the image-to-image generator."""

    image: bytes = param.Bytes(
        doc='The image to generate from.',
    )
    image_strength: float = param.Number(
        default=1.0,
        bounds=(0.0, 1.0),
        doc='The influence the image has on the generated image.',
    )


class StabilityInterface:
    def __init__(
            self,
            bot: Percy,
            *,
            host: str = 'https://api.stability.ai',
            key: str = '',
            engine_id: str = 'stable-diffusion-v1-6',
    ):
        """A client for the Stability API.

        Parameters
        ----------
        bot: Percy
            The bot instance.
        host: str
            The API host.
        key: str
            The API key.
        engine_id: str
            The engine ID.
        """
        self.bot = bot

        self.host = host
        self.key = key
        self.engine_id = engine_id

    async def check_daily_credits(self):
        url = f'{self.host}/v1/user/balance'

        headers = {
            'Authorization': f'Bearer {self.key}'
        }

        async with self.bot.session.get(url, headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f'Failed to check daily credits:\n```{data["message"]}```')

            if data['credits'] < 1:
                return False

            return True

    async def engine_list(self):
        """List all engines."""

        headers = {
            'Authorization': f'Bearer {self.key}'
        }

        async with self.bot.session.get(f'{self.host}/v1/engines/list', headers=headers) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise RuntimeError(f'Failed to list engines:\n```{data["message"]}```')

            log.info(data)

    async def generate(self, job: ImageJob) -> List[ImageFile]:
        """Generate four images from a prompt using Stable Diffusion.

        Parameters
        ----------
        job: ImageJob
            The job to generate an image from.

        Returns
        -------
        FileAttachment
            The generated image.
        """

        settings = job.settings
        url = f'{self.host}/v1/generation/{self.engine_id}/text-to-image'

        headers = {
            'Content-Type': 'application/json',
            'Accept': 'application/json',
            'Authorization': f'Bearer {self.key}'
        }

        json = {
            'text_prompts': [
                {
                    "text": job.prompt
                }
            ],
            'cfg_scale': settings.guidance,
            'samples': 4,
        }

        async with self.bot.session.post(url, headers=headers, json=json) as resp:
            data = await resp.json()
            if resp.status != 200:
                raise commands.BadArgument(f'Failed to generate image:\n```{data['message']}```')

            return [
                ImageFile(requestor=job.author, prompt=job.prompt,
                          id=uuid.uuid4(), content=base64.b64decode(image['base64']))
                for index, image in enumerate(data['artifacts'])
            ]

    async def variation(self, job: 'ImageJob', index: int, vary: Literal["subtle", "strong"]) -> ImageFile:
        """Generate an image from a prompt.

        Parameters
        ----------
        job: ImageJob
            The job to generate an image from.
        index: int
            The index of the variation to generate.
        vary: Literal["subtle", "strong"]
            The variation to generate.

        Returns
        -------
        FileAttachment
            The generated image.
        """

        image_strength = 0.30 if vary == 'subtle' else 0.80
        url = f'{self.host}/v1/generation/{self.engine_id}/image-to-image'

        headers = {
            'Accept': 'image/png',
            'Authorization': f'Bearer {self.key}'
        }

        data = {
            'init_image': job.images[index].content
        }

        params = {
            'image_strength': image_strength
        }

        async with self.bot.session.post(url, headers=headers, data=data, params=params) as resp:
            if resp.status != 200:
                raise commands.BadArgument(f'Failed to generate image:\n```{(await resp.json())['message']}```')

            return ImageFile(requestor=job.author, prompt=job.prompt, id=uuid.uuid4(), content=await resp.read())

    async def upscale(self, job: ImageJob, index: int, size: Literal["2x", "4x"]) -> ImageFile:
        """Upscale an image."""

        url = f'{self.host}/v1/generation/esrgan-v1-x2plus/image-to-image/upscale'

        headers = {
            'Accept': 'image/png',
            'Authorization': f'Bearer {self.key}'
        }

        data = {
            'image': job.images[index].content
        }

        params = {
            'width': 512 * (2 if size == '2x' else 4)  # 512x512 is the default size
        }

        async with self.bot.session.post(url, headers=headers, data=data, params=params) as resp:
            if resp.status != 200:
                raise commands.BadArgument(f'Failed to upscale image:\n```{(await resp.json())["message"]}```')

            return ImageFile(requestor=job.author, prompt=job.prompt, id=uuid.uuid4(), content=await resp.read())


# Decorators

def check_daily_credits():
    # We have 25 credits per day for free,
    # that's why we need to make a request to the API to check if we have enough credits.

    # TODO: Add Limit for Image generations per User !!!

    async def predicate(ctx) -> bool:
        interface = StabilityInterface(ctx.bot, key=ctx.bot.config.stability_key)

        if not await interface.check_daily_credits():
            raise commands.BadArgument('You have no daily credits left.')

        return True

    return commands.check(predicate)
