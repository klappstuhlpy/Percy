import logging

from app.cogs.comic._data import Brand, GenericComic
from app.utils.lock import lock

log = logging.getLogger(__name__)


def serialize_resource_id_from_brand(bound_args: dict) -> str:
    """Return the cache key of the Brand `item` from the bound args of ComicCache.set."""
    item: Brand = bound_args['item']
    return f'comic:{item}'


class ComicCache:
    """Cache for the Comics cog.

    This class is used to store the Comics for a Brand.
    """

    def __init__(self) -> None:
        self._internal_cache: dict[Brand, list[GenericComic]] = {}

    def __repr__(self) -> str:
        return f'<ComicCache len={len(self._internal_cache)}>'

    def reset(self) -> None:
        """Clear the internal cache."""
        self._internal_cache.clear()

    @lock('ComicCache.set', serialize_resource_id_from_brand, wait=True)
    async def set(self, item: Brand, value: list[GenericComic]) -> None:
        """Set the Comics `value` for the brand `item`."""
        self._internal_cache.setdefault(item, [])
        self._internal_cache[item] = value

    def get(self, item: Brand) -> list[GenericComic] | None:
        """Return the Comics for the brand `item`."""
        if item in self._internal_cache:
            return self._internal_cache[item]
        return None

    def delete(self, item: Brand) -> bool:
        """Delete the Comics for the brand `item`."""
        if item in self._internal_cache:
            del self._internal_cache[item]
            return True
        return False
