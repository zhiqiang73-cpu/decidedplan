"""Unit tests for the execution layer."""

from __future__ import annotations

import csv
import shutil
import sys
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

from execution.execution_engine import ExecutionEngine
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


class ExecutionLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp_root = ROOT / "tests" / "_tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        self.run_dir = tmp_root / f"run_{time.time_ns()}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.csv_path = self.run_dir / "trades.csv"
        self.logger = TradeLogger(csv_path=self.csv_path)
        self.manager = FakeOrderManager()

    def tearDown(self) -> None:
        time.sleep(0.05)
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



if __name__ == "__main__":
    unittest.main()
