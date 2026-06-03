"""Tests for :mod:`app.services.code_stats`.

Drives the pure source-tree counter against a temporary directory so the logic
extracted from the ``stats`` cog can be verified without a bot or the real repo.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.services import CodeStats, count_code_stats

if TYPE_CHECKING:
    from pathlib import Path


def test_empty_tree_is_all_zero(tmp_path: Path) -> None:
    assert count_code_stats(tmp_path) == CodeStats()


def test_non_python_files_are_ignored(tmp_path: Path) -> None:
    (tmp_path / 'notes.txt').write_text('class Foo:\n', encoding='utf8')
    (tmp_path / 'data.json').write_text('{"def": 1}\n', encoding='utf8')

    assert count_code_stats(tmp_path) == CodeStats()


def test_counts_classes_functions_comments_and_lines(tmp_path: Path) -> None:
    source = (
        'import os  # stdlib\n'
        '\n'
        'class Widget:\n'
        '    def render(self):\n'
        '        return 1\n'
        '\n'
        '    async def fetch(self):\n'
        '        return 2  # inline comment\n'
    )
    (tmp_path / 'widget.py').write_text(source, encoding='utf8')

    stats = count_code_stats(tmp_path)

    assert stats.files == 1
    assert stats.classes == 1
    assert stats.functions == 2  # def + async def
    assert stats.comments == 2  # the import line and the inline comment
    assert stats.lines == 8
    assert stats.characters == len(source)


def test_recurses_into_subdirectories(tmp_path: Path) -> None:
    (tmp_path / 'a.py').write_text('class A: ...\n', encoding='utf8')
    nested = tmp_path / 'pkg' / 'sub'
    nested.mkdir(parents=True)
    (nested / 'b.py').write_text('def b(): ...\n', encoding='utf8')

    stats = count_code_stats(tmp_path)

    assert stats.files == 2
    assert stats.classes == 1
    assert stats.functions == 1


def test_ignored_directories_are_skipped(tmp_path: Path) -> None:
    (tmp_path / 'keep.py').write_text('class Keep: ...\n', encoding='utf8')
    venv = tmp_path / 'venv'
    venv.mkdir()
    (venv / 'vendored.py').write_text('class Vendored: ...\ndef helper(): ...\n', encoding='utf8')

    stats = count_code_stats(tmp_path, ignored=[venv])

    assert stats.files == 1  # only keep.py
    assert stats.classes == 1
    assert stats.functions == 0
