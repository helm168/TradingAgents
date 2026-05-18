"""AKShare 中文新闻 + 财联社快讯 — A 股 / 港股 sentiment 数据源.

AKShare 聚合东方财富 / 财联社 / 同花顺等中文财经源, 是中文量化圈标配,
免费开源. 内部调上游接口偶尔限速 / 改格式, 全部 try/except 兜底, 失败
返回占位字符串保证 caller 不用 special-case.

为什么需要这个
─────────────
原本 sentiment_analyst 调 StockTwits + Reddit + Yahoo Finance news, 三家
全只覆盖美股. A 股 / 港股 sentiment 输出"数据不足, 无法判定". 用户在 Futu
看到并购消息但 agent 完全没拿到, 体验差.

加这个模块后 CN/HK ticker 走 AKShare, sentiment_analyst 拿到真实数据.

ticker 归一化
─────────────
  yfinance 风格 "300131.SZ" / "688146.SS" → AKShare "300131" / "688146" (6 位无后缀)
  港股 "0700.HK" → AKShare "00700" (5 位补零)
"""
from __future__ import annotations

import json
import logging
import re
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# akshare 启动 import 巨慢 (要 lazy 加载 pandas + 一堆 vendor SDK), 用时再 import.
_ak = None


def _ak_module():
    """Lazy import akshare. 不可用时返回 None, 调用方走 placeholder."""
    global _ak
    if _ak is None:
        try:
            import akshare as ak
            _ak = ak
        except ImportError:
            logger.warning("akshare not installed; CN news fetcher unavailable")
            _ak = False
    return _ak if _ak is not False else None


def _to_akshare_symbol(ticker: str) -> Optional[str]:
    """yfinance ticker → AKShare 6 位代码 (CN) / 5 位 (HK).

    AKShare CN 接口 (stock_news_em / stock_zh_a_alerts_cls) 接收 6 位股票代码,
    不带 .SS/.SZ 后缀; 港股接口接收 5 位补零格式.
    """
    t = ticker.strip().upper()
    m = re.match(r"^(\d{6})\.(SS|SZ|SH|BJ)$", t)
    if m:
        return m.group(1)
    m = re.match(r"^0*(\d+)\.HK$", t)
    if m:
        return m.group(1).zfill(5)
    return None


