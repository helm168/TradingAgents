"""Sentiment analyst — multi-source sentiment analysis for a target ticker.

Previously named ``social_media_analyst``. Renamed and redesigned because
the old version had a prompt that demanded social-media analysis but the
only tool available was Yahoo Finance news — which led LLMs to fabricate
Reddit/X/StockTwits content under prompt pressure (verified live).

The redesigned agent pre-fetches complementary data sources before the
LLM is invoked and injects them into the prompt as structured blocks:

  1. News headlines     — Yahoo Finance (institutional framing)
  2. StockTwits messages — retail-trader posts indexed by cashtag, with
                           user-labeled Bullish/Bearish sentiment tags

(Reddit was dropped: their 2024 policy gates the Data API behind a
moderation-use-case approval, which doesn't fit sentiment research.)

The agent does not use tool-calling; the data is in the prompt from
turn 0. The LLM produces the sentiment report in a single invocation.

See: https://github.com/TauricResearch/TradingAgents/issues/557
"""

import re
from datetime import datetime, timedelta

from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from tradingagents.agents.utils.agent_utils import (
    build_instrument_context,
    get_language_instruction,
    get_news,
)
from tradingagents.dataflows.stocktwits import fetch_stocktwits_messages

# CN/HK 替代源 — 这俩 import 是 best-effort, 库没装就走 placeholder
try:
    from tradingagents.dataflows.akshare_cn_news import (
        fetch_em_news_block,
        fetch_cls_alerts_block,
    )
    _AKSHARE_AVAILABLE = True
except ImportError:
    _AKSHARE_AVAILABLE = False

try:
    from tradingagents.dataflows.tushare_cn_news import fetch_anns_block
    _TUSHARE_AVAILABLE = True
except ImportError:
    _TUSHARE_AVAILABLE = False


def _is_cn_hk(ticker: str) -> bool:
    """ticker 是不是 A 股或港股. 决定走 CN 链 还是 US 链."""
    t = ticker.strip().upper()
    return bool(re.search(r"\.(SS|SZ|SH|BJ|HK)$", t))


def _seven_days_back(trade_date: str) -> str:
    return (datetime.strptime(trade_date, "%Y-%m-%d") - timedelta(days=7)).strftime("%Y-%m-%d")


def create_sentiment_analyst(llm):
    """Create a sentiment analyst node for the trading graph.

    Pre-fetches news + StockTwits data, injects them into the prompt as
    structured blocks, and produces a sentiment report in a single LLM
    call.
    """

    def sentiment_analyst_node(state):
        ticker = state["company_of_interest"]
        end_date = state["trade_date"]
        start_date = _seven_days_back(end_date)
        instrument_context = build_instrument_context(ticker)

        # Pre-fetch sources. Each fetcher degrades gracefully and returns
        # a string (no exceptions surface from here), so the LLM always
        # sees something — either real data or a clear placeholder.
        #
        # ticker 后缀分两条链:
        #   .SS/.SZ/.SH/.BJ/.HK → AKShare (东财新闻 + 财联社快讯) + Tushare 公告
        #                          (yfinance/StockTwits 都不覆盖中文资讯)
        #   其余 (美股)         → Yahoo Finance news + StockTwits
        if _is_cn_hk(ticker):
            # CN/HK 链
            em_news_block = (
                fetch_em_news_block(ticker, limit=20)
                if _AKSHARE_AVAILABLE
                else "<akshare 未安装, 东财新闻不可用>"
            )
            anns_block = (
                fetch_anns_block(ticker, days_back=14, limit=20)
                if _TUSHARE_AVAILABLE
                else "<tushare 未安装, 公告不可用>"
            )
            cls_alerts_block = (
                fetch_cls_alerts_block(ticker, limit=20)
                if _AKSHARE_AVAILABLE
                else "<akshare 未安装, 财联社快讯不可用>"
            )
            system_message = _build_cn_system_message(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                em_news=em_news_block,
                anns=anns_block,
                cls_alerts=cls_alerts_block,
            )
        else:
            # US 链
            news_block = get_news.func(ticker, start_date, end_date)
            stocktwits_block = fetch_stocktwits_messages(ticker, limit=30)
            system_message = _build_system_message(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                news_block=news_block,
                stocktwits_block=stocktwits_block,
            )

        prompt = ChatPromptTemplate.from_messages(
            [
                (
                    "system",
                    "You are a helpful AI assistant, collaborating with other assistants."
                    " If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,"
                    " prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop."
                    "\n{system_message}\n"
                    "For your reference, the current date is {current_date}. {instrument_context}",
                ),
                MessagesPlaceholder(variable_name="messages"),
            ]
        )

        prompt = prompt.partial(system_message=system_message)
        prompt = prompt.partial(current_date=end_date)
        prompt = prompt.partial(instrument_context=instrument_context)

        # No bind_tools — the data is already in the prompt; a single LLM
        # call produces the report directly.
        chain = prompt | llm
        result = chain.invoke(state["messages"])

        return {
            "messages": [result],
            "sentiment_report": result.content,
        }

    return sentiment_analyst_node


