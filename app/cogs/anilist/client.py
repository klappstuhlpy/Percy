from typing import Any

import aiohttp
import discord

API_ENDPOINT = 'https://graphql.anilist.co'


class AniListClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session: aiohttp.ClientSession = session

    async def _request(self, query: str, **variables: dict[str, Any]) -> dict[str, Any] | None:
        headers = variables.pop('headers', {})
        async with self.session.post(API_ENDPOINT, json={'query': query, 'variables': variables},
                                     headers=headers) as resp:
            data = await resp.json()

            if resp.status != 200:
                if resp.status == 500:
                    return None
                raise discord.HTTPException(resp, data)
        return data

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

    async def schedule(self, **variables: Any) -> list[dict[str, Any]]:
        data = await self._request(query=self._schedule_query, **variables)
        if data:
            return data['data']['Page']['airingSchedules']
        return []

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
                name,
                id,
                avatar {
                    large,
                    medium
                },
                bannerImage,
                siteUrl,
                about(asHtml: false),
                statistics {
                    manga {
                        volumesRead,
                        chaptersRead,
                        count
                    }
                    anime {
                        minutesWatched,
                        episodesWatched,
                        count
                    }
                }
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
    'Boys\' Love',
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
    'Teens\' Love',
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
