# BTC 永续合约物理确定性行为筛选引擎

> 交易标的: 币安 BTCUSDT 永续合约
> 当前阶段: Testnet 实盘验证
> 技术栈: Python 3.10+, Pandas, PyArrow, aiohttp/websockets
> 语言: 中文注释，无 emoji

---

## 一、绝对禁令（红线）

违反以下任何一条都会破坏系统边际或造成实际损失：

| 禁令 | 原因 |
|------|------|
| TIME 维度特征（`hour_in_day`、`minutes_to_funding`、`day_of_week`）禁止作为 Alpha 种子或确认因子 | 时间季节性是统计伪相关，不是物理因果 |
| 入场只用限价单（Maker 0.02%x2=0.04% 来回） | Taker 0.05%x2=0.10% 来回会吃掉全部利润，系统正期望依赖 Maker 费率 |
| 所有回测/报告收益必须扣除手续费 | 不扣费的胜率和收益是假的 |
| 禁止用固定持仓 N 根 K 线作为出场逻辑 | 出场必须基于机制衰竭或 vs_entry 变化量，`safety_cap` 只是最后安全网 |
| `horizon/hold_bars` 是研究观察窗，不是出场时间 | 代码中严格分离：`horizon` 用于 IC 扫描和 WalkForward 窗口期，`safety_cap` 用于时间兜底。混淆这二者会导致把”数据挖掘窗”误当成”离场契机” |
| 禁止说”根据策略卡片的 horizon 出场” | 正确说法是”根据 vs_entry 出场，safety_cap 作最后保障”。horizon 只决定研究时窗，不决定实盘离场 |
| 禁止在回测开始时写死止损参数 | 止损值必须通过网格扫描或数据优化得出，不能人为预设一个固定值 |
| 禁止 emoji | Windows GBK 控制台会崩溃 |
| API 密钥只能从 `.env` 文件读取，禁止硬编码 | 安全要求 |
| Parquet 存储必须 Hive 分区：`year=/month=/day=/` | `FeatureEngine.load_date_range()` 依赖此结构 |
| 新策略必须有明确的物理机制（什么力在失衡、为什么会回归） | 纯统计 pattern 不被接受 |

---

## 二、核心哲学

### 一句话

**入场是捕捉一股"力"（微观结构失衡），出场是判断这股"力"用完了没有。**

### 入场 = 检测失衡

每个策略检测的都是一个微观结构失衡——市场里某种力量暂时失去了对手方。

例如：价格跌到底部但成交量消失 = 卖方力量耗尽；价格被推到高位但持仓量没跟上 = 假高位无支撑。

入场的瞬间，系统**拍快照**：把当前所有 18 个维度的特征值（VWAP 偏离、24h 区间位置、吃单买卖比、持仓变化率、成交量等）全部记录下来。这个快照就是"入场时那股力量的指纹"。

### 出场 = 判断力是否用完（vs_entry 核心概念）

出场问的是因果问题：**入场时捕捉到的那股力，现在还在吗？**

每根 K 线都把当前特征值减去入场快照值，得到 **vs_entry 变化量**。出场条件基于这些变化量，不是绝对值。

为什么必须用 vs_entry：市场绝对值每天不同，昨天的"低位"可能是今天的"中间位"。但入场时那股力的强度是确定的。出场看的是"力释放了多少"——这是相对量，不受市场绝对位置影响。

**你赚的是从"失衡"到"回归"这段距离的钱。当 vs_entry 变化量告诉你回归已经完成到足够程度，就收工。**

### 出场优先级瀑布（每根 K 线按以下顺序检查，命中即停）

| 优先级 | 层 | 逻辑 |
|--------|-----|------|
| 1 | 硬止损 | 亏损达阈值（0.30%~1.50%，因家族而异），无条件出场 |
| 2 | 止盈（如配置） | 收益达 take_profit_pct，立即出场 |
| 3 | 利润保护 | 浮盈峰值(MFE)达启动阈值后激活 trailing floor，回撤到 floor 以下锁利 |
| 4 | 最低持仓保护 | 未到 min_hold_bars，强制继续持有 |
| 5 | 机制生命周期追踪 | 主因衰竭+辅助确认，衰竭分 >= 0.6 出场，>= 0.3 收紧保护 |
| 6 | 智能出场（vs_entry 核心层） | 基于入场快照对比当前值的 Top-3 出场组合。亏损时不出场，等止损或转盈 |
| 7 | 出场确认防抖 | 需要确认的统计退出按 exit_confirm_bars 做防抖 |
| 8 | `safety_cap`（时间安全网） | 只有以上逻辑全未触发才兜底，是最后一根救命稻草 |

