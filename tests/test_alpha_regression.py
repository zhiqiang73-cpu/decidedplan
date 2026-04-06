"""Focused regression tests for the live alpha mainline."""

from __future__ import annotations

import shutil
import sys
import time
import unittest
from unittest.mock import Mock
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
from monitor.signal_runner import SignalRunner
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
        self.assertEqual(alerts[0]["mechanism_type"], "oi_divergence")
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
            "mechanism_type": "oi_divergence",
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
        self.assertEqual(entry_snapshot["mechanism_type"], "oi_divergence")
        self.assertEqual(entry_snapshot["alpha_exit_params"]["exit_confirm_bars"], 2)
        self.assertEqual(params.stop_pct, 0.7)
        self.assertEqual(params.min_hold_bars, 5)
        self.assertEqual(params.exit_confirm_bars, 2)
        self.assertEqual(params.tighten_gap_ratio, 0.3)


    def test_validate_approved_rule_pool_flags_blocking_card_issues(self) -> None:
        approved = [
            {
                "id": "card_keep",
                "status": "approved",
                "approved_by": "human_manual",
                "family": "A4-PIR",
                "entry": {"feature": "position_in_range_4h", "operator": ">", "threshold": 0.7, "direction": "short", "horizon": 30},
            },
            {
                "id": "card_dup_family",
                "status": "approved",
                "approved_by": "human_manual",
                "family": "A4-PIR",
                "entry": {"feature": "position_in_range_4h", "operator": ">", "threshold": 0.71, "direction": "short", "horizon": 30},
            },
            {
                "id": "card_missing_family",
                "status": "approved",
                "approved_by": "human_manual",
                "entry": {"feature": "position_in_range_4h", "operator": ">", "threshold": 0.72, "direction": "short", "horizon": 30},
            },
        ]

        issues = alpha_rules.validate_approved_rule_pool(approved)

        self.assertEqual(len(issues), 2)
        self.assertTrue(any("duplicate family=A4-PIR" in issue for issue in issues))
        self.assertTrue(any("missing family" in issue for issue in issues))

    def test_signal_runner_composite_preserves_card_exit_params(self) -> None:
        runner = SignalRunner(alpha_cooldown=1, p2_startup_grace_bars=0, p2_group_cooldown_min=0, p2_max_groups_per_bar=2)
        runner._bar_count = 1  # mirror the normal run() path after the first fresh bar
        alerts = [
            {
                "name": "card_one",
                "family": "A4-PIR",
                "mechanism_type": "oi_divergence",
                "group": "same_group",
                "direction": "short",
                "horizon": 30,
                "timestamp_ms": 1_700_000_000_000,
                "confidence": 2,
                "feature": "position_in_range_4h",
                "feature_value": 0.8,
                "threshold": 0.7,
                "op": ">",
                "physical_confirms": ["oi_change_rate_1h"],
                "alpha_exit_params": {
                    "stop_pct": 0.7,
                    "exit_confirm_bars": 2,
                    "tighten_gap_ratio": 0.3,
                    "mfe_ratchet_threshold": 0.25,
                },
                "alpha_exit_combos": [[{"feature": "position_in_range_4h", "operator": "<", "threshold": 0.5}]],
                "card_id": "card_one",
            }
        ]

        composite = runner._aggregate_p2_by_group(alerts, latest_ts=1_700_000_000_000)

        self.assertEqual(len(composite), 1)
        self.assertEqual(composite[0]["family"], "A4-PIR")
        self.assertEqual(composite[0]["mechanism_type"], "oi_divergence")
        self.assertEqual(composite[0]["alpha_exit_params"]["exit_confirm_bars"], 2)
        self.assertEqual(composite[0]["alpha_exit_params"]["mfe_ratchet_threshold"], 0.25)

    def test_signal_runner_run_keeps_alpha_card_params_in_live_path(self) -> None:
        runner = SignalRunner(alpha_cooldown=1, p2_startup_grace_bars=0, p2_group_cooldown_min=0, p2_max_groups_per_bar=2)
        runner._alpha_checker.check = Mock(
            return_value=[
                {
                    "name": "card_live",
                    "family": "A4-PIR",
                    "mechanism_type": "oi_divergence",
                    "group": "same_group",
                    "feature": "position_in_range_4h",
                    "feature_value": 0.8,
                    "threshold": 0.7,
                    "op": ">",
                    "direction": "short",
                    "horizon": 30,
                    "timestamp_ms": 1_700_000_000_000,
                    "physical_confirms": ["oi_change_rate_1h"],
                    "confidence": 2,
                    "confidence_label": "MEDIUM",
                    "alpha_exit_conditions": [],
                    "alpha_exit_combos": [[{"feature": "position_in_range_4h", "operator": "<", "threshold": 0.5}]],
                    "alpha_exit_params": {
                        "stop_pct": 0.7,
                        "exit_confirm_bars": 2,
                        "tighten_gap_ratio": 0.3,
                    },
                    "stop_pct": 0.7,
                    "rule_str": "same_group",
                    "card_id": "card_live",
                    "trade_ready": True,
                    "desc": "live alpha alert",
                }
            ]
        )
        for detector_name in (
            "_funding_rate",
            "_vwap_twap",
            "_bottom_drought",
            "_vwap_drought",
            "_pos_compression",
            "_taker_exhaust_low",
            "_high_pos_funding",
            "_funding_cycle_oversold",
            "_regime_transition",
        ):
            getattr(runner, detector_name).check_live = Mock(return_value=None)

        df = pd.DataFrame(
            [
                {
                    "timestamp": 1_700_000_000_000,
                    "close": 100.0,
                    "position_in_range_4h": 0.8,
                    "oi_change_rate_5m": 0.02,
                }
            ]
        )

        _raw, composite = runner.run(df)

        self.assertEqual(len(composite), 1)
        self.assertEqual(composite[0]["mechanism_type"], "oi_divergence")
        self.assertEqual(composite[0]["alpha_exit_params"]["exit_confirm_bars"], 2)
        self.assertEqual(composite[0]["alpha_exit_params"]["tighten_gap_ratio"], 0.3)


if __name__ == "__main__":
    unittest.main()