from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

import logging
import re

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_RAW_TICK_COLUMNS = ("timestamp", "price", "quantity", "is_buyer_maker")


@dataclass(frozen=True)
class GrammarFeatureSpec:
    feature: str
    role: str
    direction: str
    operator: str
    quantiles: tuple[int, ...]
    description: str


@dataclass(frozen=True)
class RhythmCondition:
    feature: str
    operator: str
    threshold: float
    role: str
    description: str


@dataclass(frozen=True)
class RhythmCandidate:
    window_seconds: int
    horizon_bars: int
    direction: str
    pattern: tuple[str, ...]
    conditions: tuple[RhythmCondition, ...]
    stats: dict[str, float]
    score: float
    mechanism_story: str
    exit_plan: dict | None = None

    def to_dict(self) -> dict:
        return {
            "window_seconds": self.window_seconds,
            "horizon_bars": self.horizon_bars,
            "direction": self.direction,
            "pattern": list(self.pattern),
            "conditions": [asdict(cond) for cond in self.conditions],
            "stats": dict(self.stats),
            "score": round(float(self.score), 4),
            "mechanism_story": self.mechanism_story,
            "exit_plan": self.exit_plan,
        }


_GRAMMAR_SPECS: tuple[GrammarFeatureSpec, ...] = (
    GrammarFeatureSpec("trend_5m_pct", "context", "long", ">", (60, 70, 80), "五分钟趋势向上"),
    GrammarFeatureSpec("trend_15m_pct", "context", "long", ">", (60, 70, 80), "十五分钟趋势向上"),
    GrammarFeatureSpec("vwap_dev_5m_pct", "pullback", "long", "<", (20, 30, 40), "价格回踩到五分钟成交均价附近或下方"),
    GrammarFeatureSpec("pullback_from_high_pct", "pullback", "long", "<", (20, 30, 40), "价格从局部高点回落"),
    GrammarFeatureSpec("sell_share", "absorption", "long", ">", (60, 70, 80), "卖压活跃"),
    GrammarFeatureSpec("absorption_long_score", "absorption", "long", ">", (60, 70, 80), "卖压被承接"),
    GrammarFeatureSpec("burst_index_z", "absorption", "long", ">", (60, 70, 80), "成交节奏聚集"),
    GrammarFeatureSpec("buy_share", "relaunch", "long", ">", (60, 70, 80), "买方重新接管"),
    GrammarFeatureSpec("direction_net", "relaunch", "long", ">", (60, 70, 80), "主动成交转为净买入"),
    GrammarFeatureSpec("large_trade_buy_ratio", "relaunch", "long", ">", (60, 70, 80), "大额成交偏向买方"),
    GrammarFeatureSpec("restart_long_score", "relaunch", "long", ">", (60, 70, 80), "再启动力量可见"),
    GrammarFeatureSpec("trend_5m_pct", "context", "short", "<", (20, 30, 40), "五分钟趋势向下"),
    GrammarFeatureSpec("trend_15m_pct", "context", "short", "<", (20, 30, 40), "十五分钟趋势向下"),
    GrammarFeatureSpec("vwap_dev_5m_pct", "pullback", "short", ">", (60, 70, 80), "价格反抽到五分钟成交均价上方"),
    GrammarFeatureSpec("bounce_from_low_pct", "pullback", "short", ">", (60, 70, 80), "价格从局部低点反抽"),
    GrammarFeatureSpec("buy_share", "absorption", "short", ">", (60, 70, 80), "买盘活跃"),
    GrammarFeatureSpec("absorption_short_score", "absorption", "short", ">", (60, 70, 80), "买盘被压住"),
    GrammarFeatureSpec("burst_index_z", "absorption", "short", ">", (60, 70, 80), "成交节奏聚集"),
    GrammarFeatureSpec("sell_share", "relaunch", "short", ">", (60, 70, 80), "卖方重新接管"),
    GrammarFeatureSpec("direction_net", "relaunch", "short", "<", (20, 30, 40), "主动成交转为净卖出"),
    GrammarFeatureSpec("large_trade_buy_ratio", "relaunch", "short", "<", (20, 30, 40), "大额成交偏向卖方"),
    GrammarFeatureSpec("restart_short_score", "relaunch", "short", ">", (60, 70, 80), "再下压力量可见"),
)

_PATTERNS: tuple[tuple[str, ...], ...] = (
    ("context", "pullback", "absorption"),
    ("context", "pullback", "relaunch"),
    ("pullback", "absorption", "relaunch"),
)


