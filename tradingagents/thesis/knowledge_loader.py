"""读 WealthPilot repo 里的 thesisKnowledge.json (跨 repo 跨语言).

为什么是绝对路径而不是 git submodule / package import:
  - WealthPilot 是 TS 项目, 没法 pip install.
  - 知识库内容是 editorial JSON, 几个月才动一次, 不需要打包通路.
  - 用户在同台机上同时维护两个 repo, 直接读文件最简单.

路径解析:
  1. cfg.wealthpilot_repo 显式参数
  2. env WEALTHPILOT_REPO
  3. ~/Documents/Code/WealthPilot  (开发机默认)

文件路径 = <repo>/src/features/thesis/thesisKnowledge.json
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from .types import ResearchConfig, ThesisKnowledge


KNOWLEDGE_RELATIVE_PATH = "src/features/thesis/thesisKnowledge.json"


def resolve_wealthpilot_repo(cfg: ResearchConfig) -> Path:
    """决定 WealthPilot repo 根路径."""
    raw = cfg.wealthpilot_repo or os.environ.get("WEALTHPILOT_REPO")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path.home() / "Documents" / "Code" / "WealthPilot"


def resolve_knowledge_path(cfg: ResearchConfig) -> Path:
    return resolve_wealthpilot_repo(cfg) / KNOWLEDGE_RELATIVE_PATH


def load_knowledge(cfg: Optional[ResearchConfig] = None) -> ThesisKnowledge:
    """读 thesisKnowledge.json, 返回 typed dict.

    任何路径 / IO / 解析错误直接 raise — 调研 agent 没有静态知识就没法跑,
    fallback 没有意义.
    """
    cfg = cfg or ResearchConfig()
    path = resolve_knowledge_path(cfg)
    if not path.exists():
        raise FileNotFoundError(
            f"thesisKnowledge.json not found at {path}.\n"
            f"  set WEALTHPILOT_REPO env or pass --wealthpilot-repo to point to the repo root."
        )
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    # 软 schema check (字段缺失早暴露)
    if "cards" not in data or "tracks" not in data:
        raise ValueError(
            f"thesisKnowledge.json shape unexpected — missing 'cards' or 'tracks': {path}"
        )
    return data  # type: ignore[return-value]
