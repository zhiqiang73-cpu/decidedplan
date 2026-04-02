"""
Regime Transition Backtester (run_regime_transition_scan.py)

Finds all RANGE_BOUND -> QUIET_TREND transitions in 19 months of klines data
and computes forward return statistics at 15, 30, 60 bar horizons.
"""
from __future__ import annotations
import logging, sys
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
import pyarrow.dataset as ds
from core.dimensions.price_features import compute_price_features
from core.dimensions.trade_flow_features import compute_trade_flow_features
from core.dimensions.liquidity_features import compute_liquidity_features
from core.dimensions.positioning_features import compute_positioning_features


def _load_lightweight(storage_path: Path, start_date: str, end_date: str) -> pd.DataFrame:
    """Load klines + open_interest only (skip agg_trades/book_ticker to save memory)."""
    from datetime import datetime, timezone

    def to_ms(d, eod=False):
        dt = datetime.fromisoformat(d).replace(tzinfo=timezone.utc)
        if eod:
            dt = dt.replace(hour=23, minute=59, second=59)
        return int(dt.timestamp() * 1000)

    s, e = to_ms(start_date), to_ms(end_date, eod=True)

    def load_parquet(endpoint):
        p = storage_path / endpoint
        if not p.exists():
            return pd.DataFrame()
        try:
            table = ds.dataset(p, format="parquet", partitioning="hive").to_table(
                filter=(ds.field("timestamp") >= s) & (ds.field("timestamp") <= e)
            )
            df = table.to_pandas()
            df["timestamp"] = df["timestamp"].astype("int64")
            return df
        except Exception:
            return pd.DataFrame()

    df = load_parquet("klines")
    if df.empty:
        return df
    df = df.sort_values("timestamp").reset_index(drop=True)

    # Merge open_interest via merge_asof
    oi = load_parquet("open_interest")
    if not oi.empty:
        oi = oi.sort_values("timestamp")
        df = pd.merge_asof(df, oi[["timestamp", "open_interest"]], on="timestamp", tolerance=5*60*1000)
        logger.info("  open_interest merged")

    # Merge funding_rate
    fr = load_parquet("funding_rate")
    if not fr.empty:
        fr = fr.sort_values("timestamp")
        df = pd.merge_asof(df, fr[["timestamp", "funding_rate"]], on="timestamp", tolerance=8*60*60*1000)
        logger.info("  funding_rate merged")

    # Compute only the features regime detector needs
    df = compute_price_features(df)
    df = compute_trade_flow_features(df)
    df = compute_liquidity_features(df)
    df = compute_positioning_features(df)
    logger.info("  features computed: %d rows", len(df))
    return df

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

DATA_START = "2024-09-01"
DATA_END   = "2026-04-01"
# Load in quarterly chunks to avoid memory error on 19-month agg_trades
QUARTER_RANGES = [
    ("2024-09-01", "2024-12-31"),
    ("2025-01-01", "2025-03-31"),
    ("2025-04-01", "2025-06-30"),
    ("2025-07-01", "2025-09-30"),
    ("2025-10-01", "2025-12-31"),
    ("2026-01-01", "2026-04-01"),
]
HORIZONS   = [15, 30, 60]

QUIET_TREND    = "QUIET_TREND"
VOLATILE_TREND = "VOLATILE_TREND"
RANGE_BOUND    = "RANGE_BOUND"
VOL_EXPANSION  = "VOL_EXPANSION"
CRISIS         = "CRISIS"
AMP_QUIET_MAX   = 0.0015
AMP_VOLATILE    = 0.0025
AMP_CRISIS      = 0.0050
VOL_SPIKE       = 2.0
VOL_EXTREME     = 3.0
SPREAD_WIDE     = 2.0
SPREAD_CRISIS   = 3.5
OI_DELEVER      = -0.03
RANGE_CENTER_LOW  = 0.30
RANGE_CENTER_HIGH = 0.70
CONFIRM_BARS      = 30   # 30-min confirmation window (instead of 3-bar)


def _safe_get(row, col, default=None):
    if col not in row.index: return default
    try:
        v = float(row[col])
        import math
        return default if math.isnan(v) else v
    except (TypeError, ValueError):
        return default


def _classify_raw(row) -> str:
    amp    = _safe_get(row, "amplitude_ma20",       0.001)
    vol    = _safe_get(row, "volume_vs_ma20",       1.0)
    spread = _safe_get(row, "spread_vs_ma20",       1.0)
    oi_1h  = _safe_get(row, "oi_change_rate_1h",    0.0)
    rpos   = _safe_get(row, "position_in_range_24h",0.5)
    if spread > SPREAD_CRISIS: return CRISIS
    if oi_1h is not None and oi_1h < OI_DELEVER and amp > AMP_VOLATILE: return CRISIS
    if amp > AMP_CRISIS and vol > VOL_EXTREME: return VOL_EXPANSION
    if amp > AMP_VOLATILE and vol > VOL_SPIKE and spread > SPREAD_WIDE: return VOL_EXPANSION
    if RANGE_CENTER_LOW <= (rpos or 0.5) <= RANGE_CENTER_HIGH and amp < AMP_VOLATILE:
        return RANGE_BOUND
    if amp > AMP_VOLATILE: return VOLATILE_TREND
    return QUIET_TREND


