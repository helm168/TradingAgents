"""LLM provider 抽象 — 调研 agent 支持多 LLM, 产物按 provider+model 落盘.

每个 provider 实现 `research_concern(card, track, concern, prev, cfg) -> ConcernObservation`,
负责自己的 SDK / 工具 / response 解析. runner 只调 dispatch 函数, 不感知具体哪家.

Provider id 规范 (小写, 用在文件名 / CLI / UI):
  anthropic    — Claude + native web_search_20250305
  openai       — Responses API + web_search tool

Provider id 跟 model id 是两层概念:
  provider     = 哪家 SDK / 哪条接口
  model        = 具体型号 (claude-sonnet-4-5 / gpt-4o / ...)

文件名是 provider+model 拼起来防冲突: observations.openai-gpt-4o.latest.json.
"""
from __future__ import annotations

from typing import Callable, Optional

from ..types import (
    ConcernDefinition,
    ConcernObservation,
    HealthStatus,
    ResearchConfig,
    ThesisCard,
    ThesisTrack,
)

ResearchFn = Callable[
    [ThesisCard, Optional[ThesisTrack], ConcernDefinition, Optional[HealthStatus], ResearchConfig, object],
    ConcernObservation,
]


def get_research_fn(provider: str) -> ResearchFn:
    """按 provider id 返回对应 research function. client 参数留给 provider
    自己造 (Anthropic / OpenAI / ... SDK 不通用)."""
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
    """造对应 provider 的 SDK client. dry_run 不调."""
    p = provider.lower()
    if p == "anthropic":
        import anthropic
        return anthropic.Anthropic()
    if p == "openai":
        from openai import OpenAI
        return OpenAI()
    raise ValueError(f"unsupported thesis provider: {provider!r}")


def file_id(provider: str, model: str) -> str:
    """Provider + model 拼成文件 id (用作产物文件名后缀).

    OpenAI: openai-gpt-4o
    Anthropic: anthropic-claude-sonnet-4-5
    """
    safe_model = model.replace("/", "_").replace(":", "_")
    return f"{provider.lower()}-{safe_model}"
