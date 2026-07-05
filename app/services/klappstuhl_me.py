import os
import io
from typing import List, Union, Optional, Dict, Any, Tuple
import aiohttp
import discord
from matplotlib.pyplot import stairs

# Type alias for supported file inputs
FileType = Union[str, bytes, io.BytesIO, discord.File, discord.Attachment]


class KlappstuhlMeClient:
    """
    An asynchronous client for the Klappstuhl.me API using aiohttp,
    with native support for discord.py files and attachments.
    """

    def __init__(self, session: aiohttp.ClientSession, *, api_key: str | None, base_url: str = "https://klappstuhl.me/api"):
        """
        Initializes the client with the required API key.
        """
        self._session: aiohttp.ClientSession = session
        self.base_url: str = base_url.rstrip("/")
        self.api_key: str | None = api_key

    def __repr__(self) -> str:
        return f"<KlappstuhlMeClient base_url={self.base_url}>"

    def check_api_key(self) -> None:
        """Raises an exception if the API key is not set."""
        if not self.api_key:
            raise ValueError(
                "API key is required to use the KlappstuhlMeClient. Please set the KLAPPSTUHL_ME_API_TOKEN parameter."
            )

    @property
    def headers(self) -> dict[str, str | None]:
        """Returns a dictionary with the Authorization header set to the provided API key."""
        return {"Authorization": self.api_key}

    @staticmethod
    async def _handle_response(response: aiohttp.ClientResponse, expect_json: bool = True, return_type: str = "json") -> Any:
        """
        Handles HTTP responses, rate limiting, and errors.
        Rate limits return a 429 status code with x-ratelimit headers[cite: 1].
        """
        if not response.ok:
            error_text = await response.text()
            raise Exception(f"HTTP {response.status}: {error_text}")

        if return_type == "json":
            return await response.json()
        elif return_type == "bytes":
            return await response.read()
        elif return_type == "text":
            return await response.text()

        return None

    @staticmethod
    async def _add_file_to_form(
        form: aiohttp.FormData, field_name: str, file_data: FileType, filename: Optional[str] = None
    ) -> None:
        """Helper to append various file types to an aiohttp FormData object asynchronously."""
        if isinstance(file_data, str):
            with open(file_data, "rb") as f:
                form.add_field(field_name, f.read(), filename=filename or os.path.basename(file_data))
        elif isinstance(file_data, io.BytesIO):
            if not filename:
                raise ValueError("A filename must be provided when passing io.BytesIO.")
            form.add_field(field_name, file_data.getvalue(), filename=filename)
        elif isinstance(file_data, bytes):
            if not filename:
                raise ValueError("A filename must be provided when passing bytes.")
            form.add_field(field_name, file_data, filename=filename)
        elif isinstance(file_data, discord.File):
            # Read from the file pointer and reset the position in case it gets reused
            file_data.fp.seek(0)
            data = file_data.fp.read()
            file_data.fp.seek(0)
            form.add_field(field_name, data, filename=filename or file_data.filename)
        elif isinstance(file_data, discord.Attachment):
            # Asynchronously download the attachment bytes
            file_bytes = await file_data.read()
            form.add_field(field_name, file_bytes, filename=filename or file_data.filename)
        else:
            raise TypeError(
                f"Unsupported file type: {type(file_data)}. Expected str, bytes, io.BytesIO, discord.File, or discord.Attachment."
            )

    @staticmethod
    async def _prepare_file_or_url_form(
        file: Optional[FileType] = None,
        filename: Optional[str] = None,
        url: Optional[str] = None,
    ) -> aiohttp.FormData:
        """Helper to create multipart payload for endpoints accepting file or URL."""
        form = aiohttp.FormData()
        if url:
            form.add_field("url", url)
        elif file is not None:
            await KlappstuhlMeClient._add_file_to_form(form, "file", file, filename)
        else:
            raise ValueError("Must provide either a file or a URL.")
        return form

    # ==========================================
    # Images Group
    # ==========================================

    async def upload_images(
        self, files: List[Union[FileType, Tuple[str, Union[bytes, io.BytesIO]]]], expires_in: Optional[int] = None
    ) -> Dict[str, Any]:
        """
        Upload multiple image files (.apng, .png, .jpg, .jpeg, .gif, .avif)[cite: 1].
        Pass expires_in for auto-deletion (capped at 365 days)[cite: 1].
        """
        self.check_api_key()
        form = aiohttp.FormData()

        for file_item in files:
            if isinstance(file_item, tuple) and len(file_item) == 2:
                fname, fdata = file_item
                await self._add_file_to_form(form, "file", fdata, filename=fname)
            else:
                await self._add_file_to_form(form, "file", file_item)

        params = {}
        if expires_in is not None:
            params["expires_in"] = str(expires_in)

        async with self._session.post(
            f"{self.base_url}/images/upload", data=form, params=params, headers=self.headers
        ) as resp:
            return await self._handle_response(resp)

    async def delete_image(self, image_id: str) -> Dict[str, Any]:
        """Delete an image by its ID. You must be the uploader[cite: 1]."""
        self.check_api_key()
        async with self._session.delete(f"{self.base_url}/images/{image_id}", headers=self.headers) as resp:
            return await self._handle_response(resp)

    async def download_images(self, files: List[str]) -> bytes:
        """
        Bundle one or more images into a ZIP archive[cite: 1].
        Pass an empty list to receive every image you own[cite: 1].
        Returns the raw bytes of the ZIP file[cite: 1].
        """
        self.check_api_key()
        payload = {"files": files}
        async with self._session.post(f"{self.base_url}/images/download", json=payload, headers=self.headers) as resp:
            return await self._handle_response(resp, return_type="bytes")

    # ==========================================
    # Media Group
    # ==========================================

    async def manipulate_image(
        self,
        op: str,
        amount: Optional[float] = None,
        share: Optional[bool] = None,
        file: Optional[FileType] = None,
        filename: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Union[bytes, Dict[str, Any]]:
        """
        Apply a visual effect (blur, pixelate, deepfry, invert, grayscale)[cite: 1].
        Returns PNG bytes, or JSON if share=True[cite: 1].
        """
        self.check_api_key()
        form = await self._prepare_file_or_url_form(file=file, filename=filename, url=url)

        params = {}
        if amount is not None:
            params["amount"] = str(amount)
        if share is not None:
            params["share"] = str(share).lower()

        async with self._session.post(f"{self.base_url}/image/{op}", data=form, params=params, headers=self.headers) as resp:
            return await self._handle_response(resp, return_type="json" if share else "bytes")

    async def convert_image(
        self,
        to: str,
        quality: Optional[int] = None,
        share: Optional[bool] = None,
        file: Optional[FileType] = None,
        filename: Optional[str] = None,
        url: Optional[str] = None,
    ) -> Union[bytes, Dict[str, Any]]:
        """
        Transcode an image to a different raster format (png, jpeg, webp, gif, bmp, tiff)[cite: 1].
        Returns image bytes, or JSON if share=True[cite: 1].
        """
        self.check_api_key()
        form = await self._prepare_file_or_url_form(file=file, filename=filename, url=url)

        params = {"to": to}
        if quality is not None:
            params["quality"] = str(quality)
        if share is not None:
            params["share"] = str(share).lower()

        async with self._session.post(f"{self.base_url}/convert", data=form, params=params, headers=self.headers) as resp:
            return await self._handle_response(resp, return_type="json" if share else "bytes")

    async def get_metadata(
        self, file: Optional[FileType] = None, filename: Optional[str] = None, url: Optional[str] = None
    ) -> Dict[str, Any]:
        """Inspect an image and return its dimensions, format, color type, and byte size[cite: 1]."""
        self.check_api_key()
        form = await self._prepare_file_or_url_form(file=file, filename=filename, url=url)

        async with self._session.post(f"{self.base_url}/metadata", data=form, headers=self.headers) as resp:
            return await self._handle_response(resp)

    # ==========================================
    # Render Group
    # ==========================================

    async def render_code(
        self, code: str, language: Optional[str] = None, theme: Optional[str] = None
    ) -> Union[str, Dict[str, Any]]:
        """
        Render a syntax-highlighted code screenshot.
        Returns SVG string[cite: 1].
        """
        self.check_api_key()
        payload = {"code": code}
        if language:
            payload["language"] = language
        if theme:
            payload["theme"] = theme

        async with self._session.post(f"{self.base_url}/render/code", json=payload, headers=self.headers) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "application/json" in content_type:
                return await self._handle_response(resp, return_type="json")
            return await self._handle_response(resp, return_type="text")

    async def render_screenshot(
        self,
        url: str,
        dark_mode: bool = False,
        full_page: bool = False,
        mobile: bool = False,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> bytes:
        """Render a web page to a PNG[cite: 1]."""
        self.check_api_key()
        payload = {"url": url, "dark_mode": dark_mode, "full_page": full_page, "mobile": mobile}
        if width is not None:
            payload["width"] = width
        if height is not None:
            payload["height"] = height

        async with self._session.post(f"{self.base_url}/render/screenshot", json=payload, headers=self.headers) as resp:
            return await self._handle_response(resp, return_type="bytes")

    async def render_markdown_pdf(self, markdown: str) -> bytes:
        """Convert Markdown to a PDF document[cite: 1]."""
        self.check_api_key()
        payload = {"markdown": markdown}

        async with self._session.post(f"{self.base_url}/render/markdown-pdf", json=payload, headers=self.headers) as resp:
            return await self._handle_response(resp, return_type="bytes")

    async def transcode_media(self, to: str, file: FileType, filename: Optional[str] = None) -> bytes:
        """
        Convert media that needs ffmpeg (e.g., to=mp4 or to=jpg)[cite: 1].
        """
        self.check_api_key()
        form = aiohttp.FormData()
        await self._add_file_to_form(form, "file", file, filename)

        params = {"to": to}
        async with self._session.post(
            f"{self.base_url}/convert/transcode", data=form, params=params, headers=self.headers
        ) as resp:
            return await self._handle_response(resp, return_type="bytes")

    # ==========================================
    # Scan Group
    # ==========================================

    async def scan_file(self, file: FileType, filename: Optional[str] = None) -> Dict[str, Any]:
        """Scan an uploaded file for malware via ClamAV and VirusTotal[cite: 1]."""
        self.check_api_key()
        form = aiohttp.FormData()
        await self._add_file_to_form(form, "file", file, filename)

        async with self._session.post(f"{self.base_url}/scan", data=form, headers=self.headers) as resp:
            return await self._handle_response(resp)

    # ==========================================
    # Admin Group
    # ==========================================

    async def get_admin_updates(self) -> List[Dict[str, Any]]:
        """List container image-update status. Requires `admin:read` scope[cite: 1]."""
        self.check_api_key()
        async with self._session.get(f"{self.base_url}/admin/updates", headers=self.headers) as resp:
            return await self._handle_response(resp)