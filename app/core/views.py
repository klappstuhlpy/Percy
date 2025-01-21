import asyncio
import datetime
from typing import TypeVar, Generic, Callable, Any, Awaitable, TYPE_CHECKING, Generator, Iterable, TypeAlias

import asyncpg
import discord
from discord import Interaction
from discord.ext import commands

from app.rendering import AvatarCollage, PresenceChart

if TYPE_CHECKING:
    from app.core import Context
else:
    Context = commands.Context

__all__ = (
    'View',
    'TrashView',
    'ConfirmationView',
    'DisambiguatorView',
    'UserInfoView',
)

from app.utils import AsyncCallable, get_asset_url, helpers, Timer
from config import Emojis

T = TypeVar('T')

ViewIdentifierKwars: TypeAlias = 'discord.abc.Snowflake | Iterable[discord.abc.Snowflake] | bool | float| None'


@discord.utils.copy_doc(discord.ui.View)
class View(discord.ui.View):
    """A base view for all views.

    This class inherits from :class:`discord.ui.View` and adds a few more features to it.

    Attributes
    ----------
    timeout: float
        The timeout for the view.
    members: discord.Member | discord.abc.User | Iterable[discord.Member | discord.abc.User] | None
        The member that the view is attached to.
        If given, this implements a interaction_check that checks if the interaction is from the member.
    message: discord.Message | None
        The message that the view is attached to.
        This is optional and can be set after initialization of the View.

        Note: If you want to use `delete_on_timeout`, this attribute must be set!
    """

    def __init__(
            self,
            *,
            timeout: float | None = 180.0,
            members: discord.abc.Snowflake | Iterable[discord.abc.Snowflake] | None = None,
            clear_on_timeout: bool = True,
            delete_on_timeout: bool = False
    ) -> None:
        super().__init__(timeout=timeout)
        self.members = members
        self.message: discord.Message | None = None

        self._clear_on_timeout = clear_on_timeout
        self._delete_on_timeout = delete_on_timeout

    async def interaction_check(self, interaction: Interaction, /) -> bool:
        """The default interaction check for the view."""
        if not self.members:
            return True

        is_iterable = isinstance(self.members, Iterable)
        if (
                is_iterable and not discord.utils.get(self.members, id=interaction.user.id)
                or not is_iterable and interaction.user.id != self.members.id
        ):
            await interaction.response.send_message(
                f'{Emojis.error} This view is not meant for you.')
            return False
        return True

    async def on_timeout(self) -> None:
        """|coro|

        The method that is called when the view times out.
        """
        if self._clear_on_timeout:
            self.clear_items()
            return
        if self._delete_on_timeout:
            if self.message:
                return await self.message.delete()
        self.disable_all()

    def disable_item(self, item: discord.ui.Item) -> 'View':
        """Disables the given item.

        Parameters
        ----------
        item: discord.ui.Item
            The item to disable.

        Returns
        -------
        View
            The view with the item disabled. -> Chainable
        """
        item.style = discord.ButtonStyle.secondary
        item.disabled = True

        return self

    def disable_all(self, with_style: bool = False) -> None:
        """Disables all children of the view.

        Parameters
        ----------
        with_style: bool
            Whether to change the style of the buttons to secondary or not.
        """
        for item in self._children:
            if with_style:
                item.style = discord.ButtonStyle.secondary
            item.disabled = True

    def enable_all(self, with_style: bool = False) -> None:
        """Enables all children of the view.

        Parameters
        ----------
        with_style: bool
            Whether to change the style of the buttons to blurple or not.
        """
        for item in self._children:
            if with_style:
                item.style = discord.ButtonStyle.blurple
            item.disabled = False

    def walk_children(self) -> Generator[discord.ui.Item, None, None]:
        """Walks the children of the view."""
        for item in self._children:
            yield item

    @classmethod
    def from_items(cls, *items: discord.ui.Item, **view_kwargs: ViewIdentifierKwars) -> 'View':
        """Creates a view from an item.

        Parameters
        ----------
        items: discord.ui.Item
            The items to add to the view.
        view_kwargs: Any
            The keyword arguments to pass to the view.

        Returns
        -------
        View
            The view with the item added.
        """
        view = cls(**view_kwargs)
        for item in items:
            view.add_item(item)
        return view


