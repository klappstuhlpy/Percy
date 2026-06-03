"""Tests for :mod:`app.services.char_info`."""

from __future__ import annotations

from app.services import MAX_CHARACTERS, CharInfo, get_char_info


def test_max_characters_constant() -> None:
    assert MAX_CHARACTERS == 50


def test_bmp_character_uses_short_escape() -> None:
    info = get_char_info('A')

    assert info == CharInfo(
        char='A',
        codepoint='41',
        escape='\\u0041',  # zero-padded to 4 hex digits
        name='LATIN CAPITAL LETTER A',
        url='https://www.compart.com/en/unicode/U+0041',
    )


def test_astral_character_uses_long_escape() -> None:
    info = get_char_info('\U0001f600')  # grinning face emoji

    assert info.codepoint == '1f600'
    assert info.escape == '\\U0001f600'  # >4 hex digits -> 8-wide \U form
    assert info.name == 'GRINNING FACE'


def test_unnamed_character_yields_empty_name() -> None:
    info = get_char_info('\x00')  # control char has no Unicode name

    assert info.name == ''
    assert info.escape == '\\u0000'
