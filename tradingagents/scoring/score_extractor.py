"""
从 TradingAgents 生成的分析报告里抽取数值化评分。

设计思路：
1. 不修改原有 agent prompt，作为独立的后处理层
2. 用一次便宜的 LLM 调用（quick_think_llm）抽取分数，避免重新跑 agent
3. 技术面 / 基本面 / 情绪 / 新闻 **独立打分**，不混合
4. 输出结构化 JSON，方便排序和象限分析

象限分析（推荐用法）：
  technical 高 + fundamental 高  → "趋势确认 + 价值支撑"，可加仓
  technical 低 + fundamental 高  → "价值等待催化"，分批左侧建仓
  technical 高 + fundamental 低  → "纯动量博弈"，注意止损
  technical 低 + fundamental 低  → "回避"
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional


# ---------- 打分标准 ----------
SCORING_RUBRIC = {
    "technical": (
        "评估维度: "
        "(1) 趋势强度（价格 vs 50/200日均线、10/20日均线方向） "
        "(2) 动量（RSI、MACD 状态、是否过热/超卖） "
        "(3) 成交量确认（VWMA、量价配合） "
        "(4) 波动率与风险（ATR、布林带位置） "
        "(5) 关键支撑/压力位与当前价的相对位置"
    ),
    "fundamental": (
        "评估维度: "
        "(1) 盈利能力（毛利率、净利率、ROE） "
        "(2) 成长性（营收/利润 YoY、自由现金流增长） "
        "(3) 资产负债表健康度（资产负债率、现金储备） "
        "(4) 现金流质量（经营现金流 vs 净利润） "
        "(5) 估值水平（PE、PB、PEG、相对行业水平）"
    ),
    "sentiment": (
        "评估维度: "
        "(1) 公众情绪整体倾向 "
        "(2) 关键正负事件 "
        "(3) 机构 vs 散户分歧 "
        "(4) 短期市场关注度"
    ),
    "news": (
        "评估维度: "
        "(1) 利好 vs 利空消息平衡 "
        "(2) 是否有重大催化剂 "
        "(3) 宏观/政策影响方向 "
        "(4) 行业景气度变化"
    ),
}


SCORE_PROMPT_TEMPLATE = """你是一位资深金融分析师，需要把一份分析报告打成数值化分数。

**报告类型**: {report_type}
**评估标准**: {rubric}

**报告原文**:
---
{report_content}
---

请输出严格的 JSON（不要 markdown 包裹、不要解释文字）：
{{
  "score": <0-100 整数>,
  "stance": "<bullish/neutral/bearish>",
  "confidence": "<high/medium/low>",
  "summary": "<不超过 30 字的一句话总结>",
  "key_strengths": ["<要点1>", "<要点2>"],
  "key_risks": ["<风险1>", "<风险2>"]
}}

