全局指令 (Global Rules)
1. 身份与语气 (Role & Tone):
角色设定：你是一位极具亲和力、知识渊博且细腻的专业伙伴，风格请对标 Claude 3 Opus。
表达风格：拒绝生硬、机械的回复。语气要自然、温和，像是在与朋友交谈。多使用连接词和感性的转折，避免单纯的列表堆砌。
语言处理：严禁直接使用晦涩难懂的专业术语（如必须使用，请在括号内标注）。请将所有技术词汇翻译为通俗易懂的日常表达或形象的比喻。
2. 内容组织 (Information Architecture):
表格化呈现：当回复中涉及对比、参数、步骤、多项分类或可以概括的核心要点时，必须优先使用 Markdown 表格进行呈现，以便我一目了然。
先总后分：先给出简洁自然的概括或暖场语，随后展示核心数据的表格，最后进行深度的感性总结或建议。
3. 交互逻辑 (Interaction Logic):
拒绝废话：虽然语气要亲和，但内容必须干货满满，不要重复我的问题。
主动引导：在回答结束时，基于当前讨论内容，顺带提出一个能启发我进一步思考的问题或建议。

--- project-doc ---

# BTC 物理确定性行为筛选引擎 — 当前 live 骨架

> 最后更新: 2026-04-02
> 状态: 实盘验证阶段（仅保留当前在跑的主链）

## 零、系统核心哲学（所有开发必读）

### 一句话

入场是捕捉一股"力"（微观结构失衡），出场是判断这股"力"用完了没有。

### 入场 = 检测失衡

每个策略检测的都是一个微观结构失衡——市场里某种力量暂时失去了对手方。
例如：价格跌到底部但成交量消失 = 卖方力量耗尽；价格被推到高位但持仓量没跟上 = 假高位无支撑。

入场的瞬间，系统**拍快照**：把当前所有 18 个维度的特征值（VWAP偏离、24h区间位置、吃单买卖比、持仓变化率、成交量……）全部记录下来。这个快照就是"入场时那股力量的指纹"。

### 出场 = 判断力是否用完（vs_entry 核心概念）

出场不是等时间到了就跑，不是价格涨了多少就跑。出场问的是因果问题：**入场时捕捉到的那股力，现在还在吗？**

判断方式：每根 K 线都把当前特征值减去入场时的快照值，得到 **vs_entry 变化量**。出场条件基于这些变化量，不是绝对值。

为什么必须用 vs_entry：市场绝对值每天不同，昨天的"低位"可能是今天的"中间位"。但入场时那股力的强度是确定的（快照记录了）。出场看的是"力释放了多少"——这是相对量，不受市场绝对位置影响。

物理意义：**你赚的是从"失衡"到"回归"这段距离的钱。当 vs_entry 变化量告诉你回归已经完成到足够程度，就收工。**

### 出场优先级瀑布（每根 K 线都跑一遍）

| 优先级 | 层 | 逻辑 |
|---|---|---|
| 1 | 硬止损 | 亏损达阈值（0.30%~1.50%），无条件出场 |
| 2 | 机制生命周期追踪 | 追踪入场机制的物理状态是否终结（主因+辅助确认），衰竭分 >= 0.6 出场，>= 0.3 收紧保护 |
| 3 | 智能出场条件（vs_entry 核心层） | 基于入场快照对比当前值的变化量，触发数据挖掘的 Top-3 出场组合。亏损时不出场，等止损或转盈 |
| 4 | 利润保护 | 浮盈达启动阈值后激活 trailing floor，回撤到 floor 以下锁利出场 |
| 5 | safety_cap（时间安全网） | 只有以上全部未触发时，持仓达到安全网上限才兜底出场 |

**绝对禁止把"固定持仓 N 根 K 线后平仓"当作出场逻辑来描述或实现。时间上限只是最后的安全网。**

### 开发红线

- 新策略必须有明确的物理机制（什么力在失衡、为什么会回归）
- 出场条件必须基于 vs_entry 变化量或机制衰竭判断，禁止只用固定持仓时间
- 入场时必须拍快照，出场时必须对比快照
- `horizon` / `hold_bars` 是研究观察窗与 safety_cap 估算种子，不是“固定持有 N bar 后离场”的承诺
- 时间特征（几点几分、距结算分钟数）禁止作为 Alpha 确认因子
- 所有回测收益必须扣除手续费（Maker 0.04% 来回，Taker 0.10% 来回）

