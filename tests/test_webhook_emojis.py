from __future__ import annotations

from app.utils.webhooks import strip_custom_emojis


def test_strips_custom_and_animated_emojis_everywhere() -> None:
    payload = {
        'title': '<:greenTick:1322354661289754755> Member banned',
        'fields': [{'name': '<a:loading:1322356006054793226>', 'value': 'by <:x_:1> **mod**'}],
        'url': 'https://example.com/<not-emoji>',
    }
    assert strip_custom_emojis(payload) == {
        'title': 'Member banned',
        'fields': [{'name': '​', 'value': 'by **mod**'}],
        'url': 'https://example.com/<not-emoji>',
    }
    assert strip_custom_emojis('') == ''
