"""单次 spawn 算所有 4 个量化分 (F/Q/G/V), stdout 输出 JSON.

供 Billionaire middleware 调用. 一次 spawn 覆盖所有 score, 不要每个 score
都 spawn 一次 Python (启动 ~1s).

用法
────
    # 单票 (旧路径, /api/local/quant-scores/:tsCode 用, 输出不变):
    python scripts/compute_quant_scores.py <ticker>

    # 批量 (/api/local/bulk-quant-scores 用): ts_code 从 stdin 按行读,
    # 一次 Python 启动算完整批 —— 趋势页 Q/G 闸门曾经一票一 spawn,
    # 300 票 = 300 次 ~1s 启动, 现在摊到 1 次.
    printf '600519.SH\\nAAPL.US\\n' | python scripts/compute_quant_scores.py --batch

单票返回 stdout JSON:
    { "ts_code": "600519.SH", "fscore": {...}|null, "qscore": ...,
      "gscore": ..., "vscore": ..., "errors": [...] }

批量返回 stdout JSON (map, key = 归一化后的 ts_code):
    { "600519.SH": {<上面那个对象>}, "AAPL.US": {...} }

每个 score 独立 try/except, 单个失败不影响其它. 4 个全失败也返完整对象,
让前端自己判断显示什么.
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

# 让 import 找到 tradingagents 包
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def score_one(ticker: str) -> dict:
    """算一支股票的 F/Q/G/V. import 放函数内, 批量时只在首票真正加载一次
    (后续命中模块缓存), 单票行为与旧版完全一致."""
    out: dict = {
        "ts_code": ticker,
        "fscore": None,
        "qscore": None,
        "gscore": None,
        "vscore": None,
        "errors": [],
    }

    try:
        from tradingagents.scoring.fscore import compute_fscore
        r = compute_fscore(ticker)
        if r is not None:
            out["fscore"] = r.to_dict()
            out["ts_code"] = r.ts_code  # 用归一化后的 ts_code
    except Exception as e:
        out["errors"].append(f"fscore: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)

    try:
        from tradingagents.scoring.qscore import compute_qscore
        r = compute_qscore(ticker)
        if r is not None:
            out["qscore"] = r.to_dict()
            out["ts_code"] = r.ts_code
    except Exception as e:
        out["errors"].append(f"qscore: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)

    try:
        from tradingagents.scoring.gscore import compute_gscore
        r = compute_gscore(ticker)
        if r is not None:
            out["gscore"] = r.to_dict()
            out["ts_code"] = r.ts_code
    except Exception as e:
        out["errors"].append(f"gscore: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)

    try:
        from tradingagents.scoring.vscore import compute_vscore
        r = compute_vscore(ticker)
        if r is not None:
            out["vscore"] = r.to_dict()
            out["ts_code"] = r.ts_code
    except Exception as e:
        out["errors"].append(f"vscore: {type(e).__name__}: {e}")
        traceback.print_exc(file=sys.stderr)

    return out


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"errors": ["usage: compute_quant_scores.py <ticker> | --batch"]}))
        sys.exit(1)

    if sys.argv[1] == "--batch":
        tickers = [ln.strip() for ln in sys.stdin if ln.strip()]
        result: dict = {}
        for t in tickers:
            try:
                env = score_one(t)
            except Exception as e:  # score_one 内部已兜底, 这层只防意外
                env = {
                    "ts_code": t, "fscore": None, "qscore": None,
                    "gscore": None, "vscore": None,
                    "errors": [f"{type(e).__name__}: {e}"],
                }
                traceback.print_exc(file=sys.stderr)
            result[env["ts_code"]] = env
        print(json.dumps(result, ensure_ascii=False))
        return

    print(json.dumps(score_one(sys.argv[1]), ensure_ascii=False))


if __name__ == "__main__":
    main()
