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
from alpha.combo_scanner import ComboScanner, CONFIRM_FEATURES
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
_FORCE_DECAY_RATIO = 0.55
_FORCE_INVALIDATION_RATIO = 0.30

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

    if horizon >= 60:
        stop_pct *= 1.08
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
        protect_gap_ratio=base.protect_gap_ratio,
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
        cc = combo_conditions[0]
        cc_feature = str(cc["feature"])
        cc_op = str(cc["op"])
        cc_thr = float(cc["threshold"])
        confirm_decay, confirm_abs = _build_force_decay_condition(
            cc_feature, cc_op, cc_thr
        )
        confirm_invalidation = _build_invalidation_condition(
            cc_feature, cc_op, cc_thr
        )
        combos.extend(
            [
                {
                    "conditions": [primary_decay, confirm_decay],
                    "combo_label": "C3",
                    "description": "Entry force and confirm force both repaired versus entry",
                },
                {
                    "conditions": [primary_abs, confirm_abs],
                    "combo_label": "C4",
                    "description": "Primary and confirm states both returned toward neutral",
                },
            ]
        )
        invalidation.append(
            {
                "conditions": [primary_invalidation, confirm_invalidation],
                "combo_label": "I2",
                "description": "Primary and confirm force both worsened versus entry",
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

    # ── 主入口 ────────────────────────────────────────────────────────────────

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
            key = (
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

                # 出场：入场信号消失 = 出场，不挖掘历史数据
                direction = row["direction"]
                mechanism_type = _infer_mechanism_type(
                    str(row["seed_feature"]),
                    direction,
                    str(row["seed_op"]),
                )
                exit_params = _build_mechanism_exit_params(
                    mechanism_type,
                    int(row["horizon"]),
                    direction,
                )
                final_exit = _derive_mechanism_exit_v2(
                    entry_feature=str(row["seed_feature"]),
                    entry_op=str(row["seed_op"]),
                    entry_threshold=float(row["seed_threshold"]),
                    combo_conditions=[{
                        "feature":   row["confirm_feature"],
                        "op":        row["confirm_op"],
                        "threshold": float(row["confirm_threshold"]),
                    }],
                    mechanism_type=mechanism_type,
                )

                # Backtest exit conditions on OOS data
                exit_metrics = self._shadow_smart_exit_backtest(
                    df, entry_mask, final_exit, direction, int(row["horizon"]),
                    params=exit_params,
                )
                final_exit.update(exit_metrics)

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
                exit_cond = _derive_mechanism_exit_v2(
                    entry_feature=atom.feature,
                    entry_op=atom.operator,
                    entry_threshold=float(atom.threshold),
                    mechanism_type=mechanism_type,
                )
                entry_mask_atom = self._build_entry_mask(df, atom)
                exit_metrics = self._shadow_smart_exit_backtest(
                    df, entry_mask_atom, exit_cond, atom.direction, int(atom.horizon),
                    params=exit_params,
                )
                exit_cond.update(exit_metrics)
                card = self._build_card(atom, wf, exit_cond, exit_params, mechanism_type)
                results.append(card)

        if not results:
            logger.info("[DISCOVERY] 本次未发现合格策略")
            return []

        logger.info(f"[DISCOVERY] 合格策略: {len(results)} 个")

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

        card_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_") + atom.feature[:10]

        card = {
            "id": card_id,
            "entry": {
                "feature":   atom.feature,
                "operator":  atom.operator,
                "threshold": round(float(atom.threshold), 6),
                "direction": atom.direction,
                "horizon":   max(int(atom.horizon), 30),
            },
            "exit": exit_cond,  # None = 使用固定持仓 horizon
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
                "oos_win_rate":   round(float(oos.get("win_rate") or 0), 2),
                "n_oos":          int(oos.get("n_triggers") or 0),
                "oos_net_return": round(float(oos.get("avg_return_pct") or 0), 4),
                "degradation":    round(float(wf_report.get("degradation") or 0), 3),
                "is_robust":      bool(wf_report.get("is_robust", False)),
            },
            "explanation": explanation,
            "rule_str": atom.rule_str(),
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "status": "pending",
            "mechanism_type": mechanism_type,
        }
        card["family"] = infer_product_family(card)

        return card

    def _build_combo_entry_mask(self, df: pd.DataFrame, row) -> pd.Series:
        """根据 combo 扫描结果构建多条件入场掩码 (种子 AND 确认)。"""
        from alpha.combo_scanner import _crossing_mask

        # 种子条件 (带 crossing 检测)
        seed_feat = row["seed_feature"]
        seed_op = row["seed_op"]
        seed_thresh = row["seed_threshold"]
        if seed_feat in df.columns:
            seed_mask = pd.Series(
                _crossing_mask(df[seed_feat].values, seed_op, seed_thresh, cooldown=60),
                index=df.index,
            )
        else:
            return pd.Series(False, index=df.index)

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
        card_id = (
            datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_")
            + row["seed_feature"][:8]
            + "_"
            + row["confirm_feature"][:8]
        )

        stop_pct = round(float(exit_params.stop_pct), 4)

        card = {
            "id": card_id,
            "group": row.get("seed_name", row["seed_feature"]),
            "status": "pending",
            "entry": {
                "feature":   row["seed_feature"],
                "operator":  row["seed_op"],
                "threshold": round(float(row["seed_threshold"]), 6),
                "direction": row["direction"],
                "horizon":   max(int(row["horizon"]), 30),
            },
            "combo_conditions": [
                {
                    "feature":   row["confirm_feature"],
                    "op":        row["confirm_op"],
                    "threshold": round(float(row["confirm_threshold"]), 6),
                }
            ],
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
                "oos_win_rate":    round(float(row["oos_wr"]), 2),
                "n_oos":           int(row["oos_n"]),
                "oos_pf":          round(float(row["oos_pf"]), 3),
                "oos_avg_ret":     round(float(row["oos_avg_ret"]), 4),
                "oos_net_return":  round(float(row["oos_avg_ret"]), 4),
                "wr_improvement":  round(float(row["wr_improvement"]), 2),
                "seed_oos_wr":     round(float(row["seed_oos_wr"]), 2),
                "degradation":     round(float(row.get("degradation") or 0), 3),
            },
            "explanation": (
                f"种子: {row['seed_feature']} {row['seed_op']} {row['seed_threshold']:.4g} "
                f"({row['direction'].upper()} {int(row['horizon'])}bars)\n"
                f"确认: {row['confirm_feature']} {row['confirm_op']} {row['confirm_threshold']:.4g}\n"
                f"组合OOS胜率={row['oos_wr']:.1f}% (种子单独={row['seed_oos_wr']:.1f}%, "
                f"提升+{row['wr_improvement']:.1f}%)"
            ),
            "rule_str": (
                f"{row['seed_feature']} {row['seed_op']} {row['seed_threshold']:.4g} "
                f"AND {row['confirm_feature']} {row['confirm_op']} {row['confirm_threshold']:.4g} "
                f"-> {row['direction']} {int(row['horizon'])}bars"
            ),
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
    ):
        """
        OOS 回测出场条件，模拟 P1 智能出场框架的 5 层逻辑。

        模拟层:
          Layer 1: 硬止损 (adverse >= stop_pct)
          Layer 3: 利润保护追踪止损 (protect_armed once mfe >= protect_start_pct)
          Layer 5: 最小持仓保护 (skip logic_complete if j < min_hold_bars)
          Layer 7: 信号消失出场 (entry combo 反转, current_return > 0)
          Fallback: 时间上限 (time_cap at max_hold_bars)

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
                "thesis_invalidated": 0,
                "profit_protect": 0,
                "logic_complete": 0,
                "time_cap": 0,
            },
            "avg_bars_held": 0.0,
            "win_rate": 0.0,
        }

        combos = exit_info.get("top3") if isinstance(exit_info, dict) else None
        invalidation = exit_info.get("invalidation") if isinstance(exit_info, dict) else None
        if not isinstance(combos, list) or not combos:
            return _EMPTY

        n_total = len(df)
        split_idx = int(n_total * 0.67)
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
            "thesis_invalidated": 0,
            "profit_protect": 0,
            "logic_complete": 0,
            "time_cap": 0,
        }

        sign = -1.0 if direction == "short" else 1.0

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

                # Layer 1: 硬止损
                if adverse >= params.stop_pct:
                    exit_reason = "hard_stop"
                    exit_bar = j
                    break

                # Layer 3: 利润保护追踪止损
                if mfe >= params.protect_start_pct:
                    protect_armed = True
                if invalidation_combos and _combo_matches(invalidation_combos, entry_idx, bar_idx):
                    exit_reason = "thesis_invalidated"
                    exit_bar = j
                    break
                if protect_armed:
                    gap = max(params.protect_floor_pct, mfe * params.protect_gap_ratio)
                    new_floor = mfe - gap
                    new_floor = max(new_floor, params.protect_floor_pct)
                    protect_floor = max(protect_floor, new_floor)
                    if current_return <= protect_floor:
                        exit_reason = "profit_protect"
                        exit_bar = j
                        break

                # Layer 5: 最小持仓保护
                if j < params.min_hold_bars:
                    continue

                # Layer 7: 信号消失出场 (只在盈利时触发)
                if _combo_matches(valid_combos, entry_idx, bar_idx) and current_return > 0:
                    exit_reason = "logic_complete"
                    exit_bar = j
                    break

            # Fallback: 时间上限
            if exit_reason is None:
                exit_reason = "time_cap"
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
        triggered_count = n_samples - reason_counts.get("time_cap", 0)
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
                f"      方向={entry['direction'].upper()}  周期={entry['horizon']}bar  机制={mech}"
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
                print(f"      出场条件: 固定持仓 {entry['horizon']}bar (未找到更优出场)")

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
