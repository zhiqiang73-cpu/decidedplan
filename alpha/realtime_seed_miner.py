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

# Mirror of seller_impulse for LONG direction.
# Physics: buyers exhaust sellers → price rises → mechanism decays when
# selling pressure returns (taker_buy_sell_ratio drops, sell_notional rises).
_BUYER_IMPULSE_CONFIRM_FEATURES = [
    "taker_buy_sell_ratio",      # high = buyers dominating (entry confirm)
    "volume_vs_ma20",            # above-average volume = conviction behind buying
    "volume_acceleration",       # positive = buying pressure still building
    "spread_vs_ma20",            # wide spread = impact, makers retreating
    "large_trade_buy_ratio",     # high = large orders are buying
    "direction_net_1m",          # positive = net buyer flow
    "sell_notional_share_1m",    # LOW = buyers dominating notional
    "trade_burst_index",         # high burst + buyer flow = aggressive buying
    "direction_autocorr",        # high = directional persistence (trend)
]

_MM_REBALANCE_CONFIRM_FEATURES = [
    "spread_vs_ma20",
    "kyle_lambda",
    "quote_imbalance",
    "bid_depth_ratio",
    "spread_anomaly",
    "direction_autocorr",
]

_LIQUIDATION_CONFIRM_FEATURES = [
    "btc_liq_net_pressure",
    "total_liq_usd_5m",
    "liq_size_p90_5m",
    "taker_buy_sell_ratio",
    "direction_net_1m",
    "direction_autocorr",
]

_FUNDING_DIVERGENCE_CONFIRM_FEATURES = [
    "oi_change_rate_5m",
    "oi_change_rate_1h",
    "mark_basis",
    "mark_basis_ma10",
    "rt_funding_rate",
    "ls_ratio_change_5m",
]


@dataclass(frozen=True)
class SeedSpec:
    feature: str
    operator: str
    direction: str
    quantiles: tuple[int, ...]
    mechanism_type: str = "seller_impulse"
    confirm_features: tuple[str, ...] = ()
    group: str = ""
    cooldown: int = 60
    min_is_n: int = 15
    min_oos_n: int = 20


@dataclass(frozen=True)
class MultiConditionSeedSpec:
    """Multi-condition seed spec (max 3 conditions).

    For signals that need multiple features + context simultaneously.
    Bypasses single-feature atom -> combo_scanner pipeline.
    """
    conditions: tuple[tuple[str, str, tuple[int, ...]], ...]  # (feature, op, quantiles)
    direction: str
    mechanism_type: str
    context: str = ""  # "TREND_UP" / "TREND_DOWN" / ""
    confirm_features: tuple[str, ...] = ()
    group: str = ""
    cooldown: int = 60
    min_is_n: int = 15
    min_oos_n: int = 10


