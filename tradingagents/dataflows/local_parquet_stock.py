"""local_parquet vendor —— 从 sh_quant data_cache 直接读 parquet.

为什么需要这个 vendor
─────────────────────
原本 TradingAgents 所有数据走 yfinance / Polygon / FMP / efinance 远程 API:
1. 每次跑都打远程, 慢 + 受限速
2. 跟 sh_quant / Billionaire 数据底座不一致 (复权口径、IPO 日期 cutoff 等)
3. 网络不稳定时整个 agent 流挂掉

加这个 vendor 后, 数据本地命中时:
- OHLC 用 stocks/<ts_code>.parquet (含 adj_factor, 自动复权)
- 财报 三大表 用 financials/<ts_code>.parquet (Tushare income/balance/cashflow,
  统一 schema, 见 sh_quant/scripts/pull_financials.py)

ticker 归一化
─────────────
TradingAgents 用的 ticker 风格跟 sh_quant 不完全一致 (跟 yfinance 习惯):
    NVDA          → NVDA.US
    600519.SS     → 600519.SH        (yfinance .SS → tushare .SH)
    000001.SZ     → 000001.SZ        (深市同)
    300750.SZ     → 300750.SZ
    0700.HK       → 00700.HK         (港股 4 位补 5 位)

Schema (跟 sh_quant pull_financials.py 对齐)
──────────────────────────────────────────────
stocks/<ts>.parquet:    trade_date, open, high, low, close, vol, adj_factor, ...
financials/<ts>.parquet: ann_date, end_date, period, fiscal_year, currency,
                         revenue, gross_profit, operating_income, net_income, ...
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


# ─── 数据根目录 ────────────────────────────────────────────────────────
def _data_root() -> Path:
    override = os.environ.get("SH_QUANT_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home() / ".market_data"


def is_available() -> bool:
    """探测 sh_quant 数据底座是否在线. interface.py 用这个决定要不要注册 vendor."""
    root = _data_root()
    return root.exists() and (root / "stocks").exists()


# ─── ticker ↔ ts_code 归一化 ─────────────────────────────────────────
_HK_RE = re.compile(r"^(\d+)\.HK$", re.IGNORECASE)


def normalize_ts_code(ticker: str) -> str:
    t = ticker.strip().upper()

    # 港股: 0700.HK / 0981.HK → 00700.HK / 00981.HK (5 位)
    hk = _HK_RE.match(t)
    if hk:
        return f"{hk.group(1).zfill(5)}.HK"

    # A 股: yfinance .SS → tushare .SH; 其余 .SZ/.SH/.BJ 同
    if t.endswith(".SS"):
        return f"{t[:-3]}.SH"
    if t.endswith((".SZ", ".SH", ".BJ", ".HK", ".US")):
        return t

    # 美股纯字母: AAPL → AAPL.US (也接 BRK-B 这种)
    if re.match(r"^[A-Z][A-Z0-9.\-]{0,11}$", t):
        return f"{t}.US"

    return t


# ─── 文件路径 ──────────────────────────────────────────────────────────
def _stock_path(ts_code: str) -> Path:
    return _data_root() / "stocks" / f"{ts_code}.parquet"


def _financial_path(ts_code: str) -> Path:
    return _data_root() / "financials" / f"{ts_code}.parquet"


# ─── OHLC ──────────────────────────────────────────────────────────────
def _read_ohlc(ts_code: str, start: str, end: str) -> Optional[pd.DataFrame]:
    fp = _stock_path(ts_code)
    if not fp.exists():
        return None
    df = pd.read_parquet(fp)
    if df is None or len(df) == 0:
        return None

    # trade_date 兼容 'YYYY-MM-DD' / 'YYYYMMDD' / Timestamp
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce")
    df = df.dropna(subset=["trade_date"])
    df = df.sort_values("trade_date").reset_index(drop=True)

    # 前复权 (qfq): close × adj_factor / latest_adj_factor.
    # 跟后复权数学等价 (ratio 不变, R²/动量/回撤都一致), 但**今天的价 = 实际市价**,
    # LLM agent 看到熟悉的 1700 而不是 11800 (后复权 茅台累积复权 7x), 不会写出
    # "严重高估" 这种幻觉判断.
    # 在窗口 filter 之前算 latest_af —— 用 parquet 里"全历史"最新的, 不是窗口里最新的.
    if "adj_factor" in df.columns:
        latest_af = df["adj_factor"].iloc[-1]
        if pd.notna(latest_af) and latest_af != 0:
            af = df["adj_factor"].fillna(latest_af) / latest_af
        else:
            af = 1.0
    else:
        af = 1.0

    s = pd.to_datetime(start)
    e = pd.to_datetime(end)
    in_window = (df["trade_date"] >= s) & (df["trade_date"] <= e)
    df = df.loc[in_window].copy()
    if len(df) == 0:
        return None

    # 把 af reindex 到窗口里 (af 是 Series 时按索引对齐, 标量直接乘)
    if isinstance(af, pd.Series):
        af = af.loc[df.index]
    for col in ("open", "high", "low", "close"):
        if col in df.columns:
            df[col] = (df[col] * af).round(4)

    df = df.reset_index(drop=True)
    return df


def get_local_parquet_data(symbol: str, start_date: str, end_date: str) -> str:
    """OHLC 输出对齐 yfinance/efinance 的 CSV+header 风格."""
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    ts_code = normalize_ts_code(symbol)
    df = _read_ohlc(ts_code, start_date, end_date)
    if df is None:
        return f"local_parquet OHLC 未找到 {symbol} ({ts_code})"

    # 列重命名对齐 yfinance: Open/High/Low/Close/Volume, 索引设为 Date
    out = df.rename(
        columns={
            "trade_date": "Date",
            "open": "Open",
            "high": "High",
            "low": "Low",
            "close": "Close",
            "vol": "Volume",
        }
    )
    cols = [c for c in ["Date", "Open", "High", "Low", "Close", "Volume"] if c in out.columns]
    out = out[cols].set_index("Date")

    header = (
        f"# Stock data for {symbol.upper()} (local_parquet ts_code: {ts_code}) "
        f"from {start_date} to {end_date}\n"
        f"# Total records: {len(out)}, 前复权 (qfq, close × adj_factor / latest_af)\n"
        f"# Source: sh_quant {_data_root()}\n\n"
    )
    return header + out.to_csv()


# ─── 财报 ──────────────────────────────────────────────────────────────
# 跟 sh_quant pull_financials.py 的 COMMON_COLS 对齐:
#   ann_date, end_date, period, fiscal_year, currency,
#   revenue, gross_profit, operating_income, pretax_income, net_income, eps_basic, eps_diluted,
#   total_assets, total_liabilities, total_equity, cash_and_equivalents, long_term_debt, short_term_debt,
#   operating_cf, investing_cf, financing_cf, free_cash_flow, capex,
#   roe, roa, gross_margin, net_margin, debt_to_equity, current_ratio


def _read_financials(ts_code: str, n_latest: int = 8) -> Optional[pd.DataFrame]:
    fp = _financial_path(ts_code)
    if not fp.exists():
        return None
    df = pd.read_parquet(fp)
    if df is None or len(df) == 0:
        return None
    df["end_date"] = pd.to_datetime(df["end_date"], errors="coerce")
    df = df.dropna(subset=["end_date"]).sort_values("end_date").tail(n_latest).reset_index(drop=True)
    return df


def _period_label(row) -> str:
    """生成不可误读的财季标签: 'FY2026 Q1 (截至 2025-11-27)'.

    解决幻觉: 美光/NVDA/AAPL 这种 fiscal year 跟 calendar year 错位的公司,
    直接展示 end_date.year 会让 LLM 把 2025-11-27 (实际是 FY2026 Q1) 标成
    "2025 Q1". 显式拼 fiscal_year 前缀消除歧义.
    """
    period = row.get("period", "?")
    fy = row.get("fiscal_year")
    end_date = row["end_date"].date() if pd.notna(row.get("end_date")) else "?"
    if pd.notna(fy):
        return f"FY{int(fy)} {period} (截至 {end_date})"
    return f"{end_date} ({period})"


def _fmt_money(x) -> str:
    """raw → 易读 string (千分位 + 单位). 大额走亿/百万."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    if pd.isna(v):
        return "—"
    if abs(v) >= 1e8:
        return f"{v / 1e8:.2f}亿"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.2f}M"
    if abs(v) >= 1e4:
        return f"{v / 1e4:.2f}万"
    return f"{v:.2f}"


