import inspect
import re
from pathlib import Path
from typing import Callable, Dict, Any, Union, TypeVar, Coroutine

import discord
import matplotlib
from discord import app_commands
from discord.ext import commands

from cogs.utils.context import Context, GuildContext

cash_emoji = discord.PartialEmoji(name='cash', id=1195034729083326504)
coin_emoji = discord.PartialEmoji(name='pokercoin', id=1197217157096939649)

BOT_BASE_FOLDER = Path(__file__).parent.parent.parent.as_posix()

PH_GUILD_ID = 1066703165669515264
PH_BOTS_ROLE = 1066703165669515266
PH_HELP_FORUM = 1079786704862445668
PH_SOLVED_TAG = 1079787335803207701
PH_MEMBERS_ROLE = 1066703165669515267
PLAYGROUND_GUILD_ID = 1062074624935993424

PH_LOGGING_CHANNEL = 1085947081094594693

PH_VOICE_ROOM_ID = 1077008868187578469
PH_GENERAL_VOICE_ID = 1079788410220322826

DSTATUS_CHANNEL_ID = 1066703170409070666
PH_HEAD_DEV_ROLE_ID = 1101538861663911986

ObjectHook = Callable[[Dict[str, Any]], Any]

COLOUR_DICT = matplotlib.colors.CSS4_COLORS | matplotlib.colors.XKCD_COLORS

PartialCommandGroup = Union[
    commands.Group | commands.hybrid.HybridGroup | commands.hybrid.Group | app_commands.commands.Group]
PartialCommand = Union[
    commands.Command | app_commands.commands.Command | commands.hybrid.HybridCommand, commands.hybrid.Command]

Core = Union[commands.Command, commands.Group]
App = Union[app_commands.commands.Command, app_commands.commands.Group]
Hybrid = Union[
    commands.hybrid.HybridCommand, commands.hybrid.Command, commands.hybrid.Group, commands.hybrid.HybridGroup]

PossibleTarget = Union[
    discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel, discord.StageChannel, discord.GroupChannel,
    discord.ForumChannel, discord.Member, discord.Role, discord.Emoji, discord.PartialEmoji, discord.Invite,
    discord.StageInstance, discord.Webhook, discord.Message, discord.User, discord.Guild, discord.Thread,
    discord.ThreadMember, discord.Interaction
]
StarableChannel = Union[discord.TextChannel, discord.VoiceChannel, discord.Thread]
IgnoreableEntity = Union[discord.TextChannel, discord.VoiceChannel, discord.Thread, discord.User, discord.Role]

_TContext = Union[Context, GuildContext]

Coro = TypeVar('Coro', bound=Callable[..., Coroutine[Any, Any, Any]])
NonCoro = TypeVar('NonCoro', bound=Callable[..., Any])

# REGEX

INVITE_REGEX = re.compile(r'(?:https?:)?discord(?:\.gg|\.com|app\.com(/invite)?)?[A-Za-z0-9]+')

WORD_REGEX = re.compile(r'\W', re.IGNORECASE)

MENTION_REGEX = re.compile(r'<@(!?)([0-9]*)>')
URL_REGEX = re.compile(r'https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|%[0-9a-fA-F][0-9a-fA-F])+')

TOKEN_REGEX = re.compile(r'[a-zA-Z0-9_-]{23,28}\.[a-zA-Z0-9_-]{6,7}\.[a-zA-Z0-9_-]{27,}')
GITHUB_URL_REGEX = re.compile(r'https?://(?:www\.)?github\.com/[^/\s]+/[^/\s]+(?:/[^/\s]+)*/?')

EMOJI_REGEX = re.compile(r'<a?:.+?:([0-9]{15,21})>')
EMOJI_NAME_REGEX = re.compile(r'^[0-9a-zA-Z-_]{2,32}$')

CMYK_REGEX = re.compile(
    r'^\(?(?P<c>[0-9]{1,3})%?\s*,?\s*(?P<m>[0-9]{1,3})%?\s*,?\s*(?P<y>[0-9]{1,3})%?\s*,?\s*(?P<k>[0-9]{1,3})%?\)?$')
HEX_REGEX = re.compile(r'^(#|0x)(?P<hex>[a-fA-F0-9]{6})$')
RGB_REGEX = re.compile(r'^\(?(?P<red>[0-9]+),?\s*(?P<green>[0-9]+),?\s*(?P<blue>[0-9]+)\)?$')

REVISION_FILE = re.compile(r'(?P<kind>[VU])(?P<version>[0-9]+)__(?P<description>.+).sql')

FORMATTED_CODE_REGEX = re.compile(
    r"""
        (?P<delim>(?P<block>```)|``?)
        (?(block)(?:(?P<lang>[a-z]+)\n)?)
        (?:[ \t]*\n)*
        (?P<code>.*?)
        \s*
        (?P=delim)
    """,
    flags=re.DOTALL | re.IGNORECASE | re.VERBOSE
)

RAW_CODE_REGEX = re.compile(
    r"""
        ^(?:[ \t]*\n)*
        (?P<code>.*?)
        \s*$
    """,
    flags=re.DOTALL | re.VERBOSE
)

GITHUB_FULL_REGEX = re.compile(
    r"""
        https?://(?:www\.)?github\.com/
        (?P<user>[^/]+)/
        (?P<repository>[^/]+)/
        blob/
        (?P<branch>[^/]+)/
        (?P<file_path>[^/]+(?:/[^/]+)*/)?
        (?P<filename>[^/#]+\.[^/#]+)
        (?:\?[^#]+)?
    """,
    re.VERBOSE
)

GITHUB_RE = re.compile(
    r'https://github\.com/(?P<repo>[a-zA-Z0-9-]+/[\w.-]+)/blob/'
    r'(?P<path>[^#>]+)(\?[^#>]+)?(#L(?P<start_line>\d+)(([-~:]|(\.\.))L(?P<end_line>\d+))?)?'
)

GITHUB_GIST_RE = re.compile(
    r'https://gist\.github\.com/([a-zA-Z0-9-]+)/(?P<gist_id>[a-zA-Z0-9]+)/*'
    r'(?P<revision>[a-zA-Z0-9]*)/*#file-(?P<file_path>[^#>]+?)(\?[^#>]+)?'
    r'(-L(?P<start_line>\d+)([-~:]L(?P<end_line>\d+))?)'
)

PACKAGE_NAME_RE = re.compile(r'[^a-zA-Z0-9_.]')

GUILD_FEATURES = {
    'ANIMATED_BANNER': ('🖼️', 'Server can upload and use an animated banner.'),
    'ANIMATED_ICON': ('🌟', 'Server can upload an animated icon.'),
    'APPLICATION_permissions_V2': ('🔒', 'Server is using the new command permissions system.'),
    'AUTO_MODERATION': ('🛡️', 'Server has set up Auto Moderation.'),
    'BANNER': ('🖼️', 'Server can upload and use a banner.'),
    'COMMUNITY': ('👥', 'Server is a community server.'),
    'CREATOR_MONETIZABLE_PROVISIONAL': ('💰', 'Server is a creator server.'),
    'CREATOR_STORE_PAGE': ('🏪', 'Server has a store page.'),
    'DEVELOPER_SUPPORT_SERVER': ('👨‍💻', 'Server is a dev support server.'),
    'DISCOVERABLE': ('🔍', 'Server is discoverable.'),
    'FEATURABLE': ('🌟', 'Server is featurable.'),
    'INVITE_SPLASH': ('🌊', 'Server can upload an invite splash.'),
    'INVITES_DISABLED': ('🚫', 'Server has disabled invites.'),
    'MEMBER_VERIFICATION_GATE_ENABLED': ('✅', 'Server has enabled Membership Screening.'),
    'MONETIZATION_ENABLED': ('💰', 'Server has enabled monetization.'),
    'MORE_EMOJI': ('🔢', 'Server can upload more emojis.'),
    'MORE_STICKERS': ('🔖', 'Server can upload more stickers.'),
    'NEWS': ('📰', 'Server has set up news channels.'),
    'PARTNERED': ('🤝', 'Server is partnered.'),
    'PREVIEW_ENABLED': ('👀', 'Server has enabled preview.'),
    'ROLE_ICONS': ('👑', 'Server can set role icons.'),
    'ROLE_SUBSCRIPTIONS_AVAILABLE_FOR_PURCHASE': ('💎', 'Server has purchasable role subscriptions.'),
    'ROLE_SUBSCRIPTIONS_ENABLED': ('🔑', 'Server has enabled role subscriptions.'),
    'TICKETED_EVENTS_ENABLED': ('🎟️', 'Server has enabled ticketed events.'),
    'VANITY_URL': ('🌐', 'Server has a vanity URL.'),
    'VERIFIED': ('✔️', 'Server is verified.'),
    'VIP_REGIONS': ('🎤', 'Server has VIP voice regions.'),
    'WELCOME_SCREEN_ENABLED': ('🚪', 'Server has enabled the welcome screen.')
}

