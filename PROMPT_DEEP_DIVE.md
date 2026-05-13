# TradingAgents Prompt 详解与运行指南

> 针对 4 大核心模块的 prompt 设计拆解，外加本地跑起来的完整步骤。
> 代码引用均给出文件路径，方便你直接跳过去对照读。

---

## 一、4 个分析师（Analyst Team）

4 个分析师是**流水线串行**执行的（`graph/setup.py` 里默认顺序是 market → social → news → fundamentals），每个分析师输出一份独立报告存到 state 里。

### 1.1 统一的"协作 system prompt"模板

4 个分析师共用同一个顶层 system 模板（`agents/analysts/market_analyst.py:55-64` 等）：

```
You are a helpful AI assistant, collaborating with other assistants.
Use the provided tools to progress towards answering the question.
If you are unable to fully answer, that's OK; another assistant with different tools will help where you left off. Execute what you can to make progress.
If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable, prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop.
You have access to the following tools: {tool_names}.
{system_message}
For your reference, the current date is {current_date}. {instrument_context}
```

**设计要点：**
- 明确告诉 LLM："你不是独自工作的，后面还有别人接力"——这降低了 LLM 在信息不全时硬憋答案的倾向；
- 预埋了**全局停止信号** `FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`，任一 agent 喊出这句话整条链就能停下；
- 用 `partial` 把 `tool_names` / `current_date` / `instrument_context`（股票基本信息）预填进去，避免 LLM 每轮都要重复读；
- 末尾的 `{instrument_context}` 是 `build_instrument_context(ticker)` 生成的，把交易日历、交易所、货币等写在 prompt 里，防止 LLM 搞错公司。

### 1.2 Market Analyst（技术面）—— **最值得抄的 prompt**

文件：`tradingagents/agents/analysts/market_analyst.py:22-50`

这是全项目最精巧的 prompt 之一。核心思路是**把 13 个指标按类别列出来，每个都附 Usage + Tips**，然后强制 LLM 最多选 8 个**互补**的指标：

```
Your role is to select the **most relevant indicators** for a given market condition 
or trading strategy from the following list. The goal is to choose up to **8 indicators** 
that provide complementary insights without redundancy.

Moving Averages:
- close_50_sma: 50 SMA: A medium-term trend indicator. Usage: ... Tips: ...
- close_200_sma: 200 SMA: A long-term trend benchmark. Usage: ... Tips: ...
- close_10_ema: 10 EMA: A responsive short-term average. Usage: ... Tips: ...

MACD Related:
- macd / macds / macdh: ...

Momentum Indicators:
- rsi: ... Tips: In strong trends, RSI may remain extreme; 
       always cross-check with trend analysis.

Volatility Indicators:
- boll / boll_ub / boll_lb / atr: ...

Volume-Based Indicators:
- vwma: ...

- Select indicators that provide diverse and complementary information. 
  Avoid redundancy (e.g., do not select both rsi and stochrsi). 
  Also briefly explain why they are suitable for the given market context.
- When you tool call, please use the exact name of the indicators provided above.
- Please make sure to call get_stock_data first to retrieve the CSV 
  that is needed to generate indicators.
```

**可以抄的设计模式：**

1. **"菜单式"工具 prompt**：不是让 LLM 开放式选指标，而是给一张附带使用说明+陷阱提示的菜单。这种设计适用于任何"有一堆工具/参数但 LLM 容易用错"的场景。
2. **互补性约束**：`"do not select both rsi and stochrsi"` 这种具体的反例，比抽象地说"选多样化的指标"更有效。
3. **工具调用顺序约束**：`"Please make sure to call get_stock_data first"`——明确依赖关系，避免 LLM 跳过必需步骤。
4. **精确的 ID 约束**：`"use the exact name of the indicators provided above as they are defined parameters, otherwise your call will fail"`——告诉 LLM 这是严格参数名，降低幻觉风险。
5. **结尾固定的总结要求**：`"Make sure to append a Markdown table at the end of the report"`——让下游 agent 更容易读取关键信息。

### 1.3 Fundamentals Analyst（基本面）

