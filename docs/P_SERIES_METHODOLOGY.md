# P 系列策略方法论参考文档

> 本文档是 Alpha 引擎 Kimi 研究员的核心参考。
> 所有 Alpha 引擎发现的策略必须遵循本文档的方法论。
> P 系列策略在实盘验证中持续盈利，本方法论是经过验证的"黄金标准"。

---

## 一、核心哲学

**入场 = 捕捉一股"力"（微观结构失衡）**
每个策略检测的都是一个微观结构失衡——市场里某种力量暂时失去了对手方。

**出场 = 判断这股"力"用完了没有（vs_entry 核心概念）**
出场问的是因果问题：入场时捕捉到的那股力，现在还在吗？

**你赚的是从"失衡"到"回归"这段距离的钱。**

---

## 二、6 步方法论流程

| 步骤 | 做什么 | 关键约束 |
|------|--------|---------|
| 1. 物理假设 | 识别一个微观结构失衡，说明什么力、为什么暂时、预期方向 | 必须有物理因果，不接受纯统计 pattern |
| 2. IS 扫描 | 在样本内数据（前 67%）扫描分位数阈值 | 使用 365 天全量数据 |
| 3. 持续性要求 | 入场条件必须要求"连续 N 个时间块满足" | 单 bar 阈值穿越不可靠 |
| 4. 冻结阈值 + OOS 验证 | IS 确定的阈值直接应用到 OOS（后 33%）验证 | OOS WR >= 65%, n >= 30, MFE 覆盖率 >= 75% |
| 5. MFE 峰值出场挖掘 | 对每个入场点追踪 MFE 峰值，找峰值时刻的 vs_entry 特征组合 | 出场条件必须用 vs_entry，不能用公式推导 |
| 6. 网格扫描止损 | 数据驱动优化 stop_pct 和 protect_start_pct | 止损值不能预设，必须从数据得出 |

---

## 三、持续性要求（Block State）

P 系列盈利的核心在于"持续性"——不是看某一根 K 线满足条件，而是看连续 N 个时间块都满足条件。

### compute_state_blocks() 算法

```python
def compute_state_blocks(df, tf_min, vol_drought_ratio=0.5):
    """
    参数:
        df: 1 分钟 DataFrame（含 volume, high, low）
        tf_min: 聚合粒度（分钟），如 5 或 10
        vol_drought_ratio: 成交量枯竭阈值（默认 0.5 = 低于均值 50%）
    
    返回:
        (vol_drought_count, price_compression_count)
    
    算法:
        1. 将 1 分钟数据聚合成 tf_min 分钟的块
        2. vol_blocks[i] = 块内成交量之和
        3. range_blocks[i] = 块内最高价 - 块内最低价
        4. 20 块滚动窗口计算基准（vol_ma, range_median）
        5. vol_drought = vol_blocks[i] < vol_ma * 0.5
        6. compress = range_blocks[i] < range_median
        7. 从最新块向前数连续满足的块数
    """
```

### 为什么持续性这么重要

- 单 bar 信号触发率 ~20-25%，信号被噪声稀释
- 连续 N 块信号触发率 ~0.5-3%，每次触发都是极端事件
- P1-8 要求 "连续 4 个 10 分钟块量能枯竭"，这意味着过去 40 分钟卖方力量持续消失
- P1-9 要求 "连续 8 个 10 分钟块价格压缩"，这意味着过去 80 分钟能量在积累

---

## 四、P 系列策略完整案例

### P1-8: VWAP 偏离 + 成交量干旱

**物理机制**: VWAP 算法交易商持续往公允价方向执行订单。当价格严重偏离 VWAP 且成交量同时干旱，说明推动价格的主动买/卖已停止——价格靠惯性撑着，一旦 VWAP 回归压力累积到临界点，直接反转。

**入场条件（IS 冻结阈值）**:

| Variant | 条件 | OOS 胜率 | OOS n |
|---------|------|---------|-------|
| SHORT | vwap_deviation > 0.020180 (p95) AND vol_drought_blocks_10m >= 4 | 96% | 25 |
| LONG | vwap_deviation < -0.023646 (p2) AND vol_drought_blocks_10m >= 3 | 91% | 33 |

**出场条件（MFE 峰值挖掘所得，非公式推导）**:

