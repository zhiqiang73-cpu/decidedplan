"""Signal runner for phase-1 event detectors and phase-2 alpha rules."""

from __future__ import annotations

import logging
from typing import List, Optional, Tuple

import pandas as pd

from monitor.alpha_rules import AlphaRuleChecker
from monitor.exit_policy_config import has_explicit_exit_params
from monitor.live_catalog import (
    build_strategy_status_rows,
)
from monitor.flow_classifier import FlowClassifier
from monitor.regime_detector import RegimeDetector
from monitor.signal_health import SignalHealth
from signals.bottom_volume_drought import BottomVolumeDroughtDetector
from signals.funding_rate_signal import FundingRateDetector
from signals.funding_cycle_oversold_long import FundingCycleOversoldLong
from signals.high_pos_funding import HighPosFundingDetector
from signals.position_compression import PositionCompressionDetector
from signals.taker_exhaustion_low import TakerExhaustionLowDetector
from signals.vwap_twap import VWAPTWAPDetector
from signals.vwap_vol_drought import VwapVolDroughtDetector
from signals.regime_transition import RegimeTransitionDetector
from monitor.mechanism_tracker import (
    get_mechanism_for_family,
    get_force_category,
    check_conflicts,
    check_reinforces,
)

logger = logging.getLogger(__name__)

_PHASE1_TAIL = 300


class AdaptiveCooldown:
    """Self-tuning cooldown per group: hot strategies trade faster, cold ones get throttled.

    Tracks last N outcomes per (group, direction) and adjusts cooldown accordingly:
    - WR >= 60% last 5 trades: 3 min (let hot strategies fire more)
    - WR 40-60%: default (10 min)
    - WR < 40%: 20 min (throttle)
    - WR < 25%: 30 min (near-frozen)
    """

    def __init__(self, default_cooldown_ms: int, window: int = 5):
        self._default_ms = default_cooldown_ms
        self._window = max(3, window)
        self._outcomes: dict[str, list[bool]] = {}  # key -> [win, loss, win, ...]

    def record_outcome(self, group_key: str, is_win: bool) -> None:
        buf = self._outcomes.setdefault(group_key, [])
        buf.append(is_win)
        if len(buf) > self._window * 2:
            self._outcomes[group_key] = buf[-self._window:]

    def get_cooldown_ms(self, group_key: str) -> int:
        buf = self._outcomes.get(group_key, [])
        if len(buf) < self._window:
            return self._default_ms
        recent = buf[-self._window:]
        wr = sum(recent) / len(recent)
        if wr >= 0.60:
            return int(self._default_ms * 0.3)  # 3 min if default is 10
        if wr >= 0.40:
            return self._default_ms
        if wr >= 0.25:
            return int(self._default_ms * 2.0)  # 20 min
        return int(self._default_ms * 3.0)  # 30 min


