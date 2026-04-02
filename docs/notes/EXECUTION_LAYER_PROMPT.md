# 执行层开发任务书

## 背景（必读）

这是一个 BTC 永续合约量化交易系统。信号检测层已经完成，现在需要开发**执行层**。

系统已有的部分：
- `monitor/signal_runner.py`：信号调度器，每根 1 分钟 K 线跑一次，输出触发的信号
- `monitor/live_engine.py`：实时特征引擎，接收 WebSocket 数据，维护滑动窗口
- `run_monitor.py`：主入口，已在跑 WebSocket + REST

信号输出格式（`signal_runner.run(df)` 返回的每个 alert dict）：
```python
{
    "phase":            "P1",
    "name":             "P1-8_vwap_vol_drought",
    "direction":        "short",          # "long" 或 "short"
    "horizon":          10,               # 建议持仓分钟数
    "timestamp_ms":     1704067200000,    # 信号触发时间戳（毫秒）
    "confidence":       3,               # 1=LOW 2=MEDIUM 3=HIGH
    "confidence_label": "HIGH",
    "apply_fatigue":    False,
    "feature":          "vwap_deviation",
    "feature_value":    0.0215,
}
```

---

## 你要做的事：执行层

### 核心原则（不能违反）
1. **只用限价单（Limit Order）**，绝对不用市价单。做市商费率 0.04%，市价单 0.10%，差距是利润的关键。
2. **先跑 Binance Futures Testnet**，不碰真实资金。Testnet URL: `https://testnet.binancefuture.com`
3. 每条策略**最大持仓 1 个仓位**，同一方向已有持仓时不重复开仓。
4. 入场限价单挂在**当前 best bid/ask 附近**（做多挂 bid+1tick，做空挂 ask-1tick）。
5. 限价单**挂单超过 30 秒未成交则撤销**，本次信号作废。

---

### 需要创建的文件

#### `execution/order_manager.py`
负责下单、撤单、查询持仓。

```python
# 需要实现的接口
class OrderManager:
    def __init__(self, api_key: str, api_secret: str, testnet: bool = True): ...

    def place_limit_entry(self, direction: str, qty: float, price: float,
                          signal_name: str, horizon_min: int) -> dict:
        """
        挂限价入场单。
        - direction: "long" → BUY, "short" → SELL
        - qty: 合约数量（BTC，如 0.001）
        - price: 限价价格
        - signal_name: 用于日志追踪
        - horizon_min: 持仓时间（分钟），到时间自动平仓
        返回: {"order_id": ..., "status": "placed"/"rejected"}
        """

    def cancel_order(self, order_id: str) -> bool: ...

    def close_position(self, direction: str, qty: float) -> dict:
        """市价平仓（仅平仓允许市价）"""

    def get_open_positions(self) -> list[dict]: ...

    def get_best_price(self, direction: str) -> float:
        """做多返回 best_bid+1tick，做空返回 best_ask-1tick"""
```

#### `execution/execution_engine.py`
桥接信号层和订单层。

```python
class ExecutionEngine:
    def __init__(self, order_manager: OrderManager,
                 qty_per_trade: float = 0.001,
                 min_confidence: int = 2): ...

    def on_signal(self, alert: dict) -> None:
        """
        收到信号后的处理逻辑：
        1. confidence < min_confidence → 忽略
        2. 同方向已有仓位 → 忽略
        3. 获取最优限价 → 挂单
        4. 启动计时器：30秒未成交 → 撤单
        5. 成交后启动持仓计时器：horizon_min 分钟后平仓
        6. 记录所有操作到日志
        """

    def on_bar(self, df: pd.DataFrame) -> None:
        """每根 K 线调用一次，检查持仓超时"""
```

#### `execution/trade_logger.py`
记录每笔交易，供后续分析。

CSV 格式（`execution/logs/trades.csv`）：
```
signal_name, direction, entry_time, entry_price, exit_time, exit_price,
qty, gross_return_pct, net_return_pct, exit_reason, confidence, horizon_min
```
`exit_reason`: `filled_timeout`（持仓到期）/ `not_filled`（未成交撤单）/ `manual`

---

### 接入现有系统

在 `run_monitor.py` 的主循环里，信号触发后调用 `ExecutionEngine.on_signal(alert)`：

```python
# run_monitor.py 里已有类似这段逻辑：
raw_alerts, composite_alerts = runner.run(df)
for alert in composite_alerts:
    alert_handler.handle(alert)
    # ↓ 新增这一行
    execution_engine.on_signal(alert)
```

---

### 配置文件

新建 `execution/config.py`：
```python
TESTNET         = True
API_KEY         = ""          # 从环境变量读取，不要硬编码
API_SECRET      = ""
SYMBOL          = "BTCUSDT"
QTY_PER_TRADE   = 0.001       # 每笔 0.001 BTC
MIN_CONFIDENCE  = 2           # 只执行 MEDIUM 及以上信号
ENTRY_TIMEOUT_S = 30          # 限价单挂单超时秒数
FEE_RATE        = 0.0004      # maker 0.04%
```

---

### 不要碰的文件
- `signals/` 目录下所有文件
- `monitor/signal_runner.py`
- `monitor/live_engine.py`
- `monitor/alpha_rules.py`
- `core/` 目录

---

### 完成标准
1. `python run_monitor.py` 启动后，信号触发时自动在 testnet 挂限价单
2. 30秒未成交自动撤单，有日志记录
3. 成交后按 `horizon_min` 分钟到期自动平仓
4. `execution/logs/trades.csv` 有完整记录
5. 不会出现同时持有两个同方向仓位的情况

---

### 项目结构（执行层新增部分）
```
execution/
├── __init__.py
├── config.py
├── order_manager.py     ← Binance API 封装
├── execution_engine.py  ← 信号→订单桥接
├── trade_logger.py      ← CSV 记录
└── logs/
    └── trades.csv
```

---

### 参考：Binance Futures Testnet API

Python 库：`python-binance` 或直接用 `requests`

关键接口：
- 下单：`POST /fapi/v1/order`，`type=LIMIT`，`timeInForce=GTC`
- 撤单：`DELETE /fapi/v1/order`
- 查持仓：`GET /fapi/v2/positionRisk`
- 查最优价：`GET /fapi/v1/ticker/bookTicker`

Testnet base URL: `https://testnet.binancefuture.com`

申请 testnet API key: https://testnet.binancefuture.com（注册后在账户页面生成）
