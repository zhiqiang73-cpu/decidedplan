"""
Conviction Engine - 自适应交易大脑

核心思想: 用 ONE score（信念度, 0-1）替代几十条 if-else 规则。

两个模型（均为在线逻辑回归，各 6 个参数）:
  - Entry model: 应该入场吗？（信号触发时计算）
  - Hold model:  应该继续持有吗？（每根 K 线计算）

五个 Meta-Feature，每个都有物理含义:
  Entry: mechanism_clearance, trend_alignment, regime_fitness, confirm_depth, recent_streak
  Hold:  mechanism_drift, pnl_velocity, time_decay, adverse_ratio, trend_shift

学习: 每笔交易关闭后，逐 bar 样本标注 + 梯度更新。
不对称损失: 亏损时持有的惩罚 2x（偏保守）。

Phase 0（shadow mode）: 只记录分数，不干预交易决策。
"""

from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_STATE_FILE = Path(__file__).parent / "output" / "conviction_state.json"

# Max possible physical confirms (from alpha_rules.py: 7 confirm types)
_MAX_CONFIRMS = 7


# ---------------------------------------------------------------------------
# Numerics
# ---------------------------------------------------------------------------

def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _clip(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _sf(val, default: float = 0.0) -> float:
    """Safely convert to float, treating NaN/Inf as default."""
    if val is None:
        return default
    try:
        v = float(val)
        return default if (math.isnan(v) or math.isinf(v)) else v
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Feature vector helpers (no numpy dependency — use plain lists)
# ---------------------------------------------------------------------------

def _dot(w: list[float], x: list[float]) -> float:
    return sum(wi * xi for wi, xi in zip(w, x))


def _scale_sub(w: list[float], grad: list[float], lr: float, l2: float) -> list[float]:
    """w -= lr * (grad + l2 * w)"""
    return [wi - lr * (gi + l2 * wi) for wi, gi in zip(w, grad)]


# ===========================================================================
# ConvictionEngine
# ===========================================================================

class ConvictionEngine:
    """
    Conviction-based adaptive brain.

    In shadow mode (default): computes and logs conviction scores every bar
    but does NOT affect entry/exit/stop decisions. Safe to deploy from day 1.
    """

    def __init__(
        self,
        shadow_mode: bool = True,
        learning_rate: float = 0.02,
        l2_reg: float = 0.001,
        loss_penalty: float = 2.0,
        state_path: Optional[Path] = None,
    ):
        self.shadow_mode = shadow_mode
        self._lr = learning_rate
        self._l2 = l2_reg
        self._loss_penalty = loss_penalty
        self._state_path = state_path or _STATE_FILE

        # Model parameters: 5 weights + 1 bias each
        self._entry_w: list[float] = [0.0] * 5
        self._entry_b: float = 0.0
        self._hold_w: list[float] = [0.0] * 5
        self._hold_b: float = 0.0

        # Training counters
        self._entry_n: int = 0
        self._hold_n: int = 0

        # Per-strategy rolling returns (for regime_fitness + recent_streak)
        self._strategy_returns: dict[str, list[float]] = {}
        self._regime_returns: dict[str, list[float]] = {}

        # Per-position bar snapshots (key: "family|direction")
        self._position_snapshots: dict[str, list[dict]] = {}

        self._load_state()

    # ── Public API: Scoring ─────────────────────────────────────────────────

    def entry_score(
        self,
        feature_value: float,
        threshold: float,
        direction: str,
        trend: str,
        family: str,
        regime: str,
        physical_confirms: list[str] | None,
        op: str = ">",
    ) -> float:
        """Compute entry conviction (0-1). Higher = more confident to enter."""
        x = self._entry_features(
            feature_value, threshold, direction, trend,
            family, regime, physical_confirms or [], op,
        )
        return _sigmoid(_dot(self._entry_w, x) + self._entry_b)

    def hold_score(
        self,
        entry_feature_value: float,
        current_feature_value: float,
        direction: str,
        current_return: float,
        bars_held: int,
        max_hold: int,
        adverse_pct: float,
        stop_pct: float,
        entry_trend: str,
        current_trend: str,
        prev_returns: list[float] | None,
        op: str = ">",
    ) -> float:
        """Compute hold conviction (0-1). Higher = more confident to keep holding."""
        x = self._hold_features(
            entry_feature_value, current_feature_value, direction,
            current_return, bars_held, max_hold, adverse_pct, stop_pct,
            entry_trend, current_trend, prev_returns or [], op,
        )
        return _sigmoid(_dot(self._hold_w, x) + self._hold_b)

    def compute_hold_features(
        self,
        entry_feature_value: float,
        current_feature_value: float,
        direction: str,
        current_return: float,
        bars_held: int,
        max_hold: int,
        adverse_pct: float,
        stop_pct: float,
        entry_trend: str,
        current_trend: str,
        prev_returns: list[float] | None,
        op: str = ">",
    ) -> list[float]:
        """Compute hold meta-features (public, for recording bar snapshots)."""
        return self._hold_features(
            entry_feature_value, current_feature_value, direction,
            current_return, bars_held, max_hold, adverse_pct, stop_pct,
            entry_trend, current_trend, prev_returns or [], op,
        )

    # ── Public API: Bar Recording ───────────────────────────────────────────

    def record_bar(
        self,
        pos_key: str,
        hold_features: list[float],
        current_return: float,
    ) -> None:
        """Record one bar's meta-features for a position (for learning later)."""
        snapshots = self._position_snapshots.setdefault(pos_key, [])
        if len(snapshots) < 200:  # cap memory
            snapshots.append({
                "f": hold_features,
                "r": round(current_return, 6),
            })

    def clear_position(self, pos_key: str) -> None:
        """Discard snapshots for a position (e.g. on non-fill)."""
        self._position_snapshots.pop(pos_key, None)

    # ── Public API: Learning ────────────────────────────────────────────────

    def learn_from_trade(
        self,
        pos_key: str,
        entry_features: list[float],
        final_return_pct: float,
        family: str,
        regime: str,
    ) -> dict[str, Any]:
        """Learn from a completed trade. Returns learning stats."""
        # 1. Update strategy performance trackers
        self._update_strategy_returns(family, final_return_pct, regime)

        # 2. Train entry model (1 sample per trade)
        is_win = 1.0 if final_return_pct > 0 else 0.0
        entry_loss = self._train_step(
            self._entry_w, "entry", entry_features, is_win,
            weight=max(0.01, abs(final_return_pct)),
        )
        self._entry_n += 1

        # 3. Train hold model (N samples from per-bar snapshots)
        snapshots = self._position_snapshots.pop(pos_key, [])
        hold_updates = 0
        hold_loss_sum = 0.0
        for snap in snapshots:
            remaining = final_return_pct - snap["r"]
            label = 1.0 if remaining > 0 else 0.0
            weight = abs(remaining)
            if label == 0.0:
                weight *= self._loss_penalty  # asymmetric: 2x penalty for holding through losses
            if weight < 1e-6:
                continue
            loss = self._train_step(
                self._hold_w, "hold", snap["f"], label, weight,
            )
            self._hold_n += 1
            hold_updates += 1
            hold_loss_sum += loss

        # 4. Persist
        self._save_state()

        stats = {
            "entry_loss": round(entry_loss, 4),
            "hold_updates": hold_updates,
            "hold_avg_loss": round(hold_loss_sum / max(1, hold_updates), 4),
            "entry_n": self._entry_n,
            "hold_n": self._hold_n,
        }
        logger.info(
            "[BRAIN] Learned from %s: final=%.3f%%, entry_loss=%.4f, "
            "hold_updates=%d, total_entry=%d, total_hold=%d",
            pos_key, final_return_pct, entry_loss,
            hold_updates, self._entry_n, self._hold_n,
        )
        return stats

    # ── Public API: Strategy Fitness ────────────────────────────────────────

    def get_strategy_fitness(self, family: str, regime: str = "") -> float:
        """EMA win rate for a strategy in current regime (0-1, default 0.5)."""
        key = f"{family}|{regime}" if regime else family
        returns = self._regime_returns.get(key) if regime else None
        if not returns:
            returns = self._strategy_returns.get(family)
        if not returns:
            return 0.5  # neutral prior
        recent = returns[-20:]
        wins = sum(1 for r in recent if r > 0)
        return wins / len(recent)

    def get_recent_streak(self, family: str) -> float:
        """EMA of recent returns for a strategy."""
        returns = self._strategy_returns.get(family)
        if not returns:
            return 0.0
        recent = returns[-5:]
        alpha = 0.4
        ema = recent[0]
        for r in recent[1:]:
            ema = alpha * r + (1 - alpha) * ema
        return ema

    # ── Public API: Diagnostics ─────────────────────────────────────────────

    @property
    def entry_weights(self) -> dict[str, float]:
        names = [
            "mechanism_clearance", "trend_alignment", "regime_fitness",
            "confirm_depth", "recent_streak",
        ]
        return {n: round(w, 4) for n, w in zip(names, self._entry_w)}

    @property
    def hold_weights(self) -> dict[str, float]:
        names = [
            "mechanism_drift", "pnl_velocity", "time_decay",
            "adverse_ratio", "trend_shift",
        ]
        return {n: round(w, 4) for n, w in zip(names, self._hold_w)}

    def status_summary(self) -> dict[str, Any]:
        return {
            "shadow_mode": self.shadow_mode,
            "entry_n": self._entry_n,
            "hold_n": self._hold_n,
            "entry_weights": self.entry_weights,
            "entry_bias": round(self._entry_b, 4),
            "hold_weights": self.hold_weights,
            "hold_bias": round(self._hold_b, 4),
            "tracked_strategies": sorted(self._strategy_returns.keys()),
        }

    # ── Meta-Feature Computation ────────────────────────────────────────────

    def _entry_features(
        self,
        feature_value: float,
        threshold: float,
        direction: str,
        trend: str,
        family: str,
        regime: str,
        physical_confirms: list[str],
        op: str = ">",
    ) -> list[float]:
        """Compute 5 entry meta-features."""
        # 1. mechanism_clearance: how far past threshold (normalized)
        #    For ">" rules: clearance = (value - threshold) / |threshold|  (positive = past)
        #    For "<" rules: clearance = (threshold - value) / |threshold|  (positive = past)
        fv = _sf(feature_value)
        th = _sf(threshold)
        if abs(th) > 1e-10:
            clearance = (fv - th) / abs(th) if op == ">" else (th - fv) / abs(th)
        else:
            clearance = 0.0
        clearance = _clip(clearance, -3.0, 3.0)

        # 2. trend_alignment: +1 if direction matches trend, -1 if against
        alignment = 0.0
        if direction == "short":
            alignment = {"TREND_DOWN": 1.0, "TREND_UP": -1.0}.get(trend, 0.0)
        elif direction == "long":
            alignment = {"TREND_UP": 1.0, "TREND_DOWN": -1.0}.get(trend, 0.0)

        # 3. regime_fitness: strategy win rate in this regime
        fitness = self.get_strategy_fitness(family, regime)

        # 4. confirm_depth: physical confirms / max possible
        depth = min(1.0, len(physical_confirms) / _MAX_CONFIRMS)

        # 5. recent_streak: EMA of recent returns for this strategy
        streak = _clip(self.get_recent_streak(family), -2.0, 2.0)

        return [clearance, alignment, fitness, depth, streak]

    def _hold_features(
        self,
        entry_feature_value: float,
        current_feature_value: float,
        direction: str,
        current_return: float,
        bars_held: int,
        max_hold: int,
        adverse_pct: float,
        stop_pct: float,
        entry_trend: str,
        current_trend: str,
        prev_returns: list[float],
        op: str = ">",
    ) -> list[float]:
        """Compute 5 hold meta-features."""
        efv = _sf(entry_feature_value)
        cfv = _sf(current_feature_value)

        # 1. mechanism_drift: how much has entry feature changed since entry
        #    positive = mechanism still valid (feature still past threshold)
        #    negative = mechanism deteriorating (feature reverting toward threshold)
        #    For ">" rules: feature staying above threshold is good → drift = (cfv - efv)
        #    For "<" rules: feature staying below threshold is good → drift = (efv - cfv)
        if abs(efv) > 1e-10:
            raw_drift = (cfv - efv) / abs(efv) if op == ">" else (efv - cfv) / abs(efv)
        else:
            raw_drift = 0.0
        drift = _clip(raw_drift, -3.0, 3.0)

        # 2. pnl_velocity: rate of P&L change (improving or worsening?)
        if prev_returns and len(prev_returns) >= 2:
            velocity = (current_return - prev_returns[0]) / len(prev_returns)
        else:
            velocity = 0.0
        velocity = _clip(velocity, -1.0, 1.0)

        # 3. time_decay: remaining fraction of expected hold period
        max_h = max(1, int(max_hold))
        decay = max(0.0, 1.0 - bars_held / max_h)

        # 4. adverse_ratio: how close to stop line (0 = far, 1 = at stop)
        adverse_r = min(1.0, adverse_pct / stop_pct) if stop_pct > 0 else 0.0

        # 5. trend_shift: has trend changed since entry?
        #    +1 = shifted in our favor, -1 = shifted against, 0 = same
        shift = 0.0
        if direction == "short":
            if current_trend == "TREND_DOWN" and entry_trend != "TREND_DOWN":
                shift = 1.0
            elif current_trend == "TREND_UP" and entry_trend != "TREND_UP":
                shift = -1.0
        elif direction == "long":
            if current_trend == "TREND_UP" and entry_trend != "TREND_UP":
                shift = 1.0
            elif current_trend == "TREND_DOWN" and entry_trend != "TREND_DOWN":
                shift = -1.0

        return [drift, velocity, decay, adverse_r, shift]

    # ── Online Learning (SGD) ───────────────────────────────────────────────

    def _train_step(
        self,
        weights: list[float],
        model_name: str,
        x: list[float],
        label: float,
        weight: float,
    ) -> float:
        """One SGD step. Returns absolute error."""
        bias = self._entry_b if model_name == "entry" else self._hold_b
        pred = _sigmoid(_dot(weights, x) + bias)
        error = pred - label
        grad_scalar = error * weight

        # Update weights: w -= lr * (grad * x + l2 * w)
        new_w = _scale_sub(weights, [grad_scalar * xi for xi in x], self._lr, self._l2)
        for i in range(len(weights)):
            weights[i] = new_w[i]

        # Update bias
        new_bias = bias - self._lr * grad_scalar
        if model_name == "entry":
            self._entry_b = new_bias
        else:
            self._hold_b = new_bias

        return abs(error)

    # ── Strategy Performance Tracking ───────────────────────────────────────

    def _update_strategy_returns(self, family: str, return_pct: float, regime: str) -> None:
        buf = self._strategy_returns.setdefault(family, [])
        buf.append(return_pct)
        if len(buf) > 100:
            self._strategy_returns[family] = buf[-50:]

        if regime:
            key = f"{family}|{regime}"
            buf2 = self._regime_returns.setdefault(key, [])
            buf2.append(return_pct)
            if len(buf2) > 100:
                self._regime_returns[key] = buf2[-50:]

    # ── Persistence ─────────────────────────────────────────────────────────

    def _save_state(self) -> None:
        state = {
            "entry_w": self._entry_w,
            "entry_b": self._entry_b,
            "hold_w": self._hold_w,
            "hold_b": self._hold_b,
            "entry_n": self._entry_n,
            "hold_n": self._hold_n,
            "strategy_returns": {k: v[-50:] for k, v in self._strategy_returns.items()},
            "regime_returns": {k: v[-50:] for k, v in self._regime_returns.items()},
            "saved_at": time.time(),
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._state_path, "w") as f:
                json.dump(state, f, indent=2)
        except Exception as exc:
            logger.warning("[BRAIN] Save state failed: %s", exc)

    def _load_state(self) -> None:
        if not self._state_path.exists():
            logger.info("[BRAIN] No saved state, starting fresh (all weights=0, conviction=0.5)")
            return
        try:
            with open(self._state_path) as f:
                state = json.load(f)
            self._entry_w = [float(w) for w in state["entry_w"]]
            self._entry_b = float(state["entry_b"])
            self._hold_w = [float(w) for w in state["hold_w"]]
            self._hold_b = float(state["hold_b"])
            self._entry_n = int(state.get("entry_n", 0))
            self._hold_n = int(state.get("hold_n", 0))
            self._strategy_returns = {
                k: [float(v) for v in vals]
                for k, vals in state.get("strategy_returns", {}).items()
            }
            self._regime_returns = {
                k: [float(v) for v in vals]
                for k, vals in state.get("regime_returns", {}).items()
            }
            logger.info(
                "[BRAIN] Loaded state: entry_n=%d hold_n=%d entry_w=%s hold_w=%s",
                self._entry_n, self._hold_n,
                self.entry_weights, self.hold_weights,
            )
        except Exception as exc:
            logger.warning("[BRAIN] Load state failed, starting fresh: %s", exc)