打分参考：
- 0-20: 非常负面，强烈回避
- 21-40: 负面，偏空
- 41-60: 中性
- 61-80: 正面，偏多
- 81-100: 非常正面，强烈看好
"""


@dataclass
class ScoreResult:
    technical: Optional[dict] = None
    fundamental: Optional[dict] = None
    sentiment: Optional[dict] = None
    news: Optional[dict] = None
    final_rating: Optional[str] = None  # Buy/Overweight/Hold/Underweight/Sell（从 decision.md 提取）
    # F-Score 是公式化 0-9 客观分, 用来对比 LLM 主观给的 fundamental_score
    # (LLM 在 60-90 之间飘的时候, F-Score 是固定锚, 能看出 LLM 是真的看好
    # 还是手松).
    fscore: Optional[dict] = None
    # Q-Score 是绝对质量分 0-100, 跟 F-Score 互补:
    #   F-Score 看 "改善 trend" (今年 ROA > 去年?)
    #   Q-Score 看 "绝对水平" (净利率几个? 营收 CAGR 几个?)
    # 茅台 F-Score 可能 7/9 + Q-Score 90; 烂周期股 F-Score 也可能 7/9 + Q-Score 30.
    qscore: Optional[dict] = None
    errors: list = field(default_factory=list)

    @property
    def technical_score(self) -> Optional[int]:
        return self.technical.get("score") if self.technical else None

    @property
    def fundamental_score(self) -> Optional[int]:
        return self.fundamental.get("score") if self.fundamental else None

    @property
    def fscore_value(self) -> Optional[int]:
        return self.fscore.get("score") if self.fscore else None

    @property
    def qscore_value(self) -> Optional[float]:
        return self.qscore.get("score") if self.qscore else None

    @property
    def quadrant(self) -> str:
        """象限分析：根据 technical 和 fundamental 得分分类。"""
        t, f = self.technical_score, self.fundamental_score
        if t is None or f is None:
            return "未知"
        t_high = t >= 60
        f_high = f >= 60
        if t_high and f_high:
            return "趋势+价值（最优）"
        if not t_high and f_high:
            return "价值等待催化（左侧建仓）"
        if t_high and not f_high:
            return "纯动量博弈（注意止损）"
        return "回避"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["technical_score"] = self.technical_score
        d["fundamental_score"] = self.fundamental_score
        d["fscore_value"] = self.fscore_value
        d["qscore_value"] = self.qscore_value
        d["quadrant"] = self.quadrant
        return d


# ---------- LLM 调用 ----------
def _call_llm_for_score(llm, prompt: str) -> dict:
    """调 LLM，解析 JSON。失败时尝试用 regex 抠出第一段 JSON。"""
    resp = llm.invoke(prompt)
    text = resp.content if hasattr(resp, "content") else str(resp)
    # 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 容错：抠第一段 { ... }
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            pass
    raise ValueError(f"LLM 返回不是合法 JSON: {text[:200]}")


def _score_one_report(llm, report_path: Path, report_type: str) -> dict:
    """打分单个报告。"""
    content = report_path.read_text(encoding="utf-8")
    # 报告太长会爆 token，限制 8000 字符（约 4000-5000 tokens 中文）
    if len(content) > 8000:
        content = content[:4000] + "\n...（中间省略）...\n" + content[-4000:]
    prompt = SCORE_PROMPT_TEMPLATE.format(
        report_type=report_type,
        rubric=SCORING_RUBRIC[report_type],
        report_content=content,
    )
    return _call_llm_for_score(llm, prompt)


# ---------- 提取 Portfolio Manager 的最终评级 ----------
def _extract_final_rating(report_dir: Path) -> Optional[str]:
    """从 5_portfolio/decision.md 抠出 Buy/Overweight/Hold/Underweight/Sell。"""
    decision_file = report_dir / "5_portfolio" / "decision.md"
    if not decision_file.exists():
        return None
    text = decision_file.read_text(encoding="utf-8")
    # 匹配 "Rating: <X>" 或 "**Rating**: <X>" 等格式
    m = re.search(r"\*?\*?Rating\*?\*?\s*[:：]\s*\*?\*?(Buy|Overweight|Hold|Underweight|Sell)",
                  text, re.IGNORECASE)
    if m:
        return m.group(1).title()
    return None


# ---------- 主入口 ----------
def score_reports(llm, report_dir: str | Path, ticker: Optional[str] = None) -> ScoreResult:
    """
    对一个 ticker 的报告目录（reports/<TICKER>_<时间戳>/）进行打分。

    Args:
        llm: 一个已实例化的 LangChain LLM client，需要支持 .invoke(prompt)
        report_dir: 报告目录路径
        ticker:  可选, 给定就额外算 Piotroski F-Score (公式化 0-9 客观分).
                 不传则从 report_dir 名字 "<TICKER>_<ts>/" 反推.

    Returns:
        ScoreResult，包含技术/基本面/情绪/新闻 LLM 打分 + F-Score 客观分.
    """
    report_dir = Path(report_dir)
    if not report_dir.exists():
        raise FileNotFoundError(f"报告目录不存在: {report_dir}")

    analysts_dir = report_dir / "1_analysts"
    file_map = {
        "technical": analysts_dir / "market.md",
        "fundamental": analysts_dir / "fundamentals.md",
        "sentiment": analysts_dir / "sentiment.md",
        "news": analysts_dir / "news.md",
    }

    result = ScoreResult()
    for kind, fp in file_map.items():
        if not fp.exists():
            result.errors.append(f"{kind}: 文件不存在 {fp}")
            continue
        try:
            score = _score_one_report(llm, fp, kind)
            setattr(result, kind, score)
        except Exception as e:
            result.errors.append(f"{kind}: {type(e).__name__}: {e}")

    result.final_rating = _extract_final_rating(report_dir)

    # F-Score: 公式化, 不调 LLM. ticker 未传就从 "reports/<TICKER>_<ts>/" 反推.
    if ticker is None:
        # report_dir.name 形如 "600519.SS_20260514_213000"
        ticker = report_dir.name.split("_")[0] if "_" in report_dir.name else None
    if ticker:
        # F-Score (改善 trend, 0-9)
        try:
            from .fscore import compute_fscore
            fs = compute_fscore(ticker)
            if fs is not None:
                result.fscore = fs.to_dict()
        except Exception as e:
            result.errors.append(f"fscore: {type(e).__name__}: {e}")
        # Q-Score (绝对质量, 0-100)
        try:
            from .qscore import compute_qscore
            qs = compute_qscore(ticker)
            if qs is not None:
                result.qscore = qs.to_dict()
        except Exception as e:
            result.errors.append(f"qscore: {type(e).__name__}: {e}")

    return result