CARD_EMOJIS = {
    '2_black_nobottom': 1196530957767950337, '2_black_nobottomright': 1196530960372600852,
    '2_black_notopleft': 1196530961828020365, '3_black_nobottom': 1196530963467997304,
    '3_black_nobottomright': 1196530966039105718, '3_black_notopleft': 1196530967016390707,
    '3_red_nobottom': 1196530969776238743, '4_black_nobottom': 1196530971290390729,
    '4_black_nobottomright': 1196530973823750306, '4_black_notopleft': 1196530975623098459,
    '4_red_nobottom': 1196530977003012176, '5_black_nobottom': 1196530979242778684,
    '5_red_nobottom': 1196530982484983938, '6_black_nobottomright': 1196530986402451568,
    '6_red_nobottom': 1196530990127009895, '7_black_nobottomright': 1196530995080470588,
    '7_black_notopleft': 1196530998096175324, '8_black_nobottom': 1196532058453979257,
    '8_black_notopleft': 1196531003989168198, '9_black_nobottom': 1196531009500483796,
    '9_black_nobottomright': 1196531011480199188, '9_black_notopleft': 1196531014231662672,
    '9_red_nobottom': 1196531017297702942, '10_black_nobottomright': 1196531023295545495,
    '10_black_notopleft': 1196531025795358810, '10_red_nobottom': 1196531027682799657,
    'ace_black_nobottomright': 1196531031394746428, 'ace_red_nobottom': 1196531037027700806,
    'blank_nobottomleft': 1196531038336319699, 'clubs': 1196531041658220575,
    'diamonds_notop': 1196531045965770823, 'hearts': 1196531048662708244,
    'jack_black_nobottom': 1196531054769606808, 'jack_black_nobottomright': 1196531056455729192,
    'jack_black_notopleft': 1196531059341406319, 'king_black_nobottom': 1196531063149834311,
    'king_black_notopleft': 1196531068342386829, 'king_red_nobottom': 1196531070783475832,
    'queen_black_nobottomright': 1196531073400713478, 'queen_red_nobottom': 1196531077280436354,
    'spades': 1196531081118236753, '5_black_nobottomright': 1196531886550433812,
    '5_black_notopleft': 1196531887750004737, '6_black_nobottom': 1196532050203791493,
    '6_black_notopleft': 1196532053190115468, '7_black_nobottom': 1196532054620381324,
    '7_red_nobottom': 1196532056012890214, '8_red_nobottom': 1196532059980693655,
    '10_black_nobottom': 1196532061817802772, 'ace_black_nobottom': 1196532971121950731,
    'ace_black_notopleft': 1196532972984225862, 'blank_notopright': 1196532975345618995,
    'clubs_notop': 1196532978407440384, 'diamonds': 1196532979820941462, 'hearts_notop': 1196532981276344422,
    'jack_red_nobottom': 1196532982824058931, 'king_black_nobottomright': 1196532985365811380,
    'queen_black_nobottom': 1196532986993180764, 'queen_black_notopleft': 1196532990134714459,
    'spades_notop': 1196532992722600077, '2_red_nobottom': 1196893793073512489,
    '8_black_nobottomright': 1196891878931578950,
    '2_red_nobottomright': 1196893796047265842, '2_red_notopleft': 1196893798039564381,
    '3_red_nobottomright': 1196893800702939248, '3_red_notopleft': 1196893801923485766,
    '4_red_nobottomright': 1196893802913337426, '4_red_notopleft': 1196893805719335012,
    '5_red_nobottomright': 1196893806897942621, '5_red_notopleft': 1196893808936357950,
    '6_red_nobottomright': 1196893810739916850, '6_red_notopleft': 1196893813617201252,
    '7_red_nobottomright': 1196893815542403082, '7_red_notopleft': 1196893817895403671,
    '9_red_nobottomright': 1196893821238251561, '10_red_nobottomright': 1196893825671630948,
    '10_red_notopleft': 1196893830318915675, 'ace_red_nobottomright': 1196893832093122611,
    'jack_red_nobottomright': 1196893835960254555, 'jack_red_notopleft': 1196893838829162599,
    'king_red_notopleft': 1196893842952171561, 'queen_red_nobottomright': 1196893845758169213,
    '8_red_nobottomright': 1196894033272905828, '8_red_notopleft': 1196894034736726086,
    '9_red_notopleft': 1196894037744037889, 'ace_red_notopleft': 1196894040180924576,
    'king_red_nobottomright': 1196894041854443600, 'queen_red_notopleft': 1196894044576555172,
    'cardback_bottom1': 1196908527579578539, 'cardback_bottom2': 1196908529647370401,
    'cardback_middle': 1196908532470137004, 'cardback_top1': 1196908535850745896, 'cardback_top2': 1196908537863995473
}

