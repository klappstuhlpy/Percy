import re
from typing import Callable, Dict, Any, Union

import discord
import matplotlib

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

PossibleTarget = Union[
    discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel, discord.StageChannel, discord.GroupChannel,
    discord.ForumChannel, discord.Member, discord.Role, discord.Emoji, discord.PartialEmoji, discord.Invite,
    discord.StageInstance, discord.Webhook, discord.Message, discord.User, discord.Guild, discord.Thread,
    discord.ThreadMember, discord.Interaction
]
StarableChannel = Union[discord.TextChannel, discord.VoiceChannel, discord.Thread]
IgnoreableEntity = Union[discord.TextChannel, discord.VoiceChannel, discord.Thread, discord.User, discord.Role]

# REGEX

MENTION_REGEX = re.compile(r"<@(!?)([0-9]*)>")
URL_REGEX = re.compile(r'https?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*(),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+')

TOKEN_REGEX = re.compile(r'[a-zA-Z0-9_-]{23,28}\.[a-zA-Z0-9_-]{6,7}\.[a-zA-Z0-9_-]{27,}')
GITHUB_URL_REGEX = re.compile(r'https?://(?:www\.)?github\.com/[^/\s]+/[^/\s]+(?:/[^/\s]+)*/?')

EMOJI_REGEX = re.compile(r'<a?:.+?:([0-9]{15,21})>')
EMOJI_NAME_REGEX = re.compile(r'^[0-9a-zA-Z-_]{2,32}$')

CMYK_REGEX = re.compile(r"^\(?(?P<c>[0-9]{1,3})%?\s*,?\s*(?P<m>[0-9]{1,3})%?\s*,?\s*(?P<y>[0-9]{1,3})%?\s*,?\s*(?P<k>[0-9]{1,3})%?\)?$")
HEX_REGEX = re.compile(r"^(#|0x)(?P<hex>[a-fA-F0-9]{6})$")
RGB_REGEX = re.compile(r"^\(?(?P<red>[0-9]+),?\s*(?P<green>[0-9]+),?\s*(?P<blue>[0-9]+)\)?$")

GITHUB_FULL_REGEX = re.compile(
    r"""
        https?://                               # http:// or https://
        (?:www\.)?github\.com/                  # optional www. and github.com/
        (?P<user>[^/]+)/                        # capture the user/organization name
        (?P<repository>[^/]+)/                  # capture the repository name
        blob/                                   # literal "blob/"
        (?P<branch>[^/]+)/                      # capture the branch name
        (?:
            (?P<file_path>[^/]+(?:/[^/]+)*/)?   # capture the file path (optional)
            (?P<filename>[^/#]+\.[^/#]+)        # capture the filename
        )
        (?:\#.*$|$)                             # optional fragment identifier or end of line
    """,
    re.VERBOSE
)


GUILD_FEATURES = {
    'ANIMATED_BANNER': ('🖼️', 'Server can upload and use an animated banner.'),
    'ANIMATED_ICON': ('🌟', 'Server can upload an animated icon.'),
    'APPLICATION_COMMAND_PERMISSIONS_V2': ('🔒', 'Server is using the new command permissions system.'),
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
    (
        ""
    ), (
        """
          _______
         |/      |
         |      
         |      
         |       
         |      
         |
        _|___
        """
    ), (
        """
          _______
         |/      |
         |      (_)
         |      
         |       
         |      
         |
        _|___
        """
    ), (
        """
          _______
         |/      |
         |      (_)
         |       |
         |       |
         |      
         |
        _|___
        """
    ), (
        """
          _______
         |/      |
         |      (_)
         |      \\|/
         |       |
         |      
         |
        _|___
        """
    ), (
        """
          _______
         |/      |
         |      (_)
         |      \\|/
         |       |
         |      / \\
         |
        _|___
        """
    ), (
        """
          _______
         |/      |
         |      (x)
         |      \\|/
         |       |
         |      / \\
         |
        _|___
        """
    )
]