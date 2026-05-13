"""
efinance vendor —— 东方财富数据源封装，覆盖美股/港股/A 股。

为什么用 efinance:
- 免费、不需要 API key、不限速（公开接口）
- 港股、A 股数据质量比 yfinance 好，覆盖更全
- 美股也支持，但建议美股仍走 yfinance（yfinance 的美股 fundamentals/news 更全）

输出格式严格对齐 y_finance.py：返回 CSV 字符串，列名 Open/High/Low/Close/Volume，
这样下游 stockstats 计算和 LLM agent 都不用改。
"""

from datetime import datetime, timedelta
from typing import Annotated, Optional

import pandas as pd

try:
    import efinance as ef
except ImportError as e:
    raise ImportError(
        "efinance 未安装，请先 `pip install efinance`，或在 config 里换用 yfinance/alpha_vantage"
    ) from e


# ---------- ticker 格式转换 ----------
def normalize_for_efinance(symbol: str) -> str:
    """
    把用户/yfinance 风格的 ticker 转成 efinance 接受的格式。

    yfinance 格式            efinance 格式
    -----------------       -----------------
    NVDA                ->  NVDA            (美股不变)
    0981.HK             ->  00981           (港股: 去后缀 + 补 5 位)
    600519.SS           ->  600519          (A 股沪市: 去后缀)
    000001.SZ           ->  000001          (A 股深市: 去后缀)
    """
    s = symbol.strip().upper()

    if s.endswith(".HK"):
        # 港股：4 位补到 5 位
        core = s[:-3]
        return core.zfill(5)
    if s.endswith(".SS") or s.endswith(".SH"):
        return s[:-3]
    if s.endswith(".SZ"):
        return s[:-3]
    # 没后缀，原样返回（美股、或本来就是裸代码的中港股）
    return s


