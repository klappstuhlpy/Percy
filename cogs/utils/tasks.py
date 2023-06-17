import asyncio
import functools
import inspect
from contextlib import suppress
from datetime import datetime, timezone

from typing import Callable, Coroutine, Awaitable, ParamSpec, TypeVar, Self, Any, Hashable, Generator
import discord


T = TypeVar('T')
P = ParamSpec('P')

_background_tasks: set[asyncio.Task] = set()


class PerformanceMocker:
    """A mock object that can also be used in await expressions."""

    def __init__(self):
        self.loop = asyncio.get_running_loop()

    @property
    def permissions_for(self) -> discord.Permissions:
        perms = discord.Permissions.all()
        perms.administrator = False
        perms.embed_links = False
        perms.add_reactions = False
        return perms

    def __getattr__(self, attr: str) -> Self:
        return self

    def __call__(self, *args: Any, **kwargs: Any) -> Self:
        return self

    def __repr__(self) -> str:
        return '<PerformanceMocker>'

    def __await__(self) -> Generator[Any, None, Self]:
        future: asyncio.Future[Self] = self.loop.create_future()
        future.set_result(self)  # type: ignore
        return future.__await__()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: Any) -> Self:
        return self

    def __len__(self) -> int:
        return 0

    def __bool__(self) -> bool:
        return False


