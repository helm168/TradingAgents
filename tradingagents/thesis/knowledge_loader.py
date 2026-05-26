"""读 thesis 知识库 — 入口只有 `~/.market_data/thesis/knowledge.json`.

跟 agent_reports 通路对称: TradingAgents **只读共享数据根**, 不感知 WealthPilot
repo 位置. WealthPilot 启动 Vite dev server 时会自动把 repo 内的
thesisKnowledge.json sync 过来 (见 WealthPilot src/server/middleware/thesisMiddleware.ts).

路径解析:
  1. cfg.knowledge_path 显式参数 (调试用)
  2. env SH_QUANT_DATA_DIR + /thesis/knowledge.json
  3. ~/.market_data/thesis/knowledge.json  (默认)
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .types import ResearchConfig, ThesisKnowledge


def resolve_data_root() -> Path:
    """共享数据根 — 跟 WealthPilot localDataMiddleware / TradingAgents
    agent_reports 同契约."""
    raw = os.environ.get("SH_QUANT_DATA_DIR")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / ".market_data"


def resolve_knowledge_path(cfg: ResearchConfig) -> Path:
    if cfg.knowledge_path:
        return Path(cfg.knowledge_path).expanduser().resolve()
    return resolve_data_root() / "thesis" / "knowledge.json"


def load_knowledge(cfg: Optional[ResearchConfig] = None) -> ThesisKnowledge:
    """读 knowledge.json, 返回 typed dict.

    没找到文件 → raise FileNotFoundError, 提示用户启动一下 WealthPilot dev
    server (那边会 sync), 不静默兜底 — 知识库没有就完全没法跑.
    """
    cfg = cfg or ResearchConfig()
    path = resolve_knowledge_path(cfg)
    if not path.exists():
        raise FileNotFoundError(
            f"thesis knowledge not found at {path}.\n"
            f"  启动一次 WealthPilot dev server (pnpm dev) 会自动 sync 过来;\n"
            f"  或手动 cp <WealthPilot>/src/features/thesis/thesisKnowledge.json {path}"
        )
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if "segments" not in data or "tracks" not in data:
        raise ValueError(
            f"knowledge.json shape unexpected — missing 'segments' or 'tracks' (v2): {path}"
        )
    return data  # type: ignore[return-value]
