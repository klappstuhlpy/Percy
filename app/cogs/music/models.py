from __future__ import annotations

import enum
from typing import TYPE_CHECKING, Any

import discord
import wavelink
from discord.utils import MISSING
from wavelink import Playable

from app.core.pagination import TextSource
from app.database import BaseRecord
from app.utils import merge, pluralize

if TYPE_CHECKING:
    import datetime

    from .cog import PlaylistTools


class ShuffleMode(enum.IntEnum):
    """Enum representing the various shuffle modes.

    Attributes
    ----------
    off
        When off, the queue will not be shuffled.
    on
        When on, the queue will be shuffled.
    """

    off = 0
    on = 1


class Queue(wavelink.Queue):
    """A custom Queue class for the Player class."""

    def __init__(self) -> None:
        super().__init__()
        self._listen_together: int = MISSING
        self._shuffle: ShuffleMode = ShuffleMode.off

    def reset(self) -> None:
        """Resets the queue and history."""
        self._listen_together = MISSING
        self._shuffle = ShuffleMode.off
        super().reset()

    @property
    def all(self) -> list[wavelink.Playable]:
        """Returns a list of all tracks in the queue and history without duplicates."""
        return list(merge(self.history._items, self._items))

    @property
    def duration(self) -> int:
        """Returns the total duration of the queue and history."""
        return sum(track.length for track in self.all if track is not self._loaded)

    @property
    def history_is_empty(self) -> bool:
        """Returns True if the history has no members."""
        return not bool((len(self.history) - 1) if len(self.history) > 0 else 0)  # type: ignore

    @property
    def all_is_empty(self) -> bool:
        """Returns True if the queue + history has no members."""
        return not bool(self.all)

    @property
    def shuffle(self) -> ShuffleMode:
        """Property which returns a :class:`ShuffleMode` indicating if shuffle is activated.

        This property can be set with any :class:`ShuffleMode`.
        """
        return self._shuffle

    @shuffle.setter
    def shuffle(self, value: ShuffleMode) -> None:
        self._shuffle = value

    @property
    def listen_together(self) -> int:
        """Property which returns the listen together value.

        This property can be set with any integer.
        """
        return self._listen_together

    @listen_together.setter
    def listen_together(self, value: int) -> None:
        self._listen_together = value


class PlayerState(enum.Enum):
    PLAYING = 1
    PAUSED = 2
    STOPPED = 3


class SearchReturn(enum.Enum):
    NO_RESULTS = 1
    CANCELLED = 2
    NO_YOUTUBE_ALLOWED = 3


def is_dj(member: discord.Member) -> bool:
    """Checks if the Member has the DJ Role."""
    role = discord.utils.get(member.guild.roles, name="DJ")
    return role in member.roles


class Playlist(BaseRecord):
    cog: PlaylistTools
    id: int
    name: str
    owner_id: int
    created: datetime.datetime

    __slots__ = ("cog", "created", "id", "name", "owner_id", "tracks")

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.tracks: list[PlaylistTrack] = []

    def __repr__(self) -> str:
        return f"<Playlist id={self.id} name={self.name}>"

    def __str__(self) -> str:
        return self.name

    def __len__(self) -> int:
        return len(self.tracks)

    @property
    def is_liked_songs(self) -> bool:
        """:class:`bool`: Whether the playlist is the user's liked songs."""
        return self.name == "Liked Songs"

    @property
    def field_tuple(self) -> tuple[str, str]:
        """:class:`tuple`: Returns a tuple of the Playlist's name and value."""
        name = f"#{self.id}: {self.name}"
        if self.is_liked_songs:
            name = self.name

        value = None
        if len(self.tracks) >= 1:
            value = f"with {pluralize(len(self.tracks)):Track}"

        return name, value or "..."

    @property
    def choice_text(self) -> str:
        """:class:`str`: Returns the name of the Playlist."""
        if self.is_liked_songs:
            return self.name
        return f"[{self.id}] {self.name}"

    async def add_track(self, track: Playable) -> PlaylistTrack:
        """Add a track to the playlist.

        Parameters
        ----------
        track: wavelink.Playable
            The track to add to the playlist.

        Returns
        -------
        PlaylistTrack
            The track that was added to the playlist.
        """
        record = await self.cog.bot.db.playlists.add_track(self.id, track.title, track.uri)

        playlist_track = PlaylistTrack(record=record)
        self.tracks.append(playlist_track)
        return playlist_track

    async def remove_track(self, track: PlaylistTrack) -> None:
        """Remove a track from the playlist.

        Parameters
        ----------
        track: PlaylistTrack
            The track to remove from the playlist.
        """
        await self.cog.bot.db.playlists.remove_track(track.id)
        self.tracks.remove(track)

    def to_embeds(self) -> list[discord.Embed]:
        """Converts the Playlist to a list of Embeds."""
        source = TextSource(prefix=None, suffix=None, max_size=3080)
        if len(self.tracks) == 0:
            source.add_line("*This playlist is empty.*")
        else:
            for index, track in enumerate(self.tracks):
                source.add_line(f"`{index + 1}.` {track.text}")

        embeds = []
        for page in source.pages:
            embed = discord.Embed(
                title=f"{self.name} ({pluralize(len(self.tracks)):Track})", timestamp=self.created, description=page
            )
            embed.set_footer(text=f"[{self.id}] • Created at")
            embeds.append(embed)

        return embeds

    def to_select_option(self, value: Any) -> discord.SelectOption:
        """Converts the Playlist to a SelectOption."""
        return discord.SelectOption(
            label=self.name, emoji="\N{MULTIPLE MUSICAL NOTES}", value=str(value), description=f"{len(self.tracks)} Tracks"
        )

    async def delete(self) -> None:
        """Delete a playlist and all corresponding entries."""
        await self.cog.bot.db.playlists.delete_playlist(self.id)
        self.cog.get_playlists.invalidate(self.owner_id)

    async def clear(self) -> None:
        """Clear all Items in a playlist."""
        await self.cog.bot.db.playlists.clear_tracks(self.id)
        self.tracks.clear()


class PlaylistTrack(BaseRecord):
    id: int
    name: str
    url: str

    __slots__ = ('id', 'name', 'url')

    @property
    def text(self) -> str:
        return f'[{self.name}]({self.url}) (ID: {self.id})'
