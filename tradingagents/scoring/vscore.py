"""V-Score (Value Score) — 估值因子, 0-100.

跟 Q-Score / G-Score / F-Score 的位置
─────────────────────────────────────
Q-Score: 公司质量 (净利率/ROE/财务健康)
G-Score: 增长 (营收/净利 CAGR)
V-Score: **估值** (PE/PB/PEG) ← 本模块
F-Score: 改善 trend 9 信号

为什么需要 V-Score
─────────────────
Q + G 两个分都高的公司 (e.g. 茅台 / 宁德), 还要看估值才能决定要不要买. 巴菲特
"以合理价格买好公司" / 林奇 "GARP" 都强调:
  - 茅台 PE 30 + 增长 5%: PEG = 6, 贵, V-Score ≈ 25
  - 宁德 PE 20 + 增长 40%: PEG = 0.5, 极便宜, V-Score ≈ 90
两家公司的 Q + G 综合可能差不多, 但 V-Score 一拉开就立判.

三个指标 (加权)
────────────────
PE (TTM)   40%   滚动 12 个月市盈率, 反映"现在 vs 当前盈利"
PB         20%   市净率, 重资产/银行/地产 这种行业必看
PEG        40%   PE / 净利润增长率, GARP 核心. 增长率 = 净利最新一年同比
                 (forward 代理, 见 _net_income_yoy; 不用多年 CAGR — 加速期会低估).

阈值表
──────
PE (TTM):  <10  → 100  (深度价值)
           10-15 → 85
           15-20 → 65
           20-30 → 45
           30-50 → 25
           >50   → 10  (除非高速成长否则危险)

PB:        <1.5  → 100
           1.5-2.5 → 80
           2.5-4   → 60
           4-6     → 35
           >6      → 15

PEG:       <0.8  → 100  (林奇说 < 1 就是便宜)
           0.8-1.2 → 85
           1.2-1.8 → 65
           1.8-3   → 40
           >3      → 15

数据来源
─────────
sh_quant ~/.market_data/daily_basic/<ts_code>.parquet (Tushare pro.daily_basic),
schema 见 sh_quant/scripts/pull_daily_basic.py:
    trade_date, pe_ttm, pb, ...

V-Score 需要的字段: pe_ttm + pb. PEG 这边自己从 financials parquet 拿净利
增长率算 (复用 gscore.py 的逻辑而不是直接 import, 因为 V-Score 应该独立).

边界情况
─────────
- pe_ttm 为负 (亏损公司): PE 概念失效, V-Score 给 PE 0 分 (亏损不能用 PE 估值)
- pb 为负 (净资产为负, e.g. 暴雷股): 同样给 0 分
- 没本地 daily_basic 数据 (sh_quant 没拉 daily_basic): 返 None, 调用方降级
- 净利增长为负: PEG 不可定义 → PEG 给 0 分
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

from tradingagents.dataflows.local_parquet_stock import (
    _data_root,
    normalize_ts_code,
)

logger = logging.getLogger(__name__)


# ─── 数据结构 ───────────────────────────────────────────────────────────
@dataclass
class VScoreMetric:
    name: str
    value: Optional[float]
    unit: str
    score: float
    detail: str


@dataclass
class VScoreResult:
    ts_code: str
    as_of_date: Optional[str]    # 最新 trade_date YYYY-MM-DD
    score: float                  # 0-100
    metrics: list[VScoreMetric] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def rating(self) -> str:
        s = self.score
        if s >= 80:
            return "便宜"
        if s >= 60:
            return "合理"
        if s >= 40:
            return "略贵"
        if s >= 20:
            return "偏贵"
        return "泡沫"

    def to_dict(self) -> dict:
        return {
            "ts_code": self.ts_code,
            "as_of_date": self.as_of_date,
            "score": round(self.score, 1),
            "rating": self.rating,
            "metrics": [
                {
                    "name": m.name,
                    "value": m.value,
                    "unit": m.unit,
                    "score": round(m.score, 1),
                    "detail": m.detail,
                }
                for m in self.metrics
            ],
            "errors": self.errors,
        }


# ─── 阈值表 ─────────────────────────────────────────────────────────────
PE_BRACKETS  = [(50, 10), (30, 25), (20, 45), (15, 65), (10, 85), (0, 100)]   # 上下倒着排
PB_BRACKETS  = [(6, 15), (4, 35), (2.5, 60), (1.5, 80), (0, 100)]
PEG_BRACKETS = [(3, 15), (1.8, 40), (1.2, 65), (0.8, 85), (0, 100)]


def _pe_score(pe: Optional[float]) -> tuple[float, str]:
    """PE 越小越好. 负 PE (亏损) 给 0 分."""
    if pe is None or pd.isna(pe):
        return 0.0, "PE 数据缺失"
    if pe <= 0:
        return 0.0, f"PE = {pe:.2f} (亏损, 估值失效)"
    # 找第一个 threshold > pe 的档位之前一档. brackets 是大→小.
    for thresh, sc in PE_BRACKETS:
        if pe >= thresh:
            return float(sc), f"PE = {pe:.2f}x"
    return 100.0, f"PE = {pe:.2f}x"


def _pb_score(pb: Optional[float]) -> tuple[float, str]:
    if pb is None or pd.isna(pb):
        return 0.0, "PB 数据缺失"
    if pb <= 0:
        return 0.0, f"PB = {pb:.2f} (净资产为负, 暴雷信号)"
    for thresh, sc in PB_BRACKETS:
        if pb >= thresh:
            return float(sc), f"PB = {pb:.2f}x"
    return 100.0, f"PB = {pb:.2f}x"


def _peg_score(peg: Optional[float], pe: Optional[float], growth: Optional[float]) -> tuple[float, str]:
    """PEG 越小越好. 增长为负/0 时 PEG 不可定义."""
    if peg is None or pd.isna(peg):
        if growth is None:
            return 0.0, "PEG 无法计算 (缺增长率)"
        if growth <= 0:
            return 0.0, f"PEG 无法计算 (增长率 = {growth:.1f}%, 非正)"
        return 0.0, "PEG 数据缺失"
    if peg <= 0:
        # PE 负 → PEG 也负, 信息已经反映在 PE 分上
        return 0.0, f"PEG = {peg:.2f} (PE 或增长率非正)"
    for thresh, sc in PEG_BRACKETS:
        if peg >= thresh:
            return float(sc), f"PEG = {peg:.2f} (PE {pe:.1f} / 增长 {growth:.1f}%)"
    return 100.0, f"PEG = {peg:.2f}"


# ─── daily_basic 读取 ──────────────────────────────────────────────────
def _daily_basic_path(ts_code: str) -> Path:
    return _data_root() / "daily_basic" / f"{ts_code}.parquet"


def _read_latest_valuation(ts_code: str) -> Optional[dict]:
    """读最新一天 pe_ttm + pb. 返回 {trade_date, pe_ttm, pb}.

    daily_basic schema: trade_date / pe / pe_ttm / pb / ... 见
    sh_quant/scripts/pull_daily_basic.py.
    """
    fp = _daily_basic_path(ts_code)
    if not fp.exists():
        return None
    try:
        df = pd.read_parquet(fp, columns=["trade_date", "pe_ttm", "pb"])
    except Exception as e:
        logger.warning("V-Score 读 daily_basic 失败 %s: %s", fp, e)
        return None
    if df is None or df.empty:
        return None
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.dropna(subset=["trade_date"]).sort_values("trade_date")
    if df.empty:
        return None
    last = df.iloc[-1]
    return {
        "trade_date": last["trade_date"].strftime("%Y-%m-%d"),
        "pe_ttm": float(last["pe_ttm"]) if pd.notna(last["pe_ttm"]) else None,
        "pb": float(last["pb"]) if pd.notna(last["pb"]) else None,
    }


# ─── 增长率 (PEG 需要) ─────────────────────────────────────────────────
# 不直接 import gscore.compute_gscore (避免循环和重复算两遍). 这里独立从
# financials parquet 拿净利润最新一年同比 (forward 代理).
#
# 为什么用最新 YoY 而非 3Y CAGR: 业绩加速期 trailing 多年均值会把加速前的
# 慢期掺进来, 显著低估真实增速 → PEG 虚高, 把刚走出底部的高成长票当贵的
# 错杀. 真分析师一致预期 forward 增速 A 股无干净源, 故用最新已实现 YoY 作
# 代理. 与 Billionaire computePeg 同方向 (口径残差: 此处归母·年报, 那边
# 扣非·季度TTM, 差几个点可解释, 不追求数字完全一致).
def _net_income_yoy(ts_code: str) -> Optional[float]:
    """直接从 financials parquet 算净利润最新一年同比 (%).

    跟 gscore.py 的逻辑一致, 单独实现避免循环依赖.
    """
    from tradingagents.dataflows.local_parquet_stock import _financial_path
    fp = _financial_path(ts_code)
    if not fp.exists():
        return None
    try:
        df = pd.read_parquet(fp, columns=["period", "fiscal_year", "end_date", "net_income"])
    except Exception:
        return None
    if df is None or df.empty:
        return None
    annual = df[df["period"] == "Q4"].copy() if "period" in df.columns else df.copy()
    if "fiscal_year" in annual.columns:
        annual = annual.dropna(subset=["fiscal_year"]).sort_values("fiscal_year")
    elif "end_date" in annual.columns:
        annual["end_date"] = pd.to_datetime(annual["end_date"], errors="coerce")
        annual = annual.dropna(subset=["end_date"]).sort_values("end_date")
    if len(annual) < 2:
        return None

    seq = annual["net_income"].astype(float).tolist()
    clean = [v for v in seq if v is not None and not pd.isna(v)]
    if len(clean) < 2:
        return None
    prev, latest = clean[-2], clean[-1]
    if prev <= 0 or latest <= 0:
        return None
    return (latest / prev - 1.0) * 100.0


# ─── 主入口 ─────────────────────────────────────────────────────────────
def compute_vscore(ticker: str) -> Optional[VScoreResult]:
    """对单只股票算 V-Score (Value 0-100).

    需要本地 daily_basic parquet (pe_ttm/pb) 和 financials parquet (净利增长 for PEG).
    daily_basic 数据由 sh_quant scripts/pull_daily_basic.py 拉取生成.
    """
    ts_code = normalize_ts_code(ticker)

    valuation = _read_latest_valuation(ts_code)
    if valuation is None:
        # 缺 daily_basic: 返 None 让调用方降级 (compute_quant_scores.py 据此把
        # envelope 的 vscore 置 null, 下游显示"估值数据缺失" NA 卡)。
        # 绝不返 score=0.0 的对象——rating 纯按 score 分档, score=0 会落进
        # "泡沫", 把"没算"误渲染成"贵到泡沫"的确定性结论, 语义完全相反。
        logger.info("V-Score 跳过 %s: 无本地 daily_basic (US 跑 pull_us_daily_basic.py)", ts_code)
        return None

    pe = valuation["pe_ttm"]
    pb = valuation["pb"]
    growth = _net_income_yoy(ts_code)

    pe_score, pe_detail = _pe_score(pe)
    pb_score, pb_detail = _pb_score(pb)

    # PEG 算法: PE / (增长率 %). 注意单位 — Lynch 的 PEG 公式里 PE 是数字,
    # 增长率也是数字 (e.g. 20% → 20, 不是 0.2). PE=30 + 增长 20% → PEG=1.5.
    if pe is not None and pe > 0 and growth is not None and growth > 0:
        peg = pe / growth
    else:
        peg = None
    peg_score, peg_detail = _peg_score(peg, pe, growth)

    # PE 是亏损时 PB 仍然有信息 (净资产). 不直接给 0 分.
    # PEG 退化到 None 时, 仍然能从 PE + PB 加权出有意义分数.
    metrics = [
        VScoreMetric(name="PE (TTM)", value=pe, unit="x", score=pe_score, detail=pe_detail),
        VScoreMetric(name="PB",       value=pb, unit="x", score=pb_score, detail=pb_detail),
        VScoreMetric(name="PEG",      value=peg, unit="",  score=peg_score, detail=peg_detail),
    ]

    # 权重: PE 40% + PB 20% + PEG 40%. PEG 缺数据时把权重均分到 PE/PB.
    pe_w, pb_w, peg_w = 0.40, 0.20, 0.40
    if peg is None:
        # PEG 不可用: PE 60% / PB 40%
        pe_w, pb_w, peg_w = 0.60, 0.40, 0.0

    total = pe_score * pe_w + pb_score * pb_w + peg_score * peg_w

    return VScoreResult(
        ts_code=ts_code,
        as_of_date=valuation["trade_date"],
        score=total,
        metrics=metrics,
    )


# ─── 命令行 ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m tradingagents.scoring.vscore <ticker>")
        sys.exit(1)
    ticker = sys.argv[1]
    r = compute_vscore(ticker)
    if r is None:
        print(f"没有 {ticker} 的本地估值数据")
        sys.exit(2)
    print(f"\n{ticker} (ts_code: {r.ts_code})")
    print(f"V-Score: {r.score:.1f}/100 [{r.rating}]")
    print(f"as of {r.as_of_date}\n")
    for m in r.metrics:
        print(f"  • {m.name:<10} {m.detail:<35} → {m.score:.0f} 分")
    if r.errors:
        for e in r.errors:
            print(f"\n  error: {e}")