WORKING_RESPONSES = [
    'Your overtime at the office pays off, and you earn {coins}.',
    'Cleaning up the local arcade machines rewards you with {coins}.',
    'Finding a lost dog and returning it to its owner earns you {coins}.',
    'Working as a barista, your perfectly crafted coffee delights customers, resulting in {coins} in tips.',
    'Helping out at a community garden earns you appreciation and {coins}.',
    'Completing gardening tasks for neighbors not only beautifies the area but also adds {coins} to your wallet.',
    'Repairing bicycles for the neighborhood kids results in {coins} as a token of gratitude.',
    'By offering tech support to elderly neighbors, you\'ve earned {coins}.',
    'Organizing a neighborhood cleanup effort not only makes the area pristine but also adds {coins} to your earnings.',
    'Your dog-walking services for busy professionals earn you {coins}.',
    'Participating in a local talent show and showcasing your skills earns you {coins} and applause.',
    'Helping out at the local animal shelter not only warms your heart but also adds {coins} to your pocket.',
    'By offering your services as a personal shopper, you\'ve earned {coins}.',
    'Your efforts in assisting a local business with social media management result in {coins}.',
    'Completing handyman tasks for neighbors has earned you {coins}.',
    'Working as a lifeguard for a community pool results in {coins} for your watchful eyes.',
    'Creating and selling artwork online brings in {coins} from art enthusiasts.',
    'Assisting someone in moving their furniture proves physically demanding but rewarding with {coins}.',
    'Participating in a local singing competition not only showcases your talent but also earns you {coins}.',
    'Helping organize a local charity run earns you gratitude and {coins}.',
    'Providing gardening services to local businesses results in {coins}.',
    'Completing a freelance writing assignment on sustainable living earns you {coins}.',
    'By offering your skills as a private tutor, you\'ve earned {coins}.',
    'Participating in a focus group on consumer products has added {coins} to your account.',
    'Helping a neighbor set up their new smart home devices has earned you {coins}.',
    'By organizing and hosting a neighborhood movie night, you\'ve earned {coins}.',
    'Completing odd jobs for neighbors, such as fixing leaky faucets and painting fences, has earned you {coins}.',
    'Offering fitness coaching services to local residents has earned you {coins}.',
    'Participating in a community book club not only expands your literary horizons but also earns you {coins}.',
    'Helping organize a local food drive for the less fortunate has earned you {coins}.',
    'By offering your photography skills for local events, you\'ve earned {coins}.',
    'Your efforts in finding lost items for neighbors have been rewarded with {coins}.',
    'Assisting with event planning for local celebrations has earned you {coins}.',
    'Participating in a neighborhood chess tournament not only hones your skills but also earns you {coins}.',
    'By offering your skills as a language tutor, you\'ve earned {coins}.',
    'Helping set up and decorate for local celebrations has earned you {coins}.',
    'Completing a freelance graphic design project for a local business has earned you {coins}.',
    'Participating in a local cooking competition not only showcases your culinary talents but also earns you {coins}.',
    'Your efforts in helping a neighbor with computer issues have earned you {coins}.',
    'By offering your skills as a music instructor, you\'ve earned {coins}.',
    'Organizing a local gaming night has not only brought joy to gamers but also earned you {coins}.',
    'Helping out at a local farm has not only connected you with nature but also earned you {coins}.',
    'Completing a freelance programming project for a local startup has earned you {coins}.',
    'By offering your skills as a dance instructor, you\'ve earned {coins}.',
    'Participating in a community theater production not only entertains but also earns you {coins}.',
    'Your efforts in helping a local business with social media marketing have earned you {coins}.',
    'Assisting a neighbor with home repairs has earned you {coins}.',
    'By offering your skills as a yoga instructor, you\'ve earned {coins}.',
    'Participating in a neighborhood photography contest not only showcases your talent but also earns you {coins}.',
    'Your efforts in helping a local community center with event planning have earned you {coins}.',
    'Assisting a neighbor with setting up a home office has earned you {coins}.',
    'By offering your skills as a personal trainer, you\'ve earned {coins}.',
    'Participating in a local art exhibition not only showcases your creativity but also earns you {coins}.',
    'Helping a neighbor organize a garage sale has not only decluttered spaces but also earned you {coins}.',
    'Completing freelance writing assignments for local businesses has earned you {coins}.',
    'By offering your skills as a coding tutor, you\'ve earned {coins}.',
    'Participating in a neighborhood astronomy night not only stargazes but also earns you {coins}.',
    'Your efforts in helping a local school with educational workshops have earned you {coins}.',
    'Assisting a neighbor with gardening has not only cultivated plants but also earned you {coins}.',
    'Completing a freelance web development project for a local client has earned you {coins}.',
    'By offering your skills as a language translator, you\'ve earned {coins}.',
    'Participating in a community fitness challenge not only promotes health but also earns you {coins}.',
    'Your efforts in helping a local animal rescue with social media promotion have earned you {coins}.',
    'Assisting a neighbor with setting up a home gym has earned you {coins}.',
    'By offering your skills as a cooking instructor, you\'ve earned {coins}.',
    'Participating in a local bird-watching event not only appreciates nature but also earns you {coins}.',
    'Helping a neighbor organize a DIY workshop has not only fostered creativity but also earned you {coins}.',
    'Completing freelance graphic design projects for local musicians has earned you {coins}.',
    'By offering your skills as a math tutor, you\'ve earned {coins}.',
    'Participating in a neighborhood gardening club not only cultivates plants but also earns you {coins}.',
    'Your efforts in helping a local community center with tech workshops have earned you {coins}.',
    'Assisting a neighbor with setting up a home art studio has earned you {coins}.',
    'Completing a freelance marketing project for a local business has earned you {coins}.',
    'By offering your skills as a dance choreographer, you\'ve earned {coins}.',
    'Participating in a local poetry slam not only expresses creativity but also earns you {coins}.',
    'Your efforts in helping a local library with reading programs have earned you {coins}.',
    'Assisting a neighbor with setting up a home music studio has earned you {coins}.',
    'Completing freelance writing assignments for local magazines has earned you {coins}.',
    'By offering your skills as a coding consultant, you\'ve earned {coins}.',
    'Participating in a neighborhood hiking group not only promotes fitness but also earns you {coins}.',
    'Your efforts in helping a local youth center with after-school programs have earned you {coins}.',
    'Assisting a neighbor with setting up a home workshop has earned you {coins}.',
    'Completing a freelance illustration project for a local author has earned you {coins}.',
    'By offering your skills as a language interpreter, you\'ve earned {coins}.',
    'Participating in a community environmental cleanup not only helps the planet but also earns you {coins}.',
    'Your efforts in helping a local tech club with coding workshops have earned you {coins}.',
    'Assisting a neighbor with setting up a home science lab has earned you {coins}.',
    'Completing freelance video editing projects for local content creators has earned you {coins}.',
    'By offering your skills as a math problem solver, you\'ve earned {coins}.',
    'Participating in a neighborhood astronomy club not only explores the cosmos but also earns you {coins}.',
    'Your efforts in helping a local community center with art programs have earned you {coins}.',
    'Assisting a neighbor with setting up a home podcast studio has earned you {coins}.',
    'Completing freelance social media management for local businesses has earned you {coins}.',
    'By offering your skills as a dance fitness instructor, you\'ve earned {coins}.',
    'Participating in a local film festival not only celebrates cinema but also earns you {coins}.',
    'Your efforts in helping a local animal shelter with adoption events have earned you {coins}.',
    'Assisting a neighbor with setting up a home theater has earned you {coins}.',
    'Completing a freelance content writing project for a local startup has earned you {coins}.',
    'By offering your skills as a coding mentor, you\'ve earned {coins}.',
    'Participating in a community sports league not only fosters teamwork but also earns you {coins}.',
    'Your efforts in helping a local literacy program with reading sessions have earned you {coins}.',
    'Assisting a neighbor with setting up a home photography studio has earned you {coins}.',
    'Completing freelance web development projects for local nonprofits has earned you {coins}.',
    'By offering your skills as a language tutor, you\'ve earned {coins}.',
    'Participating in a neighborhood fitness challenge not only promotes health but also earns you {coins}.',
    'Your efforts in helping a local community garden with planting have earned you {coins}.',
    'Assisting a neighbor with setting up a home office has earned you {coins}.',
    'Completing a freelance marketing project for a local business has earned you {coins}.',
    'By offering your skills as a dance choreographer, you\'ve earned {coins}.',
    'Participating in a local poetry slam not only expresses creativity but also earns you {coins}.',
    'Your efforts in helping a local library with reading programs have earned you {coins}.',
    'Assisting a neighbor with setting up a home music studio has earned you {coins}.',
    'Completing freelance writing assignments for local magazines has earned you {coins}.',
    'By offering your skills as a coding consultant, you\'ve earned {coins}.',
    'Participating in a neighborhood hiking group not only promotes fitness but also earns you {coins}.',
    'Your efforts in helping a local youth center with after-school programs have earned you {coins}.',
    'Assisting a neighbor with setting up a home workshop has earned you {coins}.',
    'Completing a freelance illustration project for a local author has earned you {coins}.',
    'By offering your skills as a language interpreter, you\'ve earned {coins}.',
    'Participating in a community environmental cleanup not only helps the planet but also earns you {coins}.',
    'Your efforts in helping a local tech club with coding workshops have earned you {coins}.',
    'Assisting a neighbor with setting up a home science lab has earned you {coins}.',
    'Completing freelance video editing projects for local content creators has earned you {coins}.',
    'By offering your skills as a math problem solver, you\'ve earned {coins}.',
    'Participating in a neighborhood astronomy club not only explores the cosmos but also earns you {coins}.',
    'Your efforts in helping a local community center with art programs have earned you {coins}.',
    'Assisting a neighbor with setting up a home podcast studio has earned you {coins}.',
    'Completing freelance social media management for local businesses has earned you {coins}.',
    'By offering your skills as a dance fitness instructor, you\'ve earned {coins}.',
    'Participating in a local film festival not only celebrates cinema but also earns you {coins}.',
    'Your efforts in helping a local animal shelter with adoption events have earned you {coins}.',
    'Assisting a neighbor with setting up a home theater has earned you {coins}.',
    'Completing a freelance content writing project for a local startup has earned you {coins}.',
    'By offering your skills as a coding mentor, you\'ve earned {coins}.',
    'Participating in a community sports league not only fosters teamwork but also earns you {coins}.',
    'Your efforts in helping a local literacy program with reading sessions have earned you {coins}.',
    'Assisting a neighbor with setting up a home photography studio has earned you {coins}.',
    'Completing freelance web development projects for local nonprofits has earned you {coins}.',
    'By offering your skills as a language tutor, you\'ve earned {coins}.',
    'Participating in a neighborhood fitness challenge not only promotes health but also earns you {coins}.',
    'Your efforts in helping a local community garden with planting have earned you {coins}.',
    'Assisting a neighbor with setting up a home office has earned you {coins}.',
    'Completing a freelance marketing project for a local business has earned you {coins}.',
    'By offering your skills as a dance choreographer, you\'ve earned {coins}.',
    'Participating in a local poetry slam not only expresses creativity but also earns you {coins}.',
    'Your efforts in helping a local library with reading programs have earned you {coins}.',
    'Assisting a neighbor with setting up a home music studio has earned you {coins}.',
    'Completing freelance writing assignments for local magazines has earned you {coins}.',
    'By offering your skills as a coding consultant, you\'ve earned {coins}.',
    'Participating in a neighborhood hiking group not only promotes fitness but also earns you {coins}.',
    'Your efforts in helping a local youth center with after-school programs have earned you {coins}.',
    'Assisting a neighbor with setting up a home workshop has earned you {coins}.',
    'Completing a freelance illustration project for a local author has earned you {coins}.',
    'By offering your skills as a language interpreter, you\'ve earned {coins}.',
]

