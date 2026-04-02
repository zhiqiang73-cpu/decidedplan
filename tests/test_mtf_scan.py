import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

import numpy as np
import pandas as pd

from run_mtf_scan import format_tf_label, normalize_tfs, parse_tf_value, resample_to_tf


class TimeframeParsingTest(unittest.TestCase):
    def test_parse_tf_value_supports_minutes_and_hours(self):
        self.assertEqual(parse_tf_value(5), 5)
        self.assertEqual(parse_tf_value("5"), 5)
        self.assertEqual(parse_tf_value("5m"), 5)
        self.assertEqual(parse_tf_value("15min"), 15)
        self.assertEqual(parse_tf_value("1h"), 60)
        self.assertEqual(parse_tf_value("2h"), 120)

    def test_normalize_tfs_deduplicates_and_preserves_order(self):
        self.assertEqual(normalize_tfs(["5m", "15m", "1h", "60", "5"]), [5, 15, 60])
        self.assertEqual(normalize_tfs(None), [5, 15, 60])

    def test_format_tf_label(self):
        self.assertEqual(format_tf_label(5), "5m")
        self.assertEqual(format_tf_label(15), "15m")
        self.assertEqual(format_tf_label(60), "1h")
        self.assertEqual(format_tf_label(120), "2h")


class ResampleToTfTest(unittest.TestCase):
    def test_resample_uses_time_boundaries_and_drops_incomplete_tail(self):
        base_ts = pd.Timestamp("2026-01-01T00:00:00Z")
        rows = []
        for i in range(7):
            open_price = 100 + i
            close_price = open_price + 0.5
            rows.append(
                {
                    "timestamp": int((base_ts + pd.Timedelta(minutes=i)).timestamp() * 1000),
                    "open": open_price,
                    "high": open_price + 1.0,
                    "low": open_price - 1.0,
                    "close": close_price,
                    "volume": 1.0,
                    "quote_volume": close_price,
                    "trades": 10,
                    "taker_buy_base": 0.4,
                    "taker_buy_quote": close_price * 0.4,
                    "funding_rate": 0.0001,
                    "open_interest": 1000 + i,
                    "long_short_ratio": 1.2,
                }
            )

        df = pd.DataFrame(rows)
        resampled = resample_to_tf(df, 5)

        self.assertEqual(len(resampled), 1)

        first = resampled.iloc[0]
        self.assertEqual(first["timestamp"], rows[0]["timestamp"])
        self.assertEqual(first["open"], 100)
        self.assertEqual(first["high"], 105.0)
        self.assertEqual(first["low"], 99.0)
        self.assertEqual(first["close"], 104.5)
        self.assertEqual(first["volume"], 5.0)
        self.assertAlmostEqual(first["quote_volume"], sum(row["quote_volume"] for row in rows[:5]))
        self.assertEqual(first["trades"], 50)
        self.assertAlmostEqual(first["taker_buy_base"], 2.0)
        self.assertAlmostEqual(first["taker_buy_quote"], sum(row["taker_buy_quote"] for row in rows[:5]))
        self.assertEqual(first["open_interest"], 1004)
        self.assertEqual(first["long_short_ratio"], 1.2)

    def test_resample_1m_returns_copy(self):
        df = pd.DataFrame(
            {
                "timestamp": [1, 2],
                "open": [100.0, 101.0],
                "high": [101.0, 102.0],
                "low": [99.0, 100.0],
                "close": [100.5, 101.5],
                "volume": [1.0, 2.0],
                "quote_volume": [100.5, 203.0],
                "trades": [10, 20],
            }
        )

        resampled = resample_to_tf(df, 1)
        self.assertEqual(len(resampled), 2)
        self.assertTrue(np.array_equal(resampled["close"].values, df["close"].values))
        self.assertIsNot(resampled, df)


if __name__ == "__main__":
    unittest.main()
