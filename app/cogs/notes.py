import datetime
from collections.abc import Callable
from typing import Any

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands

from app.core import Bot, Cog, Timer, describe
from app.core.pagination import LinePaginator
from app.database import BaseRecord
from app.utils import Time, fuzzy, get_shortened_string, helpers, positive_reply, truncate


@app_commands.allowed_installs(guilds=False, users=True)
class Notes(app_commands.Group, name='notes', description='A group of commands for taking notes.'):
    pass


class Note(BaseRecord):
    """A class representing a note."""

    bot: Bot
    id: int
    owner_id: int
    content: str
    topic: str | None
    created_at: datetime.datetime
    timer: Timer | None

    __slots__ = ('bot', 'content', 'created_at', 'id', 'owner_id', 'timer', 'topic')

    @property
    def owner(self) -> discord.User | None:
        """Optional[:class:`discord.User`]: The owner of the note."""
        return self.bot.get_user(self.owner_id)

    async def _update(
            self,
            key: Callable[[tuple[int, str]], str],
            values: dict[str, Any],
            *,
            connection: asyncpg.Connection | None = None,
    ) -> 'Note':
        """|coro|

        Update the note in the database.

        Parameters
        ----------
        key: Callable[[Tuple[:class:`int`, :class:`str`]], :class:`str`]
            A function that returns the key for the update query.
        values: Dict[:class:`str`, Any]
            The values to update.
        connection: Optional[:class:`asyncpg.Connection`]
            The connection to use for the query.
        """
        record = await self.bot.db.notes.update_note(self.id, key, values, connection=connection)
        return self.__class__(bot=self.bot, record=record)

    async def get_timer(self) -> Timer | None:
        """|coro|

        Get the timer for the note.

        Returns
        --------
        Optional[:class:`Timer`]
            The timer for the note.
        """
        self.timer = await self.bot.timers.fetch('note', note_id=self.id)
        return self.timer

    async def delete(self) -> None:
        """|coro|

        Delete the note.
        """
        await self.bot.db.notes.delete_note(self.id)


