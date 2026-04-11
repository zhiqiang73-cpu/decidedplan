from __future__ import annotations

import itertools
import logging
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from alpha.combo_scanner import _context_mask, _crossing_mask

logger = logging.getLogger(__name__)

# ============================================================================
# 确认因子池（按物理机制分类）
# ============================================================================

_SELLER_IMPULSE_CONFIRM_FEATURES = [
    "taker_buy_sell_ratio",
    "volume_vs_ma20",
    "volume_acceleration",
    "spread_vs_ma20",
    "large_trade_buy_ratio",
    "direction_net_1m",
    "sell_notional_share_1m",
    "trade_burst_index",
    "direction_autocorr",
]

_BUYER_IMPULSE_CONFIRM_FEATURES = [
    "taker_buy_sell_ratio",
    "volume_vs_ma20",
    "volume_acceleration",
    "spread_vs_ma20",
    "large_trade_buy_ratio",
    "direction_net_1m",
    "sell_notional_share_1m",
    "trade_burst_index",
    "direction_autocorr",
]

_MM_REBALANCE_CONFIRM_FEATURES = [
    "spread_vs_ma20",
    "kyle_lambda",
    "quote_imbalance",
    "bid_depth_ratio",
    "spread_anomaly",
    "direction_autocorr",
]

_LIQUIDATION_CONFIRM_FEATURES = [
    "btc_liq_net_pressure",
    "total_liq_usd_5m",
    "liq_size_p90_5m",
    "taker_buy_sell_ratio",
    "direction_net_1m",
    "direction_autocorr",
]

_FUNDING_DIVERGENCE_CONFIRM_FEATURES = [
    "oi_change_rate_5m",
    "oi_change_rate_1h",
    "mark_basis",
    "mark_basis_ma10",
    "rt_funding_rate",
    "ls_ratio_change_5m",
]

# -- 新增: block-state 持续性种子的确认因子池 --

# 量能枯竭做多: 卖方持续消失, 需要买方重新出现的证据
_BLOCK_DROUGHT_CONFIRM_LONG = [
    "taker_buy_sell_ratio",      # 买方开始主导
    "direction_net_1m",          # 净流转正
    "bid_depth_ratio",           # 买盘深度增强
    "quote_imbalance",           # 盘口偏买
    "large_trade_buy_ratio",     # 大单买入
    "oi_change_rate_5m",         # 新仓开入 = 有信心
    "volume_acceleration",       # 量能开始回升
    "spread_vs_ma20",            # 价差正常化
]

# 价格压缩做多/做空: 蓄积释放, 需要方向性确认
_BLOCK_COMPRESSION_CONFIRM = [
    "volume_acceleration",       # 量能启动 = 突破信号
    "trade_burst_index",         # 交易突发 = 突破触发
    "direction_autocorr",        # 方向持续性 = 趋势开始
    "oi_change_rate_5m",         # OI 扩张 = 有信心
    "taker_buy_sell_ratio",      # 方向偏见
    "spread_vs_ma20",            # 价差变化
    "quote_imbalance",           # 盘口压力方向
    "direction_net_1m",          # 净流方向
]

# OI 背离: 价格与持仓背离
_OI_DIVERGENCE_CONFIRM = [
    "oi_change_rate_5m",
    "oi_change_rate_1h",
    "taker_buy_sell_ratio",
    "volume_vs_ma20",
    "large_trade_buy_ratio",
    "direction_net_1m",
    "spread_vs_ma20",
]


# ============================================================================
# 种子定义
# ============================================================================

@dataclass(frozen=True)
class SeedSpec:
    feature: str
    operator: str
    direction: str
    quantiles: tuple[int, ...]
    mechanism_type: str = "seller_impulse"
    confirm_features: tuple[str, ...] = ()
    group: str = ""
    cooldown: int = 60
    min_is_n: int = 15
    min_oos_n: int = 20


@dataclass(frozen=True)
class MultiConditionSeedSpec:
    """Multi-condition seed spec (max 3 conditions).

    For signals that need multiple features + context simultaneously.
    Bypasses single-feature atom -> combo_scanner pipeline.
    """
    conditions: tuple[tuple[str, str, tuple[int, ...]], ...]  # (feature, op, quantiles)
    direction: str
    mechanism_type: str
    name: str = ""
    context: str = ""  # "TREND_UP" / "TREND_DOWN" / ""
    confirm_features: tuple[str, ...] = ()
    group: str = ""
    cooldown: int = 60
    min_is_n: int = 15
    min_oos_n: int = 10


# ============================================================================
# 单条件种子规格表
# ============================================================================

