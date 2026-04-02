"""
检查点管理器
用于跟踪下载进度，支持断点续传
"""

import json
import os
import tempfile
from datetime import datetime
from typing import Dict, List, Optional
from pathlib import Path
import logging

from .exceptions import CheckpointError

logger = logging.getLogger(__name__)


class CheckpointManager:
    """
    检查点管理器

    功能：
    - 跟踪每个端点已完成的时间范围
    - 原子写入，避免损坏
    - 启动时加载，计算剩余范围
    - 验证Parquet文件存在性
    """

    def __init__(
        self,
        checkpoint_path: str,
        storage_path: str,
        auto_save: bool = True
    ):
        """
        初始化检查点管理器

        Args:
            checkpoint_path: 检查点文件路径
            storage_path: 数据存储路径
            auto_save: 是否自动保存
        """
        self.checkpoint_path = Path(checkpoint_path)
        self.storage_path = Path(storage_path)
        self.auto_save = auto_save

        # 确保目录存在
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)

        # 检查点数据结构
        self.data: dict = {
            "version": "1.0",
            "last_updated": None,
            "endpoints": {}
        }

        # 加载已有检查点
        if self.checkpoint_path.exists():
            self.load()

    def load(self):
        """从文件加载检查点"""
        try:
            with open(self.checkpoint_path, 'r', encoding='utf-8') as f:
                self.data = json.load(f)
            logger.info(f"已加载检查点: {self.checkpoint_path}")
            logger.info(f"  最后更新: {self.data.get('last_updated')}")
            logger.info(f"  端点数: {len(self.data.get('endpoints', {}))}")
        except json.JSONDecodeError as e:
            logger.error(f"检查点文件损坏，将重新创建: {e}")
            self.data = {
                "version": "1.0",
                "last_updated": None,
                "endpoints": {}
            }
        except Exception as e:
            raise CheckpointError(f"加载检查点失败: {e}")

    def save(self):
        """保存检查点到文件（原子写入）"""
        self.data["last_updated"] = datetime.utcnow().isoformat() + "Z"

        # 原子写入：先写临时文件，再重命名
        try:
            # 创建临时文件
            fd, temp_path = tempfile.mkstemp(
                dir=self.checkpoint_path.parent,
                prefix=f".{self.checkpoint_path.name}.",
                suffix=".tmp"
            )
            os.close(fd)

            # 写入数据
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False)

            # 原子重命名
            os.replace(temp_path, self.checkpoint_path)

            logger.debug(f"检查点已保存: {self.checkpoint_path}")

        except Exception as e:
            # 清理临时文件
            if 'temp_path' in locals() and os.path.exists(temp_path):
                os.remove(temp_path)
            raise CheckpointError(f"保存检查点失败: {e}")

    def get_endpoint_state(self, endpoint_name: str) -> dict:
        """
        获取端点状态

        Args:
            endpoint_name: 端点名称

        Returns:
            端点状态字典，如果不存在则返回默认值
        """
        if endpoint_name not in self.data["endpoints"]:
            return {
                "symbol": None,
                "completed_ranges": [],
                "total_records": 0,
                "last_ts": None
            }
        return self.data["endpoints"][endpoint_name]

    def add_completed_range(
        self,
        endpoint_name: str,
        start_ts: int,
        end_ts: int,
        record_count: int,
        symbol: str = "BTCUSDT"
    ):
        """
        添加已完成的时间范围

        Args:
            endpoint_name: 端点名称
            start_ts: 开始时间戳（毫秒）
            end_ts: 结束时间戳（毫秒）
            record_count: 记录数
            symbol: 交易对
        """
        if endpoint_name not in self.data["endpoints"]:
            self.data["endpoints"][endpoint_name] = {
                "symbol": symbol,
                "completed_ranges": [],
                "total_records": 0,
                "last_ts": None
            }

        endpoint = self.data["endpoints"][endpoint_name]

        # 添加范围（如果不存在）
        range_entry = {
            "start_ts": start_ts,
            "end_ts": end_ts,
            "record_count": record_count,
        }
        is_existing = any(
            existing.get("start_ts") == start_ts and
            existing.get("end_ts") == end_ts
            for existing in endpoint["completed_ranges"]
        )

        if not is_existing:
            endpoint["completed_ranges"].append(range_entry)
            endpoint["total_records"] += record_count
            endpoint["last_ts"] = max(endpoint["last_ts"] or 0, end_ts)

            # 按开始时间排序
            endpoint["completed_ranges"].sort(key=lambda x: x["start_ts"])

            logger.info(
                f"{endpoint_name}: 完成范围 "
                f"{datetime.fromtimestamp(start_ts/1000).strftime('%Y-%m-%d %H:%M')} - "
                f"{datetime.fromtimestamp(end_ts/1000).strftime('%Y-%m-%d %H:%M')} "
                f"({record_count:,} 条记录)"
            )

            if self.auto_save:
                self.save()

    def remove_completed_range(
        self,
        endpoint_name: str,
        start_ts: int,
        end_ts: int
    ) -> bool:
        """
        移除已完成的时间范围

        Args:
            endpoint_name: 端点名称
            start_ts: 开始时间戳（毫秒）
            end_ts: 结束时间戳（毫秒）

        Returns:
            是否实际移除了范围
        """
        endpoint = self.data["endpoints"].get(endpoint_name)
        if not endpoint:
            return False

        original_ranges = endpoint["completed_ranges"]
        filtered_ranges = [
            range_entry for range_entry in original_ranges
            if not (
                range_entry.get("start_ts") == start_ts and
                range_entry.get("end_ts") == end_ts
            )
        ]

        if len(filtered_ranges) == len(original_ranges):
            return False

        endpoint["completed_ranges"] = filtered_ranges
        endpoint["last_ts"] = max(
            (range_entry.get("end_ts", 0) for range_entry in filtered_ranges),
            default=None
        )
        endpoint["total_records"] = sum(
            int(range_entry.get("record_count", 0))
            for range_entry in filtered_ranges
        )

        logger.warning(
            f"{endpoint_name}: 已移除无效范围 "
            f"{datetime.fromtimestamp(start_ts/1000).strftime('%Y-%m-%d %H:%M')} - "
            f"{datetime.fromtimestamp(end_ts/1000).strftime('%Y-%m-%d %H:%M')}"
        )

        if self.auto_save:
            self.save()

        return True

    def get_completed_timestamps(self, endpoint_name: str) -> List[int]:
        """
        获取已完成的完成时间戳列表

        Args:
            endpoint_name: 端点名称

        Returns:
            已完成的时间戳列表
        """
        state = self.get_endpoint_state(endpoint_name)
        return [r["end_ts"] for r in state["completed_ranges"]]

    def get_last_timestamp(self, endpoint_name: str) -> Optional[int]:
        """
        获取最后完成的时间戳

        Args:
            endpoint_name: 端点名称

        Returns:
            最后完成的时间戳，如果没有则返回None
        """
        state = self.get_endpoint_state(endpoint_name)
        return state.get("last_ts")

    def is_range_completed(
        self,
        endpoint_name: str,
        start_ts: int,
        end_ts: int
    ) -> bool:
        """
        检查时间范围是否已完成

        Args:
            endpoint_name: 端点名称
            start_ts: 开始时间戳
            end_ts: 结束时间戳

        Returns:
            是否已完成
        """
        state = self.get_endpoint_state(endpoint_name)

        for completed in state["completed_ranges"]:
            if completed["start_ts"] == start_ts and completed["end_ts"] == end_ts:
                # 验证Parquet文件是否存在
                return self._verify_parquet_exists(endpoint_name, start_ts)

        return False

    def _verify_parquet_exists(self, endpoint_name: str, timestamp: int) -> bool:
        """
        验证Parquet文件是否存在

        Args:
            endpoint_name: 端点名称
            timestamp: 时间戳

        Returns:
            文件是否存在
        """
        dt = datetime.fromtimestamp(timestamp / 1000)

        # 构建Parquet路径
        parquet_path = self.storage_path / endpoint_name / (
            f"year={dt.year:04d}/month={dt.month:02d}/day={dt.day:02d}"
        )

        # 检查目录或文件是否存在
        exists = parquet_path.exists()
        if not exists:
            logger.warning(
                f"检查点记录完成但Parquet文件不存在: {parquet_path}"
            )

        return exists

    def calculate_remaining_ranges(
        self,
        endpoint_name: str,
        start_ts: int,
        end_ts: int,
        interval_ms: int
    ) -> List[tuple]:
        """
        计算剩余待下载的时间范围

        Args:
            endpoint_name: 端点名称
            start_ts: 开始时间戳
            end_ts: 结束时间戳
            interval_ms: 时间间隔（毫秒）

        Returns:
            待下载的时间范围列表 [(start_ts, end_ts), ...]
        """
        completed = set(self.get_completed_timestamps(endpoint_name))
        remaining = []

        current = start_ts
        while current < end_ts:
            next_ts = min(current + interval_ms, end_ts)

            if next_ts not in completed:
                remaining.append((current, next_ts))

            current = next_ts

        if remaining:
            logger.info(
                f"{endpoint_name}: 剩余 {len(remaining)} 个时间范围待下载"
            )
        else:
            logger.info(f"{endpoint_name}: 所有数据已下载完成")

        return remaining

    def get_summary(self) -> dict:
        """获取汇总信息"""
        summary = {
            "version": self.data["version"],
            "last_updated": self.data.get("last_updated"),
            "endpoints": {}
        }

        for endpoint_name, state in self.data.get("endpoints", {}).items():
            summary["endpoints"][endpoint_name] = {
                "symbol": state.get("symbol"),
                "completed_ranges": len(state.get("completed_ranges", [])),
                "total_records": state.get("total_records", 0),
                "last_ts": state.get("last_ts")
            }

        return summary

    def print_summary(self):
        """打印汇总信息"""
        summary = self.get_summary()

        print("\n" + "=" * 60)
        print("检查点汇总")
        print("=" * 60)
        print(f"版本: {summary['version']}")
        print(f"最后更新: {summary['last_updated']}")
        print("\n端点状态:")
        print("-" * 60)

        for endpoint, state in summary['endpoints'].items():
            last_ts = state.get('last_ts')
            last_time = (
                datetime.fromtimestamp(last_ts / 1000).strftime('%Y-%m-%d %H:%M')
                if last_ts else "N/A"
            )

            print(f"\n{endpoint}:")
            print(f"  交易对: {state['symbol']}")
            print(f"  已完成范围: {state['completed_ranges']}")
            print(f"  总记录数: {state['total_records']:,}")
            print(f"  最后时间: {last_time}")

        print("\n" + "=" * 60 + "\n")

    def validate_and_repair(self):
        """
        验证检查点并修复

        检查所有标记为完成的时间范围，如果Parquet文件不存在，
        则从检查点中移除该范围。
        """
        print("\n验证检查点完整性...")

        modified = False
        for endpoint_name, state in self.data.get("endpoints", {}).items():
            for range_entry in list(state["completed_ranges"]):
                range_start = range_entry.get("start_ts")
                range_end = range_entry.get("end_ts")

                if range_start is None or range_end is None:
                    continue

                if not self._verify_parquet_exists(endpoint_name, range_start):
                    if self.remove_completed_range(
                        endpoint_name,
                        range_start,
                        range_end
                    ):
                        modified = True

        if modified:
            self.save()
            print("检查点已修复")
        else:
            print("检查点完整，无需修复")
