import contextlib
import json
import os
from typing import Any, Dict, Generic, Optional, Type, TypeVar, Union, overload, Iterable
import uuid
import asyncio

from cogs.utils.tasks import executor
from cogs.utils.constants import ObjectHook, BOT_BASE_FOLDER

_T = TypeVar('_T')


class Config(Generic[_T]):
    """A database-like config object. Internally based on ``json``.

    Parameters
    ----------
    name : str
        The name of the config file.
    object_hook : Optional[ObjectHook], optional
        A function that will be called on every decoded JSON object, by default None
    encoder : Optional[Type[json.JSONEncoder]], optional
        A custom JSON encoder, by default None
    load_later : bool, optional
        Whether to load the config later, by default False

    Attributes
    ----------
    name : str
        The name of the config file.
    object_hook : Optional[ObjectHook]
        A function that will be called on every decoded JSON object, by default None
    encoder : Optional[Type[json.JSONEncoder]]
        A custom JSON encoder, by default None
    loop : asyncio.AbstractEventLoop
        The event loop.
    lock : asyncio.Lock
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

    @staticmethod
    def real_path(dest: str) -> str:
        """Returns the real path of the config file."""
        return os.path.join(BOT_BASE_FOLDER, dest)

    def load_from_file(self):
        """Loads the config from the file."""
        try:
            with open(self.real_path(self.name), 'r', encoding='utf-8') as f:
                self._db = json.load(f, object_hook=self.object_hook)
        except FileNotFoundError:
            self._db = {}

    async def load(self):
        """Loads the config from the file."""
        async with self.lock:
            self.loop.run_in_executor(None, self.load_from_file)

    @executor
    def _dump(self):
        """Dumps the config to the file."""
        temp = f'{uuid.uuid4()}-{self.name}.tmp'
        with open(self.real_path(temp), 'w', encoding='utf-8') as tmp:
            json.dump(self._db.copy(), tmp, ensure_ascii=True, cls=self.encoder, separators=(',', ':'))

        os.replace(self.real_path(temp), self.real_path(self.name))

    async def save(self) -> None:
        """Saves the config to the file."""
        async with self.lock:
            await self._dump()

    @overload
    def get(self, key: Any) -> Optional[Union[_T, Any]]:
        ...

    @overload
    def get(self, key: Any, default: Any) -> Union[_T, Any]:
        ...

    def get(self, key: Any, default: Any = None) -> Optional[Union[_T, Any]]:
        """Retrieves a config entry.

        Parameters
        ----------
        key : Any
            The key to retrieve.
        default : Any, optional
            The default value to return if the key is not found, by default None

        Returns
        -------
        Optional[Union[_T, Any]]
            The value of the key or the default value.
        """
        return self._db.get(str(key), default)

    @contextlib.asynccontextmanager
    async def aquire(self) -> None:
        """A context manager to aquire the lock.

        Example
        -------
        ... code-block:: python3

            async with config.aquire():
                await config.put('key', 'value')
        """
        try:
            await self.lock.acquire()
            yield
        finally:
            self.lock.release()

    async def put(self, key: Any, value: Union[_T, Any]) -> None:
        """Inserts a new config entry or edits a persitent one.

        Parameters
        ----------
        key : Any
            The key to add the value to.
        value : Union[_T, Any]
            The value to add.

        Raises
        ------
        KeyError
            If the key is not found.
        """
        self._db[str(key)] = value
        await self.save()

    async def deep_put(self, key: Any, value: Union[_T, Any]) -> None:
        """Inserts a new config entry or edits a persitent one.

        Key must follow the format of ``key1.key2.key3``.

        Parameters
        ----------
        key : Any
            The key to add the value to.
        value : Union[_T, Any]
            The value to add.

        Raises
        ------
        KeyError
            If the key is not found.
        """
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

    async def remove_from_deep(self, key: Any) -> None:
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
        keys = str(key).split('.')
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

    async def add(self, key: Any, value: Union[_T, Any]) -> None:
        """Adds a value to a config entry.

        Parameters
        ----------
        key : Any
            The key to add the value to.
        value : Union[_T, Any]
            The value to add.

        Raises
        ------
        KeyError
            If the key is not found.
        """
        self._db[str(key)] += value
        await self.save()

    async def add_deep(self, key: Any, value: Union[_T, Any]) -> None:
        """Adds a value to a config entry.

        Key must follow the format of ``key1.key2.key3``.

        Parameters
        ----------
        key : Any
            The key to add the value to.
        value : Union[_T, Any]
            The value to add.

        Raises
        ------
        KeyError
            If the key is not found.
        """
        keys = str(key).split('.')
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

    def all(self) -> Dict[str, Union[_T, Any]]:
        return self._db

    def __contains__(self, item: Any) -> bool:
        return str(item) in self._db

    def __iter__(self):
        for key in self._db:
            yield key

    def __add__(self, other: Union[_T, Any]) -> None:
        self._db += other

    def __sub__(self, other: Union[_T, Any]) -> None:
        self._db -= other

    def __and__(self, other: Union[_T, Any]) -> None:
        self._db &= other

    def __or__(self, other: Union[_T, Any]) -> None:
        self._db |= other

    def __xor__(self, other: Union[_T, Any]) -> None:
        self._db ^= other

    def __reversed__(self):
        return reversed(self._db)

    def __str__(self) -> str:
        return self.real_path(self.name)

    def __bool__(self):
        return bool(self._db)

    def __copy__(self):
        return self._db.copy()

    def __dir__(self) -> Iterable[str]:
        return dir(self._db)

    def __eq__(self, other: Union[_T, Any]) -> bool:
        return self._db == other

    def __getitem__(self, item: Any) -> Union[_T, Any]:
        return self._db[str(item)]

    def __setitem__(self, item: Any, value: Union[_T, Any]) -> None:
        self._db[str(item)] = value

    def __delitem__(self, item: Any) -> None:
        del self._db[str(item)]

    def __len__(self) -> int:
        return len(self._db)
