from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import pandas as pd

from alpha.combo_scanner import _crossing_mask

logger = logging.getLogger(__name__)

_SELLER_IMPULSE_CONFIRM_FEATURES = [
    "taker_buy_sell_ratio",
    "volume_vs_ma20",
    "volume_acceleration",
    "spread_vs_ma20",
    "large_trade_buy_ratio",
    "direction_net_1m",
    "sell_notional_share_1m",
    "trade_burst_index",
    "direction_autocorr",
]


@dataclass(frozen=True)
class SeedSpec:
    feature: str
    operator: str
    direction: str
    quantiles: tuple[int, ...]
    cooldown: int = 60
    min_is_n: int = 15
    min_oos_n: int = 20


_SELLER_IMPULSE_SPECS: tuple[SeedSpec, ...] = (
    SeedSpec("direction_net_1m", "<", "short", (3, 5, 7, 10, 15, 20), cooldown=30),
    SeedSpec("sell_notional_share_1m", ">", "short", (80, 85, 90, 93, 95), cooldown=30),
    SeedSpec("large_trade_buy_ratio", "<", "short", (5, 10, 15, 20, 25), cooldown=30),
    SeedSpec("taker_buy_sell_ratio", "<", "short", (5, 10, 15, 20, 25), cooldown=45),
    SeedSpec("trade_burst_index", ">", "short", (75, 80, 85, 90, 95), cooldown=30),
    SeedSpec("volume_vs_ma20", ">", "short", (75, 80, 85, 90, 95), cooldown=45),
)


