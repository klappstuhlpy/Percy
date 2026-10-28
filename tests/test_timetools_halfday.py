import asyncio
import datetime
from types import SimpleNamespace

from app.utils.timetools import UserFriendlyTime


def test_halfday_phrase_on_month_end_does_not_raise() -> None:
    created = datetime.datetime(2026, 10, 31, 3, 0, tzinfo=datetime.UTC)

    async def get_user_config(_: int) -> None:
        return None

    ctx = SimpleNamespace(
        bot=SimpleNamespace(db=SimpleNamespace(get_user_config=get_user_config)),
        author=SimpleNamespace(id=1),
        message=SimpleNamespace(created_at=created),
    )
    res = asyncio.run(UserFriendlyTime(default="x").convert(ctx, "tonight"))
    assert res.dt.month in (10, 11) and res.dt > created
