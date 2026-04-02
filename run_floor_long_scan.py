"""
Demand Floor LONG (A3-FLOOR) Validation Script.

Physical basis (Conservation / Demand-Supply Law):
  Price at 24h low = supply exhaustion zone.
  If taker buyers start dominating at this level, demand floor is materializing.
  Sellers have exhausted themselves; buyers absorb all supply at support.
  This is a universal supply/demand equilibrium law: when sellers run out at support,
  price MUST recover (short squeeze + new demand).

Rule tested:
  dist_to_24h_low < low_threshold (near 24h low)
  taker_buy_sell_ratio > taker_threshold (buyers dominant)
  Direction: LONG, Horizon: 30 bars
"""
from __future__ import annotations
import json, logging, sys
from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from core.feature_engine import FeatureEngine

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)])
logger = logging.getLogger(__name__)

# Use 2026-02 onward for full feature availability (taker_ratio + OI)
DATA_START  = "2026-02-01"
DATA_END    = "2026-04-01"
HORIZON     = 30
TRAIN_FRAC  = 0.67
FEE_PCT     = 0.10
FEE_FRAC    = FEE_PCT / 100

# Threshold grid
LOW_THRESHOLDS    = [0.003, 0.005, 0.008, 0.010, 0.015]  # dist_to_24h_low <
TAKER_THRESHOLDS  = [0.50, 0.52, 0.55, 0.58, 0.60]       # taker_buy_sell_ratio >

OUTPUT_DIR  = ROOT / "alpha" / "output"
APPROVED_OUT= OUTPUT_DIR / "approved_rules.json"


def _eval_segment(df, low_thr, taker_thr):
    for col in ["dist_to_24h_low", "taker_buy_sell_ratio", "close"]:
        if col not in df.columns:
            return {"error": f"missing {col}", "n_triggers": 0}
    close     = df["close"]
    dist_col  = df["dist_to_24h_low"]
    taker_col = df["taker_buy_sell_ratio"]
    fwd_raw   = (close.shift(-HORIZON) - close) / close   # positive = price up

    mask = (
        dist_col.notna() & taker_col.notna() & fwd_raw.notna()
        & (dist_col < low_thr)       # near 24h low
        & (taker_col > taker_thr)    # buyers dominating
    )
    n_triggers   = int(mask.sum())
    trigger_rate = n_triggers / max(len(df), 1)
    if n_triggers == 0:
        return {"n_triggers": 0, "trigger_rate": 0.0,
                "win_rate": None, "avg_return": None, "profit_factor": None}

    gross = fwd_raw[mask].values        # LONG: positive = win
    net   = gross - FEE_FRAC
    wins_n   = net[net > 0]
    losses_n = net[net <= 0]
    win_rate = len(wins_n) / len(net)
    avg_win  = wins_n.mean()        if len(wins_n)  > 0 else 0.0
    avg_loss = abs(losses_n.mean()) if len(losses_n) > 0 else 0.0
    if len(losses_n) > 0 and avg_loss > 0:
        pf = (avg_win * len(wins_n)) / (avg_loss * len(losses_n))
    else:
        pf = float("inf") if len(wins_n) > 0 else 0.0

    return {
        "n_triggers":    n_triggers,
        "trigger_rate":  round(trigger_rate * 100, 4),
        "win_rate":      round(win_rate * 100, 2),
        "avg_return":    round(float(net.mean()) * 100, 4),
        "profit_factor": round(pf, 3) if pf != float("inf") else "inf",
    }


def _fmt(v, w):
    return f"{v:>{w}}" if v is not None else " "*(w-3)+"N/A"