LONG 出场 Top-3:
- C1: vwap_deviation_vs_entry > 0.008898 AND dist_to_24h_low_vs_entry > 0.007402 AND vwap_deviation > -0.018165
- C2: position_in_range_24h_vs_entry > 0.090718 AND vwap_deviation_vs_entry > 0.008898 AND vwap_deviation > -0.018165
- C3: dist_to_24h_low_vs_entry > 0.007402 AND dist_to_24h_high_vs_entry > 0.008439 AND vwap_deviation > -0.018165
- 硬止损: -1.50%

SHORT 出场 Top-3:
- C1: oi_change_rate_5m < 0.805410 AND volume_autocorr_lag5 < -0.013628
- C2: oi_change_rate_5m < 0.805410 AND volume_autocorr_lag5 < -0.013628 AND amplitude_1m < 0.239284
- C3: oi_change_rate_1h < -0.066308
- 论文失效: vwap_deviation_vs_entry > 0.003（偏离反向扩大 = 假设错误）
- 硬止损: -1.00%

### P1-9: 极端位置 + 量能枯竭（持续性门控）

**物理机制**: 价格在极端位置（24h 高位或低位）且成交量持续低迷。注释称"价格压缩"，但代码实际用的是 vol_drought_count（成交量枯竭块数）作为门控条件。这是一个已确认的代码行为：`comp10, _ = compute_state_blocks(df, 10)` 取的是第一个返回值 vol_drought_count。在实盘中该策略持续盈利，说明 vol_drought 在极端位置上与 compression 高度相关。

**入场条件（注意：实际用 vol_drought_blocks，不是 compression_blocks）**:

| Variant | 条件 | OOS 胜率 | OOS n |
|---------|------|---------|-------|
| SHORT-A | position_in_range_24h > 0.933835 AND vol_drought_blocks_10m >= 8 | 86% | 28 |
| SHORT-B | position_in_range_4h > 0.980368 AND vol_drought_blocks_5m >= 6 | 87% | 46 |
| LONG-A | dist_to_24h_high < -0.060173 AND vol_drought_blocks_10m >= 8 | 90% | 29 |
| LONG-B | vwap_deviation < -0.023646 AND vol_drought_blocks_10m >= 6 | 90% | 30 |

**出场条件**:

LONG 出场:
- C1: taker_buy_sell_ratio_vs_entry > 0.004978 AND volume_autocorr_lag5 > 0.004775
- 硬止损: -0.30%

SHORT 出场:
- C1: position_in_range_24h < 0.70（价格离开高位区）
- C2: amplitude_1m > 0.50（振幅爆发 = 能量释放）
- 硬止损: -0.50%

### P1-10: 主动卖耗尽 + 价格位置

**物理机制**:
- LONG: 价格在 24h 最低点附近但主动卖单极少 = 空头已耗尽
- SHORT: 价格严重偏离 VWAP 向上且主动买盘急速萎缩 = 追涨力量衰竭

**入场条件**:

| Variant | 条件 | OOS 胜率 | OOS n |
|---------|------|---------|-------|
| A (LONG) | dist_to_24h_low < 0.001099 AND taker_buy_sell_ratio < 0.206743 | 80% | 35 |
| B (LONG) | position_in_range_24h < 0.041596 AND taker_ratio_delta5 > 2.271699 | 77% | 26 |
| D (SHORT) | vwap_deviation > 0.020180 AND taker_ratio_delta5 < -1.092845 | 80% | 30 |

**taker_ratio_delta5 定义**: 当前 taker_buy_sell_ratio 减去 5 bar 前的值。正值=买方力量激增，负值=买方力量萎缩。

**P1-10 SHORT 趋势守卫**: Variant D 有额外保护——如果 20-bar close 斜率为正（上升趋势中），则要求 position_in_range_4h >= 0.70（必须在高位区）。在上升趋势中段的"买方萎缩"是假信号，只有在顶部区域才有效。

**出场条件**:

LONG 出场:
- C1: position_in_range_4h > 0.556422 AND vwap_deviation_vs_entry > 0.003512
- C2: position_in_range_4h_vs_entry > 0.492069 AND vwap_deviation_vs_entry > 0.003512
- 硬止损: -1.00%

