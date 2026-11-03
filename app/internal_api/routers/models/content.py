"""Response models for ``content.py`` -- the source of truth for percy-client's generated types
(dashboard/crates/percy-client/GENERATION.md). Class names are the Rust type names; ids travel as
strings, timestamps as ISO text (naive UTC is not a valid ``datetime`` for the generated client)."""
from __future__ import annotations

from pydantic import BaseModel


class PollInfo(BaseModel):
    id: int
    channel_id: str
    message_id: str
    question: str
    description: str
    options: list[str]
    image_url: str
    color: str
    published: str | None
    expires: str | None
    ended: bool
    total_votes: int
    hide_results: bool
    role_id: str | None


class PollsResponse(BaseModel):
    polls: list[PollInfo]
    total: int


class GiveawayInfo(BaseModel):
    id: int
    channel_id: str
    message_id: str
    author_id: str
    title: str
    description: str | None
    winners_count: int
    entries: int
    ended: bool
    ends_at: str | None
    required_role_id: str | None
    min_account_days: int
    min_join_days: int


class GiveawaysResponse(BaseModel):
    giveaways: list[GiveawayInfo]
    total: int


class TagInfo(BaseModel):
    id: int
    name: str
    owner_id: str | None
    owner_name: str | None
    uses: int
    created_at: str | None


class TagCreator(BaseModel):
    user_id: str | None
    username: str
    count: int


class TagsResponse(BaseModel):
    total: int
    total_uses: int
    tags: list[TagInfo]
    top_creators: list[TagCreator]


class TagDetail(BaseModel):
    id: int
    name: str
    content: str
    owner_id: str | None
    owner_name: str | None
    uses: int
    created_at: str | None


class ComicFeedEntry(BaseModel):
    id: int
    brand: str
    channel_id: str
    format: str
    day: int
    ping: str | None
    pin: bool
    next_pull: str | None


class ComicsResponse(BaseModel):
    feeds: list[ComicFeedEntry]


class ActiveTempChannel(BaseModel):
    channel_id: str
    channel_name: str
    user_count: int


class TempChannelEntry(BaseModel):
    channel_id: str
    channel_name: str
    format: str
    active_channels: list[ActiveTempChannel]
    total_users: int


class TempChannelsResponse(BaseModel):
    entries: list[TempChannelEntry]


class LockdownEntry(BaseModel):
    channel_id: str
    channel_name: str


class LockdownsResponse(BaseModel):
    entries: list[LockdownEntry]


class EmojiStatEntry(BaseModel):
    emoji_id: str
    emoji_name: str
    emoji_url: str | None
    total: int


class EmojiStatsResponse(BaseModel):
    total_uses: int
    distinct_emojis: int
    entries: list[EmojiStatEntry]
    total: int