class RealtimeSeedMiner:
    """从实时成交痕迹里挖主动卖压爆发种子。"""

    def __init__(
        self,
        train_frac: float = 0.67,
        min_oos_wr: float = 58.0,
        min_oos_pf: float = 1.10,
        min_oos_edge_pct: float = 0.02,
        max_wr_drop: float = 12.0,
        top_k: int = 12,
    ) -> None:
        self.train_frac = train_frac
        self.min_oos_wr = min_oos_wr
        self.min_oos_pf = min_oos_pf
        self.min_oos_edge_pct = min_oos_edge_pct
        self.max_wr_drop = max_wr_drop
        self.top_k = top_k

    def mine(self, df: pd.DataFrame, horizons: Iterable[int]) -> list[dict]:
        if df.empty:
            return []

        candidates: list[dict] = []
        for spec in _SELLER_IMPULSE_SPECS:
            if spec.feature not in df.columns:
                continue

            for horizon in horizons:
                fwd_col = f"fwd_ret_{int(horizon)}"
                if fwd_col not in df.columns:
                    continue
                best_seed = self._best_seed_for_spec(
                    df=df,
                    fwd_col=fwd_col,
                    horizon=int(horizon),
                    spec=spec,
                )
                if best_seed is not None:
                    candidates.append(best_seed)

        if not candidates:
            logger.info("[RT-SEED] 未找到满足条件的 seller_impulse 种子")
            return []

        candidates.sort(
            key=lambda item: (
                float(item.get("_score", 0.0)),
                float(item.get("_oos_avg_ret", 0.0)),
                float(item.get("_oos_pf", 0.0)),
                int(item.get("_oos_n", 0)),
            ),
            reverse=True,
        )

        deduped: list[dict] = []
        seen_keys: set[tuple[str, int]] = set()
        for item in candidates:
            key = (str(item["feature"]), int(item["horizon"]))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            item.pop("_score", None)
            item.pop("_oos_avg_ret", None)
            item.pop("_oos_pf", None)
            item.pop("_oos_n", None)
            deduped.append(item)
            if len(deduped) >= self.top_k:
                break

        logger.info("[RT-SEED] 保留 %d 个实时卖压种子", len(deduped))
        for seed in deduped:
            stats = seed.get("seed_stats", {})
            logger.info(
                "[RT-SEED] %s | OOS WR=%.1f%% n=%d PF=%.2f avg=%.4f%%",
                seed["name"],
                float(stats.get("oos_wr", 0.0)),
                int(stats.get("oos_n", 0)),
                float(stats.get("oos_pf", 0.0)),
                float(stats.get("oos_avg_ret", 0.0)),
            )
        return deduped

    def _best_seed_for_spec(
        self,
        *,
        df: pd.DataFrame,
        fwd_col: str,
        horizon: int,
        spec: SeedSpec,
    ) -> dict | None:
        valid_df = df[df[spec.feature].notna() & df[fwd_col].notna()].copy()
        if len(valid_df) < (spec.min_is_n + spec.min_oos_n):
            return None

        split_idx = int(len(valid_df) * self.train_frac)
        train_df = valid_df.iloc[:split_idx].copy()
        test_df = valid_df.iloc[split_idx:].copy()
        if len(train_df) < spec.min_is_n or len(test_df) < spec.min_oos_n:
            return None

        train_col = train_df[spec.feature]
        if train_col.notna().sum() < spec.min_is_n * 2:
            return None

        best: dict | None = None
        best_score = float("-inf")

        for quantile in spec.quantiles:
            threshold = float(train_col.quantile(quantile / 100))
            seed_stats_is = self._eval_seed(
                df=train_df,
                fwd_col=fwd_col,
                feature=spec.feature,
                operator=spec.operator,
                threshold=threshold,
                direction=spec.direction,
                cooldown=spec.cooldown,
            )
            if seed_stats_is["n"] < spec.min_is_n:
                continue

            seed_stats_oos = self._eval_seed(
                df=test_df,
                fwd_col=fwd_col,
                feature=spec.feature,
                operator=spec.operator,
                threshold=threshold,
                direction=spec.direction,
                cooldown=spec.cooldown,
            )
            if seed_stats_oos["n"] < spec.min_oos_n:
                continue
            if seed_stats_oos["wr"] < self.min_oos_wr:
                continue
            if seed_stats_oos["pf"] < self.min_oos_pf:
                continue

            oos_avg_ret_pct = float(seed_stats_oos["avg_ret"] * 100.0)
            if oos_avg_ret_pct < self.min_oos_edge_pct:
                continue
            if seed_stats_is["wr"] - seed_stats_oos["wr"] > self.max_wr_drop:
                continue

            score = (
                oos_avg_ret_pct * 25.0
                + float(seed_stats_oos["pf"]) * 6.0
                + float(seed_stats_oos["wr"]) * 0.35
                + min(float(seed_stats_oos["n"]), 120.0) * 0.12
            )
            if score <= best_score:
                continue

            best_score = score
            best = {
                "name": f"rt_{spec.feature}_{quantile}",
                "feature": spec.feature,
                "op": spec.operator,
                "threshold": threshold,
                "horizon": horizon,
                "direction": spec.direction,
                "mechanism_type": "seller_impulse",
                "confirm_features": list(_SELLER_IMPULSE_CONFIRM_FEATURES),
                "group": f"seller_impulse_{spec.feature}",
                "cooldown": spec.cooldown,
                "origin": "realtime_seed_miner",
                "seed_stats": {
                    "is_n": int(seed_stats_is["n"]),
                    "is_wr": round(float(seed_stats_is["wr"]), 2),
                    "is_avg_ret": round(float(seed_stats_is["avg_ret"] * 100.0), 4),
                    "is_pf": round(float(seed_stats_is["pf"]), 3),
                    "oos_n": int(seed_stats_oos["n"]),
                    "oos_wr": round(float(seed_stats_oos["wr"]), 2),
                    "oos_avg_ret": round(oos_avg_ret_pct, 4),
                    "oos_pf": round(float(seed_stats_oos["pf"]), 3),
                },
                "_score": score,
                "_oos_avg_ret": oos_avg_ret_pct,
                "_oos_pf": float(seed_stats_oos["pf"]),
                "_oos_n": int(seed_stats_oos["n"]),
            }

        return best

    @staticmethod
    def _eval_seed(
        *,
        df: pd.DataFrame,
        fwd_col: str,
        feature: str,
        operator: str,
        threshold: float,
        direction: str,
        cooldown: int,
    ) -> dict[str, float]:
        if feature not in df.columns or fwd_col not in df.columns:
            return {"n": 0, "wr": 0.0, "avg_ret": 0.0, "pf": 0.0}

        mask = pd.Series(
            _crossing_mask(df[feature].values, operator, threshold, cooldown=cooldown),
            index=df.index,
        )
        valid = mask & df[fwd_col].notna()
        n = int(valid.sum())
        if n == 0:
            return {"n": 0, "wr": 0.0, "avg_ret": 0.0, "pf": 0.0}

        fwd = df.loc[valid, fwd_col].values
        rets = -fwd if direction == "short" else fwd
        wins = rets[rets > 0]
        losses = rets[rets <= 0]
        avg_win = float(wins.mean()) if len(wins) > 0 else 0.0
        avg_loss = float(abs(losses.mean())) if len(losses) > 0 else 0.0
        pf = (
            (avg_win * len(wins)) / (avg_loss * len(losses))
            if len(losses) > 0 and avg_loss > 0
            else float("inf")
        )
        return {
            "n": n,
            "wr": float(len(wins) / n * 100.0),
            "avg_ret": float(rets.mean()),
            "pf": float(pf),
        }
