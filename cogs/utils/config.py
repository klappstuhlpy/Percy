import contextlib
import json
import os
from typing import Any, Dict, Generic, Optional, Type, TypeVar, Union, overload
import uuid
import asyncio

from cogs.utils.async_utils import executor
from cogs.utils.scope import ObjectHook

_T = TypeVar('_T')


class Config(Generic[_T]):
    """A config-like "database" object. Internally based on ``json``."""

    def __init__(
        self,
        name: str,
        *,
        object_hook: Optional[ObjectHook] = None,
        encoder: Optional[Type[json.JSONEncoder]] = None,
        load_later: bool = False,
    ):
        self.name = name
        self.object_hook = object_hook
        self.encoder = encoder
        self.loop = asyncio.get_running_loop()
        self.lock = asyncio.Lock()
        self._db: Dict[str, Union[_T, Any]] = {}

        if load_later:
            self.loop.create_task(self.load())
        else:
            self.load_from_file()

    def load_from_file(self):
        try:
            with open(self.name, 'r', encoding='utf-8') as f:
                self._db = json.load(f, object_hook=self.object_hook)
        except FileNotFoundError:
            self._db = {}

    async def load(self):
        async with self.lock:
            self.loop.run_in_executor(None, self.load_from_file)

    @executor
    def _dump(self):
        temp = f'{uuid.uuid4()}-{self.name}.tmp'
        with open(temp, 'w', encoding='utf-8') as tmp:
            json.dump(self._db.copy(), tmp, ensure_ascii=True, cls=self.encoder, separators=(',', ':'))

        os.replace(temp, self.name)

    async def save(self) -> None:
        async with self.lock:
            await self._dump()

    @overload
    def get(self, key: Any) -> Optional[Union[_T, Any]]:
        ...

    @overload
    def get(self, key: Any, default: Any) -> Union[_T, Any]:
        ...

    def get(self, key: Any, default: Any = None) -> Optional[Union[_T, Any]]:
        """Retrieves a config entry."""
        return self._db.get(str(key), default)

    @contextlib.asynccontextmanager
    async def aquire(self) -> None:
        try:
            await self.lock.acquire()
            yield
        finally:
            self.lock.release()

    async def put(self, key: Any, value: Union[_T, Any]) -> None:
        """Inserts a new config entry or edits a persitent one."""
        self._db[str(key)] = value
        await self.save()

    async def deep_put(self, key: Any, value: Union[_T, Any]) -> None:
        """Inserts a new config entry or edits a persitent one."""
        keys = str(key).split('.')
        if len(keys) == 1:
            await self.put(key, value)
            return

        temp = self._db
        for key in keys[:-1]:
            if key not in temp:
                temp[key] = {}
            temp = temp[key]

        temp[keys[-1]] = value
        await self.save()

    async def remove(self, key: Any) -> None:
        """Removes a config entry."""
        del self._db[str(key)]
        await self.save()

    def __contains__(self, item: Any) -> bool:
        return str(item) in self._db

    def __getitem__(self, item: Any) -> Union[_T, Any]:
        return self._db[str(item)]

    def __len__(self) -> int:
        return len(self._db)

    def all(self) -> Dict[str, Union[_T, Any]]:
        return self._db