class SignalRunner:
    """Run live event detectors and alpha rules on the latest feature frame."""

    def __init__(
        self,
        alpha_cooldown: int = 1,
        p2_startup_grace_bars: int = 3,
        p2_group_cooldown_min: int = 5,
        p2_max_groups_per_bar: int = 2,
    ):
        self._alpha_checker = AlphaRuleChecker(cooldown_bars=alpha_cooldown)
        self._funding_rate = FundingRateDetector()
        self._vwap_twap = VWAPTWAPDetector()
        self._bottom_drought = BottomVolumeDroughtDetector()
        self._vwap_drought = VwapVolDroughtDetector()
        self._pos_compression = PositionCompressionDetector()
        self._taker_exhaust_low = TakerExhaustionLowDetector()
        self._high_pos_funding = HighPosFundingDetector()
        self._funding_cycle_oversold = FundingCycleOversoldLong()
        self._regime_transition = RegimeTransitionDetector()
        self._regime_detector = RegimeDetector()
        self._flow_classifier = FlowClassifier()
        self._signal_health: SignalHealth | None = None  # 外部注入

        self._oi_ready: bool = False

        self._live_detectors = [
            self._funding_rate,
            self._vwap_twap,
            self._bottom_drought,
            self._vwap_drought,
            self._pos_compression,
            self._taker_exhaust_low,
            self._high_pos_funding,
            self._funding_cycle_oversold,
            self._regime_transition,
        ]

        self._new_signal_cooldown: dict[str, int] = {}
        self._new_signal_cooldown_ms = 3 * 60 * 1000

        self._shared_cooldown_groups: dict[tuple[str, str], str] = {
            ("P1-8_vwap_vol_drought", "long"): "vwap_bottom_long",
            ("P1-9_position_compression", "long"): "vwap_bottom_long",
        }
        self._shared_cooldown: dict[str, int] = {}

        self._bar_count = 0
        self._last_bar_ts = 0
        self._p2_startup_grace_bars = max(0, int(p2_startup_grace_bars))
        self._p2_group_cooldown_ms = max(0, int(p2_group_cooldown_min) * 60 * 1000)
        self._p2_max_groups_per_bar = max(1, int(p2_max_groups_per_bar))
        self._p2_group_cooldown: dict[str, int] = {}
        self._adaptive_cooldown = AdaptiveCooldown(default_cooldown_ms=self._p2_group_cooldown_ms)
        self._last_p2_startup_block_log_bar = -1

    def set_signal_health(self, health: SignalHealth) -> None:
        """注入信号健康度监控实例（可选）。"""
        self._signal_health = health

    def record_p2_outcome(self, group_key: str, is_win: bool) -> None:
        """Feed trade outcome to adaptive cooldown for self-tuning."""
        self._adaptive_cooldown.record_outcome(group_key, is_win)

    def run(self, df: Optional[pd.DataFrame]) -> Tuple[List[dict], List[dict]]:
        if df is None or df.empty:
            return [], []

        raw_alerts: List[dict] = []
        latest_ts = int(df["timestamp"].iloc[-1]) if "timestamp" in df.columns else 0
        if latest_ts != self._last_bar_ts:
            self._bar_count += 1
            self._last_bar_ts = latest_ts

        window = df.tail(_PHASE1_TAIL).reset_index(drop=True)

        latest_row = df.iloc[-1]
        alpha_alerts = self._alpha_checker.check(row=latest_row, timestamp_ms=latest_ts)

        p2_raw: List[dict] = []
        for alert in alpha_alerts:
            p2_raw.append(
                {
                    "phase": "P2",
                    "name": alert["name"],
                    "direction": alert["direction"],
                    "horizon": alert["horizon"],
                    "timestamp_ms": latest_ts,
                    "desc": alert["desc"],
                    "feature": alert["feature"],
                    "feature_value": alert["feature_value"],
                    "threshold": alert["threshold"],
                    "op": alert["op"],
                    "group": alert.get("group", ""),
                    "family": alert.get("family"),
                    "confidence": alert.get("confidence", 1),
                    "confidence_label": alert.get("confidence_label", "LOW"),
                    "physical_confirms": alert.get("physical_confirms", []),
                    "alpha_exit_conditions": list(alert.get("alpha_exit_conditions", [])),
                    "alpha_exit_combos": list(alert.get("alpha_exit_combos", [])),
                    "stop_pct": alert.get("stop_pct"),
                    "rule_str": alert.get("rule_str", ""),
                    "card_id": alert.get("card_id", alert["name"]),
                    "trade_ready": bool(alert.get("trade_ready")),
                }
            )

        raw_alerts.extend(p2_raw)

        # OI readiness tracking
        recent_oi = (
            df["oi_change_rate_5m"].iloc[-10:]
            if "oi_change_rate_5m" in df.columns
            else pd.Series(dtype=float)
        )
        oi_ready = int(recent_oi.notna().sum()) >= 5
        if not oi_ready and not self._oi_ready:
            logger.warning(
                "[RUNNER] OI data not ready yet - skipping OI-dependent signals (P1-8 SHORT, P1-2)"
            )
        elif oi_ready and not self._oi_ready:
            logger.info("[RUNNER] OI data now ready - all signals enabled")
            self._oi_ready = True

        for detector in self._live_detectors:
            try:
                alert = detector.check_live(df.tail(_PHASE1_TAIL))
            except Exception as exc:
                logger.warning(f"[LIVE] {detector.name} detection error: {exc}")
                continue

            if alert is None:
                continue

            if detector is self._vwap_twap and not oi_ready:
                continue

            if (
                alert is not None
                and detector is self._vwap_drought
                and alert.get("direction") == "short"
                and not oi_ready
            ):
                logger.debug("[RUNNER] P1-8 SHORT skipped: OI not ready")
                continue

            cooldown_ms = int(
                getattr(detector, "runner_cooldown_ms", self._new_signal_cooldown_ms)
            )
            group_key = self._shared_cooldown_groups.get(
                (detector.name, alert.get("direction", ""))
            )
            if group_key:
                last_ts = self._shared_cooldown.get(group_key, 0)
                if latest_ts - last_ts < cooldown_ms:
                    logger.debug(
                        f"[SHARED_CD] {detector.name}({alert.get('direction')}) "
                        f"suppressed by group cooldown {group_key}"
                    )
                    continue
                self._shared_cooldown[group_key] = latest_ts
            else:
                last_ts = self._new_signal_cooldown.get(detector.name, 0)
                if latest_ts - last_ts < cooldown_ms:
                    continue
                self._new_signal_cooldown[detector.name] = latest_ts

            raw_alerts.append(alert)

        regime = self._regime_detector.detect(latest_row, window)
        trend_dir = self._regime_detector.current_trend
        logger.debug("[REGIME] current regime: %s  trend: %s", regime, trend_dir)
        flow_type = self._flow_classifier.classify(latest_row)
        logger.debug("[FLOW] current flow: %s", flow_type)

        p1_alerts = [alert for alert in raw_alerts if alert.get("phase") == "P1"]
        composite_alerts = list(p1_alerts)
        composite_alerts.extend(self._aggregate_p2_by_group(p2_raw, latest_ts))

        if self._signal_health is not None:
            _pre_count = len(composite_alerts)
            composite_alerts = self._filter_by_health(composite_alerts)
            if len(composite_alerts) < _pre_count:
                logger.info("[HEALTH] Filtered %d alerts by signal health", _pre_count - len(composite_alerts))

        composite_alerts = self._regime_detector.filter_alerts(
            composite_alerts, regime, trend_direction=trend_dir,
        )
        composite_alerts = self._apply_force_logic(composite_alerts)
        # attach flow_type and regime labels
        for alert in composite_alerts:
            alert["flow_type"] = flow_type
            alert["regime"] = regime
            alert["trend_direction"] = trend_dir
        return raw_alerts, composite_alerts

    @property
    def current_regime(self) -> str:
        return self._regime_detector.current_regime

    @property
    def current_flow(self) -> str:
        return self._flow_classifier.current_flow

    @property
    def current_trend(self) -> str:
        return self._regime_detector.current_trend

    def strategy_status_rows(self) -> list[dict]:
        return build_strategy_status_rows(has_explicit_exit_params)

    def _aggregate_p2_by_group(self, p2_alerts: List[dict], latest_ts: int) -> List[dict]:
        if not p2_alerts:
            return []

        if self._bar_count <= self._p2_startup_grace_bars:
            if self._last_p2_startup_block_log_bar != self._bar_count:
                self._last_p2_startup_block_log_bar = self._bar_count
                logger.info(
                    "[P2 STARTUP_GUARD] blocked %d grouped alerts on startup bar %d/%d",
                    len(p2_alerts),
                    self._bar_count,
                    self._p2_startup_grace_bars,
                )
            return []

        groups: dict[tuple, list[dict]] = {}
        for alert in p2_alerts:
            key = (
                alert.get("group", alert["name"]),
                alert["direction"],
                alert["horizon"],
            )
            groups.setdefault(key, []).append(alert)

        candidates: List[dict] = []
        for (group, direction, horizon), members in groups.items():
            first = members[0]
            names = " | ".join(alert["name"] for alert in members)
            family = str(first.get("family") or f"ALPHA::{group}::{direction}::{horizon}")

            confidence = max(alert["confidence"] for alert in members)
            if confidence >= 3:
                label = "HIGH"
            elif confidence >= 2:
                label = "MEDIUM"
            else:
                label = "LOW"

            all_confirms = set()
            exit_conditions: list[dict] = []
            exit_combos: list[list[dict]] = []
            seen_exit_keys: set[tuple[str, str, float]] = set()
            stop_values: list[float] = []
            card_ids: list[str] = []
            for alert in members:
                all_confirms.update(alert.get("physical_confirms", []))
                # Collect Top-3 exit combos (preferred)
                for combo in alert.get("alpha_exit_combos", []):
                    if isinstance(combo, list) and combo:
                        exit_combos.append(combo)
                # Also collect flat exit conditions (backward compat)
                for exit_cond in alert.get("alpha_exit_conditions", []):
                    if not isinstance(exit_cond, dict):
                        continue
                    feature = str(exit_cond.get("feature", "")).strip()
                    operator = str(exit_cond.get("operator") or exit_cond.get("op") or "").strip()
                    try:
                        threshold = float(exit_cond.get("threshold"))
                    except (TypeError, ValueError):
                        continue
                    key = (feature, operator, threshold)
                    if not feature or operator not in {"<", ">"} or key in seen_exit_keys:
                        continue
                    seen_exit_keys.add(key)
                    exit_conditions.append(
                        {
                            "feature": feature,
                            "operator": operator,
                            "threshold": threshold,
                            "expected_hold_bars": exit_cond.get("expected_hold_bars"),
                            "net_return_with_exit": exit_cond.get("net_return_with_exit"),
                        }
                    )
                try:
                    stop_pct = float(alert.get("stop_pct"))
                    if stop_pct > 0:
                        stop_values.append(stop_pct)
                except (TypeError, ValueError):
                    pass
                card_id = str(alert.get("card_id", "")).strip()
                if card_id:
                    card_ids.append(card_id)

            composite = {
                "phase": "P2",
                "name": names,
                "family": family,
                "direction": direction,
                "horizon": horizon,
                "timestamp_ms": first["timestamp_ms"],
                "desc": f"[P2 {label}] {group} ({len(members)} rules) {direction.upper()} {horizon}bars",
                "feature": first["feature"],
                "feature_value": first["feature_value"],
                "threshold": first["threshold"],
                "op": first["op"],
                "group": group,
                "confidence": confidence,
                "confidence_label": label,
                "physical_confirms": list(all_confirms),
                "alpha_exit_conditions": exit_conditions,
                "alpha_exit_combos": exit_combos,
                "stop_pct": min(stop_values) if stop_values else None,
                "card_ids": card_ids,
                "trade_ready": bool(exit_combos or exit_conditions),
                "_member_count": len(members),
            }
            candidates.append(composite)

        candidates.sort(
            key=lambda a: (
                int(a.get("confidence", 0)),
                int(bool(a.get("trade_ready"))),
                int(a.get("_member_count", 0)),
            ),
            reverse=True,
        )

        composites: List[dict] = []
        for candidate in candidates:
            group_key = str(candidate.get("group", ""))
            cooldown_key = f"{group_key}|{candidate.get('direction')}|{candidate.get('horizon')}"
            effective_cd_ms = self._adaptive_cooldown.get_cooldown_ms(cooldown_key)
            if effective_cd_ms > 0:
                last_ts = self._p2_group_cooldown.get(cooldown_key, 0)
                if latest_ts - last_ts < effective_cd_ms:
                    logger.debug(
                        "[P2 GROUP_CD] suppressed %s (%s min adaptive cooldown)",
                        cooldown_key,
                        effective_cd_ms // 60000,
                    )
                    continue

            if len(composites) >= self._p2_max_groups_per_bar:
                logger.info(
                    "[P2 BURST_CAP] capped at %d groups this bar; dropped %s",
                    self._p2_max_groups_per_bar,
                    cooldown_key,
                )
                continue

            self._p2_group_cooldown[cooldown_key] = latest_ts
            candidate.pop("_member_count", None)
            composites.append(candidate)

            confirms = candidate.get("physical_confirms", [])
            confirms_str = ", ".join(sorted(confirms)) if confirms else "none"
            logger.info(
                f"[P2 COMPOSITE] {candidate.get('confidence_label','LOW')}(conf={candidate.get('confidence',1)}) | "
                f"{str(candidate.get('direction','')).upper()} {candidate.get('horizon')}bars | "
                f"group={candidate.get('group')} [{candidate.get('name')}] | physical: [{confirms_str}]"
            )

        return composites


    def _apply_force_logic(self, alerts: list[dict]) -> list[dict]:
        """Enrich alerts with mechanism/force metadata; resolve conflicts and reinforcements.

        For each alert:
          - Adds 'mechanism_type' and 'force_category' fields.

        Pairwise across all alerts:
          - Conflicting mechanisms: keep the higher-confidence one, drop the lower.
            Equal confidence: keep the earlier one (index order).
          - Reinforcing mechanisms: boost confidence of both by 1 (cap at 3);
            append peer mechanism to 'force_reinforced_by' list.

        Returns a new list; input list is not mutated.
        """
        if not alerts:
            return []

        # Shallow-copy each alert and annotate with mechanism fields
        enriched: list[dict] = []
        for alert in alerts:
            a = dict(alert)
            family = str(a.get("family") or "")
            mech = get_mechanism_for_family(family)
            a["mechanism_type"] = mech
            a["force_category"] = get_force_category(mech)
            a.setdefault("force_reinforced_by", [])
            enriched.append(a)

        # --- Conflict pass: mark losers for removal ---
        drop_indices: set[int] = set()
        for i in range(len(enriched)):
            for j in range(i + 1, len(enriched)):
                if i in drop_indices or j in drop_indices:
                    continue
                mech_i = enriched[i]["mechanism_type"]
                mech_j = enriched[j]["mechanism_type"]
                if check_conflicts(mech_i, mech_j):
                    conf_i = int(enriched[i].get("confidence") or 1)
                    conf_j = int(enriched[j].get("confidence") or 1)
                    if conf_i >= conf_j:
                        drop_idx = j
                        dropped = mech_j
                    else:
                        drop_idx = i
                        dropped = mech_i
                    drop_indices.add(drop_idx)
                    logger.info(
                        "[FORCE] Conflict: %s vs %s, dropping %s",
                        mech_i, mech_j, dropped,
                    )

        # --- Reinforcement pass (only on non-dropped alerts) ---
        for i in range(len(enriched)):
            if i in drop_indices:
                continue
            for j in range(i + 1, len(enriched)):
                if j in drop_indices:
                    continue
                mech_i = enriched[i]["mechanism_type"]
                mech_j = enriched[j]["mechanism_type"]
                if check_reinforces(mech_i, mech_j):
                    enriched[i]["confidence"] = min(3, int(enriched[i].get("confidence") or 1) + 1)
                    enriched[j]["confidence"] = min(3, int(enriched[j].get("confidence") or 1) + 1)
                    enriched[i]["force_reinforced_by"].append(mech_j)
                    enriched[j]["force_reinforced_by"].append(mech_i)
                    logger.info(
                        "[FORCE] Reinforcement: %s + %s",
                        mech_i, mech_j,
                    )

        return [a for idx, a in enumerate(enriched) if idx not in drop_indices]

    def _filter_by_health(self, alerts: list[dict]) -> list[dict]:
        """按信号健康度过滤: 任一 card retired 则丢弃, 任一 degraded 则降 1 级置信度。"""
        if self._signal_health is None:
            return alerts
        result = []
        for alert in alerts:
            ids_to_check = list(alert.get("card_ids", []))
            single_id = alert.get("card_id", "")
            if single_id and single_id not in ids_to_check:
                ids_to_check.append(single_id)
            if not ids_to_check:
                result.append(alert)
                continue
            states = [self._signal_health.get_state(cid) for cid in ids_to_check]
            if "retired" in states:
                logger.warning("[HEALTH] Skipped alert with retired card(s): %s", ids_to_check)
                continue
            if "degraded" in states:
                alert = dict(alert)
                alert["confidence"] = max(1, alert.get("confidence", 1) - 1)
                if alert["confidence"] >= 3:
                    alert["confidence_label"] = "HIGH"
                elif alert["confidence"] >= 2:
                    alert["confidence_label"] = "MEDIUM"
                else:
                    alert["confidence_label"] = "LOW"
                logger.info("[HEALTH] Degraded card(s) in %s: confidence downgraded", ids_to_check)
            result.append(alert)
        return result

