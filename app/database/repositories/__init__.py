from app.database.repositories.base import BaseRepository
from app.database.repositories.community import (
    GiveawaysRepository,
    HighlightsRepository,
    PollsRepository,
    StarboardRepository,
    TagsRepository,
)
from app.database.repositories.content import (
    AutoRespondersRepository,
    ComicsRepository,
    RoleMenusRepository,
    StatCountersRepository,
    TempChannelsRepository,
)
from app.database.repositories.economy import EconomyRepository, LevelingRepository
from app.database.repositories.guilds import AdminRepository, GuildsRepository
from app.database.repositories.integrations import EventWebhooksRepository, GuildTemplatesRepository
from app.database.repositories.moderation import CasesRepository, IncidentsRepository, ModerationRepository
from app.database.repositories.music import MusicSessionsRepository
from app.database.repositories.releases import ReleasesRepository
from app.database.repositories.stats import EmojiStatsRepository, GameStatsRepository, StatsRepository
from app.database.repositories.timers import TimersRepository
from app.database.repositories.users import (
    AniListRepository,
    PlaylistsRepository,
    UsersRepository,
    VotesRepository,
)
from app.database.repositories.watchlist import WatchlistRepository

__all__ = (
    'AdminRepository',
    'AniListRepository',
    'AutoRespondersRepository',
    'BaseRepository',
    'CasesRepository',
    'ComicsRepository',
    'EconomyRepository',
    'EmojiStatsRepository',
    'EventWebhooksRepository',
    'GameStatsRepository',
    'GiveawaysRepository',
    'GuildTemplatesRepository',
    'GuildsRepository',
    'HighlightsRepository',
    'IncidentsRepository',
    'LevelingRepository',
    'ModerationRepository',
    'MusicSessionsRepository',
    'PlaylistsRepository',
    'PollsRepository',
    'ReleasesRepository',
    'RoleMenusRepository',
    'StarboardRepository',
    'StatCountersRepository',
    'StatsRepository',
    'TagsRepository',
    'TempChannelsRepository',
    'TimersRepository',
    'UsersRepository',
    'VotesRepository',
    'WatchlistRepository',
)
