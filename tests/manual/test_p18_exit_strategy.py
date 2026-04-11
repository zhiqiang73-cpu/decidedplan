"""
P1-8 Exit Strategy Validation
Test exit strategies against actual price data and measure profitability
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import numpy as np
from core.feature_engine import FeatureEngine

FEATURES_START = "2024-10-01"
FEATURES_END = "2026-03-16"
TRADES_CSV = Path("monitor/output/pipeline_backtest_new_signals/trades.csv")

features_df = pd.DataFrame()
p18_trades = pd.DataFrame()
ts_col = np.array([])
close_col = np.array([])
ts_to_idx: dict[int, int] = {}


def load_validation_inputs() -> None:
    global features_df, p18_trades, ts_col, close_col, ts_to_idx

    engine = FeatureEngine()
    features_df = engine.load_date_range(FEATURES_START, FEATURES_END)
    trades_df = pd.read_csv(TRADES_CSV)
    p18_trades = trades_df[trades_df["rule"].str.contains("P1-8|vwap_vol")].copy()

    ts_col = features_df["timestamp"].values
    close_col = features_df["close"].values
    ts_to_idx = {int(ts): i for i, ts in enumerate(ts_col)}

# Exit conditions for LONG
LONG_COMBOS = [
    # Combo A
    [("delta_vwap_deviation", ">", 0.009976),
     ("delta_position_in_range_24h", ">", 0.103100),
     ("delta_dist_to_24h_high", ">", 0.009658)],
    # Combo B
    [("delta_vwap_deviation", ">", 0.009976),
     ("delta_dist_to_24h_high", ">", 0.009658),
     ("delta_position_in_range_4h", ">", 0.357215)],
    # Combo C
    [("delta_vwap_deviation", ">", 0.009976),
     ("delta_position_in_range_24h", ">", 0.103100),
     ("delta_position_in_range_4h", ">", 0.357215)],
]

# Exit conditions for SHORT
SHORT_COMBOS = [
    # Combo A
    [("delta_dist_to_24h_high", "<", -0.005419),
     ("delta_vwap_deviation", "<", -0.006709),
     ("delta_dist_to_24h_low", "<", -0.005438)],
    # Combo B
    [("delta_position_in_range_24h", "<", -0.076940),
     ("delta_vwap_deviation", "<", -0.006709),
     ("delta_dist_to_24h_low", "<", -0.005438)],
    # Combo C
    [("delta_position_in_range_24h", "<", -0.076940),
     ("delta_dist_to_24h_high", "<", -0.005419),
     ("delta_vwap_deviation", "<", -0.006709)],
]

def load_feature_value(entry_idx, current_idx, feature_name, entry_value=None):
    """Load a feature value at a specific bar"""
    if current_idx < 0 or current_idx >= len(features_df):
        return None

    base_feature = feature_name.replace("delta_", "")
    if base_feature not in features_df.columns:
        return None

    current_value = features_df.iloc[current_idx][base_feature]

    if feature_name.startswith("delta_"):
        if entry_value is None:
            entry_value = features_df.iloc[entry_idx][base_feature]
        return current_value - entry_value if pd.notna(entry_value) and pd.notna(current_value) else None
    else:
        return current_value

def test_combo_trigger(entry_idx, offset, combo, entry_features):
    """Check if a combo triggers at this offset"""
    current_idx = entry_idx + offset
    if current_idx >= len(features_df):
        return False

    for feature_name, op, threshold in combo:
        value = load_feature_value(entry_idx, current_idx, feature_name,
                                  entry_features.get(feature_name))
        if value is None:
            return False

        if op == ">":
            if not (value > threshold):
                return False
        else:  # op == "<"
            if not (value < threshold):
                return False

    return True

def test_exit_strategy(trades, combos, max_bars=120, fee_pct=0.04):
    """Test exit strategy on trades"""
    results = []

    for _, trade in trades.iterrows():
        ts_ms = int(trade["timestamp_ms"])
        if ts_ms not in ts_to_idx:
            continue

        entry_idx = ts_to_idx[ts_ms]
        entry_price = close_col[entry_idx]
        direction = trade["direction"]

        if entry_idx + max_bars >= len(close_col):
            continue

        # Load entry features (for delta calculations)
        entry_features = {}
        for combo in combos:
            for feature_name, _, _ in combo:
                base_feature = feature_name.replace("delta_", "")
                if base_feature not in entry_features and base_feature in features_df.columns:
                    entry_features[base_feature] = features_df.iloc[entry_idx][base_feature]

        # Find exit bar: earliest trigger or max_bars
        exit_offset = max_bars
        combo_triggered = None

        for offset in range(3, max_bars + 1):  # min 3 bars hold
            idx = entry_idx + offset
            if idx >= len(close_col):
                break

            # Check hard stop loss
            if direction == "long":
                pnl = (close_col[idx] - entry_price) / entry_price * 100
            else:
                pnl = (entry_price - close_col[idx]) / entry_price * 100

            if pnl < -0.3:  # hard stop loss
                exit_offset = offset
                break

            # Check combos (earliest trigger)
            for combo_idx, combo in enumerate(combos):
                if test_combo_trigger(entry_idx, offset, combo, entry_features):
                    exit_offset = offset
                    combo_triggered = combo_idx
                    break

            if combo_triggered is not None:
                break

        # Calculate final P&L
        final_idx = entry_idx + exit_offset
        if final_idx >= len(close_col):
            final_idx = len(close_col) - 1

        if direction == "long":
            final_pnl = (close_col[final_idx] - entry_price) / entry_price * 100
        else:
            final_pnl = (entry_price - close_col[final_idx]) / entry_price * 100

        net_pnl = final_pnl - fee_pct

        results.append({
            "direction": direction,
            "exit_offset": exit_offset,
            "gross_pnl": final_pnl,
            "net_pnl": net_pnl,
            "combo_triggered": combo_triggered,
        })

    # Calculate metrics
    rdf = pd.DataFrame(results)
    if len(rdf) == 0:
        return None

    n = len(rdf)
    wr = (rdf["net_pnl"] > 0).mean() * 100
    avg_net = rdf["net_pnl"].mean()
    total_net = rdf["net_pnl"].sum()
    wins = rdf[rdf["net_pnl"] > 0]["net_pnl"].sum()
    losses = abs(rdf[rdf["net_pnl"] <= 0]["net_pnl"].sum())
    pf = wins / losses if losses > 0 else float("inf")

    return {
        "trades": n,
        "win_rate": round(wr, 1),
        "avg_net_pct": round(avg_net, 4),
        "total_net_pct": round(total_net, 4),
        "profit_factor": round(pf, 2),
        "avg_exit_bar": round(rdf["exit_offset"].mean(), 1),
        "details": rdf,
    }

if __name__ == "__main__":
    load_validation_inputs()
    print(f"Features loaded: {features_df.shape[0]} bars")
    print(f"P1-8 trades: {len(p18_trades)}")

    print("\n" + "="*70)
    print("Testing P1-8 Exit Strategies")
    print("="*70)

    for direction in ["long", "short"]:
        print(f"\n{direction.upper()} Direction:")
        sub_trades = p18_trades[p18_trades["direction"] == direction]

        combos = LONG_COMBOS if direction == "long" else SHORT_COMBOS

        print(f"\n  Legacy baselines (fixed hold benchmark only):")
        for hold_bars in [35, 90]:
            results = []
            for _, trade in sub_trades.iterrows():
                ts_ms = int(trade["timestamp_ms"])
                if ts_ms not in ts_to_idx:
                    continue
                entry_idx = ts_to_idx[ts_ms]
                entry_price = close_col[entry_idx]

                exit_idx = min(entry_idx + hold_bars, len(close_col) - 1)

                if direction == "long":
                    pnl = (close_col[exit_idx] - entry_price) / entry_price * 100
                else:
                    pnl = (entry_price - close_col[exit_idx]) / entry_price * 100

                results.append({"net_pnl": pnl - 0.04})

            if results:
                rdf = pd.DataFrame(results)
                wr = (rdf["net_pnl"] > 0).mean() * 100
                total_net = rdf["net_pnl"].sum()
                wins = rdf[rdf["net_pnl"] > 0]["net_pnl"].sum()
                losses = abs(rdf[rdf["net_pnl"] <= 0]["net_pnl"].sum())
                pf = wins / losses if losses > 0 else float("inf")
                print(f"    Legacy fixed-hold {hold_bars}bar: WR={wr:.1f}%, total={total_net:.4f}%, PF={pf:.2f}")

        print(f"\n  Combo strategy (earliest trigger):")
        result = test_exit_strategy(sub_trades, combos)
        if result:
            print(
                f"    {result['trades']:3d} trades: WR={result['win_rate']:.1f}%, "
                f"total={result['total_net_pct']:+.4f}%, PF={result['profit_factor']:.2f}, "
                f"avg exit={result['avg_exit_bar']:.0f}bar"
            )

    print("\n" + "="*70)