文件：`tradingagents/agents/analysts/fundamentals_analyst.py:26-31`

```
You are a researcher tasked with analyzing fundamental information over the past week 
about a company. Please write a comprehensive report of the company's fundamental 
information such as financial documents, company profile, basic company financials, 
and company financial history to gain a full view of the company's fundamental 
information to inform traders. Make sure to include as much detail as possible. 
Provide specific, actionable insights with supporting evidence to help traders 
make informed decisions.

Use the available tools: 
`get_fundamentals` for comprehensive company analysis, 
`get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements.
```

**相对 market analyst 就朴素很多**——没有菜单、没有互补性约束。说明作者认为基本面分析更"开放"，让 LLM 自主决定读哪些财报即可。

### 1.4 News Analyst（新闻）

文件：`tradingagents/agents/analysts/news_analyst.py:22-25`

```
You are a news researcher tasked with analyzing recent news and trends over the past week. 
Please write a comprehensive report of the current state of the world that is relevant 
for trading and macroeconomics. Use the available tools: 
  get_news(query, start_date, end_date) for company-specific or targeted news searches, 
  get_global_news(curr_date, look_back_days, limit) for broader macroeconomic news.
```

**要点**：强调"过去一周"+"公司新闻 + 宏观新闻"双维度。工具签名直接写在 prompt 里（带参数名），这种做法能有效减少参数错误。

### 1.5 Social Media Analyst（情绪）

文件：`tradingagents/agents/analysts/social_media_analyst.py:15-18`

```
You are a social media and company specific news researcher/analyst tasked with 
analyzing social media posts, recent company news, and public sentiment for a 
specific company over the past week. You will be given a company's name, your 
objective is to write a comprehensive long report detailing your analysis, 
insights, and implications for traders and investors on this company's current 
state after looking at social media and what people are saying about that company, 
analyzing sentiment data of what people feel each day about the company, and 
looking at recent company news. Use the get_news(query, start_date, end_date) 
tool to search for company-specific news and social media discussions.
```

**有意思的细节**：这个 agent 其实**只有一个工具** `get_news`（项目里没有真正的社媒 API）。作者用 prompt "硬说"它是在分析社媒——LLM 会根据这个框架输出社媒风格的情绪解读。这是一种"用 prompt 来补位缺失数据源"的妥协，实际效果一般。**如果要真实用，建议接入真实的 Twitter/Reddit API 替换掉**。

### 1.6 分析师流水线的运行机制

`graph/setup.py:134-152` 里每个分析师节点都接了：

```
Market Analyst → [conditional] → tools_market → Market Analyst (循环直到不再调工具)
                              ↘ Msg Clear Market → Social Analyst (进入下一个)
