"""Q-Score (Quality Score) — 纯 Quality 因子, 绝对盈利水平 + 财务健康度.

跟 F-Score / G-Score / V-Score 的位置
──────────────────────────────────────
F-Score: 9 个布尔信号, 看"改善 trend" (今年 ROA > 去年?). 距离破产线的距离感.
Q-Score: 看 **绝对盈利能力 + 财务质量**. 净利率/毛利率/ROE/ROA/资产负债率
         按阈值打分加权 0-100. 茅台 Q≈85 (高利润王), 亏损周期股 Q≈25.
G-Score: 看 **增长**. 营收/净利润 CAGR (独立模块 gscore.py)
V-Score: 看 **估值**. PE/PB/PEG (独立模块 vscore.py)

业界出处: 类似 MSCI Quality Index, Fama-French RMW (profitability factor),
S&P Quality Score. 这套 Quality 因子学术上明确**不包含 Growth**, 是独立因子.
所以 Q-Score 也只看 Quality, 不看增长.

三大维度
─────────
A. 盈利能力 40% — 净利率/毛利率/ROE 三指标均值
B. 回报率   30% — ROA / (OCF/总资产) 两指标均值
C. 财务健康 30% — 资产负债率/流动比率/现金流质量 三指标均值

数据来源
─────────
sh_quant ~/.market_data/financials/<ts_code>.parquet (跟 F-Score 同源).
跟 fscore.py 共用 normalize_ts_code + _financial_path.
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
class QScoreMetric:
    """单条指标 (e.g. 净利率) 的打分明细."""
    name: str        # "净利率"
    value: Optional[float]  # 25.34 (单位见 unit)
    unit: str        # "%", "x", "" 等
    score: float     # 0-100 (该指标自身的分)
    detail: str      # "25.34% → 100 分 (≥25%)"


@dataclass
class QScoreDimension:
    """单个维度 (e.g. 盈利能力)."""
    name: str
    weight: float    # 0.30
    score: float     # 0-100 (该维度自身的分)
    metrics: list[QScoreMetric] = field(default_factory=list)


@dataclass
class QScoreResult:
    ts_code: str
    fiscal_year: Optional[int]
    score: float                                          # 0-100 加权总分
    as_of: str = ""        # 展示用期间标签, e.g. "TTM 截至 2026-03-31" / "FY2025 年报"
    basis: str = "ttm"     # "ttm" = 滚动 12 个月 (默认); "annual" = 回退最近年报
    dimensions: list[QScoreDimension] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def rating(self) -> str:
        """0-100 分映射到 5 档 rating."""
        s = self.score
        if s >= 80:
            return "优质"
        if s >= 60:
            return "良好"
        if s >= 40:
            return "一般"
        if s >= 20:
            return "偏弱"
        return "较差"

    def to_dict(self) -> dict:
        return {
            "ts_code": self.ts_code,
            "fiscal_year": self.fiscal_year,
            "as_of": self.as_of,
            "basis": self.basis,
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


# ─── 通用打分映射 ────────────────────────────────────────────────────────
def _bracket_score(value: Optional[float], brackets: list[tuple[float, float]], reverse: bool = False) -> float:
    """把数值按阈值映射到 0-100.

    brackets: [(threshold, score), ...] 按 threshold **降序** 排.
              第一个 threshold 是"最高档", 命中给对应 score.
              最后一个隐含 (-inf, fallback_score).
    reverse:  True 表示"越小越好" (e.g. 资产负债率). 内部把 value 取反再比.
    """
    if value is None or pd.isna(value):
        return 0.0
    v = -value if reverse else value
    adjusted = [(-t if reverse else t, s) for t, s in brackets]
    for threshold, sc in adjusted:
        if v >= threshold:
            return float(sc)
    # 全部不达标, 用最后一档下限
    return 0.0


def _fmt_value(v: Optional[float], unit: str) -> str:
    if v is None or pd.isna(v):
        return "—"
    if unit == "%":
        return f"{v:.2f}%"
    if unit == "x":
        return f"{v:.2f}x"
    return f"{v:.2f}"


def _to_float(x) -> Optional[float]:
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


# ─── TTM (滚动 12 个月) 流量 ─────────────────────────────────────────────
# 为什么不直接读 parquet 的 roe/roa/net_margin 列:
#   A 股 (.SZ/.SH/.BJ) 的 revenue/net_income/operating_cf 是**财年内 YTD 累计**
#   (Q1=1季, Q2=半年, Q3=三季, Q4=全年). 那几个比率列同样是按行 YTD 算的,
#   年中拿最新季会拿到"被压低的半年/三季值" (e.g. Q1 ROE 只有 1 季利润 ≈ 2%,
#   不是公司真实水平). 美股 (.US) 反过来是**单季离散**, 4 季直接相加才是 TTM.
#   所以这里按市场分支拼出滚动 12 个月流量, 再由调用方自己算比率.
_FLOW_COLS = ("revenue", "gross_profit", "net_income", "operating_cf")


def _is_discrete_market(ts_code: str) -> bool:
    """美股财报是单季离散; A 股/港股是财年内 YTD 累计."""
    return ts_code.upper().endswith(".US")


def _row_flows(row) -> dict:
    return {c: _to_float(row.get(c)) for c in _FLOW_COLS}


def _ttm_flows(df: pd.DataFrame, ts_code: str):
    """算最近滚动 12 个月的流量 (revenue/gross_profit/net_income/operating_cf).

    返回 (ttm: dict|None, cur_row, as_of_label: str). ttm 为 None 表示历史
    不足以拼 TTM, 调用方应回退到最近年报.

    - 美股 (离散): TTM = 最近 4 个季度行直接相加 (需 ≥4 行).
    - A 股/港股 (累计): cur 是 Q4 → 本身就是滚动 12m, 直接用;
      cur 是 Q1/Q2/Q3 → TTM = 上一完整财年(Q4) + 本年至今(cur) − 去年同期.
    """
    d = df.copy()
    if "end_date" in d.columns:
        d["end_date"] = pd.to_datetime(d["end_date"], errors="coerce")
        d = d.dropna(subset=["end_date"]).sort_values("end_date")
    if len(d) == 0:
        return None, None, ""

    cur = d.iloc[-1]
    end_date = cur.get("end_date")
    as_of = f"TTM 截至 {pd.to_datetime(end_date):%Y-%m-%d}" if pd.notna(end_date) else "TTM"

    if _is_discrete_market(ts_code):
        if len(d) < 4:
            return None, cur, as_of
        last4 = d.tail(4)
        ttm = {c: 0.0 for c in _FLOW_COLS}
        for _, r in last4.iterrows():
            for c in _FLOW_COLS:
                v = _to_float(r.get(c))
                if v is None:
                    ttm[c] = None if ttm[c] is None else None
                elif ttm[c] is not None:
                    ttm[c] += v
        return ttm, cur, as_of

    # A 股/港股: YTD 累计口径
    period = str(cur.get("period")) if pd.notna(cur.get("period")) else ""
    if period == "Q4":
        # 全年报本身就是滚动 12 个月
        return _row_flows(cur), cur, as_of

    fy = cur.get("fiscal_year")
    if fy is None or pd.isna(fy):
        return None, cur, as_of
    fy = int(fy)
    prev_fy = fy - 1
    prev_q4 = d[(d.get("period") == "Q4") & (d["fiscal_year"] == prev_fy)]
    prev_same = d[(d.get("period") == period) & (d["fiscal_year"] == prev_fy)]
    if len(prev_q4) == 0 or len(prev_same) == 0:
        return None, cur, as_of

    f_cur = _row_flows(cur)
    f_pq4 = _row_flows(prev_q4.iloc[-1])
    f_psame = _row_flows(prev_same.iloc[-1])
    ttm = {}
    for c in _FLOW_COLS:
        a, b, e = f_pq4[c], f_cur[c], f_psame[c]
        ttm[c] = None if (a is None or b is None or e is None) else (a + b - e)
    return ttm, cur, as_of


# ─── 各指标的阈值表 (业界经验值, 见模块顶端表格) ─────────────────────────
# 格式: [(threshold, score), ...] 降序; ≥threshold 给 score.
NET_MARGIN_BRACKETS  = [(25, 100), (20, 85), (15, 70), (10, 55), (5, 35), (0, 15)]
GROSS_MARGIN_BRACKETS = [(60, 100), (40, 85), (30, 65), (20, 45), (10, 25)]
ROE_BRACKETS         = [(25, 100), (20, 85), (15, 70), (10, 50), (5, 30)]
GROWTH_BRACKETS      = [(30, 100), (20, 85), (10, 60), (5, 40), (0, 20)]  # YoY 或 CAGR %
ROA_BRACKETS         = [(15, 100), (10, 80), (7, 60), (4, 40), (0, 15)]
OCF_RETURN_BRACKETS  = [(15, 100), (10, 80), (5, 60), (2, 40)]
# 资产负债率 越小越好, reverse=True. brackets 仍然写"小的命中给高分":
# ≤30 → 100, ≤45 → 80, ≤60 → 60, ≤75 → 35, 其他 (>75) → 10
DEBT_RATIO_BRACKETS  = [(30, 100), (45, 80), (60, 60), (75, 35)]
CURRENT_RATIO_BRACKETS = [(2.0, 100), (1.5, 80), (1.2, 60), (1.0, 35)]
CFO_QUALITY_BRACKETS = [(1.2, 100), (0.8, 80), (0.5, 50), (0, 20)]


# ─── 主入口 ─────────────────────────────────────────────────────────────
def compute_qscore(ticker: str) -> Optional[QScoreResult]:
    """对单只股票算 Q-Score (绝对质量分 0-100).

    口径: 默认用**最近滚动 12 个月 (TTM)** 的财务比率 — 比最近年报新, 滞后
    只 ~1 个季度而非最多 4+ 个季度. 流量按市场分支拼 (A 股累计 / 美股离散,
    见 `_ttm_flows`), 比率全部用 TTM 流量重算, 不读 parquet 那几个会随
    季度缩水的 roe/roa/net_margin 列. 时点项 (资产负债率/流动比率/净资产/
    总资产) 取最新季快照. TTM 历史不足时回退最近年报 (basis="annual").
    """
    ts_code = normalize_ts_code(ticker)
    fp = _financial_path(ts_code)
    if not fp.exists():
        logger.info("Q-Score: 没有本地财报 parquet %s", fp)
        return None

    df = pd.read_parquet(fp)
    if df is None or len(df) == 0:
        return None

    errors: list[str] = []
    ttm, cur, as_of = _ttm_flows(df, ts_code)

    if ttm is not None and cur is not None:
        # ─── TTM 路径: 比率全部用滚动 12m 流量重算 ──────────────────
        basis = "ttm"
        fy = int(cur["fiscal_year"]) if pd.notna(cur.get("fiscal_year")) else None
        ta = _to_float(cur.get("total_assets"))           # 时点
        equity = _to_float(cur.get("total_equity"))       # 时点
        ttm_rev, ttm_gp = ttm["revenue"], ttm["gross_profit"]
        ttm_ni, ttm_ocf = ttm["net_income"], ttm["operating_cf"]

        net_margin = (ttm_ni / ttm_rev * 100.0) if (ttm_ni is not None and ttm_rev and ttm_rev > 0) else None
        gross_margin = (ttm_gp / ttm_rev * 100.0) if (ttm_gp is not None and ttm_rev and ttm_rev > 0) else None
        roe = (ttm_ni / equity * 100.0) if (ttm_ni is not None and equity and equity > 0) else None
        roa = (ttm_ni / ta * 100.0) if (ttm_ni is not None and ta and ta > 0) else None
        ocf_return = (ttm_ocf / ta * 100.0) if (ttm_ocf is not None and ta and ta > 0) else None
        cfo_quality = _safe_div(ttm_ocf, ttm_ni)          # OCF / NI 倍数 (均 TTM)
        debt_ratio = _to_float(cur.get("debt_to_equity")) # 时点 (实为总负债/总资产*100)
        if debt_ratio is None or debt_ratio < 0:
            tl = _to_float(cur.get("total_liabilities"))
            debt_ratio = (tl / ta * 100.0) if (tl is not None and ta and ta > 0) else None
        current_ratio = _to_float(cur.get("current_ratio"))  # 时点
    else:
        # ─── 回退: TTM 历史不足, 用最近完整财年 (period == 'Q4') ────
        basis = "annual"
        annual = df[df["period"] == "Q4"].copy() if "period" in df.columns else df.copy()
        if "fiscal_year" in annual.columns:
            annual = annual.dropna(subset=["fiscal_year"]).sort_values("fiscal_year")
        elif "end_date" in annual.columns:
            annual["end_date"] = pd.to_datetime(annual["end_date"], errors="coerce")
            annual = annual.dropna(subset=["end_date"]).sort_values("end_date")
        if len(annual) < 1:
            return QScoreResult(
                ts_code=ts_code, fiscal_year=None, score=0.0,
                as_of="", basis="annual",
                errors=["财务数据不足, Q-Score 既拼不出 TTM 也没有完整年报"],
            )
        curr = annual.iloc[-1]
        fy = int(curr["fiscal_year"]) if pd.notna(curr.get("fiscal_year")) else None
        as_of = f"FY{fy} 年报" if fy is not None else "最近年报"
        errors.append(f"TTM 历史不足, 回退最近年报 ({as_of})")
        ta = _to_float(curr.get("total_assets"))
        ocf = _to_float(curr.get("operating_cf"))
        net_margin = _to_float(curr.get("net_margin"))
        gross_margin = _to_float(curr.get("gross_margin"))
        roe = _to_float(curr.get("roe"))
        roa = _to_float(curr.get("roa"))
        ocf_return = (ocf / ta * 100.0) if (ocf is not None and ta and ta > 0) else None
        cfo_quality = _safe_div(ocf, _to_float(curr.get("net_income")))
        debt_ratio = _to_float(curr.get("debt_to_equity"))
        if debt_ratio is None or debt_ratio < 0:
            tl = _to_float(curr.get("total_liabilities"))
            debt_ratio = (tl / ta * 100.0) if (tl is not None and ta and ta > 0) else None
        current_ratio = _to_float(curr.get("current_ratio"))

    # ─── A. 盈利能力 40% ────────────────────────────────────────────
    a_metrics = [
        QScoreMetric(
            name="净利率", value=net_margin, unit="%",
            score=_bracket_score(net_margin, NET_MARGIN_BRACKETS),
            detail=f"{_fmt_value(net_margin, '%')} → {_bracket_score(net_margin, NET_MARGIN_BRACKETS):.0f} 分",
        ),
        QScoreMetric(
            name="毛利率", value=gross_margin, unit="%",
            score=_bracket_score(gross_margin, GROSS_MARGIN_BRACKETS),
            detail=f"{_fmt_value(gross_margin, '%')} → {_bracket_score(gross_margin, GROSS_MARGIN_BRACKETS):.0f} 分",
        ),
        QScoreMetric(
            name="ROE", value=roe, unit="%",
            score=_bracket_score(roe, ROE_BRACKETS),
            detail=f"{_fmt_value(roe, '%')} → {_bracket_score(roe, ROE_BRACKETS):.0f} 分",
        ),
    ]
    a_score = sum(m.score for m in a_metrics) / len(a_metrics)
    dim_a = QScoreDimension(name="盈利能力", weight=0.40, score=a_score, metrics=a_metrics)

    # ─── B. 回报率 30% (roa / ocf_return 已在上方按 basis 算好) ──────
    c_metrics = [
        QScoreMetric(
            name="ROA", value=roa, unit="%",
            score=_bracket_score(roa, ROA_BRACKETS),
            detail=f"{_fmt_value(roa, '%')} → {_bracket_score(roa, ROA_BRACKETS):.0f} 分",
        ),
        QScoreMetric(
            name="OCF/总资产", value=ocf_return, unit="%",
            score=_bracket_score(ocf_return, OCF_RETURN_BRACKETS),
            detail=f"{_fmt_value(ocf_return, '%')} → {_bracket_score(ocf_return, OCF_RETURN_BRACKETS):.0f} 分",
        ),
    ]
    c_score = sum(m.score for m in c_metrics) / len(c_metrics)
    dim_c = QScoreDimension(name="回报率", weight=0.30, score=c_score, metrics=c_metrics)

    # ─── D. 财务健康 30% (debt_ratio / current_ratio / cfo_quality 已在上方算好) ──
    # 资产负债率: sh_quant 的 debt_to_equity 列实际存的是 (总负债/总资产)*100,
    # 见 pull_financials.py docstring. 列名是历史命名错位; 当 0-100% 用. 时点项.
    d_metrics = [
        QScoreMetric(
            name="资产负债率", value=debt_ratio, unit="%",
            score=_bracket_score(debt_ratio, DEBT_RATIO_BRACKETS, reverse=True),
            detail=f"{_fmt_value(debt_ratio, '%')} → {_bracket_score(debt_ratio, DEBT_RATIO_BRACKETS, reverse=True):.0f} 分",
        ),
        QScoreMetric(
            name="流动比率", value=current_ratio, unit="x",
            score=_bracket_score(current_ratio, CURRENT_RATIO_BRACKETS),
            detail=f"{_fmt_value(current_ratio, 'x')} → {_bracket_score(current_ratio, CURRENT_RATIO_BRACKETS):.0f} 分",
        ),
        QScoreMetric(
            name="OCF/净利润", value=cfo_quality, unit="x",
            score=_bracket_score(cfo_quality, CFO_QUALITY_BRACKETS),
            detail=f"{_fmt_value(cfo_quality, 'x')} → {_bracket_score(cfo_quality, CFO_QUALITY_BRACKETS):.0f} 分",
        ),
    ]
    d_score = sum(m.score for m in d_metrics) / len(d_metrics)
    dim_d = QScoreDimension(name="财务健康", weight=0.30, score=d_score, metrics=d_metrics)

    # ─── 总分 (A 40% + C 30% + D 30%, B 已剥离到 gscore.py) ────────
    dims = [dim_a, dim_c, dim_d]
    total = sum(d.score * d.weight for d in dims)

    return QScoreResult(
        ts_code=ts_code,
        fiscal_year=fy,
        score=total,
        as_of=as_of,
        basis=basis,
        dimensions=dims,
        errors=errors,
    )


# ─── 命令行 (调试用) ─────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print("usage: python -m tradingagents.scoring.qscore <ticker>")
        sys.exit(1)

    ticker = sys.argv[1]
    r = compute_qscore(ticker)
    if r is None:
        print(f"没有 {ticker} 的本地财报数据")
        sys.exit(2)

    print(f"\n{ticker} (ts_code: {r.ts_code}) — {r.as_of} [{r.basis}]")
    print(f"Q-Score: {r.score:.1f}/100 [{r.rating}]\n")
    for d in r.dimensions:
        print(f"  {d.name} ({d.weight * 100:.0f}%): {d.score:.1f}/100")
        for m in d.metrics:
            print(f"    • {m.name:<14} {m.detail}")
        print()
    if r.errors:
        print("errors:")
        for e in r.errors:
            print(f"  - {e}")
