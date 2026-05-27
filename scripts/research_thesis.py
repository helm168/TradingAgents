#!/usr/bin/env python3
"""调研 WealthPilot 投资逻辑卡里的关切点, 落盘 observations JSON.

用法:
    # 全量, 用 OpenAI (Responses API + web_search)
    python scripts/research_thesis.py --provider openai --model gpt-4o

    # 全量, 用 Anthropic (默认)
    python scripts/research_thesis.py --provider anthropic --model claude-sonnet-4-5

    # 单 concern, 跨 provider 对比 (跑两次, 不同 provider 各自落盘)
    python scripts/research_thesis.py --provider openai   --company cn:600519 --concern feitian-wholesale-price
    python scripts/research_thesis.py --provider anthropic --company cn:600519 --concern feitian-wholesale-price

    # Dry run (不调 LLM, 不落盘, 只打印 prompt 长度)
    python scripts/research_thesis.py --dry-run --company fmp:NVDA

    # 部分重跑 + 保留上次范围外的 observation
    python scripts/research_thesis.py --company fmp:NVDA --keep-previous

环境:
    OPENAI_API_KEY           OpenAI provider 必填
    ANTHROPIC_API_KEY        Anthropic provider 必填
    SH_QUANT_DATA_DIR        可选, 共享数据根; 默认 ~/.market_data

输入 / 输出 (都在共享数据根下, 跟 agent_reports 通路对称):
    <data_dir>/thesis/knowledge.json                                   ← WealthPilot dev server 启动时 sync
    <data_dir>/thesis/observations.<provider>-<model>.<date>.json      ← 本次产出
    <data_dir>/thesis/observations.<provider>-<model>.latest.json      ← UI dropdown 切换

PRD §4.2 / §8 — 调研失败/查不到 → unknown, 不编. evidence 强制带可点 URL.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

# 让 scripts/ 下的脚本能 import tradingagents/
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT))

# Load .env (跟 run_batch.py 一致 — TradingAgents 项目约定)
from dotenv import load_dotenv  # noqa: E402

load_dotenv(_REPO_ROOT / ".env")

from tradingagents.thesis.runner import run_research  # noqa: E402
from tradingagents.thesis.types import ResearchConfig  # noqa: E402


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--provider", default="anthropic",
                   choices=["anthropic", "openai"],
                   help="LLM provider (default: anthropic)")
    p.add_argument("--model", default=None,
                   help="model id (default: claude-sonnet-4-5 for anthropic, gpt-4o for openai)")
    p.add_argument("--max-web-search-uses", type=int, default=5,
                   help="单 concern 最多调几次 web_search (default: 5)")
    p.add_argument("--max-tokens", type=int, default=4096)
    p.add_argument("--knowledge-path", default=None,
                   help="knowledge.json 显式路径 (调试用; 默认 $SH_QUANT_DATA_DIR/thesis/knowledge.json)")
    p.add_argument("--output-dir", default=None,
                   help="observations 输出目录 (默认 $SH_QUANT_DATA_DIR/thesis 或 ~/.market_data/thesis)")
    p.add_argument("--company", action="append", default=None, dest="companies",
                   metavar="COMPANY_ID",
                   help="只跑含这些 companyId 的 segment 里的 Player (例 fmp:NVDA, 可重复)")
    p.add_argument("--segment", action="append", default=None, dest="segments",
                   metavar="SEGMENT_ID",
                   help="只跑这些 segment id (例 hbm-memory / baijiu-premium, 可重复)")
    p.add_argument("--track", action="append", default=None, dest="tracks",
                   metavar="TRACK_ID",
                   help="只跑这些赛道 (例 ai-compute / baijiu / upstream-chokepoint, 可重复)")
    p.add_argument("--concern", action="append", default=None, dest="concerns",
                   metavar="CONCERN_ID",
                   help="只跑这些 concernId (例 feitian-wholesale-price, 可重复)")
    p.add_argument("--no-gating", action="store_true",
                   help="禁用阶段二门控 (默认 bearish 环节跳过 Player; 调试 / 历史回填用)")
    p.add_argument("--max-age-days", type=int, default=None, metavar="N",
                   help="临时 cache TTL 上限 (天). 覆盖所有 concern. "
                        "默认走 knowledge.json 各 concern 的 cacheTtlDays (3/7/30).")
    p.add_argument("--force", action="store_true",
                   help="完全忽略 cache, 全部重跑 LLM (常用于 prompt 改完想全量 rerun)")
    p.add_argument("--keep-previous", action="store_true",
                   help="不在本次范围内的 observation 沿用上次 latest.json (默认丢掉)")
    p.add_argument("--dry-run", action="store_true",
                   help="不调 LLM, 不落盘 (review prompt 用)")
    p.add_argument("--print-prompts", action="store_true",
                   help="打印当前 system + 示例 user prompt (用于迭代). 不调 LLM 不落盘.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


def _do_print_prompts() -> int:
    """打印当前生效的 system + 用 knowledge.json 第一个 concern 渲染的示例 user prompt.
    Prompt 路径出现在前面的 INFO log 里."""
    from tradingagents.thesis.knowledge_loader import load_knowledge
    from tradingagents.thesis.providers._common import (
        SYSTEM_PROMPT,
        build_user_prompt,
    )
    print("─" * 60)
    print("SYSTEM PROMPT")
    print("─" * 60)
    print(SYSTEM_PROMPT)
    print()
    print("─" * 60)
    print("USER PROMPT (示例: 知识库里第一张卡的第一个关切点)")
    print("─" * 60)
    knowledge = load_knowledge(ResearchConfig())
    segment = knowledge["segments"][0]
    track = next((t for t in knowledge["tracks"] if t["id"] == segment.get("track")), None)
    # 优先示例环节级 concern; 没有就示例第一个 Player 的公司级 concern
    if segment.get("concerns"):
        concern = segment["concerns"][0]
        print(build_user_prompt(segment, track, concern, None, None))
    else:
        players = [p for p in segment.get("players", []) if not p.get("referenceOnly")]
        if players and players[0].get("concerns"):
            player = players[0]
            concern = player["concerns"][0]
            print(build_user_prompt(segment, track, concern, player, None))
        else:
            print("(知识库第一个 segment 没有 concern 可示例)")
    return 0


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.print_prompts:
        return _do_print_prompts()

    # provider-specific default model
    model = args.model
    if model is None:
        model = "gpt-4o" if args.provider == "openai" else "claude-sonnet-4-5"

    required_key = {"anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}[args.provider]
    if not args.dry_run and not os.environ.get(required_key):
        print(f"error: {required_key} env not set (use --dry-run to skip LLM call)",
              file=sys.stderr)
        return 2

    cfg = ResearchConfig(
        provider=args.provider,
        model=model,
        max_web_search_uses=args.max_web_search_uses,
        max_tokens=args.max_tokens,
        knowledge_path=args.knowledge_path,
        output_dir=args.output_dir,
        only_company_ids=args.companies,
        only_segment_ids=args.segments,
        only_track_ids=args.tracks,
        only_concern_ids=args.concerns,
        enable_gating=not args.no_gating,
        max_age_days_override=args.max_age_days,
        force_refresh=args.force,
        keep_previous_unchanged=args.keep_previous,
        dry_run=args.dry_run,
    )

    bundle = run_research(cfg)
    print(f"generated {len(bundle['observations'])} observations  "
          f"(agent={bundle['agent']['name']}, model={bundle['agent'].get('model','?')})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
