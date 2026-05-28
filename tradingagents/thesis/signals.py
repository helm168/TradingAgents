"""读 sh_quant ingest 的公开源 signals, 渲染成 tier 分组 evidence block.

public-sources PRD §4.4 / §5.3: thesis agent (TradingAgents) 直接读 sh_quant
落盘的 parquet (~/.market_data/industry_news/), 不经 middleware (v1 不进 UI
Repository). 同机共享数据根, 跟 knowledge.json 同约定.

给 agent 的 evidence block 按 tier 分组 (T1 > T3 > T2 > ...), 同 tier 内按时间
倒序. agent prompt 在 block 之上叠 web_search, 两者都标 tier.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# tier 排序权重 (越小越前): 一手 > 行业机构 > 二手 > 海外 > 付费 > 占位
_TIER_ORDER = {"T1": 0, "T3": 1, "T6": 2, "T2": 3, "T4": 4, "T7": 5, "T5": 6}
_TIER_LABEL = {
    "T1": "T1 一手公开",
    "T2": "T2 二手转述",
    "T3": "T3 行业研究机构 free",
    "T4": "T4 海外行业观察",
    "T5": "T5 社区",
    "T6": "T6 个人付费",
    "T7": "T7 受限占位",
}


def _data_root() -> Path:
    override = os.environ.get("SH_QUANT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".market_data"


def load_signals_for_segment(
    segment_id: str,
    *,
    max_age_days: int = 30,
    limit: int = 12,
) -> list[dict[str, Any]]:
    """读 industry_news/*.parquet, 返回命中该 segment 且在 max_age_days 内的
    signal 行 (dict). 按 tier 权重 + published_at desc 排序, 截断到 limit.

    pandas 是 sh_quant 的依赖; TradingAgents venv 也有 (run_batch 用). 没有
    parquet 文件时返回空 list (不报错 — 公开源是增强不是必需).
    """
    news_dir = _data_root() / "industry_news"
    if not news_dir.exists():
        return []

    try:
        import pandas as pd
    except ImportError:
        logger.warning("pandas 不可用, 跳过 signals 加载")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
    frames = []
    for p in sorted(news_dir.glob("*.parquet")):
        try:
            df = pd.read_parquet(p)
        except Exception as e:
            logger.warning("read %s failed: %s", p, e)
            continue
        frames.append(df)
    if not frames:
        return []

    import pandas as pd

    df = pd.concat(frames, ignore_index=True)

    # segment 命中: segments 列从 parquet 读回是 numpy array, 不能用 `or []`
    # (空 array truth value ambiguous). 显式判 None.
    def _hit(segs: Any) -> bool:
        if segs is None:
            return False
        return segment_id in list(segs)

    df = df[df["segments"].apply(_hit)]
    if len(df) == 0:
        return []

    # 时效过滤
    df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
    df = df[df["published_at"] >= cutoff]
    if len(df) == 0:
        return []

    df["_tier_order"] = df["source_tier"].map(lambda t: _TIER_ORDER.get(t, 9))
    df = df.sort_values(["_tier_order", "published_at"], ascending=[True, False])
    df = df.head(limit)

    out = []
    for _, row in df.iterrows():
        out.append(
            {
                "id": row.get("id"),
                "source_tier": row.get("source_tier"),
                "source_name": row.get("source_name"),
                "title": row.get("title"),
                "published_at": row["published_at"].date().isoformat(),
                "url": row.get("url"),
                "summary": row.get("summary") or "",
                "secondhand": bool(row.get("secondhand")),
                "cited_source": row.get("cited_source"),
                "key_numbers": _safe_json(row.get("key_numbers_json")),
            }
        )
    return out


def render_signals_block(signals: list[dict[str, Any]]) -> str:
    """按 tier 分组渲染 evidence block (PRD §5.3). 空时返回提示."""
    if not signals:
        return (
            "(本环节当前无 sh_quant 已 ingest 的公开源 signal. 完全依赖你的 "
            "web_search. 仍须按 tier 规则给每条 evidence 打 tier.)"
        )

    # 按 tier 分组
    by_tier: dict[str, list[dict[str, Any]]] = {}
    for s in signals:
        by_tier.setdefault(s["source_tier"], []).append(s)

    lines: list[str] = [
        "以下是 sh_quant 已 ingest 的公开源 signal (已按 tier 分组, 你应优先采信, "
        "并可用 web_search 补充 / 交叉验证):",
        "",
    ]
    for tier in sorted(by_tier, key=lambda t: _TIER_ORDER.get(t, 9)):
        lines.append(f"=== {_TIER_LABEL.get(tier, tier)} ===")
        for s in by_tier[tier]:
            head = f"[{s['source_name']} {s['published_at']}] {s['title']}"
            lines.append(head)
            if s.get("secondhand") and s.get("cited_source"):
                lines.append(f"  [secondhand, cited_source: {s['cited_source']}]")
            if s.get("summary"):
                lines.append(f"  {s['summary'][:300]}")
            kns = s.get("key_numbers") or []
            if kns:
                lines.append(f"  key_numbers: {json.dumps(kns, ensure_ascii=False)}")
            if s.get("url"):
                lines.append(f"  url: {s['url']}")
            lines.append("")
    return "\n".join(lines).rstrip()


def _safe_json(raw: Any) -> list:
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
