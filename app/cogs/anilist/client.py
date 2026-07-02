from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.clients import BaseHTTPClient, HTTPClientError

if TYPE_CHECKING:
    import aiohttp

API_ENDPOINT = 'https://graphql.anilist.co'


class AniListClient(BaseHTTPClient):
    """GraphQL client for AniList, hardened by :class:`~app.clients.BaseHTTPClient`.

    Rate-limit retries, transport-error backoff and circuit-breaking are inherited;
    this class only owns the GraphQL queries and AniList's quirk of returning HTTP 500
    for transient/empty results (surfaced as ``None`` so callers fall back to empty).
    """

    def __init__(self, session: aiohttp.ClientSession) -> None:
        super().__init__(session, name='AniList')

    async def _request(self, query: str, **variables: dict[str, Any]) -> dict[str, Any] | None:
        headers = variables.pop('headers', {})
        try:
            return await self.fetch(
                'POST', API_ENDPOINT,
                json={'query': query, 'variables': variables}, headers=headers,
            )
        except HTTPClientError as exc:
            if exc.status == 500:
                return None
            raise

    async def media(self, **variables: Any) -> list[dict[str, Any]]:
        data = await self._request(query=self._media_query, **variables)
        if data:
            return data['data']['Page']['media']
        return []

    async def character(self, **variables: Any) -> list[dict[str, Any]]:
        data = await self._request(query=self._character_query, **variables)
        if data:
            return data['data']['Page']['characters']
        return []

    async def staff(self, **variables: Any) -> list[dict[str, Any]]:
        data = await self._request(query=self._staff_query, **variables)
        if data:
            return data['data']['Page']['staff']
        return []

    async def studio(self, **variables: Any) -> list[dict[str, Any]]:
        data = await self._request(query=self._studio_query, **variables)
        if data:
            return data['data']['Page']['studios']
        return []

    async def user(self, **variables: Any) -> dict[str, Any]:
        data = await self._request(query=self._user_query, **variables)
        if data:
            return data['data']['Viewer']
        return {}

    async def user_favourites(self, **variables: Any) -> dict[str, Any]:
        data = await self._request(query=self._user_favourites_query, **variables)
        if data:
            return data['data']['Viewer']
        return {}

    async def media_list(self, **variables: Any) -> list[dict[str, Any]]:
        data = await self._request(query=self._media_list_query, **variables)
        if data:
            lists = data['data']['MediaListCollection']['lists'] or []
            entries: list[dict[str, Any]] = []
            for group in lists:
                entries.extend(group.get('entries') or [])
            return entries
        return []

    async def schedule(self, **variables: Any) -> list[dict[str, Any]]:
        data = await self._request(query=self._schedule_query, **variables)
        if data:
            return data['data']['Page']['airingSchedules']
        return []

    # --- Mutations (require auth headers) ---

    async def save_media_list_entry(
        self, *, media_id: int, status: str | None = None,
        progress: int | None = None, score: float | None = None,
        headers: dict[str, str],
    ) -> dict[str, Any] | None:
        variables: dict[str, Any] = {'mediaId': media_id}
        if status is not None:
            variables['status'] = status
        if progress is not None:
            variables['progress'] = progress
        if score is not None:
            variables['score'] = score
        data = await self._request(query=self._save_entry_mutation, headers=headers, **variables)
        if data:
            return data['data']['SaveMediaListEntry']
        return None

    async def delete_media_list_entry(self, *, entry_id: int, headers: dict[str, str]) -> bool:
        data = await self._request(query=self._delete_entry_mutation, headers=headers, id=entry_id)
        if data:
            return data['data']['DeleteMediaListEntry']['deleted']
        return False

    async def toggle_favourite(
        self, *, media_id: int, media_type: str, headers: dict[str, str],
    ) -> bool:
        """Toggle a media entry as favourite. Returns True if the mutation succeeded."""
        variables = {'animeId': media_id} if media_type == 'ANIME' else {'mangaId': media_id}
        data = await self._request(query=self._toggle_favourite_mutation, headers=headers, **variables)
        return data is not None

    @property
    def _media_query(self) -> str:
        return '''
        query ($page: Int, $perPage: Int, $id: Int, $season: MediaSeason, $seasonYear: Int, $type: MediaType, $isAdult: Boolean, $countryOfOrigin: CountryCode, $search: String, $genres: [String], $tags: [String], $sort: [MediaSort]) {
          Page(page: $page, perPage: $perPage) {
            media(id: $id, season: $season, seasonYear: $seasonYear, type: $type, isAdult: $isAdult, countryOfOrigin: $countryOfOrigin, search: $search, genre_in: $genres, tag_in: $tags, sort: $sort) {
              id
              idMal
              title {
                romaji
                english
              }
              type
              format
              status(version: 2)
              description
              startDate {
                year
                month
                day
              }
              endDate {
                year
                month
                day
              }
              episodes
              duration
              chapters
              volumes
              source(version: 3)
              trailer {
                id
                site
              }
              coverImage {
                large
                color
              }
              bannerImage
              genres
              hashtag
              meanScore
              popularity
              favourites
              studios(isMain: true) {
                nodes {
                  name
                  siteUrl
                }
              }
              isAdult
              nextAiringEpisode {
                airingAt
                episode
              }
              externalLinks {
                url
                site
              }
              siteUrl
            }
          }
        }
        '''

    @property
    def _character_query(self) -> str:
        return '''
        query ($page: Int, $perPage: Int, $search: String) {
          Page(page: $page, perPage: $perPage) {
            characters(search: $search) {
              name {
                full
                native
                alternative
                alternativeSpoiler
              }
              image {
                large
              }
              description
              gender
              dateOfBirth {
                year
                month
                day
              }
              age
              siteUrl
              media(sort: POPULARITY_DESC, page: 1, perPage: 5) {
                nodes {
                  title {
                    romaji
                  }
                  isAdult
                  siteUrl
                }
              }
            }
          }
        }
        '''

    @property
    def _staff_query(self) -> str:
        return '''
        query ($page: Int, $perPage: Int, $search: String) {
          Page(page: $page, perPage: $perPage) {
            staff(search: $search) {
              name {
                full
                native
                alternative
              }
              languageV2
              image {
                large
              }
              description
              primaryOccupations
              gender
              dateOfBirth {
                year
                month
                day
              }
              age
              homeTown
              siteUrl
              staffMedia(sort: POPULARITY_DESC, page: 1, perPage: 5) {
                nodes {
                  title {
                    romaji
                  }
                  isAdult
                  siteUrl
                }
              }
              characters(sort: FAVOURITES_DESC, page: 1, perPage: 5) {
                nodes {
                  name {
                    full
                  }
                  siteUrl
                }
              }
            }
          }
        }
        '''

    @property
    def _studio_query(self) -> str:
        return '''
        query ($page: Int, $perPage: Int, $search: String) {
          Page(page: $page, perPage: $perPage) {
            studios(search: $search) {
              name
              isAnimationStudio
              media(sort: POPULARITY_DESC, isMain: true, page: 1, perPage: 10) {
                nodes {
                  title {
                    romaji
                  }
                  format
                  episodes
                  coverImage {
                    large
                  }
                  isAdult
                  siteUrl
                }
              }
              siteUrl
            }
          }
        }
        '''

    @property
    def _user_query(self) -> str:
        return '''
        query {
            Viewer {
                name
                id
                avatar {
                    large
                    medium
                }
                bannerImage
                siteUrl
                about(asHtml: false)
                statistics {
                    anime {
                        count
                        minutesWatched
                        episodesWatched
                        meanScore
                    }
                    manga {
                        count
                        volumesRead
                        chaptersRead
                        meanScore
                    }
                }
            }
        }
    '''

    @property
    def _user_favourites_query(self) -> str:
        return '''
        query {
            Viewer {
                name
                id
                favourites {
                    anime(page: 1, perPage: 10) {
                        nodes {
                            id
                            title { romaji english }
                            format
                            meanScore
                            coverImage { large color }
                            siteUrl
                        }
                    }
                    manga(page: 1, perPage: 10) {
                        nodes {
                            id
                            title { romaji english }
                            format
                            meanScore
                            coverImage { large color }
                            siteUrl
                        }
                    }
                    characters(page: 1, perPage: 10) {
                        nodes {
                            name { full native }
                            image { large }
                            siteUrl
                        }
                    }
                }
            }
        }
    '''

    @property
    def _media_list_query(self) -> str:
        return '''
        query ($userId: Int, $type: MediaType, $status: MediaListStatus) {
            MediaListCollection(userId: $userId, type: $type, status: $status) {
                lists {
                    name
                    entries {
                        id
                        mediaId
                        status
                        progress
                        score(format: POINT_10)
                        media {
                            id
                            title { romaji english }
                            type
                            format
                            status(version: 2)
                            episodes
                            chapters
                            volumes
                            coverImage { large color }
                            siteUrl
                        }
                    }
                }
            }
        }
    '''

    @property
    def _save_entry_mutation(self) -> str:
        return '''
        mutation ($mediaId: Int, $status: MediaListStatus, $progress: Int, $score: Float) {
            SaveMediaListEntry(mediaId: $mediaId, status: $status, progress: $progress, score: $score) {
                id
                mediaId
                status
                progress
                score(format: POINT_10)
                media {
                    title { romaji }
                    episodes
                    chapters
                }
            }
        }
    '''

    @property
    def _delete_entry_mutation(self) -> str:
        return '''
        mutation ($id: Int) {
            DeleteMediaListEntry(id: $id) {
                deleted
            }
        }
    '''

    @property
    def _toggle_favourite_mutation(self) -> str:
        return '''
        mutation ($animeId: Int, $mangaId: Int) {
            ToggleFavourite(animeId: $animeId, mangaId: $mangaId) {
                anime { nodes { id } }
                manga { nodes { id } }
            }
        }
    '''

    @property
    def _schedule_query(self) -> str:
        return '''
        query ($page: Int, $perPage: Int, $notYetAired: Boolean, $sort: [AiringSort]) {
            Page(page: $page, perPage: $perPage) {
                airingSchedules(notYetAired: $notYetAired, sort: $sort) {
                id
                timeUntilAiring
                episode
                media {
                    id
                    idMal
                    title {
                        romaji
                        english
                    }
                    episodes
                    coverImage {
                    large
                    }
                    siteUrl
                    isAdult
                    countryOfOrigin
                }
            }
        }
    }
        '''


