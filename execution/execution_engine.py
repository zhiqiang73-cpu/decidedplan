"""Signal-to-order bridge for the execution layer."""

from __future__ import annotations

import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from execution import config
from execution.exchange_sync import ExchangeTradeSyncer
from execution.order_manager import OrderManager, OrderManagerError
from execution.trade_logger import TradeLogger
from monitor.mechanism_tracker import MechanismTracker, resolve_mechanism_type, get_mechanism_for_family, get_force_category
from monitor.live_catalog import is_execution_allowed
from monitor.exit_policy_config import (
    ExitParams,
    build_exit_params,
    get_exit_params_for_signal,
    has_explicit_exit_params,
)
from monitor.smart_exit_policy import (
    build_entry_snapshot,
    build_runtime_state,
    evaluate_exit_action,
    normalize_family,
    resolve_effective_stop_pct,
    update_mfe_mae,
)
from utils.file_io import write_json_atomic

logger = logging.getLogger(__name__)

# Exit reason constants (module-level to avoid per-call allocation)
_HARD_STOP_REASONS = frozenset({"hard_stop"})
_MECHANISM_DECAY_PREFIX = "mechanism_decay_"
_ALPHA_LOSS_COOLDOWN_S = 420   # 7 minutes (alpha cards)
_DECAY_COOLDOWN_S = 120        # 2 minutes (P1 signals)
_LOSS_THRESHOLD = -0.0002      # -0.02% gross
_TIMECAP_COOLDOWN_S = 600      # 10 minutes
_MANUAL_INTERVENTION_COOLDOWN_S = 900  # 15 minutes after exchange/manual intervention

# Position sizing overrides
_QUIET_POSITION_PCT = 0.05     # 5% for QUIET_TREND regime
_COUNTER_TREND_PCT = 0.04      # 4% for counter-trend shorts


@dataclass(frozen=True)
class EntryAttempt:
    price: float
    reference_price: float
    timeout_s: float
    time_in_force: str
    entry_fee_type: str
    mode: str


@dataclass
class PendingEntry:
    order_id: str
    signal_name: str
    family: str
    mechanism_type: str
    direction: str
    qty: float
    requested_price: float
    attempt: int
    attempt_timeout_s: float
    time_in_force: str
    entry_fee_type: str
    confidence: int
    horizon_min: int
    signal_time: datetime
    entry_snapshot: dict[str, Any]
    dynamic_exit_enabled: bool

    @property
    def research_horizon_min(self) -> int:
        return self.horizon_min


@dataclass
class OpenPosition:
    signal_name: str
    family: str
    direction: str
    qty: float
    entry_price: float
    confidence: int
    horizon_min: int
    entry_time: datetime
    exit_due_time: datetime | None
    order_id: str
    entry_snapshot: dict[str, Any]
    runtime_state: dict[str, Any]
    dynamic_exit_enabled: bool
    entry_fee_type: str = "maker"
    external: bool = False
    entry_regime: str = ""
    entry_flow_type: str = ""
    mechanism_type: str = ""
    exchange_stop_order_id: str = ""  # 交易所端止损单 ID（防宕机灾难保护）

    @property
    def research_horizon_min(self) -> int:
        return self.horizon_min


