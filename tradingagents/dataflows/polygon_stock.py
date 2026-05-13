"""
Polygon.io vendor —— 美股 OHLC + 新闻数据。

Polygon 在这个项目里的定位：
  - OHLC：作为 yfinance 的对照/兜底（实测两边数据基本一致）
  - News：替代 yfinance news 的主力（Polygon 新闻 API 免费、覆盖广、有 sentiment 字段）
  - Fundamentals：Polygon 免费档基本面数据有限，仍然让 yfinance/FMP 主导
  - 港股 / A 股：Polygon 不支持，路由层会自动跳过

需要环境变量 POLYGON_API_KEY。
免费档限速：5 req/min, 2 年历史。如果跑批量超限，路由层会自动 fallback 到 yfinance。
"""

import os
import time
from datetime import datetime, timedelta
from typing import Annotated

import requests


POLYGON_BASE = "https://api.polygon.io"


class PolygonRateLimitError(Exception):
    """Polygon 限速触发，路由层会 fallback。"""


def _get_api_key() -> str:
    key = os.getenv("POLYGON_API_KEY")
    if not key:
        raise RuntimeError("POLYGON_API_KEY 未配置，请在 .env 里设置")
    return key


def _request(path: str, params: dict = None, timeout: float = 15.0) -> dict:
    """统一请求入口，处理限速/重试/错误。"""
    params = dict(params or {})
    params["apiKey"] = _get_api_key()
    url = f"{POLYGON_BASE}{path}"

    for attempt in range(3):
        try:
            r = requests.get(url, params=params, timeout=timeout)
        except requests.exceptions.RequestException as e:
            if attempt < 2:
                time.sleep(1.0 * (attempt + 1))
                continue
            raise

        if r.status_code == 429:
            # 限速 —— 直接抛错让路由层 fallback
            raise PolygonRateLimitError(f"Polygon rate limit hit on {path}")
        if r.status_code >= 400:
            raise RuntimeError(f"Polygon API {r.status_code} on {path}: {r.text[:200]}")
        return r.json()
    return {}


def _supported_market(symbol: str) -> bool:
    """Polygon 主要支持美股；带 .HK / .SS / .SZ 后缀的直接拒绝。"""
    s = symbol.upper()
    return not any(s.endswith(suf) for suf in (".HK", ".SS", ".SH", ".SZ"))


# ---------- 1. OHLC ----------
def get_polygon_stock_data(
    symbol: Annotated[str, "美股 ticker，如 NVDA / TSLA"],
    start_date: Annotated[str, "起始日期 yyyy-mm-dd"],
    end_date: Annotated[str, "结束日期 yyyy-mm-dd（含）"],
) -> str:
    """拉日线 OHLC，返回 CSV 字符串（对齐 yfinance 输出）。"""
    datetime.strptime(start_date, "%Y-%m-%d")
    datetime.strptime(end_date, "%Y-%m-%d")

    if not _supported_market(symbol):
        return f"Polygon 不支持 {symbol} 这类市场，请使用 efinance/yfinance"

    path = f"/v2/aggs/ticker/{symbol.upper()}/range/1/day/{start_date}/{end_date}"
    data = _request(path, params={"adjusted": "true", "sort": "asc", "limit": 5000})

    results = data.get("results") or []
    if not results:
        return f"No data found for symbol '{symbol}' between {start_date} and {end_date}"

    # Polygon 返回字段：t(ms)/o/h/l/c/v/vw/n
    lines = ["Date,Open,High,Low,Close,Volume"]
    for row in results:
        dt = datetime.fromtimestamp(row["t"] / 1000).strftime("%Y-%m-%d")
        lines.append(
            f"{dt},{round(row['o'], 4)},{round(row['h'], 4)},"
            f"{round(row['l'], 4)},{round(row['c'], 4)},{int(row['v'])}"
        )

    header = (
        f"# Stock data for {symbol.upper()} (via Polygon) "
        f"from {start_date} to {end_date}\n"
        f"# Total records: {len(results)}\n"
        f"# Data retrieved on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
    )
    return header + "\n".join(lines)


# ---------- 2. News（公司） ----------
def get_polygon_news(
    query: Annotated[str, "公司 ticker 或公司名"],
    start_date: Annotated[str, "起始日期 yyyy-mm-dd"],
    end_date: Annotated[str, "结束日期 yyyy-mm-dd"],
    limit: int = 30,
) -> str:
    """拉公司新闻，返回 markdown 字符串。"""
    if not _supported_market(query):
        return f"Polygon 不支持 {query} 这类市场的新闻"

    params = {
        "ticker": query.upper(),
        "published_utc.gte": f"{start_date}T00:00:00Z",
        "published_utc.lte": f"{end_date}T23:59:59Z",
        "order": "desc",
        "limit": min(limit, 1000),
        "sort": "published_utc",
    }
    data = _request("/v2/reference/news", params=params)
    results = data.get("results") or []

    if not results:
        return f"No news found for '{query}' between {start_date} and {end_date}"

    lines = [f"# News for {query.upper()} from {start_date} to {end_date} (via Polygon)\n"]
    for n in results:
        published = n.get("published_utc", "")[:10]
        title = n.get("title", "")
        publisher = (n.get("publisher") or {}).get("name", "")
        url = n.get("article_url", "")
        # Polygon 给每条新闻一个 ticker-level 的 insight（sentiment）
        sentiments = []
        for ins in (n.get("insights") or []):
            if ins.get("ticker", "").upper() == query.upper():
                sent = ins.get("sentiment", "neutral")
                reason = ins.get("sentiment_reasoning", "")
                sentiments.append(f"sentiment={sent}; {reason[:120]}")
        sent_line = f"\n  _{sentiments[0]}_" if sentiments else ""
        desc = (n.get("description") or "")[:300]
        lines.append(f"## [{published}] {title}\n_{publisher}_  |  {url}{sent_line}\n\n{desc}\n")

    return "\n".join(lines)