def _fmt_pct(x) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return "—"
    if pd.isna(v):
        return "—"
    return f"{v:.2f}%"


def _latest_summary_md(ticker: str, ts_code: str, df: pd.DataFrame) -> str:
    """fundamentals 用的摘要 markdown: 最新季 + 同比/环比简单点评."""
    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) >= 2 else None
    yoy = None
    if len(df) >= 5:
        # 取 4 季度前的同期 (假设是连续季报)
        yoy = df.iloc[-5]

    def diff_pct(now, ref, label):
        if ref is None:
            return None
        try:
            a = float(now)
            b = float(ref)
            if pd.isna(a) or pd.isna(b) or b == 0:
                return None
            return f"{label}={(a / b - 1) * 100:.1f}%"
        except (TypeError, ValueError):
            return None

    lines = [
        f"# {ticker.upper()} Fundamentals (local_parquet ts_code: {ts_code})\n",
        # FY 前缀必须显式 — 美光/NVDA/AAPL 这种 fiscal year 错位日历年的公司,
        # 直接展示 end_date.year 会让 LLM 把 "2025-11-27" 标成 "2025 Q1",
        # 实际是 FY2026 Q1. 加 fiscal_year 列后 LLM 拿到 "FY2026 Q1 (截至
        # 2025-11-27)" 这种不可误读的标签.
        f"## 最新财季 {_period_label(latest)}",
        f"- **货币**: {latest.get('currency', '—')}",
        f"- **营业收入**: {_fmt_money(latest.get('revenue'))}",
        f"- **净利润**: {_fmt_money(latest.get('net_income'))}",
        f"- **毛利润**: {_fmt_money(latest.get('gross_profit'))}",
        f"- **经营现金流**: {_fmt_money(latest.get('operating_cf'))}",
        f"- **总资产**: {_fmt_money(latest.get('total_assets'))}",
        f"- **总负债**: {_fmt_money(latest.get('total_liabilities'))}",
        f"- **净资产**: {_fmt_money(latest.get('total_equity'))}",
        f"- **ROE (季)**: {_fmt_pct(latest.get('roe'))}",
        f"- **毛利率**: {_fmt_pct(latest.get('gross_margin'))}",
        f"- **净利率**: {_fmt_pct(latest.get('net_margin'))}",
    ]
    growth_bits = []
    rev_yoy = diff_pct(latest.get("revenue"), yoy.get("revenue") if yoy is not None else None, "营收 YoY")
    rev_qoq = diff_pct(latest.get("revenue"), prev.get("revenue") if prev is not None else None, "营收 QoQ")
    np_yoy = diff_pct(latest.get("net_income"), yoy.get("net_income") if yoy is not None else None, "净利 YoY")
    np_qoq = diff_pct(latest.get("net_income"), prev.get("net_income") if prev is not None else None, "净利 QoQ")
    growth_bits.extend(filter(None, [rev_yoy, rev_qoq, np_yoy, np_qoq]))
    if growth_bits:
        lines.append("\n## 增长")
        for g in growth_bits:
            lines.append(f"- {g}")
    lines.append(f"\n_数据源: sh_quant {_financial_path(ts_code)}_")
    return "\n".join(lines)


