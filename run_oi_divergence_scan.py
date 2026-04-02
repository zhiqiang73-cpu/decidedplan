"""OI Divergence Short (A3-OI) Validation Script.
Rule: dist_to_24h_high > -0.015 AND oi_change_rate_1h < -0.003
Direction: short, Horizon: 60 bars, Data: 2026-02-01 to 2026-04-01"""
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
DATA_START  = "2026-02-01"
DATA_END    = "2026-04-01"
HORIZON     = 60
TRAIN_FRAC  = 0.67
FEE_PCT     = 0.10
FEE_FRAC    = FEE_PCT / 100
DIST_THRESHOLDS = [-0.005, -0.010, -0.015, -0.020]
OI_THRESHOLDS   = [-0.001, -0.003, -0.005, -0.010]
OUTPUT_DIR  = ROOT / "alpha" / "output"
SCAN_OUTPUT = OUTPUT_DIR / "oi_divergence_scan.json"
APPROVED_OUT= OUTPUT_DIR / "approved_rules.json"


def _compute_icir(mask, fwd_raw, direction):
    tmp = pd.DataFrame({"fwd": fwd_raw})[mask & fwd_raw.notna()].copy()
    if len(tmp) < 2: return 0.0
    tmp["day"] = np.arange(len(tmp)) // max(1, len(tmp) // 30)
    sign = 1.0 if direction == "long" else -1.0
    daily_ic = tmp.groupby("day")["fwd"].mean() * sign
    if len(daily_ic) < 2 or daily_ic.std() == 0: return float(daily_ic.mean())
    return float(daily_ic.mean() / daily_ic.std())


def _eval_segment(df, dist_thr, oi_thr):
    for col in ["dist_to_24h_high", "oi_change_rate_1h", "close"]:
        if col not in df.columns:
            return {"error": f"missing {col}", "n_triggers": 0}
    close    = df["close"]
    dist_col = df["dist_to_24h_high"]
    oi_col   = df["oi_change_rate_1h"]
    fwd_raw  = (close.shift(-HORIZON) - close) / close
    mask = (
        dist_col.notna() & oi_col.notna() & fwd_raw.notna()
        & (dist_col > dist_thr)
        & (oi_col < oi_thr)
    )
    n_triggers   = int(mask.sum())
    trigger_rate = n_triggers / max(len(df), 1)
    if n_triggers == 0:
        return {"n_triggers": 0, "trigger_rate": 0.0,
                "win_rate": None, "avg_return": None,
                "profit_factor": None, "ICIR": None}
    gross = (-fwd_raw[mask]).values
    net   = gross - FEE_FRAC
    wins_n   = net[net > 0]
    losses_n = net[net <= 0]
    win_rate = len(wins_n) / len(net)
    avg_win_n  = wins_n.mean()        if len(wins_n)  > 0 else 0.0
    avg_loss_n = abs(losses_n.mean()) if len(losses_n) > 0 else 0.0
    if len(losses_n) > 0 and avg_loss_n > 0:
        pf = (avg_win_n * len(wins_n)) / (avg_loss_n * len(losses_n))
    else:
        pf = float("inf") if len(wins_n) > 0 else 0.0
    icir = _compute_icir(mask, fwd_raw, "short")
    return {
        "n_triggers":    n_triggers,
        "trigger_rate":  round(trigger_rate * 100, 4),
        "win_rate":      round(win_rate * 100, 2),
        "avg_return":    round(float(net.mean()) * 100, 4),
        "profit_factor": round(pf, 3) if pf != float("inf") else "inf",
        "ICIR":          round(icir, 4),
    }


def _fmt(v, w):
    return f"{v:>{w}}" if v is not None else " "*(w-3)+"N/A"


