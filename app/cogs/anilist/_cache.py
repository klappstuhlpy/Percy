import logging
import time
from typing import Any

from app.utils.lock import lock

log = logging.getLogger(__name__)


class AniListExpiringCache:
    """Cache for the Comics cog.

    This class is used to store the Comics for a Brand.
    """

    def __init__(self) -> None:
        self._internal_cache: dict[int, str] = {}
        self._set_expires: dict[int, Any] = {}

    def __repr__(self) -> str:
        return f'<AniListExpiringCache len={len(self._internal_cache)}>'

    def reset(self) -> None:
        """Clear the internal cache."""
        self._internal_cache.clear()

    @lock('AniListExpiringCache.set', 'anilist cache set', wait=True)
    async def set(self, item: int, value: str, ttl: float) -> None:
        """Set the user_id `item` to the `value` with a time-to-live of `expires`."""
        self._internal_cache[item] = value
        self._set_expires[item] = time.monotonic() + ttl

    def get(self, item: int) -> str | None:
        """Get the value of the user_id `item` and check if item is expired or not."""
        if item in self._internal_cache:
            if time.monotonic() > self._set_expires[item]:
                del self._internal_cache[item]
                del self._set_expires[item]
                return None
            return self._internal_cache[item]
        return None

    def delete(self, item: int) -> bool:
        """Delete the item from the cache and expire set."""
        if item in self._internal_cache:
            del self._internal_cache[item]
            del self._set_expires[item]
            return True
        return False
