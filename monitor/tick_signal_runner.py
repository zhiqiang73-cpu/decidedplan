"""
Tick 信号运行器。

每 30 秒调用一次，读取最近 agg_trades Parquet 数据，
聚合为 30s bar，检测 T1 系列信号，输出标准信号 dict。

集成方式: run_monitor.py 在主循环中每30秒调用 TickSignalRunner.run()，
输出的信号 dict 直接传给 ExecutionEngine.on_signal()。

注意：此模块读磁盘，线程安全，但不适合高频调用（>=10次/秒）。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from signals.tick_h1_signal import ALL_T1_DETECTORS

logger = logging.getLogger(__name__)

# ── 聚合参数 ────────────────────────────────────────────────────────────────
_WINDOW_MS = 30_000           # 30秒 bar
_LOOKBACK_MINUTES = 60        # 读取最近60分钟数据 (120根30s bar)
_ROLL_BARS = 20               # trade_count 均值窗口 (20根 = 10分钟)
_AGG_TRADE_COLS = ["timestamp", "price", "quantity", "is_buyer_maker"]
_COOLDOWN_SECS: dict[str, int] = {
    "T1-1": 300,    # 5分钟冷却 (宽松档，防止过度触发)
    "T1-2": 300,
    "T1-3": 180,    # 3分钟冷却 (严格档，信号少，冷却短)
}
_RAW_SCHEMA_FIELDS = frozenset(_AGG_TRADE_COLS)
_PREAGG_SCHEMA_FIELDS = frozenset(
    {
        "timestamp",
        "at_large_buy_ratio",
        "at_burst_index",
        "at_dir_net_1m",
        "trade_count",
        "buy_usd_1m",
        "sell_usd_1m",
    }
)
_STATUS_SCHEMA_RECHECK_S = 300.0


class TickSignalRunner:
    """读取 agg_trades -> 30s bar -> T1 检测 -> 返回信号列表。"""

    def __init__(self, storage_path: str | Path) -> None:
        self._storage = Path(storage_path)
        # Primary: raw_ticks/ written by ws_collector with raw tick schema
        # Fallback: agg_trades/ for historical data with raw tick columns
        self._raw_ticks_dir = self._storage / "raw_ticks"
        self._agg_trades_dir = self._storage / "agg_trades"
        self._last_signal_ts: dict[str, float] = {}  # family -> 上次触发 unix 时间
        self._last_schema_probe_ts = 0.0
        self._last_status_log_ts = 0.0
        self._status: dict[str, object] = {
            "mode": "init",
            "reason": "tick runner not started",
            "checked_at": None,
            "last_run_at": None,
            "last_signal_count": 0,
            "recent_raw_files": 0,
            "recent_preagg_files": 0,
        }

    def status_snapshot(self) -> dict[str, object]:
        return dict(self._status)

    def _set_status(
        self,
        mode: str,
        reason: str,
        *,
        recent_raw_files: int | None = None,
        recent_preagg_files: int | None = None,
        last_signal_count: int | None = None,
    ) -> None:
        self._status["mode"] = str(mode)
        self._status["reason"] = str(reason)
        self._status["checked_at"] = datetime.now(timezone.utc).isoformat()
        if recent_raw_files is not None:
            self._status["recent_raw_files"] = int(recent_raw_files)
        if recent_preagg_files is not None:
            self._status["recent_preagg_files"] = int(recent_preagg_files)
        if last_signal_count is not None:
            self._status["last_signal_count"] = int(last_signal_count)

    def _warn_status_once(self, message: str) -> None:
        now = time.time()
        if now - self._last_status_log_ts >= _STATUS_SCHEMA_RECHECK_S:
            logger.warning(message)
            self._last_status_log_ts = now

    # ── 主入口 ──────────────────────────────────────────────────────────────

    def run(self) -> list[dict]:
        """执行一次检测循环。返回触发的信号 dict 列表（可为空）。"""
        self._status["last_run_at"] = datetime.now(timezone.utc).isoformat()
        try:
            bars = self._load_recent_bars()
        except Exception as exc:
            self._set_status("error", f"tick data load failed: {exc}")
            logger.warning("[TickRunner] 数据加载失败: %s", exc)
            return []

        if bars is None or len(bars) < 5:
            self._status["last_signal_count"] = 0
            return []

        signals: list[dict] = []
        now = time.time()

        for detector in ALL_T1_DETECTORS:
            # 冷却检查
            last_ts = self._last_signal_ts.get(detector.family, 0.0)
            cooldown = _COOLDOWN_SECS.get(detector.family, 300)
            if now - last_ts < cooldown:
                continue

            if detector.check_live(bars):
                # 取最新完成bar的价格作为入场价参考
                entry_bar = bars.iloc[-2]
                close_price = float(entry_bar["close"])

                signal = self._build_signal(
                    detector.family,
                    price=close_price,
                    bar_ts=int(entry_bar.get("timestamp", now * 1000)),
                )
                signals.append(signal)
                self._last_signal_ts[detector.family] = now
                logger.info(
                    "[TickRunner] 信号触发: %s LONG @ %.2f",
                    detector.family, close_price,
                )

        self._set_status(
            "ready",
            "tick raw schema healthy",
            last_signal_count=len(signals),
        )
        return signals

    # ── 数据加载与聚合 ───────────────────────────────────────────────────────

    def _load_recent_bars(self) -> Optional[pd.DataFrame]:
        """读取最近 _LOOKBACK_MINUTES 分钟的 agg_trades 并聚合成 30s bar。"""
        now = time.time()
        if (
            self._status.get("mode") in {"no_files", "preaggregated_only", "no_raw_rows"}
            and now - self._last_schema_probe_ts < _STATUS_SCHEMA_RECHECK_S
        ):
            return None

        cutoff_ms = int((time.time() - _LOOKBACK_MINUTES * 60) * 1000)

        # 扫描最近2天的分区文件（避免跨日边界遗漏）
        files = self._find_recent_files(n_days=2)
        if not files:
            self._last_schema_probe_ts = now
            self._set_status("no_files", "recent agg_trades files not found")
            self._warn_status_once("[TickRunner] 未找到最近 agg_trades 文件，T1 tick 信号暂停")
            return None

        raw_files: list[Path] = []
        preagg_files = 0
        for fpath in files:
            try:
                schema_names = set(pq.ParquetFile(str(fpath)).schema_arrow.names)
            except Exception as exc:
                logger.debug("[TickRunner] schema probe failed %s: %s", fpath.name, exc)
                continue
            if _RAW_SCHEMA_FIELDS.issubset(schema_names):
                raw_files.append(fpath)
            elif _PREAGG_SCHEMA_FIELDS.issubset(schema_names):
                preagg_files += 1

        self._last_schema_probe_ts = now
        if not raw_files:
            mode = "preaggregated_only" if preagg_files else "no_raw_rows"
            reason = (
                "recent agg_trades only contain 1m pre-aggregated bars; T1 tick runner paused"
                if preagg_files
                else "recent agg_trades files do not contain raw tick schema"
            )
            self._set_status(
                mode,
                reason,
                recent_raw_files=0,
                recent_preagg_files=preagg_files,
            )
            self._warn_status_once(
                f"[TickRunner] {reason} (preagg_files={preagg_files}, recent_files={len(files)})"
            )
            return None

        frames: list[pd.DataFrame] = []
        for fpath in raw_files:
            try:
                tbl = pq.read_table(str(fpath), columns=_AGG_TRADE_COLS)
                df = tbl.to_pandas()
                df = df[df["timestamp"] >= cutoff_ms]
                if not df.empty:
                    frames.append(df)
            except Exception as exc:
                logger.debug("[TickRunner] 跳过文件 %s: %s", fpath.name, exc)

        if not frames:
            self._set_status(
                "no_raw_rows",
                "raw tick schema exists but no recent rows in lookback window",
                recent_raw_files=len(raw_files),
                recent_preagg_files=preagg_files,
            )
            return None

        raw = pd.concat(frames, ignore_index=True).sort_values("timestamp")
        self._set_status(
            "ready",
            "tick raw schema healthy",
            recent_raw_files=len(raw_files),
            recent_preagg_files=preagg_files,
        )
        return self._aggregate(raw)

    def _find_recent_files(self, n_days: int = 2) -> list[Path]:
        """返回最近 n_days 天的 parquet 文件列表（按时间排序）。

        优先从 raw_ticks/ 目录查找（ws_collector 实时写入的原始tick），
        若 raw_ticks/ 为空则回退到 agg_trades/（历史下载的原始数据）。
        """
        cutoff_date = (datetime.now(timezone.utc) - timedelta(days=max(n_days - 1, 0))).date()

        for search_dir in (self._raw_ticks_dir, self._agg_trades_dir):
            if not search_dir.exists():
                continue
            files: list[Path] = []
            for path in sorted(search_dir.glob("**/*.parquet")):
                try:
                    file_date = datetime.strptime(path.stem[:8], "%Y%m%d").date()
                except ValueError:
                    files.append(path)
                    continue
                if file_date >= cutoff_date:
                    files.append(path)
            if files:
                return files
        return []

    def _aggregate(self, raw: pd.DataFrame) -> pd.DataFrame:
        """聚合逐笔为 30s bar，计算 sell_share / buy_share / trade_count 等。"""
        if raw.empty:
            return pd.DataFrame()

        raw = raw.copy()
        raw["bucket"] = (raw["timestamp"] // _WINDOW_MS) * _WINDOW_MS
        raw["trade_usd"] = raw["price"] * raw["quantity"]
        raw["is_buy"] = ~raw["is_buyer_maker"]
        raw["buy_usd"] = np.where(raw["is_buy"], raw["trade_usd"], 0.0)
        raw["sell_usd"] = np.where(~raw["is_buy"], raw["trade_usd"], 0.0)

        bars = raw.groupby("bucket", sort=True).agg(
            timestamp=("bucket", "first"),
            open=("price", "first"),
            close=("price", "last"),
            notional=("trade_usd", "sum"),
            buy_usd=("buy_usd", "sum"),
            sell_usd=("sell_usd", "sum"),
            trade_count=("price", "count"),
        ).reset_index(drop=True)

        safe_notional = bars["notional"].clip(lower=1.0)
        bars["sell_share"] = bars["sell_usd"] / safe_notional
        bars["buy_share"] = bars["buy_usd"] / safe_notional

        # 滚动均值：使用过去 _ROLL_BARS 根 bar 的 trade_count（shift(1) 避免用当前bar）
        bars["trade_count_roll20"] = (
            bars["trade_count"].shift(1).rolling(_ROLL_BARS, min_periods=5).mean()
        )

        return bars.reset_index(drop=True)

    # ── 信号构造 ─────────────────────────────────────────────────────────────

    @staticmethod
    def _build_signal(family: str, price: float, bar_ts: int) -> dict:
        """构造与 ExecutionEngine.on_signal() 兼容的信号 dict。

        TODO(问题2): build_entry_snapshot() 需要完整的 1m bar 特征字典（SNAPSHOT_COLUMNS，34列），
        以便 vs_entry 出场逻辑在每根 K 线上计算相对变化量。当前调用方（run_monitor.py
        的 _tick_signal_loop）只传 {"close": price}，导致 34 个 snapshot 字段全为 None。
        修复方案：在 TickSignalRunner.__init__ 增加可选参数
            get_latest_features: Callable[[], dict] | None = None
        并在 run_monitor.py 初始化时传入 lambda: engine.latest_row().to_dict()，
        然后在调用 on_signal 时用返回的完整特征 dict（合并 close 覆盖）替换 minimal_features。
        该改动需要同时修改 run_monitor.py，超出本次修复范围，故暂不实施。
        """

        # 信号置信度: T1-3 最高 (HIGH=3)，T1-2 中等 (MEDIUM=2)，T1-1 低 (LOW=1 -> 实际用MEDIUM=2)
        confidence_map = {"T1-1": 2, "T1-2": 2, "T1-3": 3}
        confidence = confidence_map.get(family, 2)

        label_map = {
            "T1-1": "sell_burst_absorbed_wide",
            "T1-2": "sell_burst_absorbed",
            "T1-3": "sell_burst_absorbed_strict",
        }
        label = label_map.get(family, "sell_burst_absorbed")

        return {
            "name": f"{family}_{label}",
            "signal_name": f"{family}_{label}",  # 向后兼容别名，execution_engine 读 "name"
            "family": family,
            "direction": "long",
            "confidence": confidence,
            "price": price,
            "mechanism_type": "sell_burst_absorbed",
            "bar_timestamp_ms": bar_ts,
            "detected_at_ms": int(time.time() * 1000),
            "timeframe": "30s",
            "source": "tick_runner",
            # vs_entry 快照: tick信号用价格本身，不依赖1m特征
            "entry_snapshot": {
                "family": family,
                "direction": "long",
                "price": price,
                "mechanism_type": "sell_burst_absorbed",
            },
        }
