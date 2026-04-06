"""13 live/UI strategy descriptions used by the dashboard and system_state.json."""

from __future__ import annotations

STRATEGY_ZH: dict[str, dict[str, str]] = {
    "P0-2": {
        "name": "资金费率套利",
        "direction": "both",
        "entry_zh": "高费率偏空，低费率偏多。吃的是费率回归。",
        "exit_zh": "费率回归或止损触发离场。",
    },
    "P1-2": {
        "name": "VWAP/TWAP 拆单算法",
        "direction": "long",
        "entry_zh": "节奏连续、放量整齐时做多，识别大单拆分买盘。",
        "exit_zh": "节奏消失或止损触发离场。",
    },
    "P1-6": {
        "name": "底部量能枯竭",
        "direction": "long",
        "entry_zh": "低位缩量，卖压衰竭后等反弹。",
        "exit_zh": "放量反抽完成或止损离场。",
    },
    "P1-8": {
        "name": "VWAP 偏离 + 量能枯竭",
        "direction": "both",
        "entry_zh": "价格偏离 VWAP 且跟随量不足，等回归。",
        "exit_zh": "偏离修复或止损离场。",
    },
    "P1-9": {
        "name": "持仓压缩释放",
        "direction": "long",
        "entry_zh": "持仓量回落、价格压缩，等向上释放。",
        "exit_zh": "结构破坏或止损离场。",
    },
    "P1-10": {
        "name": "卖方耗尽触底",
        "direction": "long",
        "entry_zh": "主动卖单衰竭、价格贴底时做多。",
        "exit_zh": "卖压恢复或止损离场。",
    },
    "P1-11": {
        "name": "高位负资金费率做空",
        "direction": "short",
        "entry_zh": "高位仍是负费率，说明高位承接偏弱。",
        "exit_zh": "离开高位或费率修复离场。",
    },
    "C1": {
        "name": "资金窗口超卖反弹",
        "direction": "long",
        "entry_zh": "低位负费率，等空头回补反弹。",
        "exit_zh": "超卖修复完成或止损离场。",
    },
    "A2-26": {
        "name": "高位贴近 + OI 降温做空",
        "direction": "short",
        "entry_zh": "高位但 OI 降温，说明追价杠杆退潮。",
        "exit_zh": "按卡片出场组合和机制衰竭离场。",
    },
    "A2-29": {
        "name": "高位贴近 + 点差扩张做空",
        "direction": "short",
        "entry_zh": "高位点差变宽，说明上方承接变薄。",
        "exit_zh": "按卡片出场组合和机制衰竭离场。",
    },
    "A3-OI": {
        "name": "OI 背离做空",
        "direction": "short",
        "entry_zh": "价格在高位，OI 下行，说明顶部在分发。",
        "exit_zh": "背离修复或机制衰竭离场。",
    },
    "A4-PIR": {
        "name": "高位仓位停滞做空",
        "direction": "short",
        "entry_zh": "价格在高位，OI 增长停滞，说明多头续航变弱。",
        "exit_zh": "高位结构走完或机制衰竭离场。",
    },
    "RT-1": {
        "name": "市场状态转换做多",
        "direction": "long",
        "entry_zh": "震荡切趋势且量价确认，顺着新趋势入场。",
        "exit_zh": "趋势确认消失或止损离场。",
    },
}


def get_strategy_info(family: str) -> dict[str, str]:
    """Return UI strategy metadata with a safe fallback."""
    return STRATEGY_ZH.get(
        family,
        {
            "name": family,
            "direction": "unknown",
            "entry_zh": "该策略的入场说明还没有配置。",
            "exit_zh": "该策略的出场说明还没有配置。",
        },
    )
