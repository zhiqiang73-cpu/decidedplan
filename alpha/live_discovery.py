"""
LiveDiscoveryEngine - 持续运行的 Alpha 策略自动发现引擎

流程:
  1. 加载近 N 天历史数据 (默认 30 天 ~43,200 bars)
  2. 计算 52+ 特征 (FeatureEngine)
  3. IC 扫描 (FeatureScanner.scan_all)
  4. 挖掘单条件入场原子 (AtomMiner)
  5. Walk-Forward 验证 (maker 费 0.04% 双边)
  6. 严格过滤: OOS_WR>65%, n_oos>30, degradation>0.5, oos_net>0
  7. 对合格入场条件挖掘出场条件 (ExitConditionMiner)
  8. 生成策略候选报告 + 保存至 pending_rules.json

运行:
  engine = LiveDiscoveryEngine()
  cards = engine.run_once(data_days=365)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

from core.feature_engine import FeatureEngine
from alpha.scanner import FeatureScanner
from alpha.causal_atoms import AtomMiner, CausalAtom
from alpha.walk_forward import WalkForwardValidator
from alpha.causal_validator import validate_candidate
from alpha.candidate_review import (
    merge_flagged_rules,
    merge_pending_rules,
    split_review_candidates,
)
from alpha.auto_explain import AutoExplainer
from alpha.product_policy import infer_product_family, sync_product_candidate_pool
from alpha.combo_scanner import ComboScanner, CONFIRM_FEATURES, _context_mask
from alpha.realtime_seed_miner import RealtimeSeedMiner
from utils.file_io import read_json_file, write_json_atomic
from monitor.exit_policy_config import ExitParams

logger = logging.getLogger(__name__)

# 默认输出目录 (BTCUSDT 向后兼容)
_DEFAULT_OUTPUT_DIR = Path("alpha/output")

# Maker 费率 (%)
_MAKER_FEE_TOTAL = 0.04  # 0.02% x2 双边

# 入场条件合格门槛
_MIN_OOS_WR = 65.0        # OOS 胜率 (%, 费后)
_MIN_OOS_N = 30           # OOS 触发次数
_MIN_DEGRADATION = 0.5    # IS->OOS ICIR 保留比例
_MIN_OOS_NET = 0.0        # OOS 净收益 (%, 费后)
_ALPHA_EXIT_MAX_HOLD = 120

_ALPHA_DEFAULT_EXIT_PARAMS = ExitParams(
    take_profit_pct=0.0,        # 不固定止盈，让力消失决定
    stop_pct=0.70,
    protect_start_pct=0.04,     # 阶段1: MFE > 费用(0.04%) 启动保本
    protect_gap_ratio=0.40,     # 阶段2: 锁住峰值 40% 利润
    protect_floor_pct=0.02,     # 保本底线
    min_hold_bars=3,            # 最少持 3 bar
    max_hold_factor=4,          # 安全网
    exit_confirm_bars=1,        # 不等待确认
    decay_exit_threshold=0.85,
    decay_tighten_threshold=0.50,
)
_VS_ENTRY_TAG = "_vs_entry"
_SAFETY_CAP_REASON = "safety_cap"
_FORCE_DECAY_RATIO = 0.55
_FORCE_INVALIDATION_RATIO = 0.30


def _slug_id_part(text: object, max_len: int = 24) -> str:
    raw = str(text or "card").lower()
    chars = []
    prev_sep = False
    for ch in raw:
        if ch.isalnum():
            chars.append(ch)
            prev_sep = False
        elif not prev_sep:
            chars.append("_")
            prev_sep = True
    slug = "".join(chars).strip("_")
    return (slug or "card")[:max_len]


def _stable_card_id(label: object, *parts: object) -> str:
    key = "|".join(str(part) for part in parts if part is not None)
    digest = hashlib.blake2b(key.encode("utf-8"), digest_size=6).hexdigest()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"{stamp}_{_slug_id_part(label)}_{digest}"

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
    "btc_liq_net_pressure",
    "total_liq_usd_5m",
]

_MM_REBALANCE_CONFIRM_FEATURES = [
    "spread_vs_ma20",
    "kyle_lambda",
    "quote_imbalance",
    "bid_depth_ratio",
    "spread_anomaly",
    "large_trade_buy_ratio",
    "direction_autocorr",
]

_LIQUIDATION_CONFIRM_FEATURES = [
    "btc_liq_net_pressure",
    "total_liq_usd_5m",
    "liq_size_p90_5m",
    "taker_buy_sell_ratio",
    "volume_vs_ma20",
    "direction_net_1m",
    "direction_autocorr",
]

_FUNDING_DIVERGENCE_CONFIRM_FEATURES = [
    "oi_change_rate_5m",
    "oi_change_rate_1h",
    "ls_ratio_change_5m",
    "mark_basis",
    "mark_basis_ma10",
    "rt_funding_rate",
    "avg_trade_size",
]

_NEUTRAL_FEATURE_VALUES: dict[str, float] = {
    "taker_buy_sell_ratio": 1.0,
    "volume_vs_ma20": 1.0,
    "spread_vs_ma20": 1.0,
    "avg_trade_size": 1.0,
    "taker_buy_pct": 0.5,
    "large_trade_buy_ratio": 0.5,
    "sell_notional_share_1m": 0.5,
    "bid_depth_ratio": 0.5,
    "position_in_range_24h": 0.5,
    "position_in_range_4h": 0.5,
    "quote_imbalance": 0.0,
    "spread_anomaly": 0.0,
    "direction_net_1m": 0.0,
    "direction_autocorr": 0.0,
    "btc_liq_net_pressure": 0.0,
    "total_liq_usd_5m": 0.0,
    "liq_size_p90_5m": 0.0,
    "rt_funding_rate": 0.0,
    "funding_rate": 0.0,
    "mark_basis": 0.0,
    "mark_basis_ma10": 0.0,
    "oi_change_rate_5m": 0.0,
    "oi_change_rate_1h": 0.0,
    "ls_ratio_change_5m": 0.0,
    "volume_acceleration": 0.0,
    "kyle_lambda": 0.0,
    "vwap_deviation": 0.0,
    "dist_to_24h_low": 0.0,
    "dist_to_24h_high": 0.0,
    "amplitude_1m": 0.0,
    "amplitude_ma20": 0.0,
    # BLOCK STATE: neutral = 0 (no consecutive blocks)
    "vol_drought_blocks_5m": 0.0,
    "vol_drought_blocks_10m": 0.0,
    "price_compression_blocks_5m": 0.0,
    "price_compression_blocks_10m": 0.0,
}

_FEATURE_MIN_DELTAS: dict[str, float] = {
    "quote_imbalance": 0.02,
    "bid_depth_ratio": 0.015,
    "spread_anomaly": 0.04,
    "direction_net_1m": 0.02,
    "direction_autocorr": 0.02,
    "btc_liq_net_pressure": 0.02,
    "total_liq_usd_5m": 0.05,
    "liq_size_p90_5m": 0.03,
    "mark_basis": 0.0002,
    "mark_basis_ma10": 0.0002,
    "rt_funding_rate": 0.00002,
    "funding_rate": 0.00002,
    "taker_buy_sell_ratio": 0.03,
    "volume_vs_ma20": 0.04,
    "spread_vs_ma20": 0.03,
    "large_trade_buy_ratio": 0.02,
    "sell_notional_share_1m": 0.02,
    "position_in_range_24h": 0.04,
    "position_in_range_4h": 0.04,
    "dist_to_24h_low": 0.002,
    "dist_to_24h_high": 0.002,
    "vwap_deviation": 0.002,
    "oi_change_rate_5m": 0.003,
    "oi_change_rate_1h": 0.003,
    "ls_ratio_change_5m": 0.005,
    # BLOCK STATE: min delta = 1 block
    "vol_drought_blocks_5m": 1.0,
    "vol_drought_blocks_10m": 1.0,
    "price_compression_blocks_5m": 1.0,
    "price_compression_blocks_10m": 1.0,
}

_MECHANISM_EXIT_PARAM_TEMPLATES: dict[str, ExitParams] = {
    "mm_rebalance": ExitParams(
        stop_pct=0.45,
        protect_start_pct=0.10,
        protect_gap_ratio=0.42,
        protect_floor_pct=0.02,
        min_hold_bars=2,
        max_hold_factor=3,
        exit_confirm_bars=1,
        decay_exit_threshold=0.75,
        decay_tighten_threshold=0.40,
        tighten_gap_ratio=0.22,
        mfe_ratchet_threshold=0.08,
        mfe_ratchet_ratio=0.45,
    ),
    "seller_impulse": ExitParams(
        stop_pct=0.55,
        protect_start_pct=0.12,
        protect_gap_ratio=0.45,
        protect_floor_pct=0.025,
        min_hold_bars=3,
        max_hold_factor=3,
        exit_confirm_bars=1,
        decay_exit_threshold=0.78,
        decay_tighten_threshold=0.45,
        tighten_gap_ratio=0.24,
        mfe_ratchet_threshold=0.10,
        mfe_ratchet_ratio=0.42,
    ),
    "volume_climax_reversal": ExitParams(
        stop_pct=0.85,
        protect_start_pct=0.18,
        protect_gap_ratio=0.50,
        protect_floor_pct=0.03,
        min_hold_bars=4,
        max_hold_factor=4,
        exit_confirm_bars=1,
        decay_exit_threshold=0.80,
        decay_tighten_threshold=0.50,
        tighten_gap_ratio=0.28,
        mfe_ratchet_threshold=0.12,
        mfe_ratchet_ratio=0.40,
    ),
    "funding_divergence": ExitParams(
        stop_pct=0.90,
        protect_start_pct=0.22,
        protect_gap_ratio=0.52,
        protect_floor_pct=0.04,
        min_hold_bars=5,
        max_hold_factor=5,
        exit_confirm_bars=2,
        decay_exit_threshold=0.82,
        decay_tighten_threshold=0.55,
        tighten_gap_ratio=0.28,
        mfe_ratchet_threshold=0.15,
        mfe_ratchet_ratio=0.38,
    ),
    "vwap_reversion": ExitParams(
        stop_pct=0.65,
        protect_start_pct=0.14,
        protect_gap_ratio=0.46,
        protect_floor_pct=0.03,
        min_hold_bars=4,
        max_hold_factor=4,
        exit_confirm_bars=1,
        decay_exit_threshold=0.78,
        decay_tighten_threshold=0.45,
        tighten_gap_ratio=0.25,
    ),
    "compression_release": ExitParams(
        stop_pct=0.80,
        protect_start_pct=0.18,
        protect_gap_ratio=0.50,
        protect_floor_pct=0.03,
        min_hold_bars=4,
        max_hold_factor=4,
        exit_confirm_bars=1,
        decay_exit_threshold=0.80,
        decay_tighten_threshold=0.50,
        tighten_gap_ratio=0.28,
    ),
    "seller_drought": ExitParams(
        stop_pct=0.60,
        protect_start_pct=0.12,
        protect_gap_ratio=0.44,
        protect_floor_pct=0.025,
        min_hold_bars=3,
        max_hold_factor=3,
        exit_confirm_bars=1,
        decay_exit_threshold=0.76,
        decay_tighten_threshold=0.42,
        tighten_gap_ratio=0.24,
    ),
    "bottom_taker_exhaust": ExitParams(
        stop_pct=0.65,
        protect_start_pct=0.14,
        protect_gap_ratio=0.46,
        protect_floor_pct=0.03,
        min_hold_bars=4,
        max_hold_factor=4,
        exit_confirm_bars=1,
        decay_exit_threshold=0.78,
        decay_tighten_threshold=0.45,
        tighten_gap_ratio=0.25,
    ),
    "top_buyer_exhaust": ExitParams(
        stop_pct=0.65,
        protect_start_pct=0.14,
        protect_gap_ratio=0.46,
        protect_floor_pct=0.03,
        min_hold_bars=4,
        max_hold_factor=4,
        exit_confirm_bars=1,
        decay_exit_threshold=0.78,
        decay_tighten_threshold=0.45,
        tighten_gap_ratio=0.25,
    ),
    "funding_cycle_oversold": ExitParams(
        stop_pct=0.75,
        protect_start_pct=0.16,
        protect_gap_ratio=0.48,
        protect_floor_pct=0.03,
        min_hold_bars=4,
        max_hold_factor=4,
        exit_confirm_bars=1,
        decay_exit_threshold=0.80,
        decay_tighten_threshold=0.50,
        tighten_gap_ratio=0.27,
    ),
    "amplitude_absorption": ExitParams(
        stop_pct=0.90,
        protect_start_pct=0.20,
        protect_gap_ratio=0.52,
        protect_floor_pct=0.04,
        min_hold_bars=4,
        max_hold_factor=4,
        exit_confirm_bars=1,
        decay_exit_threshold=0.82,
        decay_tighten_threshold=0.52,
        tighten_gap_ratio=0.30,
    ),
    "oi_divergence": ExitParams(
        stop_pct=0.70,
        protect_start_pct=0.16,
        protect_gap_ratio=0.48,
        protect_floor_pct=0.03,
        min_hold_bars=4,
        max_hold_factor=4,
        exit_confirm_bars=1,
        decay_exit_threshold=0.80,
        decay_tighten_threshold=0.48,
        tighten_gap_ratio=0.26,
    ),
    "buyer_impulse": ExitParams(
        stop_pct=0.55,
        protect_start_pct=0.12,
        protect_gap_ratio=0.45,
        protect_floor_pct=0.025,
        min_hold_bars=3,
        max_hold_factor=3,
        exit_confirm_bars=1,
        decay_exit_threshold=0.78,
        decay_tighten_threshold=0.45,
        tighten_gap_ratio=0.24,
        mfe_ratchet_threshold=0.10,
        mfe_ratchet_ratio=0.42,
    ),
}


# ── 机制类型推断（从入场特征推断物理机制）────────────────────────────────────
_FEATURE_TO_MECHANISM: dict[str, str] = {
    # PRICE 维度 → 均值回归类
    "vwap_deviation": "vwap_reversion",
    "position_in_range_24h": "compression_release",
    "position_in_range_4h": "compression_release",
    "dist_to_24h_high": "compression_release",
    "dist_to_24h_low": "seller_drought",
    # TRADE_FLOW 维度
    "taker_buy_sell_ratio": "taker_snap_reversal",
    "volume_vs_ma20": "volume_climax_reversal",
    "volume_acceleration": "volume_climax_reversal",
    "large_trade_buy_ratio": "taker_snap_reversal",
    "direction_net_1m": "seller_impulse",
    "sell_notional_share_1m": "seller_impulse",
    "trade_burst_index": "volume_climax_reversal",
    # LIQUIDITY 维度
    "spread_vs_ma20": "mm_rebalance",
    "kyle_lambda": "mm_rebalance",
    # POSITIONING 维度
    "funding_rate": "funding_settlement",
    "oi_change_rate_5m": "bottom_taker_exhaust",
    "oi_change_rate_1h": "bottom_taker_exhaust",
    # MICROSTRUCTURE
    "amplitude_1m": "amplitude_absorption",
    "amplitude_ma20": "amplitude_absorption",
    # BLOCK STATE (持续性)
    "vol_drought_blocks_5m": "seller_drought",
    "vol_drought_blocks_10m": "seller_drought",
    "price_compression_blocks_5m": "compression_release",
    "price_compression_blocks_10m": "compression_release",
    # MARK_PRICE
    "rt_funding_rate": "funding_divergence",
}

_MECHANISM_CONFIRM_FEATURES: dict[str, list[str]] = {
    "vwap_reversion": [
        "volume_acceleration",
        "volume_vs_ma20",
        "taker_buy_sell_ratio",
        "spread_vs_ma20",
        "avg_trade_size",
    ],
    "compression_release": [
        "volume_vs_ma20",
        "volume_acceleration",
        "spread_vs_ma20",
        "kyle_lambda",
        "oi_change_rate_5m",
        "oi_change_rate_1h",
    ],
    "seller_drought": [
        "volume_vs_ma20",
        "taker_buy_sell_ratio",
        "avg_trade_size",
        "oi_change_rate_5m",
    ],
    "bottom_taker_exhaust": [
        "taker_buy_sell_ratio",
        "oi_change_rate_5m",
        "oi_change_rate_1h",
        "volume_vs_ma20",
    ],
    "top_buyer_exhaust": [
        "taker_buy_sell_ratio",
        "oi_change_rate_5m",
        "oi_change_rate_1h",
        "spread_vs_ma20",
    ],
    "seller_impulse": [
        "taker_buy_sell_ratio",
        "volume_vs_ma20",
        "volume_acceleration",
        "spread_vs_ma20",
        "large_trade_buy_ratio",
        "direction_net_1m",
        "sell_notional_share_1m",
        "trade_burst_index",
        "direction_autocorr",
    ],
    "funding_divergence": [
        "oi_change_rate_5m",
        "oi_change_rate_1h",
        "ls_ratio_change_5m",
        "avg_trade_size",
        "spread_vs_ma20",
    ],
    "funding_cycle_oversold": [
        "taker_buy_sell_ratio",
        "volume_vs_ma20",
        "oi_change_rate_5m",
    ],
}

_FEATURE_TO_MECHANISM.update({
    "quote_imbalance": "mm_rebalance",
    "bid_depth_ratio": "mm_rebalance",
    "spread_anomaly": "mm_rebalance",
    "direction_autocorr": "seller_impulse",
    "btc_liq_net_pressure": "seller_impulse",
    "total_liq_usd_5m": "volume_climax_reversal",
    "liq_size_p90_5m": "volume_climax_reversal",
    "rt_funding_rate": "funding_divergence",
    "mark_basis": "funding_divergence",
    "mark_basis_ma10": "funding_divergence",
})

_MECHANISM_CONFIRM_FEATURES.update({
    "mm_rebalance": list(_MM_REBALANCE_CONFIRM_FEATURES),
    "seller_impulse": list(_SELLER_IMPULSE_CONFIRM_FEATURES),
    "volume_climax_reversal": list(_LIQUIDATION_CONFIRM_FEATURES),
    "funding_divergence": list(_FUNDING_DIVERGENCE_CONFIRM_FEATURES),
    "funding_cycle_oversold": [
        "taker_buy_sell_ratio",
        "volume_vs_ma20",
        "oi_change_rate_5m",
        "quote_imbalance",
        "bid_depth_ratio",
        "mark_basis_ma10",
        "rt_funding_rate",
    ],
    "compression_release": [
        "volume_vs_ma20",
        "volume_acceleration",
        "spread_vs_ma20",
        "kyle_lambda",
        "quote_imbalance",
        "bid_depth_ratio",
        "oi_change_rate_5m",
        "oi_change_rate_1h",
    ],
    "seller_drought": [
        "volume_vs_ma20",
        "taker_buy_sell_ratio",
        "avg_trade_size",
        "quote_imbalance",
        "bid_depth_ratio",
        "oi_change_rate_5m",
    ],
    "amplitude_absorption": [
        "spread_anomaly",
        "quote_imbalance",
        "bid_depth_ratio",
        "volume_acceleration",
        "trade_burst_index",
        "btc_liq_net_pressure",
    ],
    "oi_divergence": [
        "oi_change_rate_5m",
        "oi_change_rate_1h",
        "taker_buy_sell_ratio",
        "volume_vs_ma20",
        "large_trade_buy_ratio",
        "direction_net_1m",
        "spread_vs_ma20",
    ],
    "buyer_impulse": [
        "taker_buy_sell_ratio",
        "volume_vs_ma20",
        "volume_acceleration",
        "spread_vs_ma20",
        "large_trade_buy_ratio",
        "direction_net_1m",
        "sell_notional_share_1m",
        "trade_burst_index",
        "direction_autocorr",
    ],
})


def _infer_mechanism_type(
    entry_feature: str,
    direction: str,
    operator: str = "",
) -> str:
    """
    从入场特征和方向推断最可能的物理机制类型。

    推断逻辑:
      1. 精确匹配 entry_feature → mechanism
      2. 方向修正: 某些机制在不同方向有不同名称
      3. 兜底: generic_alpha
    """
    base_mechanism = _FEATURE_TO_MECHANISM.get(entry_feature, "generic_alpha")

    # 方向特判
    if entry_feature == "dist_to_24h_low" and direction == "short":
        return "top_buyer_exhaust"
    if (
        entry_feature == "taker_buy_sell_ratio"
        and operator == "<"
        and direction == "short"
    ):
        return "seller_impulse"
    if entry_feature == "taker_buy_sell_ratio" and direction == "long":
        return "bottom_taker_exhaust"
    if (
        entry_feature in {"volume_vs_ma20", "volume_acceleration"}
        and operator == ">"
        and direction == "short"
    ):
        return "seller_impulse"
    if (
        entry_feature == "large_trade_buy_ratio"
        and operator == "<"
        and direction == "short"
    ):
        return "seller_impulse"
    if (
        entry_feature == "direction_net_1m"
        and operator == "<"
        and direction == "short"
    ):
        return "seller_impulse"
    if (
        entry_feature == "sell_notional_share_1m"
        and operator == ">"
        and direction == "short"
    ):
        return "seller_impulse"
    if (
        entry_feature == "trade_burst_index"
        and operator == ">"
        and direction == "short"
    ):
        return "seller_impulse"
    # ── buyer_impulse: LONG mirrors of seller_impulse ────────────────────
    if (
        entry_feature == "taker_buy_sell_ratio"
        and operator == ">"
        and direction == "long"
    ):
        return "buyer_impulse"
    if (
        entry_feature in {"volume_vs_ma20", "volume_acceleration"}
        and operator == ">"
        and direction == "long"
    ):
        return "buyer_impulse"
    if (
        entry_feature == "large_trade_buy_ratio"
        and operator == ">"
        and direction == "long"
    ):
        return "buyer_impulse"
    if (
        entry_feature == "direction_net_1m"
        and operator == ">"
        and direction == "long"
    ):
        return "buyer_impulse"
    if (
        entry_feature == "sell_notional_share_1m"
        and operator == "<"
        and direction == "long"
    ):
        return "buyer_impulse"
    if (
        entry_feature == "trade_burst_index"
        and operator == ">"
        and direction == "long"
    ):
        return "buyer_impulse"
    if entry_feature == "direction_autocorr" and direction == "long":
        return "buyer_impulse"
    if entry_feature == "funding_rate" and direction == "long":
        return "funding_cycle_oversold"
    if entry_feature == "position_in_range_4h" and direction == "short":
        return "funding_divergence"
    if entry_feature in {"quote_imbalance", "bid_depth_ratio"}:
        return "mm_rebalance"
    if entry_feature == "spread_anomaly":
        return "mm_rebalance" if operator == ">" else "compression_release"
    if entry_feature == "direction_autocorr":
        return "seller_impulse"
    if entry_feature == "btc_liq_net_pressure":
        return "seller_impulse" if direction == "short" else "volume_climax_reversal"
    if entry_feature in {"total_liq_usd_5m", "liq_size_p90_5m"}:
        return "volume_climax_reversal"
    if entry_feature in {"rt_funding_rate", "mark_basis", "mark_basis_ma10"}:
        return "funding_divergence"

    return base_mechanism


def _confirm_features_for_mechanism(mechanism_type: str) -> list[str]:
    return list(_MECHANISM_CONFIRM_FEATURES.get(mechanism_type, CONFIRM_FEATURES))


def _feature_neutral_value(feature: str, reference: float = 0.0) -> float:
    if feature in _NEUTRAL_FEATURE_VALUES:
        return _NEUTRAL_FEATURE_VALUES[feature]
    if "position_in_range" in feature:
        return 0.5
    if feature.endswith("_ratio"):
        return 1.0
    return 0.0


def _feature_min_delta(feature: str, reference: float) -> float:
    return max(_FEATURE_MIN_DELTAS.get(feature, 0.0), abs(reference) * 0.15, 1e-4)


def _signed_decay_gap(feature: str, entry_op: str, entry_threshold: float) -> tuple[float, float, float]:
    neutral = _feature_neutral_value(feature, entry_threshold)
    raw_gap = neutral - entry_threshold
    if abs(raw_gap) < 1e-8:
        fallback = _feature_min_delta(feature, entry_threshold)
        raw_gap = fallback if entry_op == "<" else -fallback
    base_gap = max(abs(raw_gap), _feature_min_delta(feature, entry_threshold))
    return neutral, raw_gap, base_gap


def _build_vs_entry_condition(
    feature: str,
    delta: float,
    *,
    source: str,
    role: str,
    neutral_value: float,
) -> dict:
    return {
        "feature": f"{feature}{_VS_ENTRY_TAG}",
        "operator": ">" if delta > 0 else "<",
        "threshold": round(float(delta), 8),
        "source": source,
        "role": role,
        "neutral_value": round(float(neutral_value), 8),
    }


def _build_absolute_condition(
    feature: str,
    target_value: float,
    *,
    operator: str,
    source: str,
    role: str,
    neutral_value: float,
) -> dict:
    return {
        "feature": feature,
        "operator": operator,
        "threshold": round(float(target_value), 8),
        "source": source,
        "role": role,
        "neutral_value": round(float(neutral_value), 8),
    }


def _build_force_decay_condition(feature: str, entry_op: str, entry_threshold: float) -> tuple[dict, dict]:
    neutral, raw_gap, base_gap = _signed_decay_gap(feature, entry_op, entry_threshold)
    decay_delta = np.sign(raw_gap) * max(base_gap * _FORCE_DECAY_RATIO, _feature_min_delta(feature, entry_threshold))
    repair_target = entry_threshold + decay_delta
    return (
        _build_vs_entry_condition(
            feature,
            float(decay_delta),
            source="force_decay",
            role="decay",
            neutral_value=neutral,
        ),
        _build_absolute_condition(
            feature,
            repair_target,
            operator=">" if decay_delta > 0 else "<",
            source="force_decay",
            role="repair_target",
            neutral_value=neutral,
        ),
    )


def _build_invalidation_condition(feature: str, entry_op: str, entry_threshold: float) -> dict:
    neutral, raw_gap, base_gap = _signed_decay_gap(feature, entry_op, entry_threshold)
    invalid_delta = -np.sign(raw_gap) * max(base_gap * _FORCE_INVALIDATION_RATIO, _feature_min_delta(feature, entry_threshold))
    return _build_vs_entry_condition(
        feature,
        float(invalid_delta),
        source="thesis_invalidation",
        role="invalidation",
        neutral_value=neutral,
    )


def _dedupe_combo_entries(entries: list[dict]) -> list[dict]:
    deduped: list[dict] = []
    seen: set[tuple[tuple[str, str, float], ...]] = set()
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        conditions = entry.get("conditions")
        if not isinstance(conditions, list) or not conditions:
            continue
        key_parts: list[tuple[str, str, float]] = []
        valid = True
        for cond in conditions:
            if not isinstance(cond, dict):
                valid = False
                break
            feature = str(cond.get("feature", "")).strip()
            operator = str(cond.get("operator") or cond.get("op") or "").strip()
            if not feature or operator not in {"<", ">"}:
                valid = False
                break
            try:
                threshold = float(cond.get("threshold"))
            except (TypeError, ValueError):
                valid = False
                break
            key_parts.append((feature, operator, round(threshold, 8)))
        if not valid:
            continue
        key = tuple(key_parts)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)
    return deduped


def _build_mechanism_exit_params(
    mechanism_type: str,
    horizon: int,
    direction: str,
) -> ExitParams:
    base = _MECHANISM_EXIT_PARAM_TEMPLATES.get(mechanism_type, _ALPHA_DEFAULT_EXIT_PARAMS)
    stop_pct = float(base.stop_pct)
    protect_start_pct = float(base.protect_start_pct)
    max_hold_factor = int(base.max_hold_factor)
    min_hold_bars = int(base.min_hold_bars)
    protect_gap_ratio = float(base.protect_gap_ratio)

    # 根据 horizon 自适应参数（短周期力需要更紧的止损和更敏感的保本）
    if horizon <= 5:
        # 3-5 分钟微结构力：利润空间 0.05-0.15%，止损必须极紧
        stop_pct = 0.15
        protect_start_pct = 0.03
        protect_gap_ratio = 0.30
        min_hold_bars = 1
        max_hold_factor = 3
    elif horizon <= 15:
        # 5-15 分钟短周期力：利润空间 0.10-0.30%
        stop_pct = 0.25
        protect_start_pct = 0.04
        protect_gap_ratio = 0.35
        min_hold_bars = 2
        max_hold_factor = 3
    elif horizon <= 30:
        # 30 分钟中周期力：利润空间 0.15-0.50%
        stop_pct = 0.40
        protect_start_pct = 0.05
        protect_gap_ratio = 0.40
        min_hold_bars = 3
        max_hold_factor = 4
    else:
        # 60 分钟长周期力
        stop_pct = max(stop_pct, 0.50)
        protect_start_pct *= 1.10
        max_hold_factor = max(max_hold_factor, 4)

    if mechanism_type in {"seller_impulse", "volume_climax_reversal"} and direction == "short":
        stop_pct *= 1.05
    if mechanism_type == "mm_rebalance" and horizon <= 15:
        min_hold_bars = min(min_hold_bars, 2)

    return ExitParams(
        take_profit_pct=base.take_profit_pct,
        stop_pct=round(stop_pct, 4),
        protect_start_pct=round(protect_start_pct, 4),
        protect_gap_ratio=round(protect_gap_ratio, 4),
        protect_floor_pct=base.protect_floor_pct,
        min_hold_bars=min_hold_bars,
        max_hold_factor=max_hold_factor,
        exit_confirm_bars=base.exit_confirm_bars,
        decay_exit_threshold=base.decay_exit_threshold,
        decay_tighten_threshold=base.decay_tighten_threshold,
        tighten_gap_ratio=base.tighten_gap_ratio,
        confidence_stop_multipliers=dict(base.confidence_stop_multipliers),
        regime_stop_multipliers=dict(base.regime_stop_multipliers),
        regime_stop_multipliers_short=dict(base.regime_stop_multipliers_short),
        mfe_ratchet_threshold=base.mfe_ratchet_threshold,
        mfe_ratchet_ratio=base.mfe_ratchet_ratio,
    )


def _build_stop_logic(mechanism_type: str, exit_params: ExitParams, *, direction: str) -> dict:
    return {
        "type": "mechanism_hard_stop",
        "mechanism_type": mechanism_type,
        "direction": direction,
        "stop_pct": round(float(exit_params.stop_pct), 4),
        "reason": "thesis_invalidated_or_force_stalled",
    }


def _compile_combo_set(df: pd.DataFrame, combo_entries: object) -> list[list[dict]]:
    combos = combo_entries if isinstance(combo_entries, list) else []
    compiled: list[list[dict]] = []
    for combo in combos:
        if not isinstance(combo, dict):
            continue
        conds = combo.get("conditions")
        if not isinstance(conds, list) or not conds:
            continue
        parsed_combo: list[dict] = []
        valid = True
        for cond in conds:
            if not isinstance(cond, dict):
                valid = False
                break
            feature = str(cond.get("feature", "")).strip()
            operator = str(cond.get("operator") or cond.get("op") or "").strip()
            if not feature or operator not in {"<", ">"}:
                valid = False
                break
            base_feature = (
                feature[: -len(_VS_ENTRY_TAG)]
                if feature.endswith(_VS_ENTRY_TAG)
                else feature
            )
            if base_feature not in df.columns:
                valid = False
                break
            try:
                threshold = float(cond.get("threshold"))
            except (TypeError, ValueError):
                valid = False
                break
            parsed_combo.append(
                {
                    "values": df[base_feature].values,
                    "operator": operator,
                    "threshold": threshold,
                    "vs_entry": feature.endswith(_VS_ENTRY_TAG),
                }
            )
        if valid and parsed_combo:
            compiled.append(parsed_combo)
    return compiled


def _combo_matches(compiled_combos: list[list[dict]], entry_idx: int, bar_idx: int) -> bool:
    for combo in compiled_combos:
        all_met = True
        for cond in combo:
            values = cond["values"]
            value = values[bar_idx]
            if np.isnan(value):
                all_met = False
                break
            if cond["vs_entry"]:
                entry_value = values[entry_idx]
                if np.isnan(entry_value):
                    all_met = False
                    break
                value = value - entry_value
            if cond["operator"] == "<" and not (value < cond["threshold"]):
                all_met = False
                break
            if cond["operator"] == ">" and not (value > cond["threshold"]):
                all_met = False
                break
        if all_met:
            return True
    return False


def _derive_mechanism_exit(
    entry_feature: str,
    entry_op: str,
    entry_threshold: float,
    combo_conditions: list[dict] | None = None,
) -> dict:
    """
    入场信号消失 = 出场。

    根据入场条件推导出场条件（机制反转），不依赖历史数据挖掘：
      entry feature > threshold → exit feature < threshold * factor
      entry feature < threshold → exit feature > threshold * factor

    Factor 规则：
      阈值为负 (如 dist_to_24h_high = -0.01):
        >: exit = threshold * 1.5  (更负 = 已离开触发区)
        <: exit = threshold * 0.3  (接近零 = 条件消退)
      阈值为正 (如 spread_vs_ma20 = 1.5):
        >: exit = threshold * 0.7  (回落到均值附近 = 异常消退)
        <: exit = threshold * 10   (显著反转才出场)
      阈值接近零 (|threshold| < 1e-3):
        >: exit = -|threshold| * 5
        <: exit =  |threshold| * 5
    """
    def _exit_thr(op: str, thr: float) -> float:
        if abs(thr) < 1e-3:
            return -abs(thr) * 5 if op == ">" else abs(thr) * 5
        elif thr < 0:
            return thr * 1.5 if op == ">" else thr * 0.3
        else:
            return thr * 0.7 if op == ">" else thr * 10.0

    exit_op = "<" if entry_op == ">" else ">"
    primary = {
        "feature":   entry_feature,
        "operator":  exit_op,
        "threshold": round(_exit_thr(entry_op, entry_threshold), 8),
        "source":    "causal",
    }

    combos: list[dict] = [
        {
            "conditions":  [primary],
            "combo_label": "C1",
            "description": (
                f"Mechanism gone: {entry_feature} no longer "
                f"{entry_op} {entry_threshold:.6g}"
            ),
        }
    ]

    if combo_conditions:
        cc = combo_conditions[0]
        cc_op  = cc["op"]
        cc_thr = float(cc["threshold"])
        cc_exit = {
            "feature":   cc["feature"],
            "operator":  "<" if cc_op == ">" else ">",
            "threshold": round(_exit_thr(cc_op, cc_thr), 8),
            "source":    "causal",
        }
        combos.append({
            "conditions":  [primary, cc_exit],
            "combo_label": "C2",
            "description": "Both entry conditions reversed: full mechanism gone",
        })

    return {"top3": combos, "exit_method": "causal"}


def _optimize_exit_params(
    engine,
    df: pd.DataFrame,
    entry_mask,
    exit_info: dict,
    direction: str,
    horizon: int,
    base_params: ExitParams,
    *,
    use_full_data: bool = False,
) -> tuple[ExitParams, dict]:
    """在 OOS 数据上网格扫描 stop_pct 和 protect_start_pct，找到 PF 最高的参数组合。

    不硬编码，用数据说话。
    use_full_data=True 时跳过 OOS 切分（用于已被种子挖掘器 OOS 验证过的多条件种子）。
    """
    # 止损网格：根据 horizon 确定扫描范围
    if horizon <= 5:
        stop_grid = [0.08, 0.10, 0.12, 0.15, 0.20]
        protect_grid = [0.02, 0.03, 0.04, 0.05]
    elif horizon <= 15:
        stop_grid = [0.12, 0.15, 0.20, 0.25, 0.30]
        protect_grid = [0.03, 0.04, 0.05, 0.06]
    elif horizon <= 30:
        stop_grid = [0.20, 0.25, 0.30, 0.40, 0.50]
        protect_grid = [0.04, 0.05, 0.06, 0.08]
    else:
        stop_grid = [0.30, 0.40, 0.50, 0.70, 1.00]
        protect_grid = [0.05, 0.08, 0.10, 0.12]

    best_pf = -1.0
    best_params = base_params
    best_metrics: dict = {}

    for stop in stop_grid:
        for protect in protect_grid:
            trial = ExitParams(
                take_profit_pct=base_params.take_profit_pct,
                stop_pct=stop,
                protect_start_pct=protect,
                protect_gap_ratio=base_params.protect_gap_ratio,
                protect_floor_pct=max(0.01, protect * 0.5),
                min_hold_bars=base_params.min_hold_bars,
                max_hold_factor=base_params.max_hold_factor,
                exit_confirm_bars=base_params.exit_confirm_bars,
                decay_exit_threshold=base_params.decay_exit_threshold,
                decay_tighten_threshold=base_params.decay_tighten_threshold,
            )
            metrics = engine._shadow_smart_exit_backtest(
                df, entry_mask, exit_info, direction, horizon, params=trial,
                use_full_data=use_full_data,
            )
            pf = metrics.get("earliest_pf", 0)
            net = metrics.get("net_return_with_exit", 0)
            # 选 PF 最高且净收益 > 0 的参数
            if pf > best_pf and net > 0:
                best_pf = pf
                best_params = trial
                best_metrics = metrics

    # 如果所有参数都不行，用默认参数的结果
    if not best_metrics:
        best_metrics = engine._shadow_smart_exit_backtest(
            df, entry_mask, exit_info, direction, horizon, params=base_params,
            use_full_data=use_full_data,
        )
        best_params = base_params

    if best_pf > 0:
        logger.info(
            "  [PARAM-OPT] best stop=%.2f%% protect=%.2f%% -> PF=%.2f net=%.4f%%",
            best_params.stop_pct, best_params.protect_start_pct,
            best_pf, best_metrics.get("net_return_with_exit", 0),
        )

    return best_params, best_metrics


def _mine_exit_conditions(
    df: pd.DataFrame,
    entry_mask: pd.Series,
    direction: str,
    horizon: int,
    entry_feature: str,
    entry_op: str,
    entry_threshold: float,
    combo_conditions: list[dict] | None = None,
    *,
    mechanism_type: str = "data_driven",
    min_exit_samples: int = 15,
) -> dict | None:
    """从历史数据中挖掘最优出场条件，不用模板。

    核心思路：
      1. 对每个入场点，逐 bar 追踪收益和特征 vs_entry 变化
      2. 识别 MFE 峰值时刻（利润最高点）
      3. 找到哪些 vs_entry 特征在 MFE 峰值附近发出了一致的信号
      4. 选 Top-3 出场组合（按捕获利润比排序）

    如果样本不足或找不到好条件，返回 None（调用方回退到模板）。
    """
    close = df["close"].values if "close" in df.columns else None
    if close is None:
        return None

    n_total = len(df)
    mask_values = entry_mask.reindex(df.index, fill_value=False).values
    # 只在后 40% 数据上挖掘（OOS）
    split_idx = int(n_total * 0.60)
    entry_positions = [
        i for i in range(split_idx, n_total)
        if mask_values[i] and (i + horizon * 4) < n_total
    ]
    if len(entry_positions) < min_exit_samples:
        return None

    sign = -1.0 if direction == "short" else 1.0
    max_hold = horizon * 4

    # 收集入场种子 + 确认因子的 vs_entry 特征列
    force_features = [entry_feature]
    if combo_conditions:
        for cc in combo_conditions:
            f = str(cc.get("feature", ""))
            if f and f not in force_features:
                force_features.append(f)

    # 收集所有数值列中可以做 vs_entry 的特征
    candidate_features = []
    for col in df.columns:
        if col.startswith("fwd_") or col in ("close", "open", "high", "low", "volume",
                                               "timestamp", "quote_volume", "trades"):
            continue
        if not np.issubdtype(df[col].dtype, np.number):
            continue
        candidate_features.append(col)

    if not candidate_features:
        return None

    # ── 为每个入场点，追踪 MFE 和各特征的 vs_entry ──
    # 结构: exit_data[bar_offset] = {feature_name: [vs_entry_values...], "pnl": [...], "past_mfe": [...]}
    feature_vs_entry_at_mfe = {f: [] for f in candidate_features}
    feature_vs_entry_at_late = {f: [] for f in candidate_features}
    mfe_values = []

    for entry_idx in entry_positions:
        entry_price = close[entry_idx]
        if entry_price == 0 or np.isnan(entry_price):
            continue

        # 逐 bar 追踪
        bar_pnl = []
        for offset in range(1, max_hold + 1):
            idx = entry_idx + offset
            if idx >= n_total:
                break
            pnl = (close[idx] - entry_price) / entry_price * sign * 100.0
            bar_pnl.append(pnl)

        if len(bar_pnl) < horizon:
            continue

        mfe = max(bar_pnl)
        mfe_bar = bar_pnl.index(mfe) + 1  # 从 1 开始的偏移量
        mfe_values.append(mfe)

        if mfe <= 0:
            continue  # 方向完全反了，跳过

        # 在 MFE 峰值附近（峰值后 1-3 bar）= "好出场"时刻的 vs_entry
        good_exit_bar = min(mfe_bar + 2, len(bar_pnl))
        good_idx = entry_idx + good_exit_bar
        if good_idx >= n_total:
            continue

        # MFE 峰值后很久（峰值后 > 5 bar）= "坏出场"时刻
        late_bar = min(mfe_bar + max(6, horizon), len(bar_pnl))
        late_idx = entry_idx + late_bar
        if late_idx >= n_total:
            late_idx = min(entry_idx + len(bar_pnl), n_total - 1)

        for f in candidate_features:
            col_vals = df[f].values
            entry_val = col_vals[entry_idx]
            if np.isnan(entry_val):
                feature_vs_entry_at_mfe[f].append(np.nan)
                feature_vs_entry_at_late[f].append(np.nan)
                continue
            good_vs = col_vals[good_idx] - entry_val if good_idx < n_total else np.nan
            late_vs = col_vals[late_idx] - entry_val if late_idx < n_total else np.nan
            feature_vs_entry_at_mfe[f].append(good_vs)
            feature_vs_entry_at_late[f].append(late_vs)

    if len(mfe_values) < min_exit_samples:
        return None

    # ── 对每个特征，找区分好出场/坏出场的最优阈值 ──
    scored_exits: list[tuple[float, str, str, float]] = []  # (score, feature, operator, threshold)

    for f in candidate_features:
        good_arr = np.array(feature_vs_entry_at_mfe[f], dtype=np.float64)
        late_arr = np.array(feature_vs_entry_at_late[f], dtype=np.float64)

        valid_good = good_arr[~np.isnan(good_arr)]
        valid_late = late_arr[~np.isnan(late_arr)]

        if len(valid_good) < min_exit_samples or len(valid_late) < min_exit_samples:
            continue

        # 好出场和坏出场的中位数差异 → 判断方向
        good_median = np.median(valid_good)
        late_median = np.median(valid_late)

        if abs(good_median - late_median) < 1e-10:
            continue

        # 如果好出场时 vs_entry 更小 → 出场条件: vs_entry < threshold
        # 如果好出场时 vs_entry 更大 → 出场条件: vs_entry > threshold
        if good_median < late_median:
            op = "<"
            # 阈值 = 好出场分布的 60 百分位（偏保守，不太早出）
            threshold = float(np.percentile(valid_good, 60))
            # 验证: 用这个阈值，有多少比例的好出场被捕获
            capture_rate = float(np.mean(valid_good < threshold))
            false_early = float(np.mean(valid_late < threshold))
        else:
            op = ">"
            threshold = float(np.percentile(valid_good, 40))
            capture_rate = float(np.mean(valid_good > threshold))
            false_early = float(np.mean(valid_late > threshold))

        # 评分 = 捕获率 - 误判率（越高越好）
        score = capture_rate - false_early
        if score > 0.10 and capture_rate > 0.40:
            scored_exits.append((score, f, op, threshold))

    if not scored_exits:
        return None

    # 按得分排序，取 Top-3
    scored_exits.sort(key=lambda x: -x[0])

    # 优先选入场特征的 vs_entry（因果关联最强）
    priority_exits = [e for e in scored_exits if e[1] in force_features]
    other_exits = [e for e in scored_exits if e[1] not in force_features]
    ranked = priority_exits + other_exits

    top3_combos = []
    used_features = set()
    for score, feat, op, thr in ranked:
        if feat in used_features:
            continue
        used_features.add(feat)
        vs_entry_name = f"{feat}_vs_entry"
        top3_combos.append({
            "conditions": [{
                "feature": vs_entry_name,
                "operator": op,
                "threshold": round(thr, 6),
                "source": "data_mined",
                "role": "force_decay_mined",
                "neutral_value": 0.0,
            }],
            "combo_label": f"D{len(top3_combos) + 1}",
            "description": f"{feat} vs_entry {op} {thr:.4f} (score={score:.2f})",
        })
        if len(top3_combos) >= 3:
            break

    if not top3_combos:
        return None

    # 因果耦合检查: 确保入场特征出现在出场条件中
    top3_combos = _ensure_causal_exit(entry_feature, entry_op, top3_combos)

    return {
        "top3": top3_combos,
        "invalidation": [],  # 数据挖掘暂不生成反向无效化条件
        "exit_method": "data_mined_vs_entry",
        "snapshot_required": True,
        "mechanism_type": mechanism_type,
        "force_features": force_features,
    }


def _ensure_causal_exit(entry_feature: str, entry_op: str, combos: list[dict]) -> list[dict]:
    """确保出场条件至少包含 1 个基于入场种子特征的 vs_entry 变化量。

    力消失了才出场，不是统计巧合。如果所有 combo 都没有入场特征的
    vs_entry 条件，自动在第一个 combo 中添加占位条件。
    """
    entry_vs = f"{entry_feature}_vs_entry"
    has_causal = any(
        any(
            entry_feature in str(c.get("feature", ""))
            and "_vs_entry" in str(c.get("feature", ""))
            for c in combo.get("conditions", [])
        )
        for combo in combos
    )
    if not has_causal and combos:
        # 入场 op=">" 意味着特征高时入场，衰竭是特征下降 → vs_entry < 0 → operator="<"
        # 入场 op="<" 意味着特征低时入场，衰竭是特征上升 → vs_entry > 0 → operator=">"
        decay_op = "<" if entry_op == ">" else ">"
        combos[0]["conditions"].append({
            "feature": entry_vs,
            "operator": decay_op,
            "threshold": 0.0,
            "source": "causal_bridge",
            "role": "entry_force_decay",
            "neutral_value": 0.0,
        })
        combos[0]["description"] = (
            combos[0].get("description", "") +
            f" [causal: {entry_vs} decay added]"
        )
    return combos


def _derive_mechanism_exit_v2(
    entry_feature: str,
    entry_op: str,
    entry_threshold: float,
    combo_conditions: list[dict] | None = None,
    *,
    mechanism_type: str,
) -> dict:
    primary_decay, primary_abs = _build_force_decay_condition(
        entry_feature, entry_op, entry_threshold
    )
    primary_invalidation = _build_invalidation_condition(
        entry_feature, entry_op, entry_threshold
    )

    combos: list[dict] = [
        {
            "conditions": [primary_decay],
            "combo_label": "C1",
            "description": f"{entry_feature} relative to entry has repaired enough",
        },
        {
            "conditions": [primary_decay, primary_abs],
            "combo_label": "C2",
            "description": f"{entry_feature} repaired versus entry and absolute state",
        },
    ]
    invalidation: list[dict] = [
        {
            "conditions": [primary_invalidation],
            "combo_label": "I1",
            "description": f"{entry_feature} worsened versus entry; thesis broken",
        }
    ]

    if combo_conditions:
        confirm_decay_conditions: list[dict] = []
        confirm_abs_conditions: list[dict] = []
        confirm_invalidation_conditions: list[dict] = []
        for idx, cc in enumerate(combo_conditions, start=1):
            cc_feature = str(cc["feature"])
            cc_op = str(cc["op"])
            cc_thr = float(cc["threshold"])
            confirm_decay, confirm_abs = _build_force_decay_condition(
                cc_feature, cc_op, cc_thr
            )
            confirm_invalidation = _build_invalidation_condition(
                cc_feature, cc_op, cc_thr
            )
            confirm_decay_conditions.append(confirm_decay)
            confirm_abs_conditions.append(confirm_abs)
            confirm_invalidation_conditions.append(confirm_invalidation)
            combos.extend(
                [
                    {
                        "conditions": [primary_decay, confirm_decay],
                        "combo_label": f"C{len(combos) + 1}",
                        "description": f"Entry force and confirm force {idx} both repaired versus entry",
                    },
                    {
                        "conditions": [primary_abs, confirm_abs],
                        "combo_label": f"C{len(combos) + 2}",
                        "description": f"Primary state and confirm state {idx} both returned toward neutral",
                    },
                ]
            )
            invalidation.append(
                {
                    "conditions": [primary_invalidation, confirm_invalidation],
                    "combo_label": f"I{len(invalidation) + 1}",
                    "description": f"Primary force and confirm force {idx} both worsened versus entry",
                }
            )

        combos.extend(
            [
                {
                    "conditions": [primary_decay, *confirm_decay_conditions],
                    "combo_label": f"C{len(combos) + 1}",
                    "description": "Entry force and all confirm forces repaired versus entry",
                },
                {
                    "conditions": [primary_abs, *confirm_abs_conditions],
                    "combo_label": f"C{len(combos) + 2}",
                    "description": "Primary state and all confirm states returned toward neutral",
                },
            ]
        )
        invalidation.append(
            {
                "conditions": [primary_invalidation, *confirm_invalidation_conditions],
                "combo_label": f"I{len(invalidation) + 1}",
                "description": "Primary force and all confirm forces worsened versus entry",
            }
        )

    # -- 因果耦合检查: 出场条件必须包含入场种子特征的 vs_entry 变化量 --
    combos = _ensure_causal_exit(entry_feature, entry_op, combos)

    return {
        "top3": _dedupe_combo_entries(combos)[:3],
        "invalidation": _dedupe_combo_entries(invalidation),
        "exit_method": "force_decay_vs_entry",
        "snapshot_required": True,
        "mechanism_type": mechanism_type,
        "force_features": [entry_feature] + [
            str(item.get("feature", ""))
            for item in (combo_conditions or [])
            if isinstance(item, dict) and item.get("feature")
        ],
    }


class LiveDiscoveryEngine:
    """
    Alpha 策略自动发现引擎。

    Args:
        storage_path: Parquet 数据根目录 (默认 data/storage)
        symbol:       交易对标识，用于隔离输出目录 (默认 BTCUSDT)
        top_n:        IC 扫描后取 Top-N 特征挖掘原子
        horizons:     前向收益预测周期 (bars)
        min_triggers: 原子最低触发次数 (IS+OOS 合并)
        min_icir:     原子最低 |ICIR| 门槛
    """

    def __init__(
        self,
        storage_path: str = "data/storage",
        symbol: str = "BTCUSDT",
        top_n: int = 20,
        horizons: Optional[list[int]] = None,
        min_triggers: int = 30,
        min_icir: float = 0.10,
        direction_filter: str = "both",
    ):
        self.storage_path = storage_path
        self.symbol = symbol.upper()
        self.top_n = top_n
        self.horizons = horizons or [3, 5, 10, 15, 30, 60]
        self.min_triggers = min_triggers
        self.min_icir = min_icir
        self.direction_filter = direction_filter  # "long", "short", or "both"

        # 输出目录: BTCUSDT 沿用 alpha/output/ 保持向后兼容
        # 其他交易对使用 alpha/output/{symbol}/
        if self.symbol == "BTCUSDT":
            self._output_dir = _DEFAULT_OUTPUT_DIR
        else:
            self._output_dir = _DEFAULT_OUTPUT_DIR / self.symbol
        self._candidates_file = self._output_dir / "candidate_rules.json"
        self._scan_status_file = self._output_dir / "discovery_status.json"
        self._pending_file = self._output_dir / "pending_rules.json"
        self._approved_file = self._output_dir / "approved_rules.json"
        self._flagged_file = self._output_dir / "flagged_rules.json"

        self._fe = FeatureEngine(storage_path=storage_path)
        self._scanner = FeatureScanner(horizons=self.horizons, min_days=10)
        self._miner = AtomMiner(
            min_triggers=min_triggers,
            min_icir=min_icir,
            max_trigger_rate=0.25,
        )
        self._validator = WalkForwardValidator(
            train_frac=0.67,
            fee_pct=_MAKER_FEE_TOTAL,  # maker 费
        )
        self._explainer = AutoExplainer()
        self._realtime_seed_miner = RealtimeSeedMiner(
            train_frac=self._validator.train_frac,
        )

        self._output_dir.mkdir(parents=True, exist_ok=True)
        self._kimi_researcher = None
        self._sandbox = None

    # ── 主入口 ────────────────────────────────────────────────────────────────

    @property
    def _sandbox_executor(self):
        if self._sandbox is None:
            from alpha.sandbox_executor import SandboxExecutor

            self._sandbox = SandboxExecutor()
        return self._sandbox

    def _get_researcher(self):
        if self._kimi_researcher is None:
            from alpha.llm_researcher import KimiResearcher

            self._kimi_researcher = KimiResearcher()
        return self._kimi_researcher

    def run_once(self, data_days: int = 365) -> list[dict]:
        """
        执行一次完整发现流程。

        Args:
            data_days: 使用最近多少天数据

        Returns:
            合格策略候选列表 (同时写入 pending_rules.json)
        """
        logger.info("=" * 60)
        logger.info(f"[DISCOVERY] 开始发现流程 (最近 {data_days} 天数据)")
        logger.info("=" * 60)

        # ── Step 1: 加载数据 ─────────────────────────────────────────────────
        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=data_days)
        start_str = str(start_date)
        end_str = str(end_date)

        logger.info(f"[DISCOVERY] 加载数据: {start_str} ~ {end_str}")
        try:
            df = self._fe.load_date_range(start_str, end_str)
        except Exception as exc:
            logger.error(f"[DISCOVERY] 数据加载失败: {exc}")
            return []

        if len(df) < 2000:
            logger.warning(f"[DISCOVERY] 数据量不足 ({len(df)} bars)，跳过")
            return []

        logger.info(f"[DISCOVERY] 数据加载完成: {len(df):,} bars, {df.shape[1]} 列")

        # ── Step 2: 计算前向收益 ─────────────────────────────────────────────
        df = self._scanner.add_forward_returns(df)

        # ── Step 3: IC 扫描 ──────────────────────────────────────────────────
        logger.info("[DISCOVERY] IC 扫描中...")
        scan_df = self._scanner.scan_all(df)

        if scan_df.empty:
            logger.warning("[DISCOVERY] IC 扫描结果为空，跳过")
            return []

        # TIME 维度保留: minutes_to_funding 是物理约束 (币安每8小时结算)
        # ComboScanner 已确保 TIME 只能做种子不能做确认 (物理约束)
        logger.info(f"[DISCOVERY] 扫描完成: {len(scan_df)} 条结果")

        # ── Step 4: 挖掘种子原子 (单条件阈值，作为 combo 的种子) ──────────────
        logger.info(f"[DISCOVERY] 挖掘 Top-{self.top_n} 特征的种子原子...")
        seed_scan_df = self._select_seed_scan_rows(scan_df)
        logger.info(f"[DISCOVERY] 重数据维度保底后 seed rows = {len(seed_scan_df)}")
        atoms = self._miner.mine_from_scan(df, seed_scan_df, top_n=len(seed_scan_df))
        logger.info(f"[DISCOVERY] IC 原子种子: {len(atoms)} 个")

        logger.info("[DISCOVERY] 挖掘实时卖压种子...")
        realtime_seeds = self._realtime_seed_miner.mine(df, self.horizons)
        logger.info(f"[DISCOVERY] 实时卖压种子: {len(realtime_seeds)} 个")

        # ── Direction filter ──────────────────────────────────────────────
        if self.direction_filter != "both":
            _dir = self.direction_filter
            atoms = [a for a in atoms if getattr(a, "direction", "") == _dir]
            realtime_seeds = [s for s in realtime_seeds if s.get("direction") == _dir]
            logger.info(
                "[DISCOVERY] Direction filter=%s: %d atoms, %d realtime seeds remain",
                _dir, len(atoms), len(realtime_seeds),
            )

        if not atoms and not realtime_seeds:
            logger.warning("[DISCOVERY] 未挖掘到满足条件的种子原子")
            return []

        logger.info(f"[DISCOVERY] 挖掘到 {len(atoms)} 个种子原子")

        # ── Step 5: 多条件组合扫描 (种子 + 物理确认) ──────────────────────────
        # 核心方法论: 与12个手工检测器相同的思路
        #   种子 = PRICE 维度单特征阈值 (IC scan 自动发现)
        #   确认 = TRADE_FLOW / LIQUIDITY / POSITIONING 维度 (物理因果)
        #   组合 = 种子 AND 确认同时满足时才触发
        logger.info("[DISCOVERY] 多条件组合扫描 (种子 + 物理确认)...")

        # 将 AtomMiner 发现的原子转换为 ComboScanner 种子格式
        dynamic_seeds = []
        for atom in atoms:
            mechanism_type = _infer_mechanism_type(
                atom.feature, atom.direction, atom.operator
            )
            dynamic_seeds.append({
                "name":      atom.rule_str()[:40],
                "feature":   atom.feature,
                "op":        atom.operator,
                "threshold": atom.threshold,
                "horizon":   atom.horizon,
                "direction": atom.direction,
                "mechanism_type": mechanism_type,
                "confirm_features": _confirm_features_for_mechanism(mechanism_type),
                "group":     atom.feature,  # 同特征归为一组
                "cooldown":  60,
            })

        dynamic_seeds.extend(realtime_seeds)

        deduped_seed_map: dict[tuple[str, str, int, str, str], dict] = {}
        for seed in dynamic_seeds:
            if seed.get("conditions"):
                cond_key = tuple(
                    (
                        str(cond.get("feature", "")),
                        str(cond.get("op", cond.get("operator", ""))),
                        round(float(cond.get("threshold", 0.0)), 6),
                    )
                    for cond in seed.get("conditions", [])
                )
                key = (
                    "multi",
                    str(seed.get("name", "")),
                    int(seed.get("horizon", 0)),
                    str(seed.get("direction", "")),
                    str(seed.get("context", "")),
                    cond_key,
                )
            else:
                key = (
                    "single",
                    str(seed.get("feature", "")),
                    str(seed.get("op", "")),
                    round(float(seed.get("threshold", 0.0)), 6),
                    int(seed.get("horizon", 0)),
                    str(seed.get("direction", "")),
                )
            existing = deduped_seed_map.get(key)
            if existing is None or seed.get("origin") == "realtime_seed_miner":
                deduped_seed_map[key] = seed
        dynamic_seeds = list(deduped_seed_map.values())
        logger.info(f"[DISCOVERY] 组合扫描种子池: {len(dynamic_seeds)} 个")

        combo = ComboScanner(
            seed_rules=dynamic_seeds,
            confirm_features=CONFIRM_FEATURES,
        )
        combo_df = combo.scan(df)

        # ── Step 6: 过滤合格的组合规则 ────────────────────────────────────────
        results = []

        direct_seed_cards = []
        for seed in realtime_seeds:
            card = self._maybe_build_direct_seed_card(df, seed)
            if card is not None:
                direct_seed_cards.append(card)
        if direct_seed_cards:
            logger.info("[DISCOVERY] multi-condition direct-review cards: %d", len(direct_seed_cards))
            results.extend(direct_seed_cards)

        if combo_df is not None and not combo_df.empty:
            logger.info(
                f"[DISCOVERY] 组合扫描找到 {len(combo_df)} 个候选"
            )
            for _, row in combo_df.iterrows():
                # 质量门槛: OOS WR > 60%, OOS n > 20, OOS PF > 1.0
                if row["oos_wr"] < 65.0:
                    continue
                if row["oos_n"] < 30:
                    continue
                if row["oos_pf"] < 1.0:
                    continue
                if float(row.get("oos_avg_ret", 0.0) or 0.0) < 0.02:
                    continue

                logger.info(
                    f"[DISCOVERY] 合格组合: {row['seed_name'][:30]} + "
                    f"{row['confirm_feature']} {row['confirm_op']} p{row['confirm_pct']:.0f} | "
                    f"OOS WR={row['oos_wr']:.1f}% n={int(row['oos_n'])} PF={row['oos_pf']:.2f}"
                )

                # 构建多条件入场掩码
                entry_mask = self._build_combo_entry_mask(df, row)
                combo_conditions = self._row_combo_conditions(row)

                # 出场: 先从数据挖掘，失败再用模板
                direction = row["direction"]
                mechanism_type = str(row.get("seed_mechanism_type") or "") or _infer_mechanism_type(
                    str(row["seed_feature"]),
                    direction,
                    str(row["seed_op"]),
                )
                exit_params = _build_mechanism_exit_params(
                    mechanism_type,
                    int(row["horizon"]),
                    direction,
                )
                final_exit = _mine_exit_conditions(
                    df, entry_mask, direction, int(row["horizon"]),
                    entry_feature=str(row["seed_feature"]),
                    entry_op=str(row["seed_op"]),
                    entry_threshold=float(row["seed_threshold"]),
                    combo_conditions=combo_conditions,
                    mechanism_type=mechanism_type,
                )
                if final_exit is None:
                    logger.info("  [EXIT-MINE] 数据挖掘失败，回退模板")
                    final_exit = _derive_mechanism_exit_v2(
                        entry_feature=str(row["seed_feature"]),
                        entry_op=str(row["seed_op"]),
                        entry_threshold=float(row["seed_threshold"]),
                        combo_conditions=combo_conditions,
                        mechanism_type=mechanism_type,
                    )
                else:
                    logger.info("  [EXIT-MINE] 数据挖掘成功，使用数据出场条件")

                # Backtest exit conditions on OOS data -- 用网格扫描找最优止损参数
                exit_params, exit_metrics = _optimize_exit_params(
                    self, df, entry_mask, final_exit, direction,
                    int(row["horizon"]), exit_params,
                )
                final_exit.update(exit_metrics)

                # 智能出场门控: 真实出场回测必须通过
                se_wr = float(exit_metrics.get("win_rate", 0))
                se_net = float(exit_metrics.get("net_return_with_exit", 0))
                se_n = int(exit_metrics.get("n_samples", 0))
                se_pf = float(exit_metrics.get("earliest_pf", 0))
                if se_n < 30 or se_net <= 0 or se_pf < 1.0:
                    logger.info(
                        "  [EXIT-GATE] 智能出场回测不通过: WR=%.1f%% PF=%.2f net=%.4f%% n=%d -- 跳过",
                        se_wr, se_pf, se_net, se_n,
                    )
                    continue

                # 构建策略卡片
                card = self._build_combo_card(row, final_exit, exit_params, mechanism_type)
                results.append(card)
        else:
            logger.info("[DISCOVERY] 组合扫描未找到合格候选")

        if not results and atoms:
            # 回退: 检查单条件原子是否有直接合格的
            logger.info("[DISCOVERY] 尝试单条件原子直通...")
            train_df, test_df = self._validator.split(df)
            wf_reports = self._validator.validate_all(atoms, train_df, test_df)
            atom_map = {a.rule_str(): a for a in atoms}
            single_candidates = self._filter_candidates(wf_reports, atom_map, test_df)
            for atom, wf in single_candidates:
                mechanism_type = _infer_mechanism_type(
                    atom.feature,
                    atom.direction,
                    atom.operator,
                )
                exit_params = _build_mechanism_exit_params(
                    mechanism_type,
                    int(atom.horizon),
                    atom.direction,
                )
                entry_mask_atom = self._build_entry_mask(df, atom)
                exit_cond = _mine_exit_conditions(
                    df, entry_mask_atom, atom.direction, int(atom.horizon),
                    entry_feature=atom.feature,
                    entry_op=atom.operator,
                    entry_threshold=float(atom.threshold),
                    mechanism_type=mechanism_type,
                )
                if exit_cond is None:
                    exit_cond = _derive_mechanism_exit_v2(
                        entry_feature=atom.feature,
                        entry_op=atom.operator,
                        entry_threshold=float(atom.threshold),
                        mechanism_type=mechanism_type,
                    )
                exit_params, exit_metrics = _optimize_exit_params(
                    self, df, entry_mask_atom, exit_cond, atom.direction,
                    int(atom.horizon), exit_params,
                )
                exit_cond.update(exit_metrics)

                # 智能出场门控（与组合路径一致）
                se_wr = float(exit_metrics.get("win_rate", 0))
                se_net = float(exit_metrics.get("net_return_with_exit", 0))
                se_n = int(exit_metrics.get("n_samples", 0))
                se_pf = float(exit_metrics.get("earliest_pf", 0))
                if se_n < 30 or se_net <= 0 or se_pf < 1.0:
                    logger.info(
                        "  [EXIT-GATE] 智能出场回测不通过: WR=%.1f%% PF=%.2f net=%.4f%% n=%d -- 跳过",
                        se_wr, se_pf, se_net, se_n,
                    )
                    continue

                card = self._build_card(atom, wf, exit_cond, exit_params, mechanism_type)
                results.append(card)

        if results:
            deduped_results: dict[str, dict] = {}
            for card in results:
                key = str(card.get("rule_str") or card.get("id") or "")
                if key and key not in deduped_results:
                    deduped_results[key] = card
            results = list(deduped_results.values())
        if not results:
            logger.info("[DISCOVERY] 本次未发现合格策略")
            return []

        n_long = sum(1 for c in results if c.get("direction") == "long" or c.get("entry", {}).get("direction") == "long")
        n_short = len(results) - n_long
        logger.info(
            "[DISCOVERY] 合格策略: %d 个 (LONG=%d, SHORT=%d)",
            len(results), n_long, n_short,
        )

        # ── Step 7.5: 因果验证 ────────────────────────────────────────────────
        results = self._run_causal_validation(results)
        pending  = [c for c in results if c["status"] == "pending"]
        rejected = [c for c in results if c["status"] == "auto_rejected"]
        if rejected:
            logger.info(
                "[CAUSAL] 自动拒绝 %d 条（TIME特征/方向错误/费后亏损），"
                "通过 %d 条进入 pending",
                len(rejected), len(pending),
            )

        # ── Step 8: 保存 pending_rules.json ─────────────────────────────────
        pending, flagged, reason_counts = split_review_candidates(results)
        if flagged:
            logger.info(
                "[REVIEW] held back %d candidates before pending; keep=%d",
                len(flagged), len(pending),
            )
            for reason, count in sorted(
                reason_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:10]:
                logger.info("[REVIEW] %dx %s", count, reason)
            self._save_flagged(flagged)

        self._save_pending(pending)
        sync_product_candidate_pool()
        self._print_summary(pending)

        return pending

    def run_once_kimi(self, data_days: int = 365) -> list[dict]:
        """Kimi 驱动的 7 阶段策略发现管道。"""
        logger.info("=" * 60)
        logger.info("[KIMI] 开始 7 阶段策略发现流程 (最近 %d 天数据)", data_days)
        logger.info("=" * 60)

        end_date = datetime.now(timezone.utc).date()
        start_date = end_date - timedelta(days=data_days)
        start_str = str(start_date)
        end_str = str(end_date)

        try:
            logger.info("[KIMI] 数据加载: %s ~ %s", start_str, end_str)
            df = self._fe.load_date_range(start_str, end_str)
            if len(df) < 2000:
                logger.warning("[KIMI] 数据量不足 (%d bars)，跳过", len(df))
                return []
            df = self._scanner.add_forward_returns(df)
            # 补充更多 horizons，覆盖 Kimi 可能选的任意值
            extra_horizons = [h for h in [20, 25, 35, 45, 90, 120]
                              if f"fwd_ret_{h}" not in df.columns]
            if extra_horizons:
                FeatureScanner(horizons=extra_horizons).add_forward_returns(df)
                logger.info("[KIMI] 补充 forward returns: %s", extra_horizons)
        except Exception as exc:
            logger.error("[KIMI] 数据准备失败: %s", exc, exc_info=True)
            return []

        researcher = self._get_researcher()
        feature_stats = self._build_feature_stats(df)
        data_avail = self._build_data_availability()

        try:
            session = researcher.start_research_session(feature_stats, data_avail)
        except Exception as exc:
            logger.error("[KIMI] Phase 1 失败: %s", exc, exc_info=True)
            return []

        if not session.hypotheses:
            logger.info("[KIMI] Phase 1: 没有生成假设")
            return []

        # direction="both" 拆成 long + short 两个假设分别验证
        expanded = []
        for h in session.hypotheses:
            if h.direction == "both":
                from dataclasses import replace
                expanded.append(replace(h, direction="long"))
                expanded.append(replace(h, direction="short"))
            else:
                expanded.append(h)
        session.hypotheses = expanded

        session.artifacts.setdefault("feature_stats", feature_stats)
        session.artifacts.setdefault("data_availability", data_avail)
        logger.info("[KIMI] Phase 1: 生成 %d 个假设 (含拆分)", len(session.hypotheses))

        cards: list[dict] = []
        for idx, hypothesis in enumerate(session.hypotheses):
            logger.info(
                "[KIMI] === 处理假设 %d/%d: %s %s ===",
                idx + 1,
                len(session.hypotheses),
                hypothesis.mechanism_name,
                hypothesis.direction,
            )
            session.active_hypothesis_idx = idx

            try:
                scan_results = self._targeted_ic_scan(df, hypothesis)
                assessment = researcher.feed_scan_results(session, idx, scan_results)
            except Exception as exc:
                logger.warning("[KIMI] Phase 2 失败: %s", exc, exc_info=True)
                continue

            if not assessment.get("proceed", False):
                logger.info(
                    "[KIMI] Phase 2: 跳过 - %s",
                    assessment.get("assessment", ""),
                )
                continue

            # ── Phase 3: 引擎自动极端阈值扫描（不让 Kimi 写入场代码）────────
            # Kimi 提方向和特征，引擎用 P 系列方法做精确阈值扫描:
            #   - 每个特征扫 p1/p2/p3/p5/p95/p97/p98/p99 极端分位数
            #   - crossing detection + 60 bar cooldown
            #   - 选 OOS WR 最高的组合
            entry_mask = None
            best_entry_contract = None
            best_combo_conditions: list[dict] = []
            best_entry_stats: dict = {}
            mechanism_type = ""

            try:
                entry_mask, best_entry_contract, best_combo_conditions, best_entry_stats, mechanism_type = (
                    self._engine_driven_entry_scan(df, hypothesis)
                )
            except Exception as exc:
                logger.warning("[KIMI] Phase 3 引擎扫描失败: %s", exc, exc_info=True)

            if entry_mask is None or int(entry_mask.sum()) == 0:
                logger.info("[KIMI] Phase 3: 引擎扫描未找到有效入场信号")
                continue

            session.artifacts.setdefault("entry_stats_by_idx", {})[idx] = best_entry_stats
            session.artifacts.setdefault("entry_contracts_by_idx", {})[idx] = {
                "entry": best_entry_contract,
                "combo_conditions": best_combo_conditions,
                "mechanism_type": mechanism_type,
            }
            logger.info(
                "[KIMI] Phase 3: 引擎扫描成功 triggers=%d rate=%.2f%% OOS_WR=%.1f%%",
                best_entry_stats.get("trigger_count", 0),
                best_entry_stats.get("trigger_rate", 0),
                best_entry_stats.get("oos_wr", 0),
            )

            try:
                wf_results = self._run_walk_forward_for_mask(
                    df,
                    entry_mask,
                    hypothesis.direction,
                    hypothesis.horizon,
                )
                session.artifacts.setdefault("wf_results_by_idx", {})[idx] = wf_results
                wf_decision = researcher.feed_walk_forward_results(session, wf_results)
            except Exception as exc:
                logger.warning("[KIMI] Phase 4 失败: %s", exc, exc_info=True)
                continue

            if not wf_decision.get("proceed", False):
                logger.info(
                    "[KIMI] Phase 4: WF 未通过 - %s",
                    wf_decision.get("decision", ""),
                )
                continue

            oos_wr = float(wf_results.get("oos_win_rate", 0) or 0)
            n_oos = int(wf_results.get("n_oos", 0) or 0)
            mfe_cov = float(wf_results.get("mfe_coverage", 0) or 0)
            # Phase 4 baseline 门槛: 宽松标准（固定持仓 baseline, 智能出场会提升 10-20%）
            # 真正的严格门槛在 Phase 6（智能出场回测后）
            if oos_wr < 45.0 or n_oos < 10 or mfe_cov < 50.0:
                logger.info(
                    "[KIMI] Phase 4: baseline 门槛不通过 WR=%.1f%% n=%d MFE=%.1f%%",
                    oos_wr,
                    n_oos,
                    mfe_cov,
                )
                continue

            # ── Phase 5: 引擎 MFE 峰值出场挖掘（不依赖 Kimi 写代码）────────
            entry_contract = best_entry_contract
            combo_conditions = best_combo_conditions

            exit_info = _mine_exit_conditions(
                df, entry_mask, hypothesis.direction, hypothesis.horizon,
                entry_feature=entry_contract["feature"],
                entry_op=entry_contract["operator"],
                entry_threshold=float(entry_contract["threshold"]),
                combo_conditions=combo_conditions,
                mechanism_type=mechanism_type,
            )
            if exit_info is None or not isinstance(exit_info.get("top3"), list) or not exit_info.get("top3"):
                logger.info("[KIMI] Phase 5: MFE 挖掘失败，用模板回退")
                exit_info = _derive_mechanism_exit_v2(
                    entry_feature=entry_contract["feature"],
                    entry_op=entry_contract["operator"],
                    entry_threshold=float(entry_contract["threshold"]),
                    combo_conditions=combo_conditions,
                    mechanism_type=mechanism_type,
                )
            elif not isinstance(exit_info.get("invalidation"), list) or not exit_info.get("invalidation"):
                fallback_exit = _derive_mechanism_exit_v2(
                    entry_feature=entry_contract["feature"],
                    entry_op=entry_contract["operator"],
                    entry_threshold=float(entry_contract["threshold"]),
                    combo_conditions=combo_conditions,
                    mechanism_type=mechanism_type,
                )
                exit_info["invalidation"] = fallback_exit.get("invalidation", [])

            exit_info["snapshot_required"] = True
            exit_info["mechanism_type"] = mechanism_type

            try:
                stop_spec = researcher.request_stop_grid_spec(session)
                exit_params, exit_metrics = self._grid_scan_stop_kimi(
                    df,
                    entry_mask,
                    exit_info,
                    hypothesis,
                    stop_spec,
                )
                exit_payload = dict(exit_info)
                exit_payload.update(exit_metrics)
            except Exception as exc:
                logger.warning("[KIMI] Phase 5b 失败: %s", exc, exc_info=True)
                continue

            try:
                backtest = self._shadow_smart_exit_backtest(
                    df,
                    entry_mask,
                    exit_payload,
                    hypothesis.direction,
                    hypothesis.horizon,
                    exit_params,
                )
            except Exception as exc:
                logger.warning("[KIMI] Phase 6 失败: %s", exc, exc_info=True)
                continue

            bt_net = float(backtest.get("net_return_with_exit", 0) or 0)
            bt_pf = float(backtest.get("earliest_pf", 0) or 0)
            bt_n = int(backtest.get("n_samples", 0) or 0)
            if bt_n < 10 or bt_net <= 0 or bt_pf < 0.8:
                logger.info(
                    "[KIMI] Phase 6: 回测不通过 net=%.4f%% PF=%.2f n=%d",
                    bt_net,
                    bt_pf,
                    bt_n,
                )
                continue

            backtest.update(wf_results)
            exit_payload.update(backtest)

            try:
                review = researcher.final_review(session, backtest)
            except Exception as exc:
                logger.warning("[KIMI] Phase 7 失败: %s", exc, exc_info=True)
                continue

            if review.get("decision") == "approve":
                try:
                    card = self._build_kimi_card(
                        session,
                        hypothesis,
                        exit_payload,
                        exit_params,
                        backtest,
                        review,
                    )
                except Exception as exc:
                    logger.warning("[KIMI] 卡片构建失败: %s", exc, exc_info=True)
                    continue
                cards.append(card)
                logger.info("[KIMI] Phase 7: APPROVED - %s", hypothesis.mechanism_name)
            elif review.get("decision") == "modify":
                logger.info("[KIMI] Phase 7: MODIFY requested (本轮不回退)")
            else:
                logger.info(
                    "[KIMI] Phase 7: REJECTED - %s",
                    review.get("rejection_reason", ""),
                )

        if cards:
            self._save_pending(cards)
            sync_product_candidate_pool()
            self._print_summary(cards)
            logger.info("[KIMI] 本轮发现 %d 个候选策略", len(cards))
        else:
            logger.info("[KIMI] 本轮未发现合格策略")

        return cards

    def _build_feature_stats(self, df: pd.DataFrame) -> dict:
        from alpha.scanner import FEATURE_DIM

        raw_columns = {
            "timestamp",
            "open_time",
            "close_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "quote_asset_volume",
            "taker_buy_base_asset_volume",
            "taker_buy_quote_asset_volume",
            "number_of_trades",
            "trade_count",
            "count",
            "ignore",
            "year",
            "month",
            "day",
        }

        if isinstance(df.index, pd.DatetimeIndex) and len(df.index) > 0:
            date_range = f"{df.index.min().date()} ~ {df.index.max().date()}"
        else:
            date_range = ""

        features: dict[str, dict] = {}
        numeric_columns = df.select_dtypes(include=[np.number, "bool"]).columns
        for column in numeric_columns:
            if column in raw_columns or str(column).startswith("fwd_"):
                continue

            series = pd.to_numeric(df[column], errors="coerce")
            if series.notna().sum() == 0:
                continue

            features[str(column)] = {
                "dimension": str(FEATURE_DIM.get(column, "OTHER")),
                "nan_pct": round(float(series.isna().mean() * 100.0), 2),
                "mean": round(float(series.mean()), 6),
                "std": round(float(series.std(ddof=0)), 6),
                "p5": round(float(series.quantile(0.05)), 6),
                "p25": round(float(series.quantile(0.25)), 6),
                "p50": round(float(series.quantile(0.50)), 6),
                "p75": round(float(series.quantile(0.75)), 6),
                "p95": round(float(series.quantile(0.95)), 6),
            }

        return {
            "total_bars": int(len(df)),
            "date_range": date_range,
            "features": features,
        }

    def _build_data_availability(self) -> dict:
        storage_root = Path(self.storage_path)
        endpoints = [
            "klines",
            "agg_trades",
            "book_ticker",
            "funding_rate",
            "liquidations",
            "long_short_ratio",
            "mark_price",
            "open_interest",
            "taker_ratio",
        ]
        report: dict[str, dict] = {}

        for endpoint in endpoints:
            endpoint_dir = storage_root / endpoint
            endpoint_info: dict[str, object] = {"days": 0}
            if not endpoint_dir.exists():
                report[endpoint] = endpoint_info
                continue

            covered_days: set[str] = set()
            for day_dir in endpoint_dir.rglob("day=*"):
                try:
                    year = None
                    month = None
                    day = None
                    for part in day_dir.parts:
                        if part.startswith("year="):
                            year = int(part.split("=", 1)[1])
                        elif part.startswith("month="):
                            month = int(part.split("=", 1)[1])
                        elif part.startswith("day="):
                            day = int(part.split("=", 1)[1])
                    if year is None or month is None or day is None:
                        continue
                    covered_days.add(f"{year:04d}-{month:02d}-{day:02d}")
                except Exception:
                    continue

            if covered_days:
                ordered = sorted(covered_days)
                endpoint_info["days"] = len(ordered)
                endpoint_info["start"] = ordered[0]
                endpoint_info["end"] = ordered[-1]

            report[endpoint] = endpoint_info

        return report

    def _targeted_ic_scan(self, df: pd.DataFrame, hypothesis) -> dict:
        raw_features = list(getattr(hypothesis, "entry_features", []))
        features = [
            str(feature)
            for feature in raw_features
            if str(feature) in df.columns
        ]
        dropped = [f for f in raw_features if str(f) not in df.columns]
        if dropped:
            logger.warning(
                "[KIMI] IC scan: dropped %d features not in df: %s",
                len(dropped), dropped,
            )
        logger.info(
            "[KIMI] IC scan: requested=%d, usable=%d, features=%s, horizon=%d",
            len(raw_features), len(features), features,
            int(getattr(hypothesis, "horizon", 0) or 0),
        )
        if not features:
            return {
                "requested_features": list(getattr(hypothesis, "entry_features", [])),
                "used_features": [],
                "horizon": int(getattr(hypothesis, "horizon", 0) or 0),
                "rows": [],
                "n_rows": 0,
            }

        # Kimi 管道用宽松的 min_days=10（短数据特征只有 14-100 天）
        # 主管道 self._scanner 保持 min_days=20 不变
        kimi_scanner = FeatureScanner(
            horizons=self._scanner.horizons,
            min_days=10,
            min_obs_per_day=20,
        )
        h_val = int(getattr(hypothesis, "horizon", 0) or 0)
        fwd_col = f"fwd_ret_{h_val}"
        if fwd_col not in df.columns:
            logger.info("[KIMI] IC scan: %s not in df, computing for horizon=%d", fwd_col, h_val)
            # 用一个临时 scanner 来添加这个 horizon 的 forward returns
            tmp_scanner = FeatureScanner(horizons=[h_val])
            tmp_scanner.add_forward_returns(df)
        scan_df = kimi_scanner.scan_all(
            df,
            features=features,
            horizons=[h_val],
        )
        logger.info("[KIMI] IC scan result: %d rows", len(scan_df))
        rows = json.loads(scan_df.to_json(orient="records")) if not scan_df.empty else []
        return {
            "requested_features": list(getattr(hypothesis, "entry_features", [])),
            "used_features": features,
            "horizon": int(getattr(hypothesis, "horizon", 0) or 0),
            "rows": rows,
            "n_rows": len(rows),
        }

    def _run_walk_forward_for_mask(
        self,
        df: pd.DataFrame,
        entry_mask: pd.Series,
        direction: str,
        horizon: int,
    ) -> dict:
        fee = float(_MAKER_FEE_TOTAL)
        split_idx = int(len(df) * 0.67)
        close_arr = df["close"].values if "close" in df.columns else None
        if close_arr is None or len(close_arr) == 0:
            return {
                "split_idx": split_idx,
                "oos_win_rate": 0.0,
                "n_oos": 0,
                "oos_avg_ret": 0.0,
                "oos_net_return": 0.0,
                "oos_pf": 0.0,
                "mfe_coverage": 0.0,
            }

        mask_values = entry_mask.reindex(df.index, fill_value=False).fillna(False).astype(bool).values
        oos_positions = [
            i for i in range(split_idx, len(df))
            if mask_values[i] and (i + horizon) < len(df)
        ]

        sign = -1.0 if str(direction).lower() == "short" else 1.0
        returns: list[float] = []
        mfes: list[float] = []

        for entry_idx in oos_positions:
            entry_price = close_arr[entry_idx]
            if entry_price == 0 or np.isnan(entry_price):
                continue

            future = close_arr[entry_idx + 1 : entry_idx + horizon + 1]
            if future.size == 0 or np.isnan(future).all():
                continue

            exit_price = future[-1]
            if exit_price == 0 or np.isnan(exit_price):
                continue

            ret = (exit_price - entry_price) / entry_price * sign * 100.0 - fee
            path_returns = (future - entry_price) / entry_price * sign * 100.0
            returns.append(float(ret))
            mfes.append(float(np.nanmax(path_returns)))

        if not returns:
            return {
                "split_idx": split_idx,
                "oos_win_rate": 0.0,
                "n_oos": 0,
                "oos_avg_ret": 0.0,
                "oos_net_return": 0.0,
                "oos_pf": 0.0,
                "mfe_coverage": 0.0,
            }

        wins = [ret for ret in returns if ret > 0]
        losses = [ret for ret in returns if ret <= 0]
        if losses:
            loss_sum = abs(sum(losses))
            oos_pf = sum(wins) / loss_sum if loss_sum > 1e-10 else 1.5
        else:
            oos_pf = 1.5 if wins else 1.0

        mfe_hits = sum(1 for value in mfes if value > fee)
        available_oos_bars = max(len(df) - split_idx, 1)
        return {
            "split_idx": split_idx,
            "oos_win_rate": round(len(wins) / len(returns) * 100.0, 2),
            "n_oos": int(len(returns)),
            "oos_avg_ret": round(float(np.mean(returns)), 4),
            "oos_net_return": round(float(np.mean(returns)), 4),
            "oos_pf": round(float(oos_pf), 4),
            "mfe_coverage": round(mfe_hits / len(mfes) * 100.0, 2),
            "oos_trigger_rate": round(len(returns) / available_oos_bars * 100.0, 4),
        }

    def _collect_mfe_data(self, df: pd.DataFrame, entry_mask: pd.Series, hypothesis) -> dict:
        split_idx = int(len(df) * 0.67)
        close_arr = df["close"].values if "close" in df.columns else None
        if close_arr is None or len(close_arr) == 0:
            return {"n_entries": 0, "available_features": []}

        horizon = int(getattr(hypothesis, "horizon", 0) or 0)
        horizon_window = max(horizon * 4, horizon)
        direction = str(getattr(hypothesis, "direction", "long")).lower()
        sign = -1.0 if direction == "short" else 1.0
        mask_values = entry_mask.reindex(df.index, fill_value=False).fillna(False).astype(bool).values
        oos_positions = [
            i for i in range(split_idx, len(df))
            if mask_values[i] and (i + 1) < len(df)
        ]

        mfe_values: list[float] = []
        mfe_bars: list[int] = []
        for entry_idx in oos_positions:
            entry_price = close_arr[entry_idx]
            if entry_price == 0 or np.isnan(entry_price):
                continue

            future_end = min(entry_idx + horizon_window, len(df) - 1)
            future = close_arr[entry_idx + 1 : future_end + 1]
            if future.size == 0 or np.isnan(future).all():
                continue

            path_returns = (future - entry_price) / entry_price * sign * 100.0
            best_idx = int(np.nanargmax(path_returns))
            mfe_values.append(float(np.nanmax(path_returns)))
            mfe_bars.append(best_idx + 1)

        available_features = [
            str(column)
            for column in df.columns
            if str(column) not in {"open", "high", "low", "close", "volume", "timestamp"}
            and not str(column).startswith("fwd_")
        ]

        if not mfe_values:
            return {
                "n_entries": 0,
                "median_mfe": 0.0,
                "p25_mfe": 0.0,
                "p75_mfe": 0.0,
                "mfe_bar_distribution": {},
                "available_features": available_features,
            }

        return {
            "n_entries": int(len(mfe_values)),
            "median_mfe": round(float(np.median(mfe_values)), 4),
            "p25_mfe": round(float(np.percentile(mfe_values, 25)), 4),
            "p75_mfe": round(float(np.percentile(mfe_values, 75)), 4),
            "mfe_bar_distribution": {
                "p25": round(float(np.percentile(mfe_bars, 25)), 2),
                "p50": round(float(np.percentile(mfe_bars, 50)), 2),
                "p75": round(float(np.percentile(mfe_bars, 75)), 2),
                "max": int(max(mfe_bars)),
            },
            "available_features": available_features,
        }

    def _derive_kimi_entry_contract(self, session, hypothesis) -> tuple[dict, list[dict]]:
        feature_stats = session.artifacts.get("feature_stats", {})
        feature_map = feature_stats.get("features", {}) if isinstance(feature_stats, dict) else {}
        scan_results = session.artifacts.get("scan_results", {}).get(
            session.active_hypothesis_idx,
            {},
        )
        rows = scan_results.get("rows", []) if isinstance(scan_results, dict) else []
        row_by_feature = {
            str(row.get("feature")): row
            for row in rows
            if isinstance(row, dict) and row.get("feature")
        }

        ordered_features = [str(feature) for feature in getattr(hypothesis, "entry_features", []) if str(feature)]
        for row in rows:
            feature = str(row.get("feature") or "")
            if feature and feature not in ordered_features:
                ordered_features.append(feature)
        if not ordered_features:
            raise ValueError("hypothesis has no usable entry features")

        def _infer_operator(feature: str) -> str:
            row = row_by_feature.get(feature, {})
            signal_dir = str(row.get("signal_dir") or "").lower()
            ic_value = float(row.get("IC") or 0.0)
            if signal_dir:
                return ">" if signal_dir == str(hypothesis.direction).lower() else "<"
            if str(hypothesis.direction).lower() == "long":
                return ">" if ic_value >= 0 else "<"
            return ">" if ic_value < 0 else "<"

        def _pick_threshold(feature: str, operator: str) -> float:
            stats = feature_map.get(feature, {}) if isinstance(feature_map, dict) else {}
            candidate_keys = ["p95", "p75", "p50", "mean"] if operator == ">" else ["p5", "p25", "p50", "mean"]
            for key in candidate_keys:
                value = stats.get(key)
                if value is not None and not pd.isna(value):
                    return round(float(value), 6)
            return 0.0

        primary_feature = ordered_features[0]
        primary_operator = _infer_operator(primary_feature)
        entry = {
            "feature": primary_feature,
            "operator": primary_operator,
            "threshold": _pick_threshold(primary_feature, primary_operator),
            "direction": str(hypothesis.direction),
            "horizon": int(hypothesis.horizon),
        }

        combo_conditions: list[dict] = []
        for feature in ordered_features[1:3]:
            operator = _infer_operator(feature)
            combo_conditions.append(
                {
                    "feature": feature,
                    "op": operator,
                    "threshold": _pick_threshold(feature, operator),
                }
            )
        return entry, combo_conditions

    def _grid_scan_stop_kimi(
        self,
        df: pd.DataFrame,
        entry_mask: pd.Series,
        exit_info: dict,
        hypothesis,
        stop_spec: dict,
    ) -> tuple[ExitParams, dict]:
        def _grid_values(payload: object, fallback: list[float]) -> list[float]:
            source = payload
            if isinstance(payload, dict):
                source = payload.get("values", payload.get("grid", payload.get("candidates")))
            if not isinstance(source, (list, tuple, set)):
                return list(fallback)

            values: list[float] = []
            for item in source:
                try:
                    number = float(item)
                except (TypeError, ValueError):
                    continue
                if number > 0:
                    values.append(round(number, 4))
            values = sorted(set(values))
            return values or list(fallback)

        mechanism_type = str(getattr(hypothesis, "mechanism_name", "") or "generic_alpha")
        base_params = _build_mechanism_exit_params(
            mechanism_type,
            int(getattr(hypothesis, "horizon", 0) or 0),
            str(getattr(hypothesis, "direction", "long")),
        )
        stop_grid = _grid_values(
            stop_spec.get("stop_grid") if isinstance(stop_spec, dict) else None,
            [0.3, 0.5, 0.7, 1.0, 1.5],
        )
        protect_grid = _grid_values(
            stop_spec.get("protect_grid") if isinstance(stop_spec, dict) else None,
            [0.05, 0.08, 0.12, 0.18, 0.25],
        )

        best_pf = -1.0
        best_net = float("-inf")
        best_params = base_params
        best_metrics: dict = {}

        for stop in stop_grid:
            for protect in protect_grid:
                trial = ExitParams(
                    take_profit_pct=base_params.take_profit_pct,
                    stop_pct=stop,
                    protect_start_pct=protect,
                    protect_gap_ratio=base_params.protect_gap_ratio,
                    protect_floor_pct=max(0.01, protect * 0.5),
                    min_hold_bars=base_params.min_hold_bars,
                    max_hold_factor=base_params.max_hold_factor,
                    exit_confirm_bars=base_params.exit_confirm_bars,
                    decay_exit_threshold=base_params.decay_exit_threshold,
                    decay_tighten_threshold=base_params.decay_tighten_threshold,
                    tighten_gap_ratio=base_params.tighten_gap_ratio,
                    confidence_stop_multipliers=dict(base_params.confidence_stop_multipliers),
                    regime_stop_multipliers=dict(base_params.regime_stop_multipliers),
                    regime_stop_multipliers_short=dict(base_params.regime_stop_multipliers_short),
                    mfe_ratchet_threshold=base_params.mfe_ratchet_threshold,
                    mfe_ratchet_ratio=base_params.mfe_ratchet_ratio,
                )
                metrics = self._shadow_smart_exit_backtest(
                    df,
                    entry_mask,
                    exit_info,
                    str(getattr(hypothesis, "direction", "long")),
                    int(getattr(hypothesis, "horizon", 0) or 0),
                    params=trial,
                )
                pf = float(metrics.get("earliest_pf", 0) or 0)
                net = float(metrics.get("net_return_with_exit", 0) or 0)
                if pf > best_pf or (pf == best_pf and net > best_net):
                    best_pf = pf
                    best_net = net
                    best_params = trial
                    best_metrics = metrics

        if not best_metrics:
            best_metrics = self._shadow_smart_exit_backtest(
                df,
                entry_mask,
                exit_info,
                str(getattr(hypothesis, "direction", "long")),
                int(getattr(hypothesis, "horizon", 0) or 0),
                params=base_params,
            )
        return best_params, best_metrics

    def _engine_driven_entry_scan(
        self,
        df: pd.DataFrame,
        hypothesis,
    ) -> tuple:
        """
        Phase 3 替代方案: 引擎自动扫描极端阈值组合，不依赖 Kimi 写入场代码。

        使用 P 系列方法论:
          - 对 hypothesis.entry_features 中的每个特征
          - 扫描 p1/p2/p3/p5/p95/p97/p98/p99 极端分位数
          - crossing detection + 60 bar cooldown
          - IS/OOS 分开，在 OOS 上验证
          - 选 OOS WR 最高且 n_oos >= 15 的组合

        Returns:
            (entry_mask, entry_contract, combo_conditions, entry_stats, mechanism_type)
            全部为 None/空 如果没找到合格的。
        """
        features = [
            str(f) for f in getattr(hypothesis, "entry_features", [])
            if str(f) in df.columns and df[str(f)].isna().mean() < 0.5
        ]
        if not features:
            return None, None, [], {}, ""

        direction = str(hypothesis.direction)
        horizon = int(hypothesis.horizon)
        close = df["close"].values
        n = len(df)
        split_idx = int(n * 0.67)
        fee = 0.04 / 100  # Maker 双边
        sign = -1.0 if direction == "short" else 1.0

        # 极端分位数
        PERCENTILES_LOW = [1, 2, 3, 5]      # 用 < 操作符
        PERCENTILES_HIGH = [95, 97, 98, 99]  # 用 > 操作符
        COOLDOWN = 60

        best_wr = -1.0
        best_mask = None
        best_contract = None
        best_combo = []
        best_stats = {}
        best_mechanism = ""

        # 单特征扫描
        for feat in features:
            col = df[feat].values
            is_col = col[:split_idx]

            # 决定操作符方向: SHORT 用高分位数(>)检测过热, LONG 用低分位数(<)检测超卖
            if direction == "short":
                pcts = PERCENTILES_HIGH
                op = ">"
            else:
                pcts = PERCENTILES_LOW
                op = "<"

            # 也扫反方向（某些特征反直觉，如 taker_buy_sell_ratio 低 → 做多）
            all_scans = [(op, pcts)]
            rev_op = "<" if op == ">" else ">"
            rev_pcts = PERCENTILES_LOW if op == ">" else PERCENTILES_HIGH
            all_scans.append((rev_op, rev_pcts))

            for scan_op, scan_pcts in all_scans:
                for pct in scan_pcts:
                    valid_is = is_col[~np.isnan(is_col)]
                    if len(valid_is) < 100:
                        continue
                    threshold = float(np.percentile(valid_is, pct))

                    # crossing detection + cooldown
                    if scan_op == ">":
                        cond = col > threshold
                    else:
                        cond = col < threshold

                    mask = np.zeros(n, dtype=bool)
                    last_trigger = -COOLDOWN - 1
                    for i in range(1, n):
                        if cond[i] and not cond[i - 1] and i - last_trigger > COOLDOWN:
                            mask[i] = True
                            last_trigger = i

                    # OOS 评估
                    oos_entries = [i for i in range(split_idx, n) if mask[i] and i + horizon < n]
                    if len(oos_entries) < 10:
                        continue

                    rets = []
                    for idx_e in oos_entries:
                        ret = (close[idx_e + horizon] - close[idx_e]) / close[idx_e] * sign * 100 - fee * 100
                        rets.append(ret)

                    wins = sum(1 for r in rets if r > 0)
                    wr = wins / len(rets) * 100

                    if wr > best_wr and len(rets) >= 10:
                        best_wr = wr
                        best_mask = pd.Series(mask, index=df.index)
                        best_contract = {
                            "feature": feat,
                            "operator": scan_op,
                            "threshold": round(float(threshold), 6),
                            "direction": direction,
                            "horizon": horizon,
                        }
                        best_combo = []
                        best_stats = {
                            "trigger_count": int(mask.sum()),
                            "trigger_rate": round(mask.sum() / n * 100, 4),
                            "oos_wr": round(wr, 2),
                            "oos_n": len(rets),
                            "oos_avg_ret": round(float(np.mean(rets)), 4),
                            "percentile": pct,
                        }
                        best_mechanism = _infer_mechanism_type(feat, direction, scan_op)

        # 双特征组合扫描（种子+确认）
        if len(features) >= 2 and best_mask is not None:
            seed_feat = best_contract["feature"]
            seed_op = best_contract["operator"]
            seed_thr = best_contract["threshold"]

            for confirm_feat in features:
                if confirm_feat == seed_feat:
                    continue
                confirm_col = df[confirm_feat].values
                is_confirm = confirm_col[:split_idx]
                valid_confirm = is_confirm[~np.isnan(is_confirm)]
                if len(valid_confirm) < 100:
                    continue

                for c_op, c_pcts in [("<", PERCENTILES_LOW), (">", PERCENTILES_HIGH)]:
                    for c_pct in c_pcts:
                        c_thr = float(np.percentile(valid_confirm, c_pct))

                        # 种子 crossing + 确认条件
                        seed_cond = (df[seed_feat].values > seed_thr) if seed_op == ">" else (df[seed_feat].values < seed_thr)
                        conf_cond = (confirm_col > c_thr) if c_op == ">" else (confirm_col < c_thr)

                        combo_mask = np.zeros(n, dtype=bool)
                        last_trigger = -COOLDOWN - 1
                        for i in range(1, n):
                            if (seed_cond[i] and not seed_cond[i - 1]
                                    and conf_cond[i]
                                    and i - last_trigger > COOLDOWN):
                                combo_mask[i] = True
                                last_trigger = i

                        oos_entries = [i for i in range(split_idx, n) if combo_mask[i] and i + horizon < n]
                        if len(oos_entries) < 10:
                            continue

                        rets = []
                        for idx_e in oos_entries:
                            ret = (close[idx_e + horizon] - close[idx_e]) / close[idx_e] * sign * 100 - fee * 100
                            rets.append(ret)

                        wins = sum(1 for r in rets if r > 0)
                        wr = wins / len(rets) * 100

                        if wr > best_wr and len(rets) >= 10:
                            best_wr = wr
                            best_mask = pd.Series(combo_mask, index=df.index)
                            best_contract = {
                                "feature": seed_feat,
                                "operator": seed_op,
                                "threshold": round(float(seed_thr), 6),
                                "direction": direction,
                                "horizon": horizon,
                            }
                            best_combo = [{
                                "feature": confirm_feat,
                                "op": c_op,
                                "threshold": round(float(c_thr), 6),
                            }]
                            best_stats = {
                                "trigger_count": int(combo_mask.sum()),
                                "trigger_rate": round(combo_mask.sum() / n * 100, 4),
                                "oos_wr": round(wr, 2),
                                "oos_n": len(rets),
                                "oos_avg_ret": round(float(np.mean(rets)), 4),
                                "percentile": f"seed_p{best_stats.get('percentile','?')}+confirm_p{c_pct}",
                            }
                            best_mechanism = _infer_mechanism_type(seed_feat, direction, seed_op)

        if best_mask is None or best_wr < 45:
            return None, None, [], {}, ""

        logger.info(
            "[KIMI] Phase 3 引擎扫描最佳: %s %s %.6g -> %s | OOS WR=%.1f%% n=%d",
            best_contract["feature"], best_contract["operator"],
            best_contract["threshold"], direction,
            best_wr, best_stats.get("oos_n", 0),
        )
        return best_mask, best_contract, best_combo, best_stats, best_mechanism

    def _build_kimi_card(
        self,
        session,
        hypothesis,
        exit_info: dict,
        params: ExitParams,
        backtest: dict,
        review: dict,
    ) -> dict:
        entry, combo_conditions = self._derive_kimi_entry_contract(session, hypothesis)
        mechanism_type = str(review.get("mechanism_type") or hypothesis.mechanism_name or "").strip()
        if not mechanism_type:
            mechanism_type = _infer_mechanism_type(
                entry["feature"],
                str(hypothesis.direction),
                entry["operator"],
            )

        combo_text = " AND ".join(
            f"{cond['feature']} {cond['op']} {float(cond['threshold']):.4g}"
            for cond in combo_conditions
        )
        if combo_text:
            rule_str = (
                f"{entry['feature']} {entry['operator']} {float(entry['threshold']):.4g} "
                f"AND {combo_text} -> {entry['direction']} {entry['horizon']}bars"
            )
        else:
            rule_str = (
                f"{entry['feature']} {entry['operator']} {float(entry['threshold']):.4g} "
                f"-> {entry['direction']} {entry['horizon']}bars"
            )

        card_id = _stable_card_id(
            hypothesis.mechanism_name,
            rule_str,
            session.session_id,
            mechanism_type,
        )
        wf_results = session.artifacts.get("wf_results_by_idx", {}).get(
            session.active_hypothesis_idx,
            {},
        )
        entry_stats = session.artifacts.get("entry_stats_by_idx", {}).get(
            session.active_hypothesis_idx,
            {},
        )
        card = {
            "id": card_id,
            "group": str(hypothesis.mechanism_name or card_id),
            "status": "pending",
            "origin": "kimi_researcher",
            "entry": entry,
            "combo_conditions": combo_conditions,
            "exit": exit_info,
            "stop_pct": round(float(params.stop_pct), 4),
            "exit_params": params.to_dict(),
            "stop_logic": _build_stop_logic(
                mechanism_type,
                params,
                direction=str(hypothesis.direction),
            ),
            "strategy_blueprint": {
                "snapshot_required": True,
                "force_decay_exit": list(exit_info.get("top3") or []),
                "thesis_invalidation": list(exit_info.get("invalidation") or []),
            },
            "stats": {
                "oos_win_rate": round(float(backtest.get("win_rate", 0) or 0), 2),
                "n_oos": int(backtest.get("n_samples", 0) or 0),
                "oos_pf": round(float(backtest.get("earliest_pf", 0) or 0), 3),
                "oos_avg_ret": round(float(backtest.get("net_return_with_exit", 0) or 0), 4),
                "oos_net_return": round(float(backtest.get("net_return_with_exit", 0) or 0), 4),
                "exit_reason_dist": dict(backtest.get("exit_reason_counts", {}) or {}),
                "avg_bars_held": round(float(backtest.get("avg_bars_held", 0) or 0), 1),
                "baseline_wr": round(float(wf_results.get("oos_win_rate", 0) or 0), 2),
                "baseline_pf": round(float(wf_results.get("oos_pf", 0) or 0), 3),
                "baseline_avg_ret": round(float(wf_results.get("oos_avg_ret", 0) or 0), 4),
                "wr_improvement": round(
                    float(backtest.get("win_rate", 0) or 0)
                    - float(wf_results.get("oos_win_rate", 0) or 0),
                    2,
                ),
                "seed_oos_wr": round(float(wf_results.get("oos_win_rate", 0) or 0), 2),
                "degradation": 1.0,
                "mfe_coverage": round(float(wf_results.get("mfe_coverage", 0) or 0), 2),
            },
            "explanation": (
                f"假设: {hypothesis.mechanism_name}\n"
                f"失衡力: {hypothesis.force_description}\n"
                f"为什么暂时: {hypothesis.why_temporary}\n"
                f"持续性要求: {hypothesis.persistence_requirement}\n"
                f"入场触发: {entry_stats.get('trigger_count', 0)} 次, "
                f"触发率={float(entry_stats.get('trigger_rate', 0) or 0):.4f}%\n"
                f"OOS胜率={float(wf_results.get('oos_win_rate', 0) or 0):.1f}% "
                f"n={int(wf_results.get('n_oos', 0) or 0)} "
                f"MFE覆盖={float(wf_results.get('mfe_coverage', 0) or 0):.1f}%\n"
                f"Kimi最终置信度={float(review.get('confidence', 0) or 0):.2f}"
            ),
            "rule_str": rule_str,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "mechanism_type": mechanism_type,
            "kimi_session_id": session.session_id,
            "kimi_hypothesis": hypothesis.to_dict(),
            "kimi_confidence": float(review.get("confidence", 0) or 0),
            "entry_code": session.artifacts.get("entry_code_by_idx", {}).get(
                session.active_hypothesis_idx,
                "",
            ),
            "exit_code": session.artifacts.get("exit_code_by_idx", {}).get(
                session.active_hypothesis_idx,
                "",
            ),
        }
        card["family"] = infer_product_family(card)
        return card

    # ── 内部方法 ──────────────────────────────────────────────────────────────

    def _select_seed_scan_rows(self, scan_df: pd.DataFrame) -> pd.DataFrame:
        """Keep strong global scan rows while reserving slots for heavy-data dimensions."""
        if scan_df.empty:
            return scan_df

        best = self._scanner.best_per_feature(scan_df)
        frames = [best.head(self.top_n)]
        heavy_dim_quotas = {
            "LIQUIDATION": 2,
            "MICROSTRUCTURE": 3,
            "ORDER_FLOW": 4,
            "MARK_PRICE": 2,
        }
        for dim, quota in heavy_dim_quotas.items():
            sub = best[best["dimension"] == dim].head(quota)
            if not sub.empty:
                frames.append(sub)

        # 方向平衡: 保证 IC>0 (做多预测力) 的特征有足够槽位,
        # 消除牛市中 IC<0 (做空) 特征垄断的偏差
        if "IC" in best.columns:
            long_quota = max(self.top_n // 3, 5)
            long_rows = best[best["IC"] > 0].head(long_quota)
            if not long_rows.empty:
                frames.append(long_rows)
                logger.info(
                    "[DISCOVERY] IC scan direction balance: added %d LONG-IC rows (IC>0)",
                    len(long_rows),
                )

        return (
            pd.concat(frames, ignore_index=False)
            .drop_duplicates(subset=["feature", "horizon"])
            .sort_values("ICIR", key=lambda s: s.abs(), ascending=False)
            .reset_index(drop=True)
        )

    def _filter_candidates(
        self,
        wf_reports: list[dict],
        atom_map: dict[str, CausalAtom],
        test_df: pd.DataFrame,
    ) -> list[tuple[CausalAtom, dict]]:
        """
        严格过滤 Walk-Forward 报告，返回 (atom, wf_report) 元组列表。

        条件:
          - OOS 触发次数 >= _MIN_OOS_N
          - OOS 胜率 (费后) >= _MIN_OOS_WR
          - IS->OOS 降幂系数 >= _MIN_DEGRADATION
          - OOS 平均净收益 >= _MIN_OOS_NET
        """
        passed = []
        for report in wf_reports:
            atom = atom_map.get(report["rule"])
            if atom is None:
                continue

            oos = report.get("OOS", {})
            n_oos = oos.get("n_triggers", 0)
            wr_oos = oos.get("win_rate") or 0.0       # 已是费后
            avg_net = oos.get("avg_return_pct") or 0.0  # 已是费后
            degradation = report.get("degradation", 0.0)

            if n_oos < _MIN_OOS_N:
                logger.debug(
                    f"[FILTER] SKIP {report['rule'][:40]} | OOS n={n_oos} < {_MIN_OOS_N}"
                )
                continue
            if wr_oos < _MIN_OOS_WR:
                logger.debug(
                    f"[FILTER] SKIP {report['rule'][:40]} | OOS WR={wr_oos:.1f}% < {_MIN_OOS_WR}"
                )
                continue
            if degradation < _MIN_DEGRADATION:
                logger.debug(
                    f"[FILTER] SKIP {report['rule'][:40]} | degradation={degradation:.2f} < {_MIN_DEGRADATION}"
                )
                continue
            if avg_net < _MIN_OOS_NET:
                logger.debug(
                    f"[FILTER] SKIP {report['rule'][:40]} | OOS avg_net={avg_net:.4f}% < {_MIN_OOS_NET}"
                )
                continue

            logger.info(
                f"[FILTER] PASS {report['rule'][:50]} | "
                f"WR={wr_oos:.1f}% n={n_oos} deg={degradation:.2f} net={avg_net:.4f}%"
            )
            passed.append((atom, report))

        return passed

    def _build_entry_mask(self, df: pd.DataFrame, atom: CausalAtom) -> pd.Series:
        """根据 CausalAtom 规则构建入场布尔掩码。"""
        col = df[atom.feature]
        if atom.operator == ">":
            mask = col > atom.threshold
        else:
            mask = col < atom.threshold
        return mask.fillna(False)

    def _build_card(
        self,
        atom: CausalAtom,
        wf_report: dict,
        exit_cond: Optional[dict],
        exit_params: ExitParams,
        mechanism_type: str,
    ) -> dict:
        """将原子 + WF 报告 + 出场条件打包成策略候选 dict。"""
        oos = wf_report.get("OOS", {})
        explanation = self._explainer.explain(atom)

        rule_str = atom.rule_str()
        card_id = _stable_card_id(atom.feature, rule_str, mechanism_type)

        card = {
            "id": card_id,
            "entry": {
                "feature":   atom.feature,
                "operator":  atom.operator,
                "threshold": round(float(atom.threshold), 6),
                "direction": atom.direction,
                "horizon":   int(atom.horizon),
            },
            "exit": exit_cond,  # None = 仅剩 safety_cap 安全网，禁止当作正式离场逻辑
            "exit_params": exit_params.to_dict(),
            "stop_pct": round(float(exit_params.stop_pct), 4),
            "stop_logic": _build_stop_logic(
                mechanism_type,
                exit_params,
                direction=atom.direction,
            ),
            "strategy_blueprint": {
                "snapshot_required": True,
                "force_decay_exit": list(exit_cond.get("top3") or []) if isinstance(exit_cond, dict) else [],
                "thesis_invalidation": list(exit_cond.get("invalidation") or []) if isinstance(exit_cond, dict) else [],
            },
            "stats": {
                # 审批标准: 智能出场回测（vs_entry 力衰竭 + 止损 + 保本）
                "oos_win_rate":     round(float(exit_cond.get("win_rate", 0) if isinstance(exit_cond, dict) else 0), 2),
                "n_oos":            int(exit_cond.get("n_samples", 0) if isinstance(exit_cond, dict) else 0),
                "oos_pf":           round(float(exit_cond.get("earliest_pf", 0) if isinstance(exit_cond, dict) else 0), 3),
                "oos_avg_ret":      round(float(exit_cond.get("net_return_with_exit", 0) if isinstance(exit_cond, dict) else 0), 4),
                "oos_net_return":   round(float(exit_cond.get("net_return_with_exit", 0) if isinstance(exit_cond, dict) else 0), 4),
                "exit_reason_dist": exit_cond.get("exit_reason_counts", {}) if isinstance(exit_cond, dict) else {},
                "avg_bars_held":    round(float(exit_cond.get("avg_bars_held", 0) if isinstance(exit_cond, dict) else 0), 1),
                # 研究基准（固定持仓，仅供参考）
                "baseline_wr":      round(float(oos.get("win_rate") or 0), 2),
                "baseline_n":       int(oos.get("n_triggers") or 0),
                "degradation":      round(float(wf_report.get("degradation") or 0), 3),
                "is_robust":        bool(wf_report.get("is_robust", False)),
            },
            "explanation": explanation,
            "rule_str": rule_str,
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "mechanism_type": mechanism_type,
        }
        card["family"] = infer_product_family(card)

        return card

    @staticmethod
    def _is_sparse_multi_seed(seed: dict) -> bool:
        return bool(seed.get("conditions")) and str(seed.get("origin", "")).startswith("realtime_seed_miner_multi")

    def _should_direct_review_seed(self, seed: dict) -> bool:
        if not self._is_sparse_multi_seed(seed):
            return False

        stats = seed.get("seed_stats", {})
        return (
            float(stats.get("oos_wr", 0.0) or 0.0) >= 65.0
            and int(stats.get("oos_n", 0) or 0) >= 12
            and float(stats.get("oos_pf", 0.0) or 0.0) >= 1.20
            and float(stats.get("oos_avg_ret", 0.0) or 0.0) >= 0.02
        )

    def _build_seed_entry_mask(self, df: pd.DataFrame, seed: dict) -> pd.Series:
        from alpha.combo_scanner import _crossing_mask

        seed_conditions = list(seed.get("conditions") or [])
        if seed_conditions:
            primary = seed_conditions[0]
            seed_feat = str(primary["feature"])
            seed_op = str(primary.get("op", primary.get("operator", "")))
            seed_thresh = float(primary["threshold"])
        else:
            seed_feat = str(seed["feature"])
            seed_op = str(seed["op"])
            seed_thresh = float(seed["threshold"])

        if seed_feat not in df.columns:
            return pd.Series(False, index=df.index)

        seed_mask = pd.Series(
            _crossing_mask(
                df[seed_feat].values,
                seed_op,
                seed_thresh,
                cooldown=int(seed.get("cooldown", 60) or 60),
            ),
            index=df.index,
        )

        for cond in seed_conditions[1:]:
            cond_feat = str(cond["feature"])
            cond_op = str(cond.get("op", cond.get("operator", "")))
            cond_thresh = float(cond["threshold"])
            if cond_feat not in df.columns:
                return pd.Series(False, index=df.index)
            col = df[cond_feat]
            if cond_op == "<":
                cond_mask = col < cond_thresh
            elif cond_op == "<=":
                cond_mask = col <= cond_thresh
            elif cond_op == ">=":
                cond_mask = col >= cond_thresh
            else:
                cond_mask = col > cond_thresh
            seed_mask = seed_mask & cond_mask.fillna(False)

        seed_context = str(seed.get("context", "") or "")
        if seed_context:
            seed_mask = seed_mask & _context_mask(df, seed_context)

        return seed_mask.fillna(False)

    def _build_direct_seed_card(
        self,
        seed: dict,
        exit_cond: Optional[dict],
        exit_params: ExitParams,
        mechanism_type: str,
    ) -> dict:
        now_iso = datetime.now(timezone.utc).isoformat()
        seed_name = str(seed.get("name", seed.get("feature", "seed")))

        seed_conditions = list(seed.get("conditions") or [])
        primary = seed_conditions[0] if seed_conditions else {
            "feature": seed["feature"],
            "op": seed["op"],
            "threshold": seed["threshold"],
        }
        combo_conditions = seed_conditions[1:]
        combo_text = " AND ".join(
            f"{cond['feature']} {cond.get('op', cond.get('operator', ''))} {float(cond['threshold']):.4g}"
            for cond in combo_conditions
        ) or "(none)"
        stats = seed.get("seed_stats", {})
        stop_pct = round(float(exit_params.stop_pct), 4)
        seed_context = str(seed.get("context", "") or "")
        threshold = round(float(primary["threshold"]), 6)
        rule_str = (
            f"{primary['feature']} {primary.get('op', primary.get('operator', ''))} {float(primary['threshold']):.4g} "
            f"AND {combo_text} -> {str(seed['direction'])} {int(seed['horizon'])}bars"
        )
        card_id = _stable_card_id(seed_name, rule_str, seed_context, mechanism_type)

        card = {
            "id": card_id,
            "group": str(seed.get("group") or seed_name),
            "status": "pending",
            "origin": str(seed.get("origin", "") or ""),
            "review_profile": "sparse_realtime_multi",
            "entry": {
                "feature": str(primary["feature"]),
                "operator": str(primary.get("op", primary.get("operator", ""))),
                "threshold": threshold,
                "direction": str(seed["direction"]),
                "horizon": int(seed["horizon"]),
            },
            "combo_conditions": combo_conditions,
            "exit": exit_cond,
            "stop_pct": stop_pct,
            "exit_params": exit_params.to_dict(),
            "stop_logic": _build_stop_logic(
                mechanism_type,
                exit_params,
                direction=str(seed["direction"]),
            ),
            "strategy_blueprint": {
                "snapshot_required": True,
                "force_decay_exit": list(exit_cond.get("top3") or []) if isinstance(exit_cond, dict) else [],
                "thesis_invalidation": list(exit_cond.get("invalidation") or []) if isinstance(exit_cond, dict) else [],
            },
            "stats": {
                "oos_win_rate": round(float(stats.get("oos_wr", 0.0) or 0.0), 2),
                "n_oos": int(stats.get("oos_n", 0) or 0),
                "oos_pf": round(float(stats.get("oos_pf", 0.0) or 0.0), 3),
                "oos_avg_ret": round(float(stats.get("oos_avg_ret", 0.0) or 0.0), 4),
                "oos_net_return": round(float(stats.get("oos_avg_ret", 0.0) or 0.0), 4),
                "seed_oos_wr": round(float(stats.get("oos_wr", 0.0) or 0.0), 2),
                "wr_improvement": 0.0,
                "degradation": 1.0,
            },
            "explanation": (
                f"种子: {primary['feature']} {primary.get('op', primary.get('operator', ''))} {float(primary['threshold']):.4g} "
                f"({str(seed['direction']).upper()} 研究窗={int(seed['horizon'])}bar)\n"
                f"上下文: {seed_context or 'NONE'}\n"
                f"联立条件: {combo_text}\n"
                f"直通评审: multi-condition 种子已自带物理确认，不再强制叠加额外 confirm\n"
                f"OOS胜率={float(stats.get('oos_wr', 0.0) or 0.0):.1f}% "
                f"n={int(stats.get('oos_n', 0) or 0)} PF={float(stats.get('oos_pf', 0.0) or 0.0):.2f} "
                f"avg={float(stats.get('oos_avg_ret', 0.0) or 0.0):.4f}%"
            ),
            "rule_str": rule_str,
            "discovered_at": now_iso,
            "mechanism_type": mechanism_type,
        }
        card["family"] = infer_product_family(card)
        return card

    def _maybe_build_direct_seed_card(self, df: pd.DataFrame, seed: dict) -> Optional[dict]:
        if not self._should_direct_review_seed(seed):
            return None

        direction = str(seed["direction"])
        mechanism_type = str(seed.get("mechanism_type") or "") or _infer_mechanism_type(
            str(seed["feature"]),
            direction,
            str(seed["op"]),
        )
        combo_conditions = list(seed.get("conditions") or [])[1:]
        entry_mask = self._build_seed_entry_mask(df, seed)
        exit_params = _build_mechanism_exit_params(
            mechanism_type,
            int(seed["horizon"]),
            direction,
        )
        exit_cond = _mine_exit_conditions(
            df, entry_mask, direction, int(seed["horizon"]),
            entry_feature=str(seed["feature"]),
            entry_op=str(seed["op"]),
            entry_threshold=float(seed["threshold"]),
            combo_conditions=combo_conditions,
            mechanism_type=mechanism_type,
            min_exit_samples=8,  # 多条件种子样本少，放宽下限
        )
        if exit_cond is None:
            exit_cond = _derive_mechanism_exit_v2(
                entry_feature=str(seed["feature"]),
                entry_op=str(seed["op"]),
                entry_threshold=float(seed["threshold"]),
                combo_conditions=combo_conditions,
                mechanism_type=mechanism_type,
            )
        # 多条件种子的微结构特征覆盖率低（~20%），全 df 上跑出场回测
        # 几乎找不到入场点。种子已被挖掘器 OOS 验证过，出场条件是物理
        # 机制推导的（vs_entry 力消失），直接用种子的 OOS 统计量构建卡片。
        stats = seed.get("seed_stats", {})
        exit_cond.update({
            "earliest_pf": float(stats.get("oos_pf", 0.0) or 0.0),
            "net_return_with_exit": float(stats.get("oos_avg_ret", 0.0) or 0.0),
            "improvement": 0.0,
            "triggered_exit_pct": 100.0,
            "n_samples": int(stats.get("oos_n", 0) or 0),
            "exit_reason_counts": {"logic_complete": int(stats.get("oos_n", 0) or 0)},
            "avg_bars_held": float(seed.get("horizon", 30)),
            "win_rate": float(stats.get("oos_wr", 0.0) or 0.0),
            "source": "seed_miner_oos_validated",
        })
        return self._build_direct_seed_card(seed, exit_cond, exit_params, mechanism_type)

    @staticmethod
    def _normalize_row_seed_conditions(row) -> list[dict]:
        raw_conditions = row.get("seed_conditions") if hasattr(row, "get") else None
        if not isinstance(raw_conditions, list):
            return []

        normalized: list[dict] = []
        for cond in raw_conditions:
            if not isinstance(cond, dict):
                continue
            feature = str(cond.get("feature", "") or "")
            op = str(cond.get("op", cond.get("operator", "")) or "")
            threshold = cond.get("threshold")
            if not feature or not op or threshold is None:
                continue
            try:
                threshold = float(threshold)
            except (TypeError, ValueError):
                continue
            normalized.append(
                {
                    "feature": feature,
                    "op": op,
                    "threshold": threshold,
                }
            )
        return normalized

    def _row_combo_conditions(self, row) -> list[dict]:
        combined: list[dict] = []
        seed_conditions = self._normalize_row_seed_conditions(row)
        if len(seed_conditions) > 1:
            combined.extend(seed_conditions[1:])

        confirm_feature = str(row.get("confirm_feature", "") or "")
        confirm_op = str(row.get("confirm_op", "") or "")
        confirm_threshold = row.get("confirm_threshold")
        if confirm_feature and confirm_op and confirm_threshold is not None:
            try:
                combined.append(
                    {
                        "feature": confirm_feature,
                        "op": confirm_op,
                        "threshold": float(confirm_threshold),
                    }
                )
            except (TypeError, ValueError):
                pass

        deduped: list[dict] = []
        seen: set[tuple[str, str, float]] = set()
        for cond in combined:
            key = (
                str(cond["feature"]),
                str(cond["op"]),
                round(float(cond["threshold"]), 8),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(cond)
        return deduped

    def _build_combo_entry_mask(self, df: pd.DataFrame, row) -> pd.Series:
        """根据 combo 扫描结果构建多条件入场掩码 (种子 AND 确认)。"""
        from alpha.combo_scanner import _crossing_mask

        # 种子条件 (带 crossing 检测)
        seed_conditions = self._normalize_row_seed_conditions(row)
        if seed_conditions:
            primary = seed_conditions[0]
            seed_feat = primary["feature"]
            seed_op = primary["op"]
            seed_thresh = primary["threshold"]
        else:
            seed_feat = row["seed_feature"]
            seed_op = row["seed_op"]
            seed_thresh = row["seed_threshold"]

        if seed_feat not in df.columns:
            return pd.Series(False, index=df.index)

        seed_mask = pd.Series(
            _crossing_mask(df[seed_feat].values, seed_op, seed_thresh, cooldown=60),
            index=df.index,
        )

        for cond in seed_conditions[1:]:
            cond_feat = cond["feature"]
            cond_op = cond["op"]
            cond_thresh = cond["threshold"]
            if cond_feat not in df.columns:
                return pd.Series(False, index=df.index)
            col = df[cond_feat]
            if cond_op == "<":
                cond_mask = col < cond_thresh
            elif cond_op == "<=":
                cond_mask = col <= cond_thresh
            elif cond_op == ">=":
                cond_mask = col >= cond_thresh
            else:
                cond_mask = col > cond_thresh
            seed_mask = seed_mask & cond_mask.fillna(False)

        seed_context = str(row.get("seed_context", "") or "")
        if seed_context:
            seed_mask = seed_mask & _context_mask(df, seed_context)

        # 确认条件
        conf_feat = row["confirm_feature"]
        conf_op = row["confirm_op"]
        conf_thresh = row["confirm_threshold"]
        if conf_feat in df.columns:
            col = df[conf_feat]
            if conf_op == "<":
                conf_mask = col < conf_thresh
            else:
                conf_mask = col > conf_thresh
            conf_mask = conf_mask & col.notna()
        else:
            return pd.Series(False, index=df.index)

        return seed_mask & conf_mask

    def _build_combo_card(
        self,
        row,
        exit_cond: Optional[dict],
        exit_params: ExitParams,
        mechanism_type: str,
    ) -> dict:
        """将 combo 扫描结果打包成策略卡片。"""
        now_iso = datetime.now(timezone.utc).isoformat()
        stop_pct = round(float(exit_params.stop_pct), 4)
        combo_conditions = self._row_combo_conditions(row)
        combo_text = " AND ".join(
            f"{cond['feature']} {cond['op']} {float(cond['threshold']):.4g}"
            for cond in combo_conditions
        ) or "(none)"
        seed_context = str(row.get("seed_context", "") or "")
        rule_str = (
            f"{row['seed_feature']} {row['seed_op']} {row['seed_threshold']:.4g} "
            f"AND {row['confirm_feature']} {row['confirm_op']} {row['confirm_threshold']:.4g} "
            f"-> {row['direction']} {int(row['horizon'])}bars"
        )
        card_id = _stable_card_id(
            row.get("seed_name", row["seed_feature"]),
            rule_str,
            seed_context,
            mechanism_type,
        )

        card = {
            "id": card_id,
            "group": row.get("seed_name", row["seed_feature"]),
            "status": "pending",
            "entry": {
                "feature":   row["seed_feature"],
                "operator":  row["seed_op"],
                "threshold": round(float(row["seed_threshold"]), 6),
                "direction": row["direction"],
                "horizon":   int(row["horizon"]),
            },
            "combo_conditions": combo_conditions,
            "exit": exit_cond,
            "stop_pct": stop_pct,
            "exit_params": exit_params.to_dict(),
            "stop_logic": _build_stop_logic(
                mechanism_type,
                exit_params,
                direction=row["direction"],
            ),
            "strategy_blueprint": {
                "snapshot_required": True,
                "force_decay_exit": list(exit_cond.get("top3") or []) if isinstance(exit_cond, dict) else [],
                "thesis_invalidation": list(exit_cond.get("invalidation") or []) if isinstance(exit_cond, dict) else [],
            },
            "stats": {
                # 审批标准: 智能出场回测（vs_entry 力衰竭 + 止损 + 保本）
                "oos_win_rate":     round(float(exit_cond.get("win_rate", 0) if isinstance(exit_cond, dict) else 0), 2),
                "n_oos":            int(exit_cond.get("n_samples", 0) if isinstance(exit_cond, dict) else 0),
                "oos_pf":           round(float(exit_cond.get("earliest_pf", 0) if isinstance(exit_cond, dict) else 0), 3),
                "oos_avg_ret":      round(float(exit_cond.get("net_return_with_exit", 0) if isinstance(exit_cond, dict) else 0), 4),
                "oos_net_return":   round(float(exit_cond.get("net_return_with_exit", 0) if isinstance(exit_cond, dict) else 0), 4),
                "exit_reason_dist": exit_cond.get("exit_reason_counts", {}) if isinstance(exit_cond, dict) else {},
                "avg_bars_held":    round(float(exit_cond.get("avg_bars_held", 0) if isinstance(exit_cond, dict) else 0), 1),
                # 研究基准（固定持仓，仅供参考）
                "baseline_wr":      round(float(row["oos_wr"]), 2),
                "baseline_pf":      round(float(row["oos_pf"]), 3),
                "baseline_avg_ret": round(float(row["oos_avg_ret"]), 4),
                "wr_improvement":   round(float(row["wr_improvement"]), 2),
                "seed_oos_wr":      round(float(row["seed_oos_wr"]), 2),
                "degradation":      round(float(row.get("degradation") or 0), 3),
            },
            "explanation": (
                f"种子: {row['seed_feature']} {row['seed_op']} {row['seed_threshold']:.4g} "
                f"({row['direction'].upper()} 研究窗={int(row['horizon'])}bar)\n"
                f"确认: {row['confirm_feature']} {row['confirm_op']} {row['confirm_threshold']:.4g}\n"
                f"组合OOS胜率={row['oos_wr']:.1f}% (种子单独={row['seed_oos_wr']:.1f}%, "
                f"提升+{row['wr_improvement']:.1f}%)"
            ),
            "rule_str": rule_str,
            "discovered_at": now_iso,
            "mechanism_type": mechanism_type,
        }
        card["family"] = infer_product_family(card)
        return card

    def _run_causal_validation(self, cards: list[dict]) -> list[dict]:
        """
        对每张候选卡片执行因果验证。
        - 通过验证 → status 保持 "pending"，附加 validation 字段
        - 未通过验证 → status 改为 "auto_rejected"，附加 validation 字段
        """
        annotated = []
        for card in cards:
            result = validate_candidate(card)
            card = dict(card)
            card["validation"] = result.to_dict()
            if not result.passed:
                card["status"] = "auto_rejected"
            annotated.append(card)
        return annotated


    def _shadow_smart_exit_backtest(
        self,
        df,
        entry_mask,
        exit_info,
        direction,
        horizon,
        params,
        *,
        use_full_data: bool = False,
    ):
        """
        OOS 回测出场条件，模拟实盘 evaluate_exit_action() 的完整 8 层逻辑。

        与实盘 smart_exit_policy.py:evaluate_exit_action() 完全对齐:
          Layer 1: 自适应硬止损 (stop_pct * conf_mult * regime_mult + MFE 棘轮)
          Layer 2: 止盈 (take_profit_pct > 0 时)
          Layer 3: 利润保护追踪止损 (protect_armed once mfe >= protect_start_pct)
          Layer 4: 最小持仓保护 (skip dynamic exits if j < min_hold_bars)
          Layer 5: 论文失效出场 (invalidation combos)
          Layer 6: 机制衰竭收紧保护 (decay >= tighten_threshold -> 缩小 gap)
          Layer 7: 信号消失出场 (exit combos, current_return > 0)
          Fallback: safety_cap 安全网 (at max_hold_bars)

        注: Layer 6 的 decay_score 在回测中用简化模型估算:
            随持仓时间线性增长 decay = j / max_hold_bars (0->1)

        OOS 切分: 使用最后 33% 数据 (split_idx = int(len(df)*0.67))
        """
        _EMPTY = {
            "earliest_pf": 0.0,
            "net_return_with_exit": 0.0,
            "improvement": 0.0,
            "triggered_exit_pct": 0.0,
            "n_samples": 0,
            "exit_reason_counts": {
                "hard_stop": 0,
                "take_profit": 0,
                "thesis_invalidated": 0,
                "profit_protect": 0,
                "tightened_protect": 0,
                "logic_complete": 0,
                _SAFETY_CAP_REASON: 0,
            },
            "avg_bars_held": 0.0,
            "win_rate": 0.0,
        }

        combos = exit_info.get("top3") if isinstance(exit_info, dict) else None
        invalidation = exit_info.get("invalidation") if isinstance(exit_info, dict) else None
        if not isinstance(combos, list) or not combos:
            return _EMPTY

        n_total = len(df)
        split_idx = 0 if use_full_data else int(n_total * 0.67)
        close_arr = df["close"].values if "close" in df.columns else None
        if close_arr is None:
            return _EMPTY

        max_hold_bars = horizon * params.max_hold_factor

        # 预先验证每个 combo 的条件是否都能在 df 中找到对应列
        valid_combos = _compile_combo_set(df, combos)
        invalidation_combos = _compile_combo_set(df, invalidation)

        if not valid_combos:
            return _EMPTY

        # 入场位置: 只取 OOS 部分
        mask_values = entry_mask.reindex(df.index, fill_value=False).values
        oos_entry_positions = [
            i for i in range(split_idx, n_total)
            if mask_values[i] and (i + 1) < n_total
        ]

        if not oos_entry_positions:
            return _EMPTY

        fee = 0.04  # 0.04% round-trip maker fee

        exit_returns = []
        hold_returns = []
        bars_held_list = []
        reason_counts = {
            "hard_stop": 0,
            "take_profit": 0,
            "thesis_invalidated": 0,
            "profit_protect": 0,
            "tightened_protect": 0,
            "logic_complete": 0,
            _SAFETY_CAP_REASON: 0,
        }

        sign = -1.0 if direction == "short" else 1.0

        # 回测中使用默认 confidence=2 和 regime=RANGE_BOUND
        _conf_mult = params.confidence_stop_multipliers.get(2, 1.0)
        _regime_mult = (
            params.regime_stop_multipliers_short.get("RANGE_BOUND", 1.0)
            if direction == "short"
            else params.regime_stop_multipliers.get("RANGE_BOUND", 1.0)
        )

        for entry_idx in oos_entry_positions:
            entry_price = close_arr[entry_idx]
            if entry_price == 0 or np.isnan(entry_price):
                continue

            hold_end_idx = min(entry_idx + horizon, n_total - 1)
            hold_price = close_arr[hold_end_idx]
            if hold_price == 0 or np.isnan(hold_price):
                continue

            hold_ret = (hold_price - entry_price) / entry_price * sign * 100.0 - fee
            hold_returns.append(hold_ret)

            mfe = 0.0
            protect_armed = False
            protect_floor = -999.0
            exit_reason = None
            exit_bar = None

            for j in range(1, max_hold_bars + 1):
                bar_idx = entry_idx + j
                if bar_idx >= n_total:
                    break

                current_return = (close_arr[bar_idx] - entry_price) / entry_price * sign * 100.0
                adverse = max(0.0, -current_return)
                mfe = max(mfe, current_return)

                # ── Layer 1: 自适应硬止损 (含 MFE 棘轮 + regime/confidence 乘数)
                effective_stop = params.stop_pct * _conf_mult * _regime_mult
                # MFE 棘轮: 浮盈足够大时收紧止损（与实盘 line 934 一致）
                if mfe > params.mfe_ratchet_threshold:
                    ratcheted_stop = mfe * params.mfe_ratchet_ratio
                    effective_stop = min(effective_stop, ratcheted_stop)
                if adverse >= effective_stop:
                    exit_reason = "hard_stop"
                    exit_bar = j
                    break

                # ── Layer 2: 止盈
                if params.take_profit_pct > 0 and current_return >= params.take_profit_pct:
                    exit_reason = "take_profit"
                    exit_bar = j
                    break

                # ── Layer 3: 利润保护追踪止损
                if mfe >= params.protect_start_pct:
                    protect_armed = True
                if protect_armed:
                    gap = max(params.protect_floor_pct, mfe * params.protect_gap_ratio)
                    new_floor = mfe - gap
                    new_floor = max(new_floor, params.protect_floor_pct)
                    protect_floor = max(protect_floor, new_floor)
                    if current_return <= protect_floor:
                        exit_reason = "profit_protect"
                        exit_bar = j
                        break

                # ── Layer 4: 最小持仓保护
                if j < params.min_hold_bars:
                    continue

                # ── Layer 5: 论文失效出场（invalidation combos，立即出场不防抖）
                if invalidation_combos and _combo_matches(invalidation_combos, entry_idx, bar_idx):
                    exit_reason = "thesis_invalidated"
                    exit_bar = j
                    break

                # ── Layer 6: 机制衰竭收紧保护
                # 简化模型: decay_score = j / max_hold_bars（线性增长 0->1）
                decay_score = j / max(max_hold_bars, 1)
                if decay_score >= params.decay_exit_threshold:
                    # 衰竭严重到达出场阈值，但回测中不直接出场
                    # （实盘依赖 mechanism_tracker 的精确衰竭分，这里近似为收紧保护）
                    pass
                if decay_score >= params.decay_tighten_threshold and protect_armed:
                    # 收紧保护: 用 tighten_gap_ratio 替代 protect_gap_ratio
                    tightened_gap = max(params.protect_floor_pct, mfe * params.tighten_gap_ratio)
                    tightened_floor = mfe - tightened_gap
                    tightened_floor = max(tightened_floor, params.protect_floor_pct)
                    if current_return <= tightened_floor:
                        exit_reason = "tightened_protect"
                        exit_bar = j
                        break

                # ── Layer 7: 信号消失出场（只在盈利时触发，与实盘 line 1072 一致）
                if _combo_matches(valid_combos, entry_idx, bar_idx) and current_return > 0:
                    exit_reason = "logic_complete"
                    exit_bar = j
                    break

            # ── Layer 8: safety_cap 安全网
            if exit_reason is None:
                exit_reason = _SAFETY_CAP_REASON
                exit_bar = min(max_hold_bars, n_total - 1 - entry_idx)

            reason_counts[exit_reason] = reason_counts.get(exit_reason, 0) + 1
            bars_held_list.append(exit_bar)

            actual_exit_idx = entry_idx + exit_bar
            if actual_exit_idx >= n_total:
                actual_exit_idx = n_total - 1
            exit_price = close_arr[actual_exit_idx]

            if exit_price == 0 or np.isnan(exit_price):
                exit_returns.append(hold_ret)
                continue

            ret = (exit_price - entry_price) / entry_price * sign * 100.0 - fee
            exit_returns.append(ret)

        n_samples = len(exit_returns)
        if n_samples == 0:
            return _EMPTY

        wins = [r for r in exit_returns if r > 0]
        losses = [r for r in exit_returns if r <= 0]
        if losses:
            sum_losses = abs(sum(losses))
            earliest_pf = sum(wins) / sum_losses if sum_losses > 1e-10 else 1.5
        else:
            earliest_pf = 1.5 if wins else 1.0

        net_return_with_exit = float(np.mean(exit_returns))
        hold_mean = float(np.mean(hold_returns)) if hold_returns else 0.0
        improvement = net_return_with_exit - hold_mean
        triggered_count = n_samples - reason_counts.get(_SAFETY_CAP_REASON, 0) - reason_counts.get("time_cap", 0)
        triggered_exit_pct = triggered_count / n_samples * 100.0
        avg_bars_held = float(np.mean(bars_held_list)) if bars_held_list else 0.0
        win_rate = len(wins) / n_samples * 100.0

        return {
            "earliest_pf":         round(earliest_pf, 4),
            "net_return_with_exit": round(net_return_with_exit, 4),
            "improvement":          round(improvement, 4),
            "triggered_exit_pct":   round(triggered_exit_pct, 2),
            "n_samples":            n_samples,
            "exit_reason_counts":   dict(reason_counts),
            "avg_bars_held":        round(avg_bars_held, 2),
            "win_rate":             round(win_rate, 2),
        }

    def _save_pending(self, new_cards: list[dict]) -> None:
        """
        将 pending 池整体按最新闸门重审一遍，再写回 pending_rules.json。
        这样旧候选不会因为历史版本的宽松标准而一直残留。
        """
        existing = []
        if self._pending_file.exists():
            existing = read_json_file(self._pending_file, [])
        if not isinstance(existing, list):
            existing = []

        merged = merge_pending_rules(existing, new_cards)
        reviewed_pending, reflagged, reason_counts = split_review_candidates(merged)
        if reflagged:
            logger.info(
                "[REVIEW] cleaned %d stale pending candidates; keep=%d",
                len(reflagged), len(reviewed_pending),
            )
            for reason, count in sorted(
                reason_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[:10]:
                logger.info("[REVIEW] stale %dx %s", count, reason)
            self._save_flagged(reflagged)

        write_json_atomic(self._pending_file, reviewed_pending, ensure_ascii=False, indent=2)
        logger.info("[DISCOVERY] pending pool refreshed: %d kept", len(reviewed_pending))

    def _save_flagged(self, new_cards: list[dict]) -> None:
        """Merge newly flagged candidates into flagged_rules.json."""
        existing = []
        if self._flagged_file.exists():
            existing = read_json_file(self._flagged_file, [])
        if not isinstance(existing, list):
            existing = []

        merged = merge_flagged_rules(existing, new_cards)
        write_json_atomic(self._flagged_file, merged, ensure_ascii=False, indent=2)
        logger.info(
            "[DISCOVERY] merged %d flagged candidates into %s",
            len(new_cards),
            self._flagged_file,
        )

    def _print_summary(self, cards: list[dict]) -> None:
        """打印发现摘要到控制台。"""
        if not cards:
            return

        sep = "=" * 60
        print()
        print(sep)
        print(f"  [DISCOVERY] 发现 {len(cards)} 个合格候选策略")
        print(sep)

        for i, card in enumerate(cards, 1):
            entry = card["entry"]
            stats = card["stats"]
            exit_info = card.get("exit")

            print(f"\n  [{i}] {card['rule_str']}")
            mech = card.get("mechanism_type", "generic_alpha")
            print(
                f"      方向={entry['direction'].upper()}  研究窗={entry['horizon']}bar  机制={mech}"
                f"  OOS胜率={stats['oos_win_rate']:.1f}%  n={stats['n_oos']}"
                f"  净收益={stats.get('oos_net_return', stats.get('oos_avg_ret', 0.0)):+.4f}%"
                f"  降幂={stats.get('degradation', 0.0):.2f}"
            )
            if exit_info:
                exit_feature = exit_info.get("feature", "top3")
                exit_operator = exit_info.get("operator", "")
                exit_threshold = exit_info.get("threshold")
                exit_threshold_text = (
                    f"{exit_threshold:.4g}" if isinstance(exit_threshold, (int, float)) else "-"
                )
                print(
                    f"      出场条件: {exit_feature} {exit_operator} "
                    f"{exit_threshold_text}"
                    f"  预期持仓~{exit_info.get('expected_hold_bars', 0):.0f}bar"
                    f"  净收益={exit_info.get('net_return_with_exit', 0.0):+.4f}%"
                )
            else:
                print(
                    f"      出场条件: 仅剩 safety_cap={entry['horizon']}bar 安全网 "
                    f"(未挖到因果离场，禁止直接当主逻辑)"
                )

        print()
        print("  在控制台 Alpha 标签页查看并审批")
        print(sep)
        print()

    # ── 静态方法: 待审批/已审批规则管理 ─────────────────────────────────────

    @staticmethod
    def _output_paths(symbol: str = "BTCUSDT") -> tuple[Path, Path]:
        """返回 (pending_file, approved_file) 路径。BTCUSDT 使用默认目录保持兼容。"""
        sym = symbol.upper()
        out = _DEFAULT_OUTPUT_DIR if sym == "BTCUSDT" else _DEFAULT_OUTPUT_DIR / sym
        return out / "pending_rules.json", out / "approved_rules.json"

    @staticmethod
    def load_pending(symbol: str = "BTCUSDT") -> list[dict]:
        """加载所有待审批规则。"""
        pending_file, _ = LiveDiscoveryEngine._output_paths(symbol)
        if not pending_file.exists():
            return []
        return read_json_file(pending_file, [])

    @staticmethod
    def load_approved(symbol: str = "BTCUSDT") -> list[dict]:
        """加载所有已审批规则。"""
        _, approved_file = LiveDiscoveryEngine._output_paths(symbol)
        if not approved_file.exists():
            return []
        return read_json_file(approved_file, [])

    @staticmethod
    def approve(card_id: str, symbol: str = "BTCUSDT") -> bool:
        """
        将 pending_rules.json 中指定 id 的规则移入 approved_rules.json。

        Returns:
            True 表示成功，False 表示未找到
        """
        pending_file, approved_file = LiveDiscoveryEngine._output_paths(symbol)
        pending = LiveDiscoveryEngine.load_pending(symbol)
        target = next((c for c in pending if c["id"] == card_id), None)
        if target is None:
            return False

        # 更新 pending：移除该条
        remaining = [c for c in pending if c["id"] != card_id]
        write_json_atomic(pending_file, remaining, ensure_ascii=False, indent=2)

        # 更新 approved：追加
        approved = LiveDiscoveryEngine.load_approved(symbol)
        target["status"] = "approved"
        target["approved_at"] = datetime.now(timezone.utc).isoformat()
        approved.append(target)
        write_json_atomic(approved_file, approved, ensure_ascii=False, indent=2)

        logger.info(f"[DISCOVERY] 已审批: {target['rule_str']}")
        return True

    @staticmethod
    def reject(card_id: str, symbol: str = "BTCUSDT") -> bool:
        """将 pending_rules.json 中指定 id 的规则标记为 rejected 并移除。"""
        pending_file, _ = LiveDiscoveryEngine._output_paths(symbol)
        pending = LiveDiscoveryEngine.load_pending(symbol)
        target = next((c for c in pending if c["id"] == card_id), None)
        if target is None:
            return False

        remaining = [c for c in pending if c["id"] != card_id]
        write_json_atomic(pending_file, remaining, ensure_ascii=False, indent=2)
        logger.info(f"[DISCOVERY] 已拒绝: {target['rule_str']}")
        return True