_REALTIME_SEED_SPECS: tuple[SeedSpec, ...] = (
    # ── seller_impulse: SHORT 主动卖压爆发 ───────────────────────────────
    SeedSpec(
        "direction_net_1m", "<", "short", (3, 5, 7, 10, 15, 20),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_SELLER_IMPULSE_CONFIRM_FEATURES),
        group="seller_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "sell_notional_share_1m", ">", "short", (80, 85, 90, 93, 95),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_SELLER_IMPULSE_CONFIRM_FEATURES),
        group="seller_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "large_trade_buy_ratio", "<", "short", (5, 10, 15, 20, 25),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_SELLER_IMPULSE_CONFIRM_FEATURES),
        group="seller_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "taker_buy_sell_ratio", "<", "short", (5, 10, 15, 20, 25),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_SELLER_IMPULSE_CONFIRM_FEATURES),
        group="seller_impulse_flow",
        cooldown=45,
    ),
    SeedSpec(
        "trade_burst_index", ">", "short", (75, 80, 85, 90, 95),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_SELLER_IMPULSE_CONFIRM_FEATURES),
        group="seller_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "volume_vs_ma20", ">", "short", (75, 80, 85, 90, 95),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_SELLER_IMPULSE_CONFIRM_FEATURES),
        group="seller_impulse_flow",
        cooldown=45,
    ),

    # ── buyer_impulse: LONG 主动买压爆发 ─────────────────────────────────
    SeedSpec(
        "direction_net_1m", ">", "long", (80, 85, 90, 93, 95, 97),
        mechanism_type="buyer_impulse",
        confirm_features=tuple(_BUYER_IMPULSE_CONFIRM_FEATURES),
        group="buyer_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "sell_notional_share_1m", "<", "long", (3, 5, 7, 10, 15, 20),
        mechanism_type="buyer_impulse",
        confirm_features=tuple(_BUYER_IMPULSE_CONFIRM_FEATURES),
        group="buyer_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "large_trade_buy_ratio", ">", "long", (75, 80, 85, 90, 95),
        mechanism_type="buyer_impulse",
        confirm_features=tuple(_BUYER_IMPULSE_CONFIRM_FEATURES),
        group="buyer_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "taker_buy_sell_ratio", ">", "long", (75, 80, 85, 90, 95),
        mechanism_type="buyer_impulse",
        confirm_features=tuple(_BUYER_IMPULSE_CONFIRM_FEATURES),
        group="buyer_impulse_flow",
        cooldown=45,
    ),
    SeedSpec(
        "trade_burst_index", ">", "long", (75, 80, 85, 90, 95),
        mechanism_type="buyer_impulse",
        confirm_features=tuple(_BUYER_IMPULSE_CONFIRM_FEATURES),
        group="buyer_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "volume_vs_ma20", ">", "long", (75, 80, 85, 90, 95),
        mechanism_type="buyer_impulse",
        confirm_features=tuple(_BUYER_IMPULSE_CONFIRM_FEATURES),
        group="buyer_impulse_flow",
        cooldown=45,
    ),

    # ── mm_rebalance: 做市商再平衡 ──────────────────────────────────────
    SeedSpec(
        "quote_imbalance", ">", "long", (80, 85, 90, 93, 95),
        mechanism_type="mm_rebalance",
        confirm_features=tuple(_MM_REBALANCE_CONFIRM_FEATURES),
        group="mm_rebalance_book",
        cooldown=20,
    ),
    SeedSpec(
        "quote_imbalance", "<", "short", (5, 10, 15, 20),
        mechanism_type="mm_rebalance",
        confirm_features=tuple(_MM_REBALANCE_CONFIRM_FEATURES),
        group="mm_rebalance_book",
        cooldown=20,
    ),
    SeedSpec(
        "bid_depth_ratio", ">", "long", (80, 85, 90, 93, 95),
        mechanism_type="mm_rebalance",
        confirm_features=tuple(_MM_REBALANCE_CONFIRM_FEATURES),
        group="mm_rebalance_book",
        cooldown=20,
    ),
    SeedSpec(
        "bid_depth_ratio", "<", "short", (5, 10, 15, 20),
        mechanism_type="mm_rebalance",
        confirm_features=tuple(_MM_REBALANCE_CONFIRM_FEATURES),
        group="mm_rebalance_book",
        cooldown=20,
    ),

    # ── 爆仓压力 ────────────────────────────────────────────────────────
    SeedSpec(
        "btc_liq_net_pressure", ">", "short", (75, 80, 85, 90, 95),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_LIQUIDATION_CONFIRM_FEATURES),
        group="liq_pressure",
        cooldown=25,
    ),
    SeedSpec(
        "btc_liq_net_pressure", "<", "long", (5, 10, 15, 20, 25),
        mechanism_type="volume_climax_reversal",
        confirm_features=tuple(_LIQUIDATION_CONFIRM_FEATURES),
        group="liq_pressure",
        cooldown=25,
    ),

    # ── 基差/资金费率 ───────────────────────────────────────────────────
    SeedSpec(
        "mark_basis_ma10", ">", "short", (75, 80, 85, 90, 95),
        mechanism_type="funding_divergence",
        confirm_features=tuple(_FUNDING_DIVERGENCE_CONFIRM_FEATURES),
        group="basis_divergence",
        cooldown=40,
    ),
    SeedSpec(
        "mark_basis_ma10", "<", "long", (5, 10, 15, 20, 25),
        mechanism_type="funding_divergence",
        confirm_features=tuple(_FUNDING_DIVERGENCE_CONFIRM_FEATURES),
        group="basis_divergence",
        cooldown=40,
    ),

    # ====================================================================
    # [NEW] block-state 持续性种子
    # P 系列核心能力移植: 检测"力持续存在了 N 个 block"
    # ====================================================================

    # -- 量能枯竭持续 → 卖方耗尽做多 --
    # 物理: 成交量连续多个 5 分钟 block 低于 MA 的 50%,
    # 说明卖方已经彻底找不到对手方了. 反弹只是时间问题.
    SeedSpec(
        "vol_drought_blocks_5m", ">", "long", (60, 70, 80, 85, 90, 95),
        mechanism_type="seller_drought",
        confirm_features=tuple(_BLOCK_DROUGHT_CONFIRM_LONG),
        group="block_state_drought",
        cooldown=30,
    ),
    SeedSpec(
        "vol_drought_blocks_10m", ">", "long", (60, 70, 80, 85, 90, 95),
        mechanism_type="seller_drought",
        confirm_features=tuple(_BLOCK_DROUGHT_CONFIRM_LONG),
        group="block_state_drought",
        cooldown=45,
    ),

    # -- 价格压缩持续 → 蓄积释放（方向由确认因子决定）--
    # 物理: 价格振幅连续多个 block 低于中位数, 能量在蓄积.
    # 做多: 上升趋势中的横盘压缩 → 继续向上突破
    # 做空: 下降趋势中的横盘压缩 → 继续向下突破
    SeedSpec(
        "price_compression_blocks_5m", ">", "long", (60, 70, 80, 85, 90, 95),
        mechanism_type="compression_release",
        confirm_features=tuple(_BLOCK_COMPRESSION_CONFIRM),
        group="block_state_compression",
        cooldown=30,
    ),
    SeedSpec(
        "price_compression_blocks_5m", ">", "short", (60, 70, 80, 85, 90, 95),
        mechanism_type="compression_release",
        confirm_features=tuple(_BLOCK_COMPRESSION_CONFIRM),
        group="block_state_compression",
        cooldown=30,
    ),
    SeedSpec(
        "price_compression_blocks_10m", ">", "long", (65, 75, 85, 90, 95),
        mechanism_type="compression_release",
        confirm_features=tuple(_BLOCK_COMPRESSION_CONFIRM),
        group="block_state_compression_10m",
        cooldown=45,
    ),
    SeedSpec(
        "price_compression_blocks_10m", ">", "short", (65, 75, 85, 90, 95),
        mechanism_type="compression_release",
        confirm_features=tuple(_BLOCK_COMPRESSION_CONFIRM),
        group="block_state_compression_10m",
        cooldown=45,
    ),

    # ====================================================================
    # [NEW] OI 背离种子
    # 物理: 持仓量变化与价格运动方向不一致 = 聪明钱在做反方向
    # ====================================================================
    SeedSpec(
        "oi_change_rate_1h", "<", "short", (5, 10, 15, 20, 25),
        mechanism_type="oi_divergence",
        confirm_features=tuple(_OI_DIVERGENCE_CONFIRM),
        group="oi_divergence",
        cooldown=60,
    ),
    SeedSpec(
        "oi_change_rate_1h", ">", "long", (75, 80, 85, 90, 95),
        mechanism_type="oi_divergence",
        confirm_features=tuple(_OI_DIVERGENCE_CONFIRM),
        group="oi_divergence",
        cooldown=60,
    ),

    # ====================================================================
    # [NEW] 资金费率极端种子
    # 物理: 极端正费率 = 多头杠杆过重, 反转概率大
    #        极端负费率 = 空头杠杆过重, 反转概率大
    # ====================================================================
    SeedSpec(
        "rt_funding_rate", ">", "short", (85, 90, 93, 95, 97),
        mechanism_type="funding_divergence",
        confirm_features=tuple(_FUNDING_DIVERGENCE_CONFIRM_FEATURES),
        group="funding_extreme",
        cooldown=90,
    ),
    SeedSpec(
        "rt_funding_rate", "<", "long", (3, 5, 7, 10, 15),
        mechanism_type="funding_divergence",
        confirm_features=tuple(_FUNDING_DIVERGENCE_CONFIRM_FEATURES),
        group="funding_extreme",
        cooldown=90,
    ),
)