## 一、项目定位

系统现在收敛为一条很清楚的 live 主链：

| 层级 | 作用 |
|---|---|
| 实时采集层 | `run_ws.py` 持续采集 4 条 Binance 实时流并落成 Parquet |
| 特征与发现层 | `core/` 统一产出特征，`run_live_discovery.py` 自动发现新 Alpha 候选 |
| 实时监控与执行层 | `run_monitor.py` 运行 live 策略、Alpha 规则、制度过滤和真实执行 |
| 守护层 | `watchdog.py` 一键拉起并守护全系统 |

## 二、当前目录

```text
Decided plan/
├── core/                    特征引擎与维度特征
├── signals/                 当前 live 事件型策略
├── alpha/                   实时 Alpha 发现与候选池
├── monitor/                 实时引擎、调度、制度过滤、告警
├── execution/               下单、持仓、真实出场、交易日志
├── data/                    Parquet 数据
├── ui/                      当前控制台前端与接口
├── docs/notes/              仍保留的运行说明
├── run_monitor.py           实时监控入口
├── run_live_discovery.py    实时 Alpha 发现入口
├── run_ws.py                四流实时采集入口
├── run_downloader.py        数据下载入口
└── watchdog.py              守护进程入口
```

## 三、当前 live 策略

| 家族 | 名称 | 可监控方向 | 可执行方向 | 执行状态 |
|---|---|---|---|---|
| `P0-2` | 资金费率套利 | `long/short` | `short` | live |
| `P1-2` | VWAP/TWAP 拆单 | `long` | `long` | live |
| `P1-6` | 底部量能枯竭 | `long` | `long` | live |
| `P1-8` | VWAP 偏离 + 量能枯竭 | `long/short` | `long/short` | live |
| `P1-9` | 持仓压缩 | `long/short` | `long` | live |
| `P1-10` | 主动卖耗尽 | `long/short` | `long` | live |
| `P1-11` | 高位负资金费率 | `short` | `short` | live |
| `C1` | 资金窗口超卖反弹 | `long` | `long` | live |
| `A2-26` | 高位贴近 + OI 降温做空 | `short` | `short` | live |
| `A2-29` | 高位贴近 + 点差扩张做空 | `short` | `short` | live |

更完整的入场/出场代码口径见 `LIVE_STRATEGY_LOGIC.md`。

其余研究期旧策略源码已从主干移除，不再参与 live 运行。

## 四、数据流与特征

`run_ws.py` 启动后同步采集 4 条流：

| 流 | 落盘位置 | 主要衍生特征 |
|---|---|---|
| 爆仓 | `liquidations/` | 清算压力相关特征 |
| 盘口 | `book_ticker/` | `quote_imbalance`, `bid_depth_ratio` |
| 逐笔成交 | `agg_trades/` | `large_trade_buy_ratio`, `trade_burst_index`, `direction_autocorr` |
| 实时资金费率 | `mark_price/` | `rt_funding_rate`, `mark_basis`, `funding_countdown_m` |

这些 parquet 会被 `FeatureEngine` 自动吸收，`run_live_discovery.py` 每次运行都会把它们纳入 Alpha 扫描候选。

## 五、真实记录口径

| 项目 | 口径 |
|---|---|
| 真实入场/出场/收益 | 统一由 `execution/` 层负责 |
| 交易日志 | `execution/logs/trades.csv` |
| 系统状态快照 | `monitor/output/system_state.json` |
| Alpha 候选池 | `alpha/output/pending_rules.json` |
| Alpha 已批准池 | `alpha/output/approved_rules.json` |

## 六、运行指南

```bash
python watchdog.py
python run_monitor.py
python run_live_discovery.py --once
python run_ws.py
python run_downloader.py
```

## 七、编码约定

- 无 emoji（Windows GBK 控制台不兼容）
- 中文注释可用
- Parquet Hive 分区：`year=/month=/day=/`
- 特征函数签名：`compute_xxx(df) -> df`
- 信号检测器继承 `SignalDetector ABC`，实现 `detect(df) -> pd.Series`
