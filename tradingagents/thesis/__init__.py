"""Thesis research agent — WealthPilot 投资逻辑引擎的调研产出端 (v2).

设计 (跟 WealthPilot PRD v2 §4.2 / §6.5 / §8 对齐):
  - 读 ~/.market_data/thesis/knowledge.json (WealthPilot vite dev server 同步过来,
    跨 repo 解耦; agent 这边只认共享数据根).
  - 实体倒置 (v2): Segment (环节) 是主体, Player (公司在环节里的卡位) 嵌在
    Segment 下. observation 用扁平 concernId 单键.
  - 两阶段门控调研 (PRD §6.5):
      阶段一: 调研环节级 concerns → 算环节景气度
      阶段二: bearish 环节 **跳过** Player concerns (写进 gatedSegmentIds);
              其它环节正常调研 Player.
  - 强制证据 (没有可点 URL → status=unknown), 透传 previousStatus (同 provider+
    model 内对账).
  - 落盘 observations.<provider>-<model>.{<date>,latest}.json (多 LLM 并存).

入口: scripts/research_thesis.py CLI.
"""
from .types import (
    ConcernObservation,
    ObservationsBundle,
    Player,
    ResearchConfig,
    Segment,
    ThesisKnowledge,
    ThesisTrack,
)

__all__ = [
    "ConcernObservation",
    "ObservationsBundle",
    "Player",
    "ResearchConfig",
    "Segment",
    "ThesisKnowledge",
    "ThesisTrack",
]
