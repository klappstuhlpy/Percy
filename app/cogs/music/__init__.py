from app.cogs.music.cog import Music, PlaylistTools, setup
from app.cogs.music.models import Playlist, PlaylistTrack, Queue, ShuffleMode
from app.cogs.music.player import Player

__all__ = (
    'Music',
    'Player',
    'Playlist',
    'PlaylistTools',
    'PlaylistTrack',
    'Queue',
    'ShuffleMode',
    'setup',
)