SHORT 出场:
- C1: vwap_deviation_vs_entry < -0.008（VWAP 偏离回归）
- C2: position_in_range_4h < 0.50（价格离开高位区）
- 论文失效: vwap_deviation_vs_entry > 0.003
- 硬止损: -0.50%

### P1-11: 高位 + 负资金费率

**物理机制**: 价格在 4h 高位区但资金费率为负 = 多头不愿持仓，上涨不是真实需求驱动。做市商开始卸载时价格快速下跌。

**入场条件**:

| Variant | 条件 | OOS 胜率 | OOS n |
|---------|------|---------|-------|
| A (SHORT) | position_in_range_4h > 0.980368 AND funding_rate < -0.000025 | 80.4% | 51 |
| B (SHORT) | position_in_range_4h > 0.991977 AND funding_rate < -0.000034 | 高置信度 | - |

**出场条件**:

SHORT 出场:
- C1: taker_buy_pct_vs_entry < -0.003398 AND volume_vs_ma20_vs_entry < -0.003582 AND volume_autocorr_lag5 < -0.013463
- C2: taker_buy_pct_vs_entry < -0.003398 AND volume_autocorr_lag5 < -0.013463
- 论文失效: vwap_deviation_vs_entry > 0.003
- 硬止损: -1.50%

---

## 五、MFE 峰值出场挖掘方法论

这是 P 系列出场条件的发现方法，Alpha 引擎必须复制这个方法。

### 算法步骤

1. **收集入场点**: 在 OOS 数据上，找到所有入场信号触发的 bar
2. **逐 bar 追踪 MFE**: 对每个入场点，计算持有期间的最大有利偏移（MFE）
   - LONG: MFE = max(close[t+1..t+H] / close[t] - 1)
   - SHORT: MFE = max(1 - close[t+1..t+H] / close[t])
3. **找 MFE 峰值 bar**: 利润最高的那根 K 线
4. **在峰值附近收集 vs_entry**: 峰值 bar 前后 2-3 根 K 线，计算所有特征的 vs_entry 值（当前值 - 入场快照值）
5. **对比"好出场"vs"坏出场"**: 峰值附近 = 好出场时机，远离峰值 = 坏出场时机
6. **找区分特征**: 用 Cohen's d 或 Spearman 相关，找到在好/坏出场之间有显著差异的 vs_entry 特征
7. **组合 Top-3**: 选区分力最强的 2-3 个特征组合，冻结阈值
8. **验证捕获率**: 这些条件在 OOS 上能捕获多少比例的 MFE

### 关键约束

- 出场条件**必须**用 vs_entry（相对入场的变化量），不能用绝对值
- 至少一个条件必须包含入场种子特征的 vs_entry
- 阈值从数据中挖掘，**不能**用公式推导（如 threshold * 0.7）
- Top-3 组合内是 AND 逻辑，组合之间是 OR 逻辑（任一命中即出场）

---

## 六、8 层出场优先级瀑布

每根 K 线按以下顺序检查，命中即停：

| 优先级 | 层 | 逻辑 | 参数 |
|--------|-----|------|------|
| 1 | 自适应硬止损（含 MFE 棘轮） | effective_stop = stop_pct * conf_mult * regime_mult; 若 MFE > 0.15 则 min(effective_stop, MFE * 0.40); adverse >= effective_stop 即出场 | stop_pct 因策略而异 |
| 2 | 止盈 | current_return >= take_profit_pct（如配置） | 默认 0（不启用） |
| 3 | P1-8 SHORT 特殊模式 | take_profit_pct > 0 时走 TP/SL 模式，不走动态出场 | 仅 P1-8 SHORT |
| 4 | 最低持仓保护 | bars_held < min_hold_bars 时不允许动态出场 | 默认 3 bar |
| 5 | 机制生命周期 | decay_score >= 0.85 出场；>= 0.5 收紧保护（gap_ratio 降至 tighten_gap_ratio） | decay_exit=0.85, tighten=0.50 |
| 6 | 利润保护追踪止损 | MFE >= protect_start_pct 时激活; floor = max(protect_floor_pct, MFE - MFE * gap_ratio); return <= floor 出场 | protect_start=0.12, gap=0.50 |
| 7 | 智能出场（vs_entry Top-3）+ 确认防抖 | Top-3 combo 命中 + current_return > 0; logic_complete 等原因需 exit_confirm_bars 连续确认才出场; thesis_invalidated/hard_stop/profit_protect 立即出场不防抖 | 默认确认 2 bar |
| 8 | safety_cap | 以上全未触发时的时间安全网 | max(family_cap, horizon * max_hold_factor) |

