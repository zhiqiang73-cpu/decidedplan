"""Data processor for Binance downloader responses."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .exceptions import DataProcessingError, ValidationError

logger = logging.getLogger(__name__)


SCHEMAS = {
    "klines": {
        "timestamp": "int64",
        "open": "float64",
        "high": "float64",
        "low": "float64",
        "close": "float64",
        "volume": "float64",
        "close_time": "int64",
        "quote_volume": "float64",
        "trades": "int32",
        "taker_buy_base": "float64",
        "taker_buy_quote": "float64",
    },
    "agg_trades": {
        "agg_trade_id": "int64",
        "price": "float64",
        "quantity": "float64",
        "first_trade_id": "int64",
        "last_trade_id": "int64",
        "timestamp": "int64",
        "is_buyer_maker": "bool",
    },
    "funding_rate": {
        "timestamp": "int64",
        "funding_rate": "float64",
        "mark_price": "float64",
    },
    "open_interest": {
        "timestamp": "int64",
        "open_interest": "float64",
    },
    "long_short_ratio": {
        "timestamp": "int64",
        "long_short_ratio": "float64",
        "long_account": "float64",
        "short_account": "float64",
    },
    "taker_ratio": {
        "timestamp": "int64",
        "taker_buy_sell_ratio": "float64",
        "buy_volume": "float64",
        "sell_volume": "float64",
    },
}


class DataProcessor:
    """Convert API responses into DataFrames and Parquet files."""

    def __init__(
        self,
        storage_path: str,
        compression: str = "snappy",
        row_group_size: int = 100000,
    ) -> None:
        self.storage_path = Path(storage_path)
        self.compression = compression
        self.row_group_size = row_group_size
        self.storage_path.mkdir(parents=True, exist_ok=True)

    def process_klines(self, data: List[List[Any]], symbol: str = "BTCUSDT") -> pd.DataFrame:
        """Process kline payloads from Binance."""
        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(
            data,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "close_time",
                "quote_volume",
                "trades",
                "taker_buy_base",
                "taker_buy_quote",
                "ignore",
            ],
        )

        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce").astype("Int64")
        df["close_time"] = pd.to_numeric(df["close_time"], errors="coerce").astype("Int64")
        for col in [
            "open",
            "high",
            "low",
            "close",
            "volume",
            "quote_volume",
            "taker_buy_base",
            "taker_buy_quote",
        ]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["trades"] = pd.to_numeric(df["trades"], errors="coerce").astype("Int32")

        return df.drop(columns=["ignore"])

    def process_agg_trades(self, data: List[List[Any]]) -> pd.DataFrame:
        """Process aggregate trades payloads."""
        if not data:
            return pd.DataFrame()

        if isinstance(data[0], dict):
            df = pd.DataFrame(data).rename(
                columns={
                    "a": "agg_trade_id",
                    "p": "price",
                    "q": "quantity",
                    "f": "first_trade_id",
                    "l": "last_trade_id",
                    "T": "timestamp",
                    "m": "is_buyer_maker",
                }
            )
        else:
            df = pd.DataFrame(
                data,
                columns=[
                    "agg_trade_id",
                    "price",
                    "quantity",
                    "first_trade_id",
                    "last_trade_id",
                    "timestamp",
                    "is_buyer_maker",
                ],
            )

        required = {
            "agg_trade_id",
            "price",
            "quantity",
            "first_trade_id",
            "last_trade_id",
            "timestamp",
            "is_buyer_maker",
        }
        missing = required - set(df.columns)
        if missing:
            raise DataProcessingError(f"agg_trades 缺少字段: {missing}")

        df = df[
            [
                "agg_trade_id",
                "price",
                "quantity",
                "first_trade_id",
                "last_trade_id",
                "timestamp",
                "is_buyer_maker",
            ]
        ]
        for col in ["agg_trade_id", "first_trade_id", "last_trade_id", "timestamp"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
        for col in ["price", "quantity"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["is_buyer_maker"] = df["is_buyer_maker"].astype(bool)

        return df.drop_duplicates(subset=["agg_trade_id"], keep="first")

    def process_funding_rate(self, data: List[List[Any]]) -> pd.DataFrame:
        """Process funding rate payloads."""
        if not data:
            return pd.DataFrame()

        if isinstance(data[0], dict):
            df = pd.DataFrame(data).rename(
                columns={
                    "fundingTime": "timestamp",
                    "fundingRate": "funding_rate",
                    "markPrice": "mark_price",
                }
            )
        else:
            df = pd.DataFrame(data, columns=["timestamp", "funding_rate", "mark_price", "ignore"])
            df = df.drop(columns=["ignore"])

        required = {"timestamp", "funding_rate", "mark_price"}
        missing = required - set(df.columns)
        if missing:
            raise DataProcessingError(f"funding_rate 缺少字段: {missing}")

        df = df[["timestamp", "funding_rate", "mark_price"]]
        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce").astype("Int64")
        df["funding_rate"] = pd.to_numeric(df["funding_rate"], errors="coerce")
        df["mark_price"] = pd.to_numeric(df["mark_price"], errors="coerce")
        return df

    def process_open_interest(self, data: List[List[Any]]) -> pd.DataFrame:
        """Process open interest payloads."""
        if not data:
            return pd.DataFrame()

        if isinstance(data[0], dict):
            df = pd.DataFrame(data).rename(columns={"sumOpenInterest": "open_interest"})
        else:
            df = pd.DataFrame(data, columns=["timestamp", "open_interest", "ignore"])
            df = df.drop(columns=["ignore"])

        required = {"timestamp", "open_interest"}
        missing = required - set(df.columns)
        if missing:
            raise DataProcessingError(f"open_interest 缺少字段: {missing}")

        df = df[["timestamp", "open_interest"]]
        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce").astype("Int64")
        df["open_interest"] = pd.to_numeric(df["open_interest"], errors="coerce")
        return df

    def process_long_short_ratio(self, data: List[List[Any]]) -> pd.DataFrame:
        """Process long/short ratio payloads."""
        if not data:
            return pd.DataFrame()

        if isinstance(data[0], dict):
            df = pd.DataFrame(data).rename(
                columns={
                    "longShortRatio": "long_short_ratio",
                    "longAccount": "long_account",
                    "shortAccount": "short_account",
                }
            )
        else:
            df = pd.DataFrame(
                data,
                columns=["timestamp", "long_short_ratio", "long_account", "short_account", "ignore"],
            )
            df = df.drop(columns=["ignore"])

        required = {"timestamp", "long_short_ratio", "long_account", "short_account"}
        missing = required - set(df.columns)
        if missing:
            raise DataProcessingError(f"long_short_ratio 缺少字段: {missing}")

        df = df[["timestamp", "long_short_ratio", "long_account", "short_account"]]
        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce").astype("Int64")
        for col in ["long_short_ratio", "long_account", "short_account"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def process_taker_ratio(self, data: List[List[Any]]) -> pd.DataFrame:
        """Process taker buy/sell ratio payloads."""
        if not data:
            return pd.DataFrame()

        if isinstance(data[0], dict):
            df = pd.DataFrame(data).rename(
                columns={
                    "buySellRatio": "taker_buy_sell_ratio",
                    "buyVol": "buy_volume",
                    "sellVol": "sell_volume",
                }
            )
        else:
            df = pd.DataFrame(
                data,
                columns=["timestamp", "taker_buy_sell_ratio", "buy_volume", "sell_volume", "ignore"],
            )
            df = df.drop(columns=["ignore"])

        required = {"timestamp", "taker_buy_sell_ratio", "buy_volume", "sell_volume"}
        missing = required - set(df.columns)
        if missing:
            raise DataProcessingError(f"taker_ratio 缺少字段: {missing}")

        df = df[["timestamp", "taker_buy_sell_ratio", "buy_volume", "sell_volume"]]
        df["timestamp"] = pd.to_numeric(df["timestamp"], errors="coerce").astype("Int64")
        for col in ["taker_buy_sell_ratio", "buy_volume", "sell_volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df

    def validate_dataframe(self, df: pd.DataFrame, endpoint_name: str) -> None:
        """Validate a processed DataFrame before writing."""
        if df.empty:
            logger.warning(f"{endpoint_name}: DataFrame为空")
            return

        required_columns = SCHEMAS.get(endpoint_name, {})
        missing_cols = set(required_columns.keys()) - set(df.columns)
        if missing_cols:
            raise ValidationError(f"缺少必填字段: {missing_cols}")

        if "timestamp" in df.columns:
            if df["timestamp"].isna().any():
                raise ValidationError("时间戳列包含空值")
            if not df["timestamp"].is_monotonic_increasing:
                logger.warning(f"{endpoint_name}: 时间戳非单调递增，将排序")

        if "price" in df.columns and (df["price"] <= 0).any():
            raise ValidationError("价格必须大于 0")
        if "close" in df.columns and (df["close"] <= 0).any():
            raise ValidationError("收盘价必须大于 0")
        if "volume" in df.columns and (df["volume"] < 0).any():
            raise ValidationError("成交量不能为负")

        for col in df.columns:
            null_count = int(df[col].isna().sum())
            if null_count > 0:
                logger.warning(f"{endpoint_name}: {col} 列有 {null_count} 个空值")

    def get_partition_path(self, endpoint_name: str, timestamp_ms: int) -> Path:
        """Build the Hive-style day partition path. 使用 UTC 时间，与 ws_collector 保持一致。"""
        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        return (
            self.storage_path
            / endpoint_name
            / f"year={dt.year:04d}"
            / f"month={dt.month:02d}"
            / f"day={dt.day:02d}"
        )

    def write_parquet(self, df: pd.DataFrame, endpoint_name: str, timestamp_ms: int) -> None:
        """Write a standalone parquet file into a day partition."""
        if df.empty:
            logger.warning(f"{endpoint_name}: DataFrame为空，跳过写入")
            return

        self.validate_dataframe(df, endpoint_name)
        partition_path = self.get_partition_path(endpoint_name, timestamp_ms)
        partition_path.mkdir(parents=True, exist_ok=True)

        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        file_path = partition_path / f"part-{dt.strftime('%Y%m%d-%H%M%S')}.parquet"
        table = pa.Table.from_pandas(df, preserve_index=False)
        # 原子写：先写 .tmp 再 os.replace，防止中途 kill 导致文件损坏
        tmp_path = str(file_path) + ".tmp"
        pq.write_table(table, tmp_path, compression=self.compression, row_group_size=self.row_group_size)
        os.replace(tmp_path, file_path)
        logger.info(
            f"{endpoint_name}: 已写入 {len(df):,} 条记录到 {file_path.relative_to(self.storage_path)}"
        )

    def append_to_daily_parquet(self, df: pd.DataFrame, endpoint_name: str, timestamp_ms: int) -> None:
        """Append records into a daily parquet file, deduplicating by agg_trade_id when present."""
        if df.empty:
            return

        self.validate_dataframe(df, endpoint_name)
        partition_path = self.get_partition_path(endpoint_name, timestamp_ms)
        partition_path.mkdir(parents=True, exist_ok=True)

        dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
        file_path = partition_path / f"{dt.strftime('%Y%m%d')}.parquet"

        new_df = df.copy()
        if "timestamp" in new_df.columns:
            new_df = new_df.sort_values("timestamp").reset_index(drop=True)
        new_table = pa.Table.from_pandas(new_df, preserve_index=False)

        if file_path.exists():
            try:
                existing_table = pq.ParquetFile(file_path).read(columns=new_table.column_names)
                combined_df = pd.concat(
                    [existing_table.to_pandas(), new_df],
                    ignore_index=True,
                )
                if "agg_trade_id" in combined_df.columns:
                    combined_df = combined_df.drop_duplicates(subset=["agg_trade_id"], keep="first")
                if "timestamp" in combined_df.columns:
                    combined_df = combined_df.sort_values("timestamp").reset_index(drop=True)
                combined_table = pa.Table.from_pandas(combined_df, preserve_index=False)
            except Exception as exc:
                logger.warning(f"读取现有文件失败，将覆盖: {exc}")
                combined_table = new_table
        else:
            combined_table = new_table

        # 原子写：防止进程中途被 kill 导致文件损坏
        tmp_path = str(file_path) + ".tmp"
        pq.write_table(combined_table, tmp_path, compression=self.compression, row_group_size=self.row_group_size)
        os.replace(tmp_path, file_path)
        logger.info(
            f"{endpoint_name}: 已追加 {len(df):,} 条记录到 {file_path.relative_to(self.storage_path)} "
            f"(总计: {combined_table.num_rows:,})"
        )