SUCCESSFULL_CRIME_RESPONSES = [
    'Successfully hacking into a corporation\'s database, you transfer {coins} into your account.',
    'Pickpocketing a bystander in a crowded area, you gain {coins}.',
    'With stealthy precision, you break into a bank vault and escape with {coins}.',
    'Embracing your inner cat burglar, you successfully steal a rare artifact worth {coins}.',
    'Masterfully orchestrating a heist, you make a clean getaway with {coins}.',
    'Discreetly shoplifting valuable items, you pocket {coins} without getting caught.',
    'Breaking into a wealthy home, you make off with valuable possessions worth {coins}.',
    'Successfully cracking a high-security safe, you find {coins} inside.',
    'Pilfering goods from a black-market dealer, you make a quick escape with {coins}.',
    'Conning a gullible mark, you walk away with {coins} in ill-gotten gains.',
    'Infiltrating an exclusive party, you lift valuable items worth {coins}.',
    'Successfully swindling a casino, you leave with {coins} in your pocket.',
    'Extorting a local business, you collect {coins} in protection money.',
    'Successfully counterfeiting currency, you exchange fake bills for {coins}.',
    'Robbing an armored car, you escape with {coins} before the authorities arrive.',
    'Kidnapping a wealthy individual, you receive a ransom of {coins}.',
    'Successfully looting an electronics store, you fence the goods for {coins}.',
    'Cracking a high-stakes poker game, you walk away with {coins} in winnings.',
    'Bribing a security guard, you gain access to a high-value target and steal {coins}.',
    'Successfully hijacking a delivery truck, you make off with {coins} worth of merchandise.',
    'Successfully infiltrating a museum, you steal priceless artifacts worth {coins}.',
    'Fraudulently obtaining credit card information, you make unauthorized purchases totaling {coins}.',
    'Successfully hacking into government records, you blackmail officials and receive {coins}.',
    'Bypassing security systems, you break into a luxury car showroom and steal a vehicle worth {coins}.',
    'Successfully conducting an insider trading scheme, you profit {coins} from the stock market.',
    'Evading security, you successfully shoplift luxury items worth {coins}.',
    'Selling stolen identities on the dark web, you earn {coins} from illicit transactions.',
    'Successfully infiltrating a high-end jewelry store, you steal gems worth {coins}.',
    'Ransacking a wealthy residence, you find valuable heirlooms and make off with {coins}.',
    'Successfully robbing a high-stakes poker game, you leave with {coins} in winnings.',
    'Breaking into an exclusive art gallery, you steal a valuable painting worth {coins}.',
    'Successfully hacking into a cryptocurrency exchange, you transfer {coins} to your account.',
    'Disguised as a janitor, you successfully infiltrate a government facility and steal classified documents worth {coins}.',
    'Successfully manipulating the outcome of a horse race, you earn {coins} from your bets.',
    'Successfully infiltrating a high-security laboratory, you steal experimental technology worth {coins}.',
    'Successfully sabotaging a competitor\'s business, you profit {coins} from their downfall.',
    'Successfully smuggling contraband across borders, you make {coins} from illicit trade.',
    'Successfully blackmailing a prominent figure, you receive a hefty sum of {coins}.',
    'Successfully tampering with a casino\'s slot machines, you leave with {coins} in winnings.',
    'Successfully organizing an elaborate heist, you make a dramatic escape with {coins}.',
    'Successfully manipulating the stock market, you profit {coins} from your strategic trades.',
    'Successfully conducting a cyber attack on a major corporation, you extort {coins} from them.',
    'Successfully infiltrating a high-profile event, you make off with {coins} worth of valuables.',
    'Successfully sabotaging a rival gang\'s operation, you seize control and earn {coins}.',
    'Successfully infiltrating a high-security auction, you steal a priceless artifact worth {coins}.',
    'Impersonating a wealthy individual, you successfully embezzle {coins} from unsuspecting investors.',
    'Successfully extorting a corrupt official, you receive a substantial payoff of {coins}.',
    'Infiltrating a celebrity\'s mansion, you make off with valuable items worth {coins}.',
    'Successfully tampering with a horse race, you profit {coins} from your strategic bets.',
    'Craftily manipulating a jury, you successfully acquit yourself of all charges, preserving your {coins}.',
    'Sneakily manipulating the art market, you sell forgeries and earn {coins}.',
    'Successfully stealing a rare gem from a high-security vault, you pocket {coins}.',
    'Bribing a key witness, you successfully avoid criminal charges and keep your {coins}.',
    'Fooling security systems, you successfully break into a high-end boutique and steal designer items worth {coins}.',
    'Successfully orchestrating a jewelry store heist, you escape with gems worth {coins}.',
    'Successfully blackmailing a powerful figure, you receive a substantial payoff of {coins}.',
    'Infiltrating a government auction, you steal classified documents and earn {coins} from selling them.',
    'Successfully hacking into a cryptocurrency exchange, you transfer {coins} to your account.',
    'Smoothly conning a casino, you leave with {coins} in your pocket.',
    'Successfully manipulating the outcome of a political election, you receive {coins} for your influence.',
    'Falsifying evidence, you successfully frame a rival gang and earn {coins} from the chaos.',
    'Successfully hijacking a luxury yacht, you make off with {coins} worth of valuables.',
    'Infiltrating a high-end fashion event, you steal designer outfits worth {coins}.',
    'Successfully extorting a rival gang, you receive a hefty payout of {coins}.',
    'Manipulating a jury, you successfully acquit yourself of all charges, preserving your {coins}.',
    'Masterfully infiltrating a high-security gala, you steal a rare painting worth {coins}.',
    'Successfully sabotaging a business competitor, you profit {coins} from their downfall.',
    'Escaping from prison, you successfully evade authorities and keep your {coins}.',
    'Fooling facial recognition systems, you successfully replace a high-profile individual, extorting {coins} in the process.',
    'Successfully orchestrating a massive data breach, you sell sensitive information for {coins}.',
    'Successfully stealing a rare artifact from a museum, you receive a reward of {coins} from a mysterious buyer.',
    'Successfully manipulating the stock market, you profit {coins} from your strategic trades.',
    'Craftily infiltrating a high-security laboratory, you steal experimental technology worth {coins}.',
    'Masterfully disguising yourself as a police officer, you successfully infiltrate a crime scene and steal evidence worth {coins}.',
    'Successfully conducting an elaborate Ponzi scheme, you walk away with {coins} in ill-gotten gains.',
    'Smoothly infiltrating a private club, you make off with {coins} worth of valuables.',
    'Successfully hacking into a government server, you blackmail officials and receive {coins}.',
    'Masterfully organizing a massive heist, you escape with {coins} in a daring getaway.',
    'Successfully manipulating the outcome of a sports event, you profit {coins} from your bets.',
    'Falsifying your own death, you successfully escape legal consequences and preserve your {coins}.',
    'Successfully infiltrating a secret society, you steal ancient artifacts worth {coins}.',
    'Smoothly manipulating a jury, you successfully acquit yourself of all charges, preserving your {coins}.',
    'Masterfully conning a high-profile mark, you walk away with {coins} in ill-gotten gains.',
    'Successfully orchestrating a massive casino heist, you leave with {coins} in a dazzling escape.',
    'Smoothly manipulating a jury, you successfully acquit yourself of all charges, preserving your {coins}.',
    'Masterfully infiltrating a government facility, you steal classified documents worth {coins}.',
    'Successfully conning a corrupt politician, you receive a substantial payoff of {coins}.',
    'Fooling security systems, you successfully break into a high-end technology company and steal prototypes worth {coins}.',
    'Successfully blackmailing a powerful figure, you receive a hefty payoff of {coins}.',
]

