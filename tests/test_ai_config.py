"""Tests for the per-guild AI configuration model.

Covers the pure-logic surface of Phase 1 — the ``GuildConfig.AIFlags`` bitfield and the
``GuildAIConfig`` server-wide/per-channel override merge — plus a consistency guard that
the internal-API flag map stays in lockstep with the bitfield. No database needed.
"""

from __future__ import annotations

from app.database.base import GuildAIConfig, GuildConfig
from app.internal_api.routers.guild import _AI_FLAG_MAP

AIFlags = GuildConfig.AIFlags


def make_config(server: int = 0, overrides: dict[int, tuple[int, int]] | None = None) -> GuildAIConfig:
    return GuildAIConfig(guild_id=1, flags=AIFlags(server), overrides=overrides or {})


# -- flag bitfield ----------------------------------------------------------------


def test_flag_bit_values() -> None:
    assert AIFlags(0).value == 0
    f = AIFlags(0)
    f.assistant = True
    f.music = True
    assert f.value == 1 | 16
    assert f.assistant and f.music and not f.router


def test_api_flag_map_matches_bitfield() -> None:
    # The internal-API map must mirror GuildConfig.AIFlags exactly (names + bit values).
    for name, bit in _AI_FLAG_MAP.items():
        flag = AIFlags(0)
        setattr(flag, name, True)
        assert flag.value == bit, f"{name} bit mismatch"
    # And no flag is missing from the map.
    flag_names = {name for name in dir(AIFlags) if not name.startswith("_") and name not in ("value",)}
    # AIFlags exposes its flag accessors as attributes; ensure each map key is one of them.
    assert set(_AI_FLAG_MAP) <= flag_names


# -- override merge ---------------------------------------------------------------


def test_effective_without_override_returns_server_flags() -> None:
    config = make_config(server=AIFlags(0).value | 4)  # moderation on server-wide
    eff = config.effective(channel_id=999)
    assert eff.moderation is True
    assert config.is_enabled("moderation") is True
    assert config.is_enabled("moderation", channel_id=999) is True


def test_channel_override_can_disable_a_server_flag() -> None:
    # Server: moderation(4) + music(16) on. Channel 42 overrides moderation -> off.
    config = make_config(
        server=4 | 16,
        overrides={42: (4, 0)},  # controls moderation bit, sets it off
    )
    assert config.is_enabled("moderation") is True            # server-wide unchanged
    assert config.is_enabled("moderation", channel_id=42) is False  # overridden off
    assert config.is_enabled("music", channel_id=42) is True  # uncontrolled bit inherits


def test_channel_override_can_enable_a_server_disabled_flag() -> None:
    # Server: nothing on. Channel 7 turns on polls(32) just for itself.
    config = make_config(server=0, overrides={7: (32, 32)})
    assert config.is_enabled("polls") is False
    assert config.is_enabled("polls", channel_id=7) is True
    assert config.is_enabled("polls", channel_id=8) is False  # other channels inherit server


def test_override_only_affects_controlled_bits() -> None:
    # Channel controls only the music bit; moderation must still follow the server value.
    config = make_config(server=4, overrides={5: (16, 16)})
    eff = config.effective(channel_id=5)
    assert eff.music is True       # enabled by override
    assert eff.moderation is True  # inherited from server (not controlled by override)


def test_effective_returns_aiflags_instance() -> None:
    config = make_config(server=1)
    assert isinstance(config.effective(), AIFlags)
