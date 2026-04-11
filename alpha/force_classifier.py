"""
力库分类器。

把 Alpha 管道挖掘出的策略映射到物理力类别（force_category），
并写入 alpha/output/force_registry.json 做分类汇总与集中度管控。

力库的三个作用：
  1. 分类归档 — 每个策略找到它捕捉的是哪股物理力
  2. 集中度管控 — 同一力类在仓位层面最多持 2 个（execution_engine 读取）
  3. 机制追踪激活 — 让 mechanism_tracker 对 Alpha 策略也能做衰竭评分（不再永远 generic_alpha）
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# 种子特征 → 力类别映射
# 规则：从 causal_atom 的 seed_feature 推断这股力的物理本质
SEED_TO_FORCE: dict[str, str] = {
    # 价格位置 / 均值回归
    "position_in_range_24h":   "liquidity_vacuum",
    "position_in_range_4h":    "liquidity_vacuum",
    "dist_to_24h_high":        "distribution_pattern",
    "dist_to_24h_low":         "liquidity_vacuum",
    "vwap_deviation":          "liquidity_vacuum",
    # 主动成交 / 方向耗竭
    "taker_buy_sell_ratio":    "unilateral_exhaustion",
    "large_trade_buy_ratio":   "unilateral_exhaustion",
    "direction_autocorr":      "unilateral_exhaustion",
    "trade_burst_index":       "unilateral_exhaustion",
    # 成交量 / 流动性
    "volume_vs_ma20":          "liquidity_vacuum",
    "volume_autocorr_lag5":    "liquidity_vacuum",
    "avg_trade_size_cv_10m":   "algorithmic_trace",
    # 流动性/价差
    "kyle_lambda":             "inventory_rebalance",
    "spread_vs_ma20":          "distribution_pattern",
    "spread_proxy":            "distribution_pattern",
    # 持仓量 / 资金费率
    "oi_change_rate_5m":       "open_interest_divergence",
    "oi_change_rate_1h":       "open_interest_divergence",
    "funding_rate_trend":      "leverage_cost_imbalance",
    "consecutive_extreme_funding": "leverage_cost_imbalance",
    "rt_funding_rate":         "leverage_cost_imbalance",
    "mark_basis":              "leverage_cost_imbalance",
    # 盘口微结构
    "quote_imbalance":         "inventory_rebalance",
    "bid_depth_ratio":         "inventory_rebalance",
    "spread_anomaly":          "distribution_pattern",
    # 压缩形态
    "vol_drought_blocks_5m":   "potential_energy_release",
    "vol_drought_blocks_10m":  "potential_energy_release",
    "price_compression_blocks_5m": "potential_energy_release",
    "price_compression_blocks_10m": "potential_energy_release",
}

# mechanism_type → force_category 的快速对照（给 mechanism_tracker 用）
MECHANISM_TO_FORCE: dict[str, str] = {
    "funding_settlement":       "leverage_cost_imbalance",
    "funding_divergence":       "leverage_cost_imbalance",
    "funding_cycle_oversold":   "leverage_cost_imbalance",
    "seller_drought":           "liquidity_vacuum",
    "vwap_reversion":           "liquidity_vacuum",
    "algo_slicing":             "algorithmic_trace",
    "compression_release":      "potential_energy_release",
    "bottom_taker_exhaust":     "unilateral_exhaustion",
    "top_buyer_exhaust":        "unilateral_exhaustion",
    "near_high_distribution":   "distribution_pattern",
    "oi_divergence":            "open_interest_divergence",
    "oi_accumulation_long":     "open_interest_divergence",
    "mm_rebalance":             "inventory_rebalance",
    "inventory_rebalance":      "inventory_rebalance",
    "regime_transition":        "regime_change",
    "volume_climax_reversal":   "unilateral_exhaustion",
    "taker_snap_reversal":      "unilateral_exhaustion",
    "amplitude_absorption":     "inventory_rebalance",
    "generic_alpha":            "generic",
}


def classify_force(card: dict[str, Any]) -> str:
    """
    给一张 Alpha 策略卡片分配力类别。

    优先顺序：
      1. 卡片已有 force_category 字段 -> 直接使用
      2. 从 mechanism_type 映射
      3. 从 seed_feature 映射
      4. 兜底 "generic"
    """
    if card.get("force_category"):
        return str(card["force_category"])

    mechanism = str(card.get("mechanism_type") or card.get("stats", {}).get("mechanism_type") or "")
    if mechanism and mechanism in MECHANISM_TO_FORCE:
        return MECHANISM_TO_FORCE[mechanism]

    seed = str(card.get("seed_feature") or card.get("stats", {}).get("seed_feature") or "")
    if seed and seed in SEED_TO_FORCE:
        return SEED_TO_FORCE[seed]

    logger.warning("[ForceClassifier] 无法确定力类别: family=%s seed=%s mechanism=%s",
                   card.get("family"), seed, mechanism)
    return "generic"


# ── 力注册表 I/O ─────────────────────────────────────────────────────────────

_REGISTRY_PATH = Path(__file__).parent / "output" / "force_registry.json"


def load_registry() -> dict[str, Any]:
    """读取力注册表；文件不存在时返回空骨架。"""
    if _REGISTRY_PATH.exists():
        try:
            with open(_REGISTRY_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception as exc:
            logger.warning("[ForceClassifier] 读取力注册表失败: %s", exc)
    return {"updated_at": "", "forces": {}}


def save_registry(registry: dict[str, Any]) -> None:
    """原子写入力注册表。"""
    registry["updated_at"] = datetime.now(tz=timezone.utc).isoformat()
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(_REGISTRY_PATH) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(registry, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _REGISTRY_PATH)
    logger.info("[ForceClassifier] 力注册表已更新: %s", _REGISTRY_PATH)


def register_card(card: dict[str, Any]) -> str:
    """
    将批准的 Alpha 卡片写入力注册表。

    返回分配的 force_category。
    """
    force_cat = classify_force(card)
    registry = load_registry()

    forces = registry.setdefault("forces", {})
    cat_entry = forces.setdefault(force_cat, {"description": "", "strategies": {}})

    family = str(card.get("family") or card.get("name") or "unknown")
    cat_entry["strategies"][family] = {
        "family":        family,
        "direction":     card.get("direction", ""),
        "mechanism_type": card.get("mechanism_type", ""),
        "seed_feature":  card.get("seed_feature") or card.get("stats", {}).get("seed_feature", ""),
        "oos_win_rate":  card.get("stats", {}).get("oos_win_rate", 0),
        "mfe_coverage":  card.get("stats", {}).get("mfe_coverage", 0),
        "approved_at":   card.get("approved_at", ""),
        "status":        card.get("status", "approved"),
    }

    save_registry(registry)
    logger.info("[ForceClassifier] %s 已归入力类别: %s", family, force_cat)
    return force_cat


def get_active_strategies_by_force(force_cat: str) -> list[str]:
    """返回某个力类别下所有 approved/active 的策略 family 列表（给执行引擎用）。"""
    registry = load_registry()
    cat_entry = registry.get("forces", {}).get(force_cat, {})
    return [
        fam
        for fam, info in cat_entry.get("strategies", {}).items()
        if info.get("status") in ("approved", "active", "live")
    ]


def summary() -> dict[str, int]:
    """返回各力类别的策略数量汇总。"""
    registry = load_registry()
    return {
        cat: len(info.get("strategies", {}))
        for cat, info in registry.get("forces", {}).items()
    }