FAILED_CRIME_RESPONSES = [
    'Attempting to hack into a corporation\'s database, you trigger an alarm, resulting in a {coins} fine.',
    'Caught red-handed while pickpocketing, a vigilant NPC forces you to pay a fine of {coins}.',
    'Breaking into a bank vault, an alarm is triggered, and authorities catch you. You face a fine of {coins}.',
    'Your cat burglary attempt goes awry, and you\'re apprehended with the stolen artifact. Fine: {coins} awaits.',
    'Security thwarts your ambitious heist, leading to a {coins} fine for trespassing.',
    'Shoplifting goes wrong as security apprehends you, resulting in a {coins} fine.',
    'Breaking into a wealthy home, the residents catch you in the act, and you face a fine of {coins}.',
    'Attempting to crack a high-security safe, an alarm is triggered, and you\'re caught. Fine: {coins}.',
    'Pilfering goods from a black-market dealer attracts undercover officers, leading to a {coins} fine.',
    'Your con job backfires as your gullible mark turns out to be an undercover agent. Fine: {coins}.',
    'Infiltrating an exclusive party, you\'re recognized and detained by security, facing a {coins} fine.',
    'Your attempt to swindle a casino results in security detaining you, and you\'re fined {coins}.',
    'Extorting a local business attracts the attention of authorities, resulting in a {coins} fine.',
    'Your counterfeit currency is detected, and you\'re fined {coins} for your actions.',
    'Attempting to rob an armored car, you\'re caught in the act by the police, facing a {coins} fine.',
    'Kidnapping goes wrong as law enforcement intervenes, and you\'re fined {coins}.',
    'Looting an electronics store results in your arrest, and you\'re fined {coins}.',
    'Cracking a poker game results in the casino pressing charges, and you\'re fined {coins}.',
    'Attempting to bribe a security guard leads to your arrest, accompanied by a {coins} fine.',
    'Hijacking a delivery truck is foiled, and you\'re caught by the police. Fine: {coins}.',
    'Infiltrating a museum results in your arrest for stealing artifacts. You face a {coins} fine.',
    'Fraudulently obtaining credit card information leads to your arrest, and you\'re fined {coins}.',
    'Attempting to hack into government records attracts the attention of law enforcement, resulting in a {coins} fine.',
    'Bypassing security to steal a car ends with your arrest, and you\'re fined {coins}.',
    'Insider trading results in legal consequences, and you\'re fined {coins}.',
    'Shoplifting luxury items results in your arrest, and you\'re fined {coins}.',
    'Selling stolen identities leads to your arrest, accompanied by a {coins} fine.',
    'Infiltrating a high-end jewelry store results in your arrest, and you face a {coins} fine.',
    'Ransacking a wealthy residence ends with your arrest, and you\'re fined {coins}.',
    'Attempting to rob a poker game results in your arrest, and you\'re fined {coins}.',
    'Breaking into an art gallery ends with your arrest, and you face a {coins} fine.',
    'Hacking into a cryptocurrency exchange results in legal consequences, and you\'re fined {coins}.',
    'Attempting to infiltrate a government facility results in your arrest. You face a {coins} fine.',
    'Manipulating a horse race is exposed, and you face legal consequences. Fine: {coins}.',
    'Infiltrating a high-security laboratory results in your arrest, and you\'re fined {coins}.',
    'Sabotaging a competitor\'s business attracts legal consequences, and you\'re fined {coins}.',
    'Smuggling contraband across borders results in your arrest, and you\'re fined {coins}.',
    'Blackmailing a prominent figure leads to legal consequences, and you\'re fined {coins}.',
    'Tampering with casino slot machines results in your arrest. You face a {coins} fine.',
    'Your elaborate heist is foiled, and you\'re apprehended, accompanied by a {coins} fine.',
    'Manipulating the stock market leads to legal consequences, and you\'re fined {coins}.',
    'Conducting a cyber attack on a major corporation results in legal consequences, and you\'re fined {coins}.',
    'Infiltrating a high-profile event results in your arrest. You face a {coins} fine.',
    'Sabotaging a rival gang\'s operation goes wrong, and you\'re caught. Fine: {coins}.',
    'Attempting to infiltrate a high-security auction, you are caught, and your heist ends with a {coins} fine.',
    'Impersonating a wealthy individual, you\'re exposed, and investors demand restitution, resulting in a {coins} fine.',
    'Your attempt to extort a corrupt official goes wrong, and you\'re fined {coins} for your actions.',
    'Infiltrating a celebrity\'s mansion, you\'re caught by security, resulting in a {coins} fine.',
    'Tampering with a horse race backfires, and you lose {coins} in failed bets.',
    'Attempting to manipulate a jury, your plan unravels, leading to legal consequences and a {coins} fine.',
    'Manipulating the art market results in exposure, and you\'re fined {coins} for your forgeries.',
    'Breaking into a high-security vault, an alarm is triggered, and authorities catch you. You face a {coins} fine.',
    'Bribing a key witness backfires, leading to your arrest and a {coins} fine.',
    'Breaking into a high-end boutique, you\'re caught by security, resulting in a {coins} fine.',
    'Orchestrating a jewelry store heist attracts the attention of the police, resulting in a {coins} fine.',
    'Blackmailing a powerful figure goes wrong, and you\'re fined {coins} for your actions.',
    'Infiltrating a government auction results in your arrest for stealing classified documents. Fine: {coins}.',
    'Hacking into a cryptocurrency exchange results in exposure, legal consequences, and a {coins} fine.',
    'Conning a casino leads to security detaining you, and you\'re fined {coins}.',
    'Manipulating a political election results in exposure, legal consequences, and a {coins} fine.',
    'Falsifying evidence leads to your arrest, and you\'re fined {coins} for your actions.',
    'Hijacking a luxury yacht is foiled, and you\'re caught by the police. Fine: {coins}.',
    'Infiltrating a high-end fashion event results in your arrest for stealing designer outfits. Fine: {coins}.',
    'Extorting a rival gang goes wrong, and you\'re fined {coins} for your actions.',
    'Manipulating a jury backfires, and you\'re fined {coins} for your actions.',
    'Infiltrating a high-security gala results in your arrest for stealing a rare painting. Fine: {coins}.',
    'Sabotaging a business competitor attracts legal consequences, and you\'re fined {coins}.',
    'Escaping from prison results in your re-arrest, and you\'re fined {coins}.',
    'Fooling facial recognition systems goes wrong, and you\'re fined {coins} for your actions.',
    'Orchestrating a massive data breach results in exposure, legal consequences, and a {coins} fine.',
    'Stealing a rare artifact from a museum leads to your arrest, and you\'re fined {coins}.',
    'Manipulating the stock market leads to exposure, legal consequences, and a {coins} fine.',
    'Infiltrating a high-security laboratory results in your arrest for stealing experimental technology. Fine: {coins}.',
    'Disguising yourself as a police officer goes wrong, leading to your arrest and a {coins} fine.',
    'Conducting an elaborate Ponzi scheme leads to your arrest, and you\'re fined {coins}.',
    'Infiltrating a private club results in your arrest for theft. Fine: {coins}.',
    'Hacking into a government server leads to exposure, legal consequences, and a {coins} fine.',
    'Organizing a massive heist goes wrong, and you\'re caught by authorities. Fine: {coins}.',
    'Manipulating the outcome of a sports event results in exposure, legal consequences, and a {coins} fine.',
    'Falsifying your own death attracts legal consequences, and you\'re fined {coins}.',
    'Infiltrating a secret society results in exposure, legal consequences, and a {coins} fine.',
    'Manipulating a jury backfires, and you\'re fined {coins} for your actions.',
    'Conning a high-profile mark results in exposure, legal consequences, and a {coins} fine.',
    'Orchestrating a massive casino heist goes wrong, and you\'re caught by security. Fine: {coins}.',
    'Manipulating a jury backfires, and you\'re fined {coins} for your actions.',
    'Infiltrating a government facility results in your arrest for stealing classified documents. Fine: {coins}.',
    'Conning a corrupt politician goes wrong, and you\'re fined {coins} for your actions.',
    'Breaking into a high-end technology company attracts legal consequences, and you\'re fined {coins}.',
    'Blackmailing a powerful figure goes wrong, and you\'re fined {coins} for your actions.',
]