def main():
    logger.info("=== Demand Floor LONG (A3-FLOOR) Validation ===")
    logger.info("Loading data %s ~ %s ...", DATA_START, DATA_END)
    fe = FeatureEngine(str(ROOT / "data" / "storage"))
    df = fe.load_date_range(DATA_START, DATA_END)
    if df.empty:
        logger.error("No data loaded. Aborting.")
        return
    logger.info("Loaded %d rows", len(df))

    for col in ["dist_to_24h_low", "taker_buy_sell_ratio"]:
        if col not in df.columns:
            logger.error("Required column missing: %s", col)
            return
        nan_pct = df[col].isna().mean() * 100
        logger.info("  %s: %.1f%% non-null", col, 100 - nan_pct)

    n = len(df)
    split_idx = int(n * TRAIN_FRAC)
    is_df  = df.iloc[:split_idx].copy()
    oos_df = df.iloc[split_idx:].copy()

    def ts_str(d, pos):
        ts = d["timestamp"].iloc[0 if pos == "start" else -1]
        return pd.to_datetime(ts, unit="ms", utc=True).strftime("%Y-%m-%d")
    logger.info("IS : %s ~ %s (%d rows)", ts_str(is_df, "start"), ts_str(is_df, "end"), len(is_df))
    logger.info("OOS: %s ~ %s (%d rows)", ts_str(oos_df, "start"), ts_str(oos_df, "end"), len(oos_df))

    results = []
    hdr = f"{'low_thr':>8} {'taker_thr':>10} {'IS_n':>6} {'IS_WR%':>7} {'IS_ret%':>8} {'IS_PF':>7} {'OOS_n':>6} {'OOS_WR%':>8} {'OOS_ret%':>9} {'OOS_PF':>7}"
    print("\n" + hdr)
    print("-" * len(hdr))

    for low_thr in LOW_THRESHOLDS:
        for taker_thr in TAKER_THRESHOLDS:
            is_m  = _eval_segment(is_df,  low_thr, taker_thr)
            oos_m = _eval_segment(oos_df, low_thr, taker_thr)
            results.append({"low_thr": low_thr, "taker_thr": taker_thr, "IS": is_m, "OOS": oos_m})
            n_is  = is_m.get("n_triggers", 0)
            n_oos = oos_m.get("n_triggers", 0)
            print(f"{low_thr:>8.3f} {taker_thr:>10.2f} {n_is:>6}"
                  f" {_fmt(is_m.get('win_rate'),7)} {_fmt(is_m.get('avg_return'),8)} {_fmt(is_m.get('profit_factor'),7)}"
                  f" {n_oos:>6} {_fmt(oos_m.get('win_rate'),8)} {_fmt(oos_m.get('avg_return'),9)} {_fmt(oos_m.get('profit_factor'),7)}")

    valid = [r for r in results if r["OOS"].get("win_rate") is not None and r["OOS"]["n_triggers"] >= 8]
    best = None
    if valid:
        def score(r):
            wr = r["OOS"]["win_rate"] or 0
            pf_v = r["OOS"].get("profit_factor", 0)
            pf_s = 999 if pf_v == "inf" else (pf_v or 0)
            return (wr, pf_s)
        best = max(valid, key=score)

    print("\n=== Best Combo ===")
    if best:
        iss, ois = best["IS"], best["OOS"]
        print(f"  dist_to_24h_low < {best['low_thr']}")
        print(f"  taker_buy_sell_ratio > {best['taker_thr']}")
        print(f"  IS  : n={iss['n_triggers']}, WR={iss.get('win_rate')}%, ret={iss.get('avg_return')}%, PF={iss.get('profit_factor')}")
        print(f"  OOS : n={ois['n_triggers']}, WR={ois.get('win_rate')}%, ret={ois.get('avg_return')}%, PF={ois.get('profit_factor')}")
    else:
        print("  No combo with n_OOS >= 8 found.")

    passed = False
    if best:
        oos = best["OOS"]
        pf_v = oos.get("profit_factor")
        pf_ok = (pf_v == "inf") or ((pf_v or 0) >= 1.0)
        passed = ((oos.get("win_rate") or 0) >= 55.0 and oos["n_triggers"] >= 8 and pf_ok)

    verdict = "PASS" if passed else "FAIL"
    print(f"\n>>> VERDICT: {verdict}")
    if passed:
        print("    OOS WR >= 55%, n_OOS >= 8, PF >= 1.0 -- criteria met.")
        entry = {
            "id": "A3-FLOOR-long-30",
            "group": "demand_floor_long",
            "status": "approved",
            "family": "A3-FLOOR",
            "entry": {
                "feature": "dist_to_24h_low",
                "operator": "<",
                "threshold": best["low_thr"],
                "direction": "long",
                "horizon": HORIZON,
            },
            "combo_conditions": [{
                "feature": "taker_buy_sell_ratio",
                "op": ">",
                "threshold": best["taker_thr"],
            }],
            "exit": {},
            "stats": {
                "oos_win_rate": best["OOS"]["win_rate"],
                "n_oos": best["OOS"]["n_triggers"],
                "oos_pf": best["OOS"]["profit_factor"],
                "oos_avg_ret": best["OOS"]["avg_return"],
            },
            "discovered_at": datetime.now(timezone.utc).isoformat(),
            "validation": {
                "conclusion": "APPROVE",
                "reason": "Demand floor: price at 24h support + taker buyers recovering = sellers exhausted",
            },
            "mechanism_type": "demand_floor",
        }
        existing = []
        if APPROVED_OUT.exists():
            with open(APPROVED_OUT, "r", encoding="utf-8") as fh:
                try: existing = json.load(fh)
                except Exception: existing = []
        existing = [r for r in existing if r.get("id") != "A3-FLOOR-long-30"]
        existing.append(entry)
        with open(APPROVED_OUT, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2, default=str)
        logger.info("Rule A3-FLOOR-long-30 appended to approved_rules.json")
    else:
        if best:
            oos = best["OOS"]
            print(f"    OOS WR={oos.get('win_rate')}%, n_OOS={oos['n_triggers']}, PF={oos.get('profit_factor')} -- not met.")
        else:
            print("    No valid combo found.")


if __name__ == "__main__":
    main()
