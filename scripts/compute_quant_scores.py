"""单次 spawn 算所有 4 个量化分 (F/Q/G/V), stdout 输出 JSON.

供 Billionaire middleware `/api/local/quant-scores/:tsCode` 调用. 一次 spawn
覆盖所有 score, 不要每个 score 都 spawn 一次 Python (启动 ~1s).

用法
────
    python scripts/compute_quant_scores.py <ticker>

返回 stdout JSON:
    {
      "ts_code": "600519.SH",
      "fscore": {...} | null,
      "qscore": {...} | null,
      "gscore": {...} | null,
      "vscore": {...} | null,
      "errors": [...]
    }

每个 score 独立 try/except, 单个失败不影响其它. 4 个全失败也返 200 + 空对象,
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


def main():
    if len(sys.argv) < 2:
        print(json.dumps({"errors": ["usage: compute_quant_scores.py <ticker>"]}))
        sys.exit(1)

    ticker = sys.argv[1]
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

    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
