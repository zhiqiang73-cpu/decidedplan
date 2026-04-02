"""Helpers for multi-timeframe scan utilities."""

from __future__ import annotations

import argparse

import pandas as pd

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()

_BASE_COLUMNS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trades",
]
_OPTIONAL_LAST_COLUMNS = [
    "funding_rate",
    "open_interest",
    "long_short_ratio",
    "long_account",
    "short_account",
]
_OPTIONAL_SUM_COLUMNS = [
    "taker_buy_base",
    "taker_buy_quote",
]


def parse_tf_value(value: int | str) -> int:
    """Parse timeframe values like 5, 5m, 15min, 1h into minutes."""
    if isinstance(value, int):
        minutes = value
    else:
        text = str(value).strip().lower()
        if not text:
            raise ValueError("timeframe is empty")
        if text.endswith("min"):
            minutes = int(text[:-3])
        elif text.endswith("m"):
            minutes = int(text[:-1])
        elif text.endswith("h"):
            minutes = int(text[:-1]) * 60
        else:
            minutes = int(text)
    if minutes <= 0:
        raise ValueError(f"invalid timeframe: {value}")
    return minutes


def normalize_tfs(values: list[int | str] | None) -> list[int]:
    """Normalize timeframe inputs, deduplicate them, and preserve order."""
    source = values or [5, 15, 60]
    seen: set[int] = set()
    result: list[int] = []
    for item in source:
        tf = parse_tf_value(item)
        if tf not in seen:
            seen.add(tf)
            result.append(tf)
    return result


def format_tf_label(tf_min: int) -> str:
    """Format minutes as 5m / 1h style labels."""
    if tf_min % 60 == 0:
        return f"{tf_min // 60}h"
    return f"{tf_min}m"


def resample_to_tf(df: pd.DataFrame, tf_min: int) -> pd.DataFrame:
    """Resample 1m bars into a higher timeframe and drop incomplete tail buckets."""
    if tf_min <= 1:
        return df.copy()
    if df.empty:
        return df.copy()
    if "timestamp" not in df.columns:
        raise KeyError("timestamp column is required")

    work = df.copy()
    work["dt"] = pd.to_datetime(work["timestamp"], unit="ms", utc=True)
    work = work.sort_values("dt").reset_index(drop=True)
    start = work["dt"].iloc[0]
    bucket = pd.Timedelta(minutes=tf_min)
    work["bucket_start"] = start + ((work["dt"] - start) // bucket) * bucket

    grouped = work.groupby("bucket_start", sort=True)
    rows: list[dict] = []
    for bucket_start, bucket_df in grouped:
        if len(bucket_df) < tf_min:
            continue

        row = {
            "timestamp": int(bucket_start.timestamp() * 1000),
            "open": float(bucket_df["open"].iloc[0]),
            "high": float(bucket_df["high"].max()),
            "low": float(bucket_df["low"].min()),
            "close": float(bucket_df["close"].iloc[-1]),
            "volume": float(bucket_df["volume"].sum()),
            "quote_volume": float(bucket_df["quote_volume"].sum()),
            "trades": int(bucket_df["trades"].sum()),
        }
        for column in _OPTIONAL_SUM_COLUMNS:
            if column in bucket_df.columns:
                row[column] = float(bucket_df[column].sum())
        for column in _OPTIONAL_LAST_COLUMNS:
            if column in bucket_df.columns:
                row[column] = bucket_df[column].iloc[-1]
        rows.append(row)

    resampled = pd.DataFrame(rows)
    if resampled.empty:
        return resampled

    ordered_columns = ["timestamp", *_BASE_COLUMNS]
    ordered_columns.extend([col for col in _OPTIONAL_SUM_COLUMNS if col in resampled.columns])
    ordered_columns.extend([col for col in _OPTIONAL_LAST_COLUMNS if col in resampled.columns])
    return resampled[ordered_columns]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview available multi-timeframe labels")
    parser.add_argument("timeframes", nargs="*", help="Examples: 5m 15m 1h")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    tfs = normalize_tfs(args.timeframes or None)
    print(", ".join(format_tf_label(tf) for tf in tfs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
