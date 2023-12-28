import asyncio
import functools
import inspect
import types
from collections import defaultdict
from collections.abc import Awaitable, Hashable
from functools import partial
from typing import Any, Callable
from weakref import WeakValueDictionary

from cogs.utils import function
from cogs.utils.constants import Coro
from cogs.utils.function import BoundArgs, command_wraps
from launcher import get_logger

log = get_logger(__name__)
__lock_dicts: defaultdict[Hashable] = defaultdict(WeakValueDictionary)  # noqa

_IdCallableReturn = Hashable | Awaitable[Hashable]
_IdCallable = Callable[[BoundArgs], _IdCallableReturn]
ResourceId = Hashable | _IdCallable


class LockedResourceError(RuntimeError):
    """
    Exception raised when an operation is attempted on a locked resource.

    Attributes
    ----------
    type: :class:`str`
        The type of the resource.
    id: :class:`Hashable`
        The ID of the resource.
    """

    def __init__(self, resource_type: str, resource_id: Hashable):
        self.type = resource_type
        self.id = resource_id

        # Do not log this error because we use this only to indicate
        # if a resource is locked or not and handle it accordingly
        self.bypass_log = True

        super().__init__(
            f'Cannot operate on {self.type.lower()} `{self.id}`; '
            'it is currently locked and in use by another operation.'
        )


class SharedEvent:
    """Context manager managing an internal event exposed through the wait coro.

    While any code is executing in this context manager, the underlying event will not be set;
    when all of the holders finish the event will be set.

    This is useful for waiting for all holders of a lock to finish.
    A holder should enter the context manager before acquiring the lock and exit after releasing it.
    """

    def __init__(self):
        self._active_count = 0
        self._event = asyncio.Event()
        self._event.set()

    def __enter__(self):
        """Increment the count of the active holders and clear the internal event."""
        self._active_count += 1
        self._event.clear()

    def __exit__(self, _exc_type, _exc_val, _exc_tb):  # noqa: ANN001
        """Decrement the count of the active holders; if 0 is reached set the internal event."""
        self._active_count -= 1
        if not self._active_count:
            self._event.set()

    async def wait(self) -> None:
        """Wait for all active holders to exit."""
        await self._event.wait()


def lock(
        namespace: Hashable,
        resource_id: ResourceId,
        *,
        raise_error: bool = False,
        wait: bool = False,
) -> Callable:
    """Turn the decorated coroutine function into a mutually exclusive operation on a `resource_id`.

    This generally locks the decorated function to a single invocation at a time for a given
    to ensure that the resource is not being used by multiple operations at once.

    Note
    ----
    If `wait` is True, wait until the lock becomes available. Otherwise, if any other mutually
    exclusive function currently holds the lock for a resource, do not run the decorated function
    and return None.

    If `raise_error` is True, raise `LockedResourceError` if the lock cannot be acquired.

    `namespace` is an identifier used to prevent collisions among resource IDs.

    `resource_id` identifies a resource on which to perform a mutually exclusive operation.
    It may also be a callable or awaitable which will return the resource ID given an ordered
    mapping of the parameters' names to arguments' values.

    If decorating a command, this decorator must go before (below) the `command` decorator.

    Parameters
    ----------
    namespace: :class:`Hashable`
        An identifier used to prevent collisions among resource IDs.
    resource_id: :class:`Hashable` | :class:`Callable` | :class:`Awaitable`
        The ID of the resource on which to perform a mutually exclusive operation.
    raise_error: :class:`bool`
        Whether to raise `LockedResourceError` if the lock cannot be acquired.
    wait: :class:`bool`
        Whether to wait until the lock becomes available.

    Returns
    -------
    :class:`Callable`
        The decorated coroutine function.
    """

    def decorator(func: types.FunctionType) -> types.FunctionType:
        name = func.__name__

        @command_wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            log.trace(f'{name}: mutually exclusive decorator called')

            if callable(resource_id):
                log.trace(f'{name}: binding args to signature')
                bound_args = function.get_bound_args(func, args, kwargs)

                log.trace(f'{name}: calling the given callable to get the resource ID')
                id_ = resource_id(bound_args)

                if inspect.isawaitable(id_):
                    log.trace(f'{name}: awaiting to get resource ID')
                    id_ = await id_
            else:
                id_ = resource_id

            log.trace(f'{name}: getting the lock object for resource {namespace!r}:{id_!r}')

            # Get the lock for the ID. Create a lock if one doesn't exist yet.
            locks = __lock_dicts[namespace]
            waiter_lock = locks.setdefault(id_, asyncio.Lock())

            # It's safe to check an asyncio.Lock is free before acquiring it because:
            #   1. Synchronous code like `if not lock_.locked()` does not yield execution
            #   2. `asyncio.Lock.acquire()` does not internally await anything if the lock is free
            #   3. awaits only yield execution to the event loop at actual I/O boundaries
            if wait or not waiter_lock.locked():
                log.debug(f'{name}: acquiring lock for resource {namespace!r}:{id_!r}...')
                async with waiter_lock:
                    return await func(*args, **kwargs)
            else:
                log.info(f'{name}: aborted because resource {namespace!r}:{id_!r} is locked')
                if raise_error:
                    raise LockedResourceError(str(namespace), id_)
                return None

        return wrapper

    return decorator