### ExitParams 完整参数

```python
ExitParams(
    take_profit_pct=0.0,           # 止盈百分比（0=不启用）
    stop_pct=0.70,                 # 基础硬止损
    protect_start_pct=0.12,        # MFE 达此值启动利润保护
    protect_gap_ratio=0.50,        # 追踪止损：floor = MFE - MFE * ratio
    protect_floor_pct=0.03,        # 绝对最低保本线
    min_hold_bars=3,               # 最少持仓 bar 数
    max_hold_factor=4,             # safety_cap 乘数
    exit_confirm_bars=2,           # 统计出场防抖确认
    decay_exit_threshold=0.85,     # 机制衰竭出场分
    decay_tighten_threshold=0.50,  # 机制衰竭收紧保护分
    tighten_gap_ratio=0.30,        # 收紧后的追踪比率
    mfe_ratchet_threshold=0.15,    # MFE 棘轮启动阈值
    mfe_ratchet_ratio=0.40,        # MFE 棘轮比率
    confidence_stop_multipliers={1: 0.7, 2: 1.0, 3: 1.3},
    regime_stop_multipliers={
        "QUIET_TREND": 0.8, "RANGE_BOUND": 1.0,
        "VOLATILE_TREND": 1.5, "VOL_EXPANSION": 1.5, "CRISIS": 0.5,
    },
    regime_stop_multipliers_short={
        "QUIET_TREND": 0.6, "RANGE_BOUND": 1.0,
        "VOLATILE_TREND": 1.5, "VOL_EXPANSION": 1.5, "CRISIS": 0.5,
    },
)
```

---

## 七、质量门槛

所有策略必须满足以下全部门槛才能进入待审核池：

| 指标 | 门槛 | 说明 |
|------|------|------|
| OOS 胜率（扣费后） | >= 65% | 扣除 Maker 0.04% 来回后的净胜率 |
| MFE 覆盖率 | >= 75% | 75% 的信号入场后价格至少往有利方向走超过费用 0.04% |
| OOS 样本数 | >= 30 | 不可妥协 |
| 降级比（OOS_ICIR / IS_ICIR） | > 0.5 | OOS 必须保留 IS 50% 以上的预测力 |
| OOS 净收益 | > 0% | 扣费后必须赚钱 |
| 确认因子方向性 | Spearman >= 0.005 | 涨跌都触发的因子直接拒绝 |
| 回测 PF | > 1.0 | 智能出场回测的盈亏比必须大于 1 |

费用计算：Maker 费率 = 0.02% 单边，来回 0.04%。

---

## 八、10 维特征体系

### PRICE 维度
| 特征 | 物理含义 |
|------|---------|
| vwap_deviation | 价格偏离 24h 成交量加权均价的比例。正=偏高，负=偏低 |
| position_in_range_24h | 价格在 24h 高低区间的相对位置。0=最低点，1=最高点 |
| position_in_range_4h | 价格在 4h 高低区间的相对位置 |
| dist_to_24h_high | 价格距 24h 最高点的相对距离（负值=低于高点） |
| dist_to_24h_low | 价格距 24h 最低点的相对距离（正值=高于低点） |
| amplitude_1m | 1 分钟 K 线振幅 (high-low)/close |
| amplitude_ma20 | 振幅的 20 bar 移动平均 |

### TRADE_FLOW 维度
| 特征 | 物理含义 |
|------|---------|
| taker_buy_sell_ratio | 主动买/主动卖比率。>1=买方主导，<1=卖方主导 |
| taker_buy_pct | 主动买占总成交的百分比 |
| volume_vs_ma20 | 成交量相对 20 bar 均量的比率。>1=放量，<1=缩量 |
| avg_trade_size | 平均单笔成交额 |
| volume_acceleration | 成交量二阶导（加速/减速） |
| volume_autocorr_lag5 | 成交量 5 bar 自相关（节奏一致性） |
| avg_trade_size_cv_10m | 10 分钟内单笔规模的变异系数（拆单痕迹） |

