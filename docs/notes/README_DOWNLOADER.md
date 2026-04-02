# 币安历史数据下载器

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 测试组件

```bash
python test_downloader.py
```

### 3. 运行下载器

```bash
python run_downloader.py
```

## 功能特性

- ✅ **6种数据类型**: K线、聚合交易、资金费率、持仓量、多空比、主动买卖比
- ✅ **智能限流**: 令牌桶算法，自动控制API请求速率
- ✅ **断点续传**: 基于JSON检查点，支持中断后恢复
- ✅ **高效存储**: Parquet格式，按日期分区，Snappy压缩
- ✅ **并发下载**: 异步IO，可配置并发数
- ✅ **进度报告**: tqdm进度条，实时显示下载进度

## 配置

配置文件: `config/exchanges.yaml`

```yaml
download:
  symbol: "BTCUSDT"
  start_date: "2024-09-17T00:00:00Z"
  end_date: "2025-03-17T00:00:00Z"
  max_concurrent: 10  # 并发请求数

storage:
  base_path: "data/storage"
  compression: "snappy"
```

## 目录结构

```
├── config/
│   └── exchanges.yaml          # 配置文件
├── data/
│   ├── downloader/
│   │   ├── __init__.py
│   │   ├── binance_rest.py      # 主下载器
│   │   ├── rate_limiter.py      # 限流器
│   │   ├── checkpoint.py        # 检查点管理
│   │   ├── data_processor.py    # 数据处理
│   │   └── exceptions.py        # 异常定义
│   ├── storage/                 # Parquet数据文件
│   │   ├── klines/
│   │   ├── funding_rate/
│   │   └── ...
│   └── cache/
│       └── checkpoints.json     # 检查点文件
├── logs/
│   └── downloader.log
├── run_downloader.py            # 运行脚本
├── test_downloader.py           # 测试脚本
└── requirements.txt
```

## 数据量估算

| 数据类型 | 记录数 | 文件大小 |
|---------|--------|---------|
| K线 (1m) | ~260万 | ~50 MB |
| 聚合交易 | ~1亿+ | ~8-12 GB |
| 资金费率 | ~540 | <1 MB |
| 持仓量 | ~5.2万 | ~2 MB |
| 多空比 | ~5.2万 | ~2 MB |
| 主动买卖比 | ~5.2万 | ~2 MB |
| **总计** | **~1.03亿** | **~8-13 GB** |

## 使用示例

### 下载数据

```python
import asyncio
from data.downloader.binance_rest import BinanceRestDownloader

async def main():
    async with BinanceRestDownloader() as downloader:
        # 下载所有数据
        await downloader.download_all(show_progress=True)

        # 或选择性下载
        await downloader.download_all(
            endpoints=["klines", "funding_rate"],
            show_progress=True
        )

        # 查看统计
        downloader.print_stats()

asyncio.run(main())
```

### 读取下载的数据

```python
import pandas as pd

# 读取K线数据
df = pd.read_parquet('data/storage/klines/year=2024/month=09/day=17/*.parquet')

print(df.head())
print(f"记录数: {len(df):,}")
```

## 检查点管理

```python
from data.downloader.checkpoint import CheckpointManager

checkpoint = CheckpointManager(
    checkpoint_path="data/cache/checkpoints.json",
    storage_path="data/storage"
)

# 查看汇总
checkpoint.print_summary()

# 验证并修复
checkpoint.validate_and_repair()
```

## 故障排除

### 限流问题 (HTTP 429)

- 降低并发数: 在 `exchanges.yaml` 中设置 `max_concurrent: 5`
- 增加安全边际: 设置 `safety_margin: 0.9`

### 网络问题

- 检查网络连接
- 配置代理（如需要）
- 增加超时时间: `timeout_seconds: 60`

### 接口历史窗口限制

- `agg_trades` 只能查询最近约 `1 年` 的数据
- `open_interest` 只能查询最近 `1 个月`
- `long_short_ratio` / `taker_ratio` 只能查询最近 `30 天`
- 当配置时间范围超出 Binance 接口历史窗口时，下载器会自动提示并跳过不可下载区间，而不是反复报错

### 检查点损坏

```bash
python run_downloader.py
# 选择: 3. 验证检查点
```

## 下一步

数据下载完成后：

1. **验证数据质量**
   ```python
   import pandas as pd
   df = pd.read_parquet('data/storage/klines/year=2024/month=09/day=17/*.parquet')
   assert len(df) == 1440  # 一天1440分钟
   ```

2. **实现6维特征计算器** (`core/dimensions/`)

3. **实现5个确定性信号检测器** (`signals/`)

4. **运行回测，计算胜率**
