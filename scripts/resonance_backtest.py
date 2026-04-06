"""Force Resonance hypothesis backtest for BTC alerts."""

from __future__ import annotations

import sys

sys.path.insert(0, r"D:\MyAI\My work team\Decided plan")

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

import json
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from core.feature_engine import FeatureEngine
from monitor.signal_runner import SignalRunner

ROOT = Path(r"D:\MyAI\My work team\Decided plan")
STORAGE_PATH = "data/storage"

PRIMARY_START = "2026-01-05"
FALLBACK_START = "2026-02-04"
END_DATE = "2026-04-04"

WARMUP_BARS = 300
CLUSTER_WINDOW_BARS = 5
FORWARD_WINDOW_BARS = 60
FORWARD_RETURN_BARS = 30
ROUND_TRIP_FEE_PCT = 0.04
PROGRESS_EVERY = 5000

OUTPUT_PATH = ROOT / "alpha" / "output" / "resonance_backtest.json"
CATEGORY_ORDER = ("isolated", "clustered", "resonance")


def _normalize_direction(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"long", "buy"}:
        return "long"
    if value in {"short", "sell"}:
        return "short"
    return ""


def _normalize_family(alert: dict[str, Any]) -> str:
    family = str(alert.get("family") or "").strip()
    if family:
        return family
    group = str(alert.get("group") or "").strip()
    if group:
        return group
    name = str(alert.get("name") or "").strip()
    if name:
        return name.split(" | ")[0]
    return "UNKNOWN"


def _safe_float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number):
        return None
    return number


def load_features_with_fallback() -> tuple[pd.DataFrame, str]:
    fe = FeatureEngine(storage_path=STORAGE_PATH)

    try:
        df_90 = fe.load_date_range(PRIMARY_START, END_DATE)
        if df_90 is not None and not df_90.empty:
            return df_90, PRIMARY_START
    except Exception as exc:
        print(f"[warn] 90-day load failed: {exc}")
        print(traceback.format_exc(limit=1).strip())

    print("[info] Falling back to 60-day range: 2026-02-04 -> 2026-04-04")
    df_60 = fe.load_date_range(FALLBACK_START, END_DATE)
    if df_60 is None or df_60.empty:
        raise RuntimeError("FeatureEngine returned empty data for both 90-day and 60-day ranges.")
    return df_60, FALLBACK_START


def replay_alerts(df: pd.DataFrame) -> pd.DataFrame:
    runner = SignalRunner(alpha_cooldown=1, p2_group_cooldown_min=0, p2_max_groups_per_bar=99)
    records: list[dict[str, Any]] = []
    total = len(df) - WARMUP_BARS

    print(f"[info] Replay bars: {total:,} (warmup={WARMUP_BARS})")
    for processed, i in enumerate(range(WARMUP_BARS, len(df)), start=1):
        if processed == 1 or processed % PROGRESS_EVERY == 0 or i == len(df) - 1:
            print(f"[progress] {processed:,}/{total:,} bars processed, alerts={len(records):,}")

        try:
            raw_alerts, composite_alerts = runner.run(df.iloc[: i + 1])
        except Exception as exc:
            print(f"[warn] runner failed at bar {i}: {exc}")
            continue

        all_alerts = list(raw_alerts) + list(composite_alerts)
        if not all_alerts:
            continue

        entry_price = _safe_float(df.iloc[i]["close"])
        if entry_price is None or entry_price <= 0:
            continue

        for alert in all_alerts:
            if not isinstance(alert, dict):
                continue
            direction = _normalize_direction(alert.get("direction"))
            if not direction:
                continue

            records.append(
                {
                    "bar_index": int(i),
                    "timestamp_ms": int(df.iloc[i]["timestamp"]) if "timestamp" in df.columns else None,
                    "direction": direction,
                    "signal_name": str(alert.get("name") or "UNKNOWN"),
                    "signal_family": _normalize_family(alert),
                    "entry_price": float(entry_price),
                }
            )

    return pd.DataFrame(records)


