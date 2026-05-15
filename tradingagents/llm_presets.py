"""LLM provider presets — one-line switch for run_batch / main.

每个 preset 把 4 个相关字段 (llm_provider / backend_url / deep_think_llm /
quick_think_llm) 一起设好, 用户不用记每条参数到底配啥. 想加新 provider
就往 PRESETS 里加一条, run_batch.py --llm 自动多一个选项.

用法
────
    from tradingagents.default_config import DEFAULT_CONFIG
    from tradingagents.llm_presets import apply_preset

    config = DEFAULT_CONFIG.copy()
    apply_preset(config, "xiaomi")    # 一行切到 MiMo-V2.5-Pro
    # 等价于:
    # config["llm_provider"] = "xiaomi"
    # config["backend_url"] = "https://token-plan-cn.xiaomimimo.com/v1"
    # config["deep_think_llm"] = "mimo-v2.5-pro"
    # config["quick_think_llm"] = "mimo-v2.5-pro"
"""
from __future__ import annotations

from typing import Any


PRESETS: dict[str, dict[str, Any]] = {
    "deepseek": {
        # DeepSeek 官方分 reasoner (深度) + chat (快速), 价格差 ~3-5x.
        # 深度推理走 reasoner, analysts/risk debate 类 quick 任务走 chat 省钱.
        "llm_provider": "deepseek",
        "backend_url": "https://api.deepseek.com",
        "deep_think_llm": "deepseek-reasoner",
        "quick_think_llm": "deepseek-chat",
    },
    "xiaomi": {
        # 小米 MiMo — token plan 订阅版用 token-plan-cn 专属端点 (按月限额计费,
        # 不是按 token 单价). pay-per-token 公开 API 是 api.xiaomimimo.com,
        # 这里固定 token plan 因为用户走的是月订阅档.
        #
        # deep_think → mimo-v2.5-pro (flagship reasoning, debate/judge 用)
        # quick_think → mimo-v2.5     (非 pro base 版, analysts/risk/scoring 用)
        #
        # 历史教训: deep + quick 都用 pro 时, 单 ticker 跑下来吃 ~65 万 token
        # (因为 reasoning 模型每轮都展开 thinking chain). 把 quick 任务降到
        # base 版后, 大头 analyst/risk/scoring 不再走 reasoning, token 估计
        # 砍 50-70%, 时间也跟着下来 (DeepSeek 这套同理: reasoner+chat 分档).
        "llm_provider": "xiaomi",
        "backend_url": "https://token-plan-cn.xiaomimimo.com/v1",
        "deep_think_llm": "mimo-v2.5-pro",
        "quick_think_llm": "mimo-v2.5",
    },
}


def apply_preset(config: dict[str, Any], preset_name: str) -> dict[str, Any]:
    """把 preset 的 4 个字段一并写到 config (in-place + return).

    Raises:
        ValueError: preset_name 不在 PRESETS 里
    """
    if preset_name not in PRESETS:
        available = ", ".join(sorted(PRESETS.keys()))
        raise ValueError(
            f"Unknown LLM preset: '{preset_name}'. Available: {available}"
        )
    config.update(PRESETS[preset_name])
    return config


def list_presets() -> list[str]:
    """所有可用 preset 名 (按字典序). 给 CLI / UI 列出选项用."""
    return sorted(PRESETS.keys())