### 运行时守卫（不是主离场因果）

| 守卫 | 作用 |
|---|---|
| `min_hold_bars` | 入场后前几 bar 不允许动态离场，防止噪声抖动 |
| `exit_confirm_bars` | 对统计退出做防抖确认 |
| 家族例外包装 | 个别家族可有临时 TP/SL 外壳，但不能改写全局 doctrine |

### vs_entry 具体例子

底部成交量枯竭做多，入场时 24h 区间位置 = 5%。出场条件不是"区间位置 > 20%"，而是"区间位置已回到 21.7% 以上 **并且** VWAP 偏离相对入场时回升了 0.31% 以上"。判断的是"卖方枯竭 -> 反弹"这股力释放了多少。

---

## 三、架构总览

系统由 5 个层级组成，通过 `watchdog.py` 统一守护：

| 层级 | 入口 | 核心模块 | 职责 |
|------|------|----------|------|
| 实时采集 | `run_ws.py` | `data/downloader/` | 4 条 WebSocket 流 + REST 轮询，落成 Parquet |
| 特征引擎 | `core/feature_engine.py` | `core/dimensions/*.py` (10 文件) | 10 维度、53+ 特征，每分钟更新 |
| 信号检测 + 监控 | `run_monitor.py` | `signals/*.py`, `monitor/` | 13 个 live 检测器 + Alpha 规则，制度/流/趋势过滤 |
| 执行 | （嵌入 run_monitor.py） | `execution/` | 限价入场、机制退出、仓位管理、交易日志 |
| Alpha 发现 | `run_live_discovery.py` | `alpha/` | IC 扫描 -> 原子挖掘 -> WF 验证 -> 组合扫描 -> 自动晋升 |
| 守护 | `watchdog.py` | `diagnostics/` | 一键拉起并守护全系统 |

### 实时数据流

```
Binance WebSocket (kline_1m)
  -> LiveFeatureEngine.update(kline)     # 滚动窗口 3000 bars
  -> 10 维特征计算
  -> SignalRunner.run(df)                # 9 live 检测器(含 C1/RT-1) + Alpha 规则
  -> [制度过滤] -> [流分类] -> [趋势过滤] -> [健康度过滤]
  -> ExecutionEngine.on_signal(alert)    # 白名单 + 冷却 + 集中度门控
  -> OrderManager.place_limit_entry()    # 限价入场，20s 超时
  -> 每根 K 线: on_bar() 评估出场
  -> close_position() -> TradeLogger
```

---

## 四、当前 live 策略

真值表来源: `monitor/live_catalog.py`

| 家族 | 名称 | 监控方向 | 执行方向 | 机制类型 | OOS WR | 出场方式 |
|------|------|----------|----------|----------|--------|----------|
| P0-2 | 资金费率套利 | long/short | long/short | funding_settlement | 67% | 硬编码 vs_entry |
| P1-2 | VWAP/TWAP 拆单 | long | long | algo_slicing | -- | 硬编码 vs_entry |
| P1-6 | 底部量能枯竭 | long | long | seller_drought | 54% | 硬编码 vs_entry |
| P1-8 | VWAP 偏离+量能枯竭 | long/short | long/short | vwap_reversion | 93.5% | 硬编码 vs_entry |
| P1-9 | 持仓压缩 | long/short | long/short | compression_release | 88% | 硬编码 vs_entry |
| P1-10 | 主动卖耗尽 | long/short | long/short | bottom_taker_exhaust | 78.5% | 硬编码 vs_entry |
| P1-11 | 高位负资金费率 | short | short | funding_divergence | 80.4% | 硬编码 vs_entry |
| C1 | 资金窗口超卖反弹 | long | long | funding_cycle_oversold | -- | 硬编码 vs_entry |
| RT-1 | 制度转换做多 | long | long | regime_transition | -- | 硬编码 vs_entry |
| A2-26 | 高位+OI 降温做空 | short | short | near_high_distribution | 75% | Alpha 卡片 Top-3 |
| A2-29 | 高位+点差扩张做空 | short | short | near_high_distribution | 72% | Alpha 卡片 Top-3 |
| A3-OI | OI 背离做空 | short | short | oi_divergence | -- | Alpha 卡片 Top-3 |
| A4-PIR | 高位+OI 停滞做空 | short | short | oi_divergence | 68.75% | Alpha 卡片 Top-3 |

