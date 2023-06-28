import io
from typing import Optional, Any

import asyncio
import yarl

from bot import Percy
from cogs.imagine._enum import Style, Ratio
from launcher import get_logger

log = get_logger(__name__)


def bytes_to_io(data: bytes, filename: str) -> io.BytesIO:
    """Convert bytes to a BytesIO object with a filename."""
    buffer = io.BytesIO(data)
    buffer.name = filename
    return buffer


class RequestError(Exception):
    """Raised when there's an error with a request."""
    pass


class ImagineClient:
    """Async class for handling API requests to the Imagine service."""

    def __init__(self, bot: Percy) -> None:
        self.bot: Percy = bot
        self._req_lock = asyncio.Lock()

        self.version: str = "1"

    @staticmethod
    def get_style_url(style: Style = Style.IMAGINE_V1) -> str:
        """Get link of style thumbnail"""
        return f"https://1966211409.rsc.cdn77.org/appStuff/imagine-fncisndcubnsduigfuds//assets/{style.value[2]}/{style.value[1]}.webp"

    async def assets(self, style: Style = Style.IMAGINE_V1) -> bytes:
        """Gets the assets."""
        async with self.bot.session.get(
                url=self.get_style_url(style=style)
        ) as resp:
            return await resp.read()

    async def make_request(
            self,
            method: str,
            url: str,
            *,
            params: Optional[dict[str, Any]] = None,
            data: Optional[dict[str, Any]] = None,
            headers: Optional[dict[str, Any]] = None,
    ) -> Any:
        """|coro|

        Sends a request to the GitHub API.

        Parameters
        ----------
        method: :class:`str`
            The HTTP method to use.
        url: :class:`str`
            The URL to send the request to.
        params: Optional[:class:`dict`]
            The parameters to pass to the request.
        data: Optional[:class:`dict`]
            The data to pass to the request.
        headers: Optional[:class:`dict`]
            The headers to pass to the request.

        Returns
        -------
        Any
            The JSON response from the API.
        """
        hdrs = {"accept": "*/*",
                "user-agent": "okhttp/4.10.0",
                "style-id": "30"}

        req_url = yarl.URL('https://inferenceengine.vyro.ai') / url

        if headers is not None and isinstance(headers, dict):
            hdrs.update(headers)

        async with self._req_lock:
            async with self.bot.session.request(method, req_url, params=params, json=data, headers=hdrs) as r:
                remaining = r.headers.get('X-Ratelimit-Remaining')
                js = await r.json()
                if r.status == 429 or remaining == '0':
                    delta = discord.utils._parse_ratelimit_header(r)  # noqa
                    await asyncio.sleep(delta)
                    self._req_lock.release()
                    return await self.make_request(method, url, params=params, data=data, headers=headers)
                elif 300 > r.status >= 200:
                    return js
                else:
                    raise RequestError(js['message'])

    async def sdprem(
            self,
            prompt: str,
            *,
            negative: Optional[str] = None,
            priority: Optional[str] = None,
            steps: Optional[str] = None,
            high_res_results: Optional[str] = None,
            style: Style = Style.IMAGINE_V1,
            seed: Optional[str] = None,
            ratio: Ratio = Ratio.RATIO_1X1,
            cfg: str = "9.5"
    ) -> Optional[bytes]:
        """|coro|

        Performs style transfer using the Imagine API.

        Parameters
        ----------
        prompt: :class:`str`
            The prompt to use.
        negative: Optional[:class:`str`]
            The negative prompt to use.
        priority: Optional[:class:`str`]
            The priority to use.
        steps: Optional[:class:`str`]
            The steps to use.
        high_res_results: Optional[:class:`str`]
            The high res results to use.
        style: Optional[:class:`Style`]
            The style to use.
        seed: Optional[:class:`str`]
            The seed to use.
        ratio: Optional[:class:`Ratio`]
            The ratio to use.
        cfg: Optional[:class:`str`]
            The cfg to use. (Must be in range (0; 16))

        Returns
        -------
        Optional[:class:`bytes`]
            The bytes of the image.
        """

        for attempt in range(1, 4):
            try:
                resp = await self.make_request(
                    "POST", "sdprem", headers={"style-id": str(style.value[0])},
                    data={
                        "model_version": self.version,
                        "prompt": prompt + (style.value[3] or ""),
                        "negative_prompt": negative or "ugly, disfigured, low quality, blurry, nsfw",
                        "style_id": style.value[0],
                        "aspect_ratio": f"{ratio.value[0]}:{ratio.value[1]}",
                        "seed": seed or "",
                        "steps": steps or "30",
                        "cfg": cfg,
                        "priority": priority or "0",
                        "high_res_results": high_res_results or "0"
                    })
            except Exception:
                log.exception(
                    f"An unexpected error has occurred during fetching of 'sdprem'; trying again ({attempt}/{3})."
                )
            else:
                return await resp.read()

    async def upscale(self, image: bytes) -> bytes:
        """|coro|

        Upscales the image.

        Parameters
        ----------
        image: :class:`bytes`
            The image to use.

        Returns
        -------
        :class:`bytes`
            The bytes of the image.
        """
        try:
            resp = await self.make_request("POST", "upscale", data={
                        "model_version": self.version,
                        "image": bytes_to_io(image, "test.png")
                    })
        except Exception:
            log.exception(
                f"An unexpected error has occurred during fetching of 'sdprem'."
            )
        else:
            return await resp.read()

    async def interrogator(self, image: bytes) -> str:
        """|coro|

        Interrogates the image.

        Parameters
        ----------
        image: :class:`bytes`
            The image to use.

        Returns
        -------
        :class:`str`
            The response from the API.
        """
        try:
            resp = await self.make_request("POST", "interrogator", data={
                    "model_version": self.version,
                    "image": bytes_to_io(image, "prompt_generator_temp.png")
                })
        except Exception:
            log.exception(
                f"An unexpected error has occurred during fetching of 'sdprem'."
            )
        else:
            return await resp.read()

    async def sdimg(
            self,
            image: bytes,
            prompt: str,
            negative: str = None,
            seed: str = None,
            cfg: float = 9.5
    ) -> bytes:
        """|coro|

        Performs style transfer using the Imagine API.

        Parameters
        ----------
        image: :class:`bytes`
            The image to use.
        prompt: :class:`str`
            The prompt to use.
        negative: Optional[:class:`str`]
            The negative prompt to use.
        seed: Optional[:class:`str`]
            The seed to use.
        cfg: Optional[:class:`float`]
            The cfg to use. (Must be in range (0; 16))

        Returns
        -------
        Optional[:class:`bytes`]
            The bytes of the image.
        """
        try:
            resp = await self.make_request("POST", "sdimg", data={
                    "model_version": self.version,
                    "prompt": prompt,
                    "negative_prompt": negative or "",
                    "seed": seed or "",
                    "cfg": cfg,
                    "image": bytes_to_io(image, "image.png")
                })
        except Exception:
            log.exception(
                f"An unexpected error has occurred during fetching of 'sdprem'."
            )
        else:
            return await resp.read()