def _fetch_ohlcv_df(symbol: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """从 efinance 拉 OHLC 并标准化列名，返回 DataFrame 或 None。"""
    code = normalize_for_efinance(symbol)
    beg = start_date.replace("-", "")
    end = end_date.replace("-", "")
    df = ef.stock.get_quote_history(code, beg=beg, end=end)
    if df is None or len(df) == 0:
        return None
    df = df.rename(columns={
        "日期": "Date",
        "开盘": "Open",
        "收盘": "Close",
        "最高": "High",
        "最低": "Low",
        "成交量": "Volume",
        "成交额": "Amount",
        "涨跌幅": "PctChg",
        "换手率": "Turnover",
    })
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date").sort_index()
    return df


# ---------- 1. OHLC 主接口（对应 get_YFin_data_online） ----------
def get_efinance_data_online(
    symbol: Annotated[str, "ticker symbol of the company"],
    start_date: Annotated[str, "Start date in yyyy-mm-dd format"],
    end_date: Annotated[str, "End date in yyyy-mm-dd format (inclusive)"],
) -> str:
    """拉 OHLC 数据，返回 CSV 字符串。end_date 是 INCLUSIVE 的（efinance 本来就这样）。"""
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    df = _fetch_ohlcv_df(symbol, start_date, end_date)
    if df is None or len(df) == 0:
        return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"

    # 保留主要列，对齐 yfinance 输出
    cols = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in df.columns]
    out = df[cols].round(4)
    csv_string = out.to_csv()

    header = (
        f"# Stock data for {symbol.upper()} (efinance code: {normalize_for_efinance(symbol)}) "
        f"from {start_date} to {end_date}\n"
        f"# Total records: {len(out)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + csv_string


# ---------- 2. fundamentals（对应 get_yfinance_fundamentals） ----------
def get_efinance_fundamentals(
    ticker: Annotated[str, "ticker symbol of the company"],
    curr_date: Annotated[str, "current date (unused)"] = None,
) -> str:
    """
    返回公司基本面信息的简要 markdown。efinance 对 A 股财务数据最全，
    对港股/美股覆盖有限——拉不到时会优雅返回提示，路由层会回退到 yfinance。
    """
    code = normalize_for_efinance(ticker)
    try:
        # 实时行情快照（覆盖所有市场，包含核心估值指标）
        snap_df = ef.stock.get_realtime_quotes(['全部'])
        row = snap_df[snap_df['股票代码'].astype(str) == code]
        if len(row) == 0:
            # 备选：用 latest_quotes（仅 A 股）
            try:
                snap_df = ef.stock.get_latest_quote([code])
                row = snap_df
            except Exception:
                pass

        lines = [f"# {ticker.upper()} Fundamentals (via efinance)\n"]
        if len(row) > 0:
            r = row.iloc[0]
            fields = [
                ("Name", "股票名称"),
                ("Last Price", "最新价"),
                ("PE (TTM)", "市盈率-动态"),
                ("PB", "市净率"),
                ("Market Cap", "总市值"),
                ("Float Cap", "流通市值"),
                ("Turnover", "换手率"),
                ("Volume Ratio", "量比"),
                ("Amplitude", "振幅"),
                ("52w High", "年最高"),
                ("52w Low", "年最低"),
            ]
            for label, cn in fields:
                if cn in r.index and pd.notna(r[cn]):
                    lines.append(f"- **{label}**: {r[cn]}")
        else:
            lines.append(f"⚠️ efinance 未找到 {ticker} 的实时行情快照（efinance code: {code}）。")

        return "\n".join(lines)
    except Exception as e:
        return f"efinance fundamentals 失败 ({ticker}): {e}"


# ---------- 3. 占位实现 —— 财务三大表 ----------
# A 股可以接 ef.stock.get_balance_sheet 等；港股/美股 efinance 不直接给三大表，
# 这里先返回一个明确的 "not implemented" 字符串，路由层会回退到 yfinance/alpha_vantage。

def get_efinance_balance_sheet(ticker: str, curr_date: str = None) -> str:
    code = normalize_for_efinance(ticker)
    if not _looks_like_cn_a_share(code):
        return f"efinance balance sheet 仅支持 A 股，{ticker} 请使用 yfinance/alpha_vantage"
    try:
        df = ef.stock.get_balance_sheet([code])
        return _stringify_financials(df, ticker, "Balance Sheet")
    except Exception as e:
        return f"efinance balance sheet 失败 ({ticker}): {e}"


def get_efinance_income_statement(ticker: str, curr_date: str = None) -> str:
    code = normalize_for_efinance(ticker)
    if not _looks_like_cn_a_share(code):
        return f"efinance income statement 仅支持 A 股，{ticker} 请使用 yfinance/alpha_vantage"
    try:
        df = ef.stock.get_income_statement([code])
        return _stringify_financials(df, ticker, "Income Statement")
    except Exception as e:
        return f"efinance income statement 失败 ({ticker}): {e}"


def get_efinance_cashflow(ticker: str, curr_date: str = None) -> str:
    code = normalize_for_efinance(ticker)
    if not _looks_like_cn_a_share(code):
        return f"efinance cashflow 仅支持 A 股，{ticker} 请使用 yfinance/alpha_vantage"
    try:
        df = ef.stock.get_cashflow_statement([code])
        return _stringify_financials(df, ticker, "Cash Flow")
    except Exception as e:
        return f"efinance cashflow 失败 ({ticker}): {e}"


# ---------- 工具函数 ----------
def _looks_like_cn_a_share(code: str) -> bool:
    """efinance 格式的代码是否像 A 股（6 位数字，沪市 6/5/9 开头或深市 0/3 开头）。"""
    if not code.isdigit() or len(code) != 6:
        return False
    return code[0] in ("0", "3", "6", "5", "9")


def _stringify_financials(df, ticker: str, label: str) -> str:
    if df is None or len(df) == 0:
        return f"No {label} data found for {ticker}"
    # efinance 返回的 DataFrame 行是报告期、列是科目；取最近 4 期
    out = df.head(4).to_csv(index=False)
    return f"# {ticker.upper()} {label} (recent 4 periods, via efinance)\n\n{out}"