**命名约定:**
- `P` 前缀: 手工发现的物理机制策略（P0=最高优先级，P1=核心）
- `A` 前缀: Alpha 管道自动发现并批准的策略
- `C` 前缀: 资金周期类策略
- `RT` 前缀: 制度转换类策略

入场/出场的代码真实口径见 `LIVE_STRATEGY_LOGIC.md`。当该文档与 `live_catalog.py` 代码不一致时，以代码为准。

---

## 五、10 维特征体系

所有特征函数签名: `compute_xxx(df: pd.DataFrame) -> pd.DataFrame`

| 维度 | 文件 | 关键特征 | 物理意义 |
|------|------|----------|----------|
| TIME | `time_features.py` | minute_in_hour, minutes_to_funding, amplitude_1m/ma20 | 算法执行时钟锚点、波动率 |
| PRICE | `price_features.py` | position_in_range_24h/4h, dist_to_24h_high/low, vwap_deviation | 限价单聚集带、均值回归锚 |
| TRADE_FLOW | `trade_flow_features.py` | taker_buy_sell_ratio, volume_vs_ma20, volume_autocorr_lag5, avg_trade_size_cv_10m | 主动方向、成交节奏、拆单痕迹 |
| LIQUIDITY | `liquidity_features.py` | kyle_lambda, spread_vs_ma20, spread_proxy | 价格冲击系数、流动性变化 |
| POSITIONING | `positioning_features.py` | oi_change_rate_5m/1h, funding_rate_trend, consecutive_extreme_funding | 杠杆分布、强平级联风险 |
| CROSS_MARKET | `cross_market_features.py` | btc_eth_corr_30m, btc_eth_corr_change | 跨品种信息流速 |
| LIQUIDATION | `liquidation_features.py` | btc_liq_net_pressure, total_liq_usd_5m, liq_size_p90_5m | 强平方向、鲸鱼爆仓检测 |
| MICROSTRUCTURE | `microstructure_features.py` | quote_imbalance, bid_depth_ratio, spread_anomaly | 盘口即时供需 |
| ORDER_FLOW | `order_flow_features.py` | large_trade_buy_ratio, direction_autocorr, trade_burst_index | 大单方向、趋势/反转判断 |
| MARK_PRICE | `mark_price_features.py` | rt_funding_rate, mark_basis, funding_countdown_m | 实时资金费率、基差演变 |

维度计算文件位于 `core/dimensions/`，由 `core/feature_engine.py` 按顺序调用。

---

## 六、Alpha 发现管道

管道入口: `run_live_discovery.py`（`--once` 单次 / `--watch --interval 1` 每小时持续扫描）

| 步骤 | 模块 | 做什么 |
|------|------|--------|
| 1. IC 扫描 | `alpha/scanner.py` | 53 特征 x 4 时间跨度，按 Spearman IC 排序 |
| 2. 因果原子挖掘 | `alpha/causal_atoms.py` | 单特征阈值规则: IF feature > threshold THEN direction |
| 3. Walk-Forward 验证 | `alpha/walk_forward.py` | 67% IS / 33% OOS 切分，验证样本外稳定性 |
| 4. 组合扫描 | `alpha/combo_scanner.py` | 种子+跨维度确认因子，提升胜率 |
| 5. 因果验证 | `alpha/causal_validator.py` | 物理一致性检查，拒绝无物理机制的规则 |
| 6. 自动晋升 | `alpha/auto_promoter.py` | 候选 -> approved_rules.json |

### 准入门槛（统计硬门槛，全部满足才进入 LLM 审核，代码强制执行）