# ---------- 3. Global / market news ----------
def get_polygon_global_news(
    curr_date: Annotated[str, "当前日期 yyyy-mm-dd"],
    look_back_days: int = 7,
    limit: int = 30,
) -> str:
    """拉宏观/全市场新闻（不带 ticker 过滤），返回 markdown 字符串。"""
    curr_dt = datetime.strptime(curr_date, "%Y-%m-%d")
    start = (curr_dt - timedelta(days=look_back_days)).strftime("%Y-%m-%d")
    end = curr_date

    params = {
        "published_utc.gte": f"{start}T00:00:00Z",
        "published_utc.lte": f"{end}T23:59:59Z",
        "order": "desc",
        "limit": min(limit, 1000),
        "sort": "published_utc",
    }
    data = _request("/v2/reference/news", params=params)
    results = data.get("results") or []

    if not results:
        return f"No global news found between {start} and {end}"

    lines = [f"# Global / Market News from {start} to {end} (via Polygon)\n"]
    for n in results[:limit]:
        published = n.get("published_utc", "")[:10]
        title = n.get("title", "")
        publisher = (n.get("publisher") or {}).get("name", "")
        url = n.get("article_url", "")
        desc = (n.get("description") or "")[:250]
        lines.append(f"## [{published}] {title}\n_{publisher}_  |  {url}\n\n{desc}\n")

    return "\n".join(lines)


# ---------- 4. Fundamentals（基础版） ----------
def get_polygon_fundamentals(
    ticker: Annotated[str, "美股 ticker"],
    curr_date: Annotated[str, "当前日期，未使用"] = None,
) -> str:
    """
    Polygon 的 ticker details + 最新财报概要。
    免费档能拿到公司元数据 + 部分财务字段，但不如 FMP 完整。
    """
    if not _supported_market(ticker):
        return f"Polygon 不支持 {ticker} 的基本面，请使用 efinance/yfinance"

    try:
        det = _request(f"/v3/reference/tickers/{ticker.upper()}").get("results", {})
    except Exception as e:
        return f"Polygon ticker details 失败 ({ticker}): {e}"

    lines = [f"# {ticker.upper()} Fundamentals (via Polygon)\n"]
    fields = [
        ("Name", det.get("name")),
        ("Market", det.get("market")),
        ("Primary Exchange", det.get("primary_exchange")),
        ("Type", det.get("type")),
        ("CIK", det.get("cik")),
        ("Composite FIGI", det.get("composite_figi")),
        ("Share Class FIGI", det.get("share_class_figi")),
        ("Description", (det.get("description") or "")[:500]),
        ("Homepage", det.get("homepage_url")),
        ("Total Employees", det.get("total_employees")),
        ("Listed Date", det.get("list_date")),
        ("Market Cap", det.get("market_cap")),
        ("Weighted Shares Outstanding", det.get("weighted_shares_outstanding")),
        ("SIC Description", det.get("sic_description")),
    ]
    for label, val in fields:
        if val:
            lines.append(f"- **{label}**: {val}")

    # 拉最新一期财报
    try:
        fin = _request(
            "/vX/reference/financials",
            params={"ticker": ticker.upper(), "limit": 1, "order": "desc",
                    "sort": "filing_date"},
        ).get("results") or []
        if fin:
            f0 = fin[0]
            lines.append(f"\n## Latest Financials ({f0.get('fiscal_period')} {f0.get('fiscal_year')})")
            income = (f0.get("financials") or {}).get("income_statement") or {}
            for key in ["revenues", "gross_profit", "operating_income_loss",
                        "net_income_loss", "basic_earnings_per_share"]:
                v = (income.get(key) or {}).get("value")
                if v is not None:
                    lines.append(f"- **{key}**: {v}")
    except Exception:
        pass

    return "\n".join(lines)


# ---------- 占位：财务三大表（让路由 fallback 到 yfinance） ----------
def get_polygon_balance_sheet(ticker: str, curr_date: str = None) -> str:
    return f"Polygon balance sheet 未实现（覆盖度不如 yfinance），请走 yfinance"


def get_polygon_income_statement(ticker: str, curr_date: str = None) -> str:
    return f"Polygon income statement 未实现，请走 yfinance"


def get_polygon_cashflow(ticker: str, curr_date: str = None) -> str:
    return f"Polygon cashflow 未实现，请走 yfinance"


def get_polygon_insider_transactions(ticker: str, curr_date: str = None) -> str:
    return f"Polygon insider transactions 未实现，请走 yfinance/alpha_vantage"


# ---------- 占位：指标 —— stockstats 在 OHLC 上计算 ----------
# 与 efinance 一样，indicator 走 stockstats_utils.load_ohlcv，
# 这里不需要单独实现 get_indicators。