def main():
    logger.info("=== Regime Transition Scan: RANGE_BOUND -> QUIET_TREND ===")
    storage_path = ROOT / "data" / "storage"

    # Load quarters sequentially and accumulate (lightweight loader, no agg_trades)
    all_close = []
    all_rows  = []

    for q_start, q_end in QUARTER_RANGES:
        logger.info("Loading %s ~ %s ...", q_start, q_end)
        chunk = _load_lightweight(storage_path, q_start, q_end)
        if chunk.empty:
            logger.warning("  Empty chunk, skipping")
            continue
        all_close.append(chunk["close"].values)
        regime_cols = [c for c in [
            "amplitude_ma20", "volume_vs_ma20", "spread_vs_ma20",
            "oi_change_rate_1h", "position_in_range_24h", "timestamp"
        ] if c in chunk.columns]
        all_rows.append(chunk[regime_cols])
        del chunk
        import gc; gc.collect()

    if not all_close:
        logger.error("No data loaded. Aborting.")
        return

    close = np.concatenate(all_close)
    df = pd.concat(all_rows, ignore_index=True)
    df = df.reset_index(drop=True)
    logger.info("Total rows: %d", len(df))
    n = len(df)

    # ── Walk bar-by-bar: classify + CONFIRM_BARS inertia ────────────────────
    prev_confirmed = QUIET_TREND
    candidate      = QUIET_TREND
    candidate_cnt  = 0
    transition_indices = []

    for i in range(n):
        row = df.iloc[i]
        raw = _classify_raw(row)
        if raw == candidate:
            candidate_cnt += 1
        else:
            candidate     = raw
            candidate_cnt = 1
        confirmed = None
        if candidate_cnt >= CONFIRM_BARS:
            confirmed = candidate
        if (confirmed is not None
                and prev_confirmed == RANGE_BOUND
                and confirmed == QUIET_TREND):
            transition_indices.append(i)
            logger.debug("Transition at bar %d", i)
        if confirmed is not None and confirmed != prev_confirmed:
            prev_confirmed = confirmed

    # Also track VOL_EXPANSION -> QUIET_TREND (volatility exhaustion recovery LONG)
    vol_exhaust_indices = []
    prev2 = QUIET_TREND
    cand2 = QUIET_TREND
    cnt2 = 0
    for i in range(n):
        row = df.iloc[i]
        raw = _classify_raw(row)
        if raw == cand2:
            cnt2 += 1
        else:
            cand2 = raw
            cnt2 = 1
        conf2 = cand2 if cnt2 >= CONFIRM_BARS else None
        if conf2 and prev2 == VOL_EXPANSION and conf2 == QUIET_TREND:
            vol_exhaust_indices.append(i)
        if conf2 and conf2 != prev2:
            prev2 = conf2

    # Directional filter: at transition bar, check position_in_range_24h
    pos_col = "position_in_range_24h"
    if pos_col in df.columns:
        up_breaks   = [i for i in transition_indices
                       if not pd.isna(df[pos_col].iloc[i]) and df[pos_col].iloc[i] > 0.70]
        down_breaks = [i for i in transition_indices
                       if not pd.isna(df[pos_col].iloc[i]) and df[pos_col].iloc[i] < 0.30]
    else:
        up_breaks = down_breaks = []
    logger.info("Found %d RANGE_BOUND -> QUIET_TREND transitions", len(transition_indices))
    logger.info("  Of which: %d break UP (pos>0.70), %d break DOWN (pos<0.30)",
                len(up_breaks), len(down_breaks))
    logger.info("Found %d VOL_EXPANSION -> QUIET_TREND transitions", len(vol_exhaust_indices))

    if not transition_indices:
        print("No transitions found. Possible causes: OI data missing, date range too short.")
        return

    # ── Compute forward returns ──────────────────────────────────────────────
    records = []
    for idx in transition_indices:
        entry_price = close[idx]
        rec = {"bar": idx}
        if entry_price <= 0:
            continue
        for h in HORIZONS:
            exit_idx = idx + h
            if exit_idx < n:
                ret_gross = (close[exit_idx] - entry_price) / entry_price
                rec[f"ret_{h}"] = ret_gross
            else:
                rec[f"ret_{h}"] = None
        records.append(rec)

    fwd = pd.DataFrame(records)
    fwd_pct = fwd.copy()
    for h in HORIZONS:
        fwd_pct[f"ret_{h}"] = fwd_pct[f"ret_{h}"] * 100

    print()
    print("=== Regime Transition Backtest Results ===")
    print(f"Total transitions found: {len(transition_indices)}")
    print()

    fee_pct = 0.10
    fee_frac = fee_pct / 100

    for h in HORIZONS:
        col = f"ret_{h}"
        sub = fwd_pct[col].dropna()
        if len(sub) == 0:
            print(f"Horizon {h:>3}b: no data")
            continue
        gross = sub.values / 100   # back to fraction
        net   = gross - fee_frac
        wins  = net[net > 0]
        losses= net[net <= 0]
        wr = len(wins) / len(net) if len(net) > 0 else 0.0
        avg_win  = wins.mean()        if len(wins)   > 0 else 0.0
        avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0
        if len(losses) > 0 and avg_loss > 0:
            pf = (avg_win * len(wins)) / (avg_loss * len(losses))
        else:
            pf = float("inf") if len(wins) > 0 else 0.0
        print(f"Horizon {h:>3}b: n={len(sub):>4}  WR(net)={wr*100:>6.1f}%  "
              f"avg_net={net.mean()*100:>+7.4f}%  "
              f"median_net={np.median(net)*100:>+7.4f}%  "
              f"PF(net)={pf:>6.3f}")

    # ── VOL_EXPANSION -> QUIET_TREND results ─────────────────────────────────
    print()
    print("=== VOL_EXPANSION -> QUIET_TREND (volatility exhaustion recovery) ===")
    print(f"Total transitions found: {len(vol_exhaust_indices)}")
    if vol_exhaust_indices:
        ve_records = []
        for idx in vol_exhaust_indices:
            entry_price = close[idx]
            rec = {"bar": idx}
            if entry_price <= 0: continue
            for h in HORIZONS:
                exit_idx = idx + h
                if exit_idx < n:
                    rec[f"ret_{h}"] = (close[exit_idx] - entry_price) / entry_price
                else:
                    rec[f"ret_{h}"] = None
            ve_records.append(rec)
        ve_fwd = pd.DataFrame(ve_records)
        for h in HORIZONS:
            col = f"ret_{h}"
            sub = ve_fwd[col].dropna()
            if len(sub) == 0: print(f"Horizon {h:>3}b: no data"); continue
            gross = sub.values
            net = gross - fee_frac
            wins = net[net > 0]; losses = net[net <= 0]
            wr = len(wins) / len(net)
            avg_win = wins.mean() if len(wins) > 0 else 0.0
            avg_loss = abs(losses.mean()) if len(losses) > 0 else 0.0
            pf = (avg_win * len(wins)) / (avg_loss * len(losses)) if len(losses) > 0 and avg_loss > 0 else float("inf")
            print(f"Horizon {h:>3}b: n={len(sub):>4}  WR(net)={wr*100:>6.1f}%  "
                  f"avg_net={net.mean()*100:>+7.4f}%  "
                  f"median_net={np.median(net)*100:>+7.4f}%  "
                  f"PF(net)={pf:>6.3f}")

    # ── Directional subsets ───────────────────────────────────────────────────
    def _eval_subset(label, indices, direction):
        if not indices:
            print(f"\n{label}: no transitions")
            return
        recs = []
        for idx in indices:
            ep = close[idx]
            if ep <= 0: continue
            rec = {}
            for h in HORIZONS:
                xi = idx + h
                ret = (close[xi] - ep) / ep if xi < n else None
                if direction == "short" and ret is not None:
                    ret = -ret
                rec[f"ret_{h}"] = ret
            recs.append(rec)
        sub_df = pd.DataFrame(recs)
        print(f"\n{label} (n={len(indices)}):")
        for h in HORIZONS:
            col = f"ret_{h}"
            vals = sub_df[col].dropna()
            if len(vals) == 0: continue
            g = vals.values
            net_v = g - fee_frac
            wr = (net_v > 0).mean()
            print(f"  {h:>3}b: WR={wr*100:>5.1f}%  avg_net={net_v.mean()*100:>+7.4f}%  n={len(vals)}")

    _eval_subset("RANGE->QUIET UP-break (pos>0.70) → LONG", up_breaks, "long")
    _eval_subset("RANGE->QUIET DOWN-break (pos<0.30) → SHORT", down_breaks, "short")

    print()
    # Summary verdict
    if len(transition_indices) < 5:
        print("VERDICT: INSUFFICIENT DATA (<5 transitions)")
    else:
        col = "ret_30"
        sub30 = fwd_pct[col].dropna()
        if len(sub30) >= 5:
            g30 = sub30.values / 100
            n30 = g30 - fee_frac
            wr30 = (n30 > 0).mean() * 100
            avg30 = n30.mean() * 100
            if wr30 >= 55 and avg30 > 0:
                print(f"VERDICT: PROMISING -- 30b WR={wr30:.1f}%, avg_net={avg30:.4f}%")
            else:
                print(f"VERDICT: MARGINAL -- 30b WR={wr30:.1f}%, avg_net={avg30:.4f}%")


if __name__ == "__main__":
    main()
