"""把已有的 reports/<TICKER>_<timestamp>/ 报告 export 成 Billionaire JSON.

不重新跑 agent, 纯文件转换. 适合:
  - run_batch 历史跑过但当时还没 export 步骤的旧报告
  - 手动跑过 (没走 run_batch) 的单只分析想接入 UI
  - 重新调 export schema / 修 ticker 映射后批量回填

用法
────
    # 单只 ticker, 找最新的 reports/<TICKER>_*/
    python scripts/export_to_billionaire.py NVDA

    # 多只
    python scripts/export_to_billionaire.py NVDA 600519.SS 0700.HK

    # 指定具体的 report dir (绝对/相对路径)
    python scripts/export_to_billionaire.py --report-dir reports/NVDA_20260510_085400

    # 批量: 把 reports/ 下所有的最新报告都 export
    python scripts/export_to_billionaire.py --all

    # 自定义输出目录 (默认 ~/.market_data/agent_reports/)
    python scripts/export_to_billionaire.py --out /tmp/agent_reports NVDA
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

# 确保能 import tradingagents.*
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tradingagents.exporters import export_to_billionaire, AGENT_REPORTS_DIR


REPORTS_ROOT = Path(__file__).resolve().parent.parent / "reports"

# reports/<TICKER>_<YYYYMMDD>_<HHMMSS> — ticker 里的 . 在目录名里会被替换成 _
_DIR_RE = re.compile(r"^(.+)_(\d{8})_(\d{6})$")


def find_latest_report_dir(ticker: str) -> Path | None:
    """ticker 可能是 NVDA / 600519.SS / 0700.HK, 目录名里的 . 都是 _."""
    safe = ticker.replace(".", "_").upper()
    candidates = []
    for p in REPORTS_ROOT.iterdir():
        if not p.is_dir():
            continue
        m = _DIR_RE.match(p.name)
        if m and m.group(1) == safe:
            candidates.append((p, m.group(2) + m.group(3)))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[1], reverse=True)
    return candidates[0][0]


def list_all_latest() -> dict[str, Path]:
    """扫 reports/, 每个 ticker 取最新一份."""
    latest: dict[str, tuple[Path, str]] = {}
    for p in REPORTS_ROOT.iterdir():
        if not p.is_dir():
            continue
        m = _DIR_RE.match(p.name)
        if not m:
            continue
        ticker_safe = m.group(1)
        stamp = m.group(2) + m.group(3)
        cur = latest.get(ticker_safe)
        if cur is None or stamp > cur[1]:
            latest[ticker_safe] = (p, stamp)
    # 反推 ticker: NVDA / 600519_SS → 600519.SS / 0700_HK → 0700.HK
    out: dict[str, Path] = {}
    for ticker_safe, (path, _) in latest.items():
        # 启发式: 末段是 2 字母的看作 yfinance 后缀
        parts = ticker_safe.rsplit("_", 1)
        if len(parts) == 2 and parts[1] in ("SS", "SZ", "SH", "BJ", "HK", "US"):
            ticker = f"{parts[0]}.{parts[1]}"
        else:
            ticker = ticker_safe
        out[ticker] = path
    return out


def extract_decision(report_dir: Path) -> str:
    """portfolio 子目录里找 manager / decision 文本; 没有就空."""
    p = report_dir / "5_portfolio"
    if not p.exists():
        return ""
    texts = []
    for fp in sorted(p.glob("*.md")):
        texts.append(fp.read_text(encoding="utf-8"))
    return "\n\n".join(texts)


def extract_analysis_date(report_dir: Path) -> str:
    """从 complete_report.md 头几行找 '分析日期: YYYY-MM-DD'; 没找到就用目录时间戳."""
    cr = report_dir / "complete_report.md"
    if cr.exists():
        head = cr.read_text(encoding="utf-8")[:2000]
        m = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", head)
        if m:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = _DIR_RE.match(report_dir.name)
    if m:
        ymd = m.group(2)
        return f"{ymd[0:4]}-{ymd[4:6]}-{ymd[6:8]}"
    return "unknown"


def export_one(ticker: str, report_dir: Path, out_dir: Path | None) -> Path:
    score_dict = None
    sj = report_dir / "scores.json"
    if sj.exists():
        try:
            score_dict = json.loads(sj.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  ! scores.json 解析失败: {e}")
    decision = extract_decision(report_dir)
    analysis_date = extract_analysis_date(report_dir)
    return export_to_billionaire(
        ticker=ticker,
        report_dir=report_dir,
        analysis_date=analysis_date,
        score_dict=score_dict,
        decision_text=decision,
        out_dir=out_dir,
    )


def parse_args():
    p = argparse.ArgumentParser(description="Export TradingAgents reports → Billionaire JSON")
    p.add_argument("tickers", nargs="*", help="ticker 列表 (NVDA 600519.SS …)")
    p.add_argument("--report-dir", help="直接指定 reports/<TICKER>_<TS>/ 目录")
    p.add_argument("--all", action="store_true", help="reports/ 下所有 ticker 的最新报告全 export")
    p.add_argument("--out", help=f"输出目录 (默认 {AGENT_REPORTS_DIR})")
    return p.parse_args()


def main():
    args = parse_args()
    out_dir = Path(args.out).expanduser().resolve() if args.out else None

    # --- 单 dir 模式 ---
    if args.report_dir:
        rd = Path(args.report_dir).resolve()
        # ticker 从目录名反推
        m = _DIR_RE.match(rd.name)
        if not m:
            print(f"无法从目录名解析 ticker: {rd.name}")
            sys.exit(1)
        ticker_safe = m.group(1)
        parts = ticker_safe.rsplit("_", 1)
        ticker = (
            f"{parts[0]}.{parts[1]}"
            if len(parts) == 2 and parts[1] in ("SS", "SZ", "SH", "BJ", "HK", "US")
            else ticker_safe
        )
        out = export_one(ticker, rd, out_dir)
        print(f"✓ {ticker} → {out}")
        return

    # --- --all 模式 ---
    if args.all:
        latest = list_all_latest()
        print(f"扫到 {len(latest)} 个 ticker 的最新报告")
        for ticker, rd in sorted(latest.items()):
            try:
                out = export_one(ticker, rd, out_dir)
                print(f"  ✓ {ticker:20s} {rd.name} → {out.name}")
            except Exception as e:
                print(f"  ✗ {ticker}: {e}")
        return

    # --- ticker list 模式 ---
    if not args.tickers:
        print("usage: export_to_billionaire.py [TICKER ...] | --report-dir DIR | --all")
        sys.exit(1)

    for ticker in args.tickers:
        rd = find_latest_report_dir(ticker)
        if not rd:
            print(f"  ✗ {ticker}: 找不到 reports/ 里对应目录")
            continue
        try:
            out = export_one(ticker, rd, out_dir)
            print(f"  ✓ {ticker:20s} {rd.name} → {out.name}")
        except Exception as e:
            print(f"  ✗ {ticker}: {e}")


if __name__ == "__main__":
    main()
