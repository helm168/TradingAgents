"""Runner — 遍历知识库 cards × concerns, 调研每个关切点, 落盘.

流程:
  1. 读 thesisKnowledge.json (跨 repo).
  2. 读上次 observations_latest.json (如果存在), 索引 previousStatus.
  3. 按 ResearchConfig 过滤范围 (only_company_ids / only_track_ids / only_concern_ids).
  4. 对每个 (card, concern) 调 research_concern → validate → 收集.
  5. 写 observations_<date>.json + 软链 observations_latest.json (atomic).

PRD §8.4: 调研失败 / 没查到 → unknown + 说明原因, **不沿用旧值冒充新值**.
但 keep_previous_unchanged=True 时, 不在本次范围内的 observation 沿用上次值
(部分重跑场景, 例: "只重刷 NVDA 一只票").
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

import anthropic

from .knowledge_loader import load_knowledge
from .research_agent import research_concern
from .types import (
    ConcernObservation,
    HealthStatus,
    ObservationsBundle,
    ResearchConfig,
    ThesisCard,
    ThesisKnowledge,
    ThesisTrack,
)
from .validators import validate_observation

logger = logging.getLogger(__name__)


def resolve_output_dir(cfg: ResearchConfig) -> Path:
    if cfg.output_dir:
        return Path(cfg.output_dir).expanduser().resolve()
    sh_root = os.environ.get("SH_QUANT_DATA_DIR")
    if sh_root:
        return Path(sh_root).expanduser().resolve() / "thesis"
    return Path.home() / ".market_data" / "thesis"


def _load_previous_bundle(out_dir: Path) -> Optional[ObservationsBundle]:
    latest = out_dir / "observations_latest.json"
    if not latest.exists():
        return None
    try:
        with latest.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning("read previous latest failed: %s", e)
        return None


def _index_previous(
    bundle: Optional[ObservationsBundle],
) -> Dict[str, ConcernObservation]:
    if not bundle:
        return {}
    out: Dict[str, ConcernObservation] = {}
    for o in bundle.get("observations", []):
        key = f"{o.get('companyId')}:{o.get('concernId')}"
        out[key] = o
    return out


def _select_cards(
    knowledge: ThesisKnowledge, cfg: ResearchConfig
) -> List[ThesisCard]:
    cards = knowledge["cards"]
    if cfg.only_company_ids:
        keep = set(cfg.only_company_ids)
        cards = [c for c in cards if c["companyId"] in keep]
    if cfg.only_track_ids:
        keep = set(cfg.only_track_ids)
        cards = [c for c in cards if c["thesis"].get("track") in keep]
    return cards


def _track_lookup(knowledge: ThesisKnowledge) -> Dict[str, ThesisTrack]:
    return {t["id"]: t for t in knowledge["tracks"]}


def _atomic_write_json(path: Path, data: dict) -> None:
    """tmp file + rename — 避免半写产物被 middleware 读到."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _update_latest_pointer(out_dir: Path, dated_file: Path) -> None:
    """observations_latest.json = 最新一份的副本 (不是 symlink — symlink 在
    WSL / Windows / docker volume 上行为不一致, 直接拷贝最稳).

    middleware 那边读 observations_latest.json 一个文件就行.
    """
    latest = out_dir / "observations_latest.json"
    # 复制内容 (不是 hard link, 不是 symlink — 直接 read+write)
    with dated_file.open("r", encoding="utf-8") as f:
        data = json.load(f)
    _atomic_write_json(latest, data)


def _build_bundle(
    observations: List[ConcernObservation], cfg: ResearchConfig
) -> ObservationsBundle:
    today = date.today()
    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "asOfNote": f"{today.year} 年 {today.month} 月 第 {((today.day - 1) // 7) + 1} 周",
        "agent": {"name": "thesis-research-agent", "model": cfg.model},
        "observations": observations,
    }


def run_research(cfg: ResearchConfig) -> ObservationsBundle:
    """主入口. 返回写出的 ObservationsBundle (即便 dry_run 也返回组装好的形态,
    便于上层 CLI 自检)."""
    knowledge = load_knowledge(cfg)
    out_dir = resolve_output_dir(cfg)

    previous_bundle = _load_previous_bundle(out_dir)
    previous_idx = _index_previous(previous_bundle)
    tracks_by_id = _track_lookup(knowledge)
    cards = _select_cards(knowledge, cfg)

    logger.info(
        "thesis research: %d cards selected; model=%s; dry_run=%s",
        len(cards), cfg.model, cfg.dry_run,
    )

    client: Optional[anthropic.Anthropic] = None if cfg.dry_run else anthropic.Anthropic()

    fresh_observations: List[ConcernObservation] = []
    covered_keys: set[str] = set()
    coerced = 0

    for card in cards:
        track = tracks_by_id.get(card["thesis"].get("track", ""))
        for concern in card["concerns"]:
            if cfg.only_concern_ids and concern["id"] not in cfg.only_concern_ids:
                continue
            key = f"{card['companyId']}:{concern['id']}"
            covered_keys.add(key)
            prev: Optional[HealthStatus] = None
            prev_obs = previous_idx.get(key)
            if prev_obs:
                prev_status = prev_obs.get("status")
                if prev_status in ("bullish", "neutral", "bearish", "unknown"):
                    prev = prev_status  # type: ignore[assignment]

            logger.info("  researching %s ...", key)
            raw = research_concern(card, track, concern, prev, cfg, client)
            validated, was_coerced = validate_observation(raw)
            if was_coerced:
                coerced += 1
                logger.warning("    coerced to unknown: %s", validated.get("detail", ""))
            fresh_observations.append(validated)

    # 部分重跑场景: 沿用上次 latest 里不在本次范围的 observation
    if cfg.keep_previous_unchanged and previous_bundle:
        carried = 0
        for key, prev_obs in previous_idx.items():
            if key in covered_keys:
                continue
            fresh_observations.append(prev_obs)
            carried += 1
        if carried:
            logger.info("carried %d unchanged observations from previous bundle", carried)

    bundle = _build_bundle(fresh_observations, cfg)
    logger.info(
        "done: %d observations (%d coerced to unknown)",
        len(fresh_observations), coerced,
    )

    if cfg.dry_run:
        logger.info("dry-run: skipping write")
        return bundle

    out_dir.mkdir(parents=True, exist_ok=True)
    dated = out_dir / f"observations_{date.today().isoformat()}.json"
    _atomic_write_json(dated, bundle)  # type: ignore[arg-type]
    _update_latest_pointer(out_dir, dated)
    logger.info("wrote %s + observations_latest.json", dated.name)
    return bundle
