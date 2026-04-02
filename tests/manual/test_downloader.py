"""
测试脚本 - 验证下载器各组件
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import asyncio
import time
from datetime import datetime, timezone, timedelta


def test_rate_limiter():
    """测试限流器"""
    print("\n" + "=" * 60)
    print("测试限流器")
    print("=" * 60)

    from data.downloader.rate_limiter import get_rate_limiter

    # 创建限流器（测试用：每分钟10个请求）
    limiter = get_rate_limiter(requests_per_minute=10, safety_margin=0.95)

    async def acquire_tokens():
        """获取令牌"""
        print("\n开始获取令牌...")
        start = time.time()

        for i in range(15):  # 尝试获取15个令牌（超过限制）
            success = await limiter.acquire(timeout=2)
            elapsed = time.time() - start
            print(f"  请求 {i+1}: {'成功' if success else '超时'} (耗时: {elapsed:.2f}s)")

        stats = limiter.get_stats()
        print(f"\n限流器统计:")
        print(f"  总请求: {stats['total_requests']}")
        print(f"  阻塞次数: {stats['blocked_count']}")
        print(f"  当前令牌: {stats['current_tokens']:.2f}/{stats['effective_capacity']}")

    asyncio.run(acquire_tokens())
    print("✓ 限流器测试完成")


def test_checkpoint_manager():
    """测试检查点管理器"""
    print("\n" + "=" * 60)
    print("测试检查点管理器")
    print("=" * 60)

    from data.downloader.checkpoint import CheckpointManager
    import tempfile
    import os

    # 创建临时目录
    with tempfile.TemporaryDirectory() as temp_dir:
        checkpoint_path = os.path.join(temp_dir, "test_checkpoint.json")
        storage_path = os.path.join(temp_dir, "storage")

        # 创建检查点管理器
        manager = CheckpointManager(
            checkpoint_path=checkpoint_path,
            storage_path=storage_path
        )

        print("\n1. 添加完成范围...")
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        day_ms = 24 * 60 * 60 * 1000

        for i in range(3):
            start = now + i * day_ms
            end = start + day_ms
            manager.add_completed_range(
                "test_endpoint",
                start,
                end,
                1000,
                "BTCUSDT"
            )

        print("✓ 添加了3个完成范围")

        print("\n2. 检查范围是否完成...")
        is_done = manager.is_range_completed(
            "test_endpoint",
            now,
            now + day_ms
        )
        print(f"  第一个范围已完成: {is_done}")

        print("\n3. 计算剩余范围...")
        remaining = manager.calculate_remaining_ranges(
            "test_endpoint",
            now,
            now + 5 * day_ms,
            day_ms
        )
        print(f"  剩余 {len(remaining)} 个范围")
        for start, end in remaining:
            dt = datetime.fromtimestamp(start / 1000)
            print(f"    {dt.strftime('%Y-%m-%d')}")

        print("\n4. 获取汇总...")
        manager.print_summary()

    print("✓ 检查点管理器测试完成")


def test_data_processor():
    """测试数据处理器"""
    print("\n" + "=" * 60)
    print("测试数据处理器")
    print("=" * 60)

    from data.downloader.data_processor import DataProcessor
    import pandas as pd
    import tempfile
    import os

    # 创建临时目录
    with tempfile.TemporaryDirectory() as temp_dir:
        processor = DataProcessor(
            storage_path=temp_dir,
            compression="snappy",
            row_group_size=100
        )

        print("\n1. 处理K线数据...")
        # 模拟K线数据
        klines_data = [
            [
                1710643200000,  # timestamp
                "65000.00",     # open
                "65100.00",     # high
                "64900.00",     # low
                "65050.00",     # close
                "1000.5",       # volume
                1710643259999,  # close_time
                "65025000.00",  # quote_volume
                5432,           # trades
                "500.25",       # taker_buy_base
                "32500000.00"   # taker_buy_quote
            ]
        ]

        df = processor.process_klines(klines_data)
        print(f"  DataFrame形状: {df.shape}")
        print(f"  列: {df.columns.tolist()}")

        print("\n2. 验证DataFrame...")
        try:
            processor.validate_dataframe(df, "klines")
            print("  ✓ 验证通过")
        except Exception as e:
            print(f"  ✗ 验证失败: {e}")

        print("\n3. 写入Parquet...")
        now = int(datetime.now(timezone.utc).timestamp() * 1000)
        processor.write_parquet(df, "klines", now)

        # 检查文件是否创建
        parquet_path = processor.storage_path / "klines"
        files = list(parquet_path.rglob("*.parquet"))
        print(f"  ✓ 创建了 {len(files)} 个Parquet文件")
        for f in files:
            print(f"    {f.relative_to(temp_dir)}")

        print("\n4. 处理真实接口风格的字典响应...")
        funding_df = processor.process_funding_rate([
            {
                "symbol": "BTCUSDT",
                "fundingRate": "0.00010000",
                "fundingTime": 1710633600000,
                "markPrice": "65123.45"
            }
        ])
        oi_df = processor.process_open_interest([
            {
                "symbol": "BTCUSDT",
                "sumOpenInterest": "12345.678",
                "sumOpenInterestValue": "804512345.67",
                "timestamp": 1710633900000
            }
        ])
        agg_df = processor.process_agg_trades([
            {
                "a": 123456789,
                "p": "65001.10",
                "q": "0.005",
                "f": 20001,
                "l": 20003,
                "T": 1710634200000,
                "m": True
            }
        ])

        print(f"  funding_rate 列: {funding_df.columns.tolist()}")
        print(f"  open_interest 列: {oi_df.columns.tolist()}")
        print(f"  agg_trades 列: {agg_df.columns.tolist()}")

    print("✓ 数据处理器测试完成")


def test_api_connection():
    """测试API连接"""
    print("\n" + "=" * 60)
    print("测试API连接")
    print("=" * 60)

    import aiohttp
    import asyncio

    async def fetch_server_time():
        """获取服务器时间"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://fapi.binance.com/fapi/v1/time"
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        server_time = data['serverTime']
                        dt = datetime.fromtimestamp(server_time / 1000)
                        print(f"  ✓ 服务器时间: {dt}")
                        print(f"  ✓ API连接正常")
                        return True
                    else:
                        print(f"  ✗ API返回错误: HTTP {response.status}")
                        return False
        except Exception as e:
            print(f"  ✗ 连接失败: {e}")
            return False

    result = asyncio.run(fetch_server_time())

    if result:
        print("\n提示: 如果连接失败，请检查:")
        print("  1. 网络连接是否正常")
        print("  2. 是否需要配置代理")
        print("  3. 币安API是否可访问")

    print("✓ API连接测试完成")


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("币安历史数据下载器 - 组件测试")
    print("=" * 60)

    tests = [
        ("限流器", test_rate_limiter),
        ("检查点管理器", test_checkpoint_manager),
        ("数据处理器", test_data_processor),
        ("API连接", test_api_connection)
    ]

    print("\n可用测试:")
    for i, (name, _) in enumerate(tests, 1):
        print(f"  {i}. {name}")
    print("  0. 运行所有测试")

    choice = input("\n选择测试 (0-4): ").strip()

    if choice == "0":
        for name, test_func in tests:
            try:
                test_func()
            except Exception as e:
                print(f"\n✗ {name}测试失败: {e}")
                import traceback
                traceback.print_exc()
    elif choice.isdigit() and 1 <= int(choice) <= len(tests):
        name, test_func = tests[int(choice) - 1]
        try:
            test_func()
        except Exception as e:
            print(f"\n✗ {name}测试失败: {e}")
            import traceback
            traceback.print_exc()
    else:
        print("无效选项")

    print("\n" + "=" * 60)
    print("测试完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