# ============================================================================
# 多条件联合种子规格表
# ============================================================================

_MULTI_CONDITION_SPECS: tuple[MultiConditionSeedSpec, ...] = (
    # ── 原有: 趋势中 OI 积累 + 买方主导 → 做多 ──────────────────────────
    MultiConditionSeedSpec(
        name="oi_accumulation_long",
        conditions=(
            ("oi_change_rate_5m", ">", (60, 70, 80, 85, 90)),
            ("taker_buy_sell_ratio", ">", (50, 55, 60, 65, 70)),
            ("volume_vs_ma20", ">", (55, 60, 65, 70, 75)),
        ),
        direction="long",
        mechanism_type="oi_accumulation_long",
        context="TREND_UP",
        group="oi_accumulation",
        cooldown=60,
    ),

    # ── 原有: TREND_UP VWAP 回踩吸收做多（3 变种）────────────────────────
    MultiConditionSeedSpec(
        name="trend_pullback_vwap_absorption_long",
        conditions=(
            ("vwap_deviation", "<", (20, 25, 30, 35, 40)),
            ("bid_depth_ratio", ">", (55, 60, 65, 70, 75)),
            ("direction_net_1m", ">", (55, 60, 65, 70, 75)),
        ),
        direction="long",
        mechanism_type="vwap_reversion",
        context="TREND_UP",
        confirm_features=("large_trade_buy_ratio", "trade_burst_index", "quote_imbalance"),
        group="trend_pullback_vwap_absorption",
        cooldown=30,
    ),
    MultiConditionSeedSpec(
        name="trend_pullback_vwap_orderflow_long",
        conditions=(
            ("vwap_deviation", "<", (20, 25, 30, 35, 40)),
            ("sell_notional_share_1m", "<", (25, 30, 35, 40, 45)),
            ("large_trade_buy_ratio", ">", (60, 70, 80, 85, 90)),
        ),
        direction="long",
        mechanism_type="vwap_reversion",
        context="TREND_UP",
        confirm_features=("direction_net_1m", "quote_imbalance", "bid_depth_ratio"),
        group="trend_pullback_vwap_orderflow",
        cooldown=30,
    ),
    MultiConditionSeedSpec(
        name="trend_pullback_vwap_reclaim_long",
        conditions=(
            ("vwap_deviation", "<", (20, 25, 30, 35, 40)),
            ("taker_buy_sell_ratio", ">", (50, 55, 60, 65, 70)),
            ("volume_vs_ma20", ">", (50, 55, 60, 65, 70)),
        ),
        direction="long",
        mechanism_type="vwap_reversion",
        context="TREND_UP",
        confirm_features=("direction_net_1m", "large_trade_buy_ratio", "trade_burst_index"),
        group="trend_pullback_vwap_reclaim",
        cooldown=30,
    ),

    # ── 原有: TREND_DOWN 反弹做空（3 变种）──────────────────────────────
    MultiConditionSeedSpec(
        name="oi_distribution_short",
        conditions=(
            ("oi_change_rate_5m", ">", (60, 70, 80, 85, 90)),
            ("taker_buy_sell_ratio", "<", (30, 35, 40, 45, 50)),
            ("volume_vs_ma20", ">", (55, 60, 65, 70, 75)),
        ),
        direction="short",
        mechanism_type="oi_distribution_short",
        context="TREND_DOWN",
        group="oi_distribution",
        cooldown=60,
    ),
    MultiConditionSeedSpec(
        name="trend_pullback_vwap_absorption_short",
        conditions=(
            ("vwap_deviation", ">", (60, 65, 70, 75, 80)),
            ("bid_depth_ratio", "<", (25, 30, 35, 40, 45)),
            ("direction_net_1m", "<", (25, 30, 35, 40, 45)),
        ),
        direction="short",
        mechanism_type="vwap_reversion",
        context="TREND_DOWN",
        confirm_features=("sell_notional_share_1m", "trade_burst_index", "quote_imbalance"),
        group="trend_pullback_vwap_absorption_short",
        cooldown=30,
    ),
    MultiConditionSeedSpec(
        name="trend_pullback_vwap_orderflow_short",
        conditions=(
            ("vwap_deviation", ">", (60, 65, 70, 75, 80)),
            ("sell_notional_share_1m", ">", (55, 60, 65, 70, 75)),
            ("large_trade_buy_ratio", "<", (10, 15, 20, 30, 40)),
        ),
        direction="short",
        mechanism_type="vwap_reversion",
        context="TREND_DOWN",
        confirm_features=("direction_net_1m", "quote_imbalance", "bid_depth_ratio"),
        group="trend_pullback_vwap_orderflow_short",
        cooldown=30,
    ),
    MultiConditionSeedSpec(
        name="trend_pullback_vwap_reclaim_short",
        conditions=(
            ("vwap_deviation", ">", (60, 65, 70, 75, 80)),
            ("taker_buy_sell_ratio", "<", (30, 35, 40, 45, 50)),
            ("volume_vs_ma20", ">", (50, 55, 60, 65, 70)),
        ),
        direction="short",
        mechanism_type="vwap_reversion",
        context="TREND_DOWN",
        confirm_features=("direction_net_1m", "sell_notional_share_1m", "trade_burst_index"),
        group="trend_pullback_vwap_reclaim_short",
        cooldown=30,
    ),

    # ====================================================================
    # [NEW] OI 背离分发做空: 价格在高位 + OI 下降 + 买方不足
    # 物理: 聪明钱在高位平多仓/开空仓, 散户还在追涨
    # ====================================================================
    MultiConditionSeedSpec(
        name="oi_divergence_distribution_short",
        conditions=(
            ("position_in_range_24h", ">", (65, 70, 75, 80, 85)),
            ("oi_change_rate_1h", "<", (20, 25, 30, 35, 40)),
            ("taker_buy_sell_ratio", "<", (35, 40, 45, 50)),
        ),
        direction="short",
        mechanism_type="oi_divergence",
        group="oi_divergence_distribution",
        cooldown=60,
    ),

    # ====================================================================
    # [NEW] OI 背离吸收做多: 价格在低位 + OI 上升 + 买方确认
    # 物理: 聪明钱在低位开多仓, 吸收恐慌抛售
    # ====================================================================
    MultiConditionSeedSpec(
        name="oi_divergence_accumulation_long",
        conditions=(
            ("position_in_range_24h", "<", (15, 20, 25, 30, 35)),
            ("oi_change_rate_1h", ">", (60, 65, 70, 75, 80)),
            ("taker_buy_sell_ratio", ">", (50, 55, 60, 65)),
        ),
        direction="long",
        mechanism_type="oi_divergence",
        group="oi_divergence_accumulation",
        cooldown=60,
    ),

    # ====================================================================
    # [NEW] 量能枯竭 + 底部位置 + 买盘支撑 → 做多
    # 物理: 卖方在底部完全消失, 且有新买盘介入
    # 这是 P1-6 底部量能枯竭的 Alpha 管道版本
    # ====================================================================
    MultiConditionSeedSpec(
        name="drought_bottom_accumulation_long",
        conditions=(
            ("vol_drought_blocks_5m", ">", (60, 70, 80, 85, 90)),
            ("position_in_range_24h", "<", (15, 20, 25, 30, 35)),
            ("bid_depth_ratio", ">", (55, 60, 65, 70, 75)),
        ),
        direction="long",
        mechanism_type="seller_drought",
        confirm_features=("taker_buy_sell_ratio", "direction_net_1m", "large_trade_buy_ratio"),
        group="drought_bottom",
        cooldown=30,
    ),

    # ====================================================================
    # [NEW] 资金费率挤压做空: 极端正费率 + OI 还在涨 + 价格高位
    # 物理: 多头杠杆过重, 每 8 小时支付高额费率, 难以持续
    # ====================================================================
    MultiConditionSeedSpec(
        name="funding_squeeze_short",
        conditions=(
            ("rt_funding_rate", ">", (80, 85, 90, 93, 95)),
            ("oi_change_rate_5m", ">", (55, 60, 65, 70, 75)),
            ("position_in_range_24h", ">", (55, 60, 65, 70, 75)),
        ),
        direction="short",
        mechanism_type="funding_divergence",
        group="funding_squeeze",
        cooldown=90,
    ),

    # ====================================================================
    # [NEW] 资金费率挤压做多: 极端负费率 + OI 还在涨 + 价格低位
    # 物理: 空头杠杆过重, 每 8 小时支付高额费率, 难以持续
    # ====================================================================
    MultiConditionSeedSpec(
        name="funding_squeeze_long",
        conditions=(
            ("rt_funding_rate", "<", (5, 7, 10, 15, 20)),
            ("oi_change_rate_5m", ">", (55, 60, 65, 70, 75)),
            ("position_in_range_24h", "<", (25, 30, 35, 40, 45)),
        ),
        direction="long",
        mechanism_type="funding_divergence",
        group="funding_squeeze",
        cooldown=90,
    ),

    # ====================================================================
    # [NEW] 压缩突破做多: 持续压缩 + 价差收紧 + 量能启动
    # 物理: 价格压缩 = 多空力量胶着, 一旦量能突破 → 方向性释放
    # ====================================================================
    MultiConditionSeedSpec(
        name="compression_breakout_long",
        conditions=(
            ("price_compression_blocks_5m", ">", (60, 70, 80, 85, 90)),
            ("spread_vs_ma20", "<", (25, 30, 35, 40, 45)),
            ("volume_acceleration", ">", (50, 55, 60, 65, 70)),
        ),
        direction="long",
        mechanism_type="compression_release",
        context="",  # 无需趋势上下文: 压缩突破可以从横盘/底部启动新趋势
        confirm_features=("direction_autocorr", "taker_buy_sell_ratio", "oi_change_rate_5m"),
        group="compression_breakout",
        cooldown=30,
    ),

    # ====================================================================
    # [NEW] 压缩突破做空: 镜像
    # ====================================================================
    MultiConditionSeedSpec(
        name="compression_breakout_short",
        conditions=(
            ("price_compression_blocks_5m", ">", (60, 70, 80, 85, 90)),
            ("spread_vs_ma20", "<", (25, 30, 35, 40, 45)),
            ("volume_acceleration", ">", (50, 55, 60, 65, 70)),
        ),
        direction="short",
        mechanism_type="compression_release",
        context="TREND_DOWN",
        confirm_features=("direction_autocorr", "sell_notional_share_1m", "oi_change_rate_5m"),
        group="compression_breakout",
        cooldown=30,
    ),

    # ====================================================================
    # [NEW] 爆仓级联延续做空: 正在爆仓 + 放量 + 净卖
    # 物理: 被动清算引发主动卖出, 形成下跌正反馈
    # ====================================================================
    MultiConditionSeedSpec(
        name="liquidation_cascade_short",
        conditions=(
            ("btc_liq_net_pressure", ">", (75, 80, 85, 90, 95)),
            ("volume_vs_ma20", ">", (65, 70, 75, 80, 85)),
            ("direction_net_1m", "<", (20, 25, 30, 35, 40)),
        ),
        direction="short",
        mechanism_type="seller_impulse",
        group="liquidation_cascade",
        cooldown=30,
    ),

    # ====================================================================
    # [NEW] 量能枯竭 + VWAP 偏离 + 压缩 → 做多
    # 物理: P1-8 (VWAP偏离+枯竭) 的 Alpha 管道版本
    # 价格跌到 VWAP 下方 + 卖方消失 + 价格压缩 = 三重确认
    # ====================================================================
    MultiConditionSeedSpec(
        name="vwap_drought_compression_long",
        conditions=(
            ("vwap_deviation", "<", (10, 15, 20, 25, 30)),
            ("vol_drought_blocks_5m", ">", (55, 65, 75, 80, 85)),
            ("price_compression_blocks_5m", ">", (50, 60, 70, 75, 80)),
        ),
        direction="long",
        mechanism_type="vwap_reversion",
        confirm_features=("taker_buy_sell_ratio", "direction_net_1m", "bid_depth_ratio"),
        group="vwap_drought_compression",
        cooldown=30,
    ),
)


