"""Backwards-compat shim — 老 caller 直接 import research_concern. 真正逻辑
按 cfg.provider 分发到 providers/<provider>.py.

新代码应直接走 providers.get_research_fn(provider). 这里只保留一个 thin
wrapper, 让 runner 不需要每次 switch provider.
"""
from __future__ import annotations

from typing import Optional

from .providers import get_research_fn
from .types import (
    ConcernDefinition,
    ConcernObservation,
    HealthStatus,
    ResearchConfig,
    ThesisCard,
    ThesisTrack,
)


def research_concern(
    card: ThesisCard,
    track: Optional[ThesisTrack],
    concern: ConcernDefinition,
    previous_status: Optional[HealthStatus],
    cfg: ResearchConfig,
    client: object = None,
) -> ConcernObservation:
    """按 cfg.provider 分发. client 由 runner 提前造好传进来 (避免每个 concern
    都新建 SDK client)."""
    fn = get_research_fn(cfg.provider)
    return fn(card, track, concern, previous_status, cfg, client)
