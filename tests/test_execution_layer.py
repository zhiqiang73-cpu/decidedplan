"""Unit tests for the execution layer."""

from __future__ import annotations

import csv
import json
import os
import shutil
import sys
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

from execution import config
from execution.execution_engine import ExecutionEngine, OpenPosition
from execution.order_manager import OrderManagerError
from execution.trade_logger import TradeLogger


class FakeOrderManager:
    def __init__(self) -> None:
        self.next_order_id = 1001
        self.orders: dict[str, list[dict]] = {}
        self.place_calls: list[dict] = []
        self.cancel_calls: list[str] = []
        self.close_calls: list[dict] = []
        self.book = {"bid": 100.0, "ask": 101.0, "tick_size": 0.1}
        self.book_sequence: list[dict[str, float]] = []
        self.positions: list[dict] = []
        self.calc_qty_value = 0.001

    def set_leverage(self, leverage):
        return None

    def calc_qty(self, position_pct, leverage, price):
        return self.calc_qty_value

    def place_limit_entry(
        self,
        direction,
        qty,
        price,
        signal_name,
        horizon_min,
        time_in_force="GTC",
    ):
        order_id = str(self.next_order_id)
        self.next_order_id += 1
        self.place_calls.append(
            {
                "direction": direction,
                "qty": qty,
                "price": price,
                "signal_name": signal_name,
                "horizon_min": horizon_min,
                "time_in_force": time_in_force,
                "order_id": order_id,
            }
        )
        default_status = (
            [{"status": "EXPIRED", "executed_qty": 0.0, "avg_price": price, "update_time": 0}]
            if time_in_force == "IOC"
            else [{"status": "NEW", "executed_qty": 0.0, "avg_price": price, "update_time": 0}]
        )
        self.orders.setdefault(order_id, default_status)
        return {"status": "placed", "order_id": order_id, "price": price, "qty": qty}

    def cancel_order(self, order_id):
        self.cancel_calls.append(order_id)
        return True

    def close_position(self, direction, qty):
        self.close_calls.append({"direction": direction, "qty": qty})
        self.positions = [
            p for p in self.positions
            if str(p.get("direction", "")).lower() != str(direction).lower()
        ]
        return {
            "status": "closed",
            "avg_price": 101.5,
            "qty": qty,
            "update_time": 1_700_000_060_000,
            "fee_type": "maker",
        }

    def get_open_positions(self):
        return list(self.positions)

    def get_book_ticker(self):
        if self.book_sequence:
            return dict(self.book_sequence.pop(0))
        return dict(self.book)

    def get_best_price(self, direction):
        if direction == "long":
            return self.book["bid"]
        return self.book["ask"]

    def get_order_status(self, order_id):
        sequence = self.orders[order_id]
        status = sequence.pop(0) if len(sequence) > 1 else sequence[0]
        if str(status.get("status", "")).upper() == "FILLED":
            placed = next(
                (call for call in self.place_calls if call["order_id"] == order_id),
                None,
            )
            if placed is not None:
                self.positions = [{
                    "direction": placed["direction"],
                    "qty": float(status.get("executed_qty") or status.get("orig_qty") or placed["qty"]),
                    "entry_price": float(status.get("avg_price") or placed["price"]),
                }]
        return status

    def get_user_trades(self, **kwargs):
        return []

    def place_stop_market(self, direction, qty, stop_price):
        return {
            "status": "placed",
            "order_id": f"stop-{self.next_order_id}",
            "stop_price": stop_price,
            "qty": qty,
        }


class ExecutionLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp_root = ROOT / "tests" / "_tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        self.run_dir = tmp_root / f"run_{time.time_ns()}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.prev_cwd = Path.cwd()
        os.chdir(self.run_dir)
        self.csv_path = self.run_dir / "trades.csv"
        self.logger = TradeLogger(csv_path=self.csv_path)
        self.manager = FakeOrderManager()

    def tearDown(self) -> None:
        time.sleep(0.05)
        os.chdir(self.prev_cwd)
        if self.run_dir.exists():
            shutil.rmtree(self.run_dir, ignore_errors=True)

    def _read_rows(self):
        with self.csv_path.open("r", encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))

    def _wait_for(self, predicate, timeout=1.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return predicate()

    def test_low_confidence_signal_is_ignored(self):
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=1,
            poll_interval_s=0.01,
        )

        engine.on_signal(
            {
                "name": "P1-low",
                "direction": "long",
                "confidence": 1,
                "horizon": 10,
                "timestamp_ms": 1_700_000_000_000,
            }
        )

        self.assertEqual(len(self.manager.place_calls), 0)
        self.assertEqual(self._read_rows(), [])

    def test_p110_short_in_trend_up_requires_4h_top_zone(self):
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )
        engine.update_market_state("QUIET_TREND", "PASSIVE", "TREND_UP")

        engine.on_signal(
            {
                "phase": "P1",
                "name": "P1-10_taker_exhaustion_low",
                "direction": "short",
                "confidence": 3,
                "horizon": 30,
                "timestamp_ms": 1_700_000_000_000,
            },
            latest_features={"close": 100.0, "position_in_range_4h": 0.89},
        )

        self.assertEqual(len(self.manager.place_calls), 0)

    def test_p110_short_in_trend_up_allows_4h_top_zone(self):
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )
        engine.update_market_state("QUIET_TREND", "PASSIVE", "TREND_UP")

        engine.on_signal(
            {
                "phase": "P1",
                "name": "P1-10_taker_exhaustion_low",
                "direction": "short",
                "confidence": 3,
                "horizon": 30,
                "timestamp_ms": 1_700_000_000_000,
            },
            latest_features={"close": 100.0, "position_in_range_4h": 0.91},
        )

        self.assertEqual(len(self.manager.place_calls), 1)

    def test_entry_is_blocked_when_exchange_positions_cannot_be_verified(self):
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )

        def _raise_positions_error():
            raise OrderManagerError("sync timeout")

        self.manager.get_open_positions = _raise_positions_error  # type: ignore[method-assign]

        engine.on_signal(
            {
                "name": "P1-11_high_pos_funding",
                "direction": "short",
                "confidence": 3,
                "horizon": 20,
                "timestamp_ms": 1_700_000_000_000,
            }
        )

        self.assertEqual(len(self.manager.place_calls), 0)
        self.assertEqual(engine._open_positions, {})

    def test_same_strategy_direction_blocks_duplicate_entries(self):
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.1,
            poll_interval_s=0.01,
        )
        self.manager.orders["1001"] = [
            {"status": "NEW", "executed_qty": 0.0, "avg_price": 100.0, "update_time": 0},
            {"status": "NEW", "executed_qty": 0.0, "avg_price": 100.0, "update_time": 0},
            {"status": "NEW", "executed_qty": 0.0, "avg_price": 100.0, "update_time": 0},
        ]

        alert = {
            "name": "P1-8_vwap_vol_drought",
            "direction": "long",
            "confidence": 2,
            "horizon": 10,
            "timestamp_ms": 1_700_000_000_000,
        }
        engine.on_signal(alert)
        engine.on_signal(dict(alert))

        self.assertEqual(len(self.manager.place_calls), 1)
        self.assertTrue(self._wait_for(lambda: len(self.manager.cancel_calls) == 1))

    def test_unfilled_order_is_canceled_and_logged(self):
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )
        self.manager.orders["1001"] = [
            {"status": "NEW", "executed_qty": 0.0, "avg_price": 101.0, "update_time": 0}
        ]

        engine.on_signal(
            {
                "name": "P1-11_high_pos_funding",
                "direction": "short",
                "confidence": 3,
                "horizon": 20,
                "timestamp_ms": 1_700_000_000_000,
            }
        )

        self.assertTrue(self._wait_for(lambda: len(self._read_rows()) == 1))
        rows = self._read_rows()
        self.assertEqual(rows[0]["exit_reason"], "not_filled")
        self.assertEqual(rows[0]["gross_return_pct"], "0.000000")
        self.assertEqual(len(self.manager.cancel_calls), 1)
        self.assertEqual([call["time_in_force"] for call in self.manager.place_calls], ["GTC", "IOC"])

    def test_retry_uses_latest_book_for_aggressive_limit(self):
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )
        self.manager.book_sequence = [
            {"bid": 100.0, "ask": 101.0, "tick_size": 0.1},
            {"bid": 95.0, "ask": 96.0, "tick_size": 0.1},
        ]
        self.manager.orders["1001"] = [
            {"status": "NEW", "executed_qty": 0.0, "avg_price": 101.0, "update_time": 0}
        ]

        engine.on_signal(
            {
                "name": "P1-11_high_pos_funding",
                "direction": "short",
                "confidence": 3,
                "horizon": 20,
                "timestamp_ms": 1_700_000_000_000,
            }
        )

        self.assertTrue(self._wait_for(lambda: len(self.manager.place_calls) == 2))
        self.assertEqual(self.manager.place_calls[0]["price"], 101.0)
        self.assertAlmostEqual(self.manager.place_calls[1]["price"], 94.8)
        self.assertEqual(self.manager.place_calls[1]["time_in_force"], "IOC")

    def test_filled_position_closes_on_due_bar_and_logs_trade(self):
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=1,
            poll_interval_s=0.01,
        )
        fill_ts = 1_700_000_000_000
        self.manager.orders["1001"] = [
            {
                "status": "FILLED",
                "executed_qty": 0.001,
                "avg_price": 100.5,
                "orig_qty": 0.001,
                "update_time": fill_ts,
            }
        ]

        engine.on_signal(
            {
                "name": "P1-11_high_pos_funding",
                "direction": "short",
                "confidence": 2,
                "horizon": 1,
                "timestamp_ms": fill_ts,
            }
        )

        self.assertTrue(self._wait_for(lambda: len(engine._open_positions) > 0))
        pos = next(iter(engine._open_positions.values()))
        engine._close_position(pos, exit_reason="filled_timeout")

        self.assertTrue(self._wait_for(lambda: len(self._read_rows()) == 1))
        rows = self._read_rows()
        self.assertEqual(rows[0]["exit_reason"], "filled_timeout")
        self.assertEqual(len(self.manager.close_calls), 1)
        self.assertEqual(rows[0]["net_return_pct"], "-1.035025")

    def test_alpha_card_signal_uses_dynamic_exit_logic(self):
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=1,
            poll_interval_s=0.01,
        )
        fill_ts = 1_700_000_000_000
        self.manager.orders["1001"] = [
            {
                "status": "FILLED",
                "executed_qty": 0.001,
                "avg_price": 100.5,
                "orig_qty": 0.001,
                "update_time": fill_ts,
            }
        ]

        engine.on_signal(
            {
                "phase": "P2",
                "name": "alpha_dynamic_card",
                "family": "ALPHA::mean_revert::short::10",
                "direction": "short",
                "confidence": 2,
                "horizon": 10,
                "timestamp_ms": fill_ts,
                "alpha_exit_conditions": [
                    {
                        "feature": "oi_change_rate_5m",
                        "operator": "<",
                        "threshold": -0.01,
                        "expected_hold_bars": 4,
                    }
                ],
                "alpha_exit_params": {
                    "stop_pct": 0.3,
                    "protect_start_pct": 0.12,
                    "protect_gap_ratio": 0.5,
                    "protect_floor_pct": 0.03,
                    "min_hold_bars": 1,
                    "max_hold_factor": 4,
                    "exit_confirm_bars": 1,
                    "tighten_gap_ratio": 0.3,
                },
                "stop_pct": 0.3,
            },
            latest_features={"close": 100.5, "oi_change_rate_5m": 0.02},
        )

        self.assertEqual(len(self.manager.place_calls), 1)
        self.assertTrue(self._wait_for(lambda: len(engine._open_positions) > 0))

        pos = next(iter(engine._open_positions.values()))
        self.assertTrue(pos.dynamic_exit_enabled)
        self.assertEqual(pos.family, "ALPHA::mean_revert::short::10")
        self.assertIn("alpha_exit_conditions", pos.entry_snapshot)
        self.assertEqual(pos.entry_snapshot["alpha_exit_params"]["min_hold_bars"], 1)

        engine.on_bar(
            {
                "timestamp": fill_ts + 60_000,
                "close": 99.0,
                "oi_change_rate_5m": -0.02,
            }
        )

        self.assertTrue(self._wait_for(lambda: len(self.manager.close_calls) == 1))
        self.assertTrue(self._wait_for(lambda: len(self._read_rows()) == 1))
        rows = self._read_rows()
        self.assertEqual(rows[0]["exit_reason"], "logic_complete")

    def test_alpha_combo_only_signal_uses_dynamic_exit_logic(self):
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=1,
            poll_interval_s=0.01,
        )
        fill_ts = 1_700_000_000_000
        self.manager.orders["1001"] = [
            {
                "status": "FILLED",
                "executed_qty": 0.001,
                "avg_price": 100.5,
                "orig_qty": 0.001,
                "update_time": fill_ts,
            }
        ]

        engine.on_signal(
            {
                "phase": "P2",
                "name": "alpha_combo_card",
                "family": "ALPHA::combo_revert::short::10",
                "direction": "short",
                "confidence": 2,
                "horizon": 10,
                "timestamp_ms": fill_ts,
                "alpha_exit_combos": [
                    [
                        {
                            "feature": "oi_change_rate_5m",
                            "operator": "<",
                            "threshold": -0.01,
                        },
                        {
                            "feature": "volume_vs_ma20_vs_entry",
                            "operator": ">",
                            "threshold": 0.1,
                        },
                    ]
                ],
                "alpha_exit_params": {
                    "stop_pct": 0.3,
                    "protect_start_pct": 0.12,
                    "protect_gap_ratio": 0.5,
                    "protect_floor_pct": 0.03,
                    "min_hold_bars": 1,
                    "max_hold_factor": 4,
                    "exit_confirm_bars": 1,
                    "tighten_gap_ratio": 0.3,
                },
                "stop_pct": 0.3,
            },
            latest_features={"close": 100.5, "oi_change_rate_5m": 0.02, "volume_vs_ma20": 1.0},
        )

        self.assertEqual(len(self.manager.place_calls), 1)
        self.assertTrue(self._wait_for(lambda: len(engine._open_positions) > 0))

        pos = next(iter(engine._open_positions.values()))
        self.assertTrue(pos.dynamic_exit_enabled)
        self.assertEqual(pos.family, "ALPHA::combo_revert::short::10")
        self.assertIn("alpha_exit_combos", pos.entry_snapshot)
        self.assertEqual(pos.entry_snapshot["alpha_exit_params"]["exit_confirm_bars"], 1)

        engine.on_bar(
            {
                "timestamp": fill_ts + 60_000,
                "close": 99.0,
                "oi_change_rate_5m": -0.02,
                "volume_vs_ma20": 1.2,
            }
        )

        self.assertTrue(self._wait_for(lambda: len(self.manager.close_calls) == 1))
        self.assertTrue(self._wait_for(lambda: len(self._read_rows()) == 1))
        rows = self._read_rows()
        self.assertEqual(rows[0]["exit_reason"], "logic_complete")

    def test_alpha_params_only_signal_still_uses_dynamic_exit_logic(self):
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=1,
            poll_interval_s=0.01,
        )
        fill_ts = 1_700_000_000_000
        self.manager.orders["1001"] = [
            {
                "status": "FILLED",
                "executed_qty": 0.001,
                "avg_price": 100.5,
                "orig_qty": 0.001,
                "update_time": fill_ts,
            }
        ]

        engine.on_signal(
            {
                "phase": "P2",
                "name": "alpha_params_only_card",
                "family": "ALPHA::params_only::short::10",
                "direction": "short",
                "confidence": 2,
                "horizon": 10,
                "timestamp_ms": fill_ts,
                "alpha_exit_params": {
                    "stop_pct": 0.3,
                    "protect_start_pct": 0.12,
                    "protect_gap_ratio": 0.5,
                    "protect_floor_pct": 0.03,
                    "min_hold_bars": 1,
                    "max_hold_factor": 4,
                    "exit_confirm_bars": 1,
                    "tighten_gap_ratio": 0.3,
                },
                "stop_pct": 0.3,
            },
            latest_features={"close": 100.5, "oi_change_rate_5m": 0.02, "taker_buy_sell_ratio": 0.95, "spread_vs_ma20": 1.0},
        )

        self.assertEqual(len(self.manager.place_calls), 1)
        self.assertTrue(self._wait_for(lambda: len(engine._open_positions) > 0))

        pos = next(iter(engine._open_positions.values()))
        self.assertTrue(pos.dynamic_exit_enabled)
        self.assertEqual(pos.family, "ALPHA::params_only::short::10")
        self.assertEqual(pos.entry_snapshot["alpha_exit_params"]["exit_confirm_bars"], 1)

        engine.on_bar(
            {
                "timestamp": fill_ts + 60_000,
                "close": 99.0,
                "oi_change_rate_5m": -0.03,
                "taker_buy_sell_ratio": 1.2,
                "spread_vs_ma20": 2.2,
            }
        )

        self.assertTrue(self._wait_for(lambda: len(self.manager.close_calls) == 1))
        self.assertTrue(self._wait_for(lambda: len(self._read_rows()) == 1))
        rows = self._read_rows()
        self.assertEqual(rows[0]["exit_reason"], "regime_shift")

    def test_alpha_exit_param_parser_preserves_advanced_fields(self):
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=1,
            poll_interval_s=0.01,
        )

        params = engine._exit_params_from_dict(
            {
                "stop_pct": 0.42,
                "protect_start_pct": 0.18,
                "protect_gap_ratio": 0.45,
                "protect_floor_pct": 0.05,
                "min_hold_bars": 3,
                "max_hold_factor": 6,
                "exit_confirm_bars": 2,
                "decay_exit_threshold": 0.91,
                "decay_tighten_threshold": 0.61,
                "tighten_gap_ratio": 0.22,
                "confidence_stop_multipliers": {"1": 0.5, "2": 0.9, "3": 1.6},
                "regime_stop_multipliers": {"QUIET_TREND": 0.7, "CRISIS": 0.35},
                "mfe_ratchet_threshold": 0.22,
                "mfe_ratchet_ratio": 0.45,
            }
        )

        self.assertIsNotNone(params)
        self.assertEqual(params.stop_pct, 0.42)
        self.assertEqual(params.decay_exit_threshold, 0.91)
        self.assertEqual(params.decay_tighten_threshold, 0.61)
        self.assertEqual(params.tighten_gap_ratio, 0.22)
        self.assertEqual(params.confidence_stop_multipliers[3], 1.6)
        self.assertEqual(params.regime_stop_multipliers["CRISIS"], 0.35)
        self.assertEqual(params.mfe_ratchet_threshold, 0.22)
        self.assertEqual(params.mfe_ratchet_ratio, 0.45)

    def test_disabled_engine_is_safe_without_credentials(self):
        engine = ExecutionEngine(
            order_manager=None,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )

        engine.on_signal(
            {
                "name": "P1-8_vwap_vol_drought",
                "direction": "long",
                "confidence": 3,
                "horizon": 10,
                "timestamp_ms": 1_700_000_000_000,
            }
        )
        engine.on_bar({"timestamp": 1_700_000_060_000})

        self.assertEqual(self._read_rows(), [])

    def test_restore_is_deferred_until_order_manager_reconnect(self):
        state_path = self.run_dir / "execution" / "logs" / "positions_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_payload = {
            "saved_at": "2026-04-12T15:09:52+00:00",
            "positions": {
                "P1-6|long": {
                    "signal_name": "P1-6_bottom_volume_drought",
                    "family": "P1-6",
                    "direction": "long",
                    "qty": 0.02,
                    "entry_price": 100.0,
                    "confidence": 3,
                    "horizon_min": 30,
                    "entry_time": "2026-04-12T15:09:52+00:00",
                    "exit_due_time": None,
                    "order_id": "restored-order",
                    "entry_snapshot": {"feature": "demo"},
                    "runtime_state": {"bars_held": 2},
                    "dynamic_exit_enabled": True,
                    "entry_fee_type": "maker",
                    "entry_regime": "QUIET_TREND",
                    "entry_flow_type": "PASSIVE",
                    "mechanism_type": "seller_drought",
                }
            },
        }
        state_path.write_text(json.dumps(state_payload), encoding="utf-8")

        engine = ExecutionEngine(
            order_manager=None,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )

        self.assertEqual(engine._open_positions, {})
        self.assertIn("P1-6|long", engine._pending_restore_state)
        preserved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIn("P1-6|long", preserved["positions"])

        self.manager.positions = [
            {"direction": "long", "qty": 0.02, "entry_price": 100.0}
        ]
        engine.set_order_manager(self.manager)

        self.assertIn("P1-6|long", engine._open_positions)
        self.assertEqual(engine._pending_restore_state, {})

    def test_restore_hard_stop_uses_snapshot_exit_params_instead_of_family_defaults(self):
        state_path = self.run_dir / "execution" / "logs" / "positions_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_payload = {
            "saved_at": "2026-04-12T15:09:52+00:00",
            "positions": {
                "P1-12|short": {
                    "signal_name": "P1-12_trend_down_range_top",
                    "family": "P1-12",
                    "direction": "short",
                    "qty": 0.02,
                    "entry_price": 100.0,
                    "confidence": 2,
                    "horizon_min": 30,
                    "entry_time": "2026-04-12T15:09:52+00:00",
                    "exit_due_time": None,
                    "order_id": "restored-order",
                    "entry_snapshot": {
                        "feature": "demo",
                        "alpha_exit_params": {
                            "stop_pct": 5.0,
                            "protect_start_pct": 0.1,
                            "protect_gap_ratio": 0.5,
                            "protect_floor_pct": 0.03,
                            "min_hold_bars": 1,
                            "max_hold_factor": 2,
                            "exit_confirm_bars": 1,
                        },
                    },
                    "runtime_state": {"bars_held": 2, "mfe_pct": 0.0},
                    "dynamic_exit_enabled": True,
                    "entry_fee_type": "maker",
                    "entry_regime": "RANGE_BOUND",
                    "entry_flow_type": "PASSIVE",
                    "mechanism_type": "range_top_reversion_short",
                }
            },
        }
        state_path.write_text(json.dumps(state_payload), encoding="utf-8")

        engine = ExecutionEngine(
            order_manager=None,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )

        self.manager.positions = [
            {"direction": "short", "qty": 0.02, "entry_price": 100.0}
        ]
        self.manager.book = {"bid": 101.9, "ask": 102.1}
        engine.set_order_manager(self.manager)

        self.assertIn("P1-12|short", engine._open_positions)
        self.assertEqual(len(self.manager.close_calls), 0)

    def test_restore_overdue_hard_stop_closes_immediately_without_waiting_for_bar(self):
        state_path = self.run_dir / "execution" / "logs" / "positions_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_payload = {
            "saved_at": "2026-04-12T15:09:52+00:00",
            "positions": {
                "P1-12|short": {
                    "signal_name": "P1-12_trend_down_range_top",
                    "family": "P1-12",
                    "direction": "short",
                    "qty": 0.02,
                    "entry_price": 100.0,
                    "confidence": 2,
                    "horizon_min": 30,
                    "entry_time": "2026-04-12T15:09:52+00:00",
                    "exit_due_time": None,
                    "order_id": "restored-order",
                    "entry_snapshot": {"feature": "demo"},
                    "runtime_state": {"bars_held": 2, "mfe_pct": 0.0},
                    "dynamic_exit_enabled": True,
                    "entry_fee_type": "maker",
                    "entry_regime": "RANGE_BOUND",
                    "entry_flow_type": "PASSIVE",
                    "mechanism_type": "range_top_reversion_short",
                }
            },
        }
        state_path.write_text(json.dumps(state_payload), encoding="utf-8")

        engine = ExecutionEngine(
            order_manager=None,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )

        self.manager.positions = [
            {"direction": "short", "qty": 0.02, "entry_price": 100.0}
        ]
        self.manager.book = {"bid": 100.45, "ask": 100.55}
        engine.set_order_manager(self.manager)

        self.assertEqual(len(self.manager.close_calls), 1)
        self.assertEqual(self.manager.close_calls[0]["direction"], "short")
        self.assertNotIn("P1-12|short", engine._open_positions)
        rows = self._read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["exit_reason"], "hard_stop")

    def test_manual_exchange_close_arms_persistent_reconcile_cooldown(self):
        state_path = self.run_dir / "execution" / "logs" / "positions_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)

        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )
        engine._open_positions["P1-10|short"] = OpenPosition(
            signal_name="P1-10",
            family="P1-10",
            direction="short",
            qty=0.025,
            entry_price=100.0,
            confidence=3,
            horizon_min=30,
            entry_time=datetime.now(timezone.utc) - timedelta(seconds=180),
            exit_due_time=None,
            order_id="manual-test",
            entry_snapshot={"raw_signal_name": "P1-10_taker_exhaustion_low"},
            runtime_state={"bars_held": 3},
            dynamic_exit_enabled=True,
            entry_fee_type="maker",
            entry_regime="QUIET_TREND",
            entry_flow_type="PASSIVE",
            mechanism_type="generic_alpha",
        )
        engine._save_positions_state()

        engine._apply_external_positions_locked([], {"P1-10|short"})

        self.assertNotIn("P1-10|short", engine._open_positions)
        self.assertGreater(engine._signal_cooldown.get("P1-10|short", 0.0), time.time())
        rows = self._read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["exit_reason"], "manual_close_exchange")

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["positions"], {})
        self.assertIn("P1-10|short", saved["cooldowns"])

        restarted_manager = FakeOrderManager()
        restarted_engine = ExecutionEngine(
            order_manager=restarted_manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )
        restarted_engine.on_signal(
            {
                "name": "P1-10_taker_exhaustion_low",
                "direction": "short",
                "confidence": 3,
                "horizon": 30,
                "timestamp_ms": 1_700_000_000_000,
            }
        )

        self.assertEqual(len(restarted_manager.place_calls), 0)

    def test_restore_skip_missing_exchange_position_blocks_immediate_reentry(self):
        state_path = self.run_dir / "execution" / "logs" / "positions_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_payload = {
            "saved_at": "2026-04-12T15:09:52+00:00",
            "positions": {
                "P1-10|short": {
                    "signal_name": "P1-10",
                    "family": "P1-10",
                    "direction": "short",
                    "qty": 0.025,
                    "entry_price": 100.0,
                    "confidence": 3,
                    "horizon_min": 30,
                    "entry_time": "2026-04-12T15:09:52+00:00",
                    "exit_due_time": None,
                    "order_id": "restored-order",
                    "entry_snapshot": {"raw_signal_name": "P1-10_taker_exhaustion_low"},
                    "runtime_state": {"bars_held": 2},
                    "dynamic_exit_enabled": True,
                    "entry_fee_type": "maker",
                    "entry_regime": "QUIET_TREND",
                    "entry_flow_type": "PASSIVE",
                    "mechanism_type": "generic_alpha",
                }
            },
        }
        state_path.write_text(json.dumps(state_payload), encoding="utf-8")

        engine = ExecutionEngine(
            order_manager=None,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )

        self.manager.positions = []
        engine.set_order_manager(self.manager)
        engine.on_signal(
            {
                "name": "P1-10_taker_exhaustion_low",
                "direction": "short",
                "confidence": 3,
                "horizon": 30,
                "timestamp_ms": 1_700_000_000_000,
            }
        )

        self.assertNotIn("P1-10|short", engine._open_positions)
        self.assertEqual(len(self.manager.place_calls), 0)
        self.assertGreater(engine._signal_cooldown.get("P1-10|short", 0.0), time.time())

        saved = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["positions"], {})
        self.assertIn("P1-10|short", saved["cooldowns"])

    def test_orphan_position_after_disconnected_boot_is_auto_flattened(self):
        engine = ExecutionEngine(
            order_manager=None,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )

        self.manager.positions = [
            {"direction": "long", "qty": 0.02, "entry_price": 100.0}
        ]
        engine.set_order_manager(self.manager)

        engine.on_bar({"timestamp": 1_700_000_000_000})
        self.assertTrue(
            self._wait_for(
                lambda: engine._external_sync_future is not None and engine._external_sync_future.done(),
                timeout=1.0,
            )
        )

        engine.on_bar({"timestamp": 1_700_000_060_000})
        self.assertTrue(
            self._wait_for(
                lambda: engine._orphan_flatten_future is not None and engine._orphan_flatten_future.done(),
                timeout=1.0,
            )
        )

        engine.on_bar({"timestamp": 1_700_000_120_000})
        self.assertEqual(len(self.manager.close_calls), 1)
        self.assertEqual(self.manager.close_calls[0]["direction"], "long")
        rows = self._read_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["exit_reason"], "orphan_auto_flatten")

    def test_external_position_blocks_new_entries(self):
        self.manager.positions = [
            {"direction": "long", "qty": 0.02, "entry_price": 100.0}
        ]
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )

        engine.on_bar({"timestamp": 1_700_000_000_000})
        engine.on_signal(
            {
                "name": "P1-11_high_pos_funding",
                "direction": "short",
                "confidence": 3,
                "horizon": 20,
                "timestamp_ms": 1_700_000_000_000,
            }
        )

        self.assertEqual(len(self.manager.place_calls), 0)

    def test_external_position_sync_does_not_block_bar_loop(self):
        class SlowOrderManager(FakeOrderManager):
            def get_open_positions(self):
                time.sleep(0.3)
                return [{"direction": "long", "qty": 0.02, "entry_price": 100.0}]

        slow_manager = SlowOrderManager()
        engine = ExecutionEngine(
            order_manager=slow_manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )

        started = time.perf_counter()
        engine.on_bar({"timestamp": 1_700_000_000_000})
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 0.1)
        self.assertTrue(
            self._wait_for(
                lambda: engine._external_sync_future is not None and engine._external_sync_future.done(),
                timeout=1.0,
            )
        )

        engine.on_bar({"timestamp": 1_700_000_060_000})
        self.assertIn("external|any", engine._open_positions)

    def test_persistent_external_position_is_auto_flattened(self):
        self.manager.positions = [
            {"direction": "long", "qty": 0.02, "entry_price": 100.0}
        ]
        engine = ExecutionEngine(
            order_manager=self.manager,
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=0.05,
            poll_interval_s=0.01,
        )

        engine.on_bar({"timestamp": 1_700_000_000_000})
        self.assertTrue(
            self._wait_for(
                lambda: engine._external_sync_future is not None and engine._external_sync_future.done(),
                timeout=1.0,
            )
        )

        engine.on_bar({"timestamp": 1_700_000_060_000})
        ext_pos = engine._open_positions["external|any"]
        ext_pos.entry_time = datetime.now(timezone.utc) - timedelta(
            seconds=float(config.PERSISTENT_EXTERNAL_FLATTEN_AFTER_S) + 5.0
        )
        engine._last_ext_sync_ts = 0.0

        engine.on_bar({"timestamp": 1_700_000_120_000})
        self.assertTrue(
            self._wait_for(
                lambda: engine._external_sync_future is not None and engine._external_sync_future.done(),
                timeout=1.0,
            )
        )

        engine.on_bar({"timestamp": 1_700_000_180_000})
        self.assertTrue(
            self._wait_for(
                lambda: engine._orphan_flatten_future is not None and engine._orphan_flatten_future.done(),
                timeout=1.0,
            )
        )

        engine.on_bar({"timestamp": 1_700_000_240_000})
        self.assertEqual(len(self.manager.close_calls), 1)
        self.assertEqual(self.manager.close_calls[0]["direction"], "long")



if __name__ == "__main__":
    unittest.main()
