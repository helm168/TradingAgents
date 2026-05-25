"""Runner — 遍历 cards × concerns, 调研每个关切点, 落盘.

多 LLM 并存
─────────
  文件按 provider+model 隔离:
    ~/.market_data/thesis/
      observations.anthropic-claude-sonnet-4-5.latest.json
      observations.anthropic-claude-sonnet-4-5.2026-05-25.json
      observations.openai-gpt-4o.latest.json
      observations.openai-gpt-4o.2026-05-25.json

  previousStatus 只在**同 provider+model** 内透传 — 不同模型口径 / temperature
  导致的判级差异不该被算作"上次→本次变化".

  WealthPilot middleware 启动时扫所有 observations.*.latest.json 文件,
  UI dropdown 切换.

流程:
  1. 读 ~/.market_data/thesis/knowledge.json.
  2. 读本 provider+model 的上次 latest (如果有), 索引 previousStatus.
  3. 按 cfg 过滤范围.
  4. 对每个 (card, concern) 调 research_concern → validate → 收集.
  5. atomic 写 dated + latest 文件 (同 file_id 后缀).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Dict, List, Optional

from .knowledge_loader import load_knowledge
from .providers import file_id, make_client
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


def latest_filename(cfg: ResearchConfig) -> str:
    return f"observations.{file_id(cfg.provider, cfg.model)}.latest.json"


def dated_filename(cfg: ResearchConfig, day: date) -> str:
    return f"observations.{file_id(cfg.provider, cfg.model)}.{day.isoformat()}.json"


def _load_previous_bundle(out_dir: Path, cfg: ResearchConfig) -> Optional[ObservationsBundle]:
    latest = out_dir / latest_filename(cfg)
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
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _build_bundle(
    observations: List[ConcernObservation], cfg: ResearchConfig
) -> ObservationsBundle:
    today = date.today()
    return {
        "generatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
        "asOfNote": f"{today.year} 年 {today.month} 月 第 {((today.day - 1) // 7) + 1} 周",
        "agent": {
            "name": "thesis-research-agent",
            "model": cfg.model,
            "provider": cfg.provider,
        },
        "observations": observations,
    }


def run_research(cfg: ResearchConfig) -> ObservationsBundle:
    """主入口. 返回组装好的 ObservationsBundle (dry_run 也返回, 便于自检)."""
    knowledge = load_knowledge(cfg)
    out_dir = resolve_output_dir(cfg)

    previous_bundle = _load_previous_bundle(out_dir, cfg)
    previous_idx = _index_previous(previous_bundle)
    tracks_by_id = _track_lookup(knowledge)
    cards = _select_cards(knowledge, cfg)

    logger.info(
        "thesis research: provider=%s model=%s; %d cards selected; dry_run=%s",
        cfg.provider, cfg.model, len(cards), cfg.dry_run,
    )

    client = None if cfg.dry_run else make_client(cfg.provider)

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
    dated_path = out_dir / dated_filename(cfg, date.today())
    latest_path = out_dir / latest_filename(cfg)
    _atomic_write_json(dated_path, bundle)  # type: ignore[arg-type]
    _atomic_write_json(latest_path, bundle)  # type: ignore[arg-type]
    logger.info("wrote %s + %s", dated_path.name, latest_path.name)
    return bundle
