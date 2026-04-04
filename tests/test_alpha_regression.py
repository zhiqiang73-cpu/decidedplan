"""Focused regression tests for the live alpha mainline."""

from __future__ import annotations

import shutil
import sys
import time
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

from execution.execution_engine import ExecutionEngine
from execution.trade_logger import TradeLogger
import monitor.alpha_rules as alpha_rules
from monitor.smart_exit_policy import build_entry_snapshot
from utils.file_io import read_json_file


class DummyOrderManager:
    def set_leverage(self, leverage):
        return None


class AlphaRegressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.approved_cards = read_json_file(ROOT / "alpha" / "output" / "approved_rules.json", [])

    def setUp(self) -> None:
        tmp_root = ROOT / "tests" / "_tmp"
        tmp_root.mkdir(parents=True, exist_ok=True)
        self.run_dir = tmp_root / f"alpha_reg_{time.time_ns()}"
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.logger = TradeLogger(csv_path=self.run_dir / "trades.csv")

    def tearDown(self) -> None:
        if self.run_dir.exists():
            shutil.rmtree(self.run_dir, ignore_errors=True)

    def _approved_card(self, family: str) -> dict:
        for card in self.approved_cards:
            if str(card.get("family")) == family:
                return card
        self.fail(f"Approved card not found for family={family}")

    def test_a4_pir_card_keeps_runtime_exit_params(self) -> None:
        card = self._approved_card("A4-PIR")
        exit_params = card.get("exit_params")
        self.assertIsInstance(exit_params, dict)
        self.assertEqual(exit_params.get("stop_pct"), 0.7)
        self.assertEqual(exit_params.get("exit_confirm_bars"), 2)
        self.assertEqual(exit_params.get("tighten_gap_ratio"), 0.3)

    def test_alpha_rule_checker_emits_card_exit_params(self) -> None:
        card = self._approved_card("A4-PIR")
        rules = alpha_rules._build_alpha_rules_from_approved([card])
        self.assertEqual(len(rules), 1)

        old_rules = alpha_rules.ALPHA_RULES
        alpha_rules.ALPHA_RULES = rules
        try:
            checker = alpha_rules.AlphaRuleChecker(cooldown_bars=1)
            row = pd.Series(
                {
                    "position_in_range_4h": 0.80,
                    "oi_change_rate_1h": 0.0,
                    "volume_vs_ma20": 1.20,
                    "taker_buy_sell_ratio": 0.90,
                    "oi_change_rate_5m": 0.001,
                }
            )
            alerts = checker.check(row, timestamp_ms=1_700_000_000_000)
        finally:
            alpha_rules.ALPHA_RULES = old_rules

        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0]["family"], "A4-PIR")
        self.assertEqual(alerts[0]["alpha_exit_params"]["exit_confirm_bars"], 2)
        self.assertEqual(alerts[0]["alpha_exit_params"]["tighten_gap_ratio"], 0.3)

    def test_entry_snapshot_and_execution_plan_preserve_card_params(self) -> None:
        card = self._approved_card("A4-PIR")
        alert = {
            "name": card["id"],
            "family": card["family"],
            "direction": card["entry"]["direction"],
            "horizon": card["entry"]["horizon"],
            "feature": card["entry"]["feature"],
            "feature_value": card["entry"]["threshold"] + 0.05,
            "alpha_exit_combos": [
                [
                    {
                        "feature": "position_in_range_4h",
                        "operator": "<",
                        "threshold": 0.50,
                    }
                ]
            ],
            "alpha_exit_params": dict(card["exit_params"]),
            "stop_pct": card.get("stop_pct"),
        }
        features = pd.Series({"position_in_range_4h": 0.80})
        entry_snapshot = build_entry_snapshot(alert, features)

        engine = ExecutionEngine(
            order_manager=DummyOrderManager(),
            trade_logger=self.logger,
            min_confidence=2,
            entry_timeout_s=1,
            poll_interval_s=0.01,
        )
        dynamic_exit, params = engine._resolve_alert_exit_plan(
            alert=alert,
            family=card["family"],
            direction=card["entry"]["direction"],
            horizon_min=card["entry"]["horizon"],
            entry_snapshot=entry_snapshot,
        )

        self.assertTrue(dynamic_exit)
        self.assertIsNotNone(params)
        self.assertEqual(entry_snapshot["alpha_exit_params"]["exit_confirm_bars"], 2)
        self.assertEqual(params.stop_pct, 0.7)
        self.assertEqual(params.min_hold_bars, 5)
        self.assertEqual(params.exit_confirm_bars, 2)
        self.assertEqual(params.tighten_gap_ratio, 0.3)


if __name__ == "__main__":
    unittest.main()