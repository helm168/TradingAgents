from typing import Annotated

# Import from vendor-specific modules
from .y_finance import (
    get_YFin_data_online,
    get_stock_stats_indicators_window,
    get_fundamentals as get_yfinance_fundamentals,
    get_balance_sheet as get_yfinance_balance_sheet,
    get_cashflow as get_yfinance_cashflow,
    get_income_statement as get_yfinance_income_statement,
    get_insider_transactions as get_yfinance_insider_transactions,
)
from .yfinance_news import get_news_yfinance, get_global_news_yfinance
from .alpha_vantage import (
    get_stock as get_alpha_vantage_stock,
    get_indicator as get_alpha_vantage_indicator,
    get_fundamentals as get_alpha_vantage_fundamentals,
    get_balance_sheet as get_alpha_vantage_balance_sheet,
    get_cashflow as get_alpha_vantage_cashflow,
    get_income_statement as get_alpha_vantage_income_statement,
    get_insider_transactions as get_alpha_vantage_insider_transactions,
    get_news as get_alpha_vantage_news,
    get_global_news as get_alpha_vantage_global_news,
)
from .alpha_vantage_common import AlphaVantageRateLimitError

# efinance vendor (东方财富): best for HK / A-share markets
try:
    from .efinance_stock import (
        get_efinance_data_online,
        get_efinance_fundamentals,
        get_efinance_balance_sheet,
        get_efinance_income_statement,
        get_efinance_cashflow,
    )
    EFINANCE_AVAILABLE = True
except ImportError:
    EFINANCE_AVAILABLE = False

# Polygon vendor: best for US news, alt OHLC source for US stocks
try:
    from .polygon_stock import (
        get_polygon_stock_data,
        get_polygon_news,
        get_polygon_global_news,
        get_polygon_fundamentals,
        get_polygon_balance_sheet,
        get_polygon_income_statement,
        get_polygon_cashflow,
        get_polygon_insider_transactions,
        PolygonRateLimitError,
    )
    POLYGON_AVAILABLE = True
except ImportError:
    POLYGON_AVAILABLE = False
    PolygonRateLimitError = None

# local_parquet vendor: sh_quant data_cache 本地读, 跟 Billionaire 数据底座一致.
# 只在 ~/.market_data/stocks/ 存在时才注册——不依赖该路径的部署不受影响.
try:
    from .local_parquet_stock import (
        get_local_parquet_data,
        get_local_parquet_fundamentals,
        get_local_parquet_balance_sheet,
        get_local_parquet_income_statement,
        get_local_parquet_cashflow,
        is_available as _local_parquet_is_available,
    )
    LOCAL_PARQUET_AVAILABLE = _local_parquet_is_available()
except ImportError:
    LOCAL_PARQUET_AVAILABLE = False

# Configuration and routing logic
from .config import get_config

# Tools organized by category
TOOLS_CATEGORIES = {
    "core_stock_apis": {
        "description": "OHLCV stock price data",
        "tools": [
            "get_stock_data"
        ]
    },
    "technical_indicators": {
        "description": "Technical analysis indicators",
        "tools": [
            "get_indicators"
        ]
    },
    "fundamental_data": {
        "description": "Company fundamentals",
        "tools": [
            "get_fundamentals",
            "get_balance_sheet",
            "get_cashflow",
            "get_income_statement"
        ]
    },
    "news_data": {
        "description": "News and insider data",
        "tools": [
            "get_news",
            "get_global_news",
            "get_insider_transactions",
        ]
    }
}

VENDOR_LIST = (
    ["yfinance", "alpha_vantage"]
    + (["efinance"] if EFINANCE_AVAILABLE else [])
    + (["polygon"] if POLYGON_AVAILABLE else [])
    + (["local_parquet"] if LOCAL_PARQUET_AVAILABLE else [])
)