def attach_forward_metrics(alerts: pd.DataFrame, df: pd.DataFrame) -> pd.DataFrame:
    if alerts.empty:
        return alerts

    close_arr = pd.to_numeric(df["close"], errors="coerce").to_numpy(dtype=float)
    high_arr = pd.to_numeric(df["high"], errors="coerce").to_numpy(dtype=float)
    low_arr = pd.to_numeric(df["low"], errors="coerce").to_numpy(dtype=float)

    mfe_values: list[float | None] = []
    mae_values: list[float | None] = []
    ret_values: list[float | None] = []
    win_values: list[bool | None] = []

    n = len(df)
    for row in alerts.itertuples(index=False):
        i = int(row.bar_index)
        entry = float(row.entry_price)
        direction = str(row.direction)

        if i + FORWARD_WINDOW_BARS >= n or i + FORWARD_RETURN_BARS >= n:
            mfe_values.append(None)
            mae_values.append(None)
            ret_values.append(None)
            win_values.append(None)
            continue

        future_high = high_arr[i + 1 : i + FORWARD_WINDOW_BARS + 1]
        future_low = low_arr[i + 1 : i + FORWARD_WINDOW_BARS + 1]
        future_close = close_arr[i + FORWARD_RETURN_BARS]

        if future_high.size == 0 or future_low.size == 0 or not np.isfinite(future_close):
            mfe_values.append(None)
            mae_values.append(None)
            ret_values.append(None)
            win_values.append(None)
            continue

        if direction == "long":
            mfe = np.nanmax((future_high - entry) / entry * 100.0)
            mae = np.nanmax((entry - future_low) / entry * 100.0)
            net_ret = (future_close - entry) / entry * 100.0 - ROUND_TRIP_FEE_PCT
        else:
            mfe = np.nanmax((entry - future_low) / entry * 100.0)
            mae = np.nanmax((future_high - entry) / entry * 100.0)
            net_ret = (entry - future_close) / entry * 100.0 - ROUND_TRIP_FEE_PCT

        mfe = float(mfe) if np.isfinite(mfe) else None
        mae = float(mae) if np.isfinite(mae) else None
        net_ret = float(net_ret) if np.isfinite(net_ret) else None

        mfe_values.append(mfe)
        mae_values.append(mae)
        ret_values.append(net_ret)
        win_values.append(None if net_ret is None else bool(net_ret > 0))

    out = alerts.copy()
    out["mfe_pct_60"] = mfe_values
    out["mae_pct_60"] = mae_values
    out["net_return_pct_30"] = ret_values
    out["win"] = win_values
    return out


def classify_alerts(alerts: pd.DataFrame) -> pd.DataFrame:
    if alerts.empty:
        return alerts

    df_sorted = alerts.sort_values(["direction", "bar_index"]).reset_index(drop=True)
    cluster_size = np.zeros(len(df_sorted), dtype=int)
    unique_family_count = np.zeros(len(df_sorted), dtype=int)
    category = np.empty(len(df_sorted), dtype=object)

    directions = df_sorted["direction"].to_numpy()
    bars_all = df_sorted["bar_index"].to_numpy(dtype=int)
    families_all = df_sorted["signal_family"].astype(str).to_numpy()

    for direction in ("long", "short"):
        idx = np.where(directions == direction)[0]
        if idx.size == 0:
            continue

        bars = bars_all[idx]
        families = families_all[idx]

        for pos, original in enumerate(idx):
            left = int(np.searchsorted(bars, bars[pos] - CLUSTER_WINDOW_BARS, side="left"))
            right = int(np.searchsorted(bars, bars[pos] + CLUSTER_WINDOW_BARS, side="right"))

            count_window = int(right - left)
            unique_families = len(set(families[left:right]))

            cluster_size[original] = count_window
            unique_family_count[original] = unique_families

            if count_window >= 3 and unique_families >= 3:
                category[original] = "resonance"
            elif count_window >= 2:
                category[original] = "clustered"
            else:
                category[original] = "isolated"

    df_sorted["cluster_size_same_dir"] = cluster_size
    df_sorted["unique_families_same_dir"] = unique_family_count
    df_sorted["category"] = category
    return df_sorted


