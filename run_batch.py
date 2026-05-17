"""
批量分析多个 ticker（美股 + 港股 + A 股混合），自动按市场路由数据源，
跑完后用 LLM 对每个维度打分，输出可排序的 CSV。

用法：
    source .venv/bin/activate

    # 默认列表
    python run_batch.py

    # 命令行传 ticker（可混合美股 / 港股 / A 股）
    python run_batch.py NVDA TSLA 0981.HK 600519.SS 300750.SZ

    # 从文件读
    python run_batch.py --file tickers.txt

    # 指定日期
    python run_batch.py --date 2026-05-08 NVDA 0981.HK

    # 跳过评分（只跑分析，加速）
    python run_batch.py --no-score NVDA

    # 只评分已有报告（不重跑 agent）
    python run_batch.py --score-only NVDA TSLA

输出：
    reports/<TICKER>_<timestamp>/       每只股票的完整 agent 报告
    reports/<batch_ts>_batch_summary.json  批次汇总（含每只股票的评分和决策）
    reports/<batch_ts>_ranking.csv         可直接排序的评分表（推荐用这个看）
"""
import argparse
import csv
import json
import sys
import traceback
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.scoring import score_reports
from tradingagents.exporters import export_to_billionaire, AGENT_REPORTS_DIR
from tradingagents.llm_presets import PRESETS, apply_preset, list_presets
# CLI 那边的落盘函数 —— 把 propagate 返回的 final_state 写成
# reports/<TICKER>_<TS>/{1_analysts,2_research,...,complete_report.md} 结构.
# run_batch 之前只调 propagate 不调它, 导致 markdown 报告根本没落盘, find_latest_
# report_dir 自然找不到.
from cli.main import save_report_to_disk

load_dotenv()

# ---------- 默认配置 ----------
DEFAULT_TICKERS = [
    "NVDA",
    # "TSLA", "AAPL",
    # "0981.HK",   # 港股 - 中芯国际
    # "600519.SS", # A 股沪市 - 茅台
    # "300750.SZ", # A 股深市 - 宁德
]


def build_config(llm_preset: str = "deepseek"):
    """单次跑用的 LLM/流程配置.

    Args:
        llm_preset: tradingagents.llm_presets.PRESETS 里的 key
            - "deepseek": 单只股票约 $0.05-0.15, reasoner+chat 分档
            - "xiaomi":   小米 MiMo-V2.5-Pro, token plan 月订阅
            其它新加 provider 直接往 llm_presets.PRESETS 里塞.
    """
    config = DEFAULT_CONFIG.copy()

    # --- LLM (preset 切换 4 个字段一把搞定) ---
    apply_preset(config, llm_preset)

    # --- 流程 ---
    config["max_debate_rounds"] = 1
    config["max_risk_discuss_rounds"] = 1
    config["output_language"] = "Chinese"

    # --- 数据源：用 auto 路由，按 ticker 后缀自动选 vendor ---
    # NVDA → yfinance（美股）
    # 0981.HK / 600519.SS / 000001.SZ → efinance（中港股）
    config["data_vendors"] = {
        "core_stock_apis": "auto",
        "technical_indicators": "auto",
        "fundamental_data": "auto",
        "news_data": "auto",
    }
    return config


