"""LLM provider 抽象 (v2). 调研 agent 支持多 LLM, 产物按 provider+model 落盘.

每个 provider 实现:
  research_concern(segment, track, concern, player, prev, cfg, client) -> ConcernObservation

player=None 时是**环节级调研** (景气信号); 否则是 Player 公司级调研 (份额 / 卡位).

Provider id 规范 (小写, 用在文件名 / CLI / UI):
  anthropic    — Claude + native web_search_20250305
  openai       — Responses API + web_search tool

文件名 = provider+model: observations.openai-gpt-4o.latest.json.
"""
from __future__ import annotations

from typing import Callable, Optional

from ..types import (
    ConcernDefinition,
    ConcernObservation,
    HealthStatus,
    Player,
    ResearchConfig,
    Segment,
    ThesisTrack,
)

ResearchFn = Callable[
    [
        Segment,
        Optional[ThesisTrack],
        ConcernDefinition,
        Optional[Player],
        Optional[HealthStatus],
        ResearchConfig,
        object,
    ],
    ConcernObservation,
]


def get_research_fn(provider: str) -> ResearchFn:
    p = provider.lower()
    if p == "anthropic":
        from .anthropic_provider import research_concern as fn
        return fn  # type: ignore[return-value]
    if p == "openai":
        from .openai_provider import research_concern as fn
        return fn  # type: ignore[return-value]
    raise ValueError(
        f"unsupported thesis provider: {provider!r}. supported: anthropic, openai"
    )


def make_client(provider: str):
    p = provider.lower()
    if p == "anthropic":
        import anthropic
        return anthropic.Anthropic()
    if p == "openai":
        from openai import OpenAI
        return OpenAI()
    raise ValueError(f"unsupported thesis provider: {provider!r}")


def file_id(provider: str, model: str) -> str:
    safe_model = model.replace("/", "_").replace(":", "_")
    return f"{provider.lower()}-{safe_model}"
