# BTC 物理确定性行为筛选引擎

现在这份仓库只保留 live 主链需要的骨架：实时采集、实时监控、实时 Alpha 发现、执行与日志。旧的研究脚本、固定持仓跟踪、离线审批入口和历史扫描产物已经从主干移走。

## 当前主链

| 模块 | 作用 |
|---|---|
| `run_ws.py` | 采集 4 条实时流并落盘 |
| `run_live_discovery.py` | 读取最新特征，持续发现和更新 Alpha 候选 |
| `run_monitor.py` | 读取实时特征，运行 live 策略与执行层 |
| `watchdog.py` | 一键拉起采集、监控、发现，并负责守护 |

## 当前目录骨架

| 路径 | 作用 |
|---|---|
| `core/` | 特征引擎与维度特征计算 |
| `signals/` | 当前仍接入 live 的事件型策略 |
| `alpha/` | 实时 Alpha 发现与候选池 |
| `monitor/` | 实时特征、信号调度、制度过滤、告警 |
| `execution/` | 下单、持仓、真实出场、交易日志 |
| `data/` | Parquet 数据存储 |
| `ui/` | 当前控制台前端与接口 |
| `docs/notes/` | 仍保留参考价值的运行说明 |

## 当前 live 策略

| 家族 | 名称 | 可监控方向 | 可执行方向 | 状态 |
|---|---|---|---|---|
| `P0-2` | Funding Rate Arbitrage | `long/short` | `short` | live |
| `P1-2` | VWAP/TWAP Slicing | `long` | `long` | live |
| `P1-6` | Bottom Volume Drought | `long` | `long` | live |
| `P1-8` | VWAP Vol Drought | `long/short` | `long/short` | live |
| `P1-9` | Position Compression | `long/short` | `long` | live |
| `P1-10` | Taker Exhaustion Low | `long/short` | `long` | live |
| `P1-11` | High Position Funding | `short` | `short` | live |
| `C1` | Funding Cycle Oversold Long | `long` | `long` | live |
| `A2-26` | High Proximity + OI Cooldown Short | `short` | `short` | live |
| `A2-29` | High Proximity + Wide Spread Short | `short` | `short` | live |

入场与出场的代码真实口径见 [LIVE_STRATEGY_LOGIC.md](LIVE_STRATEGY_LOGIC.md)。

## 真实记录口径

| 项目 | 口径 |
|---|---|
| 入场/出场/收益 | 统一由 `execution/` 层记录 |
| 交易日志 | `execution/logs/trades.csv` |
| 系统状态 | `monitor/output/system_state.json` |
| Alpha 候选 | `alpha/output/pending_rules.json` |
| 已批准 Alpha | `alpha/output/approved_rules.json` |

## 常用命令

```bash
python run_doctor.py
python run_smoke_check.py
python watchdog.py
python run_monitor.py
python run_live_discovery.py --once
python run_ws.py
python run_downloader.py
python start_system.py
```