class ExecutionEngine:
    """Manage limit entries and exits for the execution layer."""

    def __init__(
        self,
        order_manager: OrderManager | None,
        trade_logger: TradeLogger | None = None,
        position_pct: float = config.POSITION_PCT,
        leverage: int = config.LEVERAGE,
        max_positions: int = config.MAX_POSITIONS,
        min_confidence: int = config.MIN_CONFIDENCE,
        entry_timeout_s: int = config.ENTRY_TIMEOUT_S,
        poll_interval_s: float = config.ORDER_POLL_INTERVAL_S,
    ) -> None:
        self.order_manager = order_manager
        self.trade_logger = trade_logger or TradeLogger()
        self.position_pct = position_pct
        self.leverage = leverage
        self.max_positions = max_positions
        self.min_confidence = min_confidence
        self.entry_timeout_s = float(entry_timeout_s)
        self.entry_retry_timeout_s = min(
            self.entry_timeout_s,
            float(config.ENTRY_RETRY_TIMEOUT_S),
        )
        self.entry_maker_only = bool(getattr(config, "ENTRY_MAKER_ONLY", False))
        configured_attempts = max(1, int(config.ENTRY_MAX_ATTEMPTS))
        self.max_entry_attempts = 1 if self.entry_maker_only else configured_attempts
        self.poll_interval_s = poll_interval_s
        self.enabled = order_manager is not None

        self._lock = threading.RLock()
        self._pending_entries: dict[str, PendingEntry] = {}
        self._open_positions: dict[str, OpenPosition] = {}
        self._signal_cooldown: dict[str, float] = {}
        self._last_ext_sync_ts: float = 0.0
        self._external_sync_failures: int = 0
        self._external_sync_future = None
        self._external_sync_keys: set[str] = set()
        self._thread_pool = ThreadPoolExecutor(
            max_workers=max_positions,
            thread_name_prefix="entry-monitor",
        )
        # Dedicated pool for exchange sync — must NOT share with entry-monitor
        # to avoid starvation when all entry threads are blocked polling orders
        self._sync_pool = ThreadPoolExecutor(
            max_workers=2,
            thread_name_prefix="exchange-sync",
        )
        self._mechanism_tracker = MechanismTracker()
        self._current_regime: str = "QUIET_TREND"
        self._current_flow: str = "PASSIVE"
        self._current_trend: str = "TREND_NEUTRAL"
        self._signal_health: Any | None = None
        self._signal_runner: Any | None = None
        self._decision_logger: Any | None = None
        self._conviction_engine: Any | None = None
        # A2 family 1-bar entry confirmation: key="{family}|{direction}"
        self._entry_confirm_pending: dict[str, float] = {}

        # Exchange trade syncer -- pulls actual fills from Binance
        self._exchange_syncer: ExchangeTradeSyncer | None = (
            ExchangeTradeSyncer(order_manager) if order_manager is not None else None
        )
        self._last_trade_sync_ts: float = 0.0

        # Persist open positions across restarts
        self._state_file = Path("execution/logs/positions_state.json")
        self._pending_restore_state: dict[str, Any] = {}
        self._booted_without_order_manager = order_manager is None
        self._orphan_flatten_future = None
        self._orphan_flatten_meta: dict[str, Any] | None = None
        self._orphan_flatten_attempts = 0
        self._orphan_recovery_deadline_ts = (
            time.time() + 1800.0 if self._booted_without_order_manager else 0.0
        )
        self._restore_positions_from_state()

        if self.enabled:
            logger.info("[EXEC] Connected to Binance Testnet")
            if self.entry_maker_only:
                logger.info("[EXEC] Entry mode: maker-only (single passive GTC attempt)")
            try:
                order_manager.set_leverage(leverage)  # type: ignore[union-attr]
            except Exception as exc:
                logger.warning("[EXEC] set_leverage failed: %s", exc)
        else:
            logger.warning("[EXEC] Testnet API credentials missing; paper mode only")

    def set_signal_health(self, signal_health: Any) -> None:
        """Inject signal health tracker for outcome recording."""
        self._signal_health = signal_health

    def set_signal_runner(self, runner: Any) -> None:
        """Inject signal runner for adaptive cooldown feedback."""
        self._signal_runner = runner

    def set_decision_logger(self, decision_logger: Any) -> None:
        """Inject decision logger for signal audit trail."""
        self._decision_logger = decision_logger

    def set_conviction_engine(self, brain: Any) -> None:
        """Inject conviction engine (adaptive brain) for shadow scoring."""
        self._conviction_engine = brain

    def is_execution_connected(self) -> bool:
        """当前是否具备可用的交易接口。"""
        return bool(self.enabled and self.order_manager is not None)

    def set_order_manager(self, order_manager: OrderManager | None) -> None:
        """运行中替换交易接口，供 monitor 后台自愈重连使用。"""
        with self._lock:
            self.order_manager = order_manager
            self.enabled = order_manager is not None

        if order_manager is None:
            logger.warning("[EXEC] Order manager detached; execution disabled")
            return

        logger.info("[EXEC] Order manager attached; live execution restored")
        try:
            order_manager.set_leverage(self.leverage)
        except Exception as exc:
            logger.warning("[EXEC] set_leverage failed after reconnect: %s", exc)
        self._restore_positions_from_state()

    def _log_blocked(self, signal: str, family: str, direction: str,
                     blocked_by: str, confidence: int, reason: str) -> None:
        if self._decision_logger is not None:
            self._decision_logger.log_blocked(
                signal=signal, family=family, direction=direction,
                blocked_by=blocked_by, confidence=confidence,
                regime=self._current_regime, trend=self._current_trend,
                flow=self._current_flow, reason=reason,
            )

    def _log_executed(self, signal: str, family: str, direction: str,
                      confidence: int, reason: str) -> None:
        if self._decision_logger is not None:
            self._decision_logger.log_executed(
                signal=signal, family=family, direction=direction,
                confidence=confidence,
                regime=self._current_regime, trend=self._current_trend,
                flow=self._current_flow, reason=reason,
            )

    def _arm_manual_intervention_cooldown(
        self,
        family: str,
        direction: str,
        reason: str,
    ) -> None:
        cooldown_key = f"{family}|{direction}"
        until_ts = time.time() + _MANUAL_INTERVENTION_COOLDOWN_S
        with self._lock:
            self._signal_cooldown[cooldown_key] = max(
                float(self._signal_cooldown.get(cooldown_key, 0.0) or 0.0),
                until_ts,
            )
            remaining = max(0, int(self._signal_cooldown[cooldown_key] - time.time()))
        logger.warning(
            "[EXEC] Reconcile cooldown armed: %s blocked for %ds after %s",
            cooldown_key,
            remaining,
            reason,
        )

    def update_market_state(
        self,
        regime: str,
        flow_type: str,
        trend_direction: str = "TREND_NEUTRAL",
    ) -> None:
        """Receive current market state from signal_runner."""
        self._current_regime = regime
        self._current_flow = flow_type
        self._current_trend = trend_direction

    def on_signal(self, alert: dict[str, Any], latest_features: Any | None = None) -> None:
        direction = str(alert.get("direction", "")).lower()
        if direction not in {"long", "short"}:
            return

        signal_name = str(alert.get("name", ""))
        family = self._resolve_signal_family(alert)
        confidence = self._safe_int(alert.get("confidence"), 1)

        if not self.enabled or self.order_manager is None:
            logger.warning(
                "[EXEC] Skip %s: execution disabled (order manager unavailable)",
                signal_name or family,
            )
            self._log_blocked(
                signal_name,
                family,
                direction,
                "execution_disabled",
                confidence,
                "execution engine unavailable",
            )
            return

        if confidence < self.min_confidence:
            logger.debug(
                "[EXEC] Skip low-confidence %s conf=%s < %s",
                signal_name, confidence, self.min_confidence,
            )
            self._log_blocked(signal_name, family, direction, "confidence_gate",
                              confidence, f"conf={confidence} < min={self.min_confidence}")
            return
        is_alpha = self._is_alpha_alert(alert, family)

        # Block LONG entries during LIQUIDATION flow -- forced selling creates
        # hostile conditions for longs (dead-cat bounce risk).
        if direction == "long" and self._current_flow == "LIQUIDATION":
            logger.info(
                "[EXEC] Skip %s: LONG blocked during LIQUIDATION flow", signal_name
            )
            self._log_blocked(signal_name, family, direction, "liquidation_flow",
                              confidence, "LONG blocked during LIQUIDATION flow")
            return

        # Block ALL SHORTs in uptrend without exception.
        # 均值回归 SHORT 在单边上涨中无论置信度多高都无效：
        #   - 趋势行情中 VWAP/24h高位/买盘萎缩 的"过高"状态会持续存在
        #   - HIGH 置信度只说明特征很极端，不代表回归会发生
        # 旧逻辑：P1 SHORT 在 TREND_UP 时仅封 conf<3，HIGH 可绕过 → 已知亏损根源
        # 新逻辑：TREND_UP 时所有 SHORT 一律封死，包括 HIGH 置信度
        # 例外：P1-10 买方耗尽在 4h 区间顶部 (>0.90) 物理上仍然有效
        if (
            direction == "short"
            and self._current_trend == "TREND_UP"
        ):
            # P1-10 exception: buyer exhaustion at 4h range top is physically valid
            # even in an uptrend -- price at intraday extreme = buyers spent
            is_p110 = "P1-10" in signal_name
            at_4h_top = False
            if is_p110 and latest_features is not None:
                r4h = (
                    latest_features.get("position_in_range_4h")
                    if isinstance(latest_features, (dict,))
                    else None
                )
                if r4h is None and hasattr(latest_features, "get"):
                    r4h = latest_features.get("position_in_range_4h")
                at_4h_top = r4h is not None and float(r4h) > 0.90

            if at_4h_top:
                logger.info(
                    "[EXEC] %s SHORT exception in TREND_UP: 4h top zone r4h=%.3f",
                    signal_name, float(r4h),
                )
                # Fall through to normal execution
            else:
                if is_alpha:
                    confirm_key = f"{family}|{direction}"
                    if confirm_key in self._entry_confirm_pending:
                        logger.info(
                            "[EXEC] A2 deferred entry cancelled for %s: TREND_UP invalidates setup",
                            signal_name,
                        )
                        self._entry_confirm_pending.pop(confirm_key, None)
                logger.info(
                    "[EXEC] Skip %s: ALL SHORTs blocked in TREND_UP (conf=%d, family=%s)",
                    signal_name, confidence, family,
                )
                self._log_blocked(signal_name, family, direction, "trend_filter",
                                  confidence, "ALL SHORTs blocked in TREND_UP (P1-10@4h-top excepted)")
                return

        if (
            direction == "long"
            and self._current_trend == "TREND_DOWN"
            and confidence < 2
        ):
            logger.info(
                "[EXEC] Skip %s: LONG blocked in TREND_DOWN (conf=%d < 2)",
                signal_name, confidence,
            )
            self._log_blocked(signal_name, family, direction, "trend_filter",
                              confidence, f"LONG in TREND_DOWN needs MEDIUM+ conf (got {confidence})")
            # Cancel any pending deferred entry: TREND_DOWN invalidates the A2 setup.
            confirm_key = f"{family}|{direction}"
            if confirm_key in self._entry_confirm_pending:
                logger.info(
                    "[EXEC] A2 deferred entry cancelled for %s: TREND_DOWN invalidates setup",
                    signal_name,
                )
                self._entry_confirm_pending.pop(confirm_key, None)
            return

        # Alpha 淇″彿涔熷繀椤婚€氳繃鐧藉悕鍗?-- 闃叉 approved_rules.json 缁曡繃 live_catalog 鏆傚仠
        if not is_execution_allowed(
            family,
            direction,
            allow_synthetic_alpha=is_alpha,
        ):
            logger.info("[EXEC] Rejected %s %s: not in whitelist (alpha=%s)", family, direction, is_alpha)
            self._log_blocked(signal_name, family, direction, "whitelist",
                              confidence, f"{family}|{direction} not in execution allowlist")
            return

        cooldown_key = f"{family}|{direction}"
        now_ts = time.time()
        cooldown_until = self._signal_cooldown.get(cooldown_key, 0.0)
        if now_ts < cooldown_until:
            remaining = int(cooldown_until - now_ts)
            logger.info("[EXEC] Skip %s: execution cooldown (%ss remaining)", signal_name, remaining)
            self._log_blocked(signal_name, family, direction, "execution_cooldown",
                              confidence, f"cooldown {remaining}s remaining")
            return

        # A2 family: signal persistence filter.
        # HIGH confidence (3+) bypasses persistence -- 2+ physical confirms are
        # sufficient confirmation. Otherwise require 15s persistence (was 55s).
        if family.startswith("A2"):
            confirm_key = f"{family}|{direction}"
            if confidence >= 3:
                # HIGH confidence: immediate entry, skip persistence
                self._entry_confirm_pending.pop(confirm_key, None)
                logger.info("[EXEC] A2 fast entry %s: HIGH confidence bypass (conf=%d)", signal_name, confidence)
            else:
                first_seen = self._entry_confirm_pending.get(confirm_key)
                if first_seen is None:
                    self._entry_confirm_pending[confirm_key] = now_ts
                    logger.info("[EXEC] A2 entry deferred %s: waiting confirm (15s~30min)", signal_name)
                    return
                elapsed = now_ts - first_seen
                if elapsed < 15:
                    logger.debug(
                        "[EXEC] A2 entry deferred %s: %ds < 15s confirm window",
                        signal_name, int(elapsed),
                    )
                    return
                if elapsed > 1800:
                    self._entry_confirm_pending[confirm_key] = now_ts
                    logger.info("[EXEC] A2 entry confirm expired %s after %ds, resetting", signal_name, int(elapsed))
                    return
                del self._entry_confirm_pending[confirm_key]
                logger.info(
                    "[EXEC] A2 entry confirmed %s after %ds (persistence filter passed)",
                    signal_name, int(elapsed),
                )

        horizon_min = max(
            1,
            self._safe_int(alert.get("research_horizon_bars", alert.get("horizon")), 1),
        )
        signal_time = self._extract_alert_time(alert)
        entry_snapshot = build_entry_snapshot(alert, latest_features)
        mechanism_type = str(
            alert.get("mechanism_type")
            or entry_snapshot.get("mechanism_type")
            or get_mechanism_for_family(family)
        )
        if latest_features is None:
            logger.warning(
                "[EXEC] %s: no live features; dynamic exit may fall back to stop/safety_cap only",
                signal_name,
            )

        dynamic_exit_enabled, exit_params = self._resolve_alert_exit_plan(
            alert=alert,
            family=family,
            direction=direction,
            horizon_min=horizon_min,
            entry_snapshot=entry_snapshot,
        )
        if not dynamic_exit_enabled or exit_params is None:
            reason = (
                "alpha card has no dedicated exit feature" if is_alpha
                else f"no exit params configured for {family}|{direction}"
            )
            logger.warning("[EXEC] Rejected %s: %s", signal_name, reason)
            self._log_blocked(signal_name, family, direction, "no_exit_params",
                              confidence, reason)
            return

        with self._lock:
            self._sync_external_position_locked()
            if any(position.external for position in self._open_positions.values()):
                logger.info("[EXEC] Skip %s: external|any already active", signal_name)
                self._log_blocked(signal_name, family, direction, "external_position",
                                  confidence, "external position active")
                return

            # --- Force concentration gate (inside lock to prevent TOCTOU) ---
            alert_mechanism = mechanism_type
            alert_force_cat = get_force_category(alert_mechanism)

            # Rule 1: same force category + same direction 鈫?max 1 position
            # Check BOTH open positions AND pending entries (limit orders not yet filled)
            same_dir_force_count = sum(
                1 for pos in self._open_positions.values()
                if get_force_category(get_mechanism_for_family(pos.family)) == alert_force_cat
                and pos.direction == direction
            ) + sum(
                1 for p in self._pending_entries.values()
                if get_force_category(p.mechanism_type) == alert_force_cat
                and p.direction == direction
            )
            if same_dir_force_count >= 1:
                reason = (
                    f"same-direction force concentration "
                    f"{alert_force_cat}|{direction}={same_dir_force_count} >= 1"
                )
                logger.info("REJECT %s: %s", signal_name, reason)
                self._log_blocked(
                    signal_name,
                    family,
                    direction,
                    "force_concentration",
                    confidence,
                    reason,
                )
                return

            # Rule 2: same force category has a losing position 鈫?don't add
            close_price = self._extract_close(latest_features)
            if close_price is not None:
                for pos in self._open_positions.values():
                    if get_force_category(get_mechanism_for_family(pos.family)) != alert_force_cat:
                        continue
                    if pos.direction == "long":
                        unrealised_pct = (close_price - pos.entry_price) / pos.entry_price * 100
                    else:
                        unrealised_pct = (pos.entry_price - close_price) / pos.entry_price * 100
                    if unrealised_pct < 0:
                        reason = (
                            f"same-force {alert_force_cat} has losing position "
                            f"{pos.signal_name} ({unrealised_pct:.3f}%)"
                        )
                        logger.info("REJECT %s: %s", signal_name, reason)
                        self._log_blocked(
                            signal_name,
                            family,
                            direction,
                            "force_concentration",
                            confidence,
                            reason,
                        )
                        return

            # Rule 3: total across all directions per force category 鈫?max 2
            force_count = sum(
                1 for pos in self._open_positions.values()
                if get_force_category(get_mechanism_for_family(pos.family)) == alert_force_cat
            ) + sum(
                1 for p in self._pending_entries.values()
                if get_force_category(p.mechanism_type) == alert_force_cat
            )
            if force_count >= 2:
                reason = f"force concentration {alert_force_cat}={force_count} >= 2"
                logger.info("REJECT %s: %s", signal_name, reason)
                self._log_blocked(
                    signal_name,
                    family,
                    direction,
                    "force_concentration",
                    confidence,
                    reason,
                )
                return

            total = len(self._pending_entries) + len(self._open_positions)
            if total >= self.max_positions:
                logger.info(
                    "[EXEC] Skip %s: max_positions=%s reached (%s active)",
                    signal_name,
                    self.max_positions,
                    total,
                )
                return

            pos_key = f"{family}|{direction}"
            if pos_key in self._open_positions or any(
                pending.family == family and pending.direction == direction
                for pending in self._pending_entries.values()
            ):
                logger.info("[EXEC] Skip %s: %s already active", signal_name, pos_key)
                return

            try:
                open_pos = self.order_manager.get_open_positions()
                if len(open_pos) >= self.max_positions:
                    logger.info(
                        "[EXEC] Skip %s: max_positions=%s reached on exchange",
                        signal_name,
                        self.max_positions,
                    )
                    return
            except OrderManagerError as exc:
                logger.warning("[EXEC] get_open_positions failed: %s", exc)
                self._log_blocked(
                    signal_name,
                    family,
                    direction,
                    "exchange_unavailable",
                    confidence,
                    f"cannot verify exchange positions: {exc}",
                )
                return

            # 鈹€鈹€ Conviction Engine: entry scoring (shadow mode) 鈹€鈹€
            if self._conviction_engine is not None:
                try:
                    _fv = float(alert.get("feature_value") or 0.0)
                    _th = float(alert.get("threshold") or 0.0)
                    _op = str(alert.get("op") or ">")
                    _confirms = alert.get("physical_confirms") or []
                    entry_conv = self._conviction_engine.entry_score(
                        feature_value=_fv,
                        threshold=_th,
                        direction=direction,
                        trend=self._current_trend,
                        family=family,
                        regime=self._current_regime,
                        physical_confirms=_confirms,
                        op=_op,
                    )
                    entry_snapshot["entry_conviction"] = round(entry_conv, 4)
                    entry_snapshot["trend_direction"] = self._current_trend
                    entry_snapshot["entry_op"] = _op
                    entry_snapshot["entry_conviction_features"] = (
                        self._conviction_engine._entry_features(
                            _fv, _th, direction, self._current_trend,
                            family, self._current_regime, _confirms, _op,
                        )
                    )
                    logger.info(
                        "[BRAIN] %s entry_conviction=%.3f (shadow)",
                        signal_name, entry_conv,
                    )
                except Exception as exc:
                    logger.debug("[BRAIN] entry_score error: %s", exc)

            try:
                attempt_plan = self._build_entry_attempt(direction, attempt=1)
                # QUIET_TREND uses smaller position; counter-trend shorts use half
                effective_pct = (
                    _QUIET_POSITION_PCT
                    if self._current_regime == "QUIET_TREND"
                    else self.position_pct
                )
                # Cap counter-trend SHORT positions: uptrend shorts get half position
                if direction == "short" and self._current_trend == "TREND_UP":
                    effective_pct = min(effective_pct, _COUNTER_TREND_PCT)
                # Cap counter-trend LONG positions: P1-14 longs in TREND_DOWN get half position
                if direction == "long" and self._current_trend == "TREND_DOWN" and "P1-14" in signal_name:
                    effective_pct = min(effective_pct, _COUNTER_TREND_PCT)
                if effective_pct != self.position_pct:
                    logger.debug(
                        "[EXEC] %s regime=%s: position_pct %.0f%% 鈫?%.0f%%",
                        signal_name, self._current_regime,
                        self.position_pct * 100, effective_pct * 100,
                    )
                qty = self.order_manager.calc_qty(
                    effective_pct,
                    self.leverage,
                    attempt_plan.reference_price,
                )
                result = self.order_manager.place_limit_entry(
                    direction=direction,
                    qty=qty,
                    price=attempt_plan.price,
                    signal_name=signal_name,
                    horizon_min=horizon_min,
                    time_in_force=attempt_plan.time_in_force,
                )
            except (OrderManagerError, ValueError) as exc:
                logger.warning("[EXEC] Entry failed %s: %s", signal_name, exc)
                return

            if result.get("status") != "placed":
                logger.warning("[EXEC] Entry rejected %s: %s", signal_name, result)
                return

            self._log_executed(signal_name, family, direction, confidence,
                               f"entry placed at {attempt_plan.price:.2f}")

            pending = PendingEntry(
                order_id=str(result.get("order_id", "")),
                signal_name=signal_name,
                family=family,
                mechanism_type=mechanism_type,
                direction=direction,
                qty=float(result.get("qty", qty)),
                requested_price=float(result.get("price", attempt_plan.price)),
                attempt=1,
                attempt_timeout_s=attempt_plan.timeout_s,
                time_in_force=attempt_plan.time_in_force,
                entry_fee_type=attempt_plan.entry_fee_type,
                confidence=confidence,
                horizon_min=horizon_min,
                signal_time=signal_time,
                entry_snapshot=entry_snapshot,
                dynamic_exit_enabled=dynamic_exit_enabled,
            )
            self._pending_entries[pending.order_id] = pending
            logger.info(
                "[EXEC] Limit order placed %s %s price=%.2f qty=%.6f attempt=1 mode=%s tif=%s exit_mode=%s",
                signal_name,
                direction.upper(),
                pending.requested_price,
                pending.qty,
                attempt_plan.mode,
                attempt_plan.time_in_force,
                "dynamic" if dynamic_exit_enabled else "timed",
            )
            order_id = pending.order_id

        self._thread_pool.submit(self._monitor_pending_entry, order_id)

    def _build_entry_attempt(self, direction: str, attempt: int) -> EntryAttempt:
        if self.order_manager is None:
            raise ValueError("order manager is not available")

        book = self.order_manager.get_book_ticker()
        bid = float(book.get("bid") or 0.0)
        ask = float(book.get("ask") or 0.0)
        tick = max(float(book.get("tick_size") or 0.0), 0.0)
        cross = tick * max(1, int(config.ENTRY_FINAL_CROSS_TICKS))
        reference_price = self._reference_price_from_book(book)

        if direction == "long":
            passive_price = bid if bid > 0 else ask
            aggressive_base = ask if ask > 0 else passive_price
            aggressive_price = aggressive_base + cross
        elif direction == "short":
            passive_price = ask if ask > 0 else bid
            aggressive_base = bid if bid > 0 else passive_price
            aggressive_price = aggressive_base - cross if aggressive_base > cross else aggressive_base
        else:
            raise ValueError(f"Unsupported entry direction: {direction}")

        if attempt <= 1 or self.entry_maker_only:
            price = passive_price
            timeout_s = self.entry_timeout_s
            time_in_force = "GTC"
            entry_fee_type = "maker"
            mode = "passive_touch" if attempt <= 1 else "passive_repost"
        else:
            price = aggressive_price if aggressive_price > 0 else passive_price
            timeout_s = self.entry_retry_timeout_s
            time_in_force = "IOC"
            entry_fee_type = "taker"
            mode = "aggressive_ioc"

        if price <= 0:
            raise ValueError(f"Invalid entry price for {direction}: bid={bid} ask={ask}")

        return EntryAttempt(
            price=price,
            reference_price=reference_price,
            timeout_s=timeout_s,
            time_in_force=time_in_force,
            entry_fee_type=entry_fee_type,
            mode=mode,
        )

    @staticmethod
    def _reference_price_from_book(book: dict[str, Any]) -> float:
        bid = float(book.get("bid") or 0.0)
        ask = float(book.get("ask") or 0.0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
        if ask > 0:
            return ask
        if bid > 0:
            return bid
        raise ValueError("book ticker missing valid bid/ask")

    @staticmethod
    def _resolve_signal_family(alert: dict[str, Any]) -> str:
        family = str(alert.get("family") or "").strip()
        if family:
            return family
        return normalize_family(str(alert.get("name", "")))

    @staticmethod
    def _is_alpha_alert(alert: dict[str, Any], family: str) -> bool:
        phase = str(alert.get("phase", "")).upper()
        return (
            phase == "P2"
            or family.startswith("ALPHA::")
            or bool(alert.get("alpha_exit_conditions"))
            or bool(alert.get("alpha_exit_combos"))
        )

    @staticmethod
    def _safe_float(value: Any, default: float | None = None) -> float | None:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _exit_params_from_dict(payload: Any) -> ExitParams | None:
        return build_exit_params(payload)

    def _resolve_alert_exit_plan(
        self,
        *,
        alert: dict[str, Any],
        family: str,
        direction: str,
        horizon_min: int,
        entry_snapshot: dict[str, Any],
    ) -> tuple[bool, ExitParams | None]:
        if self._is_alpha_alert(alert, family):
            exit_conditions = entry_snapshot.get("alpha_exit_conditions") or []
            exit_combos = entry_snapshot.get("alpha_exit_combos") or []
            card_params = self._exit_params_from_dict(entry_snapshot.get("alpha_exit_params"))
            if card_params is None:
                card_params = self._exit_params_from_dict(alert.get("alpha_exit_params"))
            if not exit_conditions and not exit_combos and card_params is None and not has_explicit_exit_params(family, direction):
                return False, None

            stop_pct = self._safe_float(
                alert.get("stop_pct"),
                default=self._safe_float(entry_snapshot.get("alpha_stop_pct"), None),
            )

            if card_params is not None:
                base = card_params
            elif has_explicit_exit_params(family, direction):
                base = get_exit_params_for_signal(family, direction)
            else:
                base = ExitParams(
                    stop_pct=0.70,
                    protect_start_pct=0.12,
                    protect_gap_ratio=0.50,
                    protect_floor_pct=0.03,
                    min_hold_bars=5,
                    max_hold_factor=4,
                    exit_confirm_bars=1,
                )

            params = ExitParams(
                take_profit_pct=base.take_profit_pct,
                stop_pct=float(stop_pct if stop_pct is not None else base.stop_pct),
                protect_start_pct=base.protect_start_pct,
                protect_gap_ratio=base.protect_gap_ratio,
                protect_floor_pct=base.protect_floor_pct,
                min_hold_bars=base.min_hold_bars,
                max_hold_factor=base.max_hold_factor,
                exit_confirm_bars=base.exit_confirm_bars,
                decay_exit_threshold=base.decay_exit_threshold,
                decay_tighten_threshold=base.decay_tighten_threshold,
                tighten_gap_ratio=base.tighten_gap_ratio,
                confidence_stop_multipliers=dict(base.confidence_stop_multipliers),
                regime_stop_multipliers=dict(base.regime_stop_multipliers),
                mfe_ratchet_threshold=base.mfe_ratchet_threshold,
                mfe_ratchet_ratio=base.mfe_ratchet_ratio,
            )
            entry_snapshot["alpha_exit_params"] = params.to_dict()
            return True, params

        if not has_explicit_exit_params(family, direction):
            return False, None
        return True, get_exit_params_for_signal(family, direction)
    def _resolve_position_exit_params(self, position: OpenPosition) -> ExitParams:
        snapshot = position.entry_snapshot or {}
        alpha_params = self._exit_params_from_dict(snapshot.get("alpha_exit_params"))
        if alpha_params is not None:
            return alpha_params
        return get_exit_params_for_signal(position.family, position.direction)

    def on_bar(self, latest_features: Any) -> None:
        if not self.enabled:
            return

        current_time = self._extract_bar_time(latest_features)
        close_price = self._extract_close(latest_features)

        with self._lock:
            self._sync_external_position_locked()
            positions_snapshot = list(self._open_positions.values())

        for position in positions_snapshot:
            if position.external:
                continue

            due_position: OpenPosition | None = None
            exit_reason = "filled_timeout"
            runtime_state = position.runtime_state

            if runtime_state.get("close_pending"):
                due_position = position
                exit_reason = str(runtime_state.get("close_pending_reason") or "close_retry")
            elif position.dynamic_exit_enabled and close_price is not None:
                runtime_state["bars_held"] = int(runtime_state.get("bars_held", 0) or 0) + 1
                update_mfe_mae(
                    runtime_state,
                    {"entry_price": position.entry_price, "direction": position.direction},
                    close_price,
                )
                # Mechanism lifecycle evaluation
                decay_result = self._mechanism_tracker.evaluate_decay(
                    mechanism_type=position.mechanism_type or get_mechanism_for_family(position.family),
                    entry_snapshot=position.entry_snapshot or {},
                    current_features=latest_features,
                    entry_regime=position.entry_regime,
                    current_regime=self._current_regime,
                )
                # Pass decay info into runtime_state
                runtime_state["decay_score"] = decay_result.decay_score
                runtime_state["decay_action"] = decay_result.recommended_action
                runtime_state["decay_reason"] = decay_result.reason
                # Adaptive stop context
                runtime_state["confidence"] = position.confidence
                runtime_state["entry_regime"] = position.entry_regime

                # 鈹€鈹€ Conviction Engine: per-bar hold scoring (shadow mode) 鈹€鈹€
                if self._conviction_engine is not None:
                    try:
                        entry_snap = position.entry_snapshot or {}
                        entry_fv = float(entry_snap.get("feature_value") or 0.0)
                        entry_feature_name = str(entry_snap.get("feature") or "")
                        current_fv = entry_fv  # fallback
                        if entry_feature_name and latest_features is not None:
                            try:
                                current_fv = float(latest_features[entry_feature_name])
                            except (KeyError, TypeError, ValueError):
                                pass
                        bars_held = int(runtime_state.get("bars_held", 0) or 0)
                        # Compute current return directly (decision not yet created)
                        if position.direction == "short":
                            current_return = (position.entry_price - close_price) / position.entry_price * 100
                        else:
                            current_return = (close_price - position.entry_price) / position.entry_price * 100
                        adverse_pct = float(runtime_state.get("mae_pct", 0.0) or 0.0)
                        stop_pct = float(self._resolve_position_exit_params(position).stop_pct)
                        entry_trend = str(entry_snap.get("trend_direction") or "TREND_NEUTRAL")
                        entry_op = str(entry_snap.get("entry_op") or ">")
                        prev_returns = runtime_state.get("conviction_prev_returns") or []
                        hold_features = self._conviction_engine.compute_hold_features(
                            entry_feature_value=entry_fv,
                            current_feature_value=current_fv,
                            direction=position.direction,
                            current_return=current_return,
                            bars_held=bars_held,
                            max_hold=position.horizon_min,
                            adverse_pct=adverse_pct,
                            stop_pct=stop_pct,
                            entry_trend=entry_trend,
                            current_trend=self._current_trend,
                            prev_returns=prev_returns,
                            op=entry_op,
                        )
                        hold_conv = self._conviction_engine.hold_score(
                            entry_feature_value=entry_fv,
                            current_feature_value=current_fv,
                            direction=position.direction,
                            current_return=current_return,
                            bars_held=bars_held,
                            max_hold=position.horizon_min,
                            adverse_pct=adverse_pct,
                            stop_pct=stop_pct,
                            entry_trend=entry_trend,
                            current_trend=self._current_trend,
                            prev_returns=prev_returns,
                            op=entry_op,
                        )
                        pos_key = f"{position.family}|{position.direction}"
                        self._conviction_engine.record_bar(pos_key, hold_features, current_return)
                        runtime_state["hold_conviction"] = round(hold_conv, 4)
                        # Track recent returns for pnl_velocity (stable 5-bar window)
                        prev_returns.append(current_return)
                        if len(prev_returns) > 5:
                            prev_returns[:] = prev_returns[-5:]
                        runtime_state["conviction_prev_returns"] = prev_returns
                        if bars_held % 5 == 0 or hold_conv < 0.25:
                            logger.info(
                                "[BRAIN] %s hold_conviction=%.3f bar=%d ret=%.3f%%",
                                position.signal_name, hold_conv, bars_held, current_return,
                            )
                    except Exception as exc:
                        logger.debug("[BRAIN] hold_score error for %s: %s", position.signal_name, exc)

                decision = evaluate_exit_action(
                    position={
                        "rule": position.signal_name,
                        "family": position.family,
                        "direction": position.direction,
                        "entry_price": position.entry_price,
                        "hold_bars": position.horizon_min,
                        "research_horizon_bars": position.horizon_min,
                        "entry_snapshot": position.entry_snapshot,
                    },
                    close=close_price,
                    features=latest_features,
                    runtime_state=runtime_state,
                    params=self._resolve_position_exit_params(position),
                )
                if decision.get("action") == "exit":
                    due_position = position
                    exit_reason = str(decision.get("reason", "dynamic_exit"))
            elif position.exit_due_time is not None and current_time >= position.exit_due_time:
                due_position = position

            if due_position is not None:
                self._close_position(due_position, exit_reason=exit_reason)

    def _monitor_pending_entry(self, order_id: str) -> None:
        if self.order_manager is None:
            return

        with self._lock:
            pending = self._pending_entries.get(order_id)
            if pending is None:
                return
            deadline = time.monotonic() + pending.attempt_timeout_s

        while True:
            with self._lock:
                pending = self._pending_entries.get(order_id)
                if pending is None:
                    return

            try:
                status = self.order_manager.get_order_status(order_id)
            except OrderManagerError as exc:
                logger.warning("[EXEC] Status check failed order_id=%s: %s", order_id, exc)
                # Deadline check MUST happen even on HTTP failure, otherwise
                # persistent network outage causes an infinite loop.
                if time.monotonic() >= deadline:
                    logger.warning(
                        "[EXEC] Deadline reached during HTTP failure for order_id=%s; finalizing",
                        order_id,
                    )
                    self._finalize_not_filled(order_id)
                    return
                time.sleep(self.poll_interval_s)
                continue

            state = str(status.get("status", "")).upper()
            executed_qty = float(status.get("executed_qty") or 0.0)

            if state == "FILLED":
                self._promote_pending_to_position(order_id, status)
                return

            if state in {"CANCELED", "EXPIRED", "REJECTED"} and executed_qty <= 0:
                self._advance_pending_entry(order_id)
                return

            if time.monotonic() >= deadline:
                if state == "PARTIALLY_FILLED" or executed_qty > 0:
                    self.order_manager.cancel_order(order_id)
                    refreshed = status
                    try:
                        refreshed = self.order_manager.get_order_status(order_id)
                    except OrderManagerError:
                        pass
                    self._promote_pending_to_position(order_id, refreshed)
                    return

                self._cancel_pending_entry(order_id)
                return

            time.sleep(self.poll_interval_s)

    def _cancel_pending_entry(self, order_id: str) -> None:
        if self.order_manager is None:
            return

        canceled = self.order_manager.cancel_order(order_id)
        if not canceled:
            try:
                status = self.order_manager.get_order_status(order_id)
            except OrderManagerError:
                status = {"executed_qty": 0.0, "status": "UNKNOWN"}
            if float(status.get("executed_qty") or 0.0) > 0:
                self._promote_pending_to_position(order_id, status)
                return

        self._advance_pending_entry(order_id)

    def _advance_pending_entry(self, order_id: str) -> None:
        with self._lock:
            pending = self._pending_entries.get(order_id)
            if pending is None:
                return
            attempt = pending.attempt

        if attempt < self.max_entry_attempts:
            self._retry_pending_entry(order_id)
        else:
            self._finalize_not_filled(order_id)

    def _retry_pending_entry(self, original_order_id: str) -> None:
        if self.order_manager is None:
            return

        with self._lock:
            pending = self._pending_entries.get(original_order_id)
            if pending is None:
                return
            direction = pending.direction
            qty = pending.qty
            signal_name = pending.signal_name
            horizon_min = pending.horizon_min
            next_attempt = pending.attempt + 1

        try:
            attempt_plan = self._build_entry_attempt(direction, attempt=next_attempt)
            result = self.order_manager.place_limit_entry(
                direction=direction,
                qty=qty,
                price=attempt_plan.price,
                signal_name=signal_name,
                horizon_min=horizon_min,
                time_in_force=attempt_plan.time_in_force,
            )
        except (OrderManagerError, ValueError) as exc:
            logger.warning("[EXEC] Retry entry failed %s: %s", signal_name, exc)
            self._finalize_not_filled(original_order_id)
            return

        if result.get("status") != "placed":
            logger.warning("[EXEC] Retry entry rejected %s: %s", signal_name, result)
            self._finalize_not_filled(original_order_id)
            return

        new_order_id = str(result.get("order_id", ""))
        with self._lock:
            pending = self._pending_entries.get(original_order_id)
            if pending is None:
                return
            del self._pending_entries[original_order_id]
            retry_pending = PendingEntry(
                order_id=new_order_id,
                signal_name=pending.signal_name,
                family=pending.family,
                mechanism_type=pending.mechanism_type,
                direction=pending.direction,
                qty=float(result.get("qty", pending.qty)),
                requested_price=float(result.get("price", attempt_plan.price)),
                attempt=next_attempt,
                attempt_timeout_s=attempt_plan.timeout_s,
                time_in_force=attempt_plan.time_in_force,
                entry_fee_type=attempt_plan.entry_fee_type,
                confidence=pending.confidence,
                horizon_min=pending.horizon_min,
                signal_time=pending.signal_time,
                entry_snapshot=pending.entry_snapshot,
                dynamic_exit_enabled=pending.dynamic_exit_enabled,
            )
            self._pending_entries[new_order_id] = retry_pending

        logger.info(
            "[EXEC] Retry order placed %s %s price=%.2f qty=%.6f attempt=%s mode=%s tif=%s",
            signal_name,
            direction.upper(),
            retry_pending.requested_price,
            retry_pending.qty,
            next_attempt,
            attempt_plan.mode,
            attempt_plan.time_in_force,
        )
        self._thread_pool.submit(self._monitor_pending_entry, new_order_id)

    def _finalize_not_filled(self, order_id: str) -> None:
        with self._lock:
            pending = self._pending_entries.pop(order_id, None)
            if pending is None:
                return

        exit_time = datetime.now(timezone.utc)
        self.trade_logger.log_not_filled(
            signal_name=pending.signal_name,
            direction=pending.direction,
            entry_time=pending.signal_time,
            entry_price=pending.requested_price,
            exit_time=exit_time,
            qty=pending.qty,
            confidence=pending.confidence,
            horizon_min=pending.horizon_min,
            strategy_id=pending.family,
            raw_signal_name=str((pending.entry_snapshot or {}).get("raw_signal_name") or pending.signal_name),
        )
        cooldown_key = f"{pending.family}|{pending.direction}"
        self._signal_cooldown[cooldown_key] = time.time() + 180
        logger.info(
            "[EXEC] Order not filled %s order_id=%s (cooldown 3min)",
            pending.signal_name,
            pending.order_id,
        )

    def _promote_pending_to_position(self, order_id: str, status: dict[str, Any]) -> None:
        with self._lock:
            pending = self._pending_entries.get(order_id)
            if pending is None:
                return

            executed_qty = float(status.get("executed_qty") or status.get("orig_qty") or pending.qty)
            if executed_qty <= 0:
                del self._pending_entries[order_id]
                return

            avg_price = float(status.get("avg_price") or pending.requested_price)
            update_time = self._safe_int(status.get("update_time"), 0)
            entry_time = (
                datetime.fromtimestamp(update_time / 1000, tz=timezone.utc)
                if update_time > 0
                else datetime.now(timezone.utc)
            )
            pos_key = f"{pending.family}|{pending.direction}"
            self._open_positions[pos_key] = OpenPosition(
                signal_name=pending.signal_name,
                family=pending.family,
                direction=pending.direction,
                qty=executed_qty,
                entry_price=avg_price,
                confidence=pending.confidence,
                horizon_min=pending.horizon_min,
                entry_time=entry_time,
                exit_due_time=(
                    None if pending.dynamic_exit_enabled else entry_time + timedelta(minutes=pending.horizon_min)
                ),
                order_id=order_id,
                entry_snapshot=pending.entry_snapshot,
                runtime_state=build_runtime_state(),
                dynamic_exit_enabled=pending.dynamic_exit_enabled,
                entry_fee_type=pending.entry_fee_type,
                entry_regime=self._current_regime,
                entry_flow_type=self._current_flow,
                mechanism_type=(
                    pending.mechanism_type
                    or resolve_mechanism_type(pending.signal_name, pending.direction, family=pending.family)
                ),
            )
            del self._pending_entries[order_id]
            self._signal_cooldown.pop(pos_key, None)
            due_text = (
                self._open_positions[pos_key].exit_due_time.strftime("%H:%M:%S")
                if self._open_positions[pos_key].exit_due_time is not None
                else "dynamic"
            )

        logger.info(
            "[EXEC] Position opened %s entry=%.2f qty=%.6f due=%s UTC entry_fee=%s",
            pending.signal_name,
            avg_price,
            executed_qty,
            due_text,
            pending.entry_fee_type,
        )
        # 建仓成功后立刻在交易所挂止损单（防宕机灾难保护）
        # 失败只记录 warning，不影响仓位建立
        self._place_exchange_stop_order(pos_key, avg_price, pending.direction, executed_qty)
        self._save_positions_state()

    def _place_exchange_stop_order(
        self,
        pos_key: str,
        entry_price: float,
        direction: str,
        qty: float,
    ) -> None:
        """建仓后在交易所挂 STOP_MARKET 止损单（宕机防灾保护）。

        价格设为配置止损的 2 倍，作为极端宕机场景的最后防线：
        - 正常运行时，系统的内存止损会先触发
        - 宕机时，交易所端止损单在价格穿越 2x 止损时强制平仓
        失败只记录 warning，不影响仓位。
        """
        if self.order_manager is None:
            return

        with self._lock:
            pos = self._open_positions.get(pos_key)
            if pos is None:
                return

        try:
            exit_params = self._resolve_position_exit_params(pos)
            stop_pct = float(exit_params.stop_pct)
            # 2 倍止损作为宕机保护线（单边 %，除以 100 转换）
            stop_multiplier = 2.0
            if direction == "long":
                stop_price = entry_price * (1.0 - stop_pct * stop_multiplier / 100.0)
            else:
                stop_price = entry_price * (1.0 + stop_pct * stop_multiplier / 100.0)

            result = self.order_manager.place_stop_market(
                direction=direction,
                qty=qty,
                stop_price=stop_price,
            )

            if result.get("status") == "placed":
                stop_order_id = str(result.get("order_id", ""))
                with self._lock:
                    pos = self._open_positions.get(pos_key)
                    if pos is not None:
                        pos.exchange_stop_order_id = stop_order_id
                logger.info(
                    "[EXEC] Exchange stop order placed %s stop_price=%.2f stop_id=%s",
                    pos_key,
                    stop_price,
                    stop_order_id,
                )
            else:
                logger.warning(
                    "[EXEC] Exchange stop order placement failed %s: %s",
                    pos_key,
                    result,
                )
        except Exception as exc:
            logger.warning(
                "[EXEC] _place_exchange_stop_order error %s: %s",
                pos_key,
                exc,
            )

    def _handle_close_failure(
        self,
        position: OpenPosition,
        exit_reason: str,
        failure_reason: str,
    ) -> None:
        runtime_state = position.runtime_state
        attempts = int(runtime_state.get("close_attempts", 0) or 0) + 1
        runtime_state["close_attempts"] = attempts
        runtime_state["close_pending"] = True
        runtime_state["close_pending_reason"] = exit_reason
        runtime_state["close_last_error"] = failure_reason

        # 不从追踪中删除仓位，持续重试直到成功。
        # 历史教训：原逻辑在第 3 次失败后 pop() 删除仓位记录，导致仓位仍在
        # 交易所开着但系统再也不发平仓指令，累积大额亏损。
        # 正确兜底由 _collect_stale_exchange_keys_locked() 负责：若交易所
        # 确认仓位已消失，120s 宽限后自动清理本地记录。
        if attempts >= 3:
            logger.critical(
                "[EXEC][ALERT] Close failed %s attempt=%d, KEEP RETRYING next bar. last_error=%s",
                position.signal_name,
                attempts,
                failure_reason,
            )
        else:
            logger.warning(
                "[EXEC] Close attempt %d failed %s: %s; retry on next bar",
                attempts,
                position.signal_name,
                failure_reason,
            )
        self._save_positions_state()


    def _close_position(self, position: OpenPosition, exit_reason: str) -> None:
        if self.order_manager is None:
            return

        # Guard against double-close race: on_bar from consecutive bars may
        # both decide to close the same position while the first close is
        # still in flight (45s limit + market fallback).
        with self._lock:
            rs = position.runtime_state
            if rs.get("close_in_flight"):
                logger.debug(
                    "[EXEC] Skip duplicate close for %s (already in flight)",
                    position.signal_name,
                )
                return
            rs["close_in_flight"] = True

        try:
            result = self.order_manager.close_position(position.direction, position.qty)
        except OrderManagerError as exc:
            position.runtime_state["close_in_flight"] = False
            self._handle_close_failure(position, exit_reason, str(exc))
            return

        if result.get("status") != "closed":
            position.runtime_state["close_in_flight"] = False
            self._handle_close_failure(position, exit_reason, str(result))
            return

        # 平仓成功后立刻取消交易所端止损单，防止止损单在仓位已关闭后错误触发
        if position.exchange_stop_order_id:
            try:
                self.order_manager.cancel_order(position.exchange_stop_order_id)
                logger.info(
                    "[EXEC] Exchange stop order cancelled %s stop_id=%s",
                    position.signal_name,
                    position.exchange_stop_order_id,
                )
            except Exception as exc:
                logger.warning(
                    "[EXEC] Cancel exchange stop order failed %s stop_id=%s: %s",
                    position.signal_name,
                    position.exchange_stop_order_id,
                    exc,
                )

        # Verify no dust remnant left on exchange after close
        executed_qty = float(result.get("qty", 0.0) or 0.0)
        remnant = position.qty - executed_qty
        if remnant > 1e-6:
            logger.warning(
                "[EXEC] Dust remnant detected: %.6f BTC left after closing %s, sweeping...",
                remnant, position.signal_name,
            )
            try:
                self.order_manager.close_position(position.direction, remnant)
                logger.info("[EXEC] Dust remnant swept successfully")
            except Exception as exc:
                logger.warning("[EXEC] Dust sweep failed: %s (will be cleaned by external sync)", exc)

        exit_time_ms = self._safe_int(result.get("update_time"), 0)
        exit_time = (
            datetime.fromtimestamp(exit_time_ms / 1000, tz=timezone.utc)
            if exit_time_ms > 0
            else datetime.now(timezone.utc)
        )
        exit_price = result.get("avg_price")
        exit_fee_rate = config.fee_rate_for_type(result.get("fee_type"))
        total_fee_rate = config.fee_rate_for_type(position.entry_fee_type) + exit_fee_rate

        self.trade_logger.log_trade(
            signal_name=position.signal_name,
            direction=position.direction,
            entry_time=position.entry_time,
            entry_price=position.entry_price,
            exit_time=exit_time,
            exit_price=float(exit_price) if exit_price is not None else None,
            qty=position.qty,
            exit_reason=exit_reason,
            confidence=position.confidence,
            horizon_min=position.horizon_min,
            total_fee_rate=total_fee_rate,
            flow_type=position.entry_flow_type,
            regime=position.entry_regime,
            strategy_id=position.family,
            raw_signal_name=str((position.entry_snapshot or {}).get("raw_signal_name") or position.signal_name),
        )

        # Compute return metrics ONCE for all downstream consumers
        ep = float(exit_price) if exit_price is not None else position.entry_price
        gross_ret_pct = ((ep - position.entry_price) / position.entry_price) * 100
        if position.direction == "short":
            gross_ret_pct = -gross_ret_pct
        net_ret_pct = gross_ret_pct - total_fee_rate * 100

        # Record outcome for signal health tracking
        if self._signal_health is not None:
            try:
                card_id = position.signal_name
                sub_ids = [s.strip() for s in card_id.split(" | ")] if " | " in card_id else [card_id]
                for sid in sub_ids:
                    if sid:
                        self._signal_health.record_outcome(
                            card_id=sid,
                            direction=position.direction,
                            net_return_pct=net_ret_pct,
                            flow_type=position.entry_flow_type,
                            regime=position.entry_regime,
                        )
            except Exception as exc:
                logger.warning("[EXEC] Signal health record failed: %s", exc)

        # Feed outcome to adaptive cooldown for self-tuning
        if self._signal_runner is not None:
            try:
                is_win = net_ret_pct > 0
                group = str(position.entry_snapshot.get("group", position.family) if position.entry_snapshot else position.family)
                cooldown_key = f"{group}|{position.direction}|{position.horizon_min}"
                self._signal_runner.record_p2_outcome(cooldown_key, is_win)
            except Exception as exc:
                logger.warning("[EXEC] Adaptive cooldown feedback failed: %s", exc)

        # Conviction Engine: learn from closed trade (uses GROSS return to
        # match bar-level snapshots which record gross unrealized P&L)
        if self._conviction_engine is not None:
            try:
                pos_key_brain = f"{position.family}|{position.direction}"
                entry_snap = position.entry_snapshot or {}
                entry_features = entry_snap.get("entry_conviction_features") or [0.0] * 5
                self._conviction_engine.learn_from_trade(
                    pos_key=pos_key_brain,
                    entry_features=entry_features,
                    final_return_pct=gross_ret_pct,
                    family=position.family,
                    regime=position.entry_regime,
                )
            except Exception as exc:
                logger.warning("[BRAIN] learn_from_trade failed: %s", exc)

        with self._lock:
            pos_key = f"{position.family}|{position.direction}"
            self._open_positions.pop(pos_key, None)

        # Extended cooldown after adverse exits (hard_stop or mechanism decay).
        # Only applies to actual losses -- profitable hard_stop (MFE ratchet)
        # should not waste cooldown time.
        if exit_reason in _HARD_STOP_REASONS or exit_reason.startswith(_MECHANISM_DECAY_PREFIX):
            gross_ret_decimal = gross_ret_pct / 100.0
            is_actual_loss = gross_ret_decimal < _LOSS_THRESHOLD
            if is_actual_loss:
                cooldown_key = f"{position.family}|{position.direction}"
                cooldown_s = _ALPHA_LOSS_COOLDOWN_S if position.family.startswith("A") else _DECAY_COOLDOWN_S
                self._signal_cooldown[cooldown_key] = time.time() + cooldown_s
                logger.info(
                    "[EXEC] Loss/decay cooldown: %s blocked for %ds after %s (gross=%.4f%%)",
                    cooldown_key, cooldown_s, exit_reason, gross_ret_pct,
                )
            else:
                logger.info(
                    "[EXEC] %s exit reason=%s but gross=%.4f%% >= threshold, skipping cooldown",
                    position.signal_name, exit_reason, gross_ret_pct,
                )

        # After safety_cap on A2 cards, apply 10-min cooldown.
        if exit_reason in {"time_cap", "safety_cap"} and position.family.startswith("A2"):
            cooldown_key = f"{position.family}|{position.direction}"
            self._signal_cooldown[cooldown_key] = time.time() + _TIMECAP_COOLDOWN_S
            logger.info(
                "[EXEC] A2 safety_cap cooldown: %s blocked for 10min",
                cooldown_key,
            )

        logger.info(
            "[EXEC] Position closed %s reason=%s exit=%.2f",
            position.signal_name,
            exit_reason,
            float(exit_price) if exit_price is not None else 0.0,
        )
        self._save_positions_state()
    def _sync_external_position_locked(self) -> None:
        self._consume_orphan_flatten_result_locked()
        future = self._external_sync_future
        tracked_keys = self._external_sync_keys
        if future is not None and future.done():
            self._external_sync_future = None
            self._external_sync_keys = set()
            try:
                positions = future.result()
            except Exception as exc:
                self._external_sync_failures += 1
                logger.warning(
                    "[EXEC] Sync external positions failed (%d): %s",
                    self._external_sync_failures,
                    exc,
                )
            else:
                if self._external_sync_failures:
                    logger.info(
                        "[EXEC] External position sync recovered after %d failure(s)",
                        self._external_sync_failures,
                    )
                self._external_sync_failures = 0
                self._apply_external_positions_locked(positions, tracked_keys)

        if self.order_manager is None or self._pending_entries:
            return

        now = time.time()
        # If the sync future has been in flight for > 30s, it's stuck; discard it
        if self._external_sync_future is not None:
            if now - self._last_ext_sync_ts > 30:
                logger.warning(
                    "[EXEC] External sync future stuck >30s, discarding"
                )
                self._external_sync_future = None
                self._external_sync_keys = set()
            else:
                return

        if now - self._last_ext_sync_ts < 5:
            return
        self._last_ext_sync_ts = now
        self._external_sync_keys = {
            key for key, position in self._open_positions.items() if not position.external
        }
        self._external_sync_future = self._sync_pool.submit(
            self.order_manager.get_open_positions
        )

        # Piggyback: sync exchange trade fills every 30s
        if (self._exchange_syncer is not None
                and now - self._last_trade_sync_ts >= 30):
            self._last_trade_sync_ts = now
            self._sync_pool.submit(self._exchange_syncer.sync)

    @staticmethod
    def _position_age_seconds(position: OpenPosition) -> float:
        if position.entry_time is None:
            return float("inf")
        return max(
            0.0,
            (datetime.now(timezone.utc) - position.entry_time).total_seconds(),
        )

    def _collect_stale_exchange_keys_locked(
        self,
        exchange_qty: dict[str, float],
        tracked_keys: set[str],
    ) -> list[str]:
        """找出交易所已无对应仓位、但本地仍残留的幽灵仓位。"""
        qty_tolerance = 1e-6
        sync_grace_s = 120.0
        stale_keys: list[str] = []

        for direction in ("long", "short"):
            tracked_positions = [
                (pos_key, pos)
                for pos_key, pos in self._open_positions.items()
                if not pos.external
                and pos_key in tracked_keys
                and pos.direction == direction
            ]
            if not tracked_positions:
                continue

            tracked_positions.sort(
                key=lambda item: item[1].entry_time or datetime.min.replace(tzinfo=timezone.utc),
                reverse=True,
            )
            remaining_exchange_qty = max(
                0.0,
                float(exchange_qty.get(direction, 0.0) or 0.0),
            )

            for pos_key, pos in tracked_positions:
                if remaining_exchange_qty + qty_tolerance >= pos.qty:
                    remaining_exchange_qty = max(0.0, remaining_exchange_qty - pos.qty)
                    continue

                age_s = self._position_age_seconds(pos)
                if age_s < sync_grace_s:
                    logger.debug(
                        "[EXEC] Keep %s despite exchange qty gap %.6f < %.6f within %.0fs grace",
                        pos_key,
                        remaining_exchange_qty,
                        pos.qty,
                        sync_grace_s,
                    )
                    continue

                stale_keys.append(pos_key)

        return stale_keys

    def _apply_external_positions_locked(
        self,
        positions: list[dict[str, Any]],
        tracked_keys: set[str],
    ) -> None:
        ext_key = "external|any"
        ext_position = self._open_positions.get(ext_key)
        system_qty: dict[str, float] = {"long": 0.0, "short": 0.0}
        for position in self._open_positions.values():
            if not position.external and position.direction in system_qty:
                system_qty[position.direction] += position.qty

        exchange_qty: dict[str, float] = {"long": 0.0, "short": 0.0}
        for payload in positions:
            direction = str(payload.get("direction", "")).lower()
            if direction in exchange_qty:
                exchange_qty[direction] += float(payload.get("qty", 0.0))

        exchange_entry_price: dict[str, float] = {"long": 0.0, "short": 0.0}
        for payload in positions:
            direction = str(payload.get("direction", "")).lower()
            if direction in exchange_entry_price:
                ep = float(payload.get("entry_price", 0.0) or 0.0)
                if ep > 0:
                    exchange_entry_price[direction] = ep

        has_external = any(
            exchange_qty[direction] > system_qty[direction] + 1e-6
            for direction in ("long", "short")
        )

        ext_dir = "long"
        ext_qty = 0.0
        if has_external:
            for direction in ("long", "short"):
                surplus = exchange_qty[direction] - system_qty[direction]
                if surplus > 1e-6:
                    ext_dir = direction
                    ext_qty = surplus
                    break

            _DUST_THRESHOLD_BTC = 0.01
            if ext_qty < _DUST_THRESHOLD_BTC:
                logger.warning(
                    "[EXEC] Dust external position detected (%s %.6f BTC), ignoring for sync gate",
                    ext_dir,
                    ext_qty,
                )
                has_external = False

        if has_external and ext_position is None:
            ext_position = OpenPosition(
                signal_name="external_position",
                family="external",
                direction=ext_dir,
                qty=ext_qty,
                entry_price=exchange_entry_price.get(ext_dir, 0.0),
                confidence=0,
                horizon_min=0,
                entry_time=datetime.now(timezone.utc),
                exit_due_time=None,
                order_id="external",
                entry_snapshot={},
                runtime_state=build_runtime_state(),
                dynamic_exit_enabled=False,
                external=True,
            )
            self._open_positions[ext_key] = ext_position
            logger.warning(
                "[EXEC] Detected external position (%s %.6f), blocking new entries",
                ext_dir,
                ext_qty,
            )
        elif has_external and ext_position is not None:
            if ext_position.direction != ext_dir:
                ext_position.entry_time = datetime.now(timezone.utc)
            ext_position.direction = ext_dir
            ext_position.qty = ext_qty
            ext_entry_price = exchange_entry_price.get(ext_dir, 0.0)
            if ext_entry_price > 0:
                ext_position.entry_price = ext_entry_price

        if has_external and ext_position is not None:
            self._maybe_schedule_orphan_flatten_locked(
                ext_position.direction,
                ext_position.qty,
                ext_position.entry_time,
            )
        elif not has_external and ext_key in self._open_positions:
            logger.info("[EXEC] External position cleared")
            del self._open_positions[ext_key]

        if not self._pending_entries:
            stale_keys = self._collect_stale_exchange_keys_locked(exchange_qty, tracked_keys)

            for pos_key in stale_keys:
                pos = self._open_positions.pop(pos_key)
                held_min = (
                    int(self._position_age_seconds(pos) // 60)
                    if pos.entry_time else 0
                )
                logger.warning(
                    "[EXEC] Position %s vanished from exchange sync, removed from tracking. "
                    "entry=%.2f qty=%.6f held=%s min",
                    pos_key, pos.entry_price, pos.qty, held_min,
                )
                self._arm_manual_intervention_cooldown(
                    family=pos.family,
                    direction=pos.direction,
                    reason="manual_close_exchange",
                )
                # Log to trades.csv so manual closes appear in UI trade history
                if self.trade_logger is not None:
                    try:
                        self.trade_logger.log_trade(
                            signal_name=pos.signal_name,
                            direction=pos.direction,
                            entry_time=pos.entry_time or datetime.now(timezone.utc),
                            entry_price=pos.entry_price,
                            exit_time=datetime.now(timezone.utc),
                            exit_price=None,
                            qty=pos.qty,
                            exit_reason="manual_close_exchange",
                            confidence=pos.confidence,
                            horizon_min=pos.horizon_min,
                            total_fee_rate=config.fee_rate_for_type(pos.entry_fee_type),
                            flow_type=pos.entry_flow_type,
                            regime=pos.entry_regime,
                            strategy_id=pos.family,
                            raw_signal_name=str(
                                (pos.entry_snapshot or {}).get("raw_signal_name")
                                or pos.signal_name
                            ),
                        )
                    except Exception as exc:
                        logger.warning("[EXEC] Failed to log vanished position: %s", exc)
            if stale_keys:
                self._save_positions_state()



    def _consume_orphan_flatten_result_locked(self) -> None:
        future = self._orphan_flatten_future
        if future is None or not future.done():
            return

        meta = dict(self._orphan_flatten_meta or {})
        direction = str(meta.get("direction", "unknown"))
        qty = float(meta.get("qty", 0.0) or 0.0)
        self._orphan_flatten_future = None
        self._orphan_flatten_meta = None

        try:
            result = future.result()
        except Exception as exc:
            self._orphan_flatten_attempts += 1
            logger.critical(
                "[EXEC][ALERT] Orphan auto-flatten failed (%s %.6f): %s",
                direction,
                qty,
                exc,
            )
            return

        if result.get("status") == "closed":
            self._orphan_flatten_attempts = 0
            ext_pos = self._open_positions.get("external|any")
            if ext_pos is not None and self.trade_logger is not None:
                try:
                    exit_ts_ms = self._safe_int(result.get("update_time"), 0)
                    exit_time = (
                        datetime.fromtimestamp(exit_ts_ms / 1000, tz=timezone.utc)
                        if exit_ts_ms > 0
                        else datetime.now(timezone.utc)
                    )
                    exit_price = self._safe_float(result.get("avg_price"), None)
                    self.trade_logger.log_trade(
                        signal_name=ext_pos.signal_name,
                        direction=ext_pos.direction,
                        entry_time=ext_pos.entry_time or datetime.now(timezone.utc),
                        entry_price=float(ext_pos.entry_price or 0.0),
                        exit_time=exit_time,
                        exit_price=exit_price,
                        qty=float(result.get("qty") or ext_pos.qty or qty),
                        exit_reason="orphan_auto_flatten",
                        confidence=ext_pos.confidence,
                        horizon_min=ext_pos.horizon_min,
                        total_fee_rate=config.fee_rate_for_type(
                            str(result.get("fee_type") or ext_pos.entry_fee_type)
                        ),
                        flow_type=ext_pos.entry_flow_type,
                        regime=ext_pos.entry_regime,
                        strategy_id=ext_pos.family,
                        raw_signal_name=ext_pos.signal_name,
                    )
                except Exception as exc:
                    logger.warning("[EXEC] Failed to log orphan auto-flatten: %s", exc)
            logger.warning(
                "[EXEC][RESET] Orphan exchange position flattened (%s %.6f); waiting for sync confirmation",
                direction,
                qty,
            )
            return

        self._orphan_flatten_attempts += 1
        logger.critical(
            "[EXEC][ALERT] Orphan auto-flatten rejected (%s %.6f): %s",
            direction,
            qty,
            result,
        )

    def _maybe_schedule_orphan_flatten_locked(
        self,
        direction: str,
        qty: float,
        detected_at: datetime | None = None,
    ) -> bool:
        if self.order_manager is None or not self.enabled:
            return False
        if self._pending_entries or self._orphan_flatten_future is not None:
            return False
        if self._orphan_flatten_attempts >= 3:
            return False
        if any(not pos.external for pos in self._open_positions.values()):
            return False

        reconnect_eligible = (
            bool(config.AUTO_FLATTEN_ORPHAN_ON_RECONNECT)
            and self._booted_without_order_manager
            and time.time() <= self._orphan_recovery_deadline_ts
        )
        persistent_eligible = False
        if (
            not reconnect_eligible
            and bool(getattr(config, "AUTO_FLATTEN_PERSISTENT_EXTERNAL", False))
        ):
            persistent_after_s = float(
                getattr(config, "PERSISTENT_EXTERNAL_FLATTEN_AFTER_S", 0.0) or 0.0
            )
            if persistent_after_s > 0:
                if detected_at is None:
                    external_age_s = float("inf")
                else:
                    external_age_s = max(
                        0.0,
                        (datetime.now(timezone.utc) - detected_at).total_seconds(),
                    )
                persistent_eligible = external_age_s >= persistent_after_s

        if not reconnect_eligible and not persistent_eligible:
            return False

        self._orphan_flatten_meta = {"direction": direction, "qty": float(qty)}
        self._orphan_flatten_future = self._sync_pool.submit(
            self.order_manager.close_position,
            direction,
            qty,
        )
        if reconnect_eligible:
            logger.critical(
                "[EXEC][RESET] Untracked exchange position detected after reconnect (%s %.6f); auto-flattening orphan position",
                direction,
                qty,
            )
        else:
            logger.critical(
                "[EXEC][RESET] Persistent untracked exchange position detected (%s %.6f); auto-flattening orphan position",
                direction,
                qty,
            )
        return True

    @staticmethod
    def _extract_alert_time(alert: dict[str, Any]) -> datetime:
        ts_ms = ExecutionEngine._safe_int(alert.get("timestamp_ms"), 0)
        if ts_ms > 0:
            return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return datetime.now(timezone.utc)

    @staticmethod
    def _extract_bar_time(latest_features: Any) -> datetime:
        ts_ms = 0
        getter = getattr(latest_features, "get", None)
        if callable(getter):
            ts_ms = ExecutionEngine._safe_int(getter("timestamp", 0), 0)
        elif isinstance(latest_features, dict):
            ts_ms = ExecutionEngine._safe_int(latest_features.get("timestamp", 0), 0)
        if ts_ms > 0:
            return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
        return datetime.now(timezone.utc)

    @staticmethod
    def _extract_close(latest_features: Any) -> float | None:
        getter = getattr(latest_features, "get", None)
        close = None
        if callable(getter):
            close = getter("close", None)
        elif isinstance(latest_features, dict):
            close = latest_features.get("close", None)
        try:
            if close is None:
                return None
            return float(close)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _extract_feature_float(latest_features: Any, name: str) -> float | None:
        getter = getattr(latest_features, "get", None)
        value = None
        if callable(getter):
            value = getter(name, None)
        elif isinstance(latest_features, dict):
            value = latest_features.get(name, None)
        try:
            if value is None:
                return None
            result = float(value)
        except (TypeError, ValueError):
            return None
        if result != result:
            return None
        return result

    @staticmethod
    def _safe_int(value: Any, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    # ------------------------------------------------------------------
    # Position state persistence (survive monitor restarts)
    # ------------------------------------------------------------------

    def _save_positions_state(self) -> None:
        """Write all non-external open positions to disk."""
        try:
            snapshot: dict[str, Any] = {}
            now_ts = time.time()
            with self._lock:
                persisted_cooldowns = {
                    key: float(until_ts)
                    for key, until_ts in self._signal_cooldown.items()
                    if float(until_ts or 0.0) > now_ts
                }
                for key, pos in self._open_positions.items():
                    if pos.external:
                        continue
                    snapshot[key] = {
                        "signal_name": pos.signal_name,
                        "family": pos.family,
                        "direction": pos.direction,
                        "qty": pos.qty,
                        "entry_price": pos.entry_price,
                        "confidence": pos.confidence,
                        "horizon_min": pos.horizon_min,
                        "entry_time": pos.entry_time.isoformat(),
                        "exit_due_time": (
                            pos.exit_due_time.isoformat()
                            if pos.exit_due_time is not None
                            else None
                        ),
                        "order_id": pos.order_id,
                        "entry_snapshot": pos.entry_snapshot,
                        "runtime_state": pos.runtime_state,
                        "dynamic_exit_enabled": pos.dynamic_exit_enabled,
                        "entry_fee_type": pos.entry_fee_type,
                        "entry_regime": pos.entry_regime,
                        "entry_flow_type": pos.entry_flow_type,
                        "mechanism_type": pos.mechanism_type,
                        "exchange_stop_order_id": pos.exchange_stop_order_id,
                    }
                for key, data in self._pending_restore_state.items():
                    snapshot.setdefault(key, dict(data))
            payload = {
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "positions": snapshot,
                "cooldowns": persisted_cooldowns,
            }
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            write_json_atomic(self._state_file, payload, indent=2, default=str)
        except Exception as exc:
            logger.warning("[EXEC] Failed to save positions state: %s", exc)

    def _restore_positions_from_state(self) -> None:
        """On startup, recover positions from disk and verify against exchange."""
        if not self._state_file.exists():
            self._pending_restore_state = {}
            return
        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[EXEC] Cannot read positions state file: %s", exc)
            return

        saved: dict[str, Any] = raw.get("positions", {})
        saved_cooldowns = raw.get("cooldowns", {})
        now_ts = time.time()
        if isinstance(saved_cooldowns, dict):
            with self._lock:
                for key, until_ts in saved_cooldowns.items():
                    try:
                        until = float(until_ts)
                    except (TypeError, ValueError):
                        continue
                    if until > now_ts:
                        self._signal_cooldown[str(key)] = max(
                            float(self._signal_cooldown.get(str(key), 0.0) or 0.0),
                            until,
                        )
                expired_keys = [
                    key for key, until_ts in self._signal_cooldown.items()
                    if float(until_ts or 0.0) <= now_ts
                ]
                for key in expired_keys:
                    self._signal_cooldown.pop(key, None)
        if not saved:
            self._pending_restore_state = {}
            return

        if not self.enabled or self.order_manager is None:
            self._pending_restore_state = dict(saved)
            logger.warning(
                "[EXEC] Restore deferred for %d position(s): execution unavailable; keeping snapshot until reconnect",
                len(saved),
            )
            return

        exchange_qty: dict[str, float] = {"long": 0.0, "short": 0.0}
        try:
            for p in self.order_manager.get_open_positions():
                d = str(p.get("direction", "")).lower()
                if d in exchange_qty:
                    exchange_qty[d] += float(p.get("qty", 0.0))
        except Exception as exc:
            logger.warning(
                "[EXEC] Cannot verify exchange positions during restore: %s", exc
            )
            self._pending_restore_state = dict(saved)
            return

        restored = 0
        state_changed = False
        restored_keys: set[str] = set()
        with self._lock:
            existing_positions = {
                key: pos.qty
                for key, pos in self._open_positions.items()
                if not pos.external
            }

        for key, data in sorted(
            saved.items(),
            key=lambda item: str((item[1] or {}).get("entry_time", "")),
        ):
            direction = str(data.get("direction", "")).lower()
            qty = float(data.get("qty", 0.0))
            if direction not in exchange_qty or qty <= 0:
                logger.warning("[EXEC] Restore skip %s: invalid state payload", key)
                state_changed = True
                continue

            existing_qty = float(existing_positions.get(key, 0.0) or 0.0)
            if existing_qty > 0:
                exchange_qty[direction] = max(0.0, exchange_qty[direction] - existing_qty)
                restored_keys.add(key)
                continue

            # Skip if the exchange no longer holds this position
            if exchange_qty.get(direction, 0.0) < qty - 1e-6:
                logger.warning(
                    "[EXEC][ALERT] Restore skip %s: saved qty %.6f not found on exchange; pruning stale snapshot",
                    key,
                    qty,
                )
                self._arm_manual_intervention_cooldown(
                    family=str(data.get("family", key.split("|", 1)[0])),
                    direction=direction,
                    reason="restore_exchange_mismatch",
                )
                state_changed = True
                continue
            exchange_qty[direction] = max(0.0, exchange_qty[direction] - qty)

            try:
                entry_time = datetime.fromisoformat(str(data["entry_time"]))
                exit_due_raw = data.get("exit_due_time")
                exit_due_time = (
                    datetime.fromisoformat(str(exit_due_raw))
                    if exit_due_raw
                    else None
                )
                pos = OpenPosition(
                    signal_name=str(data["signal_name"]),
                    family=str(data["family"]),
                    direction=direction,
                    qty=qty,
                    entry_price=float(data["entry_price"]),
                    confidence=int(data.get("confidence", 1)),
                    horizon_min=int(data.get("horizon_min", 30)),
                    entry_time=entry_time,
                    exit_due_time=exit_due_time,
                    order_id=str(data.get("order_id", "")),
                    entry_snapshot=dict(data.get("entry_snapshot") or {}),
                    runtime_state=dict(data.get("runtime_state") or build_runtime_state()),
                    dynamic_exit_enabled=bool(data.get("dynamic_exit_enabled", False)),
                    entry_fee_type=str(data.get("entry_fee_type", "maker")),
                    entry_regime=str(data.get("entry_regime", "")),
                    entry_flow_type=str(data.get("entry_flow_type", "")),
                    mechanism_type=(
                        lambda _mt, _fam: (
                            get_mechanism_for_family(_fam) if _mt == "generic_alpha" and _fam else _mt
                        ) or get_mechanism_for_family(_fam)
                    )(str(data.get("mechanism_type", "")), str(data.get("family", ""))),
                    exchange_stop_order_id=str(data.get("exchange_stop_order_id", "")),
                )
                with self._lock:
                    self._open_positions[key] = pos
                restored += 1
                restored_keys.add(key)
                logger.info(
                    "[EXEC] Restored %s entry=%.2f qty=%.6f regime=%s",
                    key, pos.entry_price, pos.qty, pos.entry_regime,
                )
            except Exception as exc:
                logger.warning("[EXEC] Failed to restore %s: %s", key, exc)
                state_changed = True

        self._pending_restore_state = {}
        if restored:
            logger.info(
                "[EXEC] State recovery complete: %d position(s) restored from %s",
                restored,
                self._state_file,
            )
            # 重启后立刻做硬止损预检：对每笔还原的仓位，用当前市价判断
            # 是否已超出止损线。超过则标记 close_pending，下一个 on_bar()
            # 触发时立刻平仓，不必等待下一根 K 线到达。
            self._mark_overdue_stops_after_restore()
        if state_changed or restored_keys != set(saved):
            self._save_positions_state()

    def _mark_overdue_stops_after_restore(self) -> None:
        """重启还原后立刻用当前市价做硬止损预检。

        若某仓位的逆势幅度已超过配置的 stop_pct，立刻标记 close_pending，
        确保下一个 on_bar() 调用时第一时间平仓，不因重启延误止损。
        """
        if self.order_manager is None:
            return
        try:
            close_price = self._reference_price_from_book(
                self.order_manager.get_book_ticker()
            )
        except Exception as exc:
            logger.warning("[EXEC] Restore stop-check: cannot get book ticker: %s", exc)
            return
        if close_price <= 0:
            return

        with self._lock:
            positions_snapshot = list(self._open_positions.values())

        overdue_positions: list[OpenPosition] = []
        for pos in positions_snapshot:
            if pos.external or not pos.dynamic_exit_enabled:
                continue
            entry = pos.entry_price
            if entry <= 0:
                continue
            if pos.direction == "short":
                adverse_pct = max(0.0, (close_price - entry) / entry * 100)
            else:
                adverse_pct = max(0.0, (entry - close_price) / entry * 100)

            params = self._resolve_position_exit_params(pos)
            rs = pos.runtime_state
            rs["confidence"] = int(rs.get("confidence", pos.confidence) or pos.confidence)
            rs["entry_regime"] = str(
                rs.get("entry_regime", pos.entry_regime or "RANGE_BOUND")
                or pos.entry_regime
                or "RANGE_BOUND"
            )
            effective_stop = resolve_effective_stop_pct(
                position={"direction": pos.direction, "entry_price": pos.entry_price},
                runtime_state=rs,
                params=params,
            )
            if adverse_pct >= effective_stop:
                rs["close_pending"] = True
                rs["close_pending_reason"] = "hard_stop"
                overdue_positions.append(pos)
                logger.critical(
                    "[EXEC][RESTORE] %s adverse=%.3f%% >= live_stop=%.3f%%; attempting immediate hard stop",
                    pos.signal_name,
                    adverse_pct,
                    effective_stop,
                )

        if overdue_positions:
            self._save_positions_state()
            for pos in overdue_positions:
                self._close_position(pos, exit_reason="hard_stop")


