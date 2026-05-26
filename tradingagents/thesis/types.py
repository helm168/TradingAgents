"""Python 端 thesis 类型 (v2) — 镜像 WealthPilot src/features/thesis/types.ts.

实体倒置: 主体是 Segment (环节), 公司降为 Player (卡位玩家).
observation 用扁平单键 concernId (全局唯一).
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
    """**全局唯一** id (跨 segment / player). Observation 用 id 单键对账."""

    id: str
    label: str
    why: str
    rubric: ConcernRubric
    researchHint: ResearchHint


class Player(TypedDict, total=False):
    """公司在某环节里的卡位."""

    companyId: str
    displayName: str
    positioning: str
    scarcity: ScarcityTier  # optional, fallback Segment.scarcity
    concerns: List[ConcernDefinition]
    referenceOnly: bool  # 行业格局参考玩家 (港台日韩), 不调研


class Segment(TypedDict, total=False):
    id: str
    track: str
    label: str
    summary: str
    scarcity: ScarcityTier
    localization: LocalizationStage  # 仅 chokepoint 赛道
    concerns: List[ConcernDefinition]  # 环节级 (景气信号)
    players: List[Player]


class ThesisTrack(TypedDict):
    id: str
    label: str
    kind: TrackKind
    summary: str


class ThesisKnowledge(TypedDict):
    version: int
    tracks: List[ThesisTrack]
    segments: List[Segment]


# ── 动态层 mirrors src/features/thesis/types.ts ─────────────────────


class ObservationEvidence(TypedDict):
    source: str
    url: str
    quote: str
    publishedAt: str


class ConcernObservation(TypedDict, total=False):
    """扁平单键 concernId, 无 companyId (v2 实体倒置后不再需要复合键)."""

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
    provider: str


class ObservationsBundle(TypedDict, total=False):
    generatedAt: str
    asOfNote: str
    agent: AgentMeta
    gatedSegmentIds: List[str]  # PRD §5.2: 因环节下行未调研的环节
    observations: List[ConcernObservation]


# ── Runner config ───────────────────────────────────────────────────


@dataclass
class ResearchConfig:
    """单次调研 run 的参数."""

    # LLM
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-5"

    max_web_search_uses: int = 5
    max_tokens: int = 8192

    # I/O 路径
    knowledge_path: Optional[str] = None
    output_dir: Optional[str] = None

    # 范围过滤
    only_company_ids: Optional[List[str]] = None
    """只跑指定 companyId 列表 (匹配 Player.companyId)."""

    only_segment_ids: Optional[List[str]] = None
    """只跑指定 segment id 列表."""

    only_track_ids: Optional[List[str]] = None
    only_concern_ids: Optional[List[str]] = None

    # 行为
    dry_run: bool = False
    keep_previous_unchanged: bool = False

    # 门控 (v2)
    enable_gating: bool = True
    """True (默认) → 阶段二门控: bearish 环节跳过 Player concerns.
    False → 不门控 (调试用)."""