| 指标 | 门槛 | 说明 |
|------|------|------|
| OOS 胜率（扣费后） | >= 65% | 扣除 Maker 0.04% 来回后的净胜率 |
| MFE 覆盖率 | >= 75% | **核心指标**：75% 的信号入场后价格至少往有利方向走超过费用 0.04%，方向都没对的策略直接淘汰 |
| OOS 样本数 | >= 30 | 不可妥协，数据量不够就用更多历史数据 |
| 降级比（OOS_ICIR / IS_ICIR） | > 0.5 | OOS 必须保留 IS 50% 以上的预测力 |
| OOS 净收益 | > 0% | 扣费后必须赚钱，这条绝对不能妥协 |
| 确认因子方向性 | 必须有方向偏见 | 涨跌都会触发的因子（如价差扩大）不接受 |
| 组合确认因子来源 | 仅 TRADE_FLOW / LIQUIDITY / POSITIONING | TIME 维度禁用 |

### LLM 审核（通过统计硬门槛后，按 CLAUDE.md 原则深度分析）

LLM 必须回答以下 6 个问题，第 3、5 条不通过则直接拒绝：

1. 这个信号捕捉的是什么"力"？力的物理来源是什么？
2. 这股力是暂时的吗？预计持续多少分钟？为什么会消失？
3. **确认因子是否有方向偏见？**（涨跌都会触发 = 拒绝）
4. 这个力在一天中会重复出现几次？
5. **在单边趋势中，这个信号会不会反复误触发？**（会 = 拒绝）
6. vs_entry 出场条件能否捕捉到力的消失？

### 出场卡片自动配置（3 阶段出场：保本 -> 锁利 -> 力消失）

每张通过审核的策略卡片必须自动配置：

| 阶段 | 触发条件 | 动作 |
|------|---------|------|
| 1. 保本 | MFE > 0.04%（覆盖费用） | 启动保本线，不亏是底线 |
| 2. 锁利 | MFE > 0.15% | 激活 trailing floor，锁住峰值 40% 利润 |
| 3. 力消失 | vs_entry Top-3 条件触发 | 正常离场，拿最大利润 |

### 实盘适应（活络应变）

- 新策略前 10 笔用保守参数（止损紧、保本快、半仓位）
- 前 10 笔 WR > 60% 后提升到正常参数
- 连续 3 笔亏损自动收紧止损 + 降低仓位
- signal_health 接入主链，表现差的策略自动降级

### 4 个关键原则（实盘验证后的核心教训）

P 系列策略全部盈利而 Alpha 管道策略全部亏损的根因分析结论：

| 原则 | 说明 | 代码执行位置 |
|------|------|------------|
| **持续性优先** | 信号必须检测"连续 N 个 block 满足条件"的持续状态，单 bar 快照不可靠。特征 `vol_drought_blocks_5m/10m` 和 `price_compression_blocks_5m/10m` 可作为种子或确认因子 | `realtime_seed_miner.py` BlockStateSeed + `feature_engine.py` |
| **方向性耦合** | 确认因子必须与交易方向有统计偏见（Spearman 相关 >= 0.02）。涨跌都会触发的因子（如价差扩大）直接拒绝 | `combo_scanner.py` 方向性检验 |
| **出场因果** | 出场条件必须包含入场种子特征的 vs_entry 变化量——力消失了才出场，不是统计巧合 | `live_discovery.py` _validate_exit_causality |
| **数据充分** | 引擎必须使用 365 天全量数据，n_oos >= 30 不可妥协 | `live_discovery.py` data_days=365 |

### 数据利用

- 引擎必须使用全量历史数据（data_days=365），不是只用 30 天
- 扫描频率: 每 1 小时扫描一次（`--interval 1`）
- 扫描周期: `[3, 5, 10, 15, 30, 60]` bars，覆盖短周期（做市商再平衡）到中周期（分发/枯竭）
- 重点挖掘 ORDER_FLOW 和 MICROSTRUCTURE 维度的逐笔成交特征
- 持续性特征（block state）与瞬时特征同等重要，优先作为种子

输出: `alpha/output/pending_rules.json`（候选）、`alpha/output/approved_rules.json`（已批准）

---

## 七、6 层过滤体系

信号从检测到执行需通过 6 层过滤，任何一层拦截即不交易：