def _build_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    news_block: str,
    stocktwits_block: str,
) -> str:
    """Assemble the sentiment-analyst system message with structured data blocks."""
    return f"""You are a financial market sentiment analyst. Your task is to produce a comprehensive sentiment report for {ticker} covering the period from {start_date} to {end_date}, drawing on two complementary data sources that have already been collected for you.

## Data sources (pre-fetched, in this prompt)

### News headlines — Yahoo Finance, past 7 days
Institutional framing. Fact-driven, slower-moving signal.

<start_of_news>
{news_block}
<end_of_news>

### StockTwits messages — retail-trader social platform indexed by cashtag
Fast-moving signal. Each message carries a user-labeled sentiment tag (Bullish / Bearish / no-label) plus the message body.

<start_of_stocktwits>
{stocktwits_block}
<end_of_stocktwits>

## How to analyze this data (best practices)

1. **Read the StockTwits Bullish/Bearish ratio as a leading retail-sentiment signal.** A 70/30 bullish/bearish split is moderately bullish; ≥90/10 may indicate over-extension and contrarian risk; 50/50 is uncertainty. Sample size matters — base rates on the actual message count, not percentages alone.

2. **Look for cross-source divergences.** If news framing is bearish but StockTwits is overwhelmingly bullish, that mismatch is itself a signal — it can mean retail is leaning into a thesis the news flow hasn't caught up to (or vice versa, that retail is chasing while institutions are cautious).

3. **Distinguish opinion from event.** A news headline ("Nvidia announces $500M Corning deal") is an event; a StockTwits post ("buying NVDA, this is going to moon") is opinion. Both are inputs but should be weighted differently in your conclusions.

4. **Identify recurring narrative themes.** What topic keeps coming up across sources? That's the dominant narrative driving current sentiment.

5. **Be honest about data limits.** If StockTwits returned only a handful of messages, or one or more sources returned an "<unavailable>" placeholder, the sentiment read is less robust — flag this caveat explicitly.

6. **Identify catalysts and risks** that emerge across sources — news of upcoming earnings, product launches, competitive threats, macro headlines, etc.

7. **Past sentiment is not predictive.** Frame your conclusions as signal for the trader to weigh alongside fundamentals and technicals, not as a price call.

## Output

Produce a sentiment report covering, in order:

1. **Overall sentiment direction** — Bullish / Bearish / Neutral / Mixed — with a brief confidence note based on data quality and sample size.
2. **Source-by-source breakdown** — what each of news / StockTwits is telling you, with specific evidence (cite message counts, ratios, notable posts).
3. **Divergences, alignments, and key narratives** across sources.
4. **Catalysts and risks** surfaced by the data.
5. **Markdown table** at the end summarizing key sentiment signals, their direction, source, and supporting evidence.

{get_language_instruction()}"""