# Mapping of methods to their vendor-specific implementations
VENDOR_METHODS = {
    # core_stock_apis
    "get_stock_data": {
        "alpha_vantage": get_alpha_vantage_stock,
        "yfinance": get_YFin_data_online,
        **({"efinance": get_efinance_data_online} if EFINANCE_AVAILABLE else {}),
        **({"polygon": get_polygon_stock_data} if POLYGON_AVAILABLE else {}),
        **({"local_parquet": get_local_parquet_data} if LOCAL_PARQUET_AVAILABLE else {}),
    },
    # technical_indicators
    "get_indicators": {
        "alpha_vantage": get_alpha_vantage_indicator,
        "yfinance": get_stock_stats_indicators_window,
        # efinance/polygon/local_parquet 没有独立的 indicator 接口，技术指标统一由 stockstats
        # 在 OHLC 上算; stockstats_utils.load_ohlcv 已经按 ticker 自动选 vendor。
        # 这里复用 yfinance 的 wrapper，底层会自动切 vendor。
        **({"efinance": get_stock_stats_indicators_window} if EFINANCE_AVAILABLE else {}),
        **({"polygon": get_stock_stats_indicators_window} if POLYGON_AVAILABLE else {}),
        **({"local_parquet": get_stock_stats_indicators_window} if LOCAL_PARQUET_AVAILABLE else {}),
    },
    # fundamental_data
    "get_fundamentals": {
        "alpha_vantage": get_alpha_vantage_fundamentals,
        "yfinance": get_yfinance_fundamentals,
        **({"efinance": get_efinance_fundamentals} if EFINANCE_AVAILABLE else {}),
        **({"polygon": get_polygon_fundamentals} if POLYGON_AVAILABLE else {}),
        **({"local_parquet": get_local_parquet_fundamentals} if LOCAL_PARQUET_AVAILABLE else {}),
    },
    "get_balance_sheet": {
        "alpha_vantage": get_alpha_vantage_balance_sheet,
        "yfinance": get_yfinance_balance_sheet,
        **({"efinance": get_efinance_balance_sheet} if EFINANCE_AVAILABLE else {}),
        **({"polygon": get_polygon_balance_sheet} if POLYGON_AVAILABLE else {}),
        **({"local_parquet": get_local_parquet_balance_sheet} if LOCAL_PARQUET_AVAILABLE else {}),
    },
    "get_cashflow": {
        "alpha_vantage": get_alpha_vantage_cashflow,
        "yfinance": get_yfinance_cashflow,
        **({"efinance": get_efinance_cashflow} if EFINANCE_AVAILABLE else {}),
        **({"polygon": get_polygon_cashflow} if POLYGON_AVAILABLE else {}),
        **({"local_parquet": get_local_parquet_cashflow} if LOCAL_PARQUET_AVAILABLE else {}),
    },
    "get_income_statement": {
        "alpha_vantage": get_alpha_vantage_income_statement,
        "yfinance": get_yfinance_income_statement,
        **({"efinance": get_efinance_income_statement} if EFINANCE_AVAILABLE else {}),
        **({"polygon": get_polygon_income_statement} if POLYGON_AVAILABLE else {}),
        **({"local_parquet": get_local_parquet_income_statement} if LOCAL_PARQUET_AVAILABLE else {}),
    },
    # news_data
    "get_news": {
        "alpha_vantage": get_alpha_vantage_news,
        "yfinance": get_news_yfinance,
        **({"polygon": get_polygon_news} if POLYGON_AVAILABLE else {}),
    },
    "get_global_news": {
        "yfinance": get_global_news_yfinance,
        "alpha_vantage": get_alpha_vantage_global_news,
        **({"polygon": get_polygon_global_news} if POLYGON_AVAILABLE else {}),
    },
    "get_insider_transactions": {
        "alpha_vantage": get_alpha_vantage_insider_transactions,
        "yfinance": get_yfinance_insider_transactions,
        **({"polygon": get_polygon_insider_transactions} if POLYGON_AVAILABLE else {}),
    },
}

def get_category_for_method(method: str) -> str:
    """Get the category that contains the specified method."""
    for category, info in TOOLS_CATEGORIES.items():
        if method in info["tools"]:
            return category
    raise ValueError(f"Method '{method}' not found in any category")

def get_vendor(category: str, method: str = None) -> str:
    """Get the configured vendor for a data category or specific tool method.
    Tool-level configuration takes precedence over category-level.
    """
    config = get_config()

    # Check tool-level configuration first (if method provided)
    if method:
        tool_vendors = config.get("tool_vendors", {})
        if method in tool_vendors:
            return tool_vendors[method]

    # Fall back to category-level configuration
    return config.get("data_vendors", {}).get(category, "default")

def _ticker_from_call(method: str, args, kwargs) -> str:
    """Best-effort: 从 method 的调用参数里提取 ticker。
    绝大多数 dataflows 方法的第一个位置参数都是 ticker/symbol/query。
    """
    if args:
        return str(args[0])
    for key in ("symbol", "ticker", "query"):
        if key in kwargs:
            return str(kwargs[key])
    return ""


