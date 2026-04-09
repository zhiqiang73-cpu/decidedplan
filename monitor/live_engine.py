"""
实时特征引擎 (Live Feature Engine)

职责:
  1. 启动时从 Parquet 存储热加载最近 N 天历史数据（填满滚动窗口）
  2. 维护固定长度 deque，每条 1m 闭合 K 线 append 一行
  3. update(bar) 追加新 K 线后，对 deque 重算所有特征，返回最新特征行
  4. 维护辅助数据缓存（funding_rate / open_interest / long_short_ratio）
     供外部 REST 轮询更新，forward-fill 进 DataFrame

设计考量:
  - 窗口保留 3000 根 K 线（约 2 天），足以覆盖 24h 滚动窗口 (1440 bars)
  - 每分钟重算特征耗时 < 100 ms（pandas rolling 在 3000 行上极快）
  - 辅助数据缺失时特征列为 NaN，检测器已做 NaN 兼容处理
"""

import logging
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from core.feature_engine import FeatureEngine
from core.dimensions.time_features import compute_time_features
from core.dimensions.price_features import compute_price_features
from core.dimensions.trade_flow_features import compute_trade_flow_features
from core.dimensions.liquidity_features import compute_liquidity_features
from core.dimensions.positioning_features import compute_positioning_features
from core.dimensions.cross_market_features import compute_cross_market_features
from core.dimensions.liquidation_features import compute_liquidation_features
from core.dimensions.microstructure_features import compute_microstructure_features
from core.dimensions.order_flow_features import compute_order_flow_features
from core.dimensions.mark_price_features import compute_mark_price_features

logger = logging.getLogger(__name__)

# 滚动窗口大小：24h = 1440 bars，保留 2x 作为安全边距
WINDOW_BARS = 3000

# Binance kline WebSocket 字段 → 内部列名映射
_KLINE_FIELD_MAP = {
    "t": "timestamp",
    "o": "open",
    "h": "high",
    "l": "low",
    "c": "close",
    "v": "volume",
    "q": "quote_volume",
    "n": "trades",
    "V": "taker_buy_base",
    "Q": "taker_buy_quote",
}

# 数值列（需要转 float）
_NUMERIC_COLS = [
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "taker_buy_base",
    "taker_buy_quote",
]


