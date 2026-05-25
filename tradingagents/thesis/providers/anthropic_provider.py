"""Anthropic Claude 调研 adapter — 用 raw anthropic SDK + native web_search."""
from __future__ import annotations

import logging
from typing import Optional

import anthropic

from ..types import (
    ConcernDefinition,
    ConcernObservation,
    HealthStatus,
    ResearchConfig,
    ThesisCard,
    ThesisTrack,
)
from ._common import (
    SYSTEM_PROMPT,
    build_user_prompt,
    empty_observation,
    merge_parsed_into_skeleton,
    parse_json_response,
)

logger = logging.getLogger(__name__)


def _extract_text(message: anthropic.types.Message) -> str:
    """从 Anthropic 响应里抽 text block (跳过 tool_use / tool_result)."""
    parts = []
    for block in message.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def research_concern(
    card: ThesisCard,
    track: Optional[ThesisTrack],
    concern: ConcernDefinition,
    previous_status: Optional[HealthStatus],
    cfg: ResearchConfig,
    client: Optional[anthropic.Anthropic] = None,
) -> ConcernObservation:
    user_prompt = build_user_prompt(card, track, concern, previous_status)
    obs = empty_observation(card, concern, previous_status)

    if cfg.dry_run:
        logger.info(
            "[dry-run anthropic] %s/%s — %d chars",
            card["companyId"], concern["id"], len(user_prompt),
        )
        obs["detail"] = "[dry-run] LLM not called"
        return obs

    client = client or anthropic.Anthropic()
    try:
        message = client.messages.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=SYSTEM_PROMPT,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": cfg.max_web_search_uses,
            }],
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        logger.warning("anthropic call failed %s/%s: %s",
                       card["companyId"], concern["id"], e)
        obs["detail"] = f"[降级为 unknown] LLM 调用失败: {e}"
        return obs

    text = _extract_text(message)
    try:
        parsed = parse_json_response(text)
    except ValueError as e:
        logger.warning("anthropic JSON parse failed %s/%s: %s",
                       card["companyId"], concern["id"], e)
        obs["detail"] = f"[降级为 unknown] LLM 返回非 JSON: {e}"
        return obs

    return merge_parsed_into_skeleton(obs, parsed)