_REALTIME_SEED_SPECS: tuple[SeedSpec, ...] = (
    SeedSpec(
        "direction_net_1m", "<", "short", (3, 5, 7, 10, 15, 20),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_SELLER_IMPULSE_CONFIRM_FEATURES),
        group="seller_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "sell_notional_share_1m", ">", "short", (80, 85, 90, 93, 95),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_SELLER_IMPULSE_CONFIRM_FEATURES),
        group="seller_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "large_trade_buy_ratio", "<", "short", (5, 10, 15, 20, 25),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_SELLER_IMPULSE_CONFIRM_FEATURES),
        group="seller_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "taker_buy_sell_ratio", "<", "short", (5, 10, 15, 20, 25),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_SELLER_IMPULSE_CONFIRM_FEATURES),
        group="seller_impulse_flow",
        cooldown=45,
    ),
    SeedSpec(
        "trade_burst_index", ">", "short", (75, 80, 85, 90, 95),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_SELLER_IMPULSE_CONFIRM_FEATURES),
        group="seller_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "volume_vs_ma20", ">", "short", (75, 80, 85, 90, 95),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_SELLER_IMPULSE_CONFIRM_FEATURES),
        group="seller_impulse_flow",
        cooldown=45,
    ),

    # ── buyer_impulse: LONG mirrors of seller_impulse ────────────────────
    # Physics: large active buy orders flood the book, eating through asks.
    # Spread widens as market makers retreat. When buying pressure fades
    # and taker_buy_sell_ratio drops, the impulse is spent → exit.
    SeedSpec(
        "direction_net_1m", ">", "long", (80, 85, 90, 93, 95, 97),
        mechanism_type="buyer_impulse",
        confirm_features=tuple(_BUYER_IMPULSE_CONFIRM_FEATURES),
        group="buyer_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "sell_notional_share_1m", "<", "long", (3, 5, 7, 10, 15, 20),
        mechanism_type="buyer_impulse",
        confirm_features=tuple(_BUYER_IMPULSE_CONFIRM_FEATURES),
        group="buyer_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "large_trade_buy_ratio", ">", "long", (75, 80, 85, 90, 95),
        mechanism_type="buyer_impulse",
        confirm_features=tuple(_BUYER_IMPULSE_CONFIRM_FEATURES),
        group="buyer_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "taker_buy_sell_ratio", ">", "long", (75, 80, 85, 90, 95),
        mechanism_type="buyer_impulse",
        confirm_features=tuple(_BUYER_IMPULSE_CONFIRM_FEATURES),
        group="buyer_impulse_flow",
        cooldown=45,
    ),
    SeedSpec(
        "trade_burst_index", ">", "long", (75, 80, 85, 90, 95),
        mechanism_type="buyer_impulse",
        confirm_features=tuple(_BUYER_IMPULSE_CONFIRM_FEATURES),
        group="buyer_impulse_flow",
        cooldown=30,
    ),
    SeedSpec(
        "volume_vs_ma20", ">", "long", (75, 80, 85, 90, 95),
        mechanism_type="buyer_impulse",
        confirm_features=tuple(_BUYER_IMPULSE_CONFIRM_FEATURES),
        group="buyer_impulse_flow",
        cooldown=45,
    ),

    SeedSpec(
        "quote_imbalance", ">", "long", (80, 85, 90, 93, 95),
        mechanism_type="mm_rebalance",
        confirm_features=tuple(_MM_REBALANCE_CONFIRM_FEATURES),
        group="mm_rebalance_book",
        cooldown=20,
    ),
    SeedSpec(
        "quote_imbalance", "<", "short", (5, 10, 15, 20),
        mechanism_type="mm_rebalance",
        confirm_features=tuple(_MM_REBALANCE_CONFIRM_FEATURES),
        group="mm_rebalance_book",
        cooldown=20,
    ),
    SeedSpec(
        "bid_depth_ratio", ">", "long", (80, 85, 90, 93, 95),
        mechanism_type="mm_rebalance",
        confirm_features=tuple(_MM_REBALANCE_CONFIRM_FEATURES),
        group="mm_rebalance_book",
        cooldown=20,
    ),
    SeedSpec(
        "bid_depth_ratio", "<", "short", (5, 10, 15, 20),
        mechanism_type="mm_rebalance",
        confirm_features=tuple(_MM_REBALANCE_CONFIRM_FEATURES),
        group="mm_rebalance_book",
        cooldown=20,
    ),
    SeedSpec(
        "btc_liq_net_pressure", ">", "short", (75, 80, 85, 90, 95),
        mechanism_type="seller_impulse",
        confirm_features=tuple(_LIQUIDATION_CONFIRM_FEATURES),
        group="liq_pressure",
        cooldown=25,
    ),
    SeedSpec(
        "btc_liq_net_pressure", "<", "long", (5, 10, 15, 20, 25),
        mechanism_type="volume_climax_reversal",
        confirm_features=tuple(_LIQUIDATION_CONFIRM_FEATURES),
        group="liq_pressure",
        cooldown=25,
    ),
    SeedSpec(
        "mark_basis_ma10", ">", "short", (75, 80, 85, 90, 95),
        mechanism_type="funding_divergence",
        confirm_features=tuple(_FUNDING_DIVERGENCE_CONFIRM_FEATURES),
        group="basis_divergence",
        cooldown=40,
    ),
    SeedSpec(
        "mark_basis_ma10", "<", "long", (5, 10, 15, 20, 25),
        mechanism_type="funding_divergence",
        confirm_features=tuple(_FUNDING_DIVERGENCE_CONFIRM_FEATURES),
        group="basis_divergence",
        cooldown=40,
    ),
)


_MULTI_CONDITION_SPECS: tuple[MultiConditionSeedSpec, ...] = (
    MultiConditionSeedSpec(
        conditions=(
            ("oi_change_rate_5m", ">", (60, 70, 80, 85, 90)),
            ("taker_buy_sell_ratio", ">", (50, 55, 60, 65, 70)),
            ("volume_vs_ma20", ">", (55, 60, 65, 70, 75)),
        ),
        direction="long",
        mechanism_type="oi_accumulation_long",
        context="TREND_UP",
        group="oi_accumulation",
        cooldown=60,
    ),
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
        for spec in _REALTIME_SEED_SPECS:
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
            logger.info("[RT-SEED] No seeds found matching thresholds")
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
        seen_keys: set[tuple[str, int, str]] = set()
        for item in candidates:
            key = (
                str(item["feature"]),
                int(item["horizon"]),
                str(item.get("direction", "")),
            )
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

        logger.info("[RT-SEED] Retained %d realtime seeds (long+short)", len(deduped))
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
                "mechanism_type": spec.mechanism_type,
                "confirm_features": list(spec.confirm_features or _SELLER_IMPULSE_CONFIRM_FEATURES),
                "group": spec.group or f"{spec.mechanism_type}_{spec.feature}",
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
