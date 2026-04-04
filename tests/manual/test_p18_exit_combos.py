"""
P1-8 Exit Condition Combo Testing
Test single, double, and triple condition combinations
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np
from itertools import combinations

ANALYSIS_CSV = Path("monitor/output/p18_exit_analysis/trade_features_at_mfe.csv")


def load_analysis_frame(csv_path: Path = ANALYSIS_CSV) -> pd.DataFrame:
    return pd.read_csv(csv_path)

# Top conditions from effect size analysis (delta features only)
LONG_TOP_FEATURES = [
    "vwap_deviation",  # delta_vwap_deviation
    "position_in_range_24h",  # delta_position_in_range_24h
    "dist_to_24h_high",  # delta_dist_to_24h_high
    "position_in_range_4h",  # delta_position_in_range_4h
    "amplitude_ma20",  # delta_amplitude_ma20
]

SHORT_TOP_FEATURES = [
    "position_in_range_24h",  # delta_position_in_range_24h
    "dist_to_24h_high",  # delta_dist_to_24h_high
    "vwap_deviation",  # delta_vwap_deviation
    "dist_to_24h_low",  # delta_dist_to_24h_low
    "position_in_range_4h",  # delta_position_in_range_4h
]

def get_condition_threshold(data, feature):
    """Get median value to use as threshold (using delta_ features)"""
    col = f"delta_{feature}"
    if col not in data.columns:
        return None
    vals = data[col].dropna()
    if len(vals) == 0:
        return None
    return vals.median()

def test_combo(data, conditions):
    """Test a combination of conditions
    conditions: list of (feature, op, threshold) tuples
    Returns: dict with trigger stats
    """
    mask = None
    for feature, op, threshold in conditions:
        col = f"delta_{feature}"
        if col not in data.columns:
            return None

        if op == ">":
            cond = data[col] > threshold
        else:
            cond = data[col] < threshold

        if mask is None:
            mask = cond
        else:
            mask = mask & cond

    trigger_count = mask.sum()
    trigger_rate = trigger_count / len(data) if len(data) > 0 else 0
    avg_offset = data.loc[mask, "mfe_offset"].mean() if trigger_count > 0 else 0

    return {
        "trigger_rate": trigger_rate,
        "avg_offset": avg_offset,
        "trigger_count": trigger_count,
        "total": len(data),
    }

def analyze_direction(data, direction, top_features):
    """Analyze a single direction (LONG or SHORT)"""
    print(f"\n{'='*70}")
    print(f"{direction.upper()} Direction Exit Conditions")
    print(f"{'='*70}\n")

    sub_data = data[data["direction"] == direction].reset_index(drop=True)
    print(f"Total trades: {len(sub_data)}")

    # Get thresholds for top features
    thresholds = {}
    for feature in top_features:
        thresh = get_condition_threshold(sub_data, feature)
        if thresh is not None:
            thresholds[feature] = thresh

    print(f"\nTop feature thresholds (median at MFE peak):")
    for feat, thresh in thresholds.items():
        print(f"  {feat}: {thresh:.6f}")

    # Test single conditions
    print(f"\n--- Testing Single Conditions ---")
    single_results = []
    for feat in top_features:
        if feat not in thresholds:
            continue
        thresh = thresholds[feat]

        # Try both > and < (pick the one that makes sense based on delta direction)
        sub_mean = sub_data[f"delta_{feat}"].mean()
        if sub_mean > 0:
            op = ">"
        else:
            op = "<"

        result = test_combo(sub_data, [(feat, op, thresh)])
        if result:
            single_results.append({
                "condition": f"{feat} {op} {thresh:.6f}",
                "trigger_rate": result["trigger_rate"],
                "trigger_count": result["trigger_count"],
                "avg_offset": result["avg_offset"],
            })
            print(f"  {feat:40s} {op} {thresh:+.6f}: {result['trigger_rate']:.1%} ({result['trigger_count']} trades)")

    single_results.sort(key=lambda x: x["trigger_rate"], reverse=True)

    # Test double conditions (top 5 single conditions)
    print(f"\n--- Testing Double Conditions (Top 5 combos) ---")
    top_5_features = [r["condition"].split()[0] for r in single_results[:5]]
    double_results = []

    for feat1, feat2 in combinations(top_5_features, 2):
        if feat1 not in thresholds or feat2 not in thresholds:
            continue

        thresh1 = thresholds[feat1]
        thresh2 = thresholds[feat2]

        # Determine operators based on mean direction
        op1 = ">" if sub_data[f"delta_{feat1}"].mean() > 0 else "<"
        op2 = ">" if sub_data[f"delta_{feat2}"].mean() > 0 else "<"

        result = test_combo(sub_data, [(feat1, op1, thresh1), (feat2, op2, thresh2)])
        if result and result["trigger_count"] >= 5:  # at least 5 trades
            double_results.append({
                "condition": f"{feat1} {op1} {thresh1:.6f} AND {feat2} {op2} {thresh2:.6f}",
                "trigger_rate": result["trigger_rate"],
                "trigger_count": result["trigger_count"],
                "avg_offset": result["avg_offset"],
            })

    double_results.sort(key=lambda x: x["trigger_rate"], reverse=True)
    for r in double_results[:10]:
        print(f"  {r['trigger_rate']:.1%} ({r['trigger_count']:3d} trades): {r['condition']}")

    # Test triple conditions (top 5 features)
    print(f"\n--- Testing Triple Conditions (Top 3 combos) ---")
    triple_results = []

    for feat1, feat2, feat3 in combinations(top_5_features, 3):
        if feat1 not in thresholds or feat2 not in thresholds or feat3 not in thresholds:
            continue

        thresh1 = thresholds[feat1]
        thresh2 = thresholds[feat2]
        thresh3 = thresholds[feat3]

        op1 = ">" if sub_data[f"delta_{feat1}"].mean() > 0 else "<"
        op2 = ">" if sub_data[f"delta_{feat2}"].mean() > 0 else "<"
        op3 = ">" if sub_data[f"delta_{feat3}"].mean() > 0 else "<"

        result = test_combo(sub_data, [(feat1, op1, thresh1), (feat2, op2, thresh2), (feat3, op3, thresh3)])
        if result and result["trigger_count"] >= 5:
            triple_results.append({
                "condition": f"{feat1} {op1} + {feat2} {op2} + {feat3} {op3}",
                "trigger_rate": result["trigger_rate"],
                "trigger_count": result["trigger_count"],
                "avg_offset": result["avg_offset"],
                "features": (feat1, feat2, feat3),
                "ops": (op1, op2, op3),
                "thresholds": (thresh1, thresh2, thresh3),
            })

    triple_results.sort(key=lambda x: x["trigger_rate"], reverse=True)
    for r in triple_results[:10]:
        print(f"  {r['trigger_rate']:.1%} ({r['trigger_count']:3d} trades): {r['condition']}")

    # Recommend top 3 combos
    print(f"\n--- Top 3 Recommended Exit Combos ---")
    top_3 = triple_results[:3] if len(triple_results) >= 3 else (double_results[:3] if len(double_results) >= 3 else single_results[:3])

    for i, combo in enumerate(top_3, 1):
        print(f"\nCombo {i}: {combo['condition']}")
        print(f"  Trigger rate: {combo['trigger_rate']:.1%} ({combo['trigger_count']} trades)")
        print(f"  Avg offset to peak: {combo['avg_offset']:.1f} bars")

    return {
        "single": single_results,
        "double": double_results,
        "triple": triple_results,
        "top_3": top_3,
    }

if __name__ == "__main__":
    print("="*70)
    print("P1-8 Exit Condition Combination Testing")
    print("="*70)

    df = load_analysis_frame()
    long_results = analyze_direction(df, "long", LONG_TOP_FEATURES)
    short_results = analyze_direction(df, "short", SHORT_TOP_FEATURES)

    print("\n" + "="*70)
    print("Summary")
    print("="*70)
    print(f"\nLONG: Top combo triggers {long_results['top_3'][0]['trigger_rate']:.1%} of trades")
    print(f"SHORT: Top combo triggers {short_results['top_3'][0]['trigger_rate']:.1%} of trades")
