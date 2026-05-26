"""Backwards-compat shim — 按 cfg.provider 分发到 providers/<provider>.py.

v2 签名: (segment, track, concern, player, prev_status, cfg, client).
player=None → 环节级调研 (景气信号); 否则 → Player 公司级 (份额 / 卡位).
"""
from __future__ import annotations

from typing import Optional

from .providers import get_research_fn
from .types import (
    ConcernDefinition,
    ConcernObservation,
    HealthStatus,
    Player,
    ResearchConfig,
    Segment,
    ThesisTrack,
)


def research_concern(
    segment: Segment,
    track: Optional[ThesisTrack],
    concern: ConcernDefinition,
    player: Optional[Player],
    previous_status: Optional[HealthStatus],
    cfg: ResearchConfig,
    client: object = None,
) -> ConcernObservation:
    fn = get_research_fn(cfg.provider)
    return fn(segment, track, concern, player, previous_status, cfg, client)