def _date_from_path(path: Path) -> date | None:
    match = re.search(r"year=(\d{4}).*month=(\d{2}).*day=(\d{2})", path.as_posix())
    if not match:
        return None
    year, month, day = (int(match.group(1)), int(match.group(2)), int(match.group(3)))
    return date(year, month, day)


def _cooldown_mask(mask: pd.Series, cooldown: int) -> pd.Series:
    if mask.empty:
        return mask
    values = mask.fillna(False).astype(bool).values
    out = np.zeros(len(values), dtype=bool)
    next_allowed = 0
    cooldown = max(int(cooldown), 1)
    for idx, flag in enumerate(values):
        if not flag or idx < next_allowed:
            continue
        out[idx] = True
        next_allowed = idx + cooldown
    return pd.Series(out, index=mask.index)


def _profit_factor(returns: np.ndarray) -> float:
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    if len(losses) == 0:
        return float("inf") if len(wins) > 0 else 0.0
    loss_sum = abs(float(losses.sum()))
    if loss_sum <= 1e-12:
        return float("inf")
    return float(wins.sum() / loss_sum)


def _condition_story(direction: str, pattern: tuple[str, ...]) -> str:
    if direction == "long":
        mapping = {
            "context": "上行背景",
            "pullback": "回踩",
            "absorption": "吸收",
            "relaunch": "再启动",
        }
        return " + ".join(mapping[item] for item in pattern) + " 做多"
    mapping = {
        "context": "下行背景",
        "pullback": "反抽",
        "absorption": "承接耗尽",
        "relaunch": "再下压",
    }
    return " + ".join(mapping[item] for item in pattern) + " 做空"