| 层 | 模块 | 逻辑 | 当前状态 |
|----|------|------|----------|
| 1. 置信度 | `execution/config.py` | MIN_CONFIDENCE=2，拒绝 LOW(1) | 已生效 |
| 2. 流分类 | `monitor/flow_classifier.py` | LIQUIDATION 期间阻止 LONG | 已生效 |
| 3. 趋势方向 | `monitor/signal_runner.py` | TREND_UP 时 SHORT 需 HIGH(3)；TREND_DOWN 时 LONG 需 HIGH(3) | 已生效 |
| 4. 市场制度 | `monitor/regime_detector.py` | CRISIS 拦截全部 P2；VOL_EXPANSION 拦截 P2 LONG | 已生效 |
| 5. 信号健康度 | `monitor/signal_health.py` | 30 天滚动 PF < 0.8 连续 7 天 -> degraded -> 14 天 -> retired | 代码存在，主链未接线 |
| 6. 执行门控 | `execution/execution_engine.py` | 白名单、冷却、力集中度上限(每力类最多 2 仓)、总仓位上限(5) | 已生效 |

### 5 种市场制度（`regime_detector.py`）

QUIET_TREND / VOLATILE_TREND / RANGE_BOUND / VOL_EXPANSION / CRISIS

基于 amplitude_ma20 + volume_vs_ma20 + spread_vs_ma20 + oi_change_rate_1h 判定，需 3 根连续 K 线确认切换。

### 3 种趋势方向

TREND_UP / TREND_DOWN / TREND_NEUTRAL

三投票: 价格斜率(20 bar) + direction_autocorr + 24h 区间位置。

---

## 八、数据流与存储

### 实时流（`run_ws.py`）

| 流 | 落盘路径 | 衍生特征 |
|----|----------|----------|
| 爆仓 | `data/storage/liquidations/` | liq_net_pressure, liq_size_p90 |
| 盘口 | `data/storage/book_ticker/` | quote_imbalance, bid_depth_ratio |
| 逐笔成交 | `data/storage/agg_trades/` | large_trade_buy_ratio, trade_burst_index |
| 实时资金费率 | `data/storage/mark_price/` | rt_funding_rate, mark_basis |

### REST 端点（`run_downloader.py`）

| 端点 | 频率 | 落盘路径 |
|------|------|----------|
| klines (1m) | 持续 | `data/storage/klines/` |
| funding_rate | 8h | `data/storage/funding_rate/` |
| open_interest | 5m | `data/storage/open_interest/` |
| long_short_ratio | 5m | `data/storage/long_short_ratio/` |
| taker_ratio | 5m | `data/storage/taker_ratio/` |

### 存储格式

- Parquet, Snappy 压缩, 100K 行一个 row group
- Hive 分区: `data/storage/{endpoint}/year={YYYY}/month={MM}/day={DD}/*.parquet`
- 时间戳: UTC 毫秒
- 配置: `config/exchanges.yaml`（BTC）、`config/exchanges_eth.yaml`（ETH）

---

## 九、编码约定

### 基本规则

- 无 emoji（Windows GBK 控制台）
- 中文注释可用
- Type hints: 用 `from __future__ import annotations`
- 日志: `logger = logging.getLogger(__name__)`
- 入口脚本: 必须先调用 `bootstrap_runtime()` 再 import 项目模块
- 密钥: `.env` 文件加载，`execution/config.py` 负责读取
- 原子写文件: `.tmp` + `os.replace()` 模式

### 信号检测器接口

```python
# 继承 signals/base.py 的 SignalDetector
class MyDetector(SignalDetector):
    name = "P1-XX_my_signal"
    direction = "long"          # "long" / "short" / "both"
    research_horizon_bars = 30
    hold_bars = research_horizon_bars  # legacy alias
    required_columns = ["feature_a", "feature_b"]
    runner_cooldown_ms = 90_000  # SignalRunner 层冷却（毫秒）

    def detect(self, df: pd.DataFrame) -> pd.Series:
        """批量检测，返回与 df 同 index 的 bool Series"""
        ...

    def check_live(self, df: pd.DataFrame) -> dict | None:
        """实时检测，仅看最新 bar，返回信号 dict 或 None"""
        ...
```

### 特征函数

```python
# core/dimensions/xxx_features.py
def compute_xxx(df: pd.DataFrame) -> pd.DataFrame:
    """在 df 上原地添加特征列，返回 df"""
    ...
```

### 执行参数（`execution/config.py`）

