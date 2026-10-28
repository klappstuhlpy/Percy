"""Components V2 builders for the Discord status feed and the starboard, built with fake data (no bot)."""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import discord

from app.cogs.misc.dstatus import DiscordStatusMixin, Incident, incident_accent
from app.cogs.starboard import ui as starboard_ui
from app.core.components_v2 import Accent

NOW = discord.utils.utcnow()


def _iso(delta_minutes: int) -> str:
    return (NOW - datetime.timedelta(minutes=delta_minutes)).isoformat()


def _incident(
    incident_id: str = 'inc1',
    *,
    status: str = 'investigating',
    updates: int = 2,
    body: str = 'We are investigating.',
    age: int = 5,
    impact: str = 'major',
) -> Incident:
    payload: dict[str, Any] = {
        'id': incident_id,
        'name': 'Elevated API errors',
        'status': status,
        'impact': impact,
        'shortlink': f'https://stspg.io/{incident_id}',
        'created_at': _iso(age + 60),
        'started_at': _iso(age + 60),
        'updated_at': _iso(age),
        'brand_new_field': 'Statuspage adds keys over time',
        'incident_updates': [
            {
                'id': f'{incident_id}-u{i}',
                'status': 'investigating' if i < updates - 1 else status,
                'body': body,
                'created_at': _iso(age + updates - i),
                'display_at': _iso(age + updates - i),
                'affected_components': [
                    {'code': 'a', 'name': 'API', 'old_status': 'operational', 'new_status': 'major_outage'}
                ],
            }
            for i in range(updates)
        ],
        'components': [{'id': 'a', 'name': 'API', 'status': 'major_outage'}],
    }
    return Incident.from_payload(payload)


def _assert_within_limits(view: discord.ui.LayoutView) -> None:
    assert view.total_children_count <= 40
    assert view.content_length() <= 4000
    view.to_components()  # serialises without raising


# -- discord status ---------------------------------------------------------


def test_accent_mapping() -> None:
    assert incident_accent('investigating', 'minor') is Accent.warning
    assert incident_accent('identified', 'none') is Accent.warning
    assert incident_accent('investigating', 'critical') is Accent.error
    assert incident_accent('resolved', 'critical') is Accent.success
    assert incident_accent('in_progress', 'maintenance') is Accent.info
    assert incident_accent('scheduled', None) is Accent.info


def test_incident_card_latest_first_and_tolerant_parsing() -> None:
    incident = _incident(status='resolved', updates=3)
    assert incident.latest_update_id == 'inc1-u2'  # newest update leads the timeline
    assert incident.affected_names() == ['API']
    view = incident.build_view()
    _assert_within_limits(view)
    container = view.children[0]
    assert isinstance(container, discord.ui.Container)
    assert container.accent_colour == Accent.success.colour


def test_incident_card_stays_within_limits_for_huge_incidents() -> None:
    view = _incident(updates=40, body='x' * 3000).build_view()
    _assert_within_limits(view)
    texts = [c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay)]
    assert any('earlier update' in t for t in texts)


def test_unknown_status_does_not_raise() -> None:
    _assert_within_limits(_incident(status='postmortem').build_view())


class _FakeStatus(DiscordStatusMixin):
    def __init__(self) -> None:  # skip the cog __init__ chain
        self._seen_updates = {}
        self.published: list[tuple[str, bool]] = []
        self.posts: dict[str, tuple[int, str | None]] = {}

    async def _load_posts(self, saved: Any) -> dict[str, tuple[int, str | None]]:
        return dict(self.posts)

    async def _publish(self, saved: Any, incident: Incident, *, edit: bool, post: Any = None) -> bool:
        self.published.append((incident.id, edit))
        self.posts[incident.id] = (len(self.posts) + 1, incident.status)
        self._seen_updates[(saved.guild_id, incident.id)] = incident.latest_update_id
        return True


