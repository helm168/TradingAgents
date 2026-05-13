"""
冒烟测试 —— 不调 LLM，只验证：
  1. yfinance end_date +1 修复
  2. efinance vendor 能拉港股/A 股
  3. interface.py 的 auto 路由能按 ticker 后缀正确分发
  4. stockstats_utils.load_ohlcv 能用 efinance 缓存
  5. scoring 模块能从已有报告里提取最终评级

用法：
    source .venv/bin/activate
    python test_smoke.py
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def check(label, cond, detail=""):
    status = "✓" if cond else "✗"
    print(f"  {status} {label}" + (f"  ({detail})" if detail else ""))
    return cond


# ---------- 1. yfinance end_date 修复 ----------
def test_yfinance_end_date_fix():
    section("[1] yfinance end_date +1 fix")
    from tradingagents.dataflows.y_finance import get_YFin_data_online

    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    data = get_YFin_data_online("NVDA", start, end)
    has_data = "No data found" not in data and "Total records:" in data
    check("NVDA 拉数据成功", has_data)
    # 检查 header 里的 records 数
    if has_data:
        lines = data.split("\n")
        for ln in lines[:3]:
            print(f"    {ln}")


# ---------- 2. efinance vendor ----------
def test_efinance_vendor():
    section("[2] efinance vendor 直接调用")
    try:
        from tradingagents.dataflows.efinance_stock import (
            get_efinance_data_online,
            normalize_for_efinance,
        )
    except ImportError as e:
        check("efinance 模块导入", False, str(e))
        return False

    # ticker 格式转换
    cases = [
        ("0981.HK", "00981"),
        ("600519.SS", "600519"),
        ("000001.SZ", "000001"),
        ("NVDA", "NVDA"),
    ]
    for inp, expected in cases:
        got = normalize_for_efinance(inp)
        check(f"normalize {inp} → {expected}", got == expected, f"got {got}")

    # 实际拉数据
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    for ticker in ["0981.HK", "600519.SS"]:
        out = get_efinance_data_online(ticker, start, end)
        has = "No data found" not in out and "Total records:" in out
        check(f"efinance 拉 {ticker}", has)
        if has:
            print(f"    {out.split(chr(10))[1]}")
    return True


# ---------- 3. interface.route_to_vendor + auto ----------
def test_auto_routing():
    section("[3] auto 路由按 ticker 自动分发")
    from tradingagents.dataflows import interface
    from tradingagents.dataflows.config import set_config, get_config

    # 配置成 auto
    cfg = get_config()
    set_config({
        **cfg,
        "data_vendors": {
            "core_stock_apis": "auto",
            "technical_indicators": "auto",
            "fundamental_data": "auto",
            "news_data": "auto",
        }
    })

    # 验证 auto 解析
    args_hk = ("0981.HK", "2026-05-01", "2026-05-08")
    chain_hk = interface._resolve_auto("get_stock_data", args_hk, {})
    check("0981.HK 路由首选 efinance",
          chain_hk[0] == "efinance",
          f"chain={chain_hk}")

    args_us = ("NVDA", "2026-05-01", "2026-05-08")
    chain_us = interface._resolve_auto("get_stock_data", args_us, {})
    check("NVDA 路由首选 yfinance",
          chain_us[0] == "yfinance",
          f"chain={chain_us}")

    # 实际跑一次端到端
    end = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    try:
        result = interface.route_to_vendor("get_stock_data", "0981.HK", start, end)
        ok = isinstance(result, str) and "Total records:" in result
        check("route_to_vendor(0981.HK) 端到端", ok)
        if ok:
            print(f"    {result.split(chr(10))[0]}")
    except Exception as e:
        check("route_to_vendor(0981.HK) 端到端", False, str(e))


# ---------- 4. load_ohlcv 切换 vendor ----------
def test_load_ohlcv_vendor():
    section("[4] stockstats_utils.load_ohlcv 自动选 vendor")
    from tradingagents.dataflows.stockstats_utils import _pick_ohlcv_vendor, load_ohlcv

    cases = [
        ("NVDA", "yfinance"),
        ("0981.HK", "efinance"),
        ("600519.SS", "efinance"),
        ("000001.SZ", "efinance"),
    ]
    for ticker, expected in cases:
        got = _pick_ohlcv_vendor(ticker)
        check(f"{ticker} → {expected}", got == expected, f"got {got}")

    # 实际 load 一只港股
    today = datetime.now().strftime("%Y-%m-%d")
    try:
        df = load_ohlcv("0981.HK", today)
        check("load_ohlcv('0981.HK') 返回 DataFrame",
              df is not None and len(df) > 0,
              f"rows={len(df) if df is not None else 0}")
    except Exception as e:
        check("load_ohlcv('0981.HK')", False, str(e))


# ---------- 5. scoring 模块（不调 LLM，只测评级提取） ----------
def test_scoring_no_llm():
    section("[5] scoring._extract_final_rating（无 LLM）")
    from tradingagents.scoring.score_extractor import _extract_final_rating

    reports_root = Path("reports")
    sample = next((p for p in reports_root.glob("NVDA_*")
                   if (p / "5_portfolio" / "decision.md").exists()), None)
    if not sample:
        check("找到 NVDA 样例报告", False, "请先跑过一次 NVDA")
        return
    check("找到 NVDA 样例报告", True, str(sample))

    rating = _extract_final_rating(sample)
    check("能从 decision.md 抠出评级",
          rating in ("Buy", "Overweight", "Hold", "Underweight", "Sell"),
          f"rating={rating}")


# ---------- 主 ----------
if __name__ == "__main__":
    print(f"冒烟测试开始 - {datetime.now()}")
    try:
        test_yfinance_end_date_fix()
        test_efinance_vendor()
        test_auto_routing()
        test_load_ohlcv_vendor()
        test_scoring_no_llm()
        print("\n" + "=" * 70)
        print("全部冒烟测试完毕")
        print("=" * 70)
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)
