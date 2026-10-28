from __future__ import annotations

from app.internal_api.routers.content import _tag_problem


def test_numeric_tag_name_rejected_for_new_tags_only() -> None:
    assert _tag_problem('12345', 'x', set()) is not None
    assert _tag_problem('12345', 'x', set(), new=False) is None
    assert _tag_problem('v2', 'x', set()) is None
