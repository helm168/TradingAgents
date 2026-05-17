"""Piotroski F-Score 量化打分 — LLM 主观分的客观参照系.

F-Score (1999, Joseph Piotroski) 是经典财务质量打分卡: 9 个布尔信号, 命中
计 1 分, 总分 0-9. 学术回测: 8-9 分组合年化跑赢市场 7-9pp, 0-2 分组合
跑输. 跟 LLM 打分本质区别: 同样的财报数据进来, **不管哪家 LLM 跑**, 输出
完全一致, 没有主观裁量空间.

9 个信号
─────────
盈利能力 (4 条):
  1. ROA > 0                — 当前年度盈利
  2. CFO > 0                — 经营现金流为正
  3. ΔROA > 0               — 资产回报率同比上升
  4. CFO > Net Income       — 盈利质量 (现金 > 账面利润)

杠杆/流动性/股本 (3 条):
  5. ΔLeverage < 0          — 长期负债 / 总资产 同比下降
  6. ΔCurrent Ratio > 0     — 流动比率同比上升
  7. 未增发股票             — 股本同比未扩张 (容忍 1% 噪声)

运营效率 (2 条):
  8. ΔGross Margin > 0      — 毛利率同比上升
  9. ΔAsset Turnover > 0    — 资产周转率 (营收/总资产) 同比上升

数据要求
─────────
从 sh_quant ~/.market_data/financials/<ts_code>.parquet 读取, schema 见
sh_quant/scripts/pull_financials.py. 至少需要 2 个完整财年 (period == 'Q4')
做同比对比, 缺数据的信号保守计 0 分.

CN Tushare 习惯: income/cashflow 是 YTD 累计, Q4 行 = 全年; balance sheet
是时点快照. 美股 FMP 同样 normalize 到这套 schema.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from typing import Optional

import pandas as pd

from tradingagents.dataflows.local_parquet_stock import (
    _financial_path,
    normalize_ts_code,
)

logger = logging.getLogger(__name__)


# ─── 数据结构 ───────────────────────────────────────────────────────────
@dataclass
class FScoreSignal:
    """单条 F-Score 信号."""
    name: str        # "ROA > 0"
    value: bool      # True = 1 分, False = 0 分
    detail: str      # "ROA = 12.34%" 类人读得懂的解释


@dataclass
class FScoreResult:
    ts_code: str
    fiscal_year: Optional[int]
    prior_fiscal_year: Optional[int]
    score: int                                        # 0-9
    signals: list[FScoreSignal] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def rating(self) -> str:
        """0-9 分映射到 强壮/中性/恶化."""
        if self.score >= 7:
            return "强壮"
        if self.score >= 4:
            return "中性"
        return "恶化"

    def to_dict(self) -> dict:
        return {
            "ts_code": self.ts_code,
            "fiscal_year": self.fiscal_year,
            "prior_fiscal_year": self.prior_fiscal_year,
            "score": self.score,
            "max_score": 9,
            "rating": self.rating,
            "signals": [
                {"name": s.name, "value": s.value, "detail": s.detail}
                for s in self.signals
            ],
            "errors": self.errors,
        }


# ─── 辅助函数 ───────────────────────────────────────────────────────────
def _to_float(x) -> Optional[float]:
    """安全转 float, NaN/None/字符串 都返 None."""
    try:
        v = float(x)
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return None


def _safe_div(a, b) -> Optional[float]:
    a = _to_float(a)
    b = _to_float(b)
    if a is None or b is None or b == 0:
        return None
    return a / b


def _roa(row) -> Optional[float]:
    """ROA 优先用 parquet 自带 roa 列 (%); 缺失则用 NI/TA 现算 (小数).

    返回值统一是 **小数** (0.1234 表示 12.34%).
    """
    raw = _to_float(row.get("roa"))
    if raw is not None:
        # sh_quant 的 roa 存的是百分数 (e.g. 12.34 = 12.34%)
        return raw / 100.0
    return _safe_div(row.get("net_income"), row.get("total_assets"))


def _shares_implied(row) -> Optional[float]:
    """net_income / eps_basic 反推流通股数.

    用来检测股本扩张 (F-Score #7). EPS 缺失或 0 时返 None, 调用方保守
    给 0 分.
    """
    ni = _to_float(row.get("net_income"))
    eps = _to_float(row.get("eps_basic"))
    if ni is None or eps is None or eps == 0:
        return None
    return ni / eps


# ─── 主入口 ─────────────────────────────────────────────────────────────
def compute_fscore(ticker: str) -> Optional[FScoreResult]:
    """对单只股票算 Piotroski F-Score.

    Args:
        ticker: 任意 yfinance/TradingAgents 风格 ticker (NVDA / 600519.SS /
                0700.HK / 300131.SZ). 内部会 normalize 到 ts_code.

    Returns:
        FScoreResult, 或 None 当没有本地财报数据时.
    """
    ts_code = normalize_ts_code(ticker)
    fp = _financial_path(ts_code)
    if not fp.exists():
        logger.info("F-Score: 没有本地财报 parquet %s", fp)
        return None

    df = pd.read_parquet(fp)
    if df is None or len(df) == 0:
        return None

    # 取完整财年 (period == 'Q4', 即年报). 没 period 列就用全部行.
    if "period" in df.columns:
        annual = df[df["period"] == "Q4"].copy()
    else:
        annual = df.copy()

    # 按 fiscal_year 或 end_date 排序, 取最后 2 个
    if "fiscal_year" in annual.columns:
        annual = annual.dropna(subset=["fiscal_year"]).sort_values("fiscal_year")
    elif "end_date" in annual.columns:
        annual["end_date"] = pd.to_datetime(annual["end_date"], errors="coerce")
        annual = annual.dropna(subset=["end_date"]).sort_values("end_date")

    if len(annual) < 2:
        return FScoreResult(
            ts_code=ts_code,
            fiscal_year=None,
            prior_fiscal_year=None,
            score=0,
            errors=[f"完整财年数据不足 ({len(annual)} 年), F-Score 至少需要 2 年同比"],
        )

    curr = annual.iloc[-1]
    prev = annual.iloc[-2]

    fy_curr = int(curr["fiscal_year"]) if "fiscal_year" in curr and pd.notna(curr.get("fiscal_year")) else None
    fy_prev = int(prev["fiscal_year"]) if "fiscal_year" in prev and pd.notna(prev.get("fiscal_year")) else None

    signals: list[FScoreSignal] = []

    # ─── 盈利能力 (4) ───────────────────────────────────────────────
    # 1. ROA > 0
    roa_curr = _roa(curr)
    signals.append(FScoreSignal(
        name="ROA > 0",
        value=bool(roa_curr is not None and roa_curr > 0),
        detail=(f"ROA = {roa_curr * 100:.2f}%" if roa_curr is not None else "ROA 数据缺失"),
    ))

    # 2. CFO > 0
    cfo_curr = _to_float(curr.get("operating_cf"))
    signals.append(FScoreSignal(
        name="经营现金流 > 0",
        value=bool(cfo_curr is not None and cfo_curr > 0),
        detail=(f"OCF = {cfo_curr:,.0f}" if cfo_curr is not None else "OCF 数据缺失"),
    ))

    # 3. ΔROA > 0
    roa_prev = _roa(prev)
    delta_roa = (
        roa_curr - roa_prev
        if (roa_curr is not None and roa_prev is not None)
        else None
    )
    signals.append(FScoreSignal(
        name="ROA 同比上升",
        value=bool(delta_roa is not None and delta_roa > 0),
        detail=(
            f"ROA: {roa_prev * 100:.2f}% → {roa_curr * 100:.2f}%"
            if (roa_curr is not None and roa_prev is not None)
            else "ROA 同比数据缺失"
        ),
    ))

    # 4. CFO > Net Income (盈利质量)
    ni_curr = _to_float(curr.get("net_income"))
    quality_ok = (
        cfo_curr is not None
        and ni_curr is not None
        and cfo_curr > ni_curr
    )
    signals.append(FScoreSignal(
        name="经营现金流 > 净利润",
        value=quality_ok,
        detail=(
            f"OCF {cfo_curr:,.0f} vs NI {ni_curr:,.0f}"
            if (cfo_curr is not None and ni_curr is not None)
            else "盈利质量数据缺失"
        ),
    ))

    # ─── 杠杆/流动性/股本 (3) ───────────────────────────────────────
    # 5. ΔLeverage < 0 (LT debt / total assets 下降)
    ta_curr = _to_float(curr.get("total_assets"))
    ta_prev = _to_float(prev.get("total_assets"))
    lev_curr = _safe_div(curr.get("long_term_debt"), ta_curr)
    lev_prev = _safe_div(prev.get("long_term_debt"), ta_prev)
    delta_lev = (
        lev_curr - lev_prev
        if (lev_curr is not None and lev_prev is not None)
        else None
    )
    signals.append(FScoreSignal(
        name="长期负债率下降",
        value=bool(delta_lev is not None and delta_lev < 0),
        detail=(
            f"LT债/总资产: {lev_prev * 100:.2f}% → {lev_curr * 100:.2f}%"
            if (lev_curr is not None and lev_prev is not None)
            else "长期负债率数据缺失"
        ),
    ))

    # 6. ΔCurrent Ratio > 0
    cr_curr = _to_float(curr.get("current_ratio"))
    cr_prev = _to_float(prev.get("current_ratio"))
    delta_cr = (
        cr_curr - cr_prev
        if (cr_curr is not None and cr_prev is not None)
        else None
    )
    signals.append(FScoreSignal(
        name="流动比率上升",
        value=bool(delta_cr is not None and delta_cr > 0),
        detail=(
            f"流动比率: {cr_prev:.2f} → {cr_curr:.2f}"
            if (cr_curr is not None and cr_prev is not None)
            else "流动比率数据缺失"
        ),
    ))

    # 7. 未增发股票 (用 NI/EPS 反推股数, 容忍 1% 噪声防期权稀释/口径切换)
    shares_curr = _shares_implied(curr)
    shares_prev = _shares_implied(prev)
    if shares_curr is not None and shares_prev is not None and shares_prev > 0:
        no_issuance = shares_curr <= shares_prev * 1.01
        chg_pct = (shares_curr / shares_prev - 1) * 100
        share_detail = f"股本估算: {shares_prev:,.0f} → {shares_curr:,.0f} ({chg_pct:+.2f}%)"
    else:
        # 数据缺失保守计 0 (无法证伪"没增发")
        no_issuance = False
        share_detail = "股本估算所需 EPS 缺失"
    signals.append(FScoreSignal(
        name="未增发股票",
        value=no_issuance,
        detail=share_detail,
    ))

    # ─── 运营效率 (2) ───────────────────────────────────────────────
    # 8. ΔGross Margin > 0
    gm_curr = _to_float(curr.get("gross_margin"))
    gm_prev = _to_float(prev.get("gross_margin"))
    delta_gm = (
        gm_curr - gm_prev
        if (gm_curr is not None and gm_prev is not None)
        else None
    )
    signals.append(FScoreSignal(
        name="毛利率上升",
        value=bool(delta_gm is not None and delta_gm > 0),
        detail=(
            f"毛利率: {gm_prev:.2f}% → {gm_curr:.2f}%"
            if (gm_curr is not None and gm_prev is not None)
            else "毛利率数据缺失"
        ),
    ))

    # 9. ΔAsset Turnover > 0 (营收 / 总资产)
    at_curr = _safe_div(curr.get("revenue"), ta_curr)
    at_prev = _safe_div(prev.get("revenue"), ta_prev)
    delta_at = (
        at_curr - at_prev
        if (at_curr is not None and at_prev is not None)
        else None
    )
    signals.append(FScoreSignal(
        name="资产周转率上升",
        value=bool(delta_at is not None and delta_at > 0),
        detail=(
            f"周转率: {at_prev:.3f} → {at_curr:.3f}"
            if (at_curr is not None and at_prev is not None)
            else "周转率数据缺失"
        ),
    ))

    total = sum(1 for s in signals if s.value)
    return FScoreResult(
        ts_code=ts_code,
        fiscal_year=fy_curr,
        prior_fiscal_year=fy_prev,
        score=total,
        signals=signals,
    )


# ─── 命令行 (调试用) ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("usage: python -m tradingagents.scoring.fscore <ticker>")
        sys.exit(1)

    ticker = sys.argv[1]
    result = compute_fscore(ticker)
    if result is None:
        print(f"没有 {ticker} 的本地财报数据")
        sys.exit(2)

    print(f"\n{ticker} (ts_code: {result.ts_code})")
    print(f"F-Score: {result.score}/9 [{result.rating}]")
    print(f"对比财年: FY{result.prior_fiscal_year} → FY{result.fiscal_year}\n")
    for i, sig in enumerate(result.signals, 1):
        mark = "✓" if sig.value else "✗"
        print(f"  {mark} #{i} {sig.name:<20}  {sig.detail}")
    if result.errors:
        print("\nerrors:")
        for e in result.errors:
            print(f"  - {e}")