def watch_func(func: types.FunctionType) -> Coro:
    """Watch the decorated function.

     Sets the `__is_running__` attribute to True when the function is running.

     Parameters
     ----------
     func: :class:`types.FunctionType`
         The function to watch.
    """

    @functools.wraps(func)
    async def wrapper(*args, **kwargs) -> Any:
        name = func.__name__

        bound_args = function.get_bound_args(func, args, kwargs)
        log.trace(f'{name}: calling the given callable to get the resource ID')

        id_ = f'{name}.' + '.'.join(['%s=%r' % (k, v) for k, v in bound_args.items()])
        log.trace(f'{name}: getting the lock object for resource {func.__name__!r}:{id_!r}')

        _inter_lock = __lock_dicts.setdefault(id_, asyncio.Lock())

        await _inter_lock.acquire()
        try:
            result = await func(*args, **kwargs)
        finally:
            _inter_lock.release()

        return result
    return wrapper


def lock_func(
        watched: Coro,
        *,
        raise_error: bool = False,
        wait: bool = False,
) -> Callable:
    """Apply the `lock` decorator using the return value of the given function as the ID.

    This is generally used to lock a decorated function if another function assigned by `watched` is currently running
    or in use.

    Note
    ----
    The `watched` function must be a coroutine function and must be decorated with `watch_func`.

    Parameters
    ----------
    watched: :class:`Coro`
        The function that gets watched and checked if it is running.
    raise_error: :class:`bool`
        Whether to raise `LockedResourceError` if the lock cannot be acquired.
    wait: :class:`bool`
        Whether to wait until the lock becomes available.
    """

    def decorator(func: types.FunctionType) -> types.FunctionType:
        name = func.__name__

        @command_wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            log.trace(f'{name}: mutually exclusive decorator called')

            bound_args = function.get_bound_args(func, args, kwargs)
            log.trace(f'{name}: calling the given callable to get the resource ID')

            id_ = f'{name}.' + '.'.join(['%s=%r' % (k, v) for k, v in bound_args.items()])

            log.trace(f'{name}: getting the lock object for resource {watched.__name__!r}:{id_!r}')

            # Get the lock for the ID. Create a lock if one doesn't exist yet.
            func_lock = __lock_dicts.setdefault(id_, asyncio.Lock())

            # It's safe to check an asyncio.Lock is free before acquiring it because:
            #   1. Synchronous code like `if not lock_.locked()` does not yield execution
            #   2. `asyncio.Lock.acquire()` does not internally await anything if the lock is free
            #   3. awaits only yield execution to the event loop at actual I/O boundaries
            if wait or not func_lock.locked():
                log.debug(f'{name}: acquiring lock for resource {watched.__name__!r}:{id_!r}...')
                async with func_lock:
                    return await func(*args, **kwargs)
            else:
                log.info(f'{name}: aborted because resource {watched.__name__!r}:{id_!r} is locked')
                if raise_error:
                    raise LockedResourceError(str(func.__name__), id_)
                return None

        return wrapper
    return decorator


def lock_arg(
        namespace: Hashable,
        name_or_pos: function.Argument,
        func: Callable[[Any], _IdCallableReturn] = None,
        *,
        raise_error: bool = False,
        wait: bool = False,
) -> Callable:
    """Apply the `lock` decorator using the value of the arg at the given name/position as the ID.

    Parameters
    ----------
    namespace: :class:`Hashable`
        The namespace to use for the lock.
    name_or_pos: :class:`Argument`
        The name or position of the argument to use as the ID.
    func: Optional[:class:`Callable`[:class:`Any`, :class:`_IdCallableReturn`]]
        A callable or awaitable which will return the ID given the argument value.
        If not given, the argument value itself will be used as the ID.
    raise_error: :class:`bool`
        Whether to raise `LockedResourceError` if the lock cannot be acquired.
    wait: :class:`bool`
        Whether to wait until the lock becomes available.

    Returns
    -------
    :class:`Callable`
        The decorator.
    """
    decorator_func = partial(lock, namespace, raise_error=raise_error, wait=wait)
    return function.get_arg_value_wrapper(decorator_func, name_or_pos, func)