def _fetch_em_news_direct(symbol: str, timeout: int = 15):
    """Direct Eastmoney fallback for AKShare stock_news_em breakages."""
    import pandas as pd

    callback = "jQuery3510875346244069884_1668256937995"
    url = "https://search-api-web.eastmoney.com/search/jsonp"
    params = {
        "cb": callback,
        "param": json.dumps(
            {
                "uid": "",
                "keyword": symbol,
                "type": ["cmsArticleWebOld"],
                "client": "web",
                "clientType": "web",
                "clientVersion": "curr",
                "param": {
                    "cmsArticleWebOld": {
                        "searchScope": "default",
                        "sort": "default",
                        "pageIndex": 1,
                        "pageSize": 100,
                        "preTag": "<em>",
                        "postTag": "</em>",
                    }
                },
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    text = response.text.strip()
    prefix = f"{callback}("
    if text.startswith(prefix) and text.endswith(")"):
        text = text[len(prefix):-1]
    data = json.loads(text)
    articles = data.get("result", {}).get("cmsArticleWebOld", [])
    df = pd.DataFrame(articles)
    if df.empty:
        return df

    df = df.rename(
        columns={
            "date": "发布时间",
            "mediaName": "文章来源",
            "title": "新闻标题",
            "content": "新闻内容",
            "url": "新闻链接",
        }
    )
    df["关键词"] = symbol
    for col in ("新闻标题", "新闻内容"):
        if col in df.columns:
            df[col] = df[col].astype(str).str.replace(r"</?em>", "", regex=True)
    cols = ["关键词", "新闻标题", "新闻内容", "发布时间", "文章来源", "新闻链接"]
    return df[[col for col in cols if col in df.columns]]


def fetch_em_news_block(ticker: str, limit: int = 30) -> str:
    """东方财富个股新闻 markdown block (sentiment_analyst prompt 注入用).

    Tushare anns 是公告 (准确但延迟), 东财新闻是聚合层 (含转载/解读, 信息密度高).
    用东财抓"市场怎么解读这只股票的", 跟公告互补.
    """
    ak = _ak_module()
    if ak is None:
        return "<akshare 未安装>"
    symbol = _to_akshare_symbol(ticker)
    if symbol is None:
        return f"<{ticker} 非 CN/HK ticker, 跳过东财新闻>"

    try:
        df = ak.stock_news_em(symbol=symbol)
    except Exception as e:
        logger.warning("AKShare stock_news_em failed for %s: %s", ticker, e)
        try:
            df = _fetch_em_news_direct(symbol)
            logger.info("Eastmoney direct news fallback succeeded for %s", ticker)
        except Exception as fallback_e:
            logger.warning("Eastmoney direct news fallback failed for %s: %s", ticker, fallback_e)
            return (
                f"<东财新闻拉取失败: AKShare {type(e).__name__}: {e}; "
                f"direct fallback {type(fallback_e).__name__}: {fallback_e}>"
            )

    if df is None or len(df) == 0:
        return f"<{ticker} 东财近期无新闻>"

    df = df.head(limit)
    lines = [f"# {ticker} 东方财富个股新闻 (近 {len(df)} 条)\n"]
    for _, row in df.iterrows():
        ts = str(row.get("发布时间", "")).strip()
        title = str(row.get("新闻标题", "")).strip()
        source = str(row.get("文章来源", "")).strip()
        body = str(row.get("新闻内容", "")).strip()[:300]  # 截 300 字防 prompt 爆
        lines.append(f"- [{ts}] **{title}** ({source})")
        if body and body not in ("", "nan"):
            lines.append(f"  {body}…")
    return "\n".join(lines)


def fetch_cls_alerts_block(ticker: Optional[str] = None, limit: int = 30) -> str:
    """财联社快讯 (通过 AKShare). 全市场流, 按股票代码过滤.

    财联社是 A 股最及时的官方资讯源, 行情盘中突发 / 政策落地都先出在这.
    AKShare 提供这个流的函数名跨版本变化过, 这里 try 多个候选:
      - stock_telegraph_cls      (1.13+ 通用名)
      - stock_zh_a_alerts_cls_em (变体)
      - stock_zh_a_alerts_cls    (旧版)
    任意一个 work 就用它.
    """
    ak = _ak_module()
    if ak is None:
        return "<akshare 未安装>"

    df = None
    candidate_fns = (
        "stock_telegraph_cls",
        "stock_zh_a_alerts_cls_em",
        "stock_zh_a_alerts_cls",
    )
    last_err = None
    for fn_name in candidate_fns:
        fn = getattr(ak, fn_name, None)
        if fn is None:
            continue
        try:
            df = fn()
            break
        except Exception as e:
            last_err = f"{fn_name}: {type(e).__name__}: {e}"
            logger.warning("AKShare %s failed: %s", fn_name, e)

    if df is None or len(df) == 0:
        return f"<财联社快讯不可用 (尝试了 {len(candidate_fns)} 个函数都失败: {last_err})>"

    df = df.head(limit * 4)  # 多拉点, 过滤后剩下的更少

    # 过滤含 ticker 数字代码 (e.g. "600519") 的快讯
    relevant_df = df
    if ticker:
        symbol = _to_akshare_symbol(ticker)
        if symbol:
            cols = [c for c in ("标题", "内容") if c in df.columns]
            if cols:
                mask = df[cols].apply(
                    lambda col: col.astype(str).str.contains(symbol, na=False)
                ).any(axis=1)
                filtered = df[mask]
                if len(filtered) > 0:
                    relevant_df = filtered

    relevant_df = relevant_df.head(limit)
    header_note = (
        f", 已过滤含 {ticker}" if ticker and len(relevant_df) < len(df) else ", 全市场"
    )
    lines = [f"# 财联社快讯 (近 {len(relevant_df)} 条{header_note})\n"]
    for _, row in relevant_df.iterrows():
        # 不同版本 AKShare 列名可能略有差异, 防御性 get
        ts_parts = [str(row.get(k, "")).strip() for k in ("发布日期", "发布时间")]
        ts = " ".join(p for p in ts_parts if p and p != "nan")
        title = str(row.get("标题", "")).strip()
        content = str(row.get("内容", "")).strip()[:200]
        lines.append(f"- [{ts}] {title}")
        if content and content != title and content != "nan":
            lines.append(f"  {content}")
    return "\n".join(lines)


# ─── vendor routing entrypoint ────────────────────────────────────────
def get_akshare_cn_news(
    ticker: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> str:
    """统一接口给 route_to_vendor('get_news') 用.

    拼接东财新闻 (个股精准) + 财联社快讯 (宏观/突发) 两块. 返回单一 string
    给 LLM 的 tool call response 用.

    start_date / end_date 当前忽略 (AKShare 这俩接口都返回"最新 N 条" 不接
    日期 range). 未来需要的话再加 client-side 过滤.
    """
    em = fetch_em_news_block(ticker, limit=20)
    cls = fetch_cls_alerts_block(ticker, limit=10)
    return f"{em}\n\n---\n\n{cls}"
