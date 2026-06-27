"""Tests for the assistant persona builder (:mod:`app.services.ai.prompts`).

The persona is curated, secret-free knowledge. These tests pin the security contract
(refusal language is present) and the dynamic context injection (prefix / server / URLs).
"""

from __future__ import annotations

from app.services.ai import ASSISTANT_SYSTEM, PERCY_IDENTITY, build_assistant_system


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


def test_prefix_is_injected() -> None:
    prompt = build_assistant_system(prefix='b.')
    assert '`b.`' in prompt
    assert '`b.help <command>`' in prompt


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
