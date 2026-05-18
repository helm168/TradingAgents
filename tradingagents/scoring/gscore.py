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

路径质量风险 (不改分数, 只提示)
───────────────────────────────
两点 CAGR 只看首尾, 会把"中途巨亏 + 周期顶点"完全抹平: 佰维存储 688525
FY2023 巨亏 -6.3 亿, 但 FY2022(0.71 亿)→FY2025(8.4 亿) 两点净利 CAGR 仍
127% → 误判高增长. 这里**不改分数** (G-Score 要跨股票可比, 不能掺主观惩罚),
而是识别路径风险旗标写进 risk_note, 由前端在 G-Score 卡上显示一行红字提示.
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
    # 路径质量风险旗标 — 不影响 score, 仅当两点 CAGR 可能虚高时给一句红字提示
    risk_note: Optional[str] = None

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
            "risk_note": self.risk_note,
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


def _fmt_amt(v: Optional[float]) -> str:
    """金额 (元) → 亿 紧凑显示, 给路径旗标里点名某年用."""
    if v is None or pd.isna(v):
        return "—"
    return f"{v / 1e8:,.2f}亿"


def _fy(y: Optional[int]) -> str:
    return f"FY{y}" if y is not None else "某年"


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


# 路径质量旗标: 两点 CAGR 只看首尾, 中途巨亏 / 畸小基数 / 非单调都被抹平.
# 这里只**识别**问题 (不改分数), 让前端给一行红字提示用户.
PATH_MIN_BASE_FRAC = 0.15


def _path_flags(
    seq: list[Optional[float]],
    years: list[Optional[int]],
) -> list[str]:
    """识别算 CAGR 的年度序列的路径质量问题, 返回中文旗标列表.

    每个旗标都点名是**哪一年** + 当年金额, 不再只说"有亏损年". `years` 与
    `seq` 同序等长 (调用方按 recent 行对齐传入). 干净复利序列
    (逐年涨 / 基数不畸小 / 无亏损年) → []; 否则列出问题.
    """
    pairs = [
        (y, v)
        for y, v in zip(years, seq)
        if v is not None and not pd.isna(v)
    ]
    if len(pairs) < 2:
        return []
    vals = [v for _, v in pairs]
    base_y, base = pairs[0]
    peak, lo = max(vals), min(vals)
    flags: list[str] = []

    loss = [(y, v) for y, v in pairs if v <= 0]
    if loss:
        flags.append(
            "亏损年(" + " · ".join(f"{_fy(y)} {_fmt_amt(v)}" for y, v in loss) + ")"
        )
    if base <= 0:
        flags.append(f"基期为负({_fy(base_y)} {_fmt_amt(base)})")
    elif peak > 0 and base < PATH_MIN_BASE_FRAC * peak:
        flags.append(
            f"基数畸小({_fy(base_y)} {_fmt_amt(base)}, 仅峰值 {base / peak * 100:.0f}%)"
        )

    # 非单调: 点名回落最狠的那一年 (从前一年 → 该年)
    worst = None  # (跌入年, 跌幅, 前值, 后值)
    for i in range(len(pairs) - 1):
        drop = pairs[i][1] - pairs[i + 1][1]
        if drop > 0 and (worst is None or drop > worst[1]):
            worst = (pairs[i + 1][0], drop, pairs[i][1], pairs[i + 1][1])
    if worst:
        y2, _, a, b = worst
        flags.append(f"非单调({_fy(y2)} {_fmt_amt(a)}→{_fmt_amt(b)})")
    return flags


def _build_risk_note(rev_seq, ni_seq, years) -> Optional[str]:
    """把营收/净利两条腿的路径旗标拼成一行红字提示; 都干净则 None."""
    bits = []
    rf = _path_flags(rev_seq, years)
    nf = _path_flags(ni_seq, years)
    if rf:
        bits.append("营收 CAGR " + " · ".join(rf))
    if nf:
        bits.append("净利 CAGR " + " · ".join(nf))
    if not bits:
        return None
    return (
        "⚠ 路径存疑：" + " ｜ ".join(bits)
        + " —— 两点 CAGR 只看首尾, 中途巨亏/畸小基数被抹平, 分数偏乐观,"
        " 结合周期位置与 V-Score 估值看"
    )


# ─── 主入口 ─────────────────────────────────────────────────────────────
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

    # 路径旗标要点名"哪一年", 这里拿 recent 行对齐的财年序列 (与 *_seq 同序等长)
    years = (
        [int(y) if pd.notna(y) else None for y in recent["fiscal_year"].tolist()]
        if "fiscal_year" in recent.columns
        else [None] * len(recent)
    )

    # ─── 营收增长 ──────────────────────────────────────────────────
    rev_seq = [_to_float(r) for r in recent["revenue"].tolist()] if "revenue" in recent.columns else []
    rev_growth = _cagr(rev_seq)
    rev_label = f"营收 {n_years}Y CAGR" if n_years >= 2 else "营收 YoY"
    if rev_growth is None:
        rev_growth = _yoy(_to_float(curr.get("revenue")), _to_float(base.get("revenue")))
        rev_label = "营收 YoY"

    rev_score = _bracket_score(rev_growth, GROWTH_BRACKETS)
    rev_metric = GScoreMetric(
        name=rev_label, value=rev_growth, unit="%",
        score=rev_score,
        detail=f"{_fmt_pct(rev_growth)} → {rev_score:.0f} 分",
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
        ni_metric = GScoreMetric(
            name=ni_label, value=ni_growth, unit="%",
            score=ni_score,
            detail=f"{_fmt_pct(ni_growth)} → {ni_score:.0f} 分",
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
        risk_note=_build_risk_note(rev_seq, ni_seq, years),
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
    if r.risk_note:
        print(f"  {r.risk_note}\n")
    if r.errors:
        for e in r.errors:
            print(f"  error: {e}")