def summarize(alerts: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    eval_df = alerts.dropna(subset=["mfe_pct_60", "mae_pct_60", "net_return_pct_30", "category"]).copy()

    for cat in CATEGORY_ORDER:
        part = eval_df[eval_df["category"] == cat]
        count = int(len(part))
        avg_mfe = float(part["mfe_pct_60"].mean()) if count else np.nan
        avg_mae = float(part["mae_pct_60"].mean()) if count else np.nan
        ratio = (avg_mfe / avg_mae) if count and np.isfinite(avg_mae) and avg_mae > 0 else np.nan
        win_rate = float(part["win"].astype(float).mean() * 100.0) if count else np.nan
        avg_net = float(part["net_return_pct_30"].mean()) if count else np.nan

        rows.append(
            {
                "Category": cat.capitalize(),
                "Count": count,
                "Avg MFE": avg_mfe,
                "Avg MAE": avg_mae,
                "MFE/MAE": ratio,
                "Win Rate": win_rate,
                "Avg Net Return": avg_net,
            }
        )

    return pd.DataFrame(rows), eval_df


def print_table(summary_df: pd.DataFrame) -> None:
    if summary_df.empty:
        print("No summary rows.")
        return

    printable = summary_df.copy()
    for col in ("Avg MFE", "Avg MAE", "MFE/MAE", "Win Rate", "Avg Net Return"):
        printable[col] = printable[col].map(
            lambda x: "NA" if pd.isna(x) else f"{float(x):.4f}"
        )
    print(printable.to_string(index=False))


def save_json(
    alerts_all: pd.DataFrame,
    alerts_eval: pd.DataFrame,
    summary_df: pd.DataFrame,
    used_start: str,
) -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "date_range": {"start": used_start, "end": END_DATE},
        "settings": {
            "warmup_bars": WARMUP_BARS,
            "cluster_window_bars": CLUSTER_WINDOW_BARS,
            "forward_window_bars": FORWARD_WINDOW_BARS,
            "forward_return_bars": FORWARD_RETURN_BARS,
            "round_trip_fee_pct": ROUND_TRIP_FEE_PCT,
        },
        "counts": {
            "alerts_total": int(len(alerts_all)),
            "alerts_with_forward_window": int(len(alerts_eval)),
        },
        "summary": [
            {
                "category": row["Category"],
                "count": int(row["Count"]),
                "avg_mfe_pct": None if pd.isna(row["Avg MFE"]) else float(row["Avg MFE"]),
                "avg_mae_pct": None if pd.isna(row["Avg MAE"]) else float(row["Avg MAE"]),
                "mfe_mae_ratio": None if pd.isna(row["MFE/MAE"]) else float(row["MFE/MAE"]),
                "win_rate_pct": None if pd.isna(row["Win Rate"]) else float(row["Win Rate"]),
                "avg_net_return_pct": None
                if pd.isna(row["Avg Net Return"])
                else float(row["Avg Net Return"]),
            }
            for _, row in summary_df.iterrows()
        ],
    }

    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    print(f"[info] Results saved: {OUTPUT_PATH}")


def main() -> int:
    df, used_start = load_features_with_fallback()

    if df.empty:
        print("[error] Feature dataframe is empty.")
        return 1

    required_cols = {"timestamp", "close", "high", "low"}
    missing = sorted(col for col in required_cols if col not in df.columns)
    if missing:
        print(f"[error] Missing required columns: {missing}")
        return 1

    if len(df) <= WARMUP_BARS + FORWARD_WINDOW_BARS:
        print(f"[error] Not enough bars: {len(df)}")
        return 1

    print(
        f"[info] Loaded bars={len(df):,}, range={used_start} -> {END_DATE}, "
        f"first_ts={int(df['timestamp'].iloc[0])}, last_ts={int(df['timestamp'].iloc[-1])}"
    )

    alerts = replay_alerts(df)
    alerts = attach_forward_metrics(alerts, df)
    alerts = classify_alerts(alerts)
    summary_df, eval_df = summarize(alerts)

    print("\nCategory    | Count | Avg MFE | Avg MAE | MFE/MAE | Win Rate | Avg Net Return")
    print_table(summary_df)

    save_json(alerts, eval_df, summary_df, used_start=used_start)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
