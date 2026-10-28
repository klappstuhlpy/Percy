from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.cogs.leveling.models import LevelConfig


def _run(level: int, *, stack: bool, owned: set[int]) -> tuple[SimpleNamespace, object]:
    user = SimpleNamespace(_roles=set(owned), edit=AsyncMock())
    fake = SimpleNamespace(
        user=user, config=SimpleNamespace(level_roles={10: 5, 20: 10, 30: 20}, role_stack=stack)
    )
    return user, LevelConfig.update_roles(fake, level)  # type: ignore[arg-type]


async def test_highest_role_only_swaps_lower_one() -> None:
    user, coro = _run(10, stack=False, owned={10, 99})
    await coro
    assert {o.id for o in user.edit.call_args.kwargs['roles']} == {20, 99}


async def test_stack_keeps_every_earned_role() -> None:
    user, coro = _run(10, stack=True, owned={10, 99})
    await coro
    assert {o.id for o in user.edit.call_args.kwargs['roles']} == {10, 20, 99}


async def test_no_edit_when_already_correct() -> None:
    user, coro = _run(10, stack=False, owned={20, 99})
    await coro
    user.edit.assert_not_called()
