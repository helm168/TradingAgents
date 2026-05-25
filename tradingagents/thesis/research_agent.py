"""单 concern 调研逻辑 — Claude + native web_search tool.

为什么直接用 raw `anthropic` SDK 而不是 langchain ChatAnthropic:
  langchain-anthropic 对 Anthropic 的 native web_search tool 支持要么版本依赖,
  要么调用面绕一圈. raw SDK 是官方一手, JSON 形状最清楚, 单 concern 一次调用,
  不需要 langchain 的链式 abstraction. 项目里的 trading-debate 走 langchain
  完全不冲突 — 两条独立通路.

prompt 设计 (PRD §8.2 幻觉防线):
  - 明确告诉 Claude: 没找到可靠源 → 必须 status=unknown.
  - 非 unknown 强制 evidence (含 url + quote + publishedAt).
  - 只返回 JSON, 不要 markdown fence / 评论.
  - 措辞统一用"景气/转弱/留意", 不出现"买入/卖出/目标价" (PRD §6.5).
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Optional

import anthropic

from .types import (
    ConcernDefinition,
    ConcernObservation,
    HealthStatus,
    ResearchConfig,
    ThesisCard,
    ThesisTrack,
)

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """你是一名行业研究分析师, 给一名持仓投资者做"投资逻辑跟踪".

你的任务: 针对一只股票的**一个**核心关切点 (行业运营指标), 联网拉最新数据,
对照判断标准给出当前景气度判级 (bullish / neutral / bearish / unknown),
**必须附可点击的证据来源**.

规则 (硬约束):
1. **没有可靠源 → unknown**. 找不到最近 60 天内的权威披露 / 报告 / 财报口径,
   一律 status="unknown", 不要硬猜.
2. **非 unknown 必须带 evidence**. 每条 evidence 必须有: source 名 / 真实 url
   (http 或 https 开头) / 来源原文片段 quote / 发布日期 publishedAt
   (YYYY-MM-DD).
3. **不编**. 数字 / URL / 引文不许编造. 不确定就 unknown.
4. **措辞中性**. 用"景气/转弱/留意/值得关注", 不出现"买入/卖出/加仓/减仓/目标价".
5. **只返回 JSON**, 不要 markdown fence (```json), 不要任何前后缀文字, 不要
   reasoning.

返回 JSON 形状:

{
  "status": "bullish" | "neutral" | "bearish" | "unknown",
  "trend": "up" | "flat" | "down" | "unknown",
  "headline": "<中文一句话标题, ≤ 50 字>",
  "detail": "<中文 2-3 句解释判级理由>",
  "metrics": { "<key>": "<value>" },
  "evidence": [
    {
      "source": "<来源名>",
      "url": "<完整 URL>",
      "quote": "<原文片段>",
      "publishedAt": "YYYY-MM-DD"
    }
  ],
  "confidence": "high" | "medium" | "low"
}

confidence 自评: 来源权威且口径吻合 = high; 来源间接 / 口径存疑 = medium;
来源不太靠谱 = low (但仍带证据).
"""


def _build_user_prompt(
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
    return f"""# 公司
{card['displayName']} ({card['companyId']})

# 投资逻辑 (为什么持有)
赛道: {track_label}
环节: {thesis.get('node', '')}
一句话: {thesis.get('summary', '')}

# 本次研究的关切点
名称: {concern['label']}
为什么是命门: {concern['why']}

# 判级标准 (严格按这个来)
- bullish: {rubric['bullish']}
- neutral: {rubric['neutral']}
- bearish: {rubric['bearish']}

# 检索指引
查询关键词: {hint['query']}
优先采信源 (按顺序): {', '.join(hint['preferredSources'])}
期望数据形态: {hint['expectedShape']}

# 上下文
{prev_line}
今天日期: {date.today().isoformat()}

请用 web_search 工具检索最新数据, 然后输出 JSON.
"""


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}\s*$")


def _extract_text(message: anthropic.types.Message) -> str:
    """从 Anthropic 响应里抽 text block 内容 (跳过 tool_use / tool_result)."""
    parts = []
    for block in message.content:
        if getattr(block, "type", None) == "text":
            parts.append(block.text)
    return "\n".join(parts).strip()


def _parse_json_response(text: str) -> dict:
    """LLM 应该只返回 JSON, 但实测偶尔有前后缀解释 — 抠出最后一个 JSON 对象."""
    text = text.strip()
    # Strip markdown fence if present
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    # 直接尝试
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 抠最后一个 {...}
    m = _JSON_BLOCK_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM response 不是合法 JSON: {e}; raw={text[:500]!r}")
    raise ValueError(f"LLM response 无 JSON 对象: raw={text[:500]!r}")


def research_concern(
    card: ThesisCard,
    track: Optional[ThesisTrack],
    concern: ConcernDefinition,
    previous_status: Optional[HealthStatus],
    cfg: ResearchConfig,
    client: Optional[anthropic.Anthropic] = None,
) -> ConcernObservation:
    """单次调研: card × concern → ConcernObservation (未 validate).

    runner 拿到后再过 validators.validate_observation 强制 hallucination 防线.
    """
    user_prompt = _build_user_prompt(card, track, concern, previous_status)

    obs_skeleton: ConcernObservation = {
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
        obs_skeleton["previousStatus"] = previous_status

    if cfg.dry_run:
        logger.info(
            "[dry-run] %s / %s — prompt %d chars (skipping LLM call)",
            card["companyId"], concern["id"], len(user_prompt),
        )
        obs_skeleton["detail"] = "[dry-run] LLM not called"
        return obs_skeleton

    client = client or anthropic.Anthropic()
    try:
        message = client.messages.create(
            model=cfg.model,
            max_tokens=cfg.max_tokens,
            system=SYSTEM_PROMPT,
            tools=[
                {
                    "type": "web_search_20250305",
                    "name": "web_search",
                    "max_uses": cfg.max_web_search_uses,
                }
            ],
            messages=[{"role": "user", "content": user_prompt}],
        )
    except Exception as e:
        logger.warning(
            "LLM call failed for %s/%s: %s", card["companyId"], concern["id"], e
        )
        obs_skeleton["detail"] = f"[降级为 unknown] LLM 调用失败: {e}"
        return obs_skeleton

    text = _extract_text(message)
    try:
        parsed = _parse_json_response(text)
    except ValueError as e:
        logger.warning(
            "Parse JSON failed for %s/%s: %s", card["companyId"], concern["id"], e
        )
        obs_skeleton["detail"] = f"[降级为 unknown] LLM 返回非 JSON: {e}"
        return obs_skeleton

    # Merge parsed fields into skeleton, keeping companyId/concernId/researchedAt
    out: ConcernObservation = dict(obs_skeleton)  # type: ignore[assignment]
    for key in (
        "status", "trend", "headline", "detail", "metrics", "evidence", "confidence",
    ):
        if key in parsed:
            out[key] = parsed[key]  # type: ignore[literal-required]
    return out
