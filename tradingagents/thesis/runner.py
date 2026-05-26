"""Runner (v2) — 两阶段门控调研, 落盘.

阶段一: 对每个 Segment, 调研所有**环节级 concerns** → 算环节景气度.
阶段二: 对**非 bearish** 的 Segment, 调研每个 Player 的公司级 concerns.
        bearish 环节直接跳过, 写进 gatedSegmentIds (PRD §6.5 "矮子里选将军不干").

cfg.enable_gating=False → 不门控, 全部 Player 都调研 (调试 / 历史回填用).

Observation 用扁平 concernId 单键 (v2). previousStatus 从同 provider+model 的
latest 透传, 跨模型不混.

文件按 provider+model 隔离:
  ~/.market_data/thesis/observations.openai-gpt-4o.latest.json
  ~/.market_data/thesis/observations.openai-gpt-4o.<date>.json
  ~/.market_data/thesis/observations.anthropic-claude-sonnet-4-5.latest.json
  ...
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
    Player,
    ResearchConfig,
    Segment,
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
    """v2: 扁平 concernId 索引 (无 companyId)."""
    if not bundle:
        return {}
    out: Dict[str, ConcernObservation] = {}
    for o in bundle.get("observations", []):
        cid = o.get("concernId")
        if cid:
            out[cid] = o
    return out


def _select_segments(
    knowledge: ThesisKnowledge, cfg: ResearchConfig
) -> List[Segment]:
    segs = knowledge["segments"]
    if cfg.only_segment_ids:
        keep = set(cfg.only_segment_ids)
        segs = [s for s in segs if s.get("id") in keep]
    if cfg.only_track_ids:
        keep = set(cfg.only_track_ids)
        segs = [s for s in segs if s.get("track") in keep]
    if cfg.only_company_ids:
        # 保留含任意指定 companyId 的 segment (segment 内的过滤在阶段二)
        keep = set(cfg.only_company_ids)
        segs = [
            s for s in segs
            if any(
                p.get("companyId") in keep and not p.get("referenceOnly")
                for p in s.get("players", [])
            )
        ]
    return segs


def _track_lookup(knowledge: ThesisKnowledge) -> Dict[str, ThesisTrack]:
    return {t["id"]: t for t in knowledge["tracks"]}


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def _build_bundle(
    observations: List[ConcernObservation],
    gated_segment_ids: List[str],
    cfg: ResearchConfig,
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
        "gatedSegmentIds": gated_segment_ids,
        "observations": observations,
    }


def _prev_status(
    previous_idx: Dict[str, ConcernObservation], concern_id: str
) -> Optional[HealthStatus]:
    prev_obs = previous_idx.get(concern_id)
    if not prev_obs:
        return None
    prev_status = prev_obs.get("status")
    if prev_status in ("bullish", "neutral", "bearish", "unknown"):
        return prev_status  # type: ignore[return-value]
    return None


def _research_one(
    segment: Segment,
    track: Optional[ThesisTrack],
    concern: dict,
    player: Optional[Player],
    previous_idx: Dict[str, ConcernObservation],
    cfg: ResearchConfig,
    client: object,
) -> tuple[ConcernObservation, bool]:
    """跑一个 concern, 返回 (validated_observation, was_coerced)."""
    prev = _prev_status(previous_idx, concern["id"])
    scope_label = (
        f"{segment.get('id')}/{concern['id']}"
        if player is None
        else f"{segment.get('id')}::{player.get('companyId')}/{concern['id']}"
    )
    logger.info("  researching %s ...", scope_label)
    raw = research_concern(segment, track, concern, player, prev, cfg, client)
    return validate_observation(raw)


def run_research(cfg: ResearchConfig) -> ObservationsBundle:
    """主入口. 返回组装好的 ObservationsBundle (dry_run 也返回, 便于自检)."""
    knowledge = load_knowledge(cfg)
    out_dir = resolve_output_dir(cfg)

    previous_bundle = _load_previous_bundle(out_dir, cfg)
    previous_idx = _index_previous(previous_bundle)
    tracks_by_id = _track_lookup(knowledge)
    segments = _select_segments(knowledge, cfg)

    logger.info(
        "thesis research v2: provider=%s model=%s; %d segments selected; "
        "gating=%s; dry_run=%s",
        cfg.provider, cfg.model, len(segments), cfg.enable_gating, cfg.dry_run,
    )

    client = None if cfg.dry_run else make_client(cfg.provider)

    fresh_observations: List[ConcernObservation] = []
    gated_segment_ids: List[str] = []
    covered_concern_ids: set[str] = set()
    coerced = 0

    # ── 阶段一: 环节级 concerns → 算 segment health ──────────────────
    segment_health: Dict[str, HealthStatus] = {}
    for segment in segments:
        track = tracks_by_id.get(segment.get("track", ""))
        env_concerns = segment.get("concerns", []) or []
        env_obs: List[ConcernObservation] = []
        for concern in env_concerns:
            if cfg.only_concern_ids and concern["id"] not in cfg.only_concern_ids:
                continue
            covered_concern_ids.add(concern["id"])
            validated, was_coerced = _research_one(
                segment, track, concern, None, previous_idx, cfg, client,
            )
            if was_coerced:
                coerced += 1
                logger.warning("    coerced to unknown: %s", validated.get("detail", ""))
            env_obs.append(validated)
            fresh_observations.append(validated)
        segment_health[segment["id"]] = _rollup_health(env_obs)

    # ── 阶段二: Player concerns (门控后) ──────────────────────────────
    for segment in segments:
        seg_health = segment_health.get(segment["id"], "unknown")
        if cfg.enable_gating and seg_health == "bearish":
            gated_segment_ids.append(segment["id"])
            logger.info(
                "  gated %s (health=bearish), skipping %d players",
                segment["id"],
                sum(1 for p in segment.get("players", []) if not p.get("referenceOnly")),
            )
            continue
        track = tracks_by_id.get(segment.get("track", ""))
        for player in segment.get("players", []) or []:
            if player.get("referenceOnly"):
                continue
            if cfg.only_company_ids and player.get("companyId") not in cfg.only_company_ids:
                continue
            for concern in player.get("concerns", []) or []:
                if cfg.only_concern_ids and concern["id"] not in cfg.only_concern_ids:
                    continue
                covered_concern_ids.add(concern["id"])
                validated, was_coerced = _research_one(
                    segment, track, concern, player, previous_idx, cfg, client,
                )
                if was_coerced:
                    coerced += 1
                    logger.warning(
                        "    coerced to unknown: %s", validated.get("detail", ""),
                    )
                fresh_observations.append(validated)

    # ── keep-previous: 范围外的 observation 沿用上轮 ──────────────────
    if cfg.keep_previous_unchanged and previous_bundle:
        carried = 0
        for cid, prev_obs in previous_idx.items():
            if cid in covered_concern_ids:
                continue
            fresh_observations.append(prev_obs)
            carried += 1
        if carried:
            logger.info("carried %d unchanged observations from previous bundle", carried)

    bundle = _build_bundle(fresh_observations, gated_segment_ids, cfg)
    logger.info(
        "done: %d observations, %d gated segments (%d coerced to unknown)",
        len(fresh_observations), len(gated_segment_ids), coerced,
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


def _rollup_health(observations: List[ConcernObservation]) -> HealthStatus:
    """跟 TS rollupHealth 同算法: 任一 bearish → bearish; ..."""
    has_neutral = False
    has_bullish = False
    known_count = 0
    for o in observations:
        status = o.get("status")
        if status == "bearish":
            return "bearish"
        if status == "neutral":
            has_neutral = True
        if status == "bullish":
            has_bullish = True
        if status != "unknown":
            known_count += 1
    if known_count == 0:
        return "unknown"
    if has_neutral:
        return "neutral"
    if has_bullish:
        return "bullish"
    return "unknown"
