"""
Lightweight LONG Alpha scan for BTC.

- Loads klines + funding_rate + open_interest + taker_ratio
- Skips agg_trades, book_ticker, liquidations, mark_price
- Computes PRICE / TRADE_FLOW / LIQUIDITY / POSITIONING features only
- Scans horizons [30, 60]
- Mines LONG atoms and validates with walk-forward
"""
from __future__ import annotations

import json
import logging
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_bootstrap import bootstrap_runtime
bootstrap_runtime()

import pandas as pd

from core.feature_engine import FeatureEngine
from alpha.scanner import FeatureScanner, FEATURE_DIM
from alpha.causal_atoms import AtomMiner
from alpha.walk_forward import WalkForwardValidator


DATA_DAYS = 60
HORIZONS = [30, 60]
TRAIN_FRAC = 0.67
FEE_PCT = 0.10
SIDE_ENDPOINTS = ["funding_rate", "open_interest", "taker_ratio"]
FEATURE_DIMS = ["PRICE", "TRADE_FLOW", "LIQUIDITY", "POSITIONING"]
TOP_N = 20

OUTPUT_PATH = ROOT / "alpha" / "output" / "long_candidates.json"


def _setup_logging() -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )
    return logging.getLogger(__name__)


def _resolve_date_range(days: int) -> tuple[str, str]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    return start.isoformat(), end.isoformat()


def _fmt(val, width: int, fmt: str | None = None) -> str:
    if val is None:
        return "NA".rjust(width)
    if isinstance(val, float) and math.isinf(val):
        return "inf".rjust(width)
    if fmt is None:
        return str(val).rjust(width)
    return format(val, fmt).rjust(width)


def _select_scan_features(df: pd.DataFrame, scanner: FeatureScanner, logger: logging.Logger) -> list[str]:
    base = scanner._auto_select_features(df)
    allowed = {d.upper() for d in FEATURE_DIMS}
    filtered = [f for f in base if FEATURE_DIM.get(f) in allowed]
    if not filtered:
        logger.warning("No features matched requested dimensions; falling back to auto-selected list")
        return base
    return filtered


def _passes_thresholds(report: dict) -> bool:
    oos = report.get("OOS", {}) or {}
    if oos.get("win_rate") is None:
        return False
    if (oos.get("n_triggers") or 0) < 20:
        return False
    if (oos.get("win_rate") or 0) < 60:
        return False
    pf = oos.get("profit_factor")
    if pf is None:
        return False
    if isinstance(pf, float) and math.isinf(pf):
        pf_val = float("inf")
    else:
        pf_val = float(pf)
    if pf_val < 1.0:
        return False
    if (report.get("degradation") or 0) < 0.4:
        return False
    return True


def main() -> int:
    logger = _setup_logging()

    start_date, end_date = _resolve_date_range(DATA_DAYS)
    logger.info("=== LONG Alpha Scan (lightweight) ===")
    logger.info("Date range: %s ~ %s (%d days)", start_date, end_date, DATA_DAYS)
    logger.info("Endpoints: klines + %s", ", ".join(SIDE_ENDPOINTS))
    logger.info("Feature dims: %s", ", ".join(FEATURE_DIMS))

    fe = FeatureEngine(str(ROOT / "data" / "storage"))
    df = fe.load_date_range(
        start_date,
        end_date,
        side_endpoints=SIDE_ENDPOINTS,
        include_heavy=False,
        feature_dims=FEATURE_DIMS,
    )
    if df.empty:
        logger.error("No data loaded. Aborting.")
        return 1

    ts_start = pd.to_datetime(df["timestamp"].iloc[0], unit="ms", utc=True)
    ts_end = pd.to_datetime(df["timestamp"].iloc[-1], unit="ms", utc=True)
    logger.info("Loaded %s rows (%s ~ %s)", f"{len(df):,}", ts_start.date(), ts_end.date())

    scanner = FeatureScanner(horizons=HORIZONS)
    df = scanner.add_forward_returns(df)

    features = _select_scan_features(df, scanner, logger)
    logger.info("Scanning %d features x %d horizons", len(features), len(HORIZONS))

    scan_df = scanner.scan_all(df, features=features, horizons=HORIZONS)
    if scan_df.empty:
        logger.warning("Scan returned no results.")
        return 0

    scan_long = scan_df[scan_df["signal_dir"] == "long"].reset_index(drop=True)
    if scan_long.empty:
        logger.warning("No LONG-direction features found in scan results.")
        return 0

    miner = AtomMiner(force_direction="long")
    atoms = miner.mine_from_scan(df, scan_long, top_n=TOP_N)
    if not atoms:
        logger.warning("No LONG atoms mined.")
        return 0

    validator = WalkForwardValidator(train_frac=TRAIN_FRAC, fee_pct=FEE_PCT)
    train_df, test_df = validator.split(df)
    reports = validator.validate_all(atoms, train_df, test_df, keep_only_robust=False)

    print("\nLONG candidates (OOS stats)")
    header = (
        f"{'rule':<54} {'OOS_WR%':>8} {'OOS_PF':>7} {'OOS_n':>6} "
        f"{'OOS_ICIR':>8} {'degrad':>7}"
    )
    print(header)
    print("-" * len(header))

    for rep in reports:
        oos = rep.get("OOS", {}) or {}
        rule = rep.get("rule", "")
        rule_disp = rule if len(rule) <= 54 else (rule[:51] + "...")
        print(
            f"{rule_disp:<54}"
            f"{_fmt(oos.get('win_rate'), 8, '6.2f')}"
            f"{_fmt(oos.get('profit_factor'), 7, '6.3f')}"
            f"{_fmt(oos.get('n_triggers'), 6)}"
            f"{_fmt(oos.get('ICIR'), 8, '7.3f')}"
            f"{_fmt(rep.get('degradation'), 7, '6.3f')}"
        )

    atom_map = {a.rule_str(): a for a in atoms}
    robust = []
    for rep in reports:
        if not _passes_thresholds(rep):
            continue
        atom = atom_map.get(rep.get("rule", ""))
        if atom is None:
            continue
        entry = atom.to_dict()
        entry["IS"] = rep.get("IS")
        entry["OOS"] = rep.get("OOS")
        entry["degradation"] = rep.get("degradation")
        robust.append(entry)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_range": {"start": start_date, "end": end_date},
        "horizons": HORIZONS,
        "train_frac": TRAIN_FRAC,
        "fee_pct": FEE_PCT,
        "side_endpoints": SIDE_ENDPOINTS,
        "feature_dims": FEATURE_DIMS,
        "criteria": {
            "oos_win_rate_min": 60,
            "oos_n_triggers_min": 20,
            "oos_profit_factor_min": 1.0,
            "degradation_min": 0.4,
        },
        "total_candidates": len(reports),
        "robust_candidates": len(robust),
        "candidates": robust,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, default=str)

    logger.info("Saved %d robust LONG candidates to %s", len(robust), OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