```

每个分析师跑完后有个 `Msg Clear` 节点**清空上下文 messages**，只保留各自报告存到 state。这样下一个分析师不会被前一个的 tool-call 历史污染。**这个"报告传递、历史清零"的隔离设计很值得参考**——在多 agent 流水线里防止 context 爆炸很有用。

---

## 二、多空辩论（Bull / Bear / Research Manager）

### 2.1 Bull Researcher

文件：`tradingagents/agents/researchers/bull_researcher.py:22-40`

```python
prompt = f"""You are a Bull Analyst advocating for investing in the stock. 
Your task is to build a strong, evidence-based case emphasizing growth potential, 
competitive advantages, and positive market indicators. Leverage the provided 
research and data to address concerns and counter bearish arguments effectively.

Key points to focus on:
- Growth Potential: Highlight the company's market opportunities, revenue projections, and scalability.
- Competitive Advantages: Emphasize factors like unique products, strong branding, or dominant market positioning.
- Positive Indicators: Use financial health, industry trends, and recent positive news as evidence.
- Bear Counterpoints: Critically analyze the bear argument with specific data and sound reasoning, 
  addressing concerns thoroughly and showing why the bull perspective holds stronger merit.
- Engagement: Present your argument in a conversational style, engaging directly with the bear 
  analyst's points and debating effectively rather than just listing data.

Resources available:
Market research report: {market_research_report}
Social media sentiment report: {sentiment_report}
Latest world affairs news: {news_report}
Company fundamentals report: {fundamentals_report}
Conversation history of the debate: {history}
Last bear argument: {current_response}
Reflections from similar situations and lessons learned: {past_memory_str}

Use this information to deliver a compelling bull argument, refute the bear's concerns, 
and engage in a dynamic debate that demonstrates the strengths of the bull position. 
You must also address reflections and learn from lessons and mistakes you made in the past.
"""
```

### 2.2 Bear Researcher

文件：`tradingagents/agents/researchers/bear_researcher.py:22-42`

Bear 的 prompt 是 Bull 的**镜像**，只是替换关键词：Growth Potential → Risks & Challenges、Competitive Advantages → Competitive Weaknesses、Positive Indicators → Negative Indicators。

### 2.3 辩论 prompt 的 5 个设计亮点

1. **角色分离 + 强制对抗**：分成两个独立 agent，每个都在自己的 prompt 里被明确告知"只讲一面"，避免单个 LLM 同时考虑两面时的中立化倾向。
2. **直接引用对手论点**：`Last bear argument: {current_response}` 把对手的**最新一条**发言插进来，强制形成"接话辩论"而不是各说各话。
3. **历史全部入场**：`Conversation history of the debate: {history}` 把所有轮次的发言都带进来，让 LLM 看到辩论轨迹（不是每次都从零开始）。
4. **四份报告 + 记忆同时入场**：4 个分析师的报告 + BM25 检索出的 2 条历史相似案例的教训都塞进 prompt，信息极密集。
5. **"对话化"风格要求**：`Engagement: Present your argument in a conversational style, engaging directly with the bear analyst's points and debating effectively rather than just listing data.`——强制对话风格而非 bullet list 罗列。

### 2.4 Research Manager（裁判）

文件：`tradingagents/agents/managers/research_manager.py:23-41`

```python
prompt = f"""As the portfolio manager and debate facilitator, your role is to 
critically evaluate this round of debate and make a definitive decision: 
align with the bear analyst, the bull analyst, or choose Hold only if it 
is strongly justified based on the arguments presented.

Summarize the key points from both sides concisely, focusing on the most 
compelling evidence or reasoning. Your recommendation—Buy, Sell, or Hold—
must be clear and actionable. Avoid defaulting to Hold simply because both 
sides have valid points; commit to a stance grounded in the debate's strongest arguments.

Additionally, develop a detailed investment plan for the trader. This should include:
  Your Recommendation: A decisive stance supported by the most convincing arguments.
  Rationale: An explanation of why these arguments lead to your conclusion.
  Strategic Actions: Concrete steps for implementing the recommendation.

Take into account your past mistakes on similar situations. Use these insights 
to refine your decision-making and ensure you are learning and improving. 
Present your analysis conversationally, as if speaking naturally, without special formatting.
"""
```

**关键设计**：
- **反 Hold 偏置**：`"Avoid defaulting to Hold simply because both sides have valid points"`——LLM 做裁判时极容易为了"安全"而选折中，这句话直接把这个倾向堵死了。这是**全项目最聪明的一句 prompt**，任何做决策裁判类 agent 都应该抄。
- **输出三段式结构**：Recommendation / Rationale / Strategic Actions。不是自由发挥，是结构化输出。
- **自然语言而非 markdown**：`"without special formatting"`——后面 trader 要直接把这段当计划读，不要花哨格式。

---

## 三、风险三方辩论 + Portfolio Manager

### 3.1 三方辩论的路由设计

`graph/setup.py:172-196`：

```
Trader → Aggressive → [cond] → Conservative → [cond] → Neutral → [cond] → Aggressive → ...
                                                                            ↘ Portfolio Manager
```

3 方**按固定顺序循环**，直到 `max_risk_discuss_rounds` 用尽。每个辩论节点都能看到**另外两方的最新发言**。

### 3.2 Aggressive / Conservative / Neutral 的 prompt 骨架

三者结构几乎一样，只是**角色定位和论证方向不同**。以 Aggressive 为例（`risk_mgmt/aggressive_debator.py:19-31`）：

