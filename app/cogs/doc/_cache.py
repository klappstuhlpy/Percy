from __future__ import annotations

import datetime
import fnmatch
import logging
import time
from typing import TYPE_CHECKING, Any

from app.utils.lock import lock

if TYPE_CHECKING:
    from ._cog import DocItem

WEEK_SECONDS = int(datetime.timedelta(weeks=1).total_seconds())

log = logging.getLogger(__name__)


def serialize_resource_id_from_doc_item(bound_args: dict) -> str:
    """Return the cache key of the DocItem `item` from the bound args of DocRedisCache.set."""
    item: DocItem = bound_args['item']
    return item_key(item)


class DocCache:
    """Custom Cache class for storing Markdown content of documentation symbols.

    This class is used to store the Markdown content of documentation symbols.
    The items in the cache are stored with time-to-live (TTL) and are expired after a week.
    """

    def __init__(self) -> None:
        self._internal_cache: dict[str, Any] = {}
        self._set_expires: dict[str, Any] = {}

    @lock('DocCache.set', serialize_resource_id_from_doc_item, wait=True)
    async def set(self, item: DocItem, value: str) -> None:
        """Set the Markdown `value` for the symbol `item`.

        All keys from a single page are stored together, expiring a week after the first set.
        """
        cache_key = item_key(item)
        needs_expire = False

        set_expire = self._set_expires.get(cache_key)
        if set_expire is None:
            ttl = self._get_cache_ttl(cache_key)
            log.debug('Checked TTL for `%s`.', cache_key)

            if ttl == -1:
                log.warning('Key `%s` had no expire set.', cache_key)
            if ttl < 0:
                needs_expire = True
            else:
                log.debug('Key `%s` has a %s TTL.', cache_key, ttl)
                self._set_expires[cache_key] = time.monotonic() + ttl - 0.1

        elif time.monotonic() > set_expire:
            needs_expire = True
            log.debug('Key `%s` expired in internal key cache.', cache_key)

        self._internal_cache.setdefault(cache_key, {})
        self._internal_cache[cache_key][item.symbol_id] = value

        if needs_expire:
            self._set_expires[cache_key] = time.monotonic() + WEEK_SECONDS
            log.info('Set %s to expire in a week.', cache_key)

    async def get(self, item: DocItem) -> str | None:
        """Return the Markdown content of the symbol `item` if it exists."""
        cache_key = item_key(item)
        if cache_key in self._internal_cache and item.symbol_id in self._internal_cache[cache_key]:
            return self._internal_cache[cache_key][item.symbol_id]
        return None

    async def delete(self, package: str) -> bool:
        """Remove all values for `package`; return True if at least one key was deleted, False otherwise."""
        pattern = f'{package}:*'

        package_keys = [
            key for key in self._internal_cache if fnmatch.fnmatchcase(key, pattern)
        ]
        if package_keys:
            for key in package_keys:
                del self._internal_cache[key]
            log.info('Deleted keys from cache: %s.', package_keys)
            self._set_expires = {
                key: expire for key, expire in self._set_expires.items() if not fnmatch.fnmatchcase(key, pattern)
            }
            return True
        return False

    def _get_cache_ttl(self, cache_key: str) -> int:
        """Return the time-to-live (TTL) of the cache key."""
        return self._set_expires.get(cache_key, WEEK_SECONDS)


def item_key(item: DocItem) -> str:
    """Get the redis key string from `item`."""
    return f'{item.package}:{item.relative_url_path.removesuffix('.html')}'
