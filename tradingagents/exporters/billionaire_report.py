"""把 TradingAgents 报告导出成 Billionaire 详情页能直接消费的 JSON.

输出
────
路径: ~/.market_data/agent_reports/<ts_code>.json
        (BILLIONAIRE_AGENT_REPORTS_DIR 环境变量可覆盖)

ts_code 约定
────────────
跟 sh_quant 对齐 (DATA_SCHEMA.md):
    AAPL       → AAPL.US
    NVDA       → NVDA.US
    600519.SS  → 600519.SH         (yfinance .SS → tushare .SH)
    000001.SZ  → 000001.SZ         (深市同)
    300750.SZ  → 300750.SZ         (创业板同)
    0700.HK    → 00700.HK          (港股 4 位补成 5 位)
    0981.HK    → 00981.HK

JSON Schema
───────────
{
  "ts_code":        "600519.SH",
  "ticker":         "600519",
  "name":           null,           # 暂未带, Billionaire 自己有 universe
  "market":         "CN" | "US" | "HK",
  "generated_at":   "2026-05-13T10:00:00Z",
  "analysis_date":  "2026-05-13",
  "model": {
    "quick":  "deepseek-chat",
    "deep":   "deepseek-reasoner"
  },
  "verdict": {
    "action":     "BUY" | "HOLD" | "SELL" | "UNKNOWN",
    "confidence": 0.0..1.0,
    "raw":        "<原始 portfolio manager decision 文本>"
  },
  "scores": {
    "technical":    8.0,        # 0-10
    "fundamental":  7.5,
    "sentiment":    6.5,
    "news":         7.0,
    "quadrant":     "趋势确认 + 价值支撑",
    "final_rating": "强烈买入"
  },
  "agents": {
    "fundamentals":     "markdown text…",
    "technical":        "…",   # 来自 market.md
    "sentiment":        "…",
    "news":             "…",
    "research_bull":    "…",
    "research_bear":    "…",
    "research_manager": "…",
    "trader":           "…",
    "risk_aggressive":  "…",
    "risk_conservative":"…",
    "risk_neutral":     "…",
    "portfolio":        "…"
  }
}
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ─── 输出根目录 ────────────────────────────────────────────────────────
# 默认跟 sh_quant 共享 ~/.market_data; BILLIONAIRE_AGENT_REPORTS_DIR 覆盖.
def _default_agent_reports_dir() -> Path:
    override = os.environ.get("BILLIONAIRE_AGENT_REPORTS_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".market_data" / "agent_reports"


AGENT_REPORTS_DIR = _default_agent_reports_dir()


# ─── ticker → ts_code ─────────────────────────────────────────────────
_HK_RE = re.compile(r"^(\d+)\.HK$", re.IGNORECASE)


def normalize_ts_code(ticker: str) -> str:
    """ticker (TradingAgents 内部约定) → ts_code (sh_quant/Billionaire 约定)."""
    t = ticker.strip().upper()

    # 港股: 0700.HK / 0981.HK → 00700.HK / 00981.HK (5 位)
    hk = _HK_RE.match(t)
    if hk:
        return f"{hk.group(1).zfill(5)}.HK"

    # A 股: yfinance 的 .SS / .SZ → tushare 的 .SH / .SZ
    if t.endswith(".SS"):
        return f"{t[:-3]}.SH"
    if t.endswith(".SZ") or t.endswith(".SH") or t.endswith(".BJ"):
        return t

    # 已经是 sh_quant 风格 (XXX.US) 就保留
    if t.endswith(".US"):
        return t

    # 美股纯字母: AAPL → AAPL.US
    if re.match(r"^[A-Z][A-Z0-9.\-]{0,11}$", t):
        return f"{t}.US"

    # 实在认不出来就原样回, 调用方自己 handle
    return t


def market_of_ts_code(ts_code: str) -> str:
    if ts_code.endswith((".SH", ".SZ", ".BJ")):
        return "CN"
    if ts_code.endswith(".HK"):
        return "HK"
    if ts_code.endswith(".US"):
        return "US"
    return "OTHER"


# ─── 文件读取辅助 ──────────────────────────────────────────────────────
def _read_md(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8").strip()


def _collect_agents(report_dir: Path) -> dict[str, str]:
    """按目录结构把 markdown 文件读出来归到 agents dict."""
    a = report_dir / "1_analysts"
    r = report_dir / "2_research"
    t = report_dir / "3_trading"
    risk = report_dir / "4_risk"
    p = report_dir / "5_portfolio"

    agents: dict[str, str] = {
        "fundamentals":      _read_md(a / "fundamentals.md"),
        "technical":         _read_md(a / "market.md"),  # market.md = 技术面分析师
        "sentiment":         _read_md(a / "sentiment.md"),
        "news":              _read_md(a / "news.md"),
        "research_bull":     _read_md(r / "bull.md"),
        "research_bear":     _read_md(r / "bear.md"),
        "research_manager":  _read_md(r / "manager.md"),
        "risk_aggressive":   _read_md(risk / "aggressive.md"),
        "risk_conservative": _read_md(risk / "conservative.md"),
        "risk_neutral":      _read_md(risk / "neutral.md"),
    }
    # trader / portfolio 子目录里文件名可能不固定, 把所有 .md concat
    agents["trader"] = "\n\n".join(
        _read_md(p) for p in sorted(t.glob("*.md"))
    ) if t.exists() else ""
    agents["portfolio"] = "\n\n".join(
        _read_md(p) for p in sorted(p.glob("*.md"))
    ) if p.exists() else ""
    return agents


# ─── verdict 提取 ─────────────────────────────────────────────────────
# 优先级: 强 BUY/SELL > Overweight/Underweight > 中文 > Neutral/HOLD
# Overweight / 加仓 = BUY 的同义词 (实际 portfolio manager 写出来的就是这俩)
_ACTION_PATTERNS = [
    (re.compile(r"\b(STRONG\s+BUY|STRONGLY\s+BUY)\b", re.I), "BUY"),
    (re.compile(r"\b(STRONG\s+SELL|STRONGLY\s+SELL)\b", re.I), "SELL"),
    (re.compile(r"\bOVERWEIGHT\b", re.I), "BUY"),
    (re.compile(r"\bUNDERWEIGHT\b", re.I), "SELL"),
    (re.compile(r"\bBUY\b", re.I), "BUY"),
    (re.compile(r"\bSELL\b", re.I), "SELL"),
    (re.compile(r"\b(EQUAL\s*WEIGHT|MARKET\s*WEIGHT|NEUTRAL|HOLD)\b", re.I), "HOLD"),
    # 中文
    (re.compile(r"强烈\s*买入|强烈\s*推荐"), "BUY"),
    (re.compile(r"强烈\s*卖出"), "SELL"),
    (re.compile(r"加仓|增持|买入"), "BUY"),
    (re.compile(r"减仓|减持|卖出"), "SELL"),
    (re.compile(r"持有|观望|中性"), "HOLD"),
]

# 取强度更进一步: STRONG / Overweight / 强烈 都给高 confidence
_STRONG_RE = re.compile(r"STRONG|OVERWEIGHT|UNDERWEIGHT|强烈|加仓|增持|减仓", re.I)


def _extract_verdict(decision_text: str | None, portfolio_md: str) -> dict[str, Any]:
    """从 portfolio manager decision 文本里 grep verdict.

    优先 decision_text (run_batch 直接拿到的最终输出), 退到 portfolio markdown.
    Portfolio manager 实际更倾向用 Overweight / Underweight / Equal Weight
    这套术语, 简单 BUY/HOLD/SELL 命中率低, 见 reports/NVDA_20260510_*/
    """
    src = decision_text or portfolio_md or ""
    action = "UNKNOWN"
    for pat, label in _ACTION_PATTERNS:
        if pat.search(src):
            action = label
            break

    if _STRONG_RE.search(src):
        confidence = 0.85
    elif action != "UNKNOWN":
        confidence = 0.65
    else:
        confidence = 0.5

    return {
        "action": action,
        "confidence": confidence,
        "raw": (decision_text or portfolio_md or "").strip()[:1500],
    }


# ─── scores.json 重新映射 ─────────────────────────────────────────────
def _flatten_scores(score_dict: dict[str, Any]) -> dict[str, Any]:
    """score_extractor 的输出归一化成扁平 schema."""
    technical = score_dict.get("technical") or {}
    fundamental = score_dict.get("fundamental") or {}
    sentiment = score_dict.get("sentiment") or {}
    news = score_dict.get("news") or {}
    return {
        "technical":    score_dict.get("technical_score") if score_dict.get("technical_score") is not None else technical.get("score"),
        "fundamental":  score_dict.get("fundamental_score") if score_dict.get("fundamental_score") is not None else fundamental.get("score"),
        "sentiment":    sentiment.get("score"),
        "news":         news.get("score"),
        "quadrant":     score_dict.get("quadrant"),
        "final_rating": score_dict.get("final_rating"),
    }


# ─── 入口 ──────────────────────────────────────────────────────────────
def export_to_billionaire(
    ticker: str,
    report_dir: Path,
    *,
    analysis_date: str,
    score_dict: dict[str, Any] | None = None,
    decision_text: str | None = None,
    model_config: dict[str, str] | None = None,
    out_dir: Path | None = None,
) -> Path:
    """把一只股票的报告写成 Billionaire 能读的 JSON. 返回输出路径.

    ticker         原始 TradingAgents ticker (NVDA / 600519.SS / 0700.HK)
    report_dir     reports/<TICKER>_<ts>/  绝对路径
    analysis_date  分析日 (YYYY-MM-DD)
    score_dict     scores.json 内容 (run_batch 拿到的 score.to_dict())
                   不传则尝试从 report_dir/scores.json 读
    decision_text  portfolio manager 最终决策文本
    model_config   {"quick": "...", "deep": "..."}
    out_dir        覆盖默认 ~/.market_data/agent_reports/
    """
    if not report_dir.exists():
        raise FileNotFoundError(f"report dir not found: {report_dir}")

    ts_code = normalize_ts_code(ticker)
    market = market_of_ts_code(ts_code)

    # score_dict 优先用入参; 没传就从盘上 scores.json 读
    if score_dict is None:
        scores_fp = report_dir / "scores.json"
        if scores_fp.exists():
            score_dict = json.loads(scores_fp.read_text(encoding="utf-8"))
        else:
            score_dict = {}

    agents = _collect_agents(report_dir)
    verdict = _extract_verdict(decision_text, agents.get("portfolio") or agents.get("research_manager") or "")

    payload = {
        "ts_code": ts_code,
        "ticker": ts_code.split(".")[0],
        "name": None,
        "market": market,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "analysis_date": analysis_date,
        "model": model_config or {},
        "verdict": verdict,
        "scores": _flatten_scores(score_dict),
        "agents": agents,
    }

    out_root = out_dir or AGENT_REPORTS_DIR
    out_root.mkdir(parents=True, exist_ok=True)

    # 文件名带 model 后缀让"同一只股票, 不同模型"的报告独立存放, 用户可以
    # 在 UI 上切换对比. model_id 取 deep_think_llm (主推理模型, 是 verdict 来源);
    # 没传 model_config 就 fallback 到 'unknown' (老 export 兼容).
    # 文件名安全: model 里的 '/' 替成 '-' 防 path traversal.
    deep_model = (model_config or {}).get("deep") or "unknown"
    safe_model = deep_model.replace("/", "-").replace("\\", "-")
    out_path = out_root / f"{ts_code}.{safe_model}.json"
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return out_path
