import asyncio
import contextlib
import json
import re
import uuid
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, Generic, TypeVar, overload

from app.utils.tasks import executor
from config import path

K = TypeVar('K')
V = TypeVar('V')

ObjectHook = Callable[[dict[str, Any]], Any]


class Config(Generic[K, V]):
    """A database-like config object. Internally based on `json`.

    Parameters
    ----------
    name : str
        The name of the config file.
    object_hook : ObjectHook, optional
        A function that will be called on every decoded JSON object, by default None
    encoder : Type[json.JSONEncoder], optional
        A custom JSON encoder, by default None
    load_later : bool, optional
        Whether to load the config later, by default False

    Attributes
    ----------
    name: str
        The name of the config file.
    object_hook: ObjectHook
        A function that will be called on every decoded JSON object, by default None
    encoder: Type[json.JSONEncoder]
        A custom JSON encoder, by default None
    loop: asyncio.AbstractEventLoop
        The event loop.
    lock: asyncio.Lock
        The lock for saving, loading and dumping the config file.

    Raises
    ------
    FileNotFoundError
        If the config file doesn't exist.
    """

    def __init__(
        self,
        name: str,
        *,
        object_hook: ObjectHook | None = None,
        encoder: type[json.JSONEncoder] | None = None,
        load_later: bool = False,
    ) -> None:
        self.name = name

        self.object_hook = object_hook
        self.encoder = encoder
        self.loop = asyncio.get_running_loop()
        self.lock = asyncio.Lock()
        self._db: dict[K, V] = {}

        if load_later:
            self.loop.create_task(self.load())
        else:
            self.load_from_file()

    @staticmethod
    def real_path(dest: str) -> Path:
        """Returns the real path of the config file."""
        return Path(path, dest)

    def load_from_file(self) -> None:
        """Loads the config from the file."""
        try:
            with self.real_path(self.name).open(encoding='utf-8') as f:
                self._db = json.load(f, object_hook=self.object_hook)
        except FileNotFoundError:
            self._db = {}

    async def load(self) -> None:
        """Loads the config from the file."""
        async with self.lock:
            self.loop.run_in_executor(None, self.load_from_file)

    @executor
    def _dump(self) -> None:
        """Dumps the config to the file."""
        temp = f'{uuid.uuid4()}-{self.name}.tmp'
        _path = self.real_path(temp)
        with _path.open('w', encoding='utf-8') as tmp:
            json.dump(self._db.copy(), tmp, ensure_ascii=True, cls=self.encoder, separators=(',', ':'))

        _path.replace(self.real_path(self.name))

    async def save(self) -> None:
        """Saves the config to the file."""
        async with self.lock:
            await self._dump()

    @overload
    def get(self, key: Any) -> V | None:
        ...

    @overload
    def get(self, key: Any, default: Any) -> V:
        ...

    def get(self, key: Any, default: Any = None) -> V | None:
        """Retrieves a config entry.

        Parameters
        ----------
        key : Any
            The key to retrieve.
        default : Any, optional
            The default value to return if the key is not found, by default None

        Returns
        -------
        V | Any
            The value of the key or the default value.
        """
        return self._db.get(str(key), default)

    @contextlib.asynccontextmanager
    async def aquire(self) -> None:
        """A context manager to aquire the lock.

        Example
        -------
        .. code-block:: python3

            async with config.aquire():
                await config.put('key', 'value')
        """
        try:
            await self.lock.acquire()
            yield
        finally:
            self.lock.release()

    async def put(self, key: Any, value: V) -> None:
        """Inserts a new config entry or edits a persitent one.

        Parameters
        ----------
        key : Any
            The key to add the value to.
        value : V
            The value to add.

        Raises
        ------
        KeyError
            If the key is not found.
        """
        self._db[str(key)] = value
        await self.save()

    async def put_deep(self, key: Any, value: V) -> None:
        """Inserts a new config entry or edits a persitent one.

        Key must follow the format of ``key1.key2.key3``.

        Parameters
        ----------
        key : Any
            The key to add the value to.
        value : V
            The value to add.

        Raises
        ------
        KeyError
            If the key is not found.
        """
        keys = [key.strip("'") for key in re.findall(r"[^\s.']+|'(?:\\.|[^'])*'", str(key))]
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
        """Removes a config entry.

        Parameters
        ----------
        key : Any
            The key to remove.

        Raises
        ------
        KeyError
            If the key is not found.
        """
        del self._db[str(key)]
        await self.save()

    async def remove_deep(self, key: Any) -> None:
        """Removes a config entry.

        Key must follow the format of ``key1.key2.key3``.

        Parameters
        ----------
        key : Any
            The key to remove.

        Raises
        ------
        KeyError
            If the key is not found.
        """
        keys = [key.strip("'") for key in re.findall(r"[^\s.']+|'(?:\\.|[^'])*'", str(key))]
        if len(keys) == 1:
            await self.remove(key)
            return

        temp = self._db
        for key in keys[:-1]:
            if key not in temp:
                temp[key] = {}
            temp = temp[key]

        del temp[keys[-1]]
        await self.save()

    async def add(self, key: Any, value: V) -> None:
        """Adds a value to a config entry.

        Parameters
        ----------
        key : Any
            The key to add the value to.
        value : V
            The value to add.

        Raises
        ------
        KeyError
            If the key is not found.
        """
        self._db[str(key)] += value
        await self.save()

    async def add_deep(self, key: Any, value: V) -> None:
        """Adds a value to a config entry.

        Key must follow the format of ``key1.key2.key3``.

        Parameters
        ----------
        key : Any
            The key to add the value to.
        value : V
            The value to add.

        Raises
        ------
        KeyError
            If the key is not found.
        """
        keys = [key.strip("'") for key in re.findall(r"[^\s.']+|'(?:\\.|[^'])*'", str(key))]
        if len(keys) == 1:
            await self.add(key, value)
            return

        temp = self._db
        for key in keys[:-1]:
            if key not in temp:
                temp[key] = {}
            temp = temp[key]

        temp[keys[-1]] += value
        await self.save()

    def all(self) -> dict[K, V]:
        """Returns all the config entries.

        Returns
        -------
        dict[K, V]
            All the config entries.
        """
        return self._db

    def __contains__(self, item: Any) -> bool:
        return str(item) in self._db

    def __iter__(self):
        yield from self._db

    def __add__(self, other: V) -> None:
        self._db += other

    def __sub__(self, other: V) -> None:
        self._db -= other

    def __and__(self, other: V) -> None:
        self._db &= other

    def __or__(self, other: V) -> None:
        self._db |= other

    def __xor__(self, other: V) -> None:
        self._db ^= other

    def __reversed__(self) -> Iterable[V]:
        return reversed(self._db)

    def __str__(self) -> str:
        return str(self.real_path(self.name))

    def __bool__(self) -> bool:
        return bool(self._db)

    def __copy__(self) -> dict[str, V]:
        return self._db.copy()

    def __dir__(self) -> Iterable[str]:
        return dir(self._db)

    def __eq__(self, other: V) -> bool:
        return self._db == other

    def __getitem__(self, item: Any) -> V:
        return self._db[str(item)]

    def __setitem__(self, item: Any, value: V) -> None:
        self._db[str(item)] = value

    def __delitem__(self, item: Any) -> None:
        del self._db[str(item)]

    def __len__(self) -> int:
        return len(self._db)
