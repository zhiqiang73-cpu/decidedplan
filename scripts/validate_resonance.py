"""Validate the "Force Resonance" hypothesis on live trades and historical replay."""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
import traceback
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

import numpy as np
import pandas as pd
from scipy import stats

from core.feature_engine import FeatureEngine
from monitor.signal_runner import SignalRunner

ROUND_TRIP_FEE_PCT = 0.10
HORIZONS = (15, 30, 60)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate whether clustered multi-signal events outperform isolated signals."
    )
    parser.add_argument(
        "--trade-log",
        default="execution/logs/trades.csv",
        help="Path to the trade log CSV.",
    )
    parser.add_argument(
        "--storage-path",
        default="data/storage",
        help="FeatureEngine storage path.",
    )
    parser.add_argument(
        "--start-date",
        default="2026-01-01",
        help="Replay start date (inclusive, UTC date string).",
    )
    parser.add_argument(
        "--end-date",
        default="2026-04-04",
        help="Replay end date (inclusive, UTC date string).",
    )
    parser.add_argument(
        "--warmup-bars",
        type=int,
        default=300,
        help="Warmup bars required before replay starts.",
    )
    parser.add_argument(
        "--cluster-window",
        type=int,
        default=5,
        help="Cluster window in bars/minutes.",
    )
    parser.add_argument(
        "--fee-pct",
        type=float,
        default=ROUND_TRIP_FEE_PCT,
        help="Round-trip fee deducted from replay forward returns, in percent.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=5000,
        help="Print replay progress every N processed bars.",
    )
    parser.add_argument(
        "--verbose-logging",
        action="store_true",
        help="Keep project logging enabled during replay.",
    )
    return parser.parse_args()


def configure_logging(verbose: bool) -> None:
    if verbose:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        )
        return
    logging.basicConfig(level=logging.ERROR)
    logging.disable(logging.CRITICAL)


def format_pct(value: float | None, decimals: int = 3) -> str:
    if value is None:
        return "NA"
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return "NA"
    return f"{value:.{decimals}f}%"


def format_pvalue(value: float | None) -> str:
    if value is None:
        return "NA"
    if math.isnan(value):
        return "NA"
    if value < 1e-4:
        return "<0.0001"
    return f"{value:.4f}"


def format_seconds(value: float) -> str:
    total = max(0, int(value))
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"


def parse_trade_time(series: pd.Series) -> pd.Series:
    cleaned = series.astype(str).str.replace(r"\s+CST$", "", regex=True).str.strip()
    return pd.to_datetime(cleaned, format="%Y-%m-%d %H:%M:%S", errors="coerce")


def safe_mean(series: pd.Series) -> float | None:
    clean = pd.to_numeric(series, errors="coerce").dropna()
    if clean.empty:
        return None
    return float(clean.mean())


def welch_pvalue(left: pd.Series, right: pd.Series, min_n: int = 5) -> float | None:
    left_clean = pd.to_numeric(left, errors="coerce").dropna()
    right_clean = pd.to_numeric(right, errors="coerce").dropna()
    if len(left_clean) < min_n or len(right_clean) < min_n:
        return None
    result = stats.ttest_ind(left_clean, right_clean, equal_var=False, nan_policy="omit")
    return float(result.pvalue) if result.pvalue is not None else None


def fisher_pvalue(left_wins: int, left_losses: int, right_wins: int, right_losses: int) -> float | None:
    if min(left_wins + left_losses, right_wins + right_losses) == 0:
        return None
    _, pvalue = stats.fisher_exact(
        [[left_wins, left_losses], [right_wins, right_losses]],
        alternative="two-sided",
    )
    return float(pvalue)


def summarize_trade_group(frame: pd.DataFrame) -> dict[str, Any]:
    returns = pd.to_numeric(frame["net_return_pct"], errors="coerce").dropna()
    wins = int((returns > 0).sum())
    total = int(len(returns))
    win_rate = (wins / total * 100.0) if total else None
    return {
        "count": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": win_rate,
        "avg_net": float(returns.mean()) if total else None,
    }


