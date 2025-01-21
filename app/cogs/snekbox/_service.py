import logging

from aiohttp import ClientConnectorError

from app.core import Bot

log = logging.getLogger(__name__)

FAILED_REQUEST_ATTEMPTS = 3
MAX_PASTE_LENGTH = 100_000
PASTE_URL = 'https://paste.pythondiscord.com/{key}'


class PasteUploadError(Exception):
    """Raised when an error is encountered uploading to the paste service."""


class PasteTooLongError(Exception):
    """Raised when content is too large to upload to the paste service."""


async def send_to_paste_service(bot: Bot, contents: str, *, extension: str = "", max_length: int = MAX_PASTE_LENGTH) -> str:
    """
    Upload `contents` to the paste service.

    Add `extension` to the output URL. Use `max_length` to limit the allowed contents length
    to lower than the maximum allowed by the paste service.

    Raise `ValueError` if `max_length` is greater than the maximum allowed by the paste service.
    Raise `PasteTooLongError` if `contents` is too long to upload, and `PasteUploadError` if uploading fails.

    Return the generated URL with the extension.
    """
    if max_length > MAX_PASTE_LENGTH:
        raise ValueError(f'`max_length` must not be greater than {MAX_PASTE_LENGTH}')

    extension = extension and f'.{extension}'

    contents_size = len(contents.encode())
    if contents_size > max_length:
        log.info('Contents too large to send to paste service.')
        raise PasteTooLongError(f'Contents of size {contents_size} greater than maximum size {max_length}')

    log.debug('Sending contents of size %r bytes to paste service.', contents_size)
    paste_url = PASTE_URL.format(key='documents')
    for attempt in range(1, FAILED_REQUEST_ATTEMPTS + 1):
        try:
            async with bot.session.post(paste_url, data=contents) as response:
                response_json = await response.json()
        except ClientConnectorError:
            log.warning(
                'Failed to connect to paste service at url %s, '
                'trying again (%r/%r).', paste_url, attempt, FAILED_REQUEST_ATTEMPTS
            )
            continue
        except:
            log.exception(
                'An unexpected error has occurred during handling of the request, '
                'trying again (%r/%r).', attempt, FAILED_REQUEST_ATTEMPTS
            )
            continue

        if 'message' in response_json:
            log.warning(
                'Paste service returned error %s with status code %r, '
                'trying again (%r/%r).', response_json['message'], response.status, attempt, FAILED_REQUEST_ATTEMPTS
            )
            continue
        if 'key' in response_json:
            log.info('Successfully uploaded contents to paste service behind key %s.', response_json['key'])

            paste_link = PASTE_URL.format(key=response_json['key']) + extension

            if extension == '.py':
                return paste_link

            return paste_link + '?noredirect'

        log.warning(
            'Got unexpected JSON response from paste service: %s\n'
            'trying again (%r/%r).', response_json, attempt, FAILED_REQUEST_ATTEMPTS
        )

    raise PasteUploadError('Failed to upload contents to paste service')
