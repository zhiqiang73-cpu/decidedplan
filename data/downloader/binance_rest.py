"""
币安REST API下载器
主协调器，管理所有下载操作
"""

import asyncio
import aiohttp
import pandas as pd
import pyarrow.parquet as pq
import time
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone, timedelta
from pathlib import Path
import logging
import yaml
from tqdm import tqdm

from .rate_limiter import get_rate_limiter
from .checkpoint import CheckpointManager
from .data_processor import DataProcessor
from .exceptions import (
    BinanceAPIError, RateLimitError, IPBannedError,
    ValidationError, ConfigurationError
)

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "exchanges.yaml"


class BinanceRestDownloader:
    """
    币安REST API下载器

    功能：
    - 管理6个数据端点的下载
    - 限流控制和并发管理
    - 断点续传
    - 进度报告
    """

    # 端点配置
    ENDPOINTS = {
        "klines": {
            "path": "/fapi/v1/klines",
            "checkpoint_interval": 24 * 60 * 60 * 1000,  # 按天检查点
            "processor": "process_klines",
            "request_limit": 1500,
            "step_ms": 60 * 1000,
            "default_params": {
                "interval": "1m"
            }
        },
        "agg_trades": {
            "path": "/fapi/v1/aggTrades",
            "checkpoint_interval": 60 * 60 * 1000,  # 接口限制为1小时内
            "processor": "process_agg_trades",
            "request_limit": 1000,
            "append_mode": True,
            "history_window_ms": 365 * 24 * 60 * 60 * 1000,
            "history_window_desc": "最近1年"
        },
        "funding_rate": {
            "path": "/fapi/v1/fundingRate",
            "checkpoint_interval": 30 * 24 * 60 * 60 * 1000,
            "processor": "process_funding_rate",
            "request_limit": 1000,
            "step_ms": 8 * 60 * 60 * 1000
        },
        "open_interest": {
            "path": "/futures/data/openInterestHist",
            "checkpoint_interval": 24 * 60 * 60 * 1000,
            "processor": "process_open_interest",
            "request_limit": 500,
            "step_ms": 5 * 60 * 1000,
            "history_window_ms": 31 * 24 * 60 * 60 * 1000,
            "history_window_desc": "最近1个月",
            "default_params": {
                "period": "5m"
            }
        },
        "long_short_ratio": {
            "path": "/futures/data/globalLongShortAccountRatio",
            "checkpoint_interval": 24 * 60 * 60 * 1000,
            "processor": "process_long_short_ratio",
            "request_limit": 500,
            "step_ms": 5 * 60 * 1000,
            "history_window_ms": 30 * 24 * 60 * 60 * 1000,
            "history_window_desc": "最近30天",
            "default_params": {
                "period": "5m"
            }
        },
        "taker_ratio": {
            "path": "/futures/data/takerlongshortRatio",
            "checkpoint_interval": 24 * 60 * 60 * 1000,
            "processor": "process_taker_ratio",
            "request_limit": 500,
            "step_ms": 5 * 60 * 1000,
            "history_window_ms": 30 * 24 * 60 * 60 * 1000,
            "history_window_desc": "最近30天",
            "default_params": {
                "period": "5m"
            }
        }
    }

    def __init__(self, config_path: str | Path = DEFAULT_CONFIG_PATH):
        """
        初始化下载器

        Args:
            config_path: 配置文件路径
        """
        self.config_path = self._resolve_repo_path(config_path)
        self.config = self._load_config(self.config_path)
        self.binance_config = self.config['exchanges']['binance']
        self.endpoint_configs = self._build_endpoint_configs()

        # 初始化组件
        base_url = self.binance_config['base_url']
        download_config = self.binance_config['download']
        storage_config = dict(self.binance_config['storage'])
        storage_base_path = self._resolve_repo_path(storage_config['base_path'])
        cache_path = self._resolve_repo_path(storage_config['cache_path'])
        rate_limit_config = self.binance_config['rate_limits']

        # 限流器
        self.rate_limiter = get_rate_limiter(
            requests_per_minute=rate_limit_config['requests_per_minute'],
            safety_margin=rate_limit_config['safety_margin']
        )

        # 检查点管理器
        checkpoint_path = (
            cache_path /
            storage_config['checkpoint_file']
        )
        self.checkpoint = CheckpointManager(
            checkpoint_path=str(checkpoint_path),
            storage_path=str(storage_base_path)
        )

        # 数据处理器
        self.processor = DataProcessor(
            storage_path=str(storage_base_path),
            compression=storage_config.get('compression', 'snappy'),
            row_group_size=storage_config.get('row_group_size', 100000)
        )

        # 下载参数
        self.symbol = download_config['symbol']
        self.start_date = download_config['start_date']
        self.end_date = download_config['end_date']
        self.max_concurrent = download_config.get('max_concurrent', 10)
        self.timeout = download_config.get('timeout_seconds', 30)
        self.max_retries = download_config.get('max_retries', 5)

        # 并发控制
        self.semaphore = asyncio.Semaphore(self.max_concurrent)

        # 会话
        self.session: Optional[aiohttp.ClientSession] = None

        # 统计
        self.stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_records": 0,
            "start_time": None,
            "end_time": None
        }

        # 取消标志
        self._cancelled = False

    def _build_endpoint_configs(self) -> Dict[str, dict]:
        """合并代码默认值和 YAML 中的端点配置"""
        configured = self.binance_config.get("endpoints", {})
        merged_configs: Dict[str, dict] = {}

        for endpoint_name, defaults in self.ENDPOINTS.items():
            endpoint_config = defaults.copy()
            external = configured.get(endpoint_name, {})

            endpoint_config["path"] = external.get("path", endpoint_config["path"])
            endpoint_config["request_limit"] = external.get(
                "limit",
                endpoint_config.get("request_limit", 1000)
            )

            default_params = dict(endpoint_config.get("default_params", {}))
            default_params.update(external.get("params", {}))
            endpoint_config["default_params"] = default_params

            merged_configs[endpoint_name] = endpoint_config

        return merged_configs

    @staticmethod
    def _resolve_repo_path(path_value: str | Path) -> Path:
        path = Path(path_value)
        if path.is_absolute():
            return path
        return PROJECT_ROOT / path

    @staticmethod
    def _format_timestamp(timestamp_ms: int) -> str:
        """将毫秒时间戳转为易读字符串"""
        return datetime.fromtimestamp(
            timestamp_ms / 1000,
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")

    def _get_download_window(
        self,
        endpoint: str,
        start_ts: int,
        end_ts: int
    ) -> Optional[tuple[int, int]]:
        """
        根据接口历史窗口限制，裁剪实际可下载时间范围

        返回:
            (start_ts, end_ts) 或 None（表示当前配置完全不可下载）
        """
        endpoint_config = self.endpoint_configs[endpoint]
        history_window_ms = endpoint_config.get("history_window_ms")
        if not history_window_ms:
            return start_ts, end_ts

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        earliest_available_ts = now_ms - history_window_ms
        history_desc = endpoint_config.get("history_window_desc", "有限历史窗口")

        if end_ts <= earliest_available_ts:
            logger.warning(
                f"{endpoint}: Binance API 仅提供{history_desc}数据，"
                f"当前配置区间 {self._format_timestamp(start_ts)} - "
                f"{self._format_timestamp(end_ts)} 完全超出可下载范围，已跳过"
            )
            return None

        if start_ts < earliest_available_ts:
            logger.warning(
                f"{endpoint}: Binance API 仅提供{history_desc}数据，"
                f"起始时间将从 {self._format_timestamp(start_ts)} "
                f"裁剪到 {self._format_timestamp(earliest_available_ts)}"
            )
            start_ts = earliest_available_ts

        return start_ts, end_ts

    def _build_request_params(
        self,
        endpoint: str,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None,
        from_id: Optional[int] = None
    ) -> dict:
        """构建请求参数"""
        endpoint_config = self.endpoint_configs[endpoint]
        params = {
            "symbol": self.symbol,
            "limit": endpoint_config.get("request_limit", 1000),
        }
        params.update(endpoint_config.get("default_params", {}))

        if from_id is not None:
            params["fromId"] = from_id
            return params

        if start_ts is not None:
            params["startTime"] = start_ts
        if end_ts is not None:
            params["endTime"] = end_ts

        return params

    @staticmethod
    def _clip_dataframe_to_range(
        df,
        start_ts: int,
        end_ts: int
    ):
        """将 DataFrame 裁剪到 [start_ts, end_ts) 范围内"""
        if df.empty or 'timestamp' not in df.columns:
            return df

        clipped = df[
            (df['timestamp'] >= start_ts) &
            (df['timestamp'] < end_ts)
        ].copy()

        if not clipped.empty:
            clipped = clipped.sort_values('timestamp').reset_index(drop=True)

        return clipped

    def _expected_record_count(
        self,
        endpoint: str,
        start_ts: int,
        end_ts: int
    ) -> Optional[int]:
        """计算固定频率端点在给定窗口内的理论记录数"""
        step_ms = self.endpoint_configs[endpoint].get("step_ms")
        if not step_ms:
            return None

        return max(0, (end_ts - start_ts) // step_ms)

    def _validate_fixed_interval_batch(
        self,
        endpoint: str,
        start_ts: int,
        end_ts: int,
        df
    ) -> None:
        """验证固定频率端点是否完整下载"""
        expected_count = self._expected_record_count(endpoint, start_ts, end_ts)
        if expected_count is None:
            return

        actual_count = len(df)
        # 允许差 2 条以内（API 边界条件），只在严重缺失时报错
        if actual_count < expected_count - 2:
            raise ValidationError(
                f"{endpoint} 数据不完整: 预期 {expected_count} 条, 实际 {actual_count} 条"
            )

    def _load_config(self, config_path: str | Path) -> dict:
        """加载配置文件"""
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = yaml.safe_load(f)
            return config
        except Exception as e:
            raise ConfigurationError(f"加载配置文件失败: {e}")

    async def __aenter__(self):
        """异步上下文管理器入口"""
        self.session = aiohttp.ClientSession(
            base_url=self.binance_config['base_url'],
            timeout=aiohttp.ClientTimeout(total=self.timeout)
        )
        self.stats['start_time'] = time.time()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        if self.session:
            await self.session.close()
        self.stats['end_time'] = time.time()

    def cancel(self):
        """取消下载"""
        self._cancelled = True
        logger.info("下载已取消")

    async def _make_request(
        self,
        endpoint: str,
        params: dict
    ) -> List[List[Any]]:
        """
        发送API请求

        Args:
            endpoint: 端点名称
            params: 请求参数

        Returns:
            API响应数据
        """
        if self._cancelled:
            raise asyncio.CancelledError("下载已取消")

        endpoint_config = self.endpoint_configs[endpoint]
        path = endpoint_config['path']

        # 等待限流
        await self.rate_limiter.acquire()

        # 并发控制
        async with self.semaphore:
            for attempt in range(self.max_retries):
                try:
                    self.stats['total_requests'] += 1

                    async with self.session.get(
                        path,
                        params=params
                    ) as response:
                        # 更新限流器
                        self.rate_limiter.update_from_response(response.headers)

                        # 处理响应
                        if response.status == 200:
                            data = await response.json()
                            self.stats['successful_requests'] += 1
                            return data

                        elif response.status == 418:
                            # IP被封禁
                            logger.error(f"IP被封禁 (HTTP 418)")
                            await self.rate_limiter.wait_for_reset()
                            continue

                        elif response.status == 429:
                            # 限流
                            logger.warning(f"触发限流 (HTTP 429)")
                            await self.rate_limiter.wait_for_reset()
                            continue

                        elif response.status == 400:
                            # 400 = 参数错误（常见于超出API时间窗口），不重试
                            error_text = await response.text()
                            raise BinanceAPIError(
                                f"API请求失败: HTTP 400 (不重试): {error_text[:120]}",
                                status_code=400,
                                response={"error": error_text}
                            )

                        else:
                            # 其他错误
                            error_text = await response.text()
                            raise BinanceAPIError(
                                f"API请求失败: HTTP {response.status}",
                                status_code=response.status,
                                response={"error": error_text}
                            )

                except asyncio.CancelledError:
                    raise

                except aiohttp.ClientError as e:
                    if attempt == self.max_retries - 1:
                        raise BinanceAPIError(f"网络请求失败: {e}")
                    wait_time = 2 ** attempt
                    logger.warning(f"网络错误，{wait_time}秒后重试: {e}")
                    await asyncio.sleep(wait_time)

                except Exception as e:
                    # HTTP 400 不重试，直接抛出
                    if isinstance(e, BinanceAPIError) and getattr(e, 'status_code', 0) == 400:
                        raise
                    if attempt == self.max_retries - 1:
                        raise
                    logger.warning(f"请求失败，重试中: {e}")
                    await asyncio.sleep(2 ** attempt)

    def _get_range_file_path(self, endpoint: str, start_ts: int) -> Path:
        """根据端点和起始时间推导写入的 Parquet 文件路径"""
        partition_path = self.processor.get_partition_path(endpoint, start_ts)
        dt = datetime.fromtimestamp(start_ts / 1000)

        if self.endpoint_configs[endpoint].get("append_mode"):
            return partition_path / f"{dt.strftime('%Y%m%d')}.parquet"

        return partition_path / f"part-{dt.strftime('%Y%m%d-%H%M%S')}.parquet"

    def _is_range_data_valid(
        self,
        endpoint: str,
        start_ts: int,
        end_ts: int
    ) -> bool:
        """验证检查点对应的数据文件是否存在且完整"""
        file_path = self._get_range_file_path(endpoint, start_ts)
        if not file_path.exists():
            return False

        expected_count = self._expected_record_count(endpoint, start_ts, end_ts)
        if expected_count is None:
            return True

        try:
            metadata = pq.ParquetFile(file_path).metadata
            actual_count = metadata.num_rows
        except Exception as exc:
            logger.warning(f"{endpoint}: 读取 Parquet 元数据失败: {exc}")
            return False

        if actual_count < expected_count:
            logger.warning(
                f"{endpoint}: 已存在文件记录数不足 "
                f"({actual_count} < {expected_count})，将重新下载"
            )
            return False

        return True

    def repair_checkpoint_data(
        self,
        endpoints: Optional[List[str]] = None,
        start_ts: Optional[int] = None,
        end_ts: Optional[int] = None
    ) -> None:
        """清理已损坏或不完整的检查点，避免错误状态污染重跑"""
        endpoints = endpoints or list(self.endpoint_configs.keys())
        modified = False

        for endpoint in endpoints:
            state = self.checkpoint.data.get("endpoints", {}).get(endpoint)
            if not state:
                continue

            for range_entry in list(state.get("completed_ranges", [])):
                range_start = range_entry.get("start_ts")
                range_end = range_entry.get("end_ts")

                if range_start is None or range_end is None:
                    continue

                if start_ts is not None and range_end <= start_ts:
                    continue
                if end_ts is not None and range_start >= end_ts:
                    continue

                if not self._is_range_data_valid(endpoint, range_start, range_end):
                    if self.checkpoint.remove_completed_range(
                        endpoint,
                        range_start,
                        range_end
                    ):
                        modified = True

        if modified:
            logger.info("检查点已修复，后续下载将补齐缺失范围")
        else:
            logger.info("检查点校验通过，无需修复")

    async def _fetch_standard_dataframe(
        self,
        endpoint: str,
        range_start: int,
        range_end: int
    ):
        """下载普通 REST 端点的一段数据并转成 DataFrame"""
        processor = getattr(
            self.processor,
            self.endpoint_configs[endpoint]['processor']
        )
        params = self._build_request_params(
            endpoint,
            start_ts=range_start,
            end_ts=range_end - 1
        )
        data = await self._make_request(endpoint, params)
        if not data:
            return processor([])

        df = processor(data)
        return self._clip_dataframe_to_range(df, range_start, range_end)

    async def _fetch_agg_trades_dataframe(
        self,
        endpoint: str,
        range_start: int,
        range_end: int
    ):
        """下载一个小时窗口内的 aggTrades，并处理分页"""
        processor = getattr(
            self.processor,
            self.endpoint_configs[endpoint]['processor']
        )
        request_limit = self.endpoint_configs[endpoint].get("request_limit", 1000)
        window_end = range_end - 1

        batches = []
        next_from_id: Optional[int] = None
        request_start = range_start
        last_trade_id: Optional[int] = None

        while not self._cancelled:
            if next_from_id is None:
                params = self._build_request_params(
                    endpoint,
                    start_ts=request_start,
                    end_ts=window_end
                )
            else:
                params = self._build_request_params(
                    endpoint,
                    from_id=next_from_id
                )

            data = await self._make_request(endpoint, params)
            if not data:
                break

            batch_df = processor(data)
            if batch_df.empty:
                break

            batch_df = self._clip_dataframe_to_range(
                batch_df,
                range_start,
                range_end
            )
            if not batch_df.empty:
                batches.append(batch_df)

            if len(data) < request_limit:
                break

            last_item = data[-1]
            if not isinstance(last_item, dict):
                break

            raw_last_id = last_item.get("a")
            raw_last_ts = last_item.get("T")
            if raw_last_id is None or raw_last_ts is None:
                break

            raw_last_id = int(raw_last_id)
            raw_last_ts = int(raw_last_ts)

            if last_trade_id is not None and raw_last_id <= last_trade_id:
                logger.warning(
                    "agg_trades: 检测到分页游标未推进，提前终止以避免死循环"
                )
                break

            last_trade_id = raw_last_id
            if raw_last_ts >= window_end:
                break

            next_from_id = raw_last_id + 1
            request_start = raw_last_ts + 1

        if not batches:
            return processor([])

        combined = pd.concat(batches, ignore_index=True)
        combined = combined.drop_duplicates(
            subset=['agg_trade_id'],
            keep='first'
        )
        combined = combined.sort_values(
            ['timestamp', 'agg_trade_id']
        ).reset_index(drop=True)

        return combined

    async def _download_endpoint(
        self,
        endpoint: str,
        start_ts: int,
        end_ts: int,
        progress_bar: Optional[tqdm] = None
    ):
        """
        下载单个端点的数据

        Args:
            endpoint: 端点名称
            start_ts: 开始时间戳
            end_ts: 结束时间戳
            progress_bar: 进度条
        """
        endpoint_config = self.endpoint_configs[endpoint]
        interval_ms = endpoint_config['checkpoint_interval']

        # 计算待下载的范围
        ranges = self.checkpoint.calculate_remaining_ranges(
            endpoint, start_ts, end_ts, interval_ms
        )

        if not ranges:
            logger.info(f"{endpoint}: 无需下载，数据已完整")
            return

        logger.info(f"{endpoint}: 开始下载 {len(ranges)} 个时间范围")

        for range_start, range_end in ranges:
            if self._cancelled:
                break

            try:
                if endpoint == "agg_trades":
                    df = await self._fetch_agg_trades_dataframe(
                        endpoint,
                        range_start,
                        range_end
                    )
                else:
                    df = await self._fetch_standard_dataframe(
                        endpoint,
                        range_start,
                        range_end
                    )

                if df.empty:
                    logger.warning(
                        f"{endpoint}: 处理后DataFrame为空"
                    )
                    continue

                self._validate_fixed_interval_batch(
                    endpoint,
                    range_start,
                    range_end,
                    df
                )

                if endpoint_config.get("append_mode"):
                    self.processor.append_to_daily_parquet(
                        df,
                        endpoint,
                        range_start
                    )
                else:
                    self.processor.write_parquet(df, endpoint, range_start)

                # 更新检查点
                self.checkpoint.add_completed_range(
                    endpoint, range_start, range_end, len(df)
                )

                # 更新统计
                self.stats['total_records'] += len(df)

                # 更新进度条
                if progress_bar:
                    progress_bar.update(1)
                    progress_bar.set_postfix({
                        "endpoint": endpoint,
                        "records": f"{len(df):,}"
                    })

            except Exception as e:
                logger.error(
                    f"{endpoint}: 下载范围 {range_start}-{range_end} 失败: {e}"
                )
                self.stats['failed_requests'] += 1
                continue

    async def download_all(
        self,
        endpoints: Optional[List[str]] = None,
        show_progress: bool = True
    ):
        """
        下载所有端点数据

        Args:
            endpoints: 要下载的端点列表，None表示全部
            show_progress: 是否显示进度条
        """
        if endpoints is None:
            endpoints = list(self.endpoint_configs.keys())

        # 转换时间
        start_ts = int(datetime.fromisoformat(
            self.start_date.replace('Z', '+00:00')
        ).timestamp() * 1000)
        end_ts = int(datetime.fromisoformat(
            self.end_date.replace('Z', '+00:00')
        ).timestamp() * 1000)

        logger.info(f"开始下载: {self.symbol}")
        logger.info(f"时间范围: {self.start_date} - {self.end_date}")
        logger.info(f"端点: {', '.join(endpoints)}")

        self.repair_checkpoint_data(
            endpoints=endpoints,
            start_ts=start_ts,
            end_ts=end_ts
        )

        endpoint_windows = {}
        for endpoint in endpoints:
            window = self._get_download_window(endpoint, start_ts, end_ts)
            if window is not None:
                endpoint_windows[endpoint] = window

        if not endpoint_windows:
            logger.warning("当前配置下没有可下载的端点")
            return

        # 创建进度条
        progress_bars = {}
        if show_progress:
            for endpoint, (window_start, window_end) in endpoint_windows.items():
                ranges = self.checkpoint.calculate_remaining_ranges(
                    endpoint,
                    window_start,
                    window_end,
                    self.endpoint_configs[endpoint]['checkpoint_interval']
                )
                if ranges:
                    progress_bars[endpoint] = tqdm(
                        total=len(ranges),
                        desc=endpoint,
                        unit="range"
                    )

        # 并发下载所有端点
        tasks = []
        for endpoint, (window_start, window_end) in endpoint_windows.items():
            task = self._download_endpoint(
                endpoint,
                window_start,
                window_end,
                progress_bars.get(endpoint)
            )
            tasks.append(task)

        try:
            await asyncio.gather(*tasks, return_exceptions=True)
        finally:
            # 关闭进度条
            for bar in progress_bars.values():
                bar.close()

    def print_stats(self):
        """打印统计信息"""
        duration = self.stats.get('end_time', time.time()) - self.stats.get('start_time', time.time())

        print("\n" + "=" * 60)
        print("下载统计")
        print("=" * 60)
        print(f"总请求数: {self.stats['total_requests']:,}")
        print(f"成功请求: {self.stats['successful_requests']:,}")
        print(f"失败请求: {self.stats['failed_requests']:,}")
        print(f"总记录数: {self.stats['total_records']:,}")
        print(f"耗时: {duration:.2f} 秒")
        print(f"速率: {self.stats['total_records'] / max(duration, 1):.0f} 记录/秒")

        # 限流器统计
        limiter_stats = self.rate_limiter.get_stats()
        print(f"\n限流器:")
        print(f"  总请求: {limiter_stats['total_requests']:,}")
        print(f"  阻塞次数: {limiter_stats['blocked_count']:,}")
        print(f"  阻塞率: {limiter_stats['blocked_rate']:.2%}")

        print("=" * 60 + "\n")

        # 检查点汇总
        self.checkpoint.print_summary()


async def main():
    """主函数"""
    # 设置日志
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    async with BinanceRestDownloader() as downloader:
        try:
            # 可以选择下载特定端点，或全部
            await downloader.download_all(
                endpoints=["klines", "funding_rate"],  # 先测试这两个
                show_progress=True
            )

            downloader.print_stats()

        except KeyboardInterrupt:
            print("\n\n收到中断信号，正在安全退出...")
            downloader.cancel()
        except Exception as e:
            logger.error(f"下载失败: {e}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