# ============================================================================
# MFE 覆盖率计算常量
# ============================================================================

# 方向正确门槛: MFE > 费用 (0.04% = 0.0004)
_MFE_FEE_THRESHOLD = 0.0004

# 种子层 MFE 覆盖率最低要求 (低于此直接淘汰)
# CLAUDE.md 要求最终策略 >= 75%, 种子层宽松到 55%
_MIN_MFE_COVERAGE = 55.0


# ============================================================================
# 种子挖掘器
# ============================================================================

class RealtimeSeedMiner:
    """从实时成交痕迹里挖种子: 单条件 + 多条件联合 + 持续性种子。"""

    def __init__(
        self,
        train_frac: float = 0.67,
        min_oos_wr: float = 58.0,
        min_oos_pf: float = 1.10,
        min_oos_edge_pct: float = 0.02,
        max_wr_drop: float = 12.0,
        top_k: int = 20,
    ) -> None:
        self.train_frac = train_frac
        self.min_oos_wr = min_oos_wr
        self.min_oos_pf = min_oos_pf
        self.min_oos_edge_pct = min_oos_edge_pct
        self.max_wr_drop = max_wr_drop
        self.top_k = top_k

    def mine(self, df: pd.DataFrame, horizons: Iterable[int]) -> list[dict]:
        if df.empty:
            return []

        horizons_list = list(horizons)
        candidates: list[dict] = []
        for spec in _REALTIME_SEED_SPECS:
            if spec.feature not in df.columns:
                continue

            for horizon in horizons_list:
                fwd_col = f"fwd_ret_{int(horizon)}"
                if fwd_col not in df.columns:
                    continue
                best_seed = self._best_seed_for_spec(
                    df=df,
                    fwd_col=fwd_col,
                    horizon=int(horizon),
                    spec=spec,
                )
                if best_seed is not None:
                    candidates.append(best_seed)

        # 多条件联合种子
        mc_candidates = self._mine_multi_condition(df, horizons_list)
        candidates.extend(mc_candidates)
        if mc_candidates:
            logger.info(
                "[RT-SEED] Multi-condition seeds contributed %d candidates",
                len(mc_candidates),
            )

        if not candidates:
            logger.info("[RT-SEED] No seeds found matching thresholds")
            return []

        candidates.sort(
            key=lambda item: (
                float(item.get("_score", 0.0)),
                float(item.get("_oos_avg_ret", 0.0)),
                float(item.get("_oos_pf", 0.0)),
                int(item.get("_oos_n", 0)),
            ),
            reverse=True,
        )

        # ── 去重 ──
        deduped_long: list[dict] = []
        deduped_short: list[dict] = []
        seen_keys: set[tuple] = set()
        for item in candidates:
            if item.get("conditions"):
                cond_key = tuple(
                    (
                        str(cond.get("feature", "")),
                        str(cond.get("op", cond.get("operator", ""))),
                        round(float(cond.get("threshold", 0.0)), 8),
                    )
                    for cond in item.get("conditions", [])
                )
                key = (
                    "multi",
                    str(item.get("name", "")),
                    int(item["horizon"]),
                    str(item.get("direction", "")),
                    str(item.get("context", "")),
                    cond_key,
                )
            else:
                key = (
                    "single",
                    str(item["feature"]),
                    int(item["horizon"]),
                    str(item.get("direction", "")),
                )
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if item.get("direction") == "long":
                deduped_long.append(item)
            else:
                deduped_short.append(item)

        # ── 方向配额选择: 保证少数方向至少占 1/3 ──
        min_quota = max(self.top_k // 3, 4)
        long_take = deduped_long[:min_quota]
        short_take = deduped_short[:min_quota]

        remaining_slots = self.top_k - len(long_take) - len(short_take)
        remaining_pool = deduped_long[min_quota:] + deduped_short[min_quota:]
        remaining_pool.sort(
            key=lambda x: float(x.get("_score", 0.0)), reverse=True,
        )
        fill = remaining_pool[:max(remaining_slots, 0)]

        deduped = long_take + short_take + fill
        for item in deduped:
            item.pop("_score", None)
            item.pop("_oos_avg_ret", None)
            item.pop("_oos_pf", None)
            item.pop("_oos_n", None)

        n_long = sum(1 for s in deduped if s.get("direction") == "long")
        n_short = len(deduped) - n_long
        logger.info(
            "[RT-SEED] Retained %d realtime seeds (long=%d, short=%d)",
            len(deduped), n_long, n_short,
        )
        for seed in deduped:
            stats = seed.get("seed_stats", {})
            logger.info(
                "[RT-SEED] %s | OOS WR=%.1f%% n=%d PF=%.2f avg=%.4f%% MFE_cov=%.0f%%",
                seed["name"],
                float(stats.get("oos_wr", 0.0)),
                int(stats.get("oos_n", 0)),
                float(stats.get("oos_pf", 0.0)),
                float(stats.get("oos_avg_ret", 0.0)),
                float(stats.get("oos_mfe_coverage", 0.0)),
            )
        return deduped

    def _best_seed_for_spec(
        self,
        *,
        df: pd.DataFrame,
        fwd_col: str,
        horizon: int,
        spec: SeedSpec,
    ) -> dict | None:
        valid_df = df[df[spec.feature].notna() & df[fwd_col].notna()].copy()
        if len(valid_df) < (spec.min_is_n + spec.min_oos_n):
            return None

        split_idx = int(len(valid_df) * self.train_frac)
        train_df = valid_df.iloc[:split_idx].copy()
        test_df = valid_df.iloc[split_idx:].copy()
        if len(train_df) < spec.min_is_n or len(test_df) < spec.min_oos_n:
            return None

        train_col = train_df[spec.feature]
        if train_col.notna().sum() < spec.min_is_n * 2:
            return None

        best: dict | None = None
        best_score = float("-inf")

        for quantile in spec.quantiles:
            threshold = float(train_col.quantile(quantile / 100))
            seed_stats_is = self._eval_seed(
                df=train_df,
                fwd_col=fwd_col,
                feature=spec.feature,
                operator=spec.operator,
                threshold=threshold,
                direction=spec.direction,
                cooldown=spec.cooldown,
            )
            if seed_stats_is["n"] < spec.min_is_n:
                continue

            seed_stats_oos = self._eval_seed(
                df=test_df,
                fwd_col=fwd_col,
                feature=spec.feature,
                operator=spec.operator,
                threshold=threshold,
                direction=spec.direction,
                cooldown=spec.cooldown,
            )
            if seed_stats_oos["n"] < spec.min_oos_n:
                continue
            if seed_stats_oos["wr"] < self.min_oos_wr:
                continue
            if seed_stats_oos["pf"] < self.min_oos_pf:
                continue

            oos_avg_ret_pct = float(seed_stats_oos["avg_ret"] * 100.0)
            if oos_avg_ret_pct < self.min_oos_edge_pct:
                continue
            if seed_stats_is["wr"] - seed_stats_oos["wr"] > self.max_wr_drop:
                continue

            # MFE 覆盖率门控: 方向都不对的种子直接淘汰
            oos_mfe_cov = float(seed_stats_oos.get("mfe_coverage", 100.0))
            if oos_mfe_cov < _MIN_MFE_COVERAGE:
                continue

            score = (
                oos_avg_ret_pct * 25.0
                + float(seed_stats_oos["pf"]) * 6.0
                + float(seed_stats_oos["wr"]) * 0.35
                + min(float(seed_stats_oos["n"]), 120.0) * 0.12
                + max(oos_mfe_cov - 60.0, 0.0) * 0.25  # MFE 覆盖率加分
            )
            if score <= best_score:
                continue

            best_score = score
            best = {
                "name": f"rt_{spec.feature}_{quantile}",
                "feature": spec.feature,
                "op": spec.operator,
                "threshold": threshold,
                "horizon": horizon,
                "direction": spec.direction,
                "mechanism_type": spec.mechanism_type,
                "confirm_features": list(spec.confirm_features or _SELLER_IMPULSE_CONFIRM_FEATURES),
                "group": spec.group or f"{spec.mechanism_type}_{spec.feature}",
                "cooldown": spec.cooldown,
                "origin": "realtime_seed_miner",
                "seed_stats": {
                    "is_n": int(seed_stats_is["n"]),
                    "is_wr": round(float(seed_stats_is["wr"]), 2),
                    "is_avg_ret": round(float(seed_stats_is["avg_ret"] * 100.0), 4),
                    "is_pf": round(float(seed_stats_is["pf"]), 3),
                    "is_mfe_coverage": round(float(seed_stats_is.get("mfe_coverage", 0.0)), 1),
                    "oos_n": int(seed_stats_oos["n"]),
                    "oos_wr": round(float(seed_stats_oos["wr"]), 2),
                    "oos_avg_ret": round(oos_avg_ret_pct, 4),
                    "oos_pf": round(float(seed_stats_oos["pf"]), 3),
                    "oos_mfe_coverage": round(oos_mfe_cov, 1),
                },
                "_score": score,
                "_oos_avg_ret": oos_avg_ret_pct,
                "_oos_pf": float(seed_stats_oos["pf"]),
                "_oos_n": int(seed_stats_oos["n"]),
            }

        return best

    @staticmethod
    def _eval_seed(
        *,
        df: pd.DataFrame,
        fwd_col: str,
        feature: str,
        operator: str,
        threshold: float,
        direction: str,
        cooldown: int,
    ) -> dict[str, float]:
        if feature not in df.columns or fwd_col not in df.columns:
            return {"n": 0, "wr": 0.0, "avg_ret": 0.0, "pf": 0.0, "mfe_coverage": 0.0}

        mask = pd.Series(
            _crossing_mask(df[feature].values, operator, threshold, cooldown=cooldown),
            index=df.index,
        )
        valid = mask & df[fwd_col].notna()
        n = int(valid.sum())
        if n == 0:
            return {"n": 0, "wr": 0.0, "avg_ret": 0.0, "pf": 0.0, "mfe_coverage": 0.0}

        fwd = df.loc[valid, fwd_col].values
        rets = -fwd if direction == "short" else fwd
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = float(abs(losses.mean())) if len(losses) > 0 else 0.0
        pf = (
            (avg_win * len(wins)) / (avg_loss * len(losses))
            if len(losses) > 0 and avg_loss > 0
            else float("inf")
        )

        # MFE 覆盖率: % of signals where price moves favorably > fee
        mfe_coverage = 0.0
        horizon_str = fwd_col.replace("fwd_ret_", "")
        if direction == "long":
            mfe_col = f"fwd_max_ret_{horizon_str}"
        else:
            mfe_col = f"fwd_min_ret_{horizon_str}"
        if mfe_col in df.columns:
            mfe_vals = df.loc[valid, mfe_col].values.astype(float)
            if direction == "short":
                mfe_vals = -mfe_vals  # fwd_min_ret is negative, negate for favorable
            mfe_ok = np.nansum(mfe_vals > _MFE_FEE_THRESHOLD)
            mfe_total = np.sum(~np.isnan(mfe_vals))
            mfe_coverage = float(mfe_ok / mfe_total * 100.0) if mfe_total > 0 else 0.0

        return {
            "n": n,
            "wr": float(len(wins) / n * 100.0),
            "avg_ret": float(rets.mean()),
            "pf": float(pf),
            "mfe_coverage": mfe_coverage,
        }

    # ── 多条件联合种子挖掘 ──────────────────────────────────────────────────

    def _mine_multi_condition(
        self, df: pd.DataFrame, horizons: list[int]
    ) -> list[dict]:
        """遍历 _MULTI_CONDITION_SPECS，找多条件 AND 联合种子。"""
        candidates: list[dict] = []
        for spec in _MULTI_CONDITION_SPECS:
            features = [c[0] for c in spec.conditions]
            missing = [f for f in features if f not in df.columns]
            if missing:
                logger.debug("[RT-MC] skip %s: missing features %s", spec.mechanism_type, missing)
                continue
            for horizon in horizons:
                fwd_col = f"fwd_ret_{int(horizon)}"
                if fwd_col not in df.columns:
                    continue
                result = self._best_multi_condition_seed(
                    df=df, fwd_col=fwd_col, horizon=int(horizon), spec=spec
                )
                if result is not None:
                    candidates.append(result)
        return candidates

    def _best_multi_condition_seed(
        self,
        *,
        df: pd.DataFrame,
        fwd_col: str,
        horizon: int,
        spec: MultiConditionSeedSpec,
    ) -> dict | None:
        """穷举多条件阈值组合，按 OOS score 返回最佳组合。"""
        valid_mask = df[fwd_col].notna()
        for feat, _, _ in spec.conditions:
            valid_mask = valid_mask & df[feat].notna()
        valid_mask = valid_mask & _context_mask(df, spec.context)
        valid_df = df[valid_mask].copy()
        if len(valid_df) < (spec.min_is_n + spec.min_oos_n):
            return None

        split_idx = int(len(valid_df) * self.train_frac)
        train_df = valid_df.iloc[:split_idx].copy()
        test_df = valid_df.iloc[split_idx:].copy()
        if len(train_df) < spec.min_is_n or len(test_df) < spec.min_oos_n:
            return None

        # 每个条件在 IS 数据上计算各分位点的阈值
        cond_options: list[list[tuple[str, str, float]]] = []
        for feat, op, quantiles in spec.conditions:
            train_col = train_df[feat]
            options = [
                (feat, op, float(train_col.quantile(q / 100))) for q in quantiles
            ]
            cond_options.append(options)

        best: dict | None = None
        best_score = float("-inf")

        for combo in itertools.product(*cond_options):
            is_stats = self._eval_multi_condition_seed(
                df=train_df, fwd_col=fwd_col,
                conditions=list(combo), direction=spec.direction, cooldown=spec.cooldown,
            )
            if is_stats["n"] < spec.min_is_n:
                continue

            oos_stats = self._eval_multi_condition_seed(
                df=test_df, fwd_col=fwd_col,
                conditions=list(combo), direction=spec.direction, cooldown=spec.cooldown,
            )
            if oos_stats["n"] < spec.min_oos_n:
                continue
            if oos_stats["wr"] < self.min_oos_wr:
                continue
            if oos_stats["pf"] < self.min_oos_pf:
                continue
            oos_avg_ret_pct = float(oos_stats["avg_ret"] * 100.0)
            if oos_avg_ret_pct < self.min_oos_edge_pct:
                continue
            if is_stats["wr"] - oos_stats["wr"] > self.max_wr_drop:
                continue

            # MFE 覆盖率门控
            oos_mfe_cov = float(oos_stats.get("mfe_coverage", 100.0))
            if oos_mfe_cov < _MIN_MFE_COVERAGE:
                continue

            score = (
                oos_avg_ret_pct * 25.0
                + float(oos_stats["pf"]) * 6.0
                + float(oos_stats["wr"]) * 0.35
                + min(float(oos_stats["n"]), 120.0) * 0.12
                + max(oos_mfe_cov - 60.0, 0.0) * 0.25
            )
            if score <= best_score:
                continue

            best_score = score
            cond_dicts = [
                {"feature": f, "op": o, "threshold": t} for f, o, t in combo
            ]
            primary_feat, primary_op, primary_thr = combo[0]
            best = {
                "name": spec.name or f"mc_{spec.mechanism_type}_{horizon}",
                "feature": primary_feat,
                "op": primary_op,
                "threshold": primary_thr,
                "horizon": horizon,
                "direction": spec.direction,
                "mechanism_type": spec.mechanism_type,
                "context": spec.context,
                "conditions": cond_dicts,
                "confirm_features": list(spec.confirm_features or _BUYER_IMPULSE_CONFIRM_FEATURES),
                "group": spec.group or spec.mechanism_type,
                "cooldown": spec.cooldown,
                "origin": "realtime_seed_miner_multi",
                "seed_stats": {
                    "is_n": int(is_stats["n"]),
                    "is_wr": round(float(is_stats["wr"]), 2),
                    "is_avg_ret": round(float(is_stats["avg_ret"] * 100.0), 4),
                    "is_pf": round(float(is_stats["pf"]), 3),
                    "is_mfe_coverage": round(float(is_stats.get("mfe_coverage", 0.0)), 1),
                    "oos_n": int(oos_stats["n"]),
                    "oos_wr": round(float(oos_stats["wr"]), 2),
                    "oos_avg_ret": round(oos_avg_ret_pct, 4),
                    "oos_pf": round(float(oos_stats["pf"]), 3),
                    "oos_mfe_coverage": round(oos_mfe_cov, 1),
                },
                "_score": score,
                "_oos_avg_ret": oos_avg_ret_pct,
                "_oos_pf": float(oos_stats["pf"]),
                "_oos_n": int(oos_stats["n"]),
            }

        return best

    @staticmethod
    def _eval_multi_condition_seed(
        *,
        df: pd.DataFrame,
        fwd_col: str,
        conditions: list[tuple[str, str, float]],
        direction: str,
        cooldown: int,
    ) -> dict[str, float]:
        """多条件 AND 评估: 第一条件 crossing, 其余静态过滤。"""
        if not conditions or fwd_col not in df.columns:
            return {"n": 0, "wr": 0.0, "avg_ret": 0.0, "pf": 0.0, "mfe_coverage": 0.0}

        primary_feat, primary_op, primary_thr = conditions[0]
        if primary_feat not in df.columns:
            return {"n": 0, "wr": 0.0, "avg_ret": 0.0, "pf": 0.0, "mfe_coverage": 0.0}

        trigger = pd.Series(
            _crossing_mask(df[primary_feat].values, primary_op, primary_thr, cooldown=cooldown),
            index=df.index,
        )
        for feat, op, thr in conditions[1:]:
            if feat not in df.columns:
                return {"n": 0, "wr": 0.0, "avg_ret": 0.0, "pf": 0.0, "mfe_coverage": 0.0}
            ops = {">": df[feat] > thr, "<": df[feat] < thr,
                   ">=": df[feat] >= thr, "<=": df[feat] <= thr}
            trigger = trigger & ops.get(op, df[feat] == thr)

        valid = trigger & df[fwd_col].notna()
        n = int(valid.sum())
        if n == 0:
            return {"n": 0, "wr": 0.0, "avg_ret": 0.0, "pf": 0.0, "mfe_coverage": 0.0}

        fwd = df.loc[valid, fwd_col].values
        rets = -fwd if direction == "short" else fwd
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = float(abs(losses.mean())) if len(losses) > 0 else 0.0
        pf = (
            (avg_win * len(wins)) / (avg_loss * len(losses))
            if len(losses) > 0 and avg_loss > 0
            else float("inf")
        )

        # MFE 覆盖率
        mfe_coverage = 0.0
        horizon_str = fwd_col.replace("fwd_ret_", "")
        if direction == "long":
            mfe_col = f"fwd_max_ret_{horizon_str}"
        else:
            mfe_col = f"fwd_min_ret_{horizon_str}"
        if mfe_col in df.columns:
            mfe_vals = df.loc[valid, mfe_col].values.astype(float)
            if direction == "short":
                mfe_vals = -mfe_vals
            mfe_ok = np.nansum(mfe_vals > _MFE_FEE_THRESHOLD)
            mfe_total = np.sum(~np.isnan(mfe_vals))
            mfe_coverage = float(mfe_ok / mfe_total * 100.0) if mfe_total > 0 else 0.0

        return {
            "n": n,
            "wr": float(len(wins) / n * 100.0),
            "avg_ret": float(rets.mean()),
            "pf": float(pf),
            "mfe_coverage": mfe_coverage,
        }