def analyze_trade_clustering(trade_log_path: Path, window_minutes: int = 5) -> dict[str, Any]:
    trades = pd.read_csv(trade_log_path)
    filled = trades[trades["exit_reason"].fillna("").str.lower() != "not_filled"].copy()
    filled["entry_dt"] = parse_trade_time(filled["entry_time"])
    filled["net_return_pct"] = pd.to_numeric(filled["net_return_pct"], errors="coerce")
    filled = filled.dropna(subset=["entry_dt", "net_return_pct"]).reset_index(drop=True)

    if filled.empty:
        raise ValueError("No filled trades with valid entry_time/net_return_pct found.")

    ordered = filled[["entry_dt"]].sort_values("entry_dt").reset_index()
    times_ns = ordered["entry_dt"].astype("int64").to_numpy()
    window_ns = int(window_minutes * 60 * 1_000_000_000)
    neighbor_counts = np.zeros(len(ordered), dtype=int)

    for idx, ts_ns in enumerate(times_ns):
        left = int(np.searchsorted(times_ns, ts_ns - window_ns, side="left"))
        right = int(np.searchsorted(times_ns, ts_ns + window_ns, side="right"))
        neighbor_counts[idx] = max(0, right - left - 1)

    neighbor_map = dict(zip(ordered["index"].to_numpy(), neighbor_counts.tolist()))
    filled["neighbor_count"] = filled.index.map(neighbor_map).fillna(0).astype(int)
    filled["cluster_type"] = np.where(filled["neighbor_count"] > 0, "clustered", "isolated")

    isolated = filled[filled["cluster_type"] == "isolated"].copy()
    clustered = filled[filled["cluster_type"] == "clustered"].copy()

    isolated_stats = summarize_trade_group(isolated)
    clustered_stats = summarize_trade_group(clustered)

    return_p = welch_pvalue(isolated["net_return_pct"], clustered["net_return_pct"])
    win_p = fisher_pvalue(
        isolated_stats["wins"],
        isolated_stats["losses"],
        clustered_stats["wins"],
        clustered_stats["losses"],
    )

    return {
        "filled_count": int(len(filled)),
        "skipped_not_filled": int(len(trades) - len(filled)),
        "isolated": isolated_stats,
        "clustered": clustered_stats,
        "return_pvalue": return_p,
        "winrate_pvalue": win_p,
    }


def compute_forward_return(
    close_values: np.ndarray,
    entry_idx: int,
    horizon: int,
    direction: str,
    fee_pct: float,
) -> float | None:
    exit_idx = entry_idx + horizon
    if exit_idx >= len(close_values):
        return None

    entry_price = float(close_values[entry_idx])
    exit_price = float(close_values[exit_idx])
    if entry_price <= 0:
        return None

    if direction == "long":
        gross = (exit_price - entry_price) / entry_price * 100.0
    elif direction == "short":
        gross = (entry_price - exit_price) / entry_price * 100.0
    else:
        return None
    return gross - fee_pct


def summarize_event_group(frame: pd.DataFrame) -> dict[str, Any]:
    summary: dict[str, Any] = {"count": int(len(frame))}
    for horizon in HORIZONS:
        summary[f"fwd_{horizon}"] = safe_mean(frame[f"fwd_{horizon}"])
    return summary


def compute_bucket_tests(events: pd.DataFrame) -> dict[str, dict[int, float | None]]:
    tests: dict[str, dict[int, float | None]] = {
        "multi_vs_single": {},
        "triple_vs_single": {},
    }
    single = events[events["bucket"] == "single"]
    multi = events[events["cluster_size"] >= 2]
    triple = events[events["cluster_size"] >= 3]

    for horizon in HORIZONS:
        column = f"fwd_{horizon}"
        tests["multi_vs_single"][horizon] = welch_pvalue(single[column], multi[column])
        tests["triple_vs_single"][horizon] = welch_pvalue(single[column], triple[column])
    return tests


