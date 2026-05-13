"""
验证 efinance 数据源是否好用，并与 yfinance 对比。

测试标的：
  - 0981.HK 中芯国际 SMIC（港股，验证港股数据准不准）
  - NVDA 英伟达（美股，对照组）
  - 600519 贵州茅台（A 股，看 efinance 的 A 股表现）

用法：
    source .venv/bin/activate
    pip install efinance
    python test_efinance.py
"""
from datetime import datetime, timedelta

import pandas as pd

try:
    import efinance as ef
except ImportError:
    raise SystemExit("efinance 没装，请先：pip install efinance")

import yfinance as yf


# ---------- 配置 ----------
END_DATE = datetime.now()
START_DATE = END_DATE - timedelta(days=30)  # 拉最近 30 天，足够看趋势对不对

# (中文名, efinance 代码, yfinance 代码)
TICKERS = [
    ("中芯国际 SMIC",  "00981",  "0981.HK"),
    ("英伟达 NVDA",    "NVDA",   "NVDA"),
    ("贵州茅台",       "600519", "600519.SS"),
]

# ---------- 辅助 ----------
def fmt_d(d, dash=True):
    return d.strftime("%Y-%m-%d") if dash else d.strftime("%Y%m%d")


def fetch_efinance(code):
    df = ef.stock.get_quote_history(
        code,
        beg=fmt_d(START_DATE, dash=False),
        end=fmt_d(END_DATE, dash=False),
    )
    if df is None or len(df) == 0:
        return None
    df = df.rename(columns={
        "日期": "Date", "开盘": "Open", "收盘": "Close",
        "最高": "High", "最低": "Low",  "成交量": "Volume",
        "涨跌幅": "PctChg",
    })
    df["Date"] = pd.to_datetime(df["Date"])
    df = df.set_index("Date")
    return df[["Open", "High", "Low", "Close", "Volume", "PctChg"]]


def fetch_yfinance(code):
    df = yf.Ticker(code).history(
        start=fmt_d(START_DATE), end=fmt_d(END_DATE),
    )
    if df is None or len(df) == 0:
        return None
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    df = df.rename_axis("Date")
    return df[["Open", "High", "Low", "Close", "Volume"]].round(2)


def show_tail(name, df, n=5):
    if df is None or len(df) == 0:
        print(f"  [{name}] 无数据")
        return
    print(f"  [{name}] 共 {len(df)} 条，最近 {n} 个交易日：")
    cols = ["Open", "High", "Low", "Close", "Volume"]
    tail = df[cols].tail(n).copy()
    tail.index = tail.index.strftime("%Y-%m-%d")
    print(tail.to_string())
    last = df.iloc[-1]
    print(f"    -> 最新 {df.index[-1].strftime('%Y-%m-%d')} 收盘: {round(last['Close'], 2)}")


def diff_latest(df_ef, df_yf):
    if df_ef is None or df_yf is None:
        return None
    # 找两边都有的最新日期
    common = df_ef.index.intersection(df_yf.index)
    if len(common) == 0:
        return None
    d = common.max()
    c_ef = float(df_ef.loc[d, "Close"])
    c_yf = float(df_yf.loc[d, "Close"])
    pct = abs(c_ef - c_yf) / c_yf * 100 if c_yf else float("inf")
    return d, c_ef, c_yf, pct


# ---------- 主流程 ----------
def main():
    print("=" * 70)
    print(f"日期范围: {fmt_d(START_DATE)} → {fmt_d(END_DATE)}")
    print("=" * 70)

    for cn_name, ef_code, yf_code in TICKERS:
        print(f"\n>>> {cn_name}   efinance={ef_code}   yfinance={yf_code}")
        try:
            df_ef = fetch_efinance(ef_code)
        except Exception as e:
            df_ef = None
            print(f"  efinance 报错: {e}")
        try:
            df_yf = fetch_yfinance(yf_code)
        except Exception as e:
            df_yf = None
            print(f"  yfinance 报错: {e}")

        show_tail("efinance", df_ef)
        show_tail("yfinance", df_yf)

        diff = diff_latest(df_ef, df_yf)
        if diff:
            d, c_ef, c_yf, pct = diff
            tag = "  一致" if pct < 1 else (" 轻微差异" if pct < 5 else " 差异较大")
            print(f"  共同最新交易日 {d.strftime('%Y-%m-%d')}: "
                  f"ef={c_ef}, yf={c_yf}, 偏差={pct:.2f}% {tag}")
        else:
            print("  无法对比（其中一边没数据）")

    print("\n" + "=" * 70)
    print("结论判读：")
    print("  - 偏差 < 1%   → 两个源一致，yfinance 不是数据源问题")
    print("  - 偏差 1-5%  → 复权方式或货币换算差异，可接受")
    print("  - 偏差 > 5%  → 至少一个源数据有问题，建议切到 efinance")
    print("=" * 70)


if __name__ == "__main__":
    main()