class TrashView(View):
    def __init__(self, author: discord.Member) -> None:
        super().__init__(members=author)

    @discord.ui.button(
        style=discord.ButtonStyle.red, emoji=Emojis.trash,
        label='Delete', custom_id='delete'
    )
    async def delete(self, interaction: discord.Interaction, _) -> None:
        await interaction.message.delete()

    async def on_timeout(self) -> None:
        for children in self.children:
            children.disabled = True


class ConfirmationView(View):
    """A view for the confirmation dialog.

    This view is used to create a confirmation dialog with two buttons, one for confirming and one for canceling.

    Attributes
    ----------
    value: bool | None
        The value of the confirmation dialog.
    """
    def __init__(
            self,
            user: discord.Member | discord.User,
            *,
            true: str = 'Confirm',
            false: str = 'Cancel',
            timeout: float = None,
            defer: bool = False,
            delete_after: bool = False,
            hook: AsyncCallable[[discord.Interaction], None] = None,
    ) -> None:
        self.value: bool | None = None
        self.hook_value: Any = None

        self._defer: bool = defer
        self._delete_after: bool = delete_after
        self._hook: AsyncCallable[[discord.Interaction], None] = hook
        super().__init__(timeout=timeout, members=user)

        self._true_button = discord.ui.Button(style=discord.ButtonStyle.green, label=true)
        self._true_button.callback = self._make_callback(True)

        self._false_button = discord.ui.Button(style=discord.ButtonStyle.red, label=false)
        self._false_button.callback = self._make_callback(False)

        self.interaction: discord.Interaction | None = None
        self.message: discord.Message | None = None

        self.add_item(self._true_button)
        self.add_item(self._false_button)

    async def on_timeout(self) -> None:
        if self.message:
            await self.message.delete()

    def _make_callback(self, toggle: bool) -> Callable[[discord.Interaction], Awaitable[None]]:
        async def callback(interaction: discord.Interaction) -> None:
            self.value = toggle
            self.interaction = interaction

            self._true_button.disabled = True
            self._false_button.disabled = True

            if toggle:
                self._false_button.style = discord.ButtonStyle.secondary
            else:
                self._true_button.style = discord.ButtonStyle.secondary

            self.stop()
            if toggle and self._hook is not None:
                self.hook_value = await self._hook(interaction)
            elif self._defer:
                await interaction.response.defer()
            elif self._delete_after:
                try:
                    await interaction.message.delete()
                except discord.HTTPException:
                    if self.message:
                        await self.message.delete()

        return callback


class DisambiguatorView(View, Generic[T]):
    message: discord.Message
    selected: T

    def __init__(self, ctx: Context, data: list[T], entry: Callable[[T], Any]) -> None:
        super().__init__(members=ctx.author)
        self.ctx: Context = ctx
        self.data: list[T] = data

        options = []
        for i, x in enumerate(data):
            opt = entry(x)
            if not isinstance(opt, discord.SelectOption):
                opt = discord.SelectOption(label=str(opt))
            opt.value = str(i)
            options.append(opt)

        select = discord.ui.Select(options=options)

        select.callback = self.on_select_submit
        self.select = select
        self.add_item(select)

    async def on_select_submit(self, interaction: discord.Interaction) -> None:
        index = int(self.select.values[0])
        self.selected = self.data[index]
        await interaction.response.defer()
        if not self.message.flags.ephemeral:
            await self.message.delete()

        self.stop()


