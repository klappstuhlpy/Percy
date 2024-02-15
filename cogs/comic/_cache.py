import fnmatch

from cogs.comic._data import Brand, GenericComic
from cogs.utils.lock import lock
from launcher import get_logger

log = get_logger(__name__)


def serialize_resource_id_from_brand(bound_args: dict) -> str:
    """Return the cache key of the Brand `item` from the bound args of ComicCache.set."""
    item: Brand = bound_args['item']
    return f'comic:{item}'


class ComicCache:
    """Cache for the Comics cog."""

    def __init__(self, namespace: str = 'comic'):
        self.namespace: str = namespace
        self._internal_cache: dict[str, list[GenericComic]] = {}

    def __repr__(self):
        return f'<ComicCache namespace={self.namespace} len={len(self._internal_cache)}>'

    @lock('ComicCache.set', serialize_resource_id_from_brand, wait=True)
    async def set(self, item: Brand, value: list[GenericComic]) -> None:
        """Set the Comics `value` for the brand `item`."""
        cache_key = f'{self.namespace}:{item}'

        self._internal_cache.setdefault(cache_key, [])
        self._internal_cache[cache_key] = value

    def get(self, item: Brand) -> list[GenericComic] | None:
        """Return the Markdown content of the symbol `item` if it exists."""
        cache_key = f'{self.namespace}:{item}'
        if cache_key in self._internal_cache:
            return self._internal_cache[cache_key]
        return None

    def delete(self, package: str) -> bool:
        """Remove all values for `package`; return True if at least one key was deleted, False otherwise."""
        pattern = f'{self.namespace}:{package}:*'

        package_keys = [
            key for key in self._internal_cache.keys() if fnmatch.fnmatchcase(key, pattern)
        ]
        if package_keys:
            for key in package_keys:
                del self._internal_cache[key]
            log.info(f'Deleted keys from cache: {package_keys}.')
            return True
        return False