# ---------- 找到本次 propagate 生成的报告目录 ----------
def find_latest_report_dir(ticker: str, after_ts: float) -> Path:
    """在 reports/<TICKER>_*/ 里找时间戳最新且晚于 after_ts 的目录。"""
    reports_root = Path("reports")
    if not reports_root.exists():
        return None
    candidates = sorted(
        [p for p in reports_root.glob(f"{ticker}_*")
         if p.is_dir() and p.stat().st_mtime >= after_ts - 5],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


# ---------- 评分 LLM 客户端 ----------
def build_scoring_llm(config):
    """构造一个轻量 LLM 用于评分（用 quick_think_llm，便宜）。"""
    from tradingagents.llm_clients import create_llm_client
    client = create_llm_client(
        provider=config["llm_provider"],
        model=config["quick_think_llm"],
        base_url=config.get("backend_url"),
    )
    return client.get_llm()  # 返回 LangChain LLM 实例，支持 .invoke(prompt)


# ---------- 参数 ----------
def parse_args():
    p = argparse.ArgumentParser(description="批量跑 TradingAgents 并打分排序")
    p.add_argument("tickers", nargs="*", help="ticker 列表")
    p.add_argument("--file", help="ticker 列表文件，每行一个")
    p.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"),
                   help="分析日期 YYYY-MM-DD（默认今天）")
    p.add_argument("--llm", default="deepseek", choices=list_presets(),
                   help=f"LLM provider preset (默认 deepseek). 切换会一并改"
                        f"llm_provider / backend_url / deep_think_llm / "
                        f"quick_think_llm 4 个字段, 见 tradingagents/llm_presets.py")
    p.add_argument("--no-score", action="store_true", help="跳过 LLM 评分阶段")
    p.add_argument("--score-only", action="store_true",
                   help="只对已有报告评分（不重跑 agent）")
    return p.parse_args()


def load_tickers(args):
    if args.file:
        with open(args.file, "r", encoding="utf-8") as f:
            return [line.strip() for line in f
                    if line.strip() and not line.startswith("#")]
    return args.tickers or DEFAULT_TICKERS


# ---------- 输出 ----------
def write_ranking_csv(rows: list, path: Path):
    """把评分结果写成可排序 CSV。"""
    cols = [
        "ticker", "date",
        "technical_score", "fundamental_score",
        "sentiment_score", "news_score",
        "quadrant", "final_rating",
        "technical_stance", "fundamental_stance",
        "elapsed_seconds", "status",
    ]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