class UserInfoView(View):
    """A view for the user info command."""

    def __init__(self, ctx: Context, member: discord.Member | discord.User) -> None:
        super().__init__(timeout=120.0, members=ctx.author, clear_on_timeout=False)
        self.bot = ctx.bot
        self.member = member

        self.cog: Any = ctx.bot.get_cog('Stats')

    @staticmethod
    async def create_member_collage(results: list[dict[str, Any]]) -> discord.File | None:
        """Creates a member avatar collage."""
        avatars = [x['avatar'] for x in results]
        if not avatars:
            return

        collage = AvatarCollage(avatars)
        file = await asyncio.to_thread(collage.create)
        return file

    @discord.ui.button(label='Avatar Collage', style=discord.ButtonStyle.blurple, emoji='🖼️')
    async def avatar_collage(self, interaction: discord.Interaction, _) -> None:
        """The callback for the avatar collage button."""
        self.disable_item(self.avatar_collage)
        await interaction.response.edit_message(view=self)

        with Timer() as timer:
            results = await self.cog.get_avatar_history(self.member)
            fetching_time = timer.reset()
            file = await self.create_member_collage(results)

        if not file:
            await interaction.followup.send(f'{Emojis.error} No avatar history found. 🫠', ephemeral=True)
            return

        embed = discord.Embed(
            title=f'Avatar Collage for {self.member}',
            description=(
                f'`{'Fetching':<{12}}:` {fetching_time:.3f}s\n'
                f'`{'Generating':<{12}}:` {timer.seconds:.3f}s\n\n'
                f'Showing `{len(results)}` of up to `100` changes.'
            ),
            timestamp=results[-1]['changed_at'],
            colour=helpers.Colour.white()
        )
        embed.set_image(url=f'attachment://{file.filename if file else 'collage.png'}')
        embed.set_footer(text='Last updated')
        await interaction.followup.send(embed=embed, file=file, ephemeral=True)

    @discord.ui.button(label='Name History', style=discord.ButtonStyle.blurple, emoji='📜')
    async def name_history(self, interaction: discord.Interaction, _) -> None:
        """The callback for the name history button."""
        self.disable_item(self.name_history)
        await interaction.response.edit_message(view=self)

        un_history = await self.cog.get_item_history(self.member.id, 'name')
        nn_history = await self.cog.get_item_history(self.member.id, 'nickname')

        if not un_history:
            await interaction.followup.send(f'{Emojis.error} No name history found.', ephemeral=True)
            return

        un_text = ', '.join(
            f'`{x['item_value']}` ({discord.utils.format_dt(x['changed_at'], 'R')})' for x in un_history)
        nn_text = ', '.join(
            f'`{x['item_value']}` ({discord.utils.format_dt(x['changed_at'], 'R')})' for x in nn_history)
        embed = discord.Embed(
            title=f'Name History for {self.member}',
            description=(
                f'**Username History:**\n'
                f'{un_text}\n\n'
                f'**Nickname History:**\n'
                f'{nn_text}'
            ),
            timestamp=un_history[-1]['changed_at'],
            colour=helpers.Colour.white()
        )
        embed.set_footer(text='Username last updated')
        embed.set_thumbnail(url=get_asset_url(self.member))
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label='Status History', style=discord.ButtonStyle.blurple, emoji='📊')
    async def status_history(self, interaction: discord.Interaction, _) -> None:
        """The callback for the status history button."""
        self.disable_item(self.status_history)
        await interaction.response.edit_message(view=self)

        with Timer() as timer:
            history: list[asyncpg.Record] = await self.cog.get_presence_history(interaction.user.id, days=30)

            if not history:
                await interaction.followup.send(f'{Emojis.error} No presence history found.', ephemeral=True)
                return

            fetching_time = timer.reset()

            record_dict: dict[datetime.datetime, Any] = {
                record['changed_at']: [
                    record['status'],
                    record['status_before'],
                ]
                for record in history
            }

            status_timers: dict[str, float] = {
                'Online': 0,
                'Idle': 0,
                'Do Not Disturb': 0,
                'Offline': 0,
            }

            for i, (changed_at, statuses) in enumerate(record_dict.items()):
                if i != 0:
                    status_timers[statuses[1]] += (list(record_dict.keys())[i - 1] - changed_at).total_seconds()

            if all(value == 0 for value in status_timers.values()):
                return await interaction.followup.send(
                    f'{Emojis.error} Not enough data to generate a chart.', ephemeral=True)

            analyzing_time = timer.reset()

            presence_instance = PresenceChart(
                labels=['Online', 'Offline', 'DND', 'Idle'],
                colors=['#43b581', '#747f8d', '#f04747', '#fba31c'],
                values=[
                    int(status_timers['Online']),
                    int(status_timers['Offline']),
                    int(status_timers['Do Not Disturb']),
                    int(status_timers['Idle']),
                ]
            )
            canvas: discord.File = await asyncio.to_thread(presence_instance.create)

        embed = discord.Embed(
            title=f'Past 1 Month User Activity of {interaction.user}',
            description=(
                f'`{'Fetching':<{12}}:` {fetching_time:.3f}s\n'
                f'`{'Analyzing':<{12}}:` {analyzing_time:.3f}s\n'
                f'`{'Generating':<{12}}:` {timer.seconds:.3f}s'
            ),
            timestamp=min(record_dict.keys()),
            colour=helpers.Colour.white()
        )
        embed.set_image(url=f'attachment://{canvas.filename}')
        embed.set_footer(text='Watching since')
        await interaction.followup.send(embed=embed, file=canvas, ephemeral=True)