class UserNotes(Cog, name='Notes'):
    """A simple cog for taking notes across discord."""

    emoji = '\N{MEMO}'
    notes = Notes()

    async def notes_autocomplete(
            self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[int]]:
        records = await self.bot.db.notes.get_owner_notes(interaction.user.id, sort_by_topic=True)
        results = fuzzy.finder(current, records, key=lambda p: f'{p["topic"]} {p["content"]}', raw=True)
        return [
            app_commands.Choice(name=get_shortened_string(
                length, start, f'{note["topic"]} - {note["content"]}'), value=note['id'])
            for length, start, note in results[:20]
        ]

    async def get_note(self, note_id: int, owner_id: int | None = None, /, *, with_timer: bool = True) -> Note | None:
        """|coro|

        Get a note by its ID.

        Parameters
        ----------
        note_id: :class:`int`
            The note ID to get.
        owner_id: Optional[:class:`int`]
            The owner ID of the note.
        with_timer: :class:`bool`
            Whether to include the timer for the note.

        Returns
        --------
        :class:`Note`
            The note.
        """
        record = await self.bot.db.notes.get_note(note_id, owner_id)
        if not record:
            return

        note = Note(bot=self.bot, record=record)
        if with_timer:
            await note.get_timer()
        return note

    async def get_user_notes(self, user_id: int, /, *, with_timers: bool = True) -> list[Note] | None:
        """|coro|

        Get all notes for a user.

        Parameters
        ----------
        user_id: :class:`int`
            The user ID to get notes for.
        with_timers: :class:`bool`
            Whether to include timers for the notes.

        Returns
        --------
        List[:class:`Note`]
            A list of notes for the user.
        """
        records = await self.bot.db.notes.get_owner_notes(user_id)
        if not records:
            return

        resolved = [Note(bot=self.bot, record=record) for record in records]
        if with_timers:
            for note in resolved:
                await note.get_timer()
        return resolved

    async def create_note(
            self,
            user_id: int,
            note: str,
            topic: str | None = None,
    ) -> int:
        """|coro|

        Create a note for a user.

        Parameters
        ----------
        user_id: :class:`int`
            The user ID to create the note for.
        note: :class:`str`
            The note to create.
        topic: Optional[:class:`str`]
            The topic of the note.

        Returns
        --------
        :class:`int`
            The ID of the created note.
        """
        return await self.bot.db.notes.create_note(user_id, note, topic)

    @notes.command(name='add', description='Take a note.')
    @describe(
        content='The content of the note.',
        topic='The topic of the note.',
        expiration='When the note should expire. Must be a future time.'
    )
    async def note_add(
            self,
            interaction: discord.Interaction,
            content: str,
            topic: str | None = None,
            expiration: Time | None = None
    ) -> None:
        """Take a note."""
        if len(content) > 2000:
            raise commands.BadArgument('The reminder message is too long.')

        ctx = await self.bot.get_context(interaction)

        if ctx.replied_message is not None and ctx.replied_message.content:
            content = ctx.replied_message.content

        note_id = await self.create_note(ctx.author.id, content, topic)

        # Check if time is too close to the current time
        if expiration and expiration.dt < discord.utils.utcnow() + datetime.timedelta(seconds=15):
            raise commands.BadArgument('This time is too close to the current time. Try a time at least 15 seconds in the future.')
        else:
            zone = await self.bot.db.get_user_timezone(ctx.author.id)
            await self.bot.timers.create(
                expiration.dt,
                'note',
                note_id=note_id,
                created=ctx.message.created_at,
                timezone=zone or 'UTC',
            )

        topc = f'with topic **{topic}**' if topic else 'with no topic'
        response = f'{positive_reply()} I\'ve created the note {topc} for you.'
        if expiration:
            response += f' (Expires: {discord.utils.format_dt(expiration.dt, 'R')})'

        await ctx.send_success(response)

    @notes.command(name='list', description='List all notes.')
    async def note_list(self, interaction: discord.Interaction) -> None:
        """List all notes."""
        ctx = await self.bot.get_context(interaction)
        notes = await self.get_user_notes(ctx.author.id)
        if not notes:
            raise commands.BadArgument('You don\'t have any notes.')

        embed = discord.Embed(
            title='Your Notes',
            color=helpers.Colour.white(),
            timestamp=discord.utils.utcnow()
        )
        fields: list[tuple[str, str, bool]] = []
        for note in notes:
            text = f'Note [`{note.id}`]'
            if note.topic:
                text += f' - Topic: {note.topic}'
            if note.timer:
                text += f'\nExpires: {note.timer.human_delta()}'
            fields.append((text, truncate(note.content, 1024), False))

        await LinePaginator.start(ctx, entries=fields, embed=embed, per_page=6)

    @notes.command(name='delete', description='Delete a note.')
    @describe(note_id='The ID of the note to delete.')
    @app_commands.autocomplete(note_id=notes_autocomplete)
    async def note_delete(self, interaction: discord.Interaction, note_id: int) -> None:
        """Delete a note."""
        ctx = await self.bot.get_context(interaction)
        note = await self.get_note(note_id, ctx.author.id, with_timer=False)
        if not note:
            raise commands.BadArgument('I couldn\'t find that note.')

        await note.delete()
        await ctx.send_success(f'{positive_reply()} I\'ve deleted the note for you.')

    @notes.command(name='view', description='View a note.')
    @describe(note_id='The ID of the note to view.')
    @app_commands.autocomplete(note_id=notes_autocomplete)
    async def note_view(self, interaction: discord.Interaction, note_id: int) -> None:
        """View a note."""
        ctx = await self.bot.get_context(interaction)
        note = await self.get_note(note_id, ctx.author.id)
        if not note:
            raise commands.BadArgument('I couldn\'t find that note.')

        embed = discord.Embed(
            title=f'Note {note.id}',
            description=note.content,
            color=helpers.Colour.white(),
            timestamp=note.created_at
        )
        footer = []
        if note.topic:
            footer.append(f'Topic: {note.topic}')
        footer.append('Created at')

        embed.set_footer(text=' • '.join(footer))

        if note.timer:
            embed.add_field(name='Expires', value=note.timer.human_delta(), inline=False)

        await ctx.send(embed=embed)

    @notes.command(name='edit', description='Edit a note.')
    @describe(
        note_id='The ID of the note to edit.',
        content='The new content of the note.',
        topic='The new topic of the note.'
    )
    @app_commands.autocomplete(note_id=notes_autocomplete)
    async def note_edit(
            self,
            interaction: discord.Interaction,
            note_id: int,
            content: str | None = None,
            topic: str | None = None
    ) -> None:
        """Edit a note."""
        ctx = await self.bot.get_context(interaction)
        note = await self.get_note(note_id, ctx.author.id, with_timer=False)
        if not note:
            raise commands.BadArgument('I couldn\'t find that note.')

        if not content and not topic:
            raise commands.BadArgument('You need to provide new content or a new topic to edit the note.')

        if len(content) > 2000:
            raise commands.BadArgument('The reminder message is too long.')

        await note.update(
            content=content or note.content,
            topic=topic or note.topic
        )
        await ctx.send_success(f'{positive_reply()} I\'ve edited the note for you.')

    @notes.command(name='clear', description='Clear all notes.')
    async def note_clear(self, interaction: discord.Interaction) -> None:
        """Clear all notes."""
        ctx = await self.bot.get_context(interaction)

        await self.bot.db.notes.clear_owner_notes(ctx.author.id)
        await ctx.send_success(f'{positive_reply()} I\'ve cleared all your notes.')

    @Cog.listener()
    async def on_note_timer_complete(self, timer: Timer) -> None:
        """|coro|

        Called when a note timer is complete.

        Parameters
        ----------
        timer: :class:`Timer`
            The timer that completed.
        """
        note = await self.get_note(timer[0])
        await note.delete()

        if note.owner:
            topic = f'with topic **{note.topic}**' if note.topic else 'with no topic'
            await note.owner.send(f'Your note {topic} has expired:\n{note.content}')


async def setup(bot: Bot) -> None:
    await bot.add_cog(UserNotes(bot))