SUCCESSFULL_SLUT_RESPONSES = [
    'After a wild night at the club, you find yourself in the middle of a steamy orgy, earning {coins} and a sense of euphoria.',
    'You engage in a passionate encounter with a mysterious stranger, resulting in a night of pleasure and {coins}.',
    'During a risqué performance, you captivate a wealthy patron who rewards you generously with {coins} for a private rendezvous.',
    'You attend a lavish masquerade ball and indulge in a sinful tryst with a masked stranger, earning {coins} and a thrilling memory.',
    'After a seductive dance, you catch the eye of a powerful noble who offers you a generous payment of {coins} for a night of pleasure.',
    'You participate in a secret underground event where desires run wild, and you earn {coins} for your uninhibited participation.',
    'A high-profile client invites you to a luxury yacht party where you engage in a scandalous affair, earning {coins} and a taste of the high life.',
    'You join a group of like-minded individuals for a night of debauchery, resulting in a memorable orgy and {coins} as a reward.',
    'After downing a few shots, you find yourself in a passionate encounter with the barmaid. Somehow, you end up with {coins} and a real sore hangover.',
    'You become the center of attention at an exclusive swingers\' party, indulging in various encounters and collecting {coins} for your services.',
    'During a heated encounter with a wealthy benefactor, you leave a lasting impression and receive {coins} as a token of appreciation.',
    'In a daring act of seduction, you engage in a thrilling threesome, earning {coins} and unforgettable memories.',
    'You attend an elite BDSM club and captivate a dominatrix who rewards you with {coins} for your submission.',
    'After an intense workout session, you find yourself in a steamy encounter with your personal trainer, gaining {coins} and a new level of physical satisfaction.',
    'During a weekend getaway, you engage in a passionate affair with a stranger, earning {coins} and a secret to cherish.',
    'An influential politician becomes infatuated with you and offers a substantial sum of {coins} for a discreet affair.',
    'You join a secret society dedicated to pleasure, where you engage in a night of sinful indulgence and earn {coins} for your participation.',
    'After a provocative photo shoot, you catch the attention of a wealthy collector who commissions an intimate session, rewarding you with {coins}.',
    'In a daring act of exhibitionism, you engage in a steamy encounter in a public place, earning {coins} and an adrenaline rush.',
    'You become a muse for a talented artist who pays you handsomely with {coins} for your intimate inspiration.',
    'During a trip to a tropical paradise, you find yourself in a passionate tryst with a fellow vacationer, earning {coins} and unforgettable memories.',
    'After a sensual dance performance, you captivate a wealthy patron who offers a generous payment of {coins} for a private show.',
    'You engage in a thrilling encounter with a powerful crime lord, earning {coins} and a dangerous reputation.',
    'In a secret underground club, you participate in an extravagant orgy, earning {coins} and a reputation as a hedonistic pleasure-seeker.',
    'After a chance encounter at a masked ball, you engage in a night of uninhibited passion, earning {coins} and a sense of liberation.',
    'You become the object of desire for a group of wealthy individuals, indulging in a night of extravagant pleasure and earning {coins} as a reward.',
    'After a steamy encounter in a luxurious penthouse, a wealthy entrepreneur rewards you with a generous payment of {coins} for your services.',
    'You join a private fetish party, exploring your darkest desires and earning {coins} for your willingness to indulge.',
    'In a daring act of voyeurism, you engage in a passionate encounter while others watch, earning {coins} and a thrill like no other.',
    'You attend an exclusive sex club where you engage in a night of uninhibited pleasure, collecting {coins} as a reward for your participation.',
    'After an intense flirtation, you find yourself in a passionate encounter with a powerful CEO, earning {coins} and a newfound level of influence.',
    'After a night of heavy drinking, you find yourself in the middle of a wild orgy. You wake up with {coins} and a satisfied smile.',
    'You engage in a steamy encounter with a wealthy aristocrat, earning {coins} and a reputation as an insatiable lover.',
    'In a daring act of seduction, you entice a group of influential individuals into a night of pleasure. You leave with {coins} and a sense of power.',
    'You become the centerpiece of a hedonistic party, indulging in every desire imaginable. In the end, you walk away with {coins} and a euphoric high.',
    'After an intimate encounter with a powerful crime lord, you receive a handsome payment of {coins} and a taste of danger.',
    'You join a secret society dedicated to pleasure, engaging in an unforgettable night of ecstasy. They reward you with {coins} and a newfound sense of belonging.',
    'In a forbidden affair with a married couple, you discover the thrill of unconventional love. They shower you with {coins} as a token of their appreciation.',
    'After a passionate encounter with a mysterious stranger, you find yourself richer by {coins} and haunted by their lingering touch.',
    'You offer your services to a group of high-ranking officials, fulfilling their deepest desires. In return, they reward you generously with {coins}.',
    'Under the influence of an aphrodisiac, you engage in a wild night of pleasure with multiple partners. You wake up with {coins} and a naughty secret.',
    'During a yacht party, you catch the eye of a billionaire who offers you a night of pleasure in exchange for {coins}.',
    'In a lavish penthouse suite, you engage in a passionate threesome with a famous celebrity couple, earning {coins} and a taste of the glamorous life.',
    'After an intense workout at the gym, you join a group of fitness enthusiasts for a sweaty and exhilarating sexual escapade, receiving {coins} as a reward.',
    'In a luxurious hotel suite, you become the plaything of a wealthy socialite, receiving {coins} and lavish gifts as a token of appreciation.',
    'At an exclusive swingers club, you explore your wildest fantasies with like-minded individuals, earning {coins} and a sense of liberation.',
    'During a visit to a renowned sex club, you engage in a thrilling foursome, leaving with {coins} and unforgettable memories.',
    'In a dimly lit bar, you attract the attention of a seductive stranger who takes you to a private room for a night of passion, earning {coins} and a sense of adventure.',
    'After a provocative performance at a cabaret, you are invited backstage to join the sensual cast in an intoxicating orgy, receiving {coins} as a token of appreciation.',
    'In a secret underground society, you participate in a masked orgy, indulging in forbidden pleasures and receiving {coins} as a reward.',
    'After a night of dancing at a decadent nightclub, you find yourself in a passionate encounter with a mysterious stranger, earning {coins} and a sense of intrigue.',
    'In a hidden garden, you join a secret society dedicated to hedonism, engaging in a sensual orgy and receiving {coins} as a symbol of your membership.',
    'After a daring strip poker game, you engage in a wild group encounter with the other players, leaving with {coins} and a devilish grin.',
    'In a secluded mansion, you are initiated into a secret society of pleasure-seekers, indulging in a passionate orgy and receiving {coins} as a mark of your acceptance.',
    'After a flirtatious encounter at a masked ball, you engage in a steamy threesome with a captivating couple, earning {coins} and a sense of satisfaction.',
]

