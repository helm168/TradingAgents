#!/usr/bin/env python3
"""回填已落盘 agent_reports JSON 里被旧解析器标错的 verdict.action.

背景
────
旧 `_extract_verdict` 对整篇 PM 散文做"首个命中即停"正则, 中文论述里夹带的
英文 BUY/Overweight (模板回显 / 引用子分析师) 会先于真实的中文决策命中,
导致正文明确「卖出」却被标成 BUY (实测 SNDK / 00981.HK / 688498.gpt-5.5).

`billionaire_report.py` 的解析器已改成"优先锚定决策行". 本脚本用**同一套**
逻辑 (`_action_from_decision_line`) 重跑每份 JSON 存的 `verdict.raw`, 与
落盘的 `verdict.action` 对比:

  - 能从 raw 决策行重新判定且与落盘不一致 → MISMATCH (可修)
  - raw 决策行在 1500 字截断之外 (重新判定 UNKNOWN) → 无法从 JSON 修,
    标记 NEEDS-RERUN, 需要重跑该 ticker 的分析重新导出

默认 dry-run 只打印; 加 --apply 才原地改写 (action + confidence).

用法
────
  python scripts/repair_billionaire_verdicts.py            # dry-run
  python scripts/repair_billionaire_verdicts.py --apply     # 真改写
  BILLIONAIRE_AGENT_REPORTS_DIR=/path python scripts/...     # 覆盖目录
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 复用导出器的目录解析 + 决策行抽取逻辑, 保持单一真源
from tradingagents.exporters.billionaire_report import (  # noqa: E402
    AGENT_REPORTS_DIR,
    _action_from_decision_line,
    _earliest_action,
)

# 最老的导出路径没存富文本 portfolio_md, verdict.raw 整体就是压缩裁决词
# (单个 "Hold" / "Sell"). 这种 raw 本身即权威裁决, 短且无决策行标签 ——
# 不能因为"找不到决策行"就当成需重跑.
_BARE_VERDICT_MAXLEN = 24


def _confidence_for(action: str, strong: bool) -> float:
    if strong:
        return 0.85
    if action != "UNKNOWN":
        return 0.65
    return 0.5


def _atomic_write(fp: Path, payload: dict) -> None:
    tmp = fp.with_suffix(fp.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, fp)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="真改写 JSON (默认只 dry-run 打印)")
    ap.add_argument("--dir", default=None,
                    help="agent_reports 目录 (默认沿用导出器解析)")
    args = ap.parse_args()

    root = Path(args.dir).expanduser().resolve() if args.dir else AGENT_REPORTS_DIR
    if not root.exists():
        print(f"目录不存在: {root}", file=sys.stderr)
        return 1

    files = sorted(root.glob("*.json"))
    if not files:
        print(f"无 JSON: {root}", file=sys.stderr)
        return 1

    mismatches: list[tuple[str, str, str, float, float]] = []
    needs_rerun: list[str] = []
    ok = 0

    for fp in files:
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001 — 维护脚本, 坏文件直接报
            print(f"  [skip] {fp.name}: 读取失败 {e}", file=sys.stderr)
            continue

        verdict = payload.get("verdict") or {}
        raw = verdict.get("raw") or ""
        cur_action = verdict.get("action", "UNKNOWN")
        cur_conf = verdict.get("confidence", 0.5)

        new_action, strong = _action_from_decision_line(raw)
        if new_action == "UNKNOWN":
            stripped = raw.strip()
            if stripped and len(stripped) <= _BARE_VERDICT_MAXLEN:
                # raw 整体就是压缩裁决词 (老路径无富文本): 它本身即权威,
                # 直接据此比对, 不算需重跑.
                new_action, strong = _earliest_action(stripped)
            if new_action == "UNKNOWN":
                # 真的是大段散文又找不到决策行: JSON 修不了, 需重跑
                needs_rerun.append(fp.name)
                continue

        new_conf = _confidence_for(new_action, strong)
        if new_action == cur_action:
            ok += 1
            continue

        mismatches.append((fp.name, cur_action, new_action, cur_conf, new_conf))
        if args.apply:
            verdict["action"] = new_action
            verdict["confidence"] = new_conf
            payload["verdict"] = verdict
            _atomic_write(fp, payload)

    print(f"\n扫描 {len(files)} 份  |  一致 {ok}  |  "
          f"错标 {len(mismatches)}  |  无法从JSON修(需重跑) {len(needs_rerun)}\n")

    if mismatches:
        verb = "已修正" if args.apply else "将修正(dry-run, 加 --apply 落盘)"
        print(f"── 错标 {verb} ──")
        for name, ca, na, cc, nc in mismatches:
            print(f"  {name}\n      action {ca} → {na}   confidence {cc} → {nc}")
        print()

    if needs_rerun:
        print("── 无法从 JSON 修复 (决策行在 raw 1500 字截断外, 需重跑分析重新导出) ──")
        for name in needs_rerun:
            print(f"  {name}")
        print()

    if mismatches and not args.apply:
        print("dry-run 未改动任何文件. 确认无误后加 --apply 落盘.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
