import asyncio
import functools
import gc
import inspect
import logging
import warnings
from abc import ABC
from collections.abc import Awaitable, Callable, Coroutine, Generator, Hashable
from contextlib import suppress
from datetime import UTC, datetime, timezone
from types import SimpleNamespace
from typing import Any, ParamSpec, Protocol, TypeVar

warnings.filterwarnings('ignore', category=RuntimeWarning)

T = TypeVar('T')
P = ParamSpec('P')

__all__ = (
    'executor',
    'scheduled_coroutine',
    'Scheduler',
)

_background_tasks: set[asyncio.Task] = set()


def executor(sync_function: Callable[P, T]) -> Callable[..., Awaitable[T]] | Callable[P, Awaitable[T]] | Callable[..., Awaitable[T]]:
    """A decorator that wraps a sync function in an executor, changing it into an async function.

    This allows processing functions to be wrapped and used immediately as an async function.

    Examples
    ---------

    Pushing processing with the Python Imaging Library into an executor:

    ... code-block:: python3

        from io import BytesIO
        from PIL import Image

        from cogs.utils.executor import executor


        @executor
        def color_processing(color: discord.Color):
            with Image.new('RGB', (64, 64), color.to_rgb()) as im:
                buff = BytesIO()
                im.save(buff, 'png')

            buff.seek(0)
            return buff

        @bot.command()
        async def color(ctx: Context, color: discord.Color=None):
            color = color or ctx.author.color
            buff = await color_processing(color=color)

            await ctx.send(file=discord.File(fp=buff, filename='color.png'))
    """

    @functools.wraps(sync_function)
    async def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        """Asynchronous function that wraps a sync function with an executor """

        sync_function.__executor__ = True
        sync_function.__partial_async__ = True

        loop = asyncio.get_event_loop()
        internal_function = functools.partial(sync_function, *args, **kwargs)
        return await loop.run_in_executor(None, internal_function)

    return sync_wrapper


C = TypeVar('C', bound=Callable[..., Coroutine])


def get_function_from_coroutine(coroutine: Coroutine) -> Callable:
    """Return the function that the coroutine was created from."""
    # this is a bit hacky and not very nice, but it works for now
    # maybe improve this in the future!
    referrers = gc.get_referrers(coroutine.cr_code)
    return next(filter(lambda ref: inspect.isfunction(ref), referrers))


class ScheduledTaskProtocol(Protocol[C]):

    async def __call__(self, *args: Any, **kwargs: Any) -> C:
        ...

    def before_task(self, coro: Coroutine) -> Coroutine:
        ...

    def after_task(self, coro: Coroutine) -> Coroutine:
        ...


def scheduled_coroutine(func: Callable[..., Coroutine]) -> ScheduledTaskProtocol[Coroutine]:
    """A decorator that schedules a coroutine to run in the background.

    This decorator schedules a coroutine to run in the background. It can be awaited to
    wait for the coroutine to finish.

    Returns
    -------
    Callable[..., Coroutine]
        The actual decorator.
    """

    func.__tasks__ = SimpleNamespace(before_task=None, after_task=None)

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Coroutine:
        """The actual wrapper for the cache to be assigned to the corresponding function."""
        return await func(*args, **kwargs)

    def _before_task(coro: Coroutine) -> Coroutine:
        if not inspect.iscoroutinefunction(coro):
            raise TypeError(f'Expected coroutine function, received {coro.__class__.__name__}.')
        func.__tasks__.before_task = coro  # type: ignore
        return coro

    def _after_task(coro: Coroutine) -> Coroutine:
        if not inspect.iscoroutinefunction(coro):
            raise TypeError(f'Expected coroutine function, received {coro.__class__.__name__}.')
        func.__tasks__.after_task = coro  # type: ignore
        return coro

    wrapper.before_task = _before_task
    wrapper.after_task = _after_task

    return wrapper