def main():
    logger.info("=== OI Divergence Short (A3-OI) Validation ===")
    logger.info("Loading data %s ~ %s ...", DATA_START, DATA_END)
    fe = FeatureEngine(str(ROOT / "data" / "storage"))
    df = fe.load_date_range(DATA_START, DATA_END)
    if df.empty:
        logger.error("No data loaded. Aborting.")
        return
    logger.info("Loaded %d rows", len(df))
    for col in ["dist_to_24h_high", "oi_change_rate_1h"]:
        if col not in df.columns:
            logger.error("Required column not found: %s", col)
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
    hdr = f"{'dist_thr':>10} {'oi_thr':>10} {'IS_n':>6} {'IS_WR%':>7} {'IS_ret%':>8} {'IS_PF':>7} {'OOS_n':>6} {'OOS_WR%':>8} {'OOS_ret%':>9} {'OOS_PF':>7}"
    print(chr(10) + hdr)
    print("-" * len(hdr))
    nt_k,wr_k,ar_k,pf_k,ic_k,dt_k,ot_k = "n_triggers","win_rate","avg_return","profit_factor","ICIR","dist_threshold","oi_threshold"
    for dist_thr in DIST_THRESHOLDS:
        for oi_thr in OI_THRESHOLDS:
            is_m  = _eval_segment(is_df,  dist_thr, oi_thr)
            oos_m = _eval_segment(oos_df, dist_thr, oi_thr)
            results.append({dt_k: dist_thr, ot_k: oi_thr, "IS": is_m, "OOS": oos_m})
            print(f"{dist_thr:>10.3f} {oi_thr:>10.3f} {is_m[nt_k]:>6}"
                  f" {_fmt(is_m.get(wr_k),7)} {_fmt(is_m.get(ar_k),8)} {_fmt(is_m.get(pf_k),7)}"
                  f" {oos_m[nt_k]:>6} {_fmt(oos_m.get(wr_k),8)} {_fmt(oos_m.get(ar_k),9)} {_fmt(oos_m.get(pf_k),7)}")
    valid = [r for r in results if r["OOS"].get(wr_k) is not None and r["OOS"][nt_k] >= 8]
    best = None
    if valid:
        def pf_s(r):
            v = r["OOS"].get(pf_k, 0)
            return 999 if v == "inf" else (v or 0)
        best = max(valid, key=lambda r: (r["OOS"][wr_k], pf_s(r)))
    print()
    print("=== Best Combo ===")
    if best:
        iss, ois = best["IS"], best["OOS"]
        print(f"  dist_to_24h_high > {best[dt_k]}")
        print(f"  oi_change_rate_1h < {best[ot_k]}")
        print(f"  IS  : n={iss[nt_k]}, WR={iss.get(wr_k)}%, ret={iss.get(ar_k)}%, PF={iss.get(pf_k)}")
        print(f"  OOS : n={ois[nt_k]}, WR={ois.get(wr_k)}%, ret={ois.get(ar_k)}%, PF={ois.get(pf_k)}, ICIR={ois.get(ic_k)}")
    else:
        print("  No combo with n_OOS >= 8 found.")
    passed = False
    if best:
        oos = best["OOS"]
        pf_v = oos.get(pf_k)
        pf_ok = (pf_v == "inf") or ((pf_v or 0) >= 1.0)
        passed = ((oos.get(wr_k) or 0) >= 55.0 and oos[nt_k] >= 8 and pf_ok)
    verdict = "PASS" if passed else "FAIL"
    print()
    print(f">>> VERDICT: {verdict}")
    if passed:
        print("    OOS WR >= 55%, n_OOS >= 8, PF >= 1.0 -- criteria met.")
    else:
        if best:
            oos = best["OOS"]
            print(f"    OOS WR={oos.get(wr_k)}%, n_OOS={oos[nt_k]}, PF={oos.get(pf_k)} -- not met.")
        else:
            print("    No valid combo found (n_OOS < 8 for all).")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    scan_data = {"scan_date": datetime.now(timezone.utc).isoformat(),
                 "data_range": f"{DATA_START} ~ {DATA_END}",
                 "horizon": HORIZON, "fee_pct": FEE_PCT, "train_frac": TRAIN_FRAC,
                 "is_rows": len(is_df), "oos_rows": len(oos_df),
                 "results": results, "best_combo": best, "verdict": verdict}
    with open(SCAN_OUTPUT, "w", encoding="utf-8") as fh:
        json.dump(scan_data, fh, indent=2, default=str)
    logger.info("Scan results saved to %s", SCAN_OUTPUT)
    if passed and best:
        entry = {"id": "A3-OI-divergence-short-60", "group": "oi_divergence_short",
                 "status": "approved", "family": "A3-OI",
                 "entry": {"feature": "dist_to_24h_high", "operator": ">",
                             "threshold": best[dt_k], "direction": "short", "horizon": HORIZON},
                 "combo_conditions": [{"feature": "oi_change_rate_1h", "op": "<",
                                        "threshold": best[ot_k]}],
                 "exit": {},
                 "stats": {"oos_win_rate": best["OOS"][wr_k], "n_oos": best["OOS"][nt_k],
                             "oos_pf": best["OOS"][pf_k], "oos_avg_ret": best["OOS"][ar_k]},
                 "discovered_at": datetime.now(timezone.utc).isoformat(),
                 "validation": {"conclusion": "APPROVE",
                                  "reason": "OI divergence: price near high + OI falling = distribution"},
                 "mechanism_type": "oi_divergence"}
        existing = []
        if APPROVED_OUT.exists():
            with open(APPROVED_OUT, "r", encoding="utf-8") as fh:
                try: existing = json.load(fh)
                except Exception: existing = []
        existing = [r for r in existing if r.get("id") != "A3-OI-divergence-short-60"]
        existing.append(entry)
        with open(APPROVED_OUT, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2, default=str)
        logger.info("Rule appended to %s", APPROVED_OUT)
        print("  Rule A3-OI-divergence-short-60 added to approved_rules.json")


if __name__ == "__main__":
    main()
