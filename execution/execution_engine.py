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
from execution.order_manager import OrderManager, OrderManagerError
from execution.trade_logger import TradeLogger
from monitor.mechanism_tracker import MechanismTracker, resolve_mechanism_type
from monitor.live_catalog import EXECUTION_WHITELIST
from monitor.exit_policy_config import ExitParams, get_exit_params_for_signal, has_explicit_exit_params
from monitor.smart_exit_policy import (
    build_entry_snapshot,
    build_runtime_state,
    evaluate_exit_action,
    normalize_family,
    update_mfe_mae,
)

logger = logging.getLogger(__name__)


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
        self._thread_pool = ThreadPoolExecutor(
            max_workers=max_positions,
            thread_name_prefix="entry-monitor",
        )
        self._mechanism_tracker = MechanismTracker()
        self._current_regime: str = "QUIET_TREND"
        self._current_flow: str = "PASSIVE"
        # A2 family 1-bar entry confirmation: key="{family}|{direction}" → first_seen_ts
        self._entry_confirm_pending: dict[str, float] = {}

        # Persist open positions across restarts
        self._state_file = Path("execution/logs/positions_state.json")
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

    def update_market_state(self, regime: str, flow_type: str) -> None:
        """Receive current market state from signal_runner."""
        self._current_regime = regime
        self._current_flow = flow_type

    def on_signal(self, alert: dict[str, Any], latest_features: Any | None = None) -> None:
        if not self.enabled or self.order_manager is None:
            return

        direction = str(alert.get("direction", "")).lower()
        if direction not in {"long", "short"}:
            return

        confidence = self._safe_int(alert.get("confidence"), 1)
        if confidence < self.min_confidence:
            logger.debug(
                "[EXEC] Skip low-confidence %s conf=%s < %s",
                alert.get("name"),
                confidence,
                self.min_confidence,
            )
            return

        signal_name = str(alert.get("name", ""))
        family = self._resolve_signal_family(alert)
        is_alpha = self._is_alpha_alert(alert, family)

        # Block LONG entries during LIQUIDATION flow -- forced selling creates
        # hostile conditions for longs (dead-cat bounce risk).
        if direction == "long" and self._current_flow == "LIQUIDATION":
            logger.info(
                "[EXEC] Skip %s: LONG blocked during LIQUIDATION flow", signal_name
            )
            return

        # A2-26/A2-29 both require HIGH confidence (conf>=3, dual physical confirm).
        # At conf=2 both fire too often in QUIET_TREND; 15h audit showed 22% WR at conf=2.
        if family in {"A2-26", "A2-29"} and confidence < 3:
            logger.debug(
                "[EXEC] Skip %s %s: conf=%s < 3 (require HIGH for A2 family)",
                family, signal_name, confidence,
            )
            return

        # All P2 alpha SHORT in QUIET_TREND require HIGH confidence (conf>=3).
        # Data: 42 QUIET_TREND trades had WR=38% avg=-0.022% (shorting into uptrend).
        # conf=3 subset: WR=60% avg=+0.19%.  conf=2 subset: WR=44% avg=-0.026%.
        if (
            is_alpha
            and direction == "short"
            and self._current_regime == "QUIET_TREND"
            and confidence < 3
        ):
            logger.info(
                "[EXEC] Skip %s: alpha SHORT in QUIET_TREND requires conf>=3 (got %s)",
                signal_name, confidence,
            )
            return

        if not is_alpha and (family, direction) not in EXECUTION_WHITELIST:
            logger.debug("[EXEC] Rejected %s %s: not in whitelist", family, direction)
            return

        cooldown_key = f"{family}|{direction}"
        now_ts = time.time()
        cooldown_until = self._signal_cooldown.get(cooldown_key, 0.0)
        if now_ts < cooldown_until:
            remaining = int(cooldown_until - now_ts)
            logger.info("[EXEC] Skip %s: execution cooldown (%ss remaining)", signal_name, remaining)
            return

        # A2 family: require 1 bar (≥55s) of continuous confirmation before entering.
        # Filters trades #49/50/51 pattern: instant-reversal entries where the
        # signal fired on 1 bar and price reversed within the same candle.
        if family.startswith("A2"):
            confirm_key = f"{family}|{direction}"
            first_seen = self._entry_confirm_pending.get(confirm_key)
            if first_seen is None:
                self._entry_confirm_pending[confirm_key] = now_ts
                logger.debug("[EXEC] A2 entry deferred %s: waiting 1-bar confirm", signal_name)
                return
            elapsed = now_ts - first_seen
            if elapsed < 55:
                logger.debug(
                    "[EXEC] A2 entry deferred %s: %ds < 55s confirm window",
                    signal_name, int(elapsed),
                )
                return
            if elapsed > 180:
                # Stale (>3 bars without a clean repeat): reset the timer
                self._entry_confirm_pending[confirm_key] = now_ts
                logger.debug("[EXEC] A2 entry confirm expired %s, resetting timer", signal_name)
                return
            # 55s ≤ elapsed ≤ 180s: condition persisted across ≥1 bar → proceed
            del self._entry_confirm_pending[confirm_key]
            logger.info(
                "[EXEC] A2 entry confirmed %s after %ds (1-bar filter passed)",
                signal_name, int(elapsed),
            )

        horizon_min = max(1, self._safe_int(alert.get("horizon"), 1))
        signal_time = self._extract_alert_time(alert)
        entry_snapshot = build_entry_snapshot(alert, latest_features)
        if latest_features is None:
            logger.warning(
                "[EXEC] %s: no live features; dynamic exit may fall back to stop/time only",
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
            if is_alpha:
                logger.warning(
                    "[EXEC] Rejected %s: alpha card has no dedicated exit feature",
                    signal_name,
                )
            else:
                logger.warning(
                    "[EXEC] Rejected %s: no exit params configured for %s|%s",
                    signal_name,
                    family,
                    direction,
                )
            return

        with self._lock:
            self._sync_external_position_locked()
            if any(position.external for position in self._open_positions.values()):
                logger.info("[EXEC] Skip %s: external|any already active", signal_name)
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

            try:
                attempt_plan = self._build_entry_attempt(direction, attempt=1)
                # QUIET_TREND uses a smaller position (5% vs 8% default).
                # 11 of 12 losses in the 15h audit occurred in QUIET_TREND;
                # same stop % × smaller notional = smaller dollar loss per trade.
                _QUIET_POSITION_PCT = 0.05
                effective_pct = (
                    _QUIET_POSITION_PCT
                    if self._current_regime == "QUIET_TREND"
                    else self.position_pct
                )
                if effective_pct != self.position_pct:
                    logger.debug(
                        "[EXEC] %s regime=%s: position_pct %.0f%% → %.0f%%",
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

            pending = PendingEntry(
                order_id=str(result.get("order_id", "")),
                signal_name=signal_name,
                family=family,
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
        if not isinstance(payload, dict):
            return None
        try:
            return ExitParams(
                take_profit_pct=float(payload.get("take_profit_pct", 0.0)),
                stop_pct=float(payload.get("stop_pct", 0.70)),
                protect_start_pct=float(payload.get("protect_start_pct", 0.12)),
                protect_gap_ratio=float(payload.get("protect_gap_ratio", 0.50)),
                protect_floor_pct=float(payload.get("protect_floor_pct", 0.03)),
                min_hold_bars=int(payload.get("min_hold_bars", 1)),
                max_hold_factor=int(payload.get("max_hold_factor", 4)),
                exit_confirm_bars=int(payload.get("exit_confirm_bars", 1)),
            )
        except (TypeError, ValueError):
            return None

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
            if not exit_conditions and not exit_combos:
                return False, None

            stop_pct = self._safe_float(
                alert.get("stop_pct"),
                default=self._safe_float(entry_snapshot.get("alpha_stop_pct"), 0.70),
            )
            # Prefer best_params.json values (protect thresholds, decay config, etc.)
            # when an explicit entry for this family+direction exists.
            # Only stop_pct is overridden from the alpha card definition itself.
            if has_explicit_exit_params(family, direction):
                _base = get_exit_params_for_signal(family, direction)
                params = ExitParams(
                    stop_pct=float(stop_pct or _base.stop_pct),
                    protect_start_pct=_base.protect_start_pct,
                    protect_gap_ratio=_base.protect_gap_ratio,
                    protect_floor_pct=_base.protect_floor_pct,
                    min_hold_bars=_base.min_hold_bars,
                    max_hold_factor=_base.max_hold_factor,
                    exit_confirm_bars=_base.exit_confirm_bars,
                    decay_exit_threshold=_base.decay_exit_threshold,
                    decay_tighten_threshold=_base.decay_tighten_threshold,
                )
            else:
                params = ExitParams(
                    stop_pct=float(stop_pct or 0.70),
                    protect_start_pct=0.12,
                    protect_gap_ratio=0.50,
                    protect_floor_pct=0.03,
                    min_hold_bars=5,
                    max_hold_factor=4,
                    exit_confirm_bars=1,
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

            if position.dynamic_exit_enabled and close_price is not None:
                runtime_state = position.runtime_state
                runtime_state["bars_held"] = int(runtime_state.get("bars_held", 0) or 0) + 1
                update_mfe_mae(
                    runtime_state,
                    {"entry_price": position.entry_price, "direction": position.direction},
                    close_price,
                )
                # Mechanism lifecycle evaluation
                decay_result = self._mechanism_tracker.evaluate_decay(
                    mechanism_type=position.mechanism_type or "generic_alpha",
                    entry_snapshot=position.entry_snapshot or {},
                    current_features=latest_features,
                    entry_regime=position.entry_regime,
                    current_regime=self._current_regime,
                )
                # Pass decay info into runtime_state
                runtime_state["decay_score"] = decay_result.decay_score
                runtime_state["decay_action"] = decay_result.recommended_action
                runtime_state["decay_reason"] = decay_result.reason
                decision = evaluate_exit_action(
                    position={
                        "rule": position.signal_name,
                        "family": position.family,
                        "direction": position.direction,
                        "entry_price": position.entry_price,
                        "hold_bars": position.horizon_min,
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
        )
        cooldown_key = f"{pending.family}|{pending.direction}"
        self._signal_cooldown[cooldown_key] = time.time() + 300
        logger.info(
            "[EXEC] Order not filled %s order_id=%s (cooldown 5min)",
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
                mechanism_type=resolve_mechanism_type(pending.signal_name, pending.direction),
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
        self._save_positions_state()

    def _close_position(self, position: OpenPosition, exit_reason: str) -> None:
        if self.order_manager is None:
            return

        try:
            result = self.order_manager.close_position(position.direction, position.qty)
        except OrderManagerError as exc:
            logger.warning("[EXEC] Close failed %s: %s", position.signal_name, exc)
            return

        if result.get("status") != "closed":
            logger.warning("[EXEC] Close rejected %s: %s", position.signal_name, result)
            return

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
        )

        with self._lock:
            pos_key = f"{position.family}|{position.direction}"
            self._open_positions.pop(pos_key, None)

        # Extended cooldown after adverse exits on alpha cards.
        # Prevents loss-loops where a "sticky" condition keeps re-entering.
        _LOSS_EXIT_REASONS = {"hard_stop", "mechanism_decay_generic_alpha"}
        _ALPHA_LOSS_COOLDOWN_S = 600  # 10 minutes
        if (
            exit_reason in _LOSS_EXIT_REASONS
            and position.family.startswith("A2")
        ):
            cooldown_key = f"{position.family}|{position.direction}"
            self._signal_cooldown[cooldown_key] = time.time() + _ALPHA_LOSS_COOLDOWN_S
            logger.info(
                "[EXEC] Alpha loss cooldown: %s blocked for %dmin after %s",
                cooldown_key, _ALPHA_LOSS_COOLDOWN_S // 60, exit_reason,
            )

        # After time_cap on A2 cards, apply 30-min cooldown.
        # If 60 bars elapsed without the trend materialising the setup is
        # structurally exhausted; immediate re-entry risks the same dead market.
        _TIMECAP_COOLDOWN_S = 900  # 15 minutes
        if exit_reason == "time_cap" and position.family.startswith("A2"):
            cooldown_key = f"{position.family}|{position.direction}"
            self._signal_cooldown[cooldown_key] = time.time() + _TIMECAP_COOLDOWN_S
            logger.info(
                "[EXEC] A2 time_cap cooldown: %s blocked for 30min",
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
        if self.order_manager is None or self._pending_entries:
            return

        try:
            positions = self.order_manager.get_open_positions()
        except OrderManagerError as exc:
            logger.warning("[EXEC] Sync external positions failed: %s", exc)
            return

        ext_key = "external|any"
        system_qty: dict[str, float] = {"long": 0.0, "short": 0.0}
        for position in self._open_positions.values():
            if not position.external and position.direction in system_qty:
                system_qty[position.direction] += position.qty

        exchange_qty: dict[str, float] = {"long": 0.0, "short": 0.0}
        for payload in positions:
            direction = str(payload.get("direction", "")).lower()
            if direction in exchange_qty:
                exchange_qty[direction] += float(payload.get("qty", 0.0))

        has_external = any(
            exchange_qty[direction] > system_qty[direction] + 1e-6
            for direction in ("long", "short")
        )

        if has_external and ext_key not in self._open_positions:
            ext_dir = "long"
            ext_qty = 0.0
            for direction in ("long", "short"):
                surplus = exchange_qty[direction] - system_qty[direction]
                if surplus > 1e-6:
                    ext_dir = direction
                    ext_qty = surplus
                    break
            self._open_positions[ext_key] = OpenPosition(
                signal_name="external_position",
                family="external",
                direction=ext_dir,
                qty=ext_qty,
                entry_price=0.0,
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
            logger.warning(
                "[EXEC] Detected external position (%s %.6f), blocking new entries",
                ext_dir,
                ext_qty,
            )
        elif not has_external and ext_key in self._open_positions:
            logger.info("[EXEC] External position cleared")
            del self._open_positions[ext_key]

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
            with self._lock:
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
                    }
            payload = {
                "saved_at": datetime.now(timezone.utc).isoformat(),
                "positions": snapshot,
            }
            self._state_file.parent.mkdir(parents=True, exist_ok=True)
            self._state_file.write_text(
                json.dumps(payload, indent=2, default=str),
                encoding="utf-8",
            )
        except Exception as exc:
            logger.warning("[EXEC] Failed to save positions state: %s", exc)

    def _restore_positions_from_state(self) -> None:
        """On startup, recover positions from disk and verify against exchange."""
        if not self._state_file.exists():
            return
        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("[EXEC] Cannot read positions state file: %s", exc)
            return

        saved: dict[str, Any] = raw.get("positions", {})
        if not saved:
            return

        # Query exchange to confirm which directions still have open qty
        exchange_qty: dict[str, float] = {"long": 0.0, "short": 0.0}
        if self.enabled and self.order_manager is not None:
            try:
                for p in self.order_manager.get_open_positions():
                    d = str(p.get("direction", "")).lower()
                    if d in exchange_qty:
                        exchange_qty[d] += float(p.get("qty", 0.0))
            except Exception as exc:
                logger.warning(
                    "[EXEC] Cannot verify exchange positions during restore: %s", exc
                )
                return

        restored = 0
        for key, data in saved.items():
            direction = str(data.get("direction", "")).lower()
            qty = float(data.get("qty", 0.0))

            # Skip if the exchange no longer holds this position
            if exchange_qty.get(direction, 0.0) < qty - 1e-6:
                logger.info(
                    "[EXEC] Restore skip %s: not on exchange (already closed)", key
                )
                continue

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
                    mechanism_type=str(data.get("mechanism_type", "")),
                )
                with self._lock:
                    self._open_positions[key] = pos
                restored += 1
                logger.info(
                    "[EXEC] Restored %s entry=%.2f qty=%.6f regime=%s",
                    key, pos.entry_price, pos.qty, pos.entry_regime,
                )
            except Exception as exc:
                logger.warning("[EXEC] Failed to restore %s: %s", key, exc)

        if restored:
            logger.info(
                "[EXEC] State recovery complete: %d position(s) restored from %s",
                restored,
                self._state_file,
            )


