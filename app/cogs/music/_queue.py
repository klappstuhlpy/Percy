import enum

import wavelink
from discord.utils import MISSING

from app.utils import merge


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
        return not bool((len(self.history) - 1) if len(self.history) > 0 else 0)

    @property
    def all_is_empty(self) -> bool:
        """Returns True if the queue + history has no members."""
        return not bool(self.all)

    @property
    def shuffle(self) -> ShuffleMode:
        """Property which returns a :class:`queue.ShuffleMode` indicating if shuffle is activated.

        This property can be set with any :class:`queue.ShuffleMode`.
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