### LIQUIDITY 维度
| 特征 | 物理含义 |
|------|---------|
| kyle_lambda | Kyle 价格冲击系数。高=流动性差，低=流动性好 |
| spread_vs_ma20 | 价差相对 20 bar 均值。>1=价差扩大，<1=收窄 |
| spread_proxy | 实际价差的代理估计 |

### POSITIONING 维度
| 特征 | 物理含义 |
|------|---------|
| oi_change_rate_5m | 持仓量 5 分钟变化率 |
| oi_change_rate_1h | 持仓量 1 小时变化率 |
| funding_rate | 资金费率。正=多头付费，负=空头付费 |
| ls_ratio_change_5m | 多空比 5 分钟变化 |
| consecutive_extreme_funding | 连续极端资金费率计数 |

### MICROSTRUCTURE 维度（需 book_ticker 数据）
| 特征 | 物理含义 |
|------|---------|
| quote_imbalance | 盘口买卖挂单不平衡度 |
| bid_depth_ratio | 买盘深度占比 |
| spread_anomaly | 价差异常度（相对 MA20） |

### ORDER_FLOW 维度（需 agg_trades 数据）
| 特征 | 物理含义 |
|------|---------|
| large_trade_buy_ratio | 大单中买单占比 |
| direction_net_1m | 1 分钟净流量方向 |
| sell_notional_share_1m | 卖方成交额占比 |
| trade_burst_index | 成交突发指数 |
| direction_autocorr | 方向自相关（趋势/反转指标） |

### MARK_PRICE 维度（需 mark_price 数据）
| 特征 | 物理含义 |
|------|---------|
| rt_funding_rate | 实时资金费率（非结算后） |
| mark_basis | 标记价与最新价的基差 |
| mark_basis_ma10 | 基差 10 bar 均值 |

### LIQUIDATION 维度（需 liquidation 数据）
| 特征 | 物理含义 |
|------|---------|
| btc_liq_net_pressure | 爆仓净方向压力 |
| total_liq_usd_5m | 5 分钟爆仓总额（美元） |
| liq_size_p90_5m | 5 分钟爆仓规模 p90 |

### BLOCK STATE 持续性特征
| 特征 | 物理含义 |
|------|---------|
| vol_drought_blocks_5m | 连续 5 分钟量能枯竭块数 |
| vol_drought_blocks_10m | 连续 10 分钟量能枯竭块数 |
| price_compression_blocks_5m | 连续 5 分钟价格压缩块数 |
| price_compression_blocks_10m | 连续 10 分钟价格压缩块数 |

---

## 九、Alpha 引擎策略卡片格式

Kimi 发现的策略最终输出为策略卡片 JSON，存入 pending_rules.json，格式必须与以下结构兼容：

```json
{
  "id": "唯一ID",
  "rule_str": "人类可读规则描述",
  "entry": {
    "feature": "入场种子特征名",
    "operator": "< 或 >",
    "threshold": 0.123456,
    "direction": "long 或 short",
    "horizon": 30
  },
  "combo_conditions": [
    {"feature": "确认特征", "op": "< 或 >", "threshold": 0.123}
  ],
  "family": "A6-xxx",
  "exit": {
    "top3": [
      {
        "conditions": [
          {"feature": "feat_vs_entry", "operator": ">", "threshold": 0.01}
        ],
        "combo_label": "C1",
        "description": "描述"
      }
    ],
    "invalidation": [
      {
        "conditions": [
          {"feature": "seed_feat_vs_entry", "operator": "<", "threshold": -0.005}
        ],
        "combo_label": "I1",
        "description": "论文失效: 入场力反向扩大"
      }
    ],
    "exit_method": "mfe_peak_mined"
  },
  "exit_params": {
    "stop_pct": 0.70,
    "protect_start_pct": 0.12,
    "protect_gap_ratio": 0.50,
    "protect_floor_pct": 0.03,
    "min_hold_bars": 3,
    "max_hold_factor": 4,
    "exit_confirm_bars": 2
  },
  "stats": {
    "oos_win_rate": 72.5,
    "n_oos": 40,
    "oos_avg_ret": 0.085,
    "oos_pf": 1.85,
    "mfe_coverage": 82.0,
    "degradation": 0.72
  },
  "mechanism_type": "物理机制ID",
  "origin": "kimi_researcher",
  "status": "pending"
}
```