def get_local_parquet_fundamentals(ticker: str, curr_date: Optional[str] = None) -> str:
    ts_code = normalize_ts_code(ticker)
    df = _read_financials(ts_code, n_latest=8)
    if df is None:
        return f"local_parquet fundamentals 未找到 {ticker} ({ts_code})"
    try:
        return _latest_summary_md(ticker, ts_code, df)
    except Exception as e:
        return f"local_parquet fundamentals 失败 ({ticker}): {e}"


# ─── 三大表 (输出对齐 yfinance 风格的多季度表格) ─────────────────────
def _table_md(title: str, ticker: str, ts_code: str, df: pd.DataFrame, cols: list[tuple[str, str]]) -> str:
    """cols: [(parquet_col, display_label), ...]"""
    if df is None or len(df) == 0:
        return f"local_parquet {title} 数据为空 ({ticker})"

    header = ["Period"] + [label for _, label in cols]
    rows = []
    for _, r in df.iterrows():
        period_label = _period_label(r)
        cells = [period_label]
        for col, _ in cols:
            cells.append(_fmt_money(r.get(col)))
        rows.append(cells)

    md = [f"# {ticker.upper()} {title} (local_parquet ts_code: {ts_code})\n"]
    md.append("| " + " | ".join(header) + " |")
    md.append("|" + "---|" * len(header))
    for row in rows:
        md.append("| " + " | ".join(row) + " |")
    md.append(f"\n_数据源: sh_quant {_financial_path(ts_code)}_")
    return "\n".join(md)


def get_local_parquet_balance_sheet(ticker: str, curr_date: Optional[str] = None) -> str:
    ts_code = normalize_ts_code(ticker)
    df = _read_financials(ts_code, n_latest=8)
    if df is None:
        return f"local_parquet balance sheet 未找到 {ticker} ({ts_code})"
    cols = [
        ("total_assets", "Total Assets"),
        ("total_liabilities", "Total Liabilities"),
        ("total_equity", "Total Equity"),
        ("cash_and_equivalents", "Cash & Equiv"),
        ("long_term_debt", "Long-term Debt"),
        ("short_term_debt", "Short-term Debt"),
    ]
    return _table_md("Balance Sheet", ticker, ts_code, df, cols)


def get_local_parquet_income_statement(ticker: str, curr_date: Optional[str] = None) -> str:
    ts_code = normalize_ts_code(ticker)
    df = _read_financials(ts_code, n_latest=8)
    if df is None:
        return f"local_parquet income statement 未找到 {ticker} ({ts_code})"
    cols = [
        ("revenue", "Revenue"),
        ("gross_profit", "Gross Profit"),
        ("operating_income", "Operating Income"),
        ("pretax_income", "Pretax Income"),
        ("net_income", "Net Income"),
    ]
    return _table_md("Income Statement", ticker, ts_code, df, cols)


def get_local_parquet_cashflow(ticker: str, curr_date: Optional[str] = None) -> str:
    ts_code = normalize_ts_code(ticker)
    df = _read_financials(ts_code, n_latest=8)
    if df is None:
        return f"local_parquet cashflow 未找到 {ticker} ({ts_code})"
    cols = [
        ("operating_cf", "Operating CF"),
        ("investing_cf", "Investing CF"),
        ("financing_cf", "Financing CF"),
        ("free_cash_flow", "Free Cash Flow"),
        ("capex", "CapEx"),
    ]
    return _table_md("Cash Flow", ticker, ts_code, df, cols)