def replay_signal_resonance(
    df: pd.DataFrame,
    warmup_bars: int,
    cluster_window: int,
    fee_pct: float,
    progress_every: int,
) -> dict[str, Any]:
    if df.empty:
        raise ValueError("Historical feature frame is empty.")
    if "close" not in df.columns or "timestamp" not in df.columns:
        raise ValueError("Historical feature frame must contain 'close' and 'timestamp'.")
    if len(df) < warmup_bars:
        raise ValueError(
            f"Need at least {warmup_bars} rows for warmup, got {len(df)}."
        )

    runner = SignalRunner()
    close_values = pd.to_numeric(df["close"], errors="coerce").to_numpy()
    start_idx = warmup_bars - 1
    total_bars = len(df) - start_idx
    alerts_by_bar: dict[str, dict[int, set[str]]] = {
        "long": defaultdict(set),
        "short": defaultdict(set),
    }
    events: list[dict[str, Any]] = []
    errors: list[str] = []
    replay_start = time.time()

    print(
        f"[Replay] Starting bar-by-bar replay over {total_bars:,} bars "
        f"({df['timestamp'].iloc[start_idx]} -> {df['timestamp'].iloc[-1]})"
    )

    for processed, idx in enumerate(range(start_idx, len(df)), start=1):
        window = df.iloc[max(0, idx - warmup_bars + 1) : idx + 1]
        try:
            raw_alerts, _ = runner.run(window)
        except Exception as exc:  # pragma: no cover - defensive runtime guard
            errors.append(f"bar={idx} ts={int(df['timestamp'].iloc[idx])} err={exc}")
            if len(errors) <= 3:
                print(f"[Replay] Error on bar {idx}: {exc}")
            continue

        current_bar_alerts = {"long": set(), "short": set()}
        for alert in raw_alerts:
            direction = str(alert.get("direction", "")).strip().lower()
            name = str(alert.get("name", "")).strip()
            if direction not in current_bar_alerts or not name:
                continue
            current_bar_alerts[direction].add(name)

        for direction, names in current_bar_alerts.items():
            if not names:
                continue

            alerts_by_bar[direction][idx] = set(names)
            recent_names: set[str] = set()
            recent_start = max(start_idx, idx - cluster_window + 1)
            for recent_idx in range(recent_start, idx + 1):
                recent_names.update(alerts_by_bar[direction].get(recent_idx, set()))

            cluster_size = len(recent_names)
            if cluster_size <= 0:
                continue

            bucket = "single"
            if cluster_size == 2:
                bucket = "double"
            elif cluster_size >= 3:
                bucket = "triple_plus"

            event = {
                "bar_index": idx,
                "timestamp": int(df["timestamp"].iloc[idx]),
                "direction": direction,
                "bucket": bucket,
                "cluster_size": cluster_size,
                "current_names": " | ".join(sorted(names)),
                "window_names": " | ".join(sorted(recent_names)),
            }
            for horizon in HORIZONS:
                event[f"fwd_{horizon}"] = compute_forward_return(
                    close_values=close_values,
                    entry_idx=idx,
                    horizon=horizon,
                    direction=direction,
                    fee_pct=fee_pct,
                )
            events.append(event)

        if processed == 1 or processed % max(1, progress_every) == 0 or idx == len(df) - 1:
            elapsed = time.time() - replay_start
            speed = processed / elapsed if elapsed > 0 else 0.0
            remaining = total_bars - processed
            eta = remaining / speed if speed > 0 else 0.0
            print(
                f"[Replay] {processed:,}/{total_bars:,} bars "
                f"({processed / total_bars:.1%}) | events={len(events):,} "
                f"| errors={len(errors)} | elapsed={format_seconds(elapsed)} "
                f"| eta={format_seconds(eta)}"
            )

    event_df = pd.DataFrame(events)
    if event_df.empty:
        raise ValueError("Replay produced zero raw alert events.")

    single = event_df[event_df["bucket"] == "single"].copy()
    double = event_df[event_df["bucket"] == "double"].copy()
    triple = event_df[event_df["bucket"] == "triple_plus"].copy()
    multi = event_df[event_df["cluster_size"] >= 2].copy()

    return {
        "event_count": int(len(event_df)),
        "single": summarize_event_group(single),
        "double": summarize_event_group(double),
        "triple_plus": summarize_event_group(triple),
        "multi": summarize_event_group(multi),
        "tests": compute_bucket_tests(event_df),
        "errors": errors,
    }


