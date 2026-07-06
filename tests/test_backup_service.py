"""Tests for the pure backup/template helpers in :mod:`app.services.backup`."""

from app.services import backup


class TestBuildBackup:
    def test_wraps_with_envelope(self) -> None:
        blob = backup.build_backup(42, {"tags": [{"name": "hi", "content": "there"}]})
        assert blob["kind"] == backup.BACKUP_KIND
        assert blob["version"] == backup.BACKUP_VERSION
        assert blob["guild_id"] == "42"
        assert blob["created_at"]
        assert blob["sections"]["tags"] == [{"name": "hi", "content": "there"}]

    def test_drops_non_portable_sections(self) -> None:
        blob = backup.build_backup(1, {"tags": [], "secrets": ["nope"], "comics": [{"brand": "X"}]})
        assert set(blob["sections"]) <= set(backup.PORTABLE_SECTIONS)
        assert "secrets" not in blob["sections"]
        assert "comics" not in blob["sections"]  # trimmed from portable set


class TestValidateBackup:
    def test_accepts_a_good_blob(self) -> None:
        blob = backup.build_backup(1, {"tags": []})
        assert backup.validate_backup(blob) == (True, None)

    def test_rejects_non_dict(self) -> None:
        ok, err = backup.validate_backup(["not", "a", "dict"])
        assert not ok and err

    def test_rejects_wrong_kind(self) -> None:
        ok, err = backup.validate_backup({"kind": "something.else", "version": 1, "sections": {}})
        assert not ok and "Percy backup" in err

    def test_rejects_future_version(self) -> None:
        ok, err = backup.validate_backup(
            {"kind": backup.BACKUP_KIND, "version": backup.BACKUP_VERSION + 1, "sections": {}}
        )
        assert not ok and "version" in err

    def test_rejects_bad_sections(self) -> None:
        ok, err = backup.validate_backup({"kind": backup.BACKUP_KIND, "version": 1, "sections": "nope"})
        assert not ok and err


class TestSelectSections:
    def _blob(self) -> dict:
        return backup.build_backup(1, {"config": {"prefixes": ["!"]}, "tags": [{"name": "a", "content": "b"}]})

    def test_none_returns_all_present(self) -> None:
        selected = backup.select_sections(self._blob(), None)
        assert set(selected) == {"config", "tags"}

    def test_explicit_subset(self) -> None:
        selected = backup.select_sections(self._blob(), ["tags"])
        assert set(selected) == {"tags"}

    def test_filters_unknown_names(self) -> None:
        selected = backup.select_sections(self._blob(), ["tags", "made_up"])
        assert set(selected) == {"tags"}


class TestSummarizeSections:
    def test_counts_lists_and_dicts(self) -> None:
        summary = backup.summarize_sections(
            {"tags": [1, 2, 3], "config": {"a": 1, "b": 2}, "weird": 5}
        )
        assert summary == {"tags": 3, "config": 2, "weird": 0}
