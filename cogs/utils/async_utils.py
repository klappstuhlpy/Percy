import asyncio
import functools
import logging

from typing import Callable, Optional, Coroutine, Awaitable, ParamSpec, TypeVar, Self, Any

import discord
from discord.ext import commands


T = TypeVar('T')
P = ParamSpec('P')


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

    def __await__(self):
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


class AsyncPartialCache:
    def __init__(self, input_msg: Optional[str] = None, output_msg: Optional[str] = None):
        self.input_msg = input_msg
        self.output_msg = output_msg

        self.tasks = []
        self.running_tasks = set()
        self.completed_tasks = set()
        self.logger = logging.getLogger(__name__)

        self.loop = asyncio.get_running_loop()

        self.task_queues = {}

    @staticmethod
    def _real_signature(func: Callable[..., Coroutine]):
        return f"{func.__module__}.{func.__name__}"  # type: ignore

    async def run_task(self, task: Callable[..., Coroutine], timeout: Optional[float] = None):
        task_name = self._real_signature(task)
        self.logger.debug(f"Starting task: {task_name}")
        self.running_tasks.add(task_name)
        try:
            if timeout:
                task_coro = task()
                task_future = asyncio.ensure_future(task_coro)
                await asyncio.wait_for(task_future, timeout)
            else:
                await task()
        except asyncio.TimeoutError:
            self.logger.error(f"Task {task_name} timed out")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            self.logger.error(f"Error in task {task_name}: {str(e)}", exc_info=(type(e), e, e.__traceback__))
        else:
            self.logger.debug(f"Task completed: {task_name}")
        finally:
            self.running_tasks.remove(task_name)
            self.completed_tasks.add(task_name)
            if task_name in self.task_queues:
                self.release_queued_functions(task_name)

    async def __aenter__(self) -> 'AsyncPartialCache':
        if self.input_msg:
            self.logger.info(f"{self.input_msg}  {discord.utils.utcnow()}")
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.wait_for_tasks()
        if self.output_msg:
            self.logger.info(f"{self.output_msg}  {discord.utils.utcnow()}")

    async def wait_for_tasks(self):
        if self.tasks:
            await asyncio.gather(*self.tasks)

    def add_task(self, task: Callable[..., Coroutine], timeout: Optional[float] = None):
        self.tasks.append(self.run_task(task, timeout))

    def is_task_running(self, task: Callable[..., Coroutine]):
        return self._real_signature(task) in self.running_tasks

    def queue_function(self, task_name: str, func: Callable[..., Coroutine]):
        if task_name not in self.task_queues:
            self.task_queues[task_name] = asyncio.Queue()
        self.task_queues[task_name].put_nowait(func)

    def release_queued_functions(self, task_name):
        if task_name in self.task_queues:
            while not self.task_queues[task_name].empty():
                func = self.task_queues[task_name].get_nowait()
                asyncio.create_task(func)


class TaskInterruption(commands.CheckFailure):
    def __init__(self, task_name: str):
        self.task_name = task_name

        fmt = task_name.replace('_', ' ').title()
        super().__init__(f"<:warning:1113421726861238363> Task **{fmt}** is currently running. *Please wait a moment...*")


def block_if_task_running(task):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            async_cache = args[0]
            if async_cache.is_task_running(task):
                async_cache.queue_function(task.__name__, func(*args, **kwargs))
                raise TaskInterruption(task.__name__)
            return await func(*args, **kwargs)

        return wrapper

    return decorator
