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
  cards = engine.run_once(data_days=30)
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
from alpha.combo_scanner import ComboScanner, CONFIRM_FEATURES
from alpha.realtime_seed_miner import RealtimeSeedMiner
from utils.file_io import read_json_file, write_json_atomic

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
    if entry_feature == "funding_rate" and direction == "long":
        return "funding_cycle_oversold"
    if entry_feature == "position_in_range_4h" and direction == "short":
        return "funding_divergence"

    return base_mechanism


def _confirm_features_for_mechanism(mechanism_type: str) -> list[str]:
    return list(_MECHANISM_CONFIRM_FEATURES.get(mechanism_type, CONFIRM_FEATURES))


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
        }
        combos.append({
            "conditions":  [primary, cc_exit],
            "combo_label": "C2",
            "description": "Both entry conditions reversed: full mechanism gone",
        })

    return {"top3": combos}


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
    ):
        self.storage_path = storage_path
        self.symbol = symbol.upper()
        self.top_n = top_n
        self.horizons = horizons or [5, 15, 30, 60]
        self.min_triggers = min_triggers
        self.min_icir = min_icir

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

    def run_once(self, data_days: int = 30) -> list[dict]:
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
        atoms = self._miner.mine_from_scan(df, scan_df, top_n=self.top_n)
        logger.info(f"[DISCOVERY] IC 原子种子: {len(atoms)} 个")

        logger.info("[DISCOVERY] 挖掘实时卖压种子...")
        realtime_seeds = self._realtime_seed_miner.mine(df, self.horizons)
        logger.info(f"[DISCOVERY] 实时卖压种子: {len(realtime_seeds)} 个")

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
                final_exit = _derive_mechanism_exit(
                    entry_feature=str(row["seed_feature"]),
                    entry_op=str(row["seed_op"]),
                    entry_threshold=float(row["seed_threshold"]),
                    combo_conditions=[{
                        "feature":   row["confirm_feature"],
                        "op":        row["confirm_op"],
                        "threshold": float(row["confirm_threshold"]),
                    }],
                )

                # 构建策略卡片
                card = self._build_combo_card(row, final_exit)
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
                exit_cond = _derive_mechanism_exit(
                    entry_feature=atom.feature,
                    entry_op=atom.operator,
                    entry_threshold=float(atom.threshold),
                )
                card = self._build_card(atom, wf, exit_cond)
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
        self._print_summary(pending)

        return pending

    # ── 内部方法 ──────────────────────────────────────────────────────────────

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
            "mechanism_type": _infer_mechanism_type(
                atom.feature, atom.direction, atom.operator
            ),
        }

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
    ) -> dict:
        """将 combo 扫描结果打包成策略卡片。"""
        now_iso = datetime.now(timezone.utc).isoformat()
        card_id = (
            datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_")
            + row["seed_feature"][:8]
            + "_"
            + row["confirm_feature"][:8]
        )

        stop_pct = None

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
            "mechanism_type": _infer_mechanism_type(
                row["seed_feature"], row["direction"], str(row["seed_op"])
            ),
        }
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
