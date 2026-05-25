"""幻觉防线 — PRD §8.2.

LLM 联网调研最大的风险是"编一个看起来合理的数字". 这里把对 observation 的
约束做成纯函数, runner 调一遍 validate_observation, 不合规直接强制降级为
unknown 而不是给用户看一个不可信的判级.

约束:
  1. status ∈ {bullish, neutral, bearish} 时 evidence 必须非空, 每条至少有
     非空 url + source.
  2. evidence url 必须是 http(s):// 开头 (排除 LLM 编的相对路径 / 占位).
  3. status / trend / confidence 在合法枚举内.
  4. 字段缺失或类型错 → unknown 降级.

不做的事:
  - 不验真 URL 可访问 (HTTP HEAD 烧时间且会被 anti-bot ban).
  - 不查重原文 (PRD 显式说"让用户能一键核对", 不是 hash check).
"""
from __future__ import annotations

from typing import Tuple
from urllib.parse import urlparse

from .types import ConcernObservation, HealthStatus


_VALID_STATUS: tuple[HealthStatus, ...] = ("bullish", "neutral", "bearish", "unknown")
_VALID_TREND = ("up", "flat", "down", "unknown")
_VALID_CONFIDENCE = ("high", "medium", "low")


def _coerce_unknown(obs: ConcernObservation, reason: str) -> ConcernObservation:
    """把 observation 改成 unknown 形态 + 把 reason 塞到 detail 里, 让用户
    一眼能看出"为啥是 unknown"."""
    return {
        "companyId": obs.get("companyId", ""),
        "concernId": obs.get("concernId", ""),
        "status": "unknown",
        "trend": "unknown",
        "headline": obs.get("headline", "") or "调研失败",
        "detail": f"[降级为 unknown] {reason}",
        "metrics": {},
        "evidence": obs.get("evidence", []) or [],
        "confidence": "low",
        "previousStatus": obs.get("previousStatus"),  # type: ignore[typeddict-item]
        "researchedAt": obs.get("researchedAt", ""),
    }


def _is_real_url(s: str) -> bool:
    if not isinstance(s, str) or not s:
        return False
    try:
        u = urlparse(s)
    except Exception:
        return False
    return u.scheme in ("http", "https") and bool(u.netloc)


def validate_observation(obs: ConcernObservation) -> Tuple[ConcernObservation, bool]:
    """检查并必要时降级 observation.

    返回 (validated_obs, was_coerced). was_coerced=True 表示触发了 unknown
    降级 — runner 会打个 warning log, 但产物里看不到 (用户视角等于 unknown).
    """
    status = obs.get("status")
    if status not in _VALID_STATUS:
        return _coerce_unknown(obs, f"status 非法: {status!r}"), True

    trend = obs.get("trend")
    if trend not in _VALID_TREND:
        # trend 错不致命, 拉回 unknown 不降级 status
        obs = dict(obs)  # type: ignore[assignment]
        obs["trend"] = "unknown"

    conf = obs.get("confidence")
    if conf not in _VALID_CONFIDENCE:
        obs = dict(obs)  # type: ignore[assignment]
        obs["confidence"] = "low"

    if status == "unknown":
        return obs, False  # unknown 不要求 evidence

    # 非 unknown — 强制证据
    ev = obs.get("evidence") or []
    if not ev:
        return _coerce_unknown(obs, "状态 non-unknown 但无 evidence"), True

    real = [e for e in ev if isinstance(e, dict) and _is_real_url(e.get("url", ""))]
    if not real:
        return _coerce_unknown(obs, "evidence URL 全部非 http(s) / 缺失"), True

    # 至少有一条真 URL → 通过, 但把假 URL 的条目刷掉
    if len(real) < len(ev):
        obs = dict(obs)  # type: ignore[assignment]
        obs["evidence"] = real

    return obs, False