| 参数 | 值 | 说明 |
|------|----|------|
| TESTNET | True | 当前连接测试网 |
| LEVERAGE | 10 | 10 倍杠杆 |
| POSITION_PCT | 0.08 | 每仓 8% 保证金 |
| MAX_POSITIONS | 5 | 最多同时持 5 仓 |
| MIN_CONFIDENCE | 2 | 最低置信度 MEDIUM |
| MAKER_FEE_RATE | 0.0002 | 单边 0.02% |
| TAKER_FEE_RATE | 0.0005 | 单边 0.05% |
| ENTRY_MAX_ATTEMPTS | 2 | 先 Maker 后 IOC |

---

## 十、新增策略检查清单

添加一个新策略需要完成以下全部步骤，缺一个都不能正常交易：

| # | 步骤 | 文件 |
|---|------|------|
| 1 | 写检测器（继承 `SignalDetector`） | `signals/new_signal.py` |
| 2 | 在 SignalRunner 导入并注册 | `monitor/signal_runner.py` |
| 3 | 添加 `LiveStrategySpec` | `monitor/live_catalog.py` |
| 4 | 定义物理机制和衰竭条件 | `monitor/mechanism_tracker.py` 的 `MECHANISM_CATALOG` |
| 5 | 配置出场参数（止损/保护/安全网上限） | `monitor/exit_policy_config.py` |
| 6 | 添加 vs_entry 出场条件 | `monitor/smart_exit_policy.py` |
| 7 | Walk-Forward 验证通过准入门槛 | OOS WR > 65%, n >= 30, 降级比 > 0.5 |
| 8 | 跑回归测试 | `run_regression.ps1` |
| 9 | 写测试用例 | `tests/` |

---

## 十一、关键文件速查

| 文件 | 职责 |
|------|------|
| `run_monitor.py` | 实时监控主入口（WebSocket + REST + 执行） |
| `run_ws.py` | 4 流实时采集入口 |
| `run_live_discovery.py` | Alpha 发现入口 |
| `run_downloader.py` | 历史数据下载 |
| `watchdog.py` | 一键守护全系统 |
| `core/feature_engine.py` | 特征引擎（离线批量计算） |
| `monitor/live_engine.py` | 实时特征引擎（滚动窗口 3000 bars） |
| `monitor/signal_runner.py` | 信号调度器（P1 检测器 + Alpha 规则 + 过滤） |
| `monitor/live_catalog.py` | **策略注册表（唯一真值来源）** |
| `monitor/smart_exit_policy.py` | vs_entry 出场逻辑 |
| `monitor/mechanism_tracker.py` | 机制生命周期追踪（衰竭评分） |
| `monitor/exit_policy_config.py` | 出场参数（止损/保护/安全网上限） |
| `monitor/regime_detector.py` | 5 种市场制度检测 |
| `monitor/flow_classifier.py` | 4 种流分类 |
| `monitor/signal_health.py` | 信号健康度生命周期 |
| `monitor/alpha_rules.py` | Alpha 规则加载与物理确认 |
| `execution/execution_engine.py` | 执行引擎（门控 + 下单 + 退出） |
| `execution/order_manager.py` | 币安 REST 订单管理 |
| `execution/trade_logger.py` | 交易 CSV 日志 |
| `execution/config.py` | 执行参数与 API 密钥 |
| `signals/base.py` | 信号检测器 ABC |
| `alpha/output/approved_rules.json` | 已批准 Alpha 卡片 |
| `data/storage/` | Parquet 数据根目录 |

---

## 十二、配套文档索引

| 文档 | 内容 |
|------|------|
| `LIVE_STRATEGY_LOGIC.md` | 每个策略的真实入场条件、vs_entry 出场条件、机制衰竭定义、止损值 |
| `ALPHA_ENGINE_SPEC.md` | Alpha 引擎完整架构、策略卡片生命周期、健康监控阈值 |
| `docs/notes/EXECUTION_LAYER_PROMPT.md` | 执行层设计规范、币安 API 参考 |
| `docs/notes/mechanism_audit.md` | 物理机制审计记录 |
| `README.md` | 项目概览与常用命令 |

---

## 十三、交互风格

角色: 专业伙伴，自然温和，像朋友交谈。

- 先总后分: 先概括，再展示数据表格，最后深度建议
- 对比/参数/步骤优先用 Markdown 表格
- 技术术语必须附通俗解释
- 回答结束时主动提出一个启发性的后续问题或建议
- 拒绝废话: 内容干货满满，不重复用户的问题
