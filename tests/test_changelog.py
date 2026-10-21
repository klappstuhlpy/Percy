from __future__ import annotations

from app.internal_api.changelog import parse

SOURCE = """\
## [Unreleased]

## [1.2.0] - 2026-10-21

### Fixed

- After Percy lost the **Ban Members** permission,
  got it back and lost it again,
  the loss went unreported.
- Second entry.
"""


def test_wrapped_bullet_is_one_entry() -> None:
    (release,) = parse(SOURCE)
    assert release.sections[0].entries == [
        'After Percy lost the **Ban Members** permission, got it back and lost it again, the loss went unreported.',
        'Second entry.',
    ]
