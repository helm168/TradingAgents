"""OpenAI 调研 adapter — Responses API + web_search tool.

OpenAI Responses API (since 2024-08) 内置 web_search tool, 跟 Anthropic
web_search_20250305 等价能力. 用法:

    client.responses.create(
        model="gpt-4o",
        instructions=SYSTEM_PROMPT,
        input=user_prompt,
        tools=[{"type": "web_search"}],
        max_tool_calls=5,
        text={"format": {"type": "json_object"}},
    )

response.output 是 list, 包含 web_search_call 项和 message 项. 文字内容在
message.content[i].text. 新版 SDK 有 response.output_text 直接拼好的便捷字段.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

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


def _extract_text(response: Any) -> str:
    """从 OpenAI Responses API 响应抽 text."""
    # 1. 优先用 SDK 提供的便捷属性
    out_text = getattr(response, "output_text", None)
    if isinstance(out_text, str) and out_text.strip():
        return out_text.strip()

    # 2. 手动遍历 output items
    parts = []
    for item in getattr(response, "output", []) or []:
        # 只看 message item 的 content
        item_type = getattr(item, "type", None)
        if item_type != "message":
            continue
        content = getattr(item, "content", []) or []
        for c in content:
            ctype = getattr(c, "type", None)
            if ctype in ("output_text", "text"):
                txt = getattr(c, "text", None)
                if isinstance(txt, str):
                    parts.append(txt)
    return "\n".join(parts).strip()


def research_concern(
    card: ThesisCard,
    track: Optional[ThesisTrack],
    concern: ConcernDefinition,
    previous_status: Optional[HealthStatus],
    cfg: ResearchConfig,
    client: Any = None,
) -> ConcernObservation:
    user_prompt = build_user_prompt(card, track, concern, previous_status)
    obs = empty_observation(card, concern, previous_status)

    if cfg.dry_run:
        logger.info(
            "[dry-run openai] %s/%s — %d chars",
            card["companyId"], concern["id"], len(user_prompt),
        )
        obs["detail"] = "[dry-run] LLM not called"
        return obs

    if client is None:
        from openai import OpenAI
        client = OpenAI()

    try:
        response = client.responses.create(
            model=cfg.model,
            instructions=SYSTEM_PROMPT,
            input=user_prompt,
            tools=[{"type": "web_search"}],
            max_tool_calls=cfg.max_web_search_uses,
            max_output_tokens=cfg.max_tokens,
            # 强制 JSON 输出 — 比 Anthropic 这边的"靠 prompt 约束"更稳
            text={"format": {"type": "json_object"}},
        )
    except Exception as e:
        logger.warning("openai call failed %s/%s: %s",
                       card["companyId"], concern["id"], e)
        obs["detail"] = f"[降级为 unknown] LLM 调用失败: {e}"
        return obs

    text = _extract_text(response)
    if not text:
        obs["detail"] = "[降级为 unknown] OpenAI Responses 返回空"
        return obs

    try:
        parsed = parse_json_response(text)
    except ValueError as e:
        logger.warning("openai JSON parse failed %s/%s: %s",
                       card["companyId"], concern["id"], e)
        obs["detail"] = f"[降级为 unknown] LLM 返回非 JSON: {e}"
        return obs

    return merge_parsed_into_skeleton(obs, parsed)