# ---------- 主流程 ----------
def main():
    args = parse_args()
    tickers = load_tickers(args)
    analysis_date = args.date
    do_score = not args.no_score
    score_only = args.score_only

    config = build_config(args.llm)

    print("=" * 70)
    print(f"批量分析 {len(tickers)} 只股票，分析日期 = {analysis_date}")
    print(f"标的: {', '.join(tickers)}")
    print(f"评分: {'开启' if do_score else '跳过'}")
    print(f"LLM : {args.llm} ({config['deep_think_llm']} + {config['quick_think_llm']})")
    print("=" * 70)
    ta = None if score_only else TradingAgentsGraph(debug=False, config=config)
    scoring_llm = None
    if do_score:
        try:
            scoring_llm = build_scoring_llm(config)
        except Exception as e:
            print(f"评分 LLM 初始化失败，跳过评分: {e}")
            do_score = False

    rows = []
    results = []
    for i, ticker in enumerate(tickers, 1):
        print(f"\n[{i}/{len(tickers)}] >>> {ticker} ({analysis_date})")
        t0 = datetime.now()
        report_dir = None
        decision_str = None
        status = "success"
        err = None

        # 1) 跑 agent
        if not score_only:
            try:
                start_ts = t0.timestamp()
                # 拿到 final_state 不要丢掉, 后面要靠它落盘 markdown.
                final_state, decision = ta.propagate(ticker, analysis_date)
                decision_str = str(decision)
                print(f"  ✓ 分析完成，用时 {(datetime.now()-t0).total_seconds():.1f}s")

                # 落盘 markdown 到 reports/<TICKER>_<YYYYMMDD_HHMMSS>/, 跟 CLI 行为一致.
                # ticker 里的 . 用 reports/ 兼容文件系统; CLI 路径已经验证过的.
                reports_root = Path("reports")
                reports_root.mkdir(exist_ok=True)
                ts_stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                report_dir = reports_root / f"{ticker.upper()}_{ts_stamp}"
                try:
                    save_report_to_disk(final_state, ticker, report_dir)
                    print(f"  报告: {report_dir}")
                except Exception as e:
                    print(f"  ! 报告落盘失败 (不影响主流程): {e}")
                    report_dir = None
            except Exception as e:
                status = "failed"
                err = str(e)
                print(f"  ✗ 分析失败: {e}")
                traceback.print_exc()
        else:
            # 只评分：找最新的报告目录
            report_dir = find_latest_report_dir(ticker.replace(".", "_").upper(), 0) \
                         or find_latest_report_dir(ticker.upper(), 0)

        # 2) 打分
        score_dict = {}
        if do_score and report_dir and report_dir.exists():
            try:
                print(f"  评分中...")
                # ticker 透传到 score_reports → 触发 F-Score (公式化客观分)
                score = score_reports(scoring_llm, report_dir, ticker=ticker)
                score_dict = score.to_dict()
                # 保存到报告目录
                (report_dir / "scores.json").write_text(
                    json.dumps(score_dict, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                # 打印简要 — F-Score 是公式分, 跟 LLM 主观分对照看才有意义
                t = score.technical_score
                fv = score.fundamental_score
                fs_v = score.fscore_value
                fs_disp = f"F={fs_v}/9" if fs_v is not None else "F=—"
                print(f"  技术={t} 基本面={fv} {fs_disp} "
                      f"象限={score.quadrant} 评级={score.final_rating}")
            except Exception as e:
                print(f"  评分失败: {e}")
                err = err or f"scoring: {e}"

        # 3) 导出到 Billionaire 能消费的 JSON (~/.market_data/agent_reports/<ts_code>.json)
        if report_dir and report_dir.exists():
            try:
                exported_path = export_to_billionaire(
                    ticker=ticker,
                    report_dir=report_dir,
                    analysis_date=analysis_date,
                    score_dict=score_dict if score_dict else None,
                    decision_text=decision_str,
                    model_config={
                        "quick": config.get("quick_think_llm"),
                        "deep": config.get("deep_think_llm"),
                    },
                )
                print(f"  ✓ Billionaire 导出: {exported_path}")
            except Exception as e:
                print(f"  ! Billionaire 导出失败 (不影响主流程): {e}")
                err = err or f"export: {e}"

        # 3) 汇总行
        elapsed = (datetime.now() - t0).total_seconds()
        row = {
            "ticker": ticker,
            "date": analysis_date,
            "status": status,
            "elapsed_seconds": round(elapsed, 1),
            "technical_score": score_dict.get("technical_score"),
            "fundamental_score": score_dict.get("fundamental_score"),
            "sentiment_score": (score_dict.get("sentiment") or {}).get("score"),
            "news_score": (score_dict.get("news") or {}).get("score"),
            "quadrant": score_dict.get("quadrant"),
            "final_rating": score_dict.get("final_rating"),
            "technical_stance": (score_dict.get("technical") or {}).get("stance"),
            "fundamental_stance": (score_dict.get("fundamental") or {}).get("stance"),
            "report_dir": str(report_dir) if report_dir else None,
            "error": err,
            "decision": decision_str,
        }
        rows.append(row)
        results.append(row)

    # ---------- 落盘 ----------
    reports_root = Path("reports")
    reports_root.mkdir(exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    summary_path = reports_root / f"{ts}_batch_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "analysis_date": analysis_date,
            "tickers": tickers,
            "total": len(results),
            "succeeded": sum(1 for r in results if r["status"] == "success"),
            "results": results,
        }, f, ensure_ascii=False, indent=2)

    ranking_path = reports_root / f"{ts}_ranking.csv"
    write_ranking_csv(rows, ranking_path)

    # ---------- 终端展示排序 ----------
    print("\n" + "=" * 70)
    print("批次完成")
    print(f"  汇总 JSON: {summary_path}")
    print(f"  排序 CSV : {ranking_path}")

    scored = [r for r in rows if r["technical_score"] is not None]
    if scored:
        print("\n按 (technical + fundamental) 综合排序:")
        scored.sort(
            key=lambda r: (r["technical_score"] or 0) + (r["fundamental_score"] or 0),
            reverse=True,
        )
        print(f"  {'ticker':<12} {'技术':>5} {'基本面':>6} {'象限':<25} {'评级':<12}")
        for r in scored:
            print(f"  {r['ticker']:<12} {r['technical_score']:>5} "
                  f"{r['fundamental_score']:>6} {r['quadrant']:<25} "
                  f"{r['final_rating'] or '-':<12}")
    print("=" * 70)

    return 0 if all(r["status"] == "success" for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