def executor(sync_function: Callable[P, T]) -> Callable[P, Awaitable[T]]:
    """A decorator that wraps a sync function in an executor, changing it into an async function.

    This allows processing functions to be wrapped and used immediately as an async function.

    Examples
    ---------

    Pushing processing with the Python Imaging Library into an executor:

    .. code-block:: python3

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
    async def sync_wrapper(*args: P.args, **kwargs: P.kwargs):
        """Asynchronous function that wraps a sync function with an executor """

        sync_function.__executor__ = True
        sync_function.__partial_async__ = True

        loop = asyncio.get_event_loop()
        internal_function = functools.partial(sync_function, *args, **kwargs)
        return await loop.run_in_executor(None, internal_function)

    return sync_wrapper


class Scheduler:
    """
    Schedule the execution of coroutines and keep track of them.

    When instantiating a :obj:`Scheduler`, a name must be provided. This name is used to distinguish the
    instance's log messages from other instances. Using the name of the class or module containing
    the instance is suggested.

    Coroutines can be scheduled immediately with :obj:`schedule` or in the future with :obj:`schedule_at`
    or :obj:`schedule_later`. A unique ID is required to be given in order to keep track of the
    resulting Tasks. Any scheduled task can be cancelled prematurely using :obj:`cancel` by providing
    the same ID used to schedule it.

    The ``in`` operator is supported for checking if a task with a given ID is currently scheduled.

    Any exception raised in a scheduled task is logged when the task is done.
    """

    def __init__(self, name: str):
        """
        Initialize a new :obj:`Scheduler` instance.

        Args:
            name: The name of the :obj:`Scheduler`. Used in logging, and namespacing.
        """
        self.name = name

        from launcher import get_logger
        self._log = get_logger(f"{__name__}.{name}")

        self._scheduled_tasks: dict[Hashable, asyncio.Task] = {}

    def __contains__(self, task_id: Hashable) -> bool:
        """
        Return :obj:`True` if a task with the given ``task_id`` is currently scheduled.

        Args:
            task_id: The task to look for.

        Returns:
            :obj:`True` if the task was found.
        """
        return task_id in self._scheduled_tasks

    def schedule(self, task_id: Hashable, coroutine: Coroutine) -> None:
        """
        Schedule the execution of a ``coroutine``.

        If a task with ``task_id`` already exists, close ``coroutine`` instead of scheduling it. This
        prevents unawaited coroutine warnings. Don't pass a coroutine that'll be re-used elsewhere.

        Args:
            task_id: A unique ID to create the task with.
            coroutine: The function to be called.
        """
        self._log.trace(f"Scheduling task #{task_id}...")

        msg = f"Cannot schedule an already started coroutine for #{task_id}"
        if inspect.getcoroutinestate(coroutine) != "CORO_CREATED":
            raise ValueError(msg)

        if task_id in self._scheduled_tasks:
            self._log.debug(f"Did not schedule task #{task_id}; task was already scheduled.")
            coroutine.close()
            return

        task = asyncio.create_task(_coro_wrapper(coroutine), name=f"{self.name}_{task_id}")
        task.add_done_callback(functools.partial(self._task_done_callback, task_id))

        self._scheduled_tasks[task_id] = task
        self._log.debug(f"Scheduled task #{task_id} {id(task)}.")

    def schedule_at(self, time: datetime, task_id: Hashable, coroutine: Coroutine) -> None:
        """
        Schedule ``coroutine`` to be executed at the given ``time``.

        If ``time`` is timezone aware, then use that timezone to calculate now() when subtracting.
        If ``time`` is naïve, then use UTC.

        If ``time`` is in the past, schedule ``coroutine`` immediately.

        If a task with ``task_id`` already exists, close ``coroutine`` instead of scheduling it. This
        prevents unawaited coroutine warnings. Don't pass a coroutine that'll be re-used elsewhere.

        Args:
            time: The time to start the task.
            task_id: A unique ID to create the task with.
            coroutine: The function to be called.
        """
        now_datetime = datetime.now(time.tzinfo) if time.tzinfo else datetime.now(tz=timezone.utc)
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
        """
        Schedule ``coroutine`` to be executed after ``delay`` seconds.

        If a task with ``task_id`` already exists, close ``coroutine`` instead of scheduling it. This
        prevents unawaited coroutine warnings. Don't pass a coroutine that'll be re-used elsewhere.

        Args:
            delay: How long to wait before starting the task.
            task_id: A unique ID to create the task with.
            coroutine: The function to be called.
        """
        self.schedule(task_id, self._await_later(delay, task_id, coroutine))

    def cancel(self, task_id: Hashable) -> None:
        """
        Unschedule the task identified by ``task_id``. Log a warning if the task doesn't exist.

        Args:
            task_id: The task's unique ID.
        """
        self._log.trace(f"Cancelling task #{task_id}...")

        try:
            task = self._scheduled_tasks.pop(task_id)
        except KeyError:
            self._log.warning(f"Failed to unschedule {task_id} (no task found).")
        else:
            task.cancel()

            self._log.debug(f"Unscheduled task #{task_id} {id(task)}.")

    def cancel_all(self) -> None:
        """Unschedule all known tasks."""
        self._log.debug("Unscheduling all tasks")

        for task_id in self._scheduled_tasks.copy():
            self.cancel(task_id)

    async def _await_later(
        self,
        delay: int | float,
        task_id: Hashable,
        coroutine: Coroutine
    ) -> None:
        """Await ``coroutine`` after ``delay`` seconds."""
        try:
            self._log.trace(f"Waiting {delay} seconds before awaiting coroutine for #{task_id}.")
            await asyncio.sleep(delay)

            # Use asyncio.shield to prevent the coroutine from cancelling itself.
            self._log.trace(f"Done waiting for #{task_id}; now awaiting the coroutine.")
            await asyncio.shield(coroutine)
        finally:
            # Close it to prevent unawaited coroutine warnings,
            # which would happen if the task was cancelled during the sleep.
            # Only close it if it's not been awaited yet. This check is important because the
            # coroutine may cancel this task, which would also trigger the finally block.
            state = inspect.getcoroutinestate(coroutine)
            if state == "CORO_CREATED":
                self._log.debug(f"Explicitly closing the coroutine for #{task_id}.")
                coroutine.close()
            else:
                self._log.debug(f"Finally block reached for #{task_id}; {state=}")

    def _task_done_callback(self, task_id: Hashable, done_task: asyncio.Task) -> None:
        """
        Delete the task and raise its exception if one exists.

        If ``done_task`` and the task associated with ``task_id`` are different, then the latter
        will not be deleted. In this case, a new task was likely rescheduled with the same ID.
        """
        self._log.trace(f"Performing done callback for task #{task_id} {id(done_task)}.")

        scheduled_task = self._scheduled_tasks.get(task_id)

        if scheduled_task and done_task is scheduled_task:
            # A task for the ID exists and is the same as the done task.
            # Since this is the done callback, the task is already done so no need to cancel it.
            self._log.trace(f"Deleting task #{task_id} {id(done_task)}.")
            del self._scheduled_tasks[task_id]
        elif scheduled_task:
            # A new task was likely rescheduled with the same ID.
            self._log.debug(
                f"The scheduled task #{task_id} {id(scheduled_task)} "
                f"and the done task {id(done_task)} differ."
            )
        elif not done_task.cancelled():
            self._log.warning(
                f"Task #{task_id} not found while handling task {id(done_task)}! "
                f"A task somehow got unscheduled improperly (i.e. deleted but not cancelled)."
            )

        with suppress(asyncio.CancelledError):
            exception = done_task.exception()
            # Log the exception if one exists.
            if exception:
                self._log.error(f"Error in task #{task_id} {id(done_task)}!", exc_info=exception)
