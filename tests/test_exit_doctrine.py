from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

from monitor.alpha_rules import validate_approved_rule_pool
from monitor.exit_policy_config import ExitParams
from monitor.live_catalog import validate_live_strategy_specs
from monitor.smart_exit_policy import build_runtime_state, evaluate_exit_action
from utils.file_io import read_json_file


class ExitDoctrineTests(unittest.TestCase):
    def test_live_specs_declare_physical_exit_contracts(self) -> None:
        self.assertEqual(validate_live_strategy_specs(), [])

    def test_approved_rule_pool_keeps_physical_exit_contracts(self) -> None:
        approved = read_json_file(ROOT / "alpha" / "output" / "approved_rules.json", [])
        self.assertEqual(validate_approved_rule_pool(approved), [])

    def test_authoritative_docs_repeat_vs_entry_and_safety_cap_doctrine(self) -> None:
        docs = (
            ROOT / "AGENTS.md",
            ROOT / "CLAUDE.md",
            ROOT / "LIVE_STRATEGY_LOGIC.md",
        )
        for path in docs:
            text = path.read_text(encoding="utf-8")
            self.assertIn("vs_entry", text, msg=str(path))
            self.assertIn("safety_cap", text, msg=str(path))
            self.assertRegex(text, r"研究观察窗|观察窗", msg=str(path))

    def test_p18_note_is_explicitly_marked_legacy(self) -> None:
        text = (ROOT / "docs" / "notes" / "P1-8_STRATEGY_CARD.md").read_text(encoding="utf-8").lower()
        self.assertIn("legacy research note", text)
        self.assertNotIn("fixed hold recommended", text)

    def test_safety_cap_stays_after_dynamic_exit_logic(self) -> None:
        runtime_state = build_runtime_state()
        runtime_state["bars_held"] = 5
        decision = evaluate_exit_action(
            position={
                "family": "ALPHA::demo::short::5",
                "direction": "short",
                "entry_price": 100.0,
                "hold_bars": 5,
                "entry_snapshot": {
                    "alpha_exit_conditions": [
                        {
                            "feature": "oi_change_rate_5m",
                            "operator": "<",
                            "threshold": -0.01,
                        }
                    ]
                },
            },
            close=99.0,
            features={"close": 99.0, "oi_change_rate_5m": -0.02},
            runtime_state=runtime_state,
            params=ExitParams(
                stop_pct=5.0,
                protect_start_pct=99.0,
                protect_gap_ratio=0.5,
                protect_floor_pct=0.03,
                min_hold_bars=1,
                max_hold_factor=1,
                exit_confirm_bars=1,
            ),
        )
        self.assertEqual(decision["reason"], "logic_complete")


if __name__ == "__main__":
    unittest.main()
