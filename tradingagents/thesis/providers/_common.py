"""Provider 共享: prompt 加载 / JSON 解析 / observation skeleton.

Prompt 落盘
────────
SYSTEM_PROMPT 和 user prompt 模板都从 .md 文件读, 不写死在 .py 里. 这样你
不动代码就能迭代 prompt. 加载顺序 (越靠前优先级越高):

  1. env THESIS_PROMPTS_DIR 指向的目录
  2. $SH_QUANT_DATA_DIR/thesis/prompts/   (默认 ~/.market_data/thesis/prompts/)
     ← **本地 override**: 想试新版 prompt 在这放, gitignored, agent 优先用
  3. <repo>/tradingagents/thesis/prompts/  ← 仓库内默认, 跟代码同源

模块 import 时一次性加载, 启动 log 会 print 用的是哪份, 方便排查 "为啥改了
prompt 不生效".
"""
from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from pathlib import Path
from typing import Optional, Tuple

from ..types import (
    ConcernDefinition,
    ConcernObservation,
    HealthStatus,
    ThesisCard,
    ThesisTrack,
)

logger = logging.getLogger(__name__)


# ── Prompt 加载 ───────────────────────────────────────────────────────

_REPO_PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"


def _candidate_prompt_dirs() -> list[Path]:
    """按优先级返回候选目录列表."""
    out: list[Path] = []
    env_dir = os.environ.get("THESIS_PROMPTS_DIR")
    if env_dir:
        out.append(Path(env_dir).expanduser().resolve())
    sh_root = os.environ.get("SH_QUANT_DATA_DIR")
    user_dir = (
        Path(sh_root).expanduser().resolve() / "thesis" / "prompts"
        if sh_root
        else Path.home() / ".market_data" / "thesis" / "prompts"
    )
    out.append(user_dir)
    out.append(_REPO_PROMPTS_DIR)
    return out


def _load_prompt(filename: str) -> Tuple[str, Path]:
    """返回 (内容, 实际命中的路径). 找不到任何候选 → raise."""
    tried = []
    for d in _candidate_prompt_dirs():
        p = d / filename
        tried.append(str(p))
        if p.exists():
            return p.read_text(encoding="utf-8"), p
    raise FileNotFoundError(
        f"thesis prompt {filename!r} not found. tried:\n  " + "\n  ".join(tried)
    )


SYSTEM_PROMPT, _SYSTEM_PROMPT_PATH = _load_prompt("system.md")
_USER_PROMPT_TEMPLATE, _USER_PROMPT_PATH = _load_prompt("user.md.tmpl")

logger.info("[thesis prompts] system  = %s", _SYSTEM_PROMPT_PATH)
logger.info("[thesis prompts] user    = %s", _USER_PROMPT_PATH)


def reload_prompts() -> None:
    """运行时强制重新加载 prompts (改完 .md 不重启进程也能用; 主要给
    REPL / test / dry-run 反复改 prompt 用)."""
    global SYSTEM_PROMPT, _USER_PROMPT_TEMPLATE
    SYSTEM_PROMPT, _ = _load_prompt("system.md")
    _USER_PROMPT_TEMPLATE, _ = _load_prompt("user.md.tmpl")


def build_user_prompt(
    card: ThesisCard,
    track: Optional[ThesisTrack],
    concern: ConcernDefinition,
    previous_status: Optional[HealthStatus],
) -> str:
    thesis = card["thesis"]
    track_label = track["label"] if track else thesis.get("track", "")
    hint = concern["researchHint"]
    rubric = concern["rubric"]
    prev_line = (
        f"上次调研判级: {previous_status}" if previous_status else "上次调研判级: 无 (首次)"
    )
    return _USER_PROMPT_TEMPLATE.format(
        display_name=card["displayName"],
        company_id=card["companyId"],
        track_label=track_label,
        node=thesis.get("node", ""),
        summary=thesis.get("summary", ""),
        concern_label=concern["label"],
        why=concern["why"],
        rubric_bullish=rubric["bullish"],
        rubric_neutral=rubric["neutral"],
        rubric_bearish=rubric["bearish"],
        hint_query=hint["query"],
        hint_sources=", ".join(hint["preferredSources"]),
        hint_shape=hint["expectedShape"],
        prev_line=prev_line,
        today=date.today().isoformat(),
    )


# Claude 实测会加 "根据搜索结果, 以下是..." 前言再用 ```json fence — 软约束不一定听.
_FENCED_JSON_RE = re.compile(r"```(?:json)?\s*\n?(\{[\s\S]*?\})\s*\n?```")


def parse_json_response(text: str) -> dict:
    """三步: 直接 parse → fence 抠取 → 第一个 { 到最后一个 } 兜底."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _FENCED_JSON_RE.search(text)
    if m:
        try:
            return json.loads(m.group(1))
        except json.JSONDecodeError:
            pass
    first = text.find("{")
    last = text.rfind("}")
    if first >= 0 and last > first:
        candidate = text[first : last + 1]
        try:
            return json.loads(candidate)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"LLM 抠出 JSON 不合法 (可能被 max_tokens 截断): {e}; raw[:500]={text[:500]!r}"
            )
    raise ValueError(f"LLM response 无 JSON 对象: raw[:500]={text[:500]!r}")


def empty_observation(
    card: ThesisCard,
    concern: ConcernDefinition,
    previous_status: Optional[HealthStatus],
) -> ConcernObservation:
    """构造一个 unknown skeleton, provider 拿到 LLM 输出后往里填."""
    obs: ConcernObservation = {
        "companyId": card["companyId"],
        "concernId": concern["id"],
        "status": "unknown",
        "trend": "unknown",
        "headline": "",
        "detail": "",
        "metrics": {},
        "evidence": [],
        "confidence": "low",
        "researchedAt": date.today().isoformat(),
    }
    if previous_status:
        obs["previousStatus"] = previous_status
    return obs


def merge_parsed_into_skeleton(
    skeleton: ConcernObservation, parsed: dict
) -> ConcernObservation:
    """把 LLM 返回的字段 merge 进 skeleton, 保留 companyId/concernId/researchedAt."""
    out: ConcernObservation = dict(skeleton)  # type: ignore[assignment]
    for key in (
        "status", "trend", "headline", "detail", "metrics", "evidence", "confidence",
    ):
        if key in parsed:
            out[key] = parsed[key]  # type: ignore[literal-required]
    return out
