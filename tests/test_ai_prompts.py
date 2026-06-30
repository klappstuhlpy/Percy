"""Tests for the assistant persona builder (:mod:`app.services.ai.prompts`).

The persona is curated, secret-free knowledge. These tests pin the security contract
(refusal language is present) and the dynamic context injection (prefix / server / URLs).
"""

from __future__ import annotations

from app.services.ai import (
    ASSISTANT_SYSTEM,
    DASHBOARD_SECTIONS,
    PERCY_IDENTITY,
    build_assistant_system,
    build_dashboard_assistant_system,
)


def test_identity_is_embedded() -> None:
    prompt = build_assistant_system()
    assert PERCY_IDENTITY in prompt
    assert prompt.startswith('You are Percy')


def test_security_rules_present() -> None:
    prompt = build_assistant_system()
    lowered = prompt.lower()
    # Defence-in-depth refusal language must survive any future edit.
    assert 'environment variables' in lowered
    assert 'system prompt' in lowered
    assert 'untrusted' in lowered


def test_forbids_simulating_features() -> None:
    lowered = build_assistant_system().lower()
    # The model must name the command (with a button), not role-play the feature.
    assert 'simulate' in lowered
    assert 'button' in lowered


def test_command_catalogue_rendered_when_given() -> None:
    prompt = build_assistant_system(
        prefix='?', command_catalogue=[('blackjack', 'Play blackjack'), ('poll', 'Create a poll')]
    )
    assert 'real commands' in prompt
    assert '`?blackjack` — Play blackjack' in prompt
    assert '`?poll` — Create a poll' in prompt


def test_no_catalogue_section_without_commands() -> None:
    assert 'real commands' not in build_assistant_system()


def test_forbids_inventing_accounts_and_setup() -> None:
    lowered = build_assistant_system().lower()
    assert 'account' in lowered  # must tell the model Percy needs no account/login/confirmation


def test_prefix_is_injected() -> None:
    prompt = build_assistant_system(prefix='b.')
    assert '`b.`' in prompt
    assert '`b.help`' in prompt


def test_server_name_injected_when_present() -> None:
    assert 'My Cool Server' in build_assistant_system(server_name='My Cool Server')


def test_server_line_omitted_when_absent() -> None:
    assert 'currently in the server' not in build_assistant_system(server_name=None)


def test_urls_injected() -> None:
    prompt = build_assistant_system(website='https://example.test', support_server='https://discord.gg/x')
    assert 'https://example.test' in prompt
    assert 'https://discord.gg/x' in prompt


def test_default_assistant_system_is_built() -> None:
    assert build_assistant_system() == ASSISTANT_SYSTEM
    assert isinstance(ASSISTANT_SYSTEM, str) and ASSISTANT_SYSTEM


# -- dashboard command-palette assistant ------------------------------------------------


def test_dashboard_prompt_embeds_identity_and_security() -> None:
    prompt = build_dashboard_assistant_system()
    assert PERCY_IDENTITY in prompt
    lowered = prompt.lower()
    # Same secret-free / injection contract as the in-Discord persona.
    assert 'environment variables' in lowered
    assert 'untrusted' in lowered
    assert 'system prompt' in lowered


def test_dashboard_prompt_lists_sections_by_label() -> None:
    prompt = build_dashboard_assistant_system()
    # Every section label is offered to the model so it can name a jump-to target.
    for _slug, label, _desc in DASHBOARD_SECTIONS:
        assert f'**{label}**' in prompt


def test_dashboard_prompt_is_web_oriented_not_discord() -> None:
    lowered = build_dashboard_assistant_system().lower()
    assert 'dashboard' in lowered
    # The in-Discord persona promises an auto-added "button"; the web prompt must not.
    assert 'button' not in lowered


def test_dashboard_prompt_renders_command_catalogue() -> None:
    prompt = build_dashboard_assistant_system(
        prefix='?', command_catalogue=[('play', 'Play a track'), ('level', 'Show your rank')]
    )
    assert '`?play` — Play a track' in prompt
    assert '`?level` — Show your rank' in prompt


def test_dashboard_prompt_server_name_and_prefix_injected() -> None:
    prompt = build_dashboard_assistant_system(server_name='Cool Guild', prefix='b.')
    assert 'Cool Guild' in prompt
    assert '`b.`' in prompt