```
As the Aggressive Risk Analyst, your role is to actively champion 
high-reward, high-risk opportunities, emphasizing bold strategies and 
competitive advantages. When evaluating the trader's decision or plan, 
focus intently on the potential upside, growth potential, and innovative 
benefits—even when these come with elevated risk. Use the provided market 
data and sentiment analysis to strengthen your arguments and challenge 
the opposing views. Specifically, respond directly to each point made by 
the conservative and neutral analysts, countering with data-driven rebuttals 
and persuasive reasoning.
...
Here is the trader's decision: {trader_decision}
...
Here are the last arguments from the conservative analyst: {current_conservative_response}
Here are the last arguments from the neutral analyst: {current_neutral_response}.
If there are no responses from the other viewpoints yet, present your own argument 
based on the available data.
```

**Conservative** 把"upside / bold / high-reward"替换成"stability / risk mitigation / steady growth"；
**Neutral** 的定位是"balanced perspective, weighing both ... pointing out where each perspective may be overly optimistic or overly cautious"。

### 3.3 三方设计的好处

1. **强制考虑多视角**：单一"风险分析师"容易被 trader 的意见带节奏；三方互相 check 能发现盲点。
2. **Neutral 的"双向攻击"**：Neutral 不是简单的折中，而是**同时攻击** Aggressive 和 Conservative 的过度倾向。这个角色设定把"折中"从被动变成主动批判。
3. **冷启动容错**：`"If there are no responses from the other viewpoints yet, present your own argument based on the available data"`——处理第一轮的冷启动，避免 LLM 因为 `{current_X_response}` 为空而混乱。

### 3.4 Portfolio Manager 的 5 档评级

文件：`tradingagents/agents/managers/portfolio_manager.py:25-55`

```python
prompt = f"""As the Portfolio Manager, synthesize the risk analysts' debate 
and deliver the final trading decision.

**Rating Scale** (use exactly one):
- **Buy**: Strong conviction to enter or add to position
- **Overweight**: Favorable outlook, gradually increase exposure
- **Hold**: Maintain current position, no action needed
- **Underweight**: Reduce exposure, take partial profits
- **Sell**: Exit position or avoid entry

**Context:**
- Research Manager's investment plan: {research_plan}
- Trader's transaction proposal: {trader_plan}
- Lessons from past decisions: {past_memory_str}

**Required Output Structure:**
1. **Rating**: State one of Buy / Overweight / Hold / Underweight / Sell.
2. **Executive Summary**: A concise action plan covering entry strategy, 
   position sizing, key risk levels, and time horizon.
3. **Investment Thesis**: Detailed reasoning anchored in the analysts' debate 
   and past reflections.

Be decisive and ground every conclusion in specific evidence from the analysts.
"""
```

**亮点：**
- **5 档评级 vs 3 档**：比单纯 Buy/Hold/Sell 多出 Overweight / Underweight，允许"逐步加仓 / 逐步减仓"的**中间态**。这更贴近真实的 portfolio 管理逻辑（实战中一次性全进/全出不常见）。
- **每档有行动定义**：不是光给个标签，而是说明"Buy = 强入场、Overweight = 逐步加"。避免 LLM 理解成简单分数。
- **输出结构强制三段式**：Rating → Executive Summary → Investment Thesis。**Executive Summary 还要求包含 entry strategy / position sizing / key risk levels / time horizon 四要素**，这就是实战交易方案的核心要素。

---

## 四、记忆与反思机制

### 4.1 BM25 轻量记忆系统

文件：`tradingagents/agents/utils/memory.py`

**不用向量数据库、不用 embedding API**，用 rank-bm25 做词频相似度：

```python
class FinancialSituationMemory:
    def __init__(self, name: str, config: dict = None):
        self.documents = []       # 历史"市场情境"文本
        self.recommendations = [] # 对应的"教训/建议"
        self.bm25 = None

    def _tokenize(self, text: str) -> List[str]:
        # 简单的小写 + 非字母数字分割
        return re.findall(r'\b\w+\b', text.lower())

    def add_situations(self, situations_and_advice):
        for situation, recommendation in situations_and_advice:
            self.documents.append(situation)
            self.recommendations.append(recommendation)
        self._rebuild_index()

    def get_memories(self, current_situation: str, n_matches: int = 1):
        query_tokens = self._tokenize(current_situation)
        scores = self.bm25.get_scores(query_tokens)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n_matches]
        # 返回 top-n 匹配
```

