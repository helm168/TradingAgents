"""G-Score (Growth Score) — 独立 Growth 因子, 0-100.

跟 Q-Score / V-Score / F-Score 的位置
─────────────────────────────────────
Q-Score: 绝对盈利能力 (净利率/毛利率/ROE/财务健康). 看"公司多好".
G-Score: 增长 (营收/净利 CAGR + YoY). 看"成长多快".
V-Score: 估值 (PE/PB/PEG). 看"贵不贵".
F-Score: 改善 trend 9 信号. 看"距离破产线".

为什么 Growth 独立成因子
───────────────────────
学术上 Quality (Fama-French RMW) 跟 Growth 是**独立因子**, 不能混. 茅台
Q-Score 高 + G-Score 低 = "盈利好但成长枯竭". 宁德 Q-Score 中 + G-Score 高 =
"成长狂飙但利润率被价格战压". GARP 投资者要的就是 Q + G 都高的标的.

两大维度 (50/50)
─────────────────
A. 营收增长 50% — 3Y CAGR (有 4+ 年数据) 或 1Y YoY (退化)
B. 净利增长 50% — 3Y CAGR / 1Y YoY

权重五五开是有道理的: 营收增长是"市场扩张"的硬指标 (不易造假), 净利增长是
"商业模式" 的体现. 一家公司营收涨 30% 但净利只涨 5% (典型: 烧钱抢市场份额)
就会暴露在 G-Score 上.

数据要求
─────────
sh_quant ~/.market_data/financials/<ts_code>.parquet (跟 F/Q-Score 同源).
理想: 4 个完整财年 (跨 3 年) 算 CAGR; 退化: 2 年算 YoY; 不足 2 年返 errors.

边界情况
─────────
- 基期为负 (亏损): CAGR 数学上不可定义, 用首次扭亏年作起点; 还是负则给低分
- 营收减少 + 净利暴增 (减员增效): G-Score 反映为 营收低分 + 净利高分, 总分中等
- 周期股 (营收波动大): G-Score 会随财年漂移, 用户要结合 V-Score 估值看
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from tradingagents.dataflows.local_parquet_stock import (
    _financial_path,
    normalize_ts_code,
)

logger = logging.getLogger(__name__)


# ─── 数据结构 ───────────────────────────────────────────────────────────
@dataclass
class GScoreMetric:
    name: str        # "营收 3Y CAGR"
    value: Optional[float]  # 41.23
    unit: str        # "%"
    score: float     # 0-100
    detail: str      # "41.23% → 100 分 (≥30%)"


@dataclass
class GScoreDimension:
    name: str
    weight: float    # 0.50
    score: float
    metrics: list[GScoreMetric] = field(default_factory=list)


@dataclass
class GScoreResult:
    ts_code: str
    fiscal_year_curr: Optional[int]
    fiscal_year_base: Optional[int]   # CAGR 起点的财年
    n_years: int                       # 用了几年算的 (1=YoY, 3=3Y CAGR)
    score: float                       # 0-100
    dimensions: list[GScoreDimension] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def rating(self) -> str:
        s = self.score
        if s >= 80:
            return "高增长"
        if s >= 60:
            return "稳健"
        if s >= 40:
            return "温和"
        if s >= 20:
            return "停滞"
        return "衰退"

    def to_dict(self) -> dict:
        return {
            "ts_code": self.ts_code,
            "fiscal_year_curr": self.fiscal_year_curr,
            "fiscal_year_base": self.fiscal_year_base,
            "n_years": self.n_years,
            "score": round(self.score, 1),
            "rating": self.rating,
            "dimensions": [
                {
                    "name": d.name,
                    "weight": d.weight,
                    "score": round(d.score, 1),
                    "metrics": [
                        {
                            "name": m.name,
                            "value": m.value,
                            "unit": m.unit,
                            "score": round(m.score, 1),
                            "detail": m.detail,
                        }
                        for m in d.metrics
                    ],
                }
                for d in self.dimensions
            ],
            "errors": self.errors,
        }


# ─── 辅助函数 ───────────────────────────────────────────────────────────
def _to_float(x) -> Optional[float]:
    try:
        v = float(x)
        return None if pd.isna(v) else v
    except (TypeError, ValueError):
        return None


# Growth 阈值表 (业界经验): ≥30% 高增长 / 20% 稳健 / 10% 温和 / 5% 停滞 / 负 衰退
# 这套阈值适用于成熟期 + 成长期公司的混合; 巨型蓝筹 (茅台/可口可乐) 会偏低,
# 高景气赛道 (新能源/AI 算力) 会偏高, 这种**离散**正是 G-Score 想反映的.
GROWTH_BRACKETS = [(30, 100), (20, 85), (10, 60), (5, 40), (0, 20)]


def _bracket_score(value: Optional[float], brackets: list[tuple[float, float]]) -> float:
    """按阈值表给分. value 为 None / NaN 返 0; ≥threshold 给对应 score."""
    if value is None or pd.isna(value):
        return 0.0
    for threshold, sc in brackets:
        if value >= threshold:
            return float(sc)
    return 0.0


def _fmt_pct(v: Optional[float]) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:.2f}%"


def _cagr(seq: list[Optional[float]]) -> Optional[float]:
    """seq 按时间升序取首尾算 CAGR (%).

    首尾任一为非正数返 None — 不能开根号. 调用方应 fallback 到 YoY.
    """
    clean = [v for v in seq if v is not None and not pd.isna(v)]
    if len(clean) < 2:
        return None
    start, end = clean[0], clean[-1]
    n = len(clean) - 1
    if start <= 0 or end <= 0:
        return None
    return ((end / start) ** (1.0 / n) - 1.0) * 100.0


def _yoy(curr_v: Optional[float], prev_v: Optional[float]) -> Optional[float]:
    """单期 YoY 增长 (%). 基期 ≤0 返 None."""
    if curr_v is None or prev_v is None or pd.isna(curr_v) or pd.isna(prev_v):
        return None
    if prev_v <= 0:
        return None
    return (curr_v / prev_v - 1.0) * 100.0


# ─── 主入口 ─────────────────────────────────────────────────────────────
# 两点 CAGR 把"中途巨亏 + 周期顶点"完全抹平: 佰维存储 688525 FY2023 巨亏
# -6.3 亿, 但 FY2022(0.71 亿)→FY2025(8.4 亿) 两点净利 CAGR 仍 127% → 误判高增长.
# 在 bracket 打分之上叠一层路径惩罚: 亏损年 / 基数畸小 / 非单调 → 乘子压向 0.
PATH_LOSS_PENALTY = 0.45
PATH_BASE_PENALTY = 0.30
PATH_MONO_PENALTY = 0.40
PATH_MIN_BASE_FRAC = 0.15


def _path_penalty(seq: list[Optional[float]]) -> tuple[float, list[str]]:
    """对算 CAGR 的年度序列做路径质量检查, 返回 (乘子 0-1, flags).

    干净复利序列 (逐年涨 / 基数不畸小 / 无亏损年) → (1.0, []); 路径越脏乘子越低.
    """
    clean = [v for v in seq if v is not None and not pd.isna(v)]
    if len(clean) < 2:
        return 1.0, []
    base, peak, lo = clean[0], max(clean), min(clean)
    flags: list[str] = []
    deduct = 0.0

    if any(v <= 0 for v in clean):
        deduct += PATH_LOSS_PENALTY
        flags.append("亏损年")

    if base <= 0:
        deduct += PATH_BASE_PENALTY
        flags.append("基期为负")
    elif peak > 0 and base < PATH_MIN_BASE_FRAC * peak:
        deduct += PATH_BASE_PENALTY
        flags.append("基数畸小")

    total_down = sum(max(clean[i] - clean[i + 1], 0.0) for i in range(len(clean) - 1))
    span = peak - lo
    if span > 0 and total_down > 0:
        violation = min(total_down / span, 1.0)
        deduct += PATH_MONO_PENALTY * violation
        flags.append("非单调")

    return max(0.0, min(1.0, 1.0 - deduct)), flags


def compute_gscore(ticker: str) -> Optional[GScoreResult]:
    """对单只股票算 G-Score (Growth 0-100).

    优先用 3Y CAGR (4 个完整财年). 不够就退化到 1Y YoY (2 个财年). 都不够
    返 errors.
    """
    ts_code = normalize_ts_code(ticker)
    fp = _financial_path(ts_code)
    if not fp.exists():
        logger.info("G-Score: 没有本地财报 parquet %s", fp)
        return None

    df = pd.read_parquet(fp)
    if df is None or len(df) == 0:
        return None

    # 完整财年 (period == 'Q4')
    annual = df[df["period"] == "Q4"].copy() if "period" in df.columns else df.copy()
    if "fiscal_year" in annual.columns:
        annual = annual.dropna(subset=["fiscal_year"]).sort_values("fiscal_year")
    elif "end_date" in annual.columns:
        annual["end_date"] = pd.to_datetime(annual["end_date"], errors="coerce")
        annual = annual.dropna(subset=["end_date"]).sort_values("end_date")

    if len(annual) < 2:
        return GScoreResult(
            ts_code=ts_code, fiscal_year_curr=None, fiscal_year_base=None,
            n_years=0, score=0.0,
            errors=[f"完整财年数据不足 ({len(annual)} 年), G-Score 至少需要 2 年"],
        )

    # 尝试 3Y CAGR (4 行) → 退化 YoY (2 行)
    recent = annual.tail(4) if len(annual) >= 4 else annual.tail(2)
    n_years = len(recent) - 1   # 1 → YoY, 3 → 3Y CAGR
    curr = recent.iloc[-1]
    base = recent.iloc[0]

    fy_curr = int(curr["fiscal_year"]) if "fiscal_year" in curr and pd.notna(curr.get("fiscal_year")) else None
    fy_base = int(base["fiscal_year"]) if "fiscal_year" in base and pd.notna(base.get("fiscal_year")) else None

    # ─── 营收增长 ──────────────────────────────────────────────────
    rev_seq = [_to_float(r) for r in recent["revenue"].tolist()] if "revenue" in recent.columns else []
    rev_growth = _cagr(rev_seq)
    rev_label = f"营收 {n_years}Y CAGR" if n_years >= 2 else "营收 YoY"
    if rev_growth is None:
        rev_growth = _yoy(_to_float(curr.get("revenue")), _to_float(base.get("revenue")))
        rev_label = "营收 YoY"

    rev_raw = _bracket_score(rev_growth, GROWTH_BRACKETS)
    rev_mult, rev_flags = _path_penalty(rev_seq)
    rev_score = rev_raw * rev_mult
    if rev_flags:
        rev_detail = (
            f"{_fmt_pct(rev_growth)} → {rev_raw:.0f} 分 "
            f"[路径惩罚 ×{rev_mult:.2f} → {rev_score:.1f} 分: {'/'.join(rev_flags)}]"
        )
    else:
        rev_detail = f"{_fmt_pct(rev_growth)} → {rev_score:.0f} 分"
    rev_metric = GScoreMetric(
        name=rev_label, value=rev_growth, unit="%",
        score=rev_score,
        detail=rev_detail,
    )
    dim_rev = GScoreDimension(name="营收增长", weight=0.50, score=rev_score, metrics=[rev_metric])

    # ─── 净利润增长 ────────────────────────────────────────────────
    ni_seq = [_to_float(r) for r in recent["net_income"].tolist()] if "net_income" in recent.columns else []
    ni_growth = _cagr(ni_seq)
    ni_label = f"净利润 {n_years}Y CAGR" if n_years >= 2 else "净利润 YoY"
    if ni_growth is None:
        ni_growth = _yoy(_to_float(curr.get("net_income")), _to_float(base.get("net_income")))
        ni_label = "净利润 YoY"

    ni_score = _bracket_score(ni_growth, GROWTH_BRACKETS)
    # 亏损或基期负: CAGR 不可算 → ni_growth=None → score=0 (合理: 亏损公司不能给"成长" 分)
    # 但如果基期亏损 + 当期盈利 (扭亏), 应该额外加分. 简单做: 这种情况给 60 分.
    if ni_growth is None:
        base_ni = _to_float(base.get("net_income"))
        curr_ni = _to_float(curr.get("net_income"))
        if base_ni is not None and curr_ni is not None and base_ni < 0 and curr_ni > 0:
            ni_score = 60.0
            ni_metric = GScoreMetric(
                name=ni_label, value=None, unit="%",
                score=ni_score,
                detail=f"扭亏 ({base_ni:,.0f} → {curr_ni:,.0f}) → 60 分",
            )
        else:
            ni_metric = GScoreMetric(
                name=ni_label, value=None, unit="%",
                score=0.0,
                detail="基期非正或数据缺失 → 0 分",
            )
    else:
        ni_mult, ni_flags = _path_penalty(ni_seq)
        ni_raw = ni_score
        ni_score = ni_raw * ni_mult
        if ni_flags:
            ni_detail = (
                f"{_fmt_pct(ni_growth)} → {ni_raw:.0f} 分 "
                f"[路径惩罚 ×{ni_mult:.2f} → {ni_score:.1f} 分: {'/'.join(ni_flags)}]"
            )
        else:
            ni_detail = f"{_fmt_pct(ni_growth)} → {ni_score:.0f} 分"
        ni_metric = GScoreMetric(
            name=ni_label, value=ni_growth, unit="%",
            score=ni_score,
            detail=ni_detail,
        )

    dim_ni = GScoreDimension(name="净利增长", weight=0.50, score=ni_metric.score, metrics=[ni_metric])

    # ─── 总分 ─────────────────────────────────────────────────────
    dims = [dim_rev, dim_ni]
    total = sum(d.score * d.weight for d in dims)

    return GScoreResult(
        ts_code=ts_code,
        fiscal_year_curr=fy_curr,
        fiscal_year_base=fy_base,
        n_years=n_years,
        score=total,
        dimensions=dims,
    )


# ─── 命令行 ─────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python -m tradingagents.scoring.gscore <ticker>")
        sys.exit(1)
    ticker = sys.argv[1]
    r = compute_gscore(ticker)
    if r is None:
        print(f"没有 {ticker} 的本地财报数据")
        sys.exit(2)
    print(f"\n{ticker} (ts_code: {r.ts_code})")
    print(f"G-Score: {r.score:.1f}/100 [{r.rating}]")
    print(f"对比: FY{r.fiscal_year_base} → FY{r.fiscal_year_curr} ({r.n_years} 年)\n")
    for d in r.dimensions:
        print(f"  {d.name} ({d.weight * 100:.0f}%): {d.score:.1f}/100")
        for m in d.metrics:
            print(f"    • {m.name:<18} {m.detail}")
        print()
    if r.errors:
        for e in r.errors:
            print(f"  error: {e}")