def _build_cn_system_message(
    *,
    ticker: str,
    start_date: str,
    end_date: str,
    em_news: str,
    anns: str,
    cls_alerts: str,
) -> str:
    """CN/HK sentiment analyst system message.

    数据源跟美股完全不同 (StockTwits/Reddit/Yahoo 都不覆盖中文资讯),
    分析思路也调整: 公告权威性最高, 财联社快讯实时性最强, 东财新闻聚合
    + 解读最广. 没有 retail social tag (StockTwits Bullish/Bearish) 的
    类似指标, sentiment 从新闻语气 + 公告利好利空判断.
    """
    return f"""你是一位中文市场情绪分析师, 为 {ticker} 生成 {start_date} 至 {end_date} 期间的综合情绪报告. 三个数据源已经预先取好放在 prompt 里.

## 数据源 (已预取)

### 东方财富个股新闻 (近 7 天)
东方财富聚合多家财经媒体的个股新闻, 含转载和解读. 信息密度高, 媒体框架可见.

<start_of_em_news>
{em_news}
<end_of_em_news>

### 个股公告 (Tushare 官方公告, 近 14 天)
A 股监管要求强制披露的官方公告 — M&A / 重组 / 业绩预告 / 复牌停牌 / 高管变动 / 股权激励 等关键事件的**首发**渠道. 权威性最高, 通常领先媒体解读 1-3 天.

<start_of_anns>
{anns}
<end_of_anns>

### 财联社快讯 (近期)
A 股最及时的财经资讯流. 政策落地 / 行业动向 / 公司突发都先出在这, 比传统媒体快 30 分钟到几小时. 已按股票代码过滤相关条目 (如果原流没匹配, 会给全市场背景流以反映宏观情绪).

<start_of_cls>
{cls_alerts}
<end_of_cls>

## 怎么分析这些数据

1. **公告 = 事实, 新闻 = 解读, 快讯 = 时效.** 三类信源权威性和速度不同, 评分时区分对待:
   - 公告里出现 "重大资产重组" / "并购" / "业绩预告" / "复牌" → 高确定性事件, 直接定性 sentiment
   - 东财新闻是市场如何"解读"事件, 注意标题与正文的情绪偏差
   - 财联社快讯抓最新政策 / 行业风向, 看公司是否在受益板块或风险板块

2. **公告与新闻的时间差.** 公告先发, 新闻几小时后跟进解读. 如果只有新闻没公告 → 可能是泛行业讨论, 不是公司专属事件; 公告先于新闻出现说明事件刚刚发生, 市场尚未充分定价.

3. **关键词扫.** 中文金融语境注意识别:
   - 利好类: 业绩超预期 / 营收同比+ / 净利润+ / 重组 / 收购 / 增持 / 回购 / 利润分红 / 中标
   - 利空类: 业绩预亏 / 减持 / 商誉减值 / 立案调查 / 退市风险 / ST 处理 / 监管问询
   - 中性: 高管变动 / 股权激励 / 关联交易 / 投资者关系

4. **数据空洞要明说.** 如果某个数据块是 "<...未安装>" 或 "无返回", 在报告里明确指出, 不要凭空脑补.

5. **板块情绪 vs 个股情绪.** 财联社全市场流可能反映板块/政策面情绪, 标的本身没新闻不代表没情绪驱动 — 把板块情绪也列出来.

6. **过去 sentiment ≠ 未来价格.** 把 sentiment 作为信号给交易员, 跟基本面/技术面一起权衡, 不要直接给价格预测.

## 输出

按以下顺序产出情绪报告:

1. **总体情绪方向** — 看多 / 看空 / 中性 / 混合 — 附信心水平 (基于数据质量和样本数)
2. **分源解读** — 公告 / 东财新闻 / 财联社 各自传达了什么, 列具体证据 (引用条数 / 标题 / 关键日期)
3. **重大事件梳理** — 时间序排列, 公告优先, 然后新闻解读
4. **分歧 / 一致 / 主流叙事** — 跨源的语气差异有没有, 主流叙事是什么
5. **催化剂与风险** — 数据中浮现的近期催化或风险点
6. **Markdown 表格** — 末尾汇总关键情绪信号: 方向 / 来源 / 证据 / 时间

{get_language_instruction()}"""


# ---------------------------------------------------------------------------
# Backwards-compatibility shim
# ---------------------------------------------------------------------------
def create_social_media_analyst(llm):
    """Deprecated alias for :func:`create_sentiment_analyst`.

    Kept so existing code that imports ``create_social_media_analyst``
    continues to work.

    .. deprecated::
        Import :func:`create_sentiment_analyst` directly instead.
    """
    import warnings
    warnings.warn(
        "create_social_media_analyst is deprecated and will be removed in a "
        "future version. Use create_sentiment_analyst instead.",
        DeprecationWarning,
        stacklevel=2,
    )
    return create_sentiment_analyst(llm)
