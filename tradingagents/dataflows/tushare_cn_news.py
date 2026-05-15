"""Tushare CN 个股公告 (anns) + 公司新闻流 (news_vip).

公告是 A 股 M&A / 业绩预告 / 重大资产重组 / 复牌 等关键事件的**官方首发**渠道,
比新闻平台聚合早 / 准. 信息密度最高.

需要 TUSHARE_TOKEN env var, 5000 积分档以上.

跟 AKShare 互补:
  - Tushare anns      → 官方公告 (重大事件确证)
  - AKShare 东财新闻  → 媒体解读 (市场情绪)
  - AKShare 财联社    → 实时快讯 (短线突发)
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

_pro = None


def _tushare_pro():
    """Lazy init Tushare pro_api. 没 token 返 None, 调用方走 placeholder."""
    global _pro
    if _pro is None:
        try:
            import tushare as ts
            token = os.getenv("TUSHARE_TOKEN")
            if not token:
                logger.warning("TUSHARE_TOKEN missing; tushare CN news unavailable")
                _pro = False
                return None
            ts.set_token(token)
            _pro = ts.pro_api()
        except ImportError:
            logger.warning("tushare not installed")
            _pro = False
    return _pro if _pro is not False else None


def _to_tushare_ts_code(ticker: str) -> Optional[str]:
    """yfinance ticker → Tushare ts_code 风格.

    600519.SS → 600519.SH  (yfinance .SS → tushare .SH)
    300131.SZ → 300131.SZ  (深市同)
    688146.SS → 688146.SH
    0700.HK → 跳过 (Tushare 港股需要付费 hk_basic, 这里不接)
    """
    t = ticker.strip().upper()
    if t.endswith(".SS"):
        return f"{t[:-3]}.SH"
    if t.endswith((".SZ", ".SH", ".BJ")):
        return t
    return None


def _to_yyyymmdd(date_str: str) -> str:
    """'2026-05-14' → '20260514'. Tushare API 用紧凑日期格式."""
    if re.match(r"^\d{8}$", date_str):
        return date_str
    return datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y%m%d")


def fetch_anns_block(ticker: str, days_back: int = 14, limit: int = 30) -> str:
    """A 股个股公告 (Tushare pro.anns).

    返回最近 N 天的公告 markdown block. M&A / 业绩预告 / 重组 这种关键
    事件优先从这里抓 (官方首发, 比新闻早, 比 social 准).
    """
    pro = _tushare_pro()
    if pro is None:
        return "<tushare 不可用 (TUSHARE_TOKEN 没配或库没装)>"

    ts_code = _to_tushare_ts_code(ticker)
    if ts_code is None:
        return f"<{ticker} 非 A 股 ticker, 跳过 Tushare 公告>"

    end_date = datetime.now().strftime("%Y%m%d")
    start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y%m%d")

    try:
        df = pro.anns(ts_code=ts_code, start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.warning("Tushare anns failed for %s: %s", ticker, e)
        return f"<Tushare 公告拉取失败: {type(e).__name__}: {e}>"

    if df is None or len(df) == 0:
        return f"<{ticker} 近 {days_back} 天无公告>"

    df = df.head(limit)
    lines = [f"# {ticker} 个股公告 ({start_date}~{end_date}, {len(df)} 条)\n"]
    for _, row in df.iterrows():
        date = str(row.get("ann_date", "")).strip()
        title = str(row.get("title", "")).strip()
        content = str(row.get("content", "")).strip()[:300]
        lines.append(f"- [{date}] **{title}**")
        if content and content != "nan":
            lines.append(f"  {content}…")
    return "\n".join(lines)


def fetch_tushare_news_block(ticker: str, days_back: int = 7, limit: int = 20) -> str:
    """Tushare 公司新闻流 (pro.news, VIP).

    pro.news(src=...) 是按数据源的"全市场新闻流", 不按 ticker 索引. 拉下来
    用股票代码 grep 标题/正文过滤. 常用 src='sina'/'wallstreetcn'/'cls'.
    """
    pro = _tushare_pro()
    if pro is None:
        return "<tushare 不可用>"

    ts_code = _to_tushare_ts_code(ticker)
    if ts_code is None:
        return f"<{ticker} 非 A 股, 跳过 Tushare 新闻流>"

    code = ts_code.split(".")[0]  # 6 位 ticker (用来 grep)
    end_dt = datetime.now() + timedelta(days=1)
    start_dt = datetime.now() - timedelta(days=days_back)
    start_date = start_dt.strftime("%Y-%m-%d %H:%M:%S")
    end_date = end_dt.strftime("%Y-%m-%d %H:%M:%S")

    try:
        df = pro.news(src="sina", start_date=start_date, end_date=end_date)
    except Exception as e:
        logger.warning("Tushare news failed: %s", e)
        return f"<Tushare 新闻流拉取失败: {type(e).__name__}: {e}>"

    if df is None or len(df) == 0:
        return f"<Tushare 新闻流近 {days_back} 天无返回>"

    # grep 含 6 位股票代码的标题/正文
    try:
        mask = df.get("content", "").astype(str).str.contains(code, na=False) | df.get(
            "title", ""
        ).astype(str).str.contains(code, na=False)
        relevant = df[mask].head(limit)
    except Exception:
        relevant = df.head(0)

    if len(relevant) == 0:
        return f"<近 {days_back} 天 Tushare 新闻流没找到 {code} 相关>"

    lines = [f"# Tushare 新闻流 ({code} 相关, {len(relevant)} 条)\n"]
    for _, row in relevant.iterrows():
        ts = str(row.get("datetime", "")).strip()
        title = str(row.get("title", "")).strip()[:200]
        content = str(row.get("content", "")).strip()[:300]
        lines.append(f"- [{ts}] {title}")
        if content and content != "nan":
            lines.append(f"  {content}…")
    return "\n".join(lines)


# ─── vendor routing entrypoint ────────────────────────────────────────
def get_tushare_cn_news(
    ticker: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    """统一接口给 route_to_vendor('get_news') 用. 拼接公告 + 新闻流."""
    anns = fetch_anns_block(ticker, days_back=14, limit=20)
    news = fetch_tushare_news_block(ticker, days_back=7, limit=15)
    return f"{anns}\n\n---\n\n{news}"
