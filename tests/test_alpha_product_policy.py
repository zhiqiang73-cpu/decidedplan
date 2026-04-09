from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

from alpha.candidate_review import review_card
from alpha.product_policy import build_product_candidate_board, product_alpha_families
from utils.file_io import read_json_file


class AlphaProductPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.approved_cards = read_json_file(ROOT / "alpha" / "output" / "approved_rules.json", [])

    def test_product_candidate_board_only_keeps_four_live_alpha_families(self) -> None:
        families = [card.get("family") for card in build_product_candidate_board(self.approved_cards)]
        self.assertEqual(families, list(product_alpha_families()))

    def test_product_candidate_board_backfills_snapshot_invalidation_and_stop_logic(self) -> None:
        board = build_product_candidate_board(self.approved_cards)
        a226 = next(card for card in board if card.get("family") == "A2-26")
        self.assertTrue(a226["exit"]["snapshot_required"])
        self.assertTrue(a226["exit"]["invalidation"])
        self.assertEqual(a226["exit"]["exit_method"], "force_decay_vs_entry")
        self.assertEqual(a226["stop_logic"]["type"], "mechanism_hard_stop")
        self.assertTrue(a226["strategy_blueprint"]["snapshot_required"])

    def test_review_card_blocks_candidates_without_live_family(self) -> None:
        card = {
            "id": "tmp-card",
            "status": "pending",
            "mechanism_type": "seller_impulse",
            "entry": {
                "feature": "direction_net_1m",
                "operator": "<",
                "threshold": -0.2,
                "direction": "short",
                "horizon": 60,
            },
            "combo_conditions": [
                {"feature": "spread_vs_ma20", "op": ">", "threshold": 1.5}
            ],
            "stats": {"oos_win_rate": 71.0, "n_oos": 42, "oos_net_return": 0.12},
            "exit": {
                "top3": [
                    {
                        "conditions": [
                            {
                                "feature": "direction_net_1m_vs_entry",
                                "operator": ">",
                                "threshold": 0.1,
                                "source": "force_decay",
                            }
                        ]
                    }
                ],
                "invalidation": [
                    {
                        "conditions": [
                            {
                                "feature": "direction_net_1m_vs_entry",
                                "operator": "<",
                                "threshold": -0.05,
                                "source": "thesis_invalidation",
                            }
                        ]
                    }
                ],
                "snapshot_required": True,
                "exit_method": "force_decay_vs_entry",
                "earliest_pf": 1.2,
                "net_return_with_exit": 0.08,
                "improvement": 0.03,
                "n_samples": 42,
                "triggered_exit_pct": 30.0,
                "exit_reason_counts": {"hard_stop": 4, "time_cap": 20},
            },
            "stop_pct": 0.55,
            "stop_logic": {"type": "mechanism_hard_stop"},
        }

        decision = review_card(card)
        self.assertFalse(decision.keep_pending)
        self.assertTrue(any("live family" in reason for reason in decision.reasons))


if __name__ == "__main__":
    unittest.main()