FAILED_SLUT_RESPONSES = [
    'After indulging in a steamy affair with a wealthy client, you wake up to find {coins} missing from your pocket.',
    'You engage in a wild night of passion with a stranger, only to discover {coins} mysteriously gone from your stash.',
    'After a reckless encounter with a group of strangers, you realize {coins} have been discreetly taken from your possession.',
    'In a secret underground club, you become a part of an intense orgy and later find {coins} missing from your wallet.',
    'During a wild party, you engage in a group sex session and later realize {coins} have been stolen from your purse.',
    'After a flirtatious encounter at the casino, you join a high-stakes poker game that ends in a scandalous sexcapade, resulting in {coins} being swindled from you.',
    'During a masquerade ball, you have a passionate encounter with a masked stranger, waking up to find {coins} mysteriously vanished.',
    'You become the center of attention at a decadent sex party, indulging in various acts of pleasure, but discover {coins} missing from your earnings.',
    'At an exclusive BDSM event, you become the submissive of a skilled Dom, who cunningly takes {coins} from your pocket as punishment.',
    'In a secluded cabin, you have a passionate tryst with a rugged woodsman, only to realize {coins} have been pilfered from your belongings.',
    'During a weekend getaway, you become the object of desire for an entire group, resulting in a euphoric orgy, but leaving you with {coins} missing as a result.',
    'After a provocative performance at a burlesque club, you engage in a backstage rendezvous, but discover {coins} discreetly stolen from your purse.',
    'Under the moonlight, you join a secret society dedicated to pleasure, indulging in an unforgettable orgy, but finding {coins} missing from your stash.',
    'After a steamy encounter with a powerful politician, you receive a generous payment of {coins}, only to have it forcibly taken back.',
    'In a hidden dungeon, you explore the world of BDSM with a skilled Master, who cunningly swindles {coins} from your possession.',
    'During a yacht party, you catch the eye of a billionaire who offers you a night of pleasure in exchange for {coins}, but ends up taking more than agreed upon.',
    'In a lavish penthouse suite, you have a passionate threesome with a famous celebrity couple, but discover {coins} missing from your wallet.',
    'After an intense workout at the gym, you join a group of fitness enthusiasts for a sweaty and exhilarating sexual escapade, but find {coins} mysteriously gone.',
    'In a luxurious hotel suite, you become the plaything of a wealthy socialite, who cunningly takes {coins} from your purse as a gesture of dominance.',
    'At an exclusive swingers club, you explore your wildest fantasies with like-minded individuals, but end up losing {coins} in the process.',
    'During a visit to a renowned sex club, you engage in a thrilling foursome, but realize {coins} have been stolen from your clothing.',
    'In a dimly lit bar, you attract the attention of a seductive stranger who takes you to a private room for a night of pleasure, leaving you with {coins} missing from your pocket.',
    'After a provocative performance at a cabaret, you are invited backstage to join the sensual cast in an intoxicating orgy, but find {coins} discreetly taken from your belongings.',
    'After a wild night of passion with a client, you wake up to find {coins} missing from your purse.',
    'You engage in a risky encounter with a stranger, only to discover that {coins} have been stolen from your wallet.',
    'During a steamy threesome, your partner steals {coins} from your nightstand when you\'re not looking.',
    'In a moment of weakness, you engage in a forbidden affair, resulting in the theft of {coins} from your pocket.',
    'After a seductive encounter, you realize that {coins} have mysteriously disappeared from your bag.',
    'During an intense BDSM session, your partner swindles {coins} from your wallet while you\'re restrained.',
    'In a moment of desperation, you engage in a risky transaction, only to be robbed of {coins} by your unscrupulous client.',
    'After a wild night of partying, you wake up to find that {coins} have been pickpocketed from your purse.',
    'You naively trust a stranger who promises pleasure in exchange for {coins}, only to be left empty-handed and deceived.',
    'In a moment of weakness, you participate in a group encounter, only to realize that {coins} have been slyly stolen from your possession.',
    'After a passionate rendezvous, you discover that {coins} have been discreetly swiped from your wallet.',
    'During an intimate encounter with a wealthy patron, you find {coins} missing from your purse the next morning.',
    'You engage in a forbidden tryst with a married individual, resulting in the loss of {coins} from your wallet.',
    'After a night of indulgence, you wake up to find {coins} mysteriously gone from your bag.',
    'In a moment of vulnerability, you fall for a scam and lose {coins} to a cunning imposter.',
    'You engage in a steamy affair with a dishonest client who steals {coins} from your pocket while you\'re intimate.',
    'After a wild party, you realize that {coins} have been stolen from your wallet amidst the chaos.',
    'In a moment of desperation, you engage in a risky encounter, only to be robbed of {coins} by a deceptive partner.',
    'After a passionate encounter, you wake up to find that {coins} have been cunningly taken from your purse.',
    'You succumb to the charms of a seductive stranger, only to discover the theft of {coins} from your wallet.',
    'After an exhilarating night of pleasure, you find {coins} missing from your bag, leaving you feeling betrayed.',
    'In a moment of weakness, you engage in a dangerous liaison, resulting in the loss of {coins} from your pocket.',
    'You participate in a high-risk transaction, only to discover that {coins} have been swindled from your possession.',
    'After an intense BDSM session, you realize that {coins} have been silently taken from your wallet.',
    'In a moment of desperation, you engage in a risky encounter, only to be duped out of {coins} by a cunning client.',
    'After a night of debauchery, you wake up to find {coins} mysteriously missing from your purse.',
    'You engage in a forbidden affair, only to discover that {coins} have been cunningly stolen from your wallet.',
    'After a passionate encounter, you realize that {coins} have been discreetly pilfered from your bag.',
    'In a moment of vulnerability, you fall for a scam and lose {coins} to a manipulative imposter.',
    'You engage in a steamy encounter with a dishonest client who swipes {coins} from your pocket while you\'re intimate.',
    'After a steamy encounter with a client, you realize that {coins} have gone missing from your purse.',
    'You engage in a wild night of passion with a stranger, only to wake up and find that {coins} have been stolen from your wallet.',
    'In a moment of lust, you have a risky encounter with a wealthy patron, resulting in the loss of {coins} from your stash.',
    'After a seductive rendezvous, you discover that {coins} have mysteriously disappeared from your account.',
    'You succumb to temptation and engage in a forbidden tryst, only to realize that {coins} have been swindled from your possession.',
    'In a moment of weakness, you have an affair with a married individual, leading to the theft of {coins} from your earnings.',
    'After a night of pleasure, you wake up to find that {coins} have been cunningly taken from your pockets.',
    'You participate in a raunchy escapade, unaware that {coins} have vanished from your secret stash.',
    'In a moment of desire, you engage in a risky encounter that results in the loss of {coins} from your wallet.',
    'After a steamy encounter with a client, you discover that {coins} have been deceitfully pilfered from your savings.',
    'You surrender to temptation and engage in a forbidden affair, only to realize that {coins} have been cunningly swiped from your possession.',
    'In a passionate encounter, you are seduced by a cunning individual who steals {coins} from your earnings.',
    'After a night of wild pleasure, you wake up to find that {coins} have been mysteriously snatched from your pockets.',
    'You participate in a steamy tryst, unaware that {coins} have vanished from your carefully hidden stash.',
    'In a moment of weakness, you have an affair with a manipulative person, leading to the theft of {coins} from your hard-earned savings.',
    'After a night of intense pleasure, you wake up to find that {coins} have been slyly taken from your wallet.',
    'You engage in a seductive escapade, only to discover that {coins} have been artfully pilfered from your secret stash.',
    'In a moment of desire, you give in to a risky encounter that results in the loss of {coins} from your earnings.',
    'After a steamy encounter with a client, you realize that {coins} have been deceitfully swindled from your savings.',
    'You succumb to temptation and engage in a forbidden tryst, only to find that {coins} have been cunningly stolen from your possession.',
    'In a moment of weakness, you have an affair with a married individual, resulting in the theft of {coins} from your wallet.',
    'After a night of pleasure, you wake up to find that {coins} have been cunningly taken from your pockets.',
    'You participate in a raunchy escapade, unaware that {coins} have vanished from your secret stash.',
    'In a moment of desire, you engage in a risky encounter that leads to the loss of {coins} from your earnings.',
    'After a steamy encounter with a client, you discover that {coins} have been deceitfully pilfered from your savings.',
    'You surrender to temptation and engage in a forbidden affair, only to realize that {coins} have been cunningly swiped from your possession.',
    'In a passionate encounter, you are seduced by a cunning individual who steals {coins} from your earnings.',
    'After a night of wild pleasure, you wake up to find that {coins} have been mysteriously snatched from your pockets.',
]

PERMISSIONS = [
    {'origin': 'connect', 'name': 'Connect', 'value': 0x100000},
    {'origin': 'mute_members', 'name': 'Mute Members', 'value': 0x400000},
    {'origin': 'move_members', 'name': 'Move Members', 'value': 0x1000000},
    {'origin': 'speak', 'name': 'Speak', 'value': 0x200000},
    {'origin': 'deafen_members', 'name': 'Deafen Members', 'value': 0x800000},
    {'origin': 'use_voice_activity', 'name': 'Use Voice Activity', 'value': 0x2000000},
    {'origin': 'go_live', 'name': 'Go Live', 'value': 0x200},
    {'origin': 'priority_speaker', 'name': 'Priority Speaker', 'value': 0x100},
    {'origin': 'request_to_speak', 'name': 'Request to Speak', 'value': 0x100000000},
    {'origin': 'administrator', 'name': 'Administrator', 'value': 0x8},
    {'origin': 'manage_roles', 'name': 'Manage Roles', 'value': 0x10000000},
    {'origin': 'kick_members', 'name': 'Kick Members', 'value': 0x2},
    {'origin': 'instant_invite', 'name': 'Create Instant Invite', 'value': 0x1},
    {'origin': 'manage_nicknames', 'name': 'Manage Nicknames', 'value': 0x8000000},
    {'origin': 'manage_server', 'name': 'Manage Server', 'value': 0x20},
    {'origin': 'manage_channels', 'name': 'Manage Channels', 'value': 0x10},
    {'origin': 'ban_members', 'name': 'Ban Members', 'value': 0x4},
    {'origin': 'change_nickname', 'name': 'Change Nickname', 'value': 0x4000000},
    {'origin': 'manage_webhooks', 'name': 'Manage Webhooks', 'value': 0x20000000},
    {'origin': 'manage_emojis', 'name': 'Manage Emojis', 'value': 0x40000000},
    {'origin': 'view_audit_log', 'name': 'View Audit Log', 'value': 0x80},
    {'origin': 'view_guild_insights', 'name': 'View Server Insights', 'value': 0x80000},
    {'origin': 'view_channel', 'name': 'View Channel', 'value': 0x400},
    {'origin': 'send_tts_messages', 'name': 'Send TTS Messages', 'value': 0x1000},
    {'origin': 'embed_links', 'name': 'Embed Links', 'value': 0x4000},
    {'origin': 'read_message_history', 'name': 'Read Message History', 'value': 0x10000},
    {'origin': 'use_external_emojis', 'name': 'Use External Emojis', 'value': 0x40000},
    {'origin': 'send_messages', 'name': 'Send Messages', 'value': 0x800},
    {'origin': 'manage_messaes', 'name': 'Manage Messages', 'value': 0x2000},
    {'origin': 'attach_files', 'name': 'Attach Files', 'value': 0x8000},
    {'origin': 'mention_everyone', 'name': 'Mention Everyone', 'value': 0x20000},
    {'origin': 'add_reactions', 'name': 'Add Reactions', 'value': 0x40},
    {'origin': 'use_slash_commands', 'name': 'Use Slash Commands', 'value': 0x80000000}
]

