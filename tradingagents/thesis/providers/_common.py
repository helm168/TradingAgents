"""Provider 共享: prompt / JSON 解析 / observation skeleton."""
from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Optional

from ..types import (
    ConcernDefinition,
    ConcernObservation,
    HealthStatus,
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
5. **只返回 JSON**, 不要 markdown fence (```json), 不要任何前后缀文字.

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


def parse_json_response(text: str) -> dict:
    """LLM 应该只返回 JSON, 但实测偶尔有前后缀解释 — 抠出最后一个 JSON 对象."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = _JSON_BLOCK_RE.search(text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError as e:
            raise ValueError(f"LLM response 不是合法 JSON: {e}; raw={text[:500]!r}")
    raise ValueError(f"LLM response 无 JSON 对象: raw={text[:500]!r}")


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