**为什么这个设计值得学：**
- 离线、零成本、零依赖 API——适合本地实验和低成本部署；
- 检索延迟 O(n)，对几百条历史记录完全够用；
- 当中文 embedding 服务访问受限时是个很好的兜底方案；
- 如果后续数据量大了，只需要把 `BM25Okapi` 换成向量数据库，接口不变。

**每个关键 agent 有独立的 memory 实例**（`graph/setup.py` 和 `trading_graph.py` 里初始化）：
- `bull_memory` / `bear_memory`：多空研究员的历史教训
- `trader_memory`：交易员的过去决策
- `invest_judge_memory`：Research Manager 的历史判断
- `portfolio_manager_memory`：组合经理的历史判断

### 4.2 检索时机与用法

每次 agent 被调用时，先把当前"市场情境"（4 份报告拼起来）作为 query：

```python
curr_situation = f"{market_report}\n\n{sentiment_report}\n\n{news_report}\n\n{fundamentals_report}"
past_memories = memory.get_memories(curr_situation, n_matches=2)

past_memory_str = ""
for rec in past_memories:
    past_memory_str += rec["recommendation"] + "\n\n"

# 然后把 past_memory_str 塞进 prompt 的 "Reflections from similar situations" 部分
```

注意：**检索出来的不是"过去做了什么"，而是"过去错在哪里、下次该怎么改"**——因为存进去的本来就是反思结果（见下一节）。

### 4.3 反思 prompt（核心学习逻辑）

文件：`tradingagents/graph/reflection.py:16-46`

```
You are an expert financial analyst tasked with reviewing trading decisions/analysis 
and providing a comprehensive, step-by-step analysis. 
Your goal is to deliver detailed insights into investment decisions and highlight 
opportunities for improvement, adhering strictly to the following guidelines:

1. Reasoning:
   - For each trading decision, determine whether it was correct or incorrect. 
     A correct decision results in an increase in returns, while an incorrect 
     decision does the opposite.
   - Analyze the contributing factors to each success or mistake. Consider:
     - Market intelligence.
     - Technical indicators / signals.
     - Price movement analysis.
     - News / Social media / Sentiment / Fundamental data analysis.
     - Weight the importance of each factor in the decision-making process.

2. Improvement:
   - For any incorrect decisions, propose revisions to maximize returns.
   - Provide a detailed list of corrective actions or improvements, including specific 
     recommendations (e.g., changing a decision from HOLD to BUY on a particular date).

3. Summary:
   - Summarize the lessons learned from the successes and mistakes.
   - Highlight how these lessons can be adapted for future trading scenarios 
     and draw connections between similar situations to apply the knowledge gained.

4. Query:
   - Extract key insights from the summary into a concise sentence of no more than 1000 tokens.
   - Ensure the condensed sentence captures the essence of the lessons and reasoning 
     for easy reference.
```

### 4.4 反思的四段式非常值得抄

`Reasoning → Improvement → Summary → Query` 这个结构解决了几个常见问题：

1. **Reasoning（归因）**：不是只给结论"错了"，而是**逐因素列出**"哪个因素权重多少、哪里出错"。
2. **Improvement（修正建议）**：要求给出**具体可执行的反事实**（例如"该日应该把 HOLD 改成 BUY"），避免空洞的"下次注意"。
3. **Summary（抽象教训）**：从具体案例上升到可迁移的规则。
4. **Query（<1000 token 精炼）**：专门为 BM25 检索压缩一句话——这一步是**将"反思文本"压缩成"可检索的 query key"**，非常实用。

### 4.5 反思如何回灌记忆

`graph/reflection.py:72-120` 里每个关键 agent 都有对应的反思函数：