async def test_sync_posts_new_and_edits_tracked_without_ping_pong() -> None:
    feed = _FakeStatus()
    saved: Any = SimpleNamespace(id=None, status=None, guild_id=1, message_id=None)
    old, new = _incident('old', age=4), _incident('new', age=2)
    incidents = [new, old]  # newest first, as fetch_incidents returns them

    assert await feed._sync_subscriber(saved, incidents)
    assert feed.published == [('old', False), ('new', False)]  # oldest first, each its own post

    feed.published.clear()
    assert not await feed._sync_subscriber(saved, incidents)  # nothing changed: no dupes, no ping-pong
    assert feed.published == []

    resolved = _incident('new', status='resolved', updates=3, age=1)
    assert await feed._sync_subscriber(saved, [resolved, old])
    assert feed.published == [('new', True)]  # tracked incident edited in place

    feed.published.clear()
    newer_update = _incident('new', status='resolved', updates=4, age=0)  # same status, new update
    assert await feed._sync_subscriber(saved, [newer_update, old])
    assert feed.published == [('new', True)]


async def test_sync_keeps_updating_an_older_concurrent_incident() -> None:
    feed = _FakeStatus()
    saved: Any = SimpleNamespace(id=None, status=None, guild_id=1, message_id=None)
    old, new = _incident('old', age=4), _incident('new', age=2)
    await feed._sync_subscriber(saved, [new, old])
    feed.published.clear()
    resolved_old = _incident('old', status='resolved', updates=3, age=0)
    assert await feed._sync_subscriber(saved, [new, resolved_old])
    assert feed.published == [('old', True)]  # the older incident is still edited in place


async def test_sync_ignores_stale_incidents_for_fresh_subscription() -> None:
    feed = _FakeStatus()
    saved: Any = SimpleNamespace(id=None, status=None, guild_id=1, message_id=None)
    assert not await feed._sync_subscriber(saved, [_incident('ancient', age=60 * 24)])
    assert feed.published == []


# -- starboard ---------------------------------------------------------------


def _attachment(name: str, *, content_type: str | None = None, spoiler: bool = False) -> Any:
    return SimpleNamespace(
        filename=name, content_type=content_type, url=f'https://cdn.example/{name}', is_spoiler=lambda: spoiler
    )


def _message(content: str = 'hello <@1> @everyone', **overrides: Any) -> Any:
    data: dict[str, Any] = {
        'id': 42,
        'content': content,
        'channel': SimpleNamespace(mention='<#7>'),
        'author': SimpleNamespace(display_name='Alice', display_avatar=SimpleNamespace(url='https://cdn.example/a.png')),
        'reference': None,
        'attachments': [
            _attachment('cat.png', content_type='image/png'),
            _attachment('secret.jpg', spoiler=True),
            _attachment('notes.txt', content_type='text/plain'),
        ],
        'embeds': [discord.Embed(type='image').set_thumbnail(url='https://media.example/x.gif')],
        'stickers': [],
        'created_at': NOW,
        'jump_url': 'https://discord.com/channels/1/7/42',
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _texts(view: discord.ui.LayoutView) -> str:
    return '\n'.join(c.content for c in view.walk_children() if isinstance(c, discord.ui.TextDisplay))


def test_starboard_card_builds() -> None:
    replied = MagicMock(spec=discord.Message)
    replied.content = 'original message'
    replied.author.display_name = 'Bob'
    message = _message(reference=SimpleNamespace(resolved=replied))

    view = starboard_ui.build_starboard_view(message, emoji='\N{WHITE MEDIUM STAR}', star_count=5, threshold=3)
    _assert_within_limits(view)

    galleries = [c for c in view.walk_children() if isinstance(c, discord.ui.MediaGallery)]
    assert len(galleries) == 1
    items = galleries[0].items
    assert [i.media.url for i in items] == [
        'https://cdn.example/cat.png',
        'https://cdn.example/secret.jpg',
        'https://media.example/x.gif',
    ]
    assert items[1].spoiler

    texts = _texts(view)
    assert 'Replying to **Bob**: original message' in texts
    assert 'notes.txt' in texts and 'cat.png' not in texts
    assert '**5**' in texts

    sections = [c for c in view.walk_children() if isinstance(c, discord.ui.Section)]
    assert isinstance(sections[0].accessory, discord.ui.Thumbnail)
    buttons = [c for c in view.walk_children() if isinstance(c, discord.ui.Button)]
    assert buttons[0].url == message.jump_url


def test_starboard_card_truncates_long_content() -> None:
    view = starboard_ui.build_starboard_view(
        _message('y' * 6000, attachments=[], embeds=[]), emoji=':custom:', star_count=1, threshold=1
    )
    _assert_within_limits(view)


def test_starboard_posts_never_ping() -> None:
    mentions = starboard_ui.POST_MENTIONS
    assert not mentions.everyone and not mentions.users and not mentions.roles
