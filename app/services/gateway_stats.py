"""Gateway traffic summarization.

Extracted from the ``stats`` cog's ``gateway`` command: counting how many IDENTIFY
and RESUME events each shard emitted within a recent window is pure logic over the
bot's bookkeeping dicts, so it lives here free of Discord and is unit-testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from datetime import datetime

__all__ = (
    'GatewayTraffic',
    'summarize_gateway_traffic',
)


@dataclass(slots=True)
class GatewayTraffic:
    """Per-shard IDENTIFY/RESUME counts within a window, keyed by shard id."""

    identifies: dict[int, int]
    resumes: dict[int, int]

    @property
    def total_identifies(self) -> int:
        return sum(self.identifies.values())

    @property
    def total_resumes(self) -> int:
        return sum(self.resumes.values())


def summarize_gateway_traffic(
        identifies: Mapping[int, Iterable[datetime]],
        resumes: Mapping[int, Iterable[datetime]],
        *,
        since: datetime,
) -> GatewayTraffic:
    """Count, per shard, the IDENTIFY/RESUME timestamps strictly newer than ``since``.

    ``identifies`` / ``resumes`` map a shard id to the timestamps it recorded (as the
    bot keeps them); shards with no recent events still appear, with a count of zero.
    """
    return GatewayTraffic(
        identifies={shard_id: sum(1 for dt in dates if dt > since) for shard_id, dates in identifies.items()},
        resumes={shard_id: sum(1 for dt in dates if dt > since) for shard_id, dates in resumes.items()},
    )
