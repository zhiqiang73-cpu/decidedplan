"""Single source of truth for live strategy names and UI descriptions."""

from __future__ import annotations

STRATEGY_ZH: dict[str, dict[str, str]] = {
    "P0-2": {
        "name": "资金费率套利",
        "direction": "both",
        "entry_zh": "资金费率超阈值且OI建仓时做空；费率为负时做多",
        "exit_zh": "持仓30分钟或反向信号触发离场",
    },
    "P1-2": {
        "name": "VWAP/TWAP 偏离回归",
        "direction": "long",
        "entry_zh": "价格低于VWAP且成交量确认买方枯竭时做多",
        "exit_zh": "价格回归VWAP或超时离场",
    },
    "P1-6": {
        "name": "底部量价枯竭",
        "direction": "long",
        "entry_zh": "底部成交量枯竭且买方主动性回升",
        "exit_zh": "量能恢复或价格突破离场",
    },
    "P1-8": {
        "name": "VWAP 干涸 + 量枯竭",
        "direction": "both",
        "entry_zh": "价格偏离VWAP且成交量双重枯竭确认",
        "exit_zh": "VWAP回归或超时离场",
    },
    "P1-9": {
        "name": "仓位压缩信号",
        "direction": "both",
        "entry_zh": "多空仓位压缩至极端后触发反转",
        "exit_zh": "仓位回归正常或超时离场",
    },
    "P1-9-LONG": {
        "name": "仓位压缩信号-做多",
        "direction": "long",
        "entry_zh": "仓位压缩至极端多头入场",
        "exit_zh": "仓位回归正常或超时离场",
    },
    "P1-9-SHORT": {
        "name": "仓位压缩信号-做空",
        "direction": "short",
        "entry_zh": "仓位压缩至极端空头入场",
        "exit_zh": "仓位回归正常或超时离场",
    },
    "P1-10": {
        "name": "主动买卖力衰竭",
        "direction": "both",
        "entry_zh": "主动成交比达极端百分位且方向加速衰减",
        "exit_zh": "主买比回归中枢或超时离场",
    },
    "P1-10-LONG": {
        "name": "主动买卖力衰竭-做多",
        "direction": "long",
        "entry_zh": "低主动卖压且买力衰竭触底做多",
        "exit_zh": "主买比回归或超时离场",
    },
    "P1-10-SHORT": {
        "name": "主动买卖力衰竭-做空",
        "direction": "short",
        "entry_zh": "高主动卖压且买力衰竭做空入场",
        "exit_zh": "主卖比回归或超时离场",
    },
    "P1-11": {
        "name": "高仓位资金费空",
        "direction": "short",
        "entry_zh": "持仓量极高且资金费率持续正值时做空",
        "exit_zh": "持仓量下降或费率转负时离场",
    },
    "C1": {
        "name": "资金费超卖做多",
        "direction": "long",
        "entry_zh": "资金费深度负值且价格超卖时做多",
        "exit_zh": "费率回正或超时离场",
    },
    "A2-26": {
        "name": "价格偏离 + OI 建仓",
        "direction": "short",
        "entry_zh": "价格偏离高点且OI同步建仓确认空头入场",
        "exit_zh": "Top-3 距离回收或超时离场",
    },
    "A2-29": {
        "name": "价格偏离 + 量能确认",
        "direction": "short",
        "entry_zh": "价格偏离高点且成交量确认卖方主导",
        "exit_zh": "Top-3 距离回收或超时离场",
    },
    "A3-OI": {
        "name": "OI 异常建仓",
        "direction": "short",
        "entry_zh": "OI异常增长且价格未同步上涨时做空",
        "exit_zh": "OI回落或价格突破离场",
    },
    "A4-PIR": {
        "name": "价格冲高反转候选",
        "direction": "short",
        "entry_zh": "价格冲高后量价背离且OI异常确认做空",
        "exit_zh": "价格回归均值或超时离场",
    },
    "A4-PIR-CANDIDATE": {
        "name": "价格冲高反转候选-待审",
        "direction": "short",
        "entry_zh": "候选规则待物理确认后升级",
        "exit_zh": "符合条件后升级为 live 信号",
    },
    "LEGACY-POSITIONING": {
        "name": "遗留仓位信号",
        "direction": "short",
        "entry_zh": "基于 position 模块的历史做空规则",
        "exit_zh": "此信号已停用不进入 live 监控",
    },
    "LEGACY-HIGH-DISTRIBUTION": {
        "name": "遗留高位分布信号",
        "direction": "short",
        "entry_zh": "基于 dist_to 指标的历史做空规则",
        "exit_zh": "此信号已停用不进入 live 监控",
    },
    "RT-1": {
        "name": "实时反转信号",
        "direction": "long",
        "entry_zh": "短期急跌后量能枯竭且买方接盘确认",
        "exit_zh": "价格恢复或超时离场",
    },
    "OA-1": {
        "name": "OI 累积做多",
        "direction": "long",
        "entry_zh": "趋势上行中OI持续增长+主动买盘主导+量能放大，新多头资金涌入",
        "exit_zh": "OI增速MA5回吐入场值50%以上，新资金涌入力消失",
    },
}


def get_strategy_info(family: str) -> dict[str, str]:
    """Return UI strategy metadata with a safe fallback."""
    return STRATEGY_ZH.get(
        family,
        {
            "name": family,
            "direction": "unknown",
            "entry_zh": "暂无入场描述",
            "exit_zh": "暂无离场描述",
        },
    )