class LiveFeatureEngine:
    """
    实时滚动特征引擎。

    Args:
        storage_path: Parquet 存储根目录
        warmup_days:  热启动天数（从历史数据加载填满窗口）
    """

    def __init__(
        self,
        storage_path: str = "data/storage",
        warmup_days: int = 3,
    ):
        self.storage_path = storage_path
        self.warmup_days = warmup_days
        self._feature_engine = FeatureEngine(storage_path=self.storage_path)

        # 原始 K 线 deque（每个元素是一行字典，含 kline 原始列）
        self._bars: deque = deque(maxlen=WINDOW_BARS)

        # 辅助数据缓存（最新值，forward-fill 用）
        self._side_cache: dict = {
            "funding_rate": np.nan,
            "open_interest": np.nan,
            "long_short_ratio": np.nan,
            "long_account": np.nan,
            "short_account": np.nan,
        }
        self._side_cache_ts: dict[str, float] = {}

        # 最新特征 DataFrame（完整窗口，每次 update 后刷新）
        self._df: Optional[pd.DataFrame] = None

    @property
    def supports_external_stream_features(self) -> bool:
        """Whether the live engine enriches bars with parquet-backed microstructure streams."""
        return True

    # ── 热启动 ─────────────────────────────────────────────────────────────
    def warmup(self) -> None:
        """
        从 Parquet 加载最近 warmup_days 天历史数据，填满滚动窗口。
        必须在开始接收 WebSocket 数据前调用。
        """
        now = datetime.now(timezone.utc)
        start = now - timedelta(days=self.warmup_days + 1)
        start_str = start.strftime("%Y-%m-%d")
        end_str = now.strftime("%Y-%m-%d")

        logger.info(f"Warmup: loading {start_str} ~ {end_str} historical data...")

        try:
            df = self._feature_engine.load_date_range(start_str, end_str)
        except Exception as exc:
            logger.warning(f"Warmup failed: {exc}, starting with empty window")
            return

        if df.empty:
            logger.warning("Warmup: empty historical data, starting with empty window")
            return

        # 取最近 WINDOW_BARS 行
        df = df.tail(WINDOW_BARS).reset_index(drop=True)

        # 将历史数据行写入 deque（只保留 kline 原始列）
        kline_cols = [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "trades",
            "taker_buy_base",
            "taker_buy_quote",
        ]
        # 也保存辅助列（如果存在）
        side_cols = [
            "funding_rate",
            "open_interest",
            "long_short_ratio",
            "long_account",
            "short_account",
        ]

        for _, row in df.iterrows():
            bar = {c: row[c] for c in kline_cols if c in df.columns}
            for sc in side_cols:
                if sc in df.columns:
                    bar[sc] = row[sc]
            self._bars.append(bar)

        # 用最新辅助数据更新缓存
        last_row = df.iloc[-1]
        for key in self._side_cache:
            if key in df.columns and not pd.isna(last_row.get(key, np.nan)):
                self._side_cache[key] = float(last_row[key])

        # 初次计算特征
        self._df = self._recompute_features()

        # ── 数据覆盖范围警告 ─────────────────────────────────────────────
        # POSITIONING 特征（oi_change_rate_5m/1h、ls_ratio_change_5m）依赖
        # open_interest 和 long_short_ratio，这两个数据源历史覆盖仅约 26 天。
        # 任何涉及 POSITIONING 维度的信号或物理确认，均不能声称经过长样本验证。
        if self._df is not None:
            oi_nan_rate = (
                self._df["oi_change_rate_5m"].isna().mean()
                if "oi_change_rate_5m" in self._df.columns
                else 1.0
            )
            if oi_nan_rate > 0.5:
                logger.warning(
                    f"[DATA COVERAGE] oi_change_rate_5m NaN rate={oi_nan_rate:.0%}, "
                    f"POSITIONING features lack historical data (OI/LSR covers ~26 days only). "
                    f"OI conditions in physical confirmation layer are invalid for historical validation claims."
                )

        logger.info(
            f"Warmup complete: window {len(self._bars)} bars, "
            f"feature columns {len(self._df.columns) if self._df is not None else 0}"
        )

    # ── 接收新 K 线 ────────────────────────────────────────────────────────
    def update(self, kline: dict) -> pd.DataFrame:
        """
        处理一根新的已闭合 K 线，重新计算特征，返回最新特征 DataFrame。

        Args:
            kline: Binance WebSocket kline 字段字典（k.x == True 时调用）
                   字段名使用 Binance 原始缩写（t/o/h/l/c/v/q/n/V/Q）

        Returns:
            最新完整特征 DataFrame（包含所有滚动窗口特征）
        """
        bar = self._parse_kline(kline)

        # 将辅助数据缓存注入 bar
        now_ts = time.time()
        for key, val in self._side_cache.items():
            age_s = now_ts - self._side_cache_ts.get(key, 0.0)
            if age_s > 600:
                logger.warning(
                    "[LiveEngine] side data stale: %s age=%.0fs > 600s, setting NaN",
                    key,
                    age_s,
                )
                bar[key] = np.nan
                continue
            bar[key] = val

        self._bars.append(bar)
        self._df = self._recompute_features()
        return self._df

    # ── 更新辅助数据缓存 ───────────────────────────────────────────────────
    def update_side_data(
        self,
        funding_rate: Optional[float] = None,
        open_interest: Optional[float] = None,
        long_short_ratio: Optional[float] = None,
        long_account: Optional[float] = None,
        short_account: Optional[float] = None,
    ) -> None:
        """
        更新辅助数据缓存（由 REST 轮询协程定期调用）。
        下次 update(kline) 时会将最新值注入新 K 线行。
        """
        updates = {
            "funding_rate": funding_rate,
            "open_interest": open_interest,
            "long_short_ratio": long_short_ratio,
            "long_account": long_account,
            "short_account": short_account,
        }
        for key, val in updates.items():
            if val is not None:
                self._side_cache[key] = float(val)
                self._side_cache_ts[key] = time.time()

    @property
    def oi_ready(self) -> bool:
        """True once live OI data has accumulated enough for oi_change_rate_5m to be valid."""
        if self._df is None or "oi_change_rate_5m" not in self._df.columns:
            return False
        recent = self._df["oi_change_rate_5m"].iloc[-10:]
        return int(recent.notna().sum()) >= 5

    # ── 获取最新特征 DataFrame ─────────────────────────────────────────────
    @property
    def df(self) -> Optional[pd.DataFrame]:
        """最新完整特征 DataFrame（warmup/update 后可用）。"""
        return self._df

    def get_latest(self, n: int = 500) -> Optional[pd.DataFrame]:
        """
        返回最新 n 行特征 DataFrame，供 Phase 1 检测器使用。
        如果窗口不足 n 行，返回全部。
        """
        if self._df is None or self._df.empty:
            return None
        return self._df.tail(n).reset_index(drop=True)

    # ── 内部：解析 WebSocket kline 字典 ───────────────────────────────────
    @staticmethod
    def _parse_kline(kline: dict) -> dict:
        """将 Binance WebSocket kline 字段转为内部列名字典。"""
        bar = {}
        for src, dst in _KLINE_FIELD_MAP.items():
            val = kline.get(src)
            if dst in _NUMERIC_COLS:
                bar[dst] = float(val) if val is not None else np.nan
            elif dst == "timestamp":
                bar[dst] = int(val) if val is not None else 0
            else:
                bar[dst] = int(val) if val is not None else 0
        return bar

    # ── 内部：从 deque 重算特征 ────────────────────────────────────────────
    def _recompute_features(self) -> pd.DataFrame:
        """
        将当前 deque 转换为 DataFrame 并重新计算所有特征维度。
        """
        if not self._bars:
            return pd.DataFrame()

        df = pd.DataFrame(list(self._bars))
        df = df.sort_values("timestamp").reset_index(drop=True)

        # 确保 timestamp 为 int64
        df["timestamp"] = df["timestamp"].astype("int64")

        df = self._merge_external_streams(df)

        # 调用各维度特征计算器（顺序与 FeatureEngine 一致）
        try:
            df = compute_time_features(df)
            df = compute_price_features(df)
            df = compute_trade_flow_features(df)
            df = compute_liquidity_features(df)
            df = compute_positioning_features(df)
            df = compute_cross_market_features(df)
            df = compute_liquidation_features(df)
            df = compute_microstructure_features(df)
            df = compute_order_flow_features(df)
            df = compute_mark_price_features(df)
            if "oi_change_rate_5m" in df.columns:
                df["oi_change_rate_5m_ma5"] = df["oi_change_rate_5m"].rolling(5, min_periods=1).mean()
        except Exception as exc:
            logger.warning(f"Feature calculation error: {exc}")

        return df

    def _merge_external_streams(self, df: pd.DataFrame) -> pd.DataFrame:
        """Keep live feature parity with FeatureEngine by merging parquet-backed microstructure streams."""
        if df.empty or "timestamp" not in df.columns:
            return df

        start_ts = int(df["timestamp"].iloc[0])
        end_ts = int(df["timestamp"].iloc[-1])

        try:
            df = self._feature_engine._merge_liquidation_data(df, start_ts, end_ts)
            df = self._feature_engine._merge_book_ticker_data(df, start_ts, end_ts)
            df = self._feature_engine._merge_agg_trades_data(df, start_ts, end_ts)
            df = self._feature_engine._merge_mark_price_data(df, start_ts, end_ts)
        except Exception as exc:
            logger.warning(f"External stream merge error: {exc}")

        return df
