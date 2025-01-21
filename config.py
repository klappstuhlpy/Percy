from collections.abc import Collection
from os import getenv as env
from pathlib import Path
from platform import system
from types import SimpleNamespace
from typing import ClassVar, Literal, NamedTuple

from discord import AllowedMentions
from dotenv import load_dotenv

load_dotenv()


class version_info(NamedTuple):
    """Represents versioning information"""

    major: int
    minor: int
    micro: int
    release: Literal['alpha', 'beta', 'candidate', 'final'] = 'final'

    def __str__(self) -> str:
        RELEASE_MAP: dict[str, str] = {
            'alpha': 'a',
            'beta': 'b',
            'candidate': 'rc',  # Release Candidate
        }
        return f'{self.major}.{self.minor}.{self.micro}{RELEASE_MAP.get(self.release, '')}'


beta: bool = system() != 'Linux'
path: Path = Path(__file__).parent

name: str = 'Percy'
version: version_info = version_info(major=2, minor=0, micro=3, release='beta' if beta else 'alpha')
description: str = 'A multipurpose bot for Discord'
support_server: str = 'https://discord.com/eKwMtGydqh'
website: str = 'https://percy.klappstuhl.me/'  # comming soon
repo_url: str = 'https://github.com/klappstuhlpy/Percy-v2/'

owners: Collection[int] | int = 991398932397703238
test_guild_id: int = 1062074624935993424
main_guild_id: int = 1066703165669515264
default_prefix: Collection[str] | str = '?'
allowed_mentions: AllowedMentions = AllowedMentions(everyone=False, users=True, roles=False, replied_user=False)
stats_webhook: tuple[int, str] = (1085947117140463708, env('STATS_WEBHOOK_TOKEN'))

token: str = env('DISCORD_TOKEN')
beta_token: str = env('DISCORD_BETA_TOKEN')
client_secret: str = env('DISCORD_CLIENT_SECRET')

resolved_token: str = token# if not beta else beta_token

lavalink_nodes: Collection[SimpleNamespace] = [
    SimpleNamespace(uri='https://lavalink.klappstuhl.me/', password=env('LAVALINK_NODE_1_PASSWORD')),
]


class DatabaseConfig:
    """Represents the configuration for the database."""

    database: str = 'percy'
    user: str = 'percy'
    password: str = env('DATABASE_PASSWORD')
    host: str = env('DATABASE_HOST')
    port: int = 5432

    @classmethod
    def to_url(cls) -> str:
        return f'postgresql://{cls.user}:{cls.password}@{cls.host}:{cls.port}/{cls.database}'

    @classmethod
    def to_kwargs(cls) -> dict[str, str | int]:
        return {
            'database': cls.database,
            'user': cls.user,
            'password': cls.password,
            'host': cls.host,
            'port': cls.port,
        }


# API Keys

genius_key: str = env('GENIUS_TOKEN')
github_key: str = env('GITHUB_TOKEN')
dbots_key: str = env('DBOTS_TOKEN')
top_gg_key: str = env('TOPGG_TOKEN')
images_key: str = env('IMAGES_API_TOKEN')
anilist = SimpleNamespace(client_id=int(env('ANILIST_CLIENT_ID')), client_secret=str(env('ANILIST_CLIENT_SECRET')),
                          redirect_uri='https://anilist.co/api/v2/oauth/pin')
marvel = SimpleNamespace(public_key=str(env('MARVEL_API_PUBLIC_KEY')), private_key=(env('MARVEL_API_PRIVATE_KEY')))


