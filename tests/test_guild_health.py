from __future__ import annotations

from app.services.guild_health import ChannelState, FeatureFacts, RoleState, assess, humanize_permission, summarize


def test_off_feature_reports_nothing_even_with_broken_facts() -> None:
    facts = FeatureFacts('polls', 'Polls', False, channels=[ChannelState('polls', 1, None)])
    assert assess(facts) == {'key': 'polls', 'label': 'Polls', 'status': 'off', 'problems': []}


def test_each_problem_names_the_channel_role_and_permission() -> None:
    facts = FeatureFacts(
        'leveling', 'Leveling', True,
        channels=[ChannelState('level-up', 1, 'levels', ('send_messages', 'attach_files')), ChannelState('x', 2, None)],
        roles=[RoleState('level reward', 3, 'Elite', assignable=False), RoleState('level reward', 4, None)],
        guild_missing=('manage_roles',),
    )
    result = assess(facts)
    assert result['status'] == 'attention'
    assert result['problems'] == [
        'Percy is missing the server permission Manage Roles.',
        'Percy is missing Send Messages, Attach Files in #levels (level-up).',
        'The x channel was deleted. Pick a new one.',
        "@Elite (level reward) is above Percy's highest role, so Percy can't give it out. "
        "Move Percy's role above it in Server Settings → Roles.",
        'The level reward role was deleted. Pick a new one.',
    ]


def test_summary_puts_attention_first_and_counts_it() -> None:
    report = summarize([
        FeatureFacts('a', 'A', False),
        FeatureFacts('b', 'B', True),
        FeatureFacts('c', 'C', True, notes=['broken']),
    ])
    assert [f['key'] for f in report['features']] == ['c', 'b', 'a']
    assert report['attention'] == 1
    assert humanize_permission('view_audit_log') == 'View Audit Log'