class Scheduler(ABC):
    """Schedule the execution of coroutines and keep track of them.

    When instantiating a :obj:`Scheduler`, a name must be provided. This name is used to distinguish the
    instance's log messages from other instances. Using the name of the class or module containing
    the instance is suggested.

    Coroutines can be scheduled immediately with :obj:`schedule` or in the future with :obj:`schedule_at`
    or :obj:`schedule_later`. A unique ID is required to be given to keep track of the
    resulting Tasks. Any scheduled task can be canceled prematurely using :obj:`cancel` by providing
    the same ID used to schedule it.

    The `in` operator is supported for checking if a task with a given ID is currently scheduled.

    Any exception raised in a scheduled task is logged when the task is done.
    """

    def __init__(self, injected: object | str) -> None:
        """Initialize a new :obj:`Scheduler` instance.

        Parameters
        ----------
        injected: :class:`object` | :class:`str`
            The class or instance that the scheduler is injected from. If a string is given, then it
            is used as the name of the scheduler instead.
        """
        self.injected = None if isinstance(injected, str) else injected
        self.name = self.injected.__class__.__name__ if self.injected else injected

        self.log = logging.getLogger(f'{__name__}.{self.name}')

        self._origin_lookup: dict[Hashable, Callable] = {}
        self._scheduled_tasks: dict[Hashable, asyncio.Task] = {}

    def get_args_from_cls(self) -> tuple[TypeVar, ...]:
        """Return the arguments to use when calling the scheduler."""
        # resolve the `self` argument if the scheduler is injected (or provided) from a class
        return () if isinstance(self.injected, str) else (self.injected,)

    def __contains__(self, task_id: Hashable) -> bool:
        """Return :obj:`True` if a task with the given `task_id` is currently scheduled.

        Parameters
        ----------
        task_id: :class:`Hashable`
            The ID of the task to check.

        Returns
        -------
        :class:`bool`
            :obj:`True` if a task with the given `task_id` is currently scheduled.

        Notes
        -----
        This method is called when using the `in` operator on a :obj:`Scheduler` instance.
        """
        return task_id in self._scheduled_tasks

    def schedule(self, task_id: Hashable, coroutine: Coroutine) -> None:
        """Schedule the execution of a `coroutine`.

        If a task with `task_id` already exists, close `coroutine` instead of scheduling it. This
        prevents unawaited coroutine warnings. Don't pass a coroutine that'll be re-used elsewhere.

        Parameters
        ----------
        task_id: :class:`Hashable`
            A unique identifier for the task.
        coroutine: :class:`Coroutine`
            The coroutine to schedule.

        Raises
        ------
        ValueError
            If `coroutine` is already running.

        Notes
        -----
        The coroutine is scheduled with :func:`asyncio.create_task` and named with the scheduler's name
        and the task's ID. This allows the task to be identified in the logs.

        The task is added to the scheduler's internal dictionary of scheduled tasks. This allows the
        task to be cancelled prematurely with :obj:`cancel`.

        If a task with `task_id` already exists, close `coroutine` instead of scheduling it. This
        prevents unawaited coroutine warnings. Don't pass a coroutine that'll be re-used elsewhere.

        The task is added to the scheduler's internal dictionary of scheduled tasks. This allows the
        task to be cancelled prematurely with :obj:`cancel`.
        """
        self.log.debug('Scheduling task #%s...', task_id)

        if inspect.getcoroutinestate(coroutine) != 'CORO_CREATED':
            raise ValueError(f'Cannot schedule an already started coroutine for #{task_id}')

        if task_id in self._scheduled_tasks:
            self.log.debug('Did not schedule task #%s; task was already scheduled.', task_id)
            coroutine.close()
            return

        if task_id not in self._origin_lookup:
            self._origin_lookup[task_id] = get_function_from_coroutine(coroutine)

        origin = self._origin_lookup[task_id]
        if hasattr(origin, '__tasks__') and (before_task := origin.__tasks__.before_task):
            self.schedule(f'{task_id}:before_task', before_task(*self.get_args_from_cls()))

        task = asyncio.create_task(coroutine, name=f'{self.name}_{task_id}')
        task.add_done_callback(functools.partial(self._task_done_callback, task_id))

        self._scheduled_tasks[task_id] = task
        self.log.debug('Scheduled task #%s %s.', task_id, id(task))

    def schedule_at(self, time: datetime, task_id: Hashable, coroutine: Coroutine) -> None:
        """Schedule `coroutine` to be executed at the given `time`.

        Parameters
        ----------
        time: :class:`datetime.datetime`
            The time to schedule the coroutine at.
        task_id: :class:`Hashable`
            A unique ID to create the task with.
        coroutine: :class:`Coroutine`
            The function to be called.

        Raises
        ------
        ValueError
            If `time` is not a timezone aware datetime object.

        Notes
        -----
        If `time` is timezone aware, then use that timezone to calculate now() when subtracting.
        If `time` is naïve, then use UTC.

        If `time` is in the past, schedule `coroutine` immediately.

        If a task with `task_id` already exists, close `coroutine` instead of scheduling it. This
        prevents unawaited coroutine warnings. Don't pass a coroutine that'll be re-used elsewhere.
        """
        now_datetime = datetime.now(time.tzinfo) if time.tzinfo else datetime.now(tz=UTC)
        delay = (time - now_datetime).total_seconds()
        if delay > 0:
            coroutine = self._await_later(delay, task_id, coroutine)

        self.schedule(task_id, coroutine)

    def schedule_later(
        self,
        delay: int | float,
        task_id: Hashable,
        coroutine: Coroutine
    ) -> None:
        """Schedule `coroutine` to be executed after `delay` seconds.

        Parameters
        ----------
        delay: :class:`int` | :class:`float`
            The number of seconds to wait before executing `coroutine`.
        task_id: :class:`Hashable`
            A unique ID to create the task with.
        coroutine: :class:`Coroutine`
            The function to be called.

        Raises
        ------
        ValueError
            If `delay` is negative.

        Notes
        -----
        If `delay` is negative, schedule `coroutine` immediately.

        If a task with `task_id` already exists, close `coroutine` instead of scheduling it. This
        prevents unawaited coroutine warnings. Don't pass a coroutine that'll be re-used elsewhere.
        """
        self.schedule(task_id, self._await_later(delay, task_id, coroutine))

    def cancel(self, task_id: Hashable) -> None:
        """Unschedule the task identified by `task_id`. Log a warning if the task doesn't exist.

        Parameters
        ----------
        task_id: :class:`Hashable`
            The ID of the task to unschedule.

        Notes
        -----
        If the task identified by `task_id` is already done, then this method does nothing.
        """
        self.log.debug('Cancelling task #...', task_id)

        try:
            task = self._scheduled_tasks.pop(task_id)
        except KeyError:
            self.log.warning('Failed to unschedule %s (no task found).', task_id)
        else:
            task.cancel()

            self.log.debug('Unscheduled task #%s %s.', task_id, id(task))

    def cancel_all(self) -> None:
        """Unschedule all known tasks."""
        self.log.debug('Unscheduling all tasks')

        for task_id, _ in self.walk_tasks():
            self.cancel(task_id)

    def walk_tasks(self) -> Generator[tuple[Hashable, asyncio.Task], None, None]:
        """Safely walk through all tasks and yield them."""
        yield from self._scheduled_tasks.copy().items()

    async def _await_later(
        self,
        delay: int | float,
        task_id: Hashable,
        coroutine: Coroutine
    ) -> None:
        """|coro|

        Await `coroutine` after `delay` seconds.

        Parameters
        ----------
        delay: :class:`int` | :class:`float`
            The number of seconds to wait before executing `coroutine`.
        task_id: :class:`Hashable`
            A unique ID to create the task with.
        coroutine: :class:`Coroutine`
            The function to be called.

        Raises
        ------
        ValueError
            If `delay` is negative.

        Notes
        -----
        If `delay` is negative, await `coroutine` immediately.

        If a task with `task_id` already exists, close `coroutine` instead of scheduling it. This
        prevents unawaited coroutine warnings. Don't pass a coroutine that'll be re-used elsewhere.
        """
        self._origin_lookup[task_id] = get_function_from_coroutine(coroutine)

        try:
            self.log.debug('Waiting %r seconds before awaiting coroutine for #%s.', delay, task_id)
            await asyncio.sleep(delay)

            # Use asyncio.shield to prevent the coroutine from cancelling itself.
            self.log.debug('Done waiting for #%s; now awaiting the coroutine.',task_id)
            await asyncio.shield(coroutine)
        finally:
            # Close it to prevent unawaited coroutine warnings,
            # which would happen if the task was cancelled during the sleep.
            # Only close it if it's not been awaited yet. This check is important because the
            # coroutine may cancel this task, which would also trigger the final block.
            state = inspect.getcoroutinestate(coroutine)
            if state == 'CORO_CREATED':
                self.log.debug('Explicitly closing the coroutine for #%s.', task_id)
                coroutine.close()
            else:
                self.log.debug('Finally block reached for #%s; %s', task_id, state)

    def _task_done_callback(self, task_id: Hashable, done_task: asyncio.Task) -> None:
        """Delete the task and raise its exception if one exists.

        Parameters
        ----------
        task_id: :class:`Hashable`
            The ID of the task that's done.
        done_task: :class:`asyncio.Task`
            The task that's done.

        Notes
        -----
        If the task identified by `task_id` is not the same as `done_task`, then log a warning.

        If `done_task` and the task associated with `task_id` are different, then the latter
        will not be deleted. In this case, a new task was likely rescheduled with the same ID.
        """
        self.log.debug('Performing done callback for task #%s %s.', task_id, id(done_task))

        scheduled_task = self._scheduled_tasks.get(task_id)

        if scheduled_task and done_task is scheduled_task:
            # A task for the ID exists and is the same as the done task.
            # Since this is the done callback, the task is already done so no need to cancel it.
            self.log.debug('Deleting task #%s %s.', task_id, id(done_task))
            del self._scheduled_tasks[task_id]

            origin = self._origin_lookup.pop(task_id, None)
            if hasattr(origin, '__tasks__') and (after_task := origin.__tasks__.after_task):
                self.schedule(f'{task_id}:after_task', after_task(*self.get_args_from_cls()))

        elif scheduled_task:
            # A new task was likely rescheduled with the same ID.
            self.log.debug(
                'The scheduled task #%s %s and the done task %s differ.',
                task_id, id(scheduled_task), id(done_task)
            )

        elif not done_task.cancelled():
            self.log.warning(
                'Task #%s not found while handling task %s! '
                'A task somehow got unscheduled improperly (i.e. deleted but not cancelled).',
                task_id, id(done_task)
            )

        with suppress(asyncio.CancelledError):
            exception = done_task.exception()
            # Log the exception if one exists.
            if exception:
                self.log.error('Error in task #%s %s!', task_id, id(done_task), exc_info=exception)