def print_part_a(result: dict[str, Any]) -> None:
    isolated = result["isolated"]
    clustered = result["clustered"]
    print("=== Part A: Existing Trade Clustering Analysis ===")
    print(
        f"Filled trades analyzed: N={result['filled_count']} "
        f"(excluded not_filled={result['skipped_not_filled']})"
    )
    print(
        f"Isolated trades: N={isolated['count']}, "
        f"WR={format_pct(isolated['win_rate'], 2)}, "
        f"avg_net={format_pct(isolated['avg_net'], 3)}"
    )
    print(
        f"Clustered trades: N={clustered['count']}, "
        f"WR={format_pct(clustered['win_rate'], 2)}, "
        f"avg_net={format_pct(clustered['avg_net'], 3)}"
    )
    print(
        "Stat tests: "
        f"Welch p(return)={format_pvalue(result['return_pvalue'])}, "
        f"Fisher p(win_rate)={format_pvalue(result['winrate_pvalue'])}"
    )
    print()


def print_part_b(result: dict[str, Any], fee_pct: float) -> None:
    single = result["single"]
    double = result["double"]
    triple = result["triple_plus"]
    multi = result["multi"]
    tests = result["tests"]

    print("=== Part B: Historical Signal Replay (90 days) ===")
    print(f"Replay events analyzed: N={result['event_count']} | fee_deduction={format_pct(fee_pct, 2)}")
    print(
        f"Single signal events:  N={single['count']}, "
        f"fwd_15bar={format_pct(single['fwd_15'], 3)}, "
        f"fwd_30bar={format_pct(single['fwd_30'], 3)}, "
        f"fwd_60bar={format_pct(single['fwd_60'], 3)}"
    )
    print(
        f"2-signal cluster:      N={double['count']}, "
        f"fwd_15bar={format_pct(double['fwd_15'], 3)}, "
        f"fwd_30bar={format_pct(double['fwd_30'], 3)}, "
        f"fwd_60bar={format_pct(double['fwd_60'], 3)}"
    )
    print(
        f"3+ signal cluster:     N={triple['count']}, "
        f"fwd_15bar={format_pct(triple['fwd_15'], 3)}, "
        f"fwd_30bar={format_pct(triple['fwd_30'], 3)}, "
        f"fwd_60bar={format_pct(triple['fwd_60'], 3)}"
    )
    print(
        f"2+ combined cluster:   N={multi['count']}, "
        f"fwd_15bar={format_pct(multi['fwd_15'], 3)}, "
        f"fwd_30bar={format_pct(multi['fwd_30'], 3)}, "
        f"fwd_60bar={format_pct(multi['fwd_60'], 3)}"
    )
    print(
        "Welch p(2+ vs single): "
        f"15bar={format_pvalue(tests['multi_vs_single'][15])}, "
        f"30bar={format_pvalue(tests['multi_vs_single'][30])}, "
        f"60bar={format_pvalue(tests['multi_vs_single'][60])}"
    )
    print(
        "Welch p(3+ vs single): "
        f"15bar={format_pvalue(tests['triple_vs_single'][15])}, "
        f"30bar={format_pvalue(tests['triple_vs_single'][30])}, "
        f"60bar={format_pvalue(tests['triple_vs_single'][60])}"
    )
    if result["errors"]:
        print(f"Replay runtime warnings: {len(result['errors'])} bars failed inside runner.")
        for sample in result["errors"][:3]:
            print(f"  sample_error: {sample}")
    print()