```python
def reflect_bull_researcher(self, current_state, returns_losses, bull_memory):
    situation = self._extract_current_situation(current_state)
    bull_debate_history = current_state["investment_debate_state"]["bull_history"]
    result = self._reflect_on_component("BULL", bull_debate_history, situation, returns_losses)
    bull_memory.add_situations([(situation, result)])
```

**流程**：
1. 提取当前市场情境（4 份报告）作为 situation；
2. 提取该 agent 当时的发言/决策作为 analysis；
3. 带上真实收益 `returns_losses` 让 LLM 判断对错；
4. 生成反思文本 → 作为新记忆存回该 agent 的 memory。

下次遇到类似情境时，BM25 会检索到这条反思，放进 prompt 的"Reflections from similar situations"段落里。**这是项目的闭环学习机制**。

调用时机（`trading_graph.py`）：

```python
ta.reflect_and_remember(returns_losses)  # 用户在交易结算后手动调用
```

### 4.6 这套记忆 + 反思可借鉴的点总结

| 设计 | 为什么好 |
|------|---------|
| 每个 agent 独立 memory | 避免不同角色的教训互相污染（bull 学的是"乐观陷阱"、bear 学的是"悲观陷阱"） |
| 存的是反思而非原始发言 | 检索出来直接就是"教训"，不用再二次总结 |
| 反思四段式 | 归因 → 反事实 → 抽象 → 压缩检索 key，每一步都有明确用途 |
| BM25 无 API 检索 | 零依赖、零费用、零延迟 |
| 反思手动触发 | 用户有权决定何时学习（避免污染、允许 A/B 对比） |

---

## 五、跑起来的完整步骤（TSLA + DeepSeek）

### 5.1 环境准备

```bash
# 进入项目目录
cd /Users/helm/Documents/Code/github/TradingAgents

# 建虚拟环境（推荐 Python 3.13）
conda create -n tradingagents python=3.13 -y
conda activate tradingagents

# 安装依赖
pip install .

# 注：项目依赖 python-dotenv、langgraph、langchain、yfinance、rank-bm25、
#     stockstats、backtrader、typer、rich、questionary 等
```

如果不用 conda，用 venv 也行：

```bash
cd /Users/helm/Documents/Code/github/TradingAgents
python3 -m venv .venv
source .venv/bin/activate
pip install .
```

### 5.2 配置 API Key

```bash
# 复制模板
cp .env.example .env

# 编辑 .env，只需填这一行（其他可以留空）
# DEEPSEEK_API_KEY=你的_deepseek_key
```

DeepSeek API key 在 https://platform.deepseek.com/api_keys 申请，新账号通常送免费额度。

### 5.3 修改 main.py 跑 TSLA

把 `/Users/helm/Documents/Code/github/TradingAgents/main.py` 改成下面这样：

```python
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.default_config import DEFAULT_CONFIG
from dotenv import load_dotenv

load_dotenv()

config = DEFAULT_CONFIG.copy()

# --- 换成 DeepSeek ---
config["llm_provider"] = "deepseek"
config["backend_url"] = "https://api.deepseek.com"
config["deep_think_llm"] = "deepseek-reasoner"   # 深度推理（辩论/裁判）
config["quick_think_llm"] = "deepseek-chat"      # 快速任务（分析师/风险辩论）

# --- 流程控制 ---
config["max_debate_rounds"] = 1          # 多空辩论轮数（1=最少跑通）
config["max_risk_discuss_rounds"] = 1    # 风险三方辩论轮数
config["output_language"] = "Chinese"    # 最终报告用中文输出（内部辩论仍用英文）

# --- 数据源：用 yfinance（免费，无需额外 key） ---
config["data_vendors"] = {
    "core_stock_apis": "yfinance",
    "technical_indicators": "yfinance",
    "fundamental_data": "yfinance",
    "news_data": "yfinance",
}

ta = TradingAgentsGraph(debug=True, config=config)

# 分析 TSLA，日期选一个工作日
_, decision = ta.propagate("TSLA", "2026-04-17")
print(decision)
```

### 5.4 运行

```bash
# 方式 1：直接跑 main.py（最简单，看日志流）
python main.py

# 方式 2：跑 CLI（交互式，有漂亮的 Rich 界面）
python -m cli.main
# 或
tradingagents
```