BADGE_DICT = {
    discord.UserFlags.bug_hunter: '<:lvl1:1072925290520653884> Bug Hunter',
    discord.UserFlags.bug_hunter_level_2: '<:lvl2:1072925293351800934> Bug Hunter Level 2',
    discord.UserFlags.early_supporter: '<:earlysupporter:1072925288243146877> Early Supporter',
    discord.UserFlags.verified_bot_developer: '<:earlydev:1072925287123259423> Verified Bot Developer',
    discord.UserFlags.active_developer: '<:activedev:1070318990406189057> Active Developer',
    discord.UserFlags.partner: '<:partner:1072925295688044615> Discord Partner',
    discord.UserFlags.staff: '<:staff:1072925297365766205> Discord Staff',
    discord.UserFlags.hypesquad_balance: '<:balance:1079447402311856178> HypeSquad Balance',
    discord.UserFlags.hypesquad_bravery: '<:bravery:1079447443667689502> HypeSquad Bravery',
    discord.UserFlags.hypesquad_brilliance: '<:brilliance:1079447480569180331> HypeSquad Brilliance'
}

LANGUAGES = {
    'af': 'Afrikaans',
    'sq': 'Albanian',
    'am': 'Amharic',
    'ar': 'Arabic',
    'hy': 'Armenian',
    'az': 'Azerbaijani',
    'eu': 'Basque',
    'be': 'Belarusian',
    'bn': 'Bengali',
    'bs': 'Bosnian',
    'bg': 'Bulgarian',
    'ca': 'Catalan',
    'ceb': 'Cebuano',
    'ny': 'Chichewa',
    'zh-cn': 'Chinese (Simplified)',
    'zh-tw': 'Chinese (Traditional)',
    'co': 'Corsican',
    'hr': 'Croatian',
    'cs': 'Czech',
    'da': 'Danish',
    'nl': 'Dutch',
    'en': 'English',
    'eo': 'Esperanto',
    'et': 'Estonian',
    'tl': 'Filipino',
    'fi': 'Finnish',
    'fr': 'French',
    'fy': 'Frisian',
    'gl': 'Galician',
    'ka': 'Georgian',
    'de': 'German',
    'el': 'Greek',
    'gu': 'Gujarati',
    'ht': 'Haitian Creole',
    'ha': 'Hausa',
    'haw': 'Hawaiian',
    'iw': 'Hebrew',
    'he': 'Hebrew',
    'hi': 'Hindi',
    'hmn': 'Hmong',
    'hu': 'Hungarian',
    'is': 'Icelandic',
    'ig': 'Igbo',
    'id': 'Indonesian',
    'ga': 'Irish',
    'it': 'Italian',
    'ja': 'Japanese',
    'jw': 'Javanese',
    'kn': 'Kannada',
    'kk': 'Kazakh',
    'km': 'Khmer',
    'ko': 'Korean',
    'ku': 'Kurdish (Kurmanji)',
    'ky': 'Kyrgyz',
    'lo': 'Lao',
    'la': 'Latin',
    'lv': 'Latvian',
    'lt': 'Lithuanian',
    'lb': 'Luxembourgish',
    'mk': 'Macedonian',
    'mg': 'Malagasy',
    'ms': 'Malay',
    'ml': 'Malayalam',
    'mt': 'Maltese',
    'mi': 'Maori',
    'mr': 'Marathi',
    'mn': 'Mongolian',
    'my': 'Myanmar (Burmese)',
    'ne': 'Nepali',
    'no': 'Norwegian',
    'or': 'Odia',
    'ps': 'Pashto',
    'fa': 'Persian',
    'pl': 'Polish',
    'pt': 'Portuguese',
    'pa': 'Punjabi',
    'ro': 'Romanian',
    'ru': 'Russian',
    'sm': 'Samoan',
    'gd': 'Scots Gaelic',
    'sr': 'Serbian',
    'st': 'Sesotho',
    'sn': 'Shona',
    'sd': 'Sindhi',
    'si': 'Sinhala',
    'sk': 'Slovak',
    'sl': 'Slovenian',
    'so': 'Somali',
    'es': 'Spanish',
    'su': 'Sundanese',
    'sw': 'Swahili',
    'sv': 'Swedish',
    'tg': 'Tajik',
    'ta': 'Tamil',
    'te': 'Telugu',
    'th': 'Thai',
    'tr': 'Turkish',
    'uk': 'Ukrainian',
    'ur': 'Urdu',
    'ug': 'Uyghur',
    'uz': 'Uzbek',
    'vi': 'Vietnamese',
    'cy': 'Welsh',
    'xh': 'Xhosa',
    'yi': 'Yiddish',
    'yo': 'Yoruba',
    'zu': 'Zulu',
}

HANG_MAN = [
    'https://images.klappstuhl.me/gallery/mUZMwfXDtS.png',  # Hangman 0
    'https://images.klappstuhl.me/gallery/HMbpLMtPmT.png',
    'https://images.klappstuhl.me/gallery/kOTROrapxh.png',
    'https://images.klappstuhl.me/gallery/fQWXqJSbLS.png',
    'https://images.klappstuhl.me/gallery/LDuaOZqsHe.png',
    'https://images.klappstuhl.me/gallery/RdZkqyntSS.png',
    'https://images.klappstuhl.me/gallery/PncSvIxMJI.png'
]

HELP_PAGES = [
    inspect.cleandoc(
        """
        ## Introduction
        Here you can find all *Message-/Slash-Commands* for {name}.
        Try using the dropdown to navigate through the categories to get a list of all Commands.
    
        I'm open source! You can find my code on [GitHub](https://github.com/klappstuhlpy/Percy).
        ## More Help
        Alternatively you can use the following Commands to get Information about a specific Command or Category:
        - `{prefix}help` *`command`*
        - `{prefix}help` *`category`*
        ## Support
        For more help, consider joining the official server over at
        https://discord.com/invite/eKwMtGydqh.
        ## Stats
        Total of **{command_runs}** command runs.
        Currently are **{commands}** commands loaded.
        """
    ),
    (
        ('<argument>', 'This argument is **required**.'),
        ('[argument]', 'This argument is **optional**.'),
        ('<A|B>', 'This means **multiple choice**, you can choose by using one. Although it must be A or B.'),
        ('<argument...>', 'There are multiple Arguments.'),
        ('<\'argument\'>', 'This argument is case-sensitive and should be typed exaclty as shown.'),
        ('<argument=A>', 'The default value if you dont provide one of this argument is A.'),
        (
            'Flags',
            'Flags are mostly available for commands with many arguments.\n'
            'They can provide a better overview and are not required to be typed in.\n'
            '\n'
            'Flags are prefixed with `--` and can be used like this:\n'
            '- `{prefix}command --flag1 argument1 --flag2 argument2`\n'
            '- `{prefix}command --flag1 argument1 --flag2 argument2 --flag3 argument3`\n'
            'Some **first** flag may be used without the `--` prefix:'
            '- `{prefix}command argument1 --flag2 argument2`\n'
            '\n'
            'Flag values can also be more than one word long, they end with the next flag you type (`--`):\n'
            '- `{prefix}command --flag1 my first argument --flag2 \'argument 2`\''
        ),
        (
            '\u200b',
            '<:discord_info:1113421814132117545> **Important:**\n'
            'Do not type the arguments in brackets.\n'
            'Most of the Commands are **Hybrid Commands**, which means that you can use them as Slash Commands or Message Commands.'
        )
    ),
    inspect.cleandoc(
        """
        ## License
        Percy is licensed and underlying the [MPL-2.0 License](https://www.tldrlegal.com/license/mozilla-public-license-2-0-mpl-2) and Guidelines.
        ## Source Code
        You can obtain a copy of myself over at [GitHub](https://github.com/klappstuhlpy/Percy)
        ## Credits
        I was made by <@991398932397703238>.

        Any questions regarding licensing and credits can be directed to <@991398932397703238>.
        """
    ),
]