def build_conclusion(part_a: dict[str, Any], part_b: dict[str, Any]) -> str:
    iso = part_a["isolated"]
    clu = part_a["clustered"]
    multi = part_b["multi"]
    single = part_b["single"]
    tests = part_b["tests"]["multi_vs_single"]

    a_supported = False
    if iso["avg_net"] is not None and clu["avg_net"] is not None:
        a_supported = clu["avg_net"] > iso["avg_net"]

    b_supported_horizons = []
    for horizon in HORIZONS:
        single_ret = single.get(f"fwd_{horizon}")
        multi_ret = multi.get(f"fwd_{horizon}")
        if single_ret is not None and multi_ret is not None and multi_ret > single_ret:
            b_supported_horizons.append(horizon)

    if a_supported and len(b_supported_horizons) >= 2:
        verdict = "Supported"
    elif len(b_supported_horizons) >= 1 or a_supported:
        verdict = "Mixed / partially supported"
    else:
        verdict = "Not supported"

    return (
        f"{verdict}: Part A isolated avg_net={format_pct(iso['avg_net'], 3)} "
        f"vs clustered avg_net={format_pct(clu['avg_net'], 3)}; "
        f"Part B single 2+ combined forward returns were "
        f"{format_pct(single['fwd_15'], 3)} vs {format_pct(multi['fwd_15'], 3)} at 15 bars, "
        f"{format_pct(single['fwd_30'], 3)} vs {format_pct(multi['fwd_30'], 3)} at 30 bars, "
        f"and {format_pct(single['fwd_60'], 3)} vs {format_pct(multi['fwd_60'], 3)} at 60 bars. "
        f"Welch p-values for 2+ vs single: "
        f"15={format_pvalue(tests[15])}, 30={format_pvalue(tests[30])}, 60={format_pvalue(tests[60])}."
    )


def main() -> int:
    args = parse_args()
    configure_logging(args.verbose_logging)

    trade_log_path = Path(args.trade_log)
    if not trade_log_path.exists():
        print(f"[Error] Trade log not found: {trade_log_path}")
        return 1

    print("[Part A] Reading trade log and classifying clustered vs isolated entries...")
    part_a = analyze_trade_clustering(trade_log_path=trade_log_path, window_minutes=5)
    print_part_a(part_a)

    print("[Part B] Loading historical features for replay...")
    fe = FeatureEngine(storage_path=args.storage_path)
    replay_df = fe.load_date_range(args.start_date, args.end_date)
    replay_df = (
        replay_df.sort_values("timestamp")
        .drop_duplicates(subset=["timestamp"], keep="last")
        .reset_index(drop=True)
    )
    print(
        f"[Part B] Loaded {len(replay_df):,} bars with {len(replay_df.columns)} columns "
        f"from {args.start_date} to {args.end_date}."
    )

    part_b = replay_signal_resonance(
        df=replay_df,
        warmup_bars=args.warmup_bars,
        cluster_window=args.cluster_window,
        fee_pct=args.fee_pct,
        progress_every=args.progress_every,
    )
    print_part_b(part_b, fee_pct=args.fee_pct)

    print("=== Conclusion ===")
    print(build_conclusion(part_a, part_b))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\n[Abort] Interrupted by user.")
        raise SystemExit(130)
    except Exception as exc:  # pragma: no cover - defensive runtime guard
        print(f"\n[Fatal] {exc}")
        print(traceback.format_exc())
        raise SystemExit(1)