class RawTickRhythmResearch:
    def __init__(
        self,
        *,
        storage_path: str = "data/storage",
        round_trip_fee_pct: float = 0.04,
        train_frac: float = 0.67,
        min_train_n: int = 25,
        min_test_n: int = 12,
        cooldown_bars: int = 2,
        min_mfe_coverage_pct: float = 75.0,
        min_train_wr: float = 52.0,
        min_train_pf: float = 1.05,
    ) -> None:
        self.storage_path = Path(storage_path)
        self.round_trip_fee_pct = float(round_trip_fee_pct)
        self.train_frac = float(train_frac)
        self.min_train_n = int(min_train_n)
        self.min_test_n = int(min_test_n)
        self.cooldown_bars = int(cooldown_bars)
        self.min_mfe_coverage_pct = float(min_mfe_coverage_pct)
        self.min_train_wr = float(min_train_wr)
        self.min_train_pf = float(min_train_pf)

    def discover(
        self,
        *,
        window_seconds: Iterable[int],
        directions: Iterable[str],
        start_date: date | None = None,
        end_date: date | None = None,
        top_k: int = 5,
        max_files: int | None = None,
    ) -> dict:
        raw_files = self._raw_tick_files(start_date=start_date, end_date=end_date, max_files=max_files)
        if not raw_files:
            return {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "storage_path": str(self.storage_path),
                "raw_file_count": 0,
                "raw_date_range": None,
                "results": [],
            }

        raw_range = {
            "start": str(_date_from_path(raw_files[0])),
            "end": str(_date_from_path(raw_files[-1])),
        }
        logger.info(
            "[TICK-RHYTHM] raw tick files=%d range=%s -> %s",
            len(raw_files), raw_range["start"], raw_range["end"],
        )

        results: list[dict] = []
        for sec in window_seconds:
            bars = self._build_window_bars(raw_files, window_seconds=int(sec))
            if bars.empty:
                logger.info("[TICK-RHYTHM] %ss produced no bars", sec)
                continue
            logger.info(
                "[TICK-RHYTHM] %ss bars=%d range=%s -> %s",
                sec,
                len(bars),
                pd.to_datetime(bars["timestamp"].iloc[0], unit="ms", utc=True).isoformat(),
                pd.to_datetime(bars["timestamp"].iloc[-1], unit="ms", utc=True).isoformat(),
            )
            featured = self._add_features(bars, window_seconds=int(sec))
            for direction in directions:
                candidates = self._search_direction(featured, window_seconds=int(sec), direction=str(direction))
                top = [candidate.to_dict() for candidate in candidates[:top_k]]
                results.append(
                    {
                        "window_seconds": int(sec),
                        "direction": str(direction),
                        "bar_count": int(len(featured)),
                        "top_candidates": top,
                    }
                )

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "storage_path": str(self.storage_path),
            "raw_file_count": len(raw_files),
            "raw_date_range": raw_range,
            "round_trip_fee_pct": self.round_trip_fee_pct,
            "results": results,
        }

    def _raw_tick_files(
        self,
        *,
        start_date: date | None,
        end_date: date | None,
        max_files: int | None,
    ) -> list[Path]:
        base = self.storage_path / "agg_trades"
        files: list[Path] = []
        for path in sorted(base.rglob("*.parquet")):
            file_date = _date_from_path(path)
            if file_date is None:
                continue
            if start_date and file_date < start_date:
                continue
            if end_date and file_date > end_date:
                continue
            try:
                import pyarrow.parquet as pq

                sample_columns = set(pq.ParquetFile(path).schema_arrow.names)
            except Exception as exc:
                logger.warning("[TICK-RHYTHM] skip unreadable parquet %s: %s", path, exc)
                continue
            if not set(_RAW_TICK_COLUMNS).issubset(sample_columns):
                continue
            files.append(path)
        if max_files is not None and len(files) > max_files:
            files = files[-int(max_files):]
        return files

    def _build_window_bars(self, raw_files: list[Path], *, window_seconds: int) -> pd.DataFrame:
        frames: list[pd.DataFrame] = []
        for path in raw_files:
            try:
                ticks = pd.read_parquet(path, columns=list(_RAW_TICK_COLUMNS))
            except Exception as exc:
                logger.warning("[TICK-RHYTHM] failed loading %s: %s", path, exc)
                continue
            if ticks.empty:
                continue
            ticks = ticks.dropna(subset=["timestamp", "price", "quantity", "is_buyer_maker"]).copy()
            if ticks.empty:
                continue
            ticks["timestamp"] = ticks["timestamp"].astype("int64")
            frames.append(self._aggregate_ticks(ticks, window_seconds=window_seconds))
        if not frames:
            return pd.DataFrame()
        return (
            pd.concat(frames, ignore_index=True)
            .sort_values("timestamp")
            .reset_index(drop=True)
        )

    def _aggregate_ticks(self, ticks: pd.DataFrame, *, window_seconds: int) -> pd.DataFrame:
        bucket_ms = int(window_seconds) * 1000
        ticks = ticks.copy()
        ticks["bucket"] = (ticks["timestamp"] // bucket_ms) * bucket_ms
        ticks["trade_usd"] = ticks["price"] * ticks["quantity"]
        ticks["is_buy"] = ~ticks["is_buyer_maker"].astype(bool)
        ticks["buy_usd"] = np.where(ticks["is_buy"], ticks["trade_usd"], 0.0)
        ticks["sell_usd"] = np.where(~ticks["is_buy"], ticks["trade_usd"], 0.0)
        ticks["direction"] = np.where(ticks["is_buy"], 1.0, -1.0)
        ticks = ticks.sort_values(["bucket", "timestamp"]).reset_index(drop=True)
        ticks["interval_ms"] = ticks.groupby("bucket", sort=False)["timestamp"].diff()

        group = ticks.groupby("bucket", sort=True)
        bars = group.agg(
            open=("price", "first"),
            high=("price", "max"),
            low=("price", "min"),
            close=("price", "last"),
            volume=("quantity", "sum"),
            notional=("trade_usd", "sum"),
            buy_usd=("buy_usd", "sum"),
            sell_usd=("sell_usd", "sum"),
            trade_count=("trade_usd", "count"),
        )
        bars["direction_net"] = group["direction"].mean()
        bars["window_vwap"] = bars["notional"] / bars["volume"].replace(0.0, np.nan)

        # 用文件级九十分位定义“大单”，避免逐窗口 group.apply 把研究器拖成龟速。
        # 这是研究阶段的近似口径：识别“明显大于常态的成交”，而不是追求每个 10 秒窗口内的局部排序。
        large_threshold = float(ticks["trade_usd"].quantile(0.9))
        large = ticks[ticks["trade_usd"] >= large_threshold]
        bars["large_trade_buy_ratio"] = large.groupby("bucket", sort=True)["is_buy"].mean().reindex(bars.index)

        interval = ticks.dropna(subset=["interval_ms"])
        interval_stats = interval.groupby("bucket", sort=True)["interval_ms"].agg(["std", "mean"])
        burst = interval_stats["std"] / interval_stats["mean"].replace(0.0, np.nan)
        bars["burst_index"] = burst.reindex(bars.index)

        bars = bars.reset_index().rename(columns={"bucket": "timestamp"})
        return bars

    def _add_features(self, bars: pd.DataFrame, *, window_seconds: int) -> pd.DataFrame:
        df = bars.copy()
        windows_5m = max(int(round(300 / window_seconds)), 2)
        windows_15m = max(int(round(900 / window_seconds)), windows_5m + 1)

        df["buy_share"] = df["buy_usd"] / df["notional"].replace(0.0, np.nan)
        df["sell_share"] = df["sell_usd"] / df["notional"].replace(0.0, np.nan)
        df["return_pct"] = df["close"].pct_change() * 100.0
        df["range_pct"] = (df["high"] - df["low"]) / df["close"].replace(0.0, np.nan) * 100.0
        roll_notional_5m = df["notional"].rolling(windows_5m, min_periods=windows_5m).sum()
        roll_volume_5m = df["volume"].rolling(windows_5m, min_periods=windows_5m).sum()
        roll_vwap_5m = roll_notional_5m / roll_volume_5m.replace(0.0, np.nan)
        df["vwap_dev_5m_pct"] = (df["close"] / roll_vwap_5m - 1.0) * 100.0
        df["trend_5m_pct"] = (df["close"] / df["close"].shift(windows_5m) - 1.0) * 100.0
        df["trend_15m_pct"] = (df["close"] / df["close"].shift(windows_15m) - 1.0) * 100.0
        df["pullback_from_high_pct"] = (df["close"] / df["high"].rolling(windows_5m, min_periods=windows_5m).max() - 1.0) * 100.0
        df["bounce_from_low_pct"] = (df["close"] / df["low"].rolling(windows_5m, min_periods=windows_5m).min() - 1.0) * 100.0
        burst_mean = df["burst_index"].rolling(windows_15m, min_periods=windows_5m).mean()
        burst_std = df["burst_index"].rolling(windows_15m, min_periods=windows_5m).std()
        df["burst_index_z"] = (df["burst_index"] - burst_mean) / burst_std.replace(0.0, np.nan)
        df["buy_share_delta"] = df["buy_share"] - df["buy_share"].rolling(windows_5m, min_periods=2).mean()
        df["direction_delta"] = df["direction_net"] - df["direction_net"].rolling(windows_5m, min_periods=2).mean()
        df["absorption_long_score"] = (
            (df["sell_share"].fillna(0.5) - 0.5) * 100.0
            - df["return_pct"].fillna(0.0)
            + df["burst_index_z"].fillna(0.0) * 0.8
        )
        df["restart_long_score"] = (
            df["buy_share_delta"].fillna(0.0) * 100.0
            + df["direction_delta"].fillna(0.0) * 40.0
            + (df["large_trade_buy_ratio"].fillna(0.5) - 0.5) * 60.0
        )
        df["absorption_short_score"] = (
            (df["buy_share"].fillna(0.5) - 0.5) * 100.0
            + df["return_pct"].fillna(0.0)
            + df["burst_index_z"].fillna(0.0) * 0.8
        )
        df["restart_short_score"] = (
            (df["sell_share"].fillna(0.5) - 0.5) * 100.0
            - df["direction_delta"].fillna(0.0) * 40.0
            + (0.5 - df["large_trade_buy_ratio"].fillna(0.5)) * 60.0
        )
        return df

    def _search_direction(self, df: pd.DataFrame, *, window_seconds: int, direction: str) -> list[RhythmCandidate]:
        if direction not in {"long", "short"}:
            return []

        specs = [spec for spec in _GRAMMAR_SPECS if spec.direction == direction and spec.feature in df.columns]
        by_role: dict[str, list[GrammarFeatureSpec]] = {}
        for spec in specs:
            by_role.setdefault(spec.role, []).append(spec)

        candidates: list[RhythmCandidate] = []
        horizons = self._horizon_grid(window_seconds)
        for pattern in _PATTERNS:
            if any(role not in by_role for role in pattern):
                continue
            for chosen_specs in self._role_product(by_role, pattern):
                for horizon in horizons:
                    candidate = self._search_pattern(df, direction=direction, pattern=pattern, chosen_specs=chosen_specs, horizon=horizon, window_seconds=window_seconds)
                    if candidate is not None:
                        candidates.append(candidate)

        candidates.sort(key=lambda item: (-item.score, -item.stats["oos_pf"], -item.stats["oos_wr"]))
        deduped: list[RhythmCandidate] = []
        seen: set[tuple[int, int, str, tuple[tuple[str, str, float], ...]]] = set()
        for item in candidates:
            key = (
                item.window_seconds,
                item.horizon_bars,
                item.direction,
                tuple((cond.feature, cond.operator, round(cond.threshold, 8)) for cond in item.conditions),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _role_product(by_role: dict[str, list[GrammarFeatureSpec]], pattern: tuple[str, ...]) -> Iterable[tuple[GrammarFeatureSpec, ...]]:
        import itertools

        buckets = [by_role[role] for role in pattern]
        return itertools.product(*buckets)

    @staticmethod
    def _horizon_grid(window_seconds: int) -> tuple[int, ...]:
        if window_seconds <= 10:
            return (3, 6, 12)
        if window_seconds <= 30:
            return (2, 4, 8)
        return (2, 3, 5)

    def _search_pattern(
        self,
        df: pd.DataFrame,
        *,
        direction: str,
        pattern: tuple[str, ...],
        chosen_specs: tuple[GrammarFeatureSpec, ...],
        horizon: int,
        window_seconds: int,
    ) -> RhythmCandidate | None:
        working = df.copy()
        fwd_col = f"fwd_ret_{direction}_{horizon}"
        sign = 1.0 if direction == "long" else -1.0
        working[fwd_col] = ((working["close"].shift(-horizon) - working["close"]) / working["close"]) * sign * 100.0 - self.round_trip_fee_pct
        usable = working.dropna(subset=[fwd_col]).reset_index(drop=True)
        if len(usable) < (self.min_train_n + self.min_test_n) * 4:
            return None

        split_idx = int(len(usable) * self.train_frac)
        train_df = usable.iloc[:split_idx].copy()
        test_df = usable.iloc[split_idx:].copy()
        if len(train_df) < self.min_train_n or len(test_df) < self.min_test_n:
            return None

        thresholds_per_spec: list[list[RhythmCondition]] = []
        for spec in chosen_specs:
            series = train_df[spec.feature].dropna()
            if len(series) < max(self.min_train_n, 20):
                return None
            options: list[RhythmCondition] = []
            for quantile in spec.quantiles:
                threshold = float(series.quantile(quantile / 100.0))
                options.append(
                    RhythmCondition(
                        feature=spec.feature,
                        operator=spec.operator,
                        threshold=threshold,
                        role=spec.role,
                        description=spec.description,
                    )
                )
            thresholds_per_spec.append(options)

        import itertools

        ranked_candidates: list[tuple[float, tuple[RhythmCondition, ...], dict[str, float]]] = []
        for conditions in itertools.product(*thresholds_per_spec):
            train_stats = self._evaluate_conditions(train_df, conditions=conditions, fwd_col=fwd_col)
            if train_stats["n"] < self.min_train_n:
                continue
            if train_stats["wr"] < self.min_train_wr:
                continue
            if train_stats["pf"] < self.min_train_pf:
                continue
            if train_stats["avg_ret"] <= 0.0:
                continue
            test_stats = self._evaluate_conditions(test_df, conditions=conditions, fwd_col=fwd_col)
            if test_stats["n"] < self.min_test_n:
                continue
            if test_stats["wr"] < 54.0:
                continue
            if test_stats["pf"] < 1.20:
                continue
            if test_stats["avg_ret"] < 0.01:
                continue

            degradation = max(0.0, train_stats["wr"] - test_stats["wr"])
            if degradation > 15.0:
                continue

            score = (
                test_stats["avg_ret"] * 28.0
                + min(test_stats["pf"], 5.0) * 6.0
                + test_stats["wr"] * 0.30
                + min(test_stats["n"], 200.0) * 0.06
            )
            stats = {
                "is_n": train_stats["n"],
                "is_wr": train_stats["wr"],
                "is_avg_ret": train_stats["avg_ret"],
                "is_pf": train_stats["pf"],
                "oos_n": test_stats["n"],
                "oos_wr": test_stats["wr"],
                "oos_avg_ret": test_stats["avg_ret"],
                "oos_pf": test_stats["pf"],
                "degradation_wr": round(float(degradation), 2),
            }
            ranked_candidates.append((float(score), tuple(conditions), stats))

        ranked_candidates.sort(key=lambda item: -item[0])
        for score, conditions, stats in ranked_candidates:
            train_mask = self._conditions_mask(train_df, conditions)
            test_mask = self._conditions_mask(test_df, conditions)
            train_mfe = self._mfe_coverage(train_df, train_mask, direction=direction, horizon=horizon)
            test_mfe = self._mfe_coverage(test_df, test_mask, direction=direction, horizon=horizon)
            if train_mfe < self.min_mfe_coverage_pct or test_mfe < self.min_mfe_coverage_pct:
                continue

            stats = {
                **stats,
                "is_mfe_coverage": round(float(train_mfe), 2),
                "oos_mfe_coverage": round(float(test_mfe), 2),
            }
            exit_plan = self._mine_vs_entry_exit_plan(
                usable,
                conditions=conditions,
                direction=direction,
                horizon=horizon,
            )
            if not exit_plan:
                continue

            return RhythmCandidate(
                window_seconds=window_seconds,
                horizon_bars=horizon,
                direction=direction,
                pattern=pattern,
                conditions=tuple(conditions),
                stats=stats,
                score=score,
                mechanism_story=_condition_story(direction, pattern),
                exit_plan=exit_plan,
            )
        return None

    def _evaluate_conditions(
        self,
        df: pd.DataFrame,
        *,
        conditions: tuple[RhythmCondition, ...],
        fwd_col: str,
    ) -> dict[str, float]:
        mask = pd.Series(True, index=df.index)
        for cond in conditions:
            column = df[cond.feature]
            if cond.operator == "<":
                mask = mask & (column < cond.threshold)
            else:
                mask = mask & (column > cond.threshold)
        mask = _cooldown_mask(mask.fillna(False), cooldown=self.cooldown_bars)
        valid = mask & df[fwd_col].notna()
        n = int(valid.sum())
        if n <= 0:
            return {"n": 0, "wr": 0.0, "avg_ret": 0.0, "pf": 0.0}

        returns = df.loc[valid, fwd_col].to_numpy(dtype=float)
        wins = returns[returns > 0]
        return {
            "n": n,
            "wr": round(float(len(wins) / n * 100.0), 2),
            "avg_ret": round(float(np.mean(returns)), 4),
            "pf": round(float(_profit_factor(returns)), 3),
        }

    def _conditions_mask(self, df: pd.DataFrame, conditions: tuple[RhythmCondition, ...]) -> pd.Series:
        mask = pd.Series(True, index=df.index)
        for cond in conditions:
            column = df[cond.feature]
            if cond.operator == "<":
                mask = mask & (column < cond.threshold)
            else:
                mask = mask & (column > cond.threshold)
        return _cooldown_mask(mask.fillna(False), cooldown=self.cooldown_bars)

    def _mfe_coverage(
        self,
        df: pd.DataFrame,
        mask: pd.Series,
        *,
        direction: str,
        horizon: int,
    ) -> float:
        valid_idx = np.flatnonzero((mask & df["close"].notna()).to_numpy())
        if len(valid_idx) == 0:
            return 0.0
        close = df["close"].to_numpy(dtype=float)
        high = df["high"].to_numpy(dtype=float) if "high" in df.columns else close
        low = df["low"].to_numpy(dtype=float) if "low" in df.columns else close
        covered = 0
        total = 0
        for idx in valid_idx:
            end = min(idx + int(horizon), len(close) - 1)
            if end <= idx or close[idx] == 0 or np.isnan(close[idx]):
                continue
            if direction == "long":
                mfe = (np.nanmax(high[idx + 1 : end + 1]) / close[idx] - 1.0) * 100.0
            else:
                mfe = (close[idx] / np.nanmin(low[idx + 1 : end + 1]) - 1.0) * 100.0
            if np.isfinite(mfe):
                total += 1
                if mfe > self.round_trip_fee_pct:
                    covered += 1
        return float(covered / total * 100.0) if total else 0.0

    def _mine_vs_entry_exit_plan(
        self,
        df: pd.DataFrame,
        *,
        conditions: tuple[RhythmCondition, ...],
        direction: str,
        horizon: int,
    ) -> dict | None:
        from alpha.live_discovery import _mine_exit_conditions

        entry_mask = self._conditions_mask(df, conditions)
        entry = conditions[0]
        combo_conditions = [
            {
                "feature": cond.feature,
                "operator": cond.operator,
                "threshold": cond.threshold,
            }
            for cond in conditions[1:]
        ]
        return _mine_exit_conditions(
            df,
            entry_mask,
            direction,
            horizon,
            entry.feature,
            entry.operator,
            entry.threshold,
            combo_conditions,
            mechanism_type="tick_rhythm",
            min_exit_samples=max(self.min_test_n, 12),
        )