GENRES = [
    'Action',
    'Adventure',
    'Comedy',
    'Drama',
    'Ecchi',
    'Fantasy',
    'Horror',
    'Mahou Shoujo',
    'Mecha',
    'Music',
    'Mystery',
    'Psychological',
    'Romance',
    'Sci-Fi',
    'Slice of Life',
    'Sports',
    'Supernatural',
    'Thriller',
]

TAGS = [
    '4-koma',
    'Achromatic',
    'Achronological Order',
    'Acting',
    'Adoption',
    'Advertisement',
    'Afterlife',
    'Age Gap',
    'Age Regression',
    'Agender',
    'Agriculture',
    'Airsoft',
    'Aliens',
    'Alternate Universe',
    'American Football',
    'Amnesia',
    'Anachronism',
    'Angels',
    'Animals',
    'Anthology',
    'Anti-Hero',
    'Archery',
    'Artificial Intelligence',
    'Asexual',
    'Assassins',
    'Astronomy',
    'Athletics',
    'Augmented Reality',
    'Autobiographical',
    'Aviation',
    'Badminton',
    'Band',
    'Bar',
    'Baseball',
    'Basketball',
    'Battle Royale',
    'Biographical',
    'Bisexual',
    'Body Horror',
    'Body Swapping',
    'Boxing',
    "Boys' Love",
    'Bullying',
    'Butler',
    'Calligraphy',
    'Cannibalism',
    'Card Battle',
    'Cars',
    'Centaur',
    'CGI',
    'Cheerleading',
    'Chibi',
    'Chimera',
    'Chuunibyou',
    'Circus',
    'Classic Literature',
    'Clone',
    'College',
    'Coming of Age',
    'Conspiracy',
    'Cosmic Horror',
    'Cosplay',
    'Crime',
    'Crossdressing',
    'Crossover',
    'Cult',
    'Cultivation',
    'Cute Boys Doing Cute Things',
    'Cute Girls Doing Cute Things',
    'Cyberpunk',
    'Cyborg',
    'Cycling',
    'Dancing',
    'Death Game',
    'Delinquents',
    'Demons',
    'Denpa',
    'Detective',
    'Dinosaurs',
    'Disability',
    'Dissociative Identities',
    'Dragons',
    'Drawing',
    'Drugs',
    'Dullahan',
    'Dungeon',
    'Dystopian',
    'E-Sports',
    'Economics',
    'Educational',
    'Elf',
    'Ensemble Cast',
    'Environmental',
    'Episodic',
    'Ero Guro',
    'Espionage',
    'Fairy Tale',
    'Family Life',
    'Fashion',
    'Female Harem',
    'Female Protagonist',
    'Fencing',
    'Firefighters',
    'Fishing',
    'Fitness',
    'Flash',
    'Food',
    'Football',
    'Foreign',
    'Fugitive',
    'Full CGI',
    'Full Color',
    'Gambling',
    'Gangs',
    'Gender Bending',
    'Ghost',
    'Go',
    'Goblin',
    'Gods',
    'Golf',
    'Gore',
    'Guns',
    'Gyaru',
    'Henshin',
    'Heterosexual',
    'Hikikomori',
    'Historical',
    'Ice Skating',
    'Idol',
    'Isekai',
    'Iyashikei',
    'Josei',
    'Judo',
    'Kaiju',
    'Karuta',
    'Kemonomimi',
    'Kids',
    'Kuudere',
    'Lacrosse',
    'Language Barrier',
    'LGBTQ+ Themes',
    'Lost Civilization',
    'Love Triangle',
    'Mafia',
    'Magic',
    'Mahjong',
    'Maids',
    'Makeup',
    'Male Harem',
    'Male Protagonist',
    'Martial Arts',
    'Medicine',
    'Memory Manipulation',
    'Mermaid',
    'Meta',
    'Military',
    'Monster Boy',
    'Monster Girl',
    'Mopeds',
    'Motorcycles',
    'Musical',
    'Mythology',
    'Necromancy',
    'Nekomimi',
    'Ninja',
    'No Dialogue',
    'Noir',
    'Non-fiction',
    'Nudity',
    'Nun',
    'Office Lady',
    'Oiran',
    'Ojou-sama',
    'Otaku Culture',
    'Outdoor',
    'Pandemic',
    'Parkour',
    'Parody',
    'Philosophy',
    'Photography',
    'Pirates',
    'Poker',
    'Police',
    'Politics',
    'Post-Apocalyptic',
    'POV',
    'Primarily Adult Cast',
    'Primarily Child Cast',
    'Primarily Female Cast',
    'Primarily Male Cast',
    'Primarily Teen Cast',
    'Puppetry',
    'Rakugo',
    'Real Robot',
    'Rehabilitation',
    'Reincarnation',
    'Religion',
    'Revenge',
    'Robots',
    'Rotoscoping',
    'Rugby',
    'Rural',
    'Samurai',
    'Satire',
    'School',
    'School Club',
    'Scuba Diving',
    'Seinen',
    'Shapeshifting',
    'Ships',
    'Shogi',
    'Shoujo',
    'Shounen',
    'Shrine Maiden',
    'Skateboarding',
    'Skeleton',
    'Slapstick',
    'Slavery',
    'Software Development',
    'Space',
    'Space Opera',
    'Steampunk',
    'Stop Motion',
    'Succubus',
    'Suicide',
    'Sumo',
    'Super Power',
    'Super Robot',
    'Superhero',
    'Surfing',
    'Surreal Comedy',
    'Survival',
    'Swimming',
    'Swordplay',
    'Table Tennis',
    'Tanks',
    'Tanned Skin',
    'Teacher',
    "Teens' Love",
    'Tennis',
    'Terrorism',
    'Time Manipulation',
    'Time Skip',
    'Tokusatsu',
    'Tomboy',
    'Torture',
    'Tragedy',
    'Trains',
    'Transgender',
    'Travel',
    'Triads',
    'Tsundere',
    'Twins',
    'Urban',
    'Urban Fantasy',
    'Vampire',
    'Video Games',
    'Vikings',
    'Villainess',
    'Virtual World',
    'Volleyball',
    'VTuber',
    'War',
    'Werewolf',
    'Witch',
    'Work',
    'Wrestling',
    'Writing',
    'Wuxia',
    'Yakuza',
    'Yandere',
    'Youkai',
    'Yuri',
    'Zombie',
]