运行时出场通过 `smart_exit_policy._eval_alpha_card()` 评估：
- 先检查 invalidation combos（论文失效 → 立即出场）
- 再检查 exit top3 combos（力消失 → 正常出场）
- 都不命中 → 继续持有

---

## 十、禁令

| 禁令 | 原因 |
|------|------|
| 禁止 TIME 维度特征作为入场条件 | 时间季节性是统计伪相关 |
| 禁止单 bar 阈值穿越作为入场 | 必须有持续性（block-state 或 multi-bar） |
| 禁止公式推导出场条件 | 必须用 MFE 峰值挖掘 |
| 禁止硬编码止损参数 | 必须通过网格扫描从数据得出 |
| 禁止不扣费的胜率 | 所有指标必须扣除 Maker 0.04% 来回 |
| 禁止 n_oos < 30 的策略 | 统计不显著 |
| 禁止净收益 < 0.03% 的策略 | 收益太小不值得交易，扣除滑点后可能为负 |
| 禁止只有做空没有做多 | 必须双向都有策略，单边风险太大 |

---

## 十一、参数优化实操方法论（Alpha 引擎的 Kimi 必须掌握）

这是从手动验证中总结出的完整参数优化流程。Kimi 必须自主执行这套流程。

### 第 1 步: 入场阈值网格扫描

对每个假设的入场特征，引擎自动扫描极端分位数：

```
方向  操作符  扫描分位数
LONG    <    p1, p2, p3, p5
SHORT   >    p95, p97, p98, p99
双向都要  <+>  上下都扫
```

每个阈值用 crossing detection + 60 bar cooldown，模拟实际交易。
在 OOS（后 33%）上计算 WR、PF、n、avg_return。
选 WR 最高且 n >= 30 的组合。

### 第 2 步: 双特征组合扫描

在第 1 步的最佳种子上叠加确认特征：

```
种子: feature_A > p95
确认: feature_B < p5 (或 > p95)
组合: 种子 crossing AND 确认同时满足
```

确认特征必须来自不同维度（TRADE_FLOW 确认 PRICE，POSITIONING 确认 LIQUIDITY）。

### 第 3 步: 出场条件阈值扫描（关键！）

不用 MFE 峰值挖掘的原始结果——那些阈值通常太小、太激进。
改为网格扫描 vs_entry 出场阈值：

```
入场特征 vs_entry 阈值扫描:
  LONG 出场: feat_vs_entry > [0.005, 0.008, 0.010, 0.015, 0.020, 0.030]
  SHORT 出场: feat_vs_entry < [-0.005, -0.008, -0.010, -0.015, -0.020, -0.030]

辅助特征 vs_entry 阈值:
  position_in_range vs_entry: [-0.05, -0.10, -0.15, -0.20, -0.30]
  vwap_deviation vs_entry: [-0.003, -0.005, -0.008, -0.010, -0.015]
```

每个组合跑 8 层智能出场回测，选 PF 最高且 net > 0.03% 的参数。

### 第 4 步: 止损/保护参数扫描

```
止损 stop_pct: [0.30, 0.50, 0.70, 1.00, 1.50]
保护启动 protect_start_pct: [0.04, 0.08, 0.12, 0.15, 0.20]
```

组合扫描 5 x 5 = 25 种参数，选 PF 最高且净收益 > 0.03% 的。

### 第 5 步: 收益门槛

最终策略必须满足：
- OOS WR >= 65%（扣费后）
- OOS n >= 30
- OOS 净收益 >= 0.03%（每笔至少赚 3 个基点，否则不值得交易）
- PF >= 1.2（赢的要比亏的多 20%）
- MFE 覆盖率 >= 75%
- **LONG 和 SHORT 方向都有策略产出**

### 第 6 步: 迭代

如果第一轮没通过，Kimi 应该：
1. 收紧入场阈值（从 p95 收到 p98，减少噪声）
2. 放宽出场阈值（让利润跑得更远）
3. 加宽止损（从 0.5% 加到 1.0%，减少被震出去）
4. 换一组特征重新扫

最多迭代 3 轮。3 轮还不过就放弃这个假设，换下一个。
