"""TradingAgents 报告导出层。

把 reports/<TICKER>_<timestamp>/ 下的 markdown + scores.json + decision 归一化
成 JSON, 给下游消费者 (Billionaire 详情页) 用. 见 billionaire_report.py.
"""
from .billionaire_report import (
    export_to_billionaire,
    normalize_ts_code,
    AGENT_REPORTS_DIR,
)

__all__ = ["export_to_billionaire", "normalize_ts_code", "AGENT_REPORTS_DIR"]
