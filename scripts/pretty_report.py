#!/usr/bin/env python
"""Re-render TradingAgents report markdown files with the same Panel-style
visualization the original CLI uses on completion.

The TradingAgents CLI saves reports to disk under:

    ~/.tradingagents/logs/<TICKER>/<DATE>/reports/
    ├── 1_analysts/
    │   ├── market.md
    │   ├── sentiment.md
    │   ├── news.md
    │   └── fundamentals.md
    ├── 2_research/
    │   ├── bull.md
    │   ├── bear.md
    │   └── manager.md
    ├── 3_trading/
    │   └── trader.md
    ├── 4_risk/
    │   ├── aggressive.md
    │   ├── conservative.md
    │   └── neutral.md
    ├── 5_portfolio/
    │   └── decision.md
    └── complete_report.md

This script walks that tree (or any single .md file you point it at) and
renders the content with the exact same Panel + Markdown + Rule combination
that ``cli/main.py`` uses, so you get the "demo-style" terminal output any
time after the run is over -- not just at the moment the CLI completes.

Usage:
    # Whole reports folder (most common)
    python scripts/pretty_report.py ~/.tradingagents/logs/NVDA/2025-04-01/reports/

    # Just the final consolidated markdown file
    python scripts/pretty_report.py ~/.tradingagents/logs/NVDA/2025-04-01/reports/complete_report.md

    # Any single agent's .md file
    python scripts/pretty_report.py ~/.tradingagents/logs/NVDA/2025-04-01/reports/1_analysts/fundamentals.md
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule


# ---------------------------------------------------------------------------
# Section schema
#
# Mirrors the layout in ``cli/main.py::display_complete_report`` and
# ``cli/main.py::save_report_to_disk``: 5 sections, each with its own border
# colour, each section listing the agents in display order along with the
# on-disk filename (relative to the reports/ root).
# ---------------------------------------------------------------------------
SECTIONS: list[tuple[str, str, list[tuple[str, str]]]] = [
    (
        "I. Analyst Team Reports",
        "cyan",
        [
            ("1_analysts/market.md", "Market Analyst"),
            ("1_analysts/sentiment.md", "Social Analyst"),
            ("1_analysts/news.md", "News Analyst"),
            ("1_analysts/fundamentals.md", "Fundamentals Analyst"),
        ],
    ),
    (
        "II. Research Team Decision",
        "magenta",
        [
            ("2_research/bull.md", "Bull Researcher"),
            ("2_research/bear.md", "Bear Researcher"),
            ("2_research/manager.md", "Research Manager"),
        ],
    ),
    (
        "III. Trading Team Plan",
        "yellow",
        [
            ("3_trading/trader.md", "Trader"),
        ],
    ),
    (
        "IV. Risk Management Team Decision",
        "red",
        [
            ("4_risk/aggressive.md", "Aggressive Analyst"),
            ("4_risk/conservative.md", "Conservative Analyst"),
            ("4_risk/neutral.md", "Neutral Analyst"),
        ],
    ),
    (
        "V. Portfolio Manager Decision",
        "green",
        [
            ("5_portfolio/decision.md", "Portfolio Manager"),
        ],
    ),
]


def _render_agent_panel(console: Console, content: str, title: str) -> None:
    """Render one agent's markdown body in the blue-bordered Panel style."""
    console.print(
        Panel(
            Markdown(content),
            title=title,
            border_style="blue",
            padding=(1, 2),
        )
    )


def _render_section_header(console: Console, title: str, color: str) -> None:
    """Render the bold section header (I. / II. / ...) with the team colour."""
    console.print(
        Panel(f"[bold]{title}[/bold]", border_style=color)
    )


def render_reports_dir(console: Console, reports_dir: Path) -> None:
    """Walk a TradingAgents reports/ folder and render each agent in order."""
    console.print()
    console.print(Rule("Complete Analysis Report", style="bold green"))

    rendered_any = False

    for section_title, color, files in SECTIONS:
        present = [
            (reports_dir / rel, agent_title)
            for rel, agent_title in files
            if (reports_dir / rel).exists()
        ]
        if not present:
            continue

        _render_section_header(console, section_title, color)
        for path, agent_title in present:
            content = path.read_text(encoding="utf-8")
            _render_agent_panel(console, content, agent_title)
        rendered_any = True

    if not rendered_any:
        console.print(
            f"[yellow]No known TradingAgents report files found under "
            f"{reports_dir}.[/yellow]\n"
            "Expected sub-folders: 1_analysts/, 2_research/, 3_trading/, "
            "4_risk/, 5_portfolio/."
        )


def render_single_file(console: Console, path: Path) -> None:
    """Render a single .md file. Used for one-off agent reports or for the
    final ``complete_report.md`` consolidated file."""
    content = path.read_text(encoding="utf-8")
    title = path.stem.replace("_", " ").title()
    console.print()
    _render_agent_panel(console, content, title)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Re-render TradingAgents reports with the original CLI's "
            "Panel/Markdown styling."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "path",
        type=Path,
        help=(
            "Either a reports/ directory (walks the 5 sub-folders) or a "
            "single .md file (rendered as a single Panel)."
        ),
    )
    parser.add_argument(
        "--width",
        type=int,
        default=None,
        help="Override terminal width (default: auto-detect from terminal).",
    )
    args = parser.parse_args()

    target: Path = args.path.expanduser().resolve()
    if not target.exists():
        print(f"Path not found: {target}", file=sys.stderr)
        return 1

    console = Console(width=args.width)

    if target.is_file():
        render_single_file(console, target)
    elif target.is_dir():
        render_reports_dir(console, target)
    else:
        print(f"Not a file or directory: {target}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
