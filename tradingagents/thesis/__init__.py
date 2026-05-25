"""Thesis research agent — WealthPilot 投资逻辑卡引擎的调研产出端.

设计 (跟 WealthPilot PRD §4.2 / §8 对齐):
  - 跨 repo 读 WealthPilot 的 thesisKnowledge.json (env: WEALTHPILOT_REPO).
  - 按 researchHint 联网调研每个关切点 (Anthropic Claude + native web_search).
  - 强制证据 (没有可点 URL → status=unknown), 透传 previousStatus.
  - 落盘 ~/.market_data/thesis/observations_<date>.json + symlink _latest.json.

入口: scripts/research_thesis.py CLI.

这个 module 跟现有 trading-debate (run_batch.py) 完全独立, 不复用 graph /
debate / scoring 的代码 — 调研方向不同 (单只票全景 vs 逐指标定向), 共享
基础设施只剩 LLM client (而且这里直接用 raw anthropic SDK 拿 native
web_search, 不走 langchain).
"""
from .types import (
    ConcernObservation,
    ObservationsBundle,
    ResearchConfig,
    ThesisCard,
    ThesisKnowledge,
)

__all__ = [
    "ConcernObservation",
    "ObservationsBundle",
    "ResearchConfig",
    "ThesisCard",
    "ThesisKnowledge",
]
