"""Python 端 thesis 类型 — 镜像 WealthPilot src/features/thesis/types.ts.

用 TypedDict / 普通 dataclass 而不是 pydantic, 减少依赖, 同时跟 JSON I/O 对齐.
schema drift 时 runtime 报错好定位.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional, TypedDict


# ── 静态层 mirrors src/features/thesis/types.ts ─────────────────────

ScarcityTier = Literal["monopoly", "oligopoly", "moat", "commodity"]
LocalizationStage = Literal["blocked", "early", "catching-up", "self-sufficient"]
HealthStatus = Literal["bullish", "neutral", "bearish", "unknown"]
TrendDirection = Literal["up", "flat", "down", "unknown"]
Confidence = Literal["high", "medium", "low"]
TrackKind = Literal["chokepoint", "consumer"]


class ConcernRubric(TypedDict):
    bullish: str
    neutral: str
    bearish: str


class ResearchHint(TypedDict):
    query: str
    preferredSources: List[str]
    expectedShape: str


class ConcernDefinition(TypedDict):
    id: str
    label: str
    why: str
    rubric: ConcernRubric
    researchHint: ResearchHint


class ThesisDescriptor(TypedDict, total=False):
    track: str
    node: str
    scarcity: ScarcityTier
    localization: LocalizationStage  # optional
    summary: str


class ThesisCardMeta(TypedDict):
    author: str
    createdAt: str
    reviewedAt: str


class ThesisCard(TypedDict):
    companyId: str
    displayName: str
    thesis: ThesisDescriptor
    concerns: List[ConcernDefinition]
    meta: ThesisCardMeta


class ThesisTrack(TypedDict):
    id: str
    label: str
    kind: TrackKind
    summary: str


class ThesisKnowledge(TypedDict):
    version: int
    tracks: List[ThesisTrack]
    cards: List[ThesisCard]


# ── 动态层 mirrors src/features/thesis/types.ts ConcernObservation ──


class ObservationEvidence(TypedDict):
    source: str
    url: str
    quote: str
    publishedAt: str


class ConcernObservation(TypedDict, total=False):
    companyId: str
    concernId: str
    status: HealthStatus
    trend: TrendDirection
    headline: str
    detail: str
    metrics: dict  # Dict[str, str | int | float]
    evidence: List[ObservationEvidence]
    confidence: Confidence
    previousStatus: HealthStatus  # optional
    researchedAt: str


class AgentMeta(TypedDict, total=False):
    name: str
    model: str


class ObservationsBundle(TypedDict):
    generatedAt: str
    asOfNote: str
    agent: AgentMeta
    observations: List[ConcernObservation]


# ── Runner config (Python-side, 不出现在产出 JSON 里) ───────────────


@dataclass
class ResearchConfig:
    """单次调研 run 的参数."""

    # LLM
    model: str = "claude-sonnet-4-5"
    """Anthropic model id. 走 raw anthropic SDK, 不经 langchain."""

    max_web_search_uses: int = 5
    """单 concern 最多调 web_search 几次. 太高费 token, 太低查不全."""

    max_tokens: int = 4096

    # 跨 repo 路径
    wealthpilot_repo: Optional[str] = None
    """WealthPilot repo 根. None → 用 env WEALTHPILOT_REPO; 还没有 → 默认
    ~/Documents/Code/WealthPilot."""

    # 产出
    output_dir: Optional[str] = None
    """~/.market_data/thesis/. None → 用 env SH_QUANT_DATA_DIR + /thesis,
    fallback ~/.market_data/thesis/."""

    # 范围过滤
    only_company_ids: Optional[List[str]] = None
    """只跑指定 companyId 列表 (手动触发用); None → 全跑."""

    only_track_ids: Optional[List[str]] = None
    """只跑指定 track 下的卡; None → 全跑."""

    only_concern_ids: Optional[List[str]] = None
    """卡内 concern id 白名单 (跟 only_company_ids 组合); None → 全跑."""

    # 行为
    dry_run: bool = False
    """True → 只打印 prompt 不调 LLM, 也不落盘. 调研逻辑 review 用."""

    keep_previous_unchanged: bool = False
    """True → 没在本次范围内的 observation 沿用上次 latest.json 里的值
    (部分重跑). False (默认) → 不在范围内的丢掉. PRD §8.1 手动触发场景."""
