from app.cogs.music.cog import Music, setup
from app.cogs.music.models import Playlist, PlaylistTrack, Queue, ShuffleMode
from app.cogs.music.player import Player

__all__ = (
    'Music',
    'Player',
    'Playlist',
    'PlaylistTrack',
    'Queue',
    'ShuffleMode',
    'setup',
)