class Emojis:
    info: ClassVar[str] = '<:discord_info:1322338333027995778>'
    success: ClassVar[str] = '<:greenTick:1322354661289754755>'
    error: ClassVar[str] = '<:redTick:1322355105231671296>'
    none: ClassVar[str] = '<:greyTick:1322355530366193766>'
    warning: ClassVar[str] = '<:warning:1322355170746568705>'
    trash: ClassVar[str] = '<:trashcan:1322338025279197209>'
    loading: ClassVar[str] = '<a:loading:1322356006054793226>'

    yes: ClassVar[str] = '<a:accepted:1322338082082783253>'
    no: ClassVar[str] = '<a:declined:1066183072984350770>'

    cross: ClassVar[str] = '<:x_:1322355178304966731>'
    circle: ClassVar[str] = '<:o_:1322355043252572180>'

    very_cool: ClassVar[str] = '<:very_cool:1322355154808213615>'
    giveaway: ClassVar[str] = '<a:giveaway:1322356384645517333>'
    level_up: ClassVar[str] = '<:oneup:1322338839909634118>'

    leave: ClassVar[str] = '<:leave:1322354707724894249>'
    join: ClassVar[str] = '<:join:1322354686745116683>'
    banhammer: ClassVar[str] = '<:banhammer:1322338348697915532>'

    empty: ClassVar[str] = '<:__:1322354521997054044>'

    class Arrows:
        right: ClassVar[str] = '<a:vega_arrow_right:1322337825198313512>'
        left: ClassVar[str] = '<a:vega_arrow_left:1322337776812949514>'

    class PollVoteBar:
        info: ClassVar[str] = '<:redinfo:1322338316435062974>'
        start: ClassVar[str] = '<:lfc:1322354731842015242>'
        middle: ClassVar[str] = '<:lf:1322354723482767360>'
        end: ClassVar[str] = '<:le:1322354701769113672>'
        corner: ClassVar[str] = '<:ld:1322354694013845595>'

        A: ClassVar[str] = '<:A_p:1322338212349345812>'
        B: ClassVar[str] = '<:B_p:1322338220330979431>'
        C: ClassVar[str] = '<:C_p:1322338236131184670>'
        D: ClassVar[str] = '<:D_p:1322338258767581264>'
        E: ClassVar[str] = '<:E_p:1322338268246708344>'
        F: ClassVar[str] = '<:F_p:1322338280246874213>'
        G: ClassVar[str] = '<:G_p:1322338290325786626>'
        H: ClassVar[str] = '<:H_p:1322338299930738719>'

    class Economy:
        cash: ClassVar[str] = '<:cash:1322338386467622922>'
        coin: ClassVar[str] = '<:pokercoin:1322338546920722575>'

    class Command:
        locked: ClassVar[str] = '<:locked:1322338444047028378>'
        more_info: ClassVar[str] = '<:pin:1322338527345639635>'
        slash: ClassVar[str] = '<:command:1322354609754210394>'
        example: ClassVar[str] = '<:script:1208429751027372103>'
        alias: ClassVar[str] = '<:equal:1322338418008657920>'

    class Status:
        online: ClassVar[str] = '<:online:1322355061698986004>'
        idle: ClassVar[str] = '<:idle:1322354671549026405>'
        dnd: ClassVar[str] = '<:dnd:1322354625541574666>'
        offline: ClassVar[str] = '<:offline:1322355052504940617>'
        # streaming: ClassVar[str] = '<:streaming:1113421788347960320>'

    class Card:
        ten_black_notopleft: ClassVar[str] = '<:10_black_notopleft:1322339479721349181>'
        ten_black_nobottom: ClassVar[str] = '<:10_black_nobottom:1322339459194159216>'
        ten_black_nobottomright: ClassVar[str] = '<:10_black_nobottomright:1322339469130469447>'
        ten_red_nobottom: ClassVar[str] = '<:10_red_nobottom:1322339491683500146>'
        ten_red_nobottomright: ClassVar[str] = '<:10_red_nobottomright:1322339502546620498>'
        ten_red_notopleft: ClassVar[str] = '<:10_red_notopleft:1322339510733901846>'
        two_black_nobottom: ClassVar[str] = '<:2_black_nobottom:1322338989528842270>'
        two_black_nobottomright: ClassVar[str] = '<:2_black_nobottomright:1322339009216643154>'
        two_black_notopleft: ClassVar[str] = '<:2_black_notopleft:1322339018888970251>'
        two_red_nobottom: ClassVar[str] = '<:2_red_nobottom:1322339027797545001>'
        two_red_nobottomright: ClassVar[str] = '<:2_red_nobottomright:1322339036047867965>'
        two_red_notopleft: ClassVar[str] = '<:2_red_notopleft:1322339043647684661>'
        three_black_nobottom: ClassVar[str] = '<:3_black_nobottom:1322339050841178254>'
        three_black_nobottomright: ClassVar[str] = '<:3_black_nobottomright:1322339058940252253>'
        three_black_notopleft: ClassVar[str] = '<:3_black_notopleft:1322339067047837796>'
        three_red_nobottom: ClassVar[str] = '<:3_red_nobottom:1322339076447404052>'
        three_red_nobottomright: ClassVar[str] = '<:3_red_nobottomright:1322339085179687004>'
        three_red_notopleft: ClassVar[str] = '<:3_red_notopleft:1322339096890445874>'
        four_black_nobottom: ClassVar[str] = '<:4_black_nobottom:1322339107762081812>'
        four_black_nobottomright: ClassVar[str] = '<:4_black_nobottomright:1322339115219554314>'
        four_black_notopleft: ClassVar[str] = '<:4_black_notopleft:1322339122249203753>'
        four_red_nobottom: ClassVar[str] = '<:4_red_nobottom:1322339129316343858>'
        four_red_nobottomright: ClassVar[str] = '<:4_red_nobottomright:1322339138917236857>'
        four_red_notopleft: ClassVar[str] = '<:4_red_notopleft:1322339145984774197>'
        five_black_nobottom: ClassVar[str] = '<:5_black_nobottom:1322339154905792534>'
        five_black_nobottomright: ClassVar[str] = '<:5_black_nobottomright:1322339162891751494>'
        five_black_notopleft: ClassVar[str] = '<:5_black_notopleft:1322339170630242416>'
        five_red_nobottom: ClassVar[str] = '<:5_red_nobottom:1322339178456944720>'
        five_red_nobottomright: ClassVar[str] = '<:5_red_nobottomright:1322339185398513791>'
        five_red_notopleft: ClassVar[str] = '<:5_red_notopleft:1322339191891427399>'
        six_black_nobottom: ClassVar[str] = '<:6_black_nobottom:1322339198677549086>'
        six_black_nobottomright: ClassVar[str] = '<:6_black_nobottomright:1322339205367468062>'
        six_black_notopleft: ClassVar[str] = '<:6_black_notopleft:1322339211927621632>'
        six_red_nobottom: ClassVar[str] = '<:6_red_nobottom:1322339229568733297>'
        six_red_nobottomright: ClassVar[str] = '<:6_red_nobottomright:1322339239114965063>'
        six_red_notopleft: ClassVar[str] = '<:6_red_notopleft:1322339246744408125>'
        seven_black_nobottom: ClassVar[str] = '<:7_black_nobottom:1322339254650802307>'
        seven_black_nobottomright: ClassVar[str] = '<:7_black_nobottomright:1322339262020194306>'
        seven_black_notopleft: ClassVar[str] = '<:7_black_notopleft:1322339269809016832>'
        seven_red_nobottom: ClassVar[str] = '<:7_red_nobottom:1322339278315061399>'
        seven_red_nobottomright: ClassVar[str] = '<:7_red_nobottomright:1322339285080346725>'
        seven_red_notopleft: ClassVar[str] = '<:7_red_notopleft:1322339295482220655>'
        eight_black_nobottom: ClassVar[str] = '<:8_black_nobottom:1322339306106392709>'
        eight_black_nobottomright: ClassVar[str] = '<:8_black_nobottomright:1322339314247401513>'
        eight_black_notopleft: ClassVar[str] = '<:8_black_notopleft:1322339322774425600>'
        eight_red_nobottom: ClassVar[str] = '<:8_red_nobottom:1322339333247733782>'
        eight_red_nobottomright: ClassVar[str] = '<:8_red_nobottomright:1322339344463433789>'
        eight_red_notopleft: ClassVar[str] = '<:8_red_notopleft:1322339353036328970>'
        nine_black_nobottom: ClassVar[str] = '<:9_black_nobottom:1322339361425064007>'
        nine_black_nobottomright: ClassVar[str] = '<:9_black_nobottomright:1322339402692821012>'
        nine_black_notopleft: ClassVar[str] = '<:9_black_notopleft:1322339412574732401>'
        nine_red_nobottom: ClassVar[str] = '<:9_red_nobottom:1322339423316213891>'
        nine_red_nobottomright: ClassVar[str] = '<:9_red_nobottomright:1322339434972053545>'
        nine_red_notopleft: ClassVar[str] = '<:9_red_notopleft:1322339446770896989>'
        ace_black_nobottom: ClassVar[str] = '<:ace_black_nobottom:1322339521093828618>'
        ace_black_nobottomright: ClassVar[str] = '<:ace_black_nobottomright:1322339530069639211>'
        ace_black_notopleft: ClassVar[str] = '<:ace_black_notopleft:1322339538466639903>'
        ace_red_nobottom: ClassVar[str] = '<:ace_red_nobottom:1322339550965796917>'
        ace_red_nobottomright: ClassVar[str] = '<:ace_red_nobottomright:1322339561019408394>'
        ace_red_notopleft: ClassVar[str] = '<:ace_red_notopleft:1322339570263658631>'
        blank_nobottomleft: ClassVar[str] = '<:blank_nobottomleft:1322339582624272436>'
        blank_notopright: ClassVar[str] = '<:blank_notopright:1322339592082555002>'
        clubs: ClassVar[str] = '<:clubs:1322339655554830356>'
        clubs_notop: ClassVar[str] = '<:clubs_notop:1322339666581524540>'
        diamonds: ClassVar[str] = '<:diamonds:1322339676308111451>'
        diamonds_notop: ClassVar[str] = '<:diamonds_notop:1322339687288803428>'
        hearts: ClassVar[str] = '<:hearts:1322339704036786217>'
        hearts_notop: ClassVar[str] = '<:hearts_notop:1322339714463825990>'
        jack_black_nobottom: ClassVar[str] = '<:jack_black_nobottom:1322339724844732547>'
        jack_black_nobottomright: ClassVar[str] = '<:jack_black_nobottomright:1322339733350912071>'
        jack_black_notopleft: ClassVar[str] = '<:jack_black_notopleft:1322339742267867266>'
        jack_red_nobottom: ClassVar[str] = '<:jack_red_nobottom:1322339750811795456>'
        jack_red_nobottomright: ClassVar[str] = '<:jack_red_nobottomright:1322339760412426254>'
        jack_red_notopleft: ClassVar[str] = '<:jack_red_notopleft:1322339768821878896>'
        king_black_nobottom: ClassVar[str] = '<:king_black_nobottom:1322339777059487804>'
        king_black_nobottomright: ClassVar[str] = '<:king_black_nobottomright:1322339785888501814>'
        king_black_notopleft: ClassVar[str] = '<:king_black_notopleft:1322339795967541368>'
        king_red_nobottom: ClassVar[str] = '<:king_red_nobottom:1322339804976775252>'
        king_red_nobottomright: ClassVar[str] = '<:king_red_nobottomright:1322339813340479559>'
        king_red_notopleft: ClassVar[str] = '<:king_red_notopleft:1322339821405864048>'
        queen_black_nobottom: ClassVar[str] = '<:queen_black_nobottom:1322339829866037288>'
        queen_black_nobottomright: ClassVar[str] = '<:queen_black_nobottomright:1322339837566521354>'
        queen_black_notopleft: ClassVar[str] = '<:queen_black_notopleft:1322339845204480011>'
        queen_red_nobottom: ClassVar[str] = '<:queen_red_nobottom:1322339854478213224>'
        queen_red_nobottomright: ClassVar[str] = '<:queen_red_nobottomright:1322339863273410611>'
        queen_red_notopleft: ClassVar[str] = '<:queen_red_notopleft:1322339872790286449>'
        spades: ClassVar[str] = '<:spades:1322339883381031033>'
        spades_notop: ClassVar[str] = '<:spades_notop:1322339894453993504>'

        cardback_top1: ClassVar[str] = '<:cardback_top1:1322339632368848928>'
        cardback_top2: ClassVar[str] = '<:cardback_top2:1322339641906434048>'
        cardback_middle: ClassVar[str] = '<:cardback_middle:1322339623275462760>'
        cardback_bottom1: ClassVar[str] = '<:cardback_bottom1:1322339601569939506>'
        cardback_bottom2: ClassVar[str] = '<:cardback_bottom2:1322339614077222925>'