def _resolve_auto(method: str, args, kwargs) -> list:
    """把 'auto' 展开成实际 vendor 列表。

    路由策略（按 ticker 市场 + method 类型）：

    中港股（.HK / .SS / .SH / .SZ）：
        所有方法都优先 efinance，yfinance 兜底（Polygon 不支持）

    美股：
        新闻类（news / global_news）   → polygon 优先（覆盖最好），yfinance 兜底
        基本面（fundamentals/balance/cashflow/income）→ yfinance 优先（financials 最全），polygon 兜底
        OHLC / 指标 / insider          → yfinance 优先（已验证准），polygon 兜底
    """
    ticker = _ticker_from_call(method, args, kwargs).strip().upper()
    is_cn_hk = (
        ticker.endswith(".HK") or ticker.endswith(".SS")
        or ticker.endswith(".SH") or ticker.endswith(".SZ")
    )

    # local_parquet 总是排第一 (如果 vendor 注册了) —— 数据已在本地, 零成本零延迟,
    # miss 时自动 fallback 到下游远程 vendor.
    # 新闻类 (get_news/get_global_news/get_insider_transactions) 没本地数据,
    # local_parquet 不参与, 走原路径.
    news_methods = {"get_news", "get_global_news", "get_insider_transactions"}
    local_first = LOCAL_PARQUET_AVAILABLE and method not in news_methods

    if is_cn_hk:
        # 中港股：local_parquet → efinance → yfinance
        chain = []
        if local_first:
            chain.append("local_parquet")
        if EFINANCE_AVAILABLE:
            chain.append("efinance")
        chain.extend(["yfinance", "alpha_vantage"])
        return chain

    # 美股 - 新闻类 (没本地): Polygon → yfinance
    if method in news_methods and POLYGON_AVAILABLE:
        return ["polygon", "yfinance", "alpha_vantage"]

    # 美股 OHLC/指标/基本面: local_parquet → yfinance → polygon → alpha_vantage
    chain = []
    if local_first:
        chain.append("local_parquet")
    chain.extend(["yfinance", "alpha_vantage"])
    if POLYGON_AVAILABLE:
        chain.append("polygon")
    if EFINANCE_AVAILABLE:
        chain.append("efinance")
    return chain


def route_to_vendor(method: str, *args, **kwargs):
    """Route method calls to appropriate vendor implementation with fallback support.

    Supports a special vendor name 'auto' that dispatches by ticker suffix:
      - .HK / .SS / .SH / .SZ → efinance (中港股)
      - 其余                 → yfinance (美股)
    """
    category = get_category_for_method(method)
    vendor_config = get_vendor(category, method)
    primary_vendors = [v.strip() for v in vendor_config.split(',')]

    # Expand 'auto' to a ticker-aware vendor chain
    expanded = []
    for v in primary_vendors:
        if v == "auto":
            expanded.extend(_resolve_auto(method, args, kwargs))
        else:
            expanded.append(v)
    primary_vendors = expanded

    if method not in VENDOR_METHODS:
        raise ValueError(f"Method '{method}' not supported")

    # Build fallback chain: primary vendors first, then remaining available vendors
    all_available_vendors = list(VENDOR_METHODS[method].keys())
    fallback_vendors = primary_vendors.copy()
    for vendor in all_available_vendors:
        if vendor not in fallback_vendors:
            fallback_vendors.append(vendor)

    last_error = None
    for vendor in fallback_vendors:
        if vendor not in VENDOR_METHODS[method]:
            continue

        vendor_impl = VENDOR_METHODS[method][vendor]
        impl_func = vendor_impl[0] if isinstance(vendor_impl, list) else vendor_impl

        try:
            result = impl_func(*args, **kwargs)
            # 一些 vendor 对不支持的市场会返回明确字符串（"efinance 失败"、"Polygon 不支持"、
            # "未实现"、"local_parquet ... 未找到"），这些情况也触发 fallback
            if isinstance(result, str):
                fail_markers = (
                    "efinance ", "Polygon 不支持", "Polygon ", "未实现",
                    "local_parquet ",
                )
                if any(marker in result and ("失败" in result or "不支持" in result
                                              or "未实现" in result or "未找到" in result
                                              or "数据为空" in result)
                       for marker in fail_markers):
                    last_error = result
                    continue
            return result
        except AlphaVantageRateLimitError:
            continue  # Only rate limits trigger fallback
        except Exception as e:
            # Polygon 限速也走 fallback
            if PolygonRateLimitError is not None and isinstance(e, PolygonRateLimitError):
                last_error = e
                continue
            last_error = e
            continue  # 通用异常也回退到下一个 vendor

    raise RuntimeError(f"No available vendor for '{method}'. Last error: {last_error}")