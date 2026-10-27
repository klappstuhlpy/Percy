"""Giveaway CV2 cards and winner draw, built from fake data (no bot)."""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import discord

from app.cogs.giveaway import Giveaway, entry_error, winners_layout


def _limits(view: discord.ui.LayoutView) -> None:
    children = list(view.walk_children())
    assert len(children) <= 40
    assert sum(len(c.content) for c in children if isinstance(c, discord.ui.TextDisplay)) <= 4000


def _custom_ids(view: discord.ui.LayoutView) -> list[str]:
    items = (c.item if isinstance(c, discord.ui.DynamicItem) else c for c in view.walk_children())
    return [c.custom_id for c in items if isinstance(c, discord.ui.Button) and c.custom_id]


class FakeDB:
    def __init__(self) -> None:
        self.saved: set[int] | None = None
        self.giveaways = self

    async def set_entries(self, _id: int, entries: set[int]) -> None:
        self.saved = set(entries)


def _member(uid: int, *, bot: bool = False) -> SimpleNamespace:
    return SimpleNamespace(id=uid, bot=bot, mention=f"<@{uid}>")


def make(entries: list[int], *, winners: int = 1, ended: bool = False, prize: str = "Nitro", members=None) -> Giveaway:
    members = members if members is not None else {uid: _member(uid) for uid in entries}
    guild = SimpleNamespace(id=1)
    db = FakeDB()

    async def get_or_fetch_member(_guild: object, uid: int) -> SimpleNamespace | None:
        return members.get(uid)

    bot = SimpleNamespace(get_guild=lambda _id: guild, get_or_fetch_member=get_or_fetch_member, db=db)
    expires = discord.utils.utcnow() + datetime.timedelta(hours=-1 if ended else 1)
    return Giveaway(cog=SimpleNamespace(bot=bot), record={
        "id": 7, "channel_id": 2, "message_id": 3, "guild_id": 1, "author_id": 99, "entries": entries,
        "metadata": {"args": [], "kwargs": {
            "prize": prize, "description": "d" * 5000, "winner_count": winners,
            "created": discord.utils.utcnow().isoformat(), "expires": expires.isoformat(),
        }},
    })


def test_running_card_has_persistent_enter_button() -> None:
    view = make([1, 2]).to_layout()
    _limits(view)
    assert _custom_ids(view) == ["giveaway:enter:7"]  # same format legacy embed giveaways carry
    assert view.is_persistent()


def test_ended_and_cancelled_cards_have_no_button_and_fit() -> None:
    g = make([], prize="x" * 1000)
    many = [_member(i) for i in range(100)]
    for view in (g.to_layout(winners=many), g.to_layout(state="cancelled"), winners_layout(g, many)):  # type: ignore[arg-type]
        _limits(view)
    assert _custom_ids(g.to_layout(winners=[])) == []
    assert _custom_ids(g.to_layout(state="cancelled")) == []


def test_entry_rules() -> None:
    g = make([1])
    assert entry_error(g, 1) is not None  # double entry
    assert entry_error(g, 99) is not None  # host
    assert entry_error(g, 2) is None
    assert entry_error(make([], ended=True), 2) is not None


async def test_winners_distinct_skip_host_bots_and_leavers() -> None:
    members = {1: _member(1), 2: _member(2, bot=True), 3: _member(3), 99: _member(99)}  # 4 left the server
    g = make([1, 2, 3, 4, 99], winners=5, members=members)
    winners = await g.get_winners()
    assert sorted(w.id for w in winners) == [1, 3]
    assert g.bot.db.saved == set()


async def test_fewer_winners_than_slots_and_reroll_pool() -> None:
    g = make([1, 2, 3], winners=2)
    first = {w.id for w in await g.get_winners()}
    assert len(first) == 2
    assert g.bot.db.saved == {1, 2, 3} - first
    rest = {w.id for w in await g.get_winners()}
    assert rest == {1, 2, 3} - first and not rest & first
    assert await g.get_winners() == []
    _limits(winners_layout(g, [], rerolled=True))


def test_requirement_error() -> None:
    from app.cogs.giveaway import requirement_error

    now = datetime.datetime(2026, 1, 31, tzinfo=datetime.UTC)
    day = datetime.timedelta(days=1)
    member = SimpleNamespace(roles=[SimpleNamespace(id=5)], created_at=now - 10 * day, joined_at=now - 2 * day)
    assert requirement_error({}, member, now) is None
    assert requirement_error({"required_role_id": 5}, member, now) is None
    assert "role" in requirement_error({"required_role_id": 6}, member, now)
    assert "account" in requirement_error({"min_account_days": 30}, member, now)
    assert "server" in requirement_error({"min_join_days": 7}, member, now)
    assert requirement_error({"min_join_days": 1}, member, now) is None


async def test_add_entry_sql_refuses_ended() -> None:
    from app.database.repositories.community import GiveawaysRepository

    seen: list[str] = []

    class R(GiveawaysRepository):
        def __init__(self) -> None: ...

        async def fetchval(self, query: str, *a: object) -> int | None:
            seen.append(query)
            return None

    assert await R().add_entry(1, 2) is False
    assert "ended" in seen[0]