**日志输出位置**：`~/.tradingagents/logs/`（可通过 `TRADINGAGENTS_RESULTS_DIR` 环境变量改）
**数据缓存位置**：`~/.tradingagents/cache/`

### 5.5 观察流程的建议

第一次跑的时候 `debug=True` 已经开了，会在终端打印每个节点的输入输出。建议这样观察：

1. **先看 4 份分析师报告**（terminal 里会依次出现 Market/Social/News/Fundamentals Analyst 的报告）——确认数据拉到了；
2. **再看 Bull/Bear 辩论**——观察两边是否真的在"接话"而不是各说各话；
3. **看 Research Manager 的投资计划**——注意它是否避免了 Hold 偏置；
4. **看 Trader 的 FINAL TRANSACTION PROPOSAL**；
5. **最后 3 个风险分析师 + Portfolio Manager 的 5 档评级**。

跑一次完整流程大约：
- **DeepSeek**：5-8 分钟，成本约 $0.05-0.20（具体看辩论轮数）
- **OpenAI GPT-5.4**：3-5 分钟，成本约 $1-3
- **本地 Ollama**：取决于显卡，可能 20-60 分钟

### 5.6 常见坑

| 坑 | 解决 |
|----|-----|
| yfinance 拿不到数据 | 换一个最近的工作日（周末/节假日无数据）；或切换 `alpha_vantage`（需要 `ALPHA_VANTAGE_API_KEY`） |
| DeepSeek reasoner 工具调用不稳定 | 如果工具调用经常失败，把 `deep_think_llm` 也换成 `deepseek-chat`（牺牲一点推理深度换稳定性） |
| 报 `max_recur_limit` | LangGraph 觉得某个节点陷入死循环。把 `max_recur_limit` 从 100 提到 150 |
| 想看完整日志 | 去 `~/.tradingagents/logs/` 查对应运行的目录 |
| 想省钱只跑部分分析师 | 在 `TradingAgentsGraph()` 初始化时传 `selected_analysts=["market", "news"]`（见 `graph/setup.py:40`） |

### 5.7 想更深入玩的下一步

- **加辩论轮数**：`max_debate_rounds = 3`，观察后面的辩论是否真的有新信息；
- **对比不同 LLM**：分别用 DeepSeek-reasoner 和 GPT-5.4 跑同一支股票同一天，看决策差异；
- **替换 prompt**：改 `research_manager.py` 里的"Avoid defaulting to Hold"那句话试试，看裁判行为变化；
- **记忆冷启动**：跑 5-10 次不同日期不同股票，手动 `ta.reflect_and_remember(returns)`，观察后续决策是否受历史记忆影响；
- **接入真实社媒**：把 `social_media_analyst.py` 的 `get_news` 换成真正的 Twitter / Reddit API，看情绪分析质量变化。

---

## 六、快速导航

| 关键文件 | 作用 |
|---------|-----|
| `main.py` | 最简入口，直接跑 `TradingAgentsGraph.propagate()` |
| `cli/main.py` | 交互式 CLI（Rich 界面） |
| `tradingagents/default_config.py` | 所有配置项 |
| `tradingagents/graph/trading_graph.py` | `TradingAgentsGraph` 主类，初始化所有 memory/LLM |
| `tradingagents/graph/setup.py` | LangGraph 图的节点和边定义 |
| `tradingagents/graph/conditional_logic.py` | 辩论循环的路由条件 |
| `tradingagents/graph/reflection.py` | 反思 prompt + 回灌记忆 |
| `tradingagents/agents/utils/agent_states.py` | 所有 state 的 TypedDict 定义 |
| `tradingagents/agents/utils/memory.py` | BM25 记忆系统 |
| `tradingagents/agents/analysts/market_analyst.py` | 技术面 prompt（最精巧） |
| `tradingagents/agents/researchers/bull_researcher.py` | 多方辩论 prompt |
| `tradingagents/agents/managers/research_manager.py` | 裁判 prompt（反 Hold 偏置） |
| `tradingagents/agents/managers/portfolio_manager.py` | 5 档评级 + 三段式输出 |
