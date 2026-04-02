// Mock data for development and demonstration
// All timestamps are UTC

export const MOCK_TRADING_PAIRS = [
  { symbol: "BTCUSDT", baseAsset: "BTC", quoteAsset: "USDT", isTracked: true, dataCollectionStatus: "completed" as const, dataDownloadProgress: 100, alphaEngineStatus: "completed" as const, totalKlines: 525600, dataQualityScore: 98.5, currentPrice: "83245.50", priceChange24h: 2.34, volume24h: "2847392847.00", lastDataUpdate: new Date() },
  { symbol: "ETHUSDT", baseAsset: "ETH", quoteAsset: "USDT", isTracked: true, dataCollectionStatus: "completed" as const, dataDownloadProgress: 100, alphaEngineStatus: "scanning" as const, totalKlines: 525600, dataQualityScore: 97.2, currentPrice: "1876.30", priceChange24h: -1.12, volume24h: "1234567890.00", lastDataUpdate: new Date() },
  { symbol: "SOLUSDT", baseAsset: "SOL", quoteAsset: "USDT", isTracked: true, dataCollectionStatus: "downloading" as const, dataDownloadProgress: 67, alphaEngineStatus: "idle" as const, totalKlines: 350000, dataQualityScore: 85.0, currentPrice: "132.45", priceChange24h: 5.67, volume24h: "987654321.00", lastDataUpdate: new Date() },
  { symbol: "BNBUSDT", baseAsset: "BNB", quoteAsset: "USDT", isTracked: false, dataCollectionStatus: "pending" as const, dataDownloadProgress: 0, alphaEngineStatus: "idle" as const, totalKlines: 0, dataQualityScore: 0, currentPrice: "598.20", priceChange24h: 0.89, volume24h: "456789012.00", lastDataUpdate: undefined },
];

export const MOCK_STRATEGIES = [
  {
    strategyId: "P0-2", name: "资金费率套利", type: "P1" as const, direction: "SHORT" as const, symbol: "BTCUSDT",
    entryCondition: "funding_rate > 0.01%",
    exitConditionTop3: [
      { feature: "funding_countdown_m", op: "<", threshold: 5, label: "资金费率倒计时 < 5min" },
      { feature: "mark_basis_ma10", op: ">", threshold: 0.003, label: "基差MA10 > 0.003" },
      { feature: "direction_autocorr", op: "<", threshold: -0.3, label: "方向自相关 < -0.3" }
    ],
    oosWinRate: 78.5, oosAvgReturn: 0.0312, isSampleSize: 156, oosSampleSize: 52,
    overfitScore: 0.12, featureDiversityScore: 0.65, confidenceScore: 82.3,
    triggerCount7d: 8, tradeCount7d: 6, pnl7d: "234.50", totalTrades: 89, totalPnl: "2847.30",
    status: "active" as const, discoveredAt: new Date("2024-01-15"), approvedAt: new Date("2024-01-15"),
    backtestStatus: "completed" as const,
    backtestResult: { equity_curve: [100, 102, 105, 103, 108, 112, 115], sharpe: 2.1, max_drawdown: -3.2 },
    tags: ["funding_rate", "short_bias", "high_frequency"]
  },
  {
    strategyId: "P1-2", name: "VWAP偏离拆单", type: "P1" as const, direction: "LONG" as const, symbol: "BTCUSDT",
    entryCondition: "vwap_deviation < -0.56% AND volume_vs_ma20 > 1.5",
    exitConditionTop3: [
      { feature: "vwap_deviation", op: ">", threshold: 0.1, label: "VWAP偏离恢复 > 0.1%" },
      { feature: "volume_vs_ma20", op: "<", threshold: 0.8, label: "成交量回落 < MA20*0.8" },
      { feature: "mark_basis_ma10", op: ">", threshold: 0.005, label: "基差MA10 > 0.005" }
    ],
    oosWinRate: 71.2, oosAvgReturn: 0.0285, isSampleSize: 134, oosSampleSize: 45,
    overfitScore: 0.18, featureDiversityScore: 0.72, confidenceScore: 74.8,
    triggerCount7d: 5, tradeCount7d: 4, pnl7d: "187.20", totalTrades: 67, totalPnl: "1923.40",
    status: "active" as const, discoveredAt: new Date("2024-01-20"), approvedAt: new Date("2024-01-20"),
    backtestStatus: "completed" as const,
    backtestResult: { equity_curve: [100, 101, 104, 102, 106, 109, 112], sharpe: 1.8, max_drawdown: -4.1 },
    tags: ["vwap", "volume", "long_bias"]
  },
  {
    strategyId: "P1-6", name: "底部量能枯竭", type: "P1" as const, direction: "LONG" as const, symbol: "BTCUSDT",
    entryCondition: "dist_to_24h_low < 1.0% AND volume_vs_ma20 < 0.8",
    exitConditionTop3: [
      { feature: "mark_basis_ma10", op: ">", threshold: 0.005, label: "基差MA10 > 0.005" },
      { feature: "funding_countdown_m", op: "<", threshold: 5, label: "资金费率倒计时 < 5min" },
      { feature: "direction_autocorr", op: "<", threshold: -0.2, label: "方向自相关 < -0.2" }
    ],
    oosWinRate: 75.0, oosAvgReturn: 0.0318, isSampleSize: 98, oosSampleSize: 34,
    overfitScore: 0.15, featureDiversityScore: 0.68, confidenceScore: 78.5,
    triggerCount7d: 5, tradeCount7d: 3, pnl7d: "125.43", totalTrades: 45, totalPnl: "1456.78",
    status: "active" as const, discoveredAt: new Date("2024-02-01"), approvedAt: new Date("2024-02-01"),
    backtestStatus: "completed" as const,
    backtestResult: { equity_curve: [100, 103, 106, 104, 109, 113, 118], sharpe: 2.3, max_drawdown: -2.8 },
    tags: ["volume_exhaustion", "support", "long_bias"]
  },
  {
    strategyId: "P1-3", name: "大单方向突破", type: "P1" as const, direction: "BOTH" as const, symbol: "BTCUSDT",
    entryCondition: "large_order_ratio > 0.35 AND price_momentum_5m > 0.2%",
    exitConditionTop3: [
      { feature: "price_momentum_5m", op: "<", threshold: 0, label: "动量反转" },
      { feature: "large_order_ratio", op: "<", threshold: 0.2, label: "大单比例下降" },
      { feature: "oi_change_rate", op: "<", threshold: -0.01, label: "持仓量下降" }
    ],
    oosWinRate: 63.4, oosAvgReturn: 0.0198, isSampleSize: 187, oosSampleSize: 63,
    overfitScore: 0.22, featureDiversityScore: 0.58, confidenceScore: 65.2,
    triggerCount7d: 12, tradeCount7d: 8, pnl7d: "89.30", totalTrades: 134, totalPnl: "987.60",
    status: "active" as const, discoveredAt: new Date("2024-02-10"), approvedAt: new Date("2024-02-10"),
    backtestStatus: "completed" as const,
    backtestResult: { equity_curve: [100, 101, 103, 102, 104, 106, 108], sharpe: 1.4, max_drawdown: -5.6 },
    tags: ["order_flow", "momentum", "both"]
  },
  {
    strategyId: "P1-7", name: "清算瀑布反转", type: "P1" as const, direction: "LONG" as const, symbol: "BTCUSDT",
    entryCondition: "liquidation_cascade_score > 0.7 AND price_drop_5m < -1.5%",
    exitConditionTop3: [
      { feature: "liquidation_rate", op: "<", threshold: 0.1, label: "清算率回落" },
      { feature: "price_recovery_pct", op: ">", threshold: 0.8, label: "价格恢复80%" },
      { feature: "funding_rate", op: ">", threshold: 0.005, label: "资金费率转正" }
    ],
    oosWinRate: 69.8, oosAvgReturn: 0.0445, isSampleSize: 76, oosSampleSize: 26,
    overfitScore: 0.28, featureDiversityScore: 0.75, confidenceScore: 71.3,
    triggerCount7d: 2, tradeCount7d: 2, pnl7d: "312.80", totalTrades: 28, totalPnl: "2134.50",
    status: "active" as const, discoveredAt: new Date("2024-02-15"), approvedAt: new Date("2024-02-15"),
    backtestStatus: "completed" as const,
    backtestResult: { equity_curve: [100, 104, 108, 106, 112, 118, 124], sharpe: 2.7, max_drawdown: -6.2 },
    tags: ["liquidation", "reversal", "high_return"]
  },
  {
    strategyId: "P2-1", name: "OI变化+VWAP组合", type: "P2" as const, direction: "SHORT" as const, symbol: "BTCUSDT",
    entryCondition: "oi_change_rate > 0.02 AND vwap_deviation > 0.8%",
    exitConditionTop3: [
      { feature: "oi_change_rate", op: "<", threshold: 0, label: "OI开始下降" },
      { feature: "vwap_deviation", op: "<", threshold: 0.2, label: "VWAP偏离收窄" },
      { feature: "funding_rate", op: "<", threshold: 0, label: "资金费率转负" }
    ],
    oosWinRate: 66.7, oosAvgReturn: 0.0267, isSampleSize: 89, oosSampleSize: 30,
    overfitScore: 0.19, featureDiversityScore: 0.62, confidenceScore: 68.9,
    triggerCount7d: 4, tradeCount7d: 3, pnl7d: "98.70", totalTrades: 56, totalPnl: "1234.50",
    status: "active" as const, discoveredAt: new Date("2024-03-01"), approvedAt: new Date("2024-03-02"),
    backtestStatus: "completed" as const,
    backtestResult: { equity_curve: [100, 102, 104, 103, 106, 108, 110], sharpe: 1.6, max_drawdown: -4.3 },
    tags: ["oi", "vwap", "short_bias"]
  },
  {
    strategyId: "ALPHA-20240329-001", name: "微结构量价背离", type: "ALPHA" as const, direction: "LONG" as const, symbol: "BTCUSDT",
    entryCondition: "[SEED] dist_high < -2.37% [CONF] vol_ma20 > 1.0 [CONF] oi_change_rate > 0",
    exitConditionTop3: [
      { feature: "mark_basis_ma10", op: ">", threshold: 0.002, label: "基差MA10 > 0.002" },
      { feature: "funding_countdown_m", op: "<", threshold: 5, label: "资金费率倒计时 < 5min" },
      { feature: "direction_autocorr", op: "<", threshold: -0.3, label: "方向自相关 < -0.3" }
    ],
    oosWinRate: 65.3, oosAvgReturn: 0.0280, isSampleSize: 102, oosSampleSize: 34,
    overfitScore: 0.21, featureDiversityScore: 0.70, confidenceScore: 67.8,
    triggerCount7d: 3, tradeCount7d: 2, pnl7d: "67.40", totalTrades: 12, totalPnl: "234.80",
    status: "active" as const, discoveredAt: new Date("2024-03-29"), approvedAt: new Date("2024-03-29"),
    backtestStatus: "completed" as const,
    backtestResult: { equity_curve: [100, 101, 103, 102, 105, 107, 109], sharpe: 1.5, max_drawdown: -3.8 },
    tags: ["alpha", "microstructure", "auto_discovered"]
  },
  {
    strategyId: "P1-5", name: "盘口失衡反转", type: "P1" as const, direction: "BOTH" as const, symbol: "BTCUSDT",
    entryCondition: "book_imbalance > 0.7 AND price_momentum_1m < -0.3%",
    exitConditionTop3: [
      { feature: "book_imbalance", op: "<", threshold: 0.5, label: "盘口失衡恢复" },
      { feature: "spread_bps", op: ">", threshold: 5, label: "价差扩大" },
      { feature: "trade_intensity", op: "<", threshold: 0.5, label: "成交强度下降" }
    ],
    oosWinRate: 58.9, oosAvgReturn: 0.0156, isSampleSize: 234, oosSampleSize: 78,
    overfitScore: 0.31, featureDiversityScore: 0.55, confidenceScore: 58.2,
    triggerCount7d: 18, tradeCount7d: 10, pnl7d: "45.20", totalTrades: 198, totalPnl: "678.90",
    status: "degraded" as const, discoveredAt: new Date("2024-01-25"), approvedAt: new Date("2024-01-25"),
    backtestStatus: "completed" as const,
    backtestResult: { equity_curve: [100, 100, 101, 100, 102, 101, 103], sharpe: 0.9, max_drawdown: -7.1 },
    tags: ["order_book", "microstructure", "degraded"]
  },
];

export const MOCK_ALPHA_CANDIDATES = [
  {
    candidateId: "ALPHA-CAND-001",
    symbol: "BTCUSDT",
    direction: "LONG" as const,
    seedCondition: "vwap_deviation < -0.56%",
    confirmConditions: [
      { feature: "volume_vs_ma20", op: "<", threshold: 0.80, label: "成交量 < MA20*0.8" },
      { feature: "oi_change_rate", op: ">", threshold: 0, label: "OI变化率 > 0" }
    ],
    fullExpression: "[SEED] vwap_deviation < -0.56% [CONF] volume_vs_ma20 < 0.80 [CONF] oi_change_rate > 0",
    oosWinRate: 65.3, oosAvgReturn: 0.0280, sampleSize: 34,
    icScore: 0.187,
    featureDimensions: ["PRICE", "TRADE_FLOW", "POSITIONING"],
    exitConditionTop3: [
      { feature: "mark_basis_ma10", op: ">", threshold: 0.005, label: "基差MA10 > 0.005" },
      { feature: "funding_countdown_m", op: "<", threshold: 5, label: "资金费率倒计时 < 5min" },
      { feature: "direction_autocorr", op: "<", threshold: -0.2, label: "方向自相关 < -0.2" }
    ],
    estimatedDailyTriggers: 1.5,
    confidenceScore: 67.8, overfitScore: 0.21,
    status: "pending" as const,
    discoveredAt: new Date(Date.now() - 2 * 3600 * 1000),
    backtestStatus: "completed" as const,
    backtestResult: { equity_curve: [100, 102, 104, 103, 106, 108, 110], sharpe: 1.5, max_drawdown: -3.8 }
  },
  {
    candidateId: "ALPHA-CAND-002",
    symbol: "BTCUSDT",
    direction: "SHORT" as const,
    seedCondition: "funding_rate > 0.008%",
    confirmConditions: [
      { feature: "oi_change_rate", op: ">", threshold: 0.015, label: "OI增速 > 1.5%" },
      { feature: "liquidation_ratio_long", op: ">", threshold: 0.6, label: "多头清算比例 > 60%" }
    ],
    fullExpression: "[SEED] funding_rate > 0.008% [CONF] oi_change_rate > 0.015 [CONF] liquidation_ratio_long > 0.6",
    oosWinRate: 72.1, oosAvgReturn: 0.0334, sampleSize: 28,
    icScore: 0.234,
    featureDimensions: ["MARK_PRICE", "POSITIONING", "LIQUIDITY"],
    exitConditionTop3: [
      { feature: "funding_rate", op: "<", threshold: 0.003, label: "资金费率回落" },
      { feature: "oi_change_rate", op: "<", threshold: -0.01, label: "OI开始下降" },
      { feature: "price_recovery_pct", op: ">", threshold: 0.5, label: "价格反弹50%" }
    ],
    estimatedDailyTriggers: 0.8,
    confidenceScore: 74.5, overfitScore: 0.16,
    status: "pending" as const,
    discoveredAt: new Date(Date.now() - 5 * 3600 * 1000),
    backtestStatus: "completed" as const,
    backtestResult: { equity_curve: [100, 103, 107, 105, 110, 115, 119], sharpe: 2.1, max_drawdown: -2.9 }
  },
  {
    candidateId: "ALPHA-CAND-003",
    symbol: "ETHUSDT",
    direction: "LONG" as const,
    seedCondition: "dist_to_24h_low < 0.8%",
    confirmConditions: [
      { feature: "btc_correlation_1h", op: ">", threshold: 0.85, label: "BTC相关性 > 0.85" },
      { feature: "volume_vs_ma20", op: "<", threshold: 0.7, label: "成交量萎缩" }
    ],
    fullExpression: "[SEED] dist_to_24h_low < 0.8% [CONF] btc_correlation_1h > 0.85 [CONF] volume_vs_ma20 < 0.7",
    oosWinRate: 68.4, oosAvgReturn: 0.0312, sampleSize: 41,
    icScore: 0.198,
    featureDimensions: ["PRICE", "TRADE_FLOW", "MICROSTRUCTURE"],
    exitConditionTop3: [
      { feature: "dist_to_24h_high", op: "<", threshold: 1.0, label: "接近24h高点" },
      { feature: "volume_vs_ma20", op: ">", threshold: 1.5, label: "成交量放大" },
      { feature: "btc_correlation_1h", op: "<", threshold: 0.7, label: "与BTC脱钩" }
    ],
    estimatedDailyTriggers: 2.1,
    confidenceScore: 70.2, overfitScore: 0.19,
    status: "pending" as const,
    discoveredAt: new Date(Date.now() - 8 * 3600 * 1000),
    backtestStatus: "completed" as const,
    backtestResult: { equity_curve: [100, 101, 104, 102, 106, 109, 112], sharpe: 1.7, max_drawdown: -4.2 }
  },
];

export const MOCK_TRADES = [
  {
    tradeId: "TRD-001", symbol: "BTCUSDT", strategyId: "P1-6", direction: "LONG" as const,
    entryPrice: "82450.00", exitPrice: "83120.50", quantity: "0.05", leverage: 5,
    pnl: "167.63", pnlPercent: 0.81, fee: "3.30", mfe: 1.2, mae: -0.3,
    exitReason: "exit_condition_1", exitConditionTriggered: "mark_basis_ma10 > 0.005",
    status: "closed" as const, entryAt: new Date(Date.now() - 4 * 3600 * 1000), exitAt: new Date(Date.now() - 2 * 3600 * 1000), holdingMinutes: 120
  },
  {
    tradeId: "TRD-002", symbol: "BTCUSDT", strategyId: "P0-2", direction: "SHORT" as const,
    entryPrice: "83500.00", exitPrice: "83100.00", quantity: "0.03", leverage: 3,
    pnl: "72.00", pnlPercent: 0.48, fee: "2.00", mfe: 0.8, mae: -0.2,
    exitReason: "exit_condition_2", exitConditionTriggered: "funding_countdown_m < 5",
    status: "closed" as const, entryAt: new Date(Date.now() - 6 * 3600 * 1000), exitAt: new Date(Date.now() - 5 * 3600 * 1000), holdingMinutes: 58
  },
  {
    tradeId: "TRD-003", symbol: "BTCUSDT", strategyId: "P1-2", direction: "LONG" as const,
    entryPrice: "82800.00", exitPrice: null, quantity: "0.04", leverage: 4,
    pnl: null, pnlPercent: null, fee: "1.32", mfe: 0.5, mae: -0.1,
    exitReason: null, exitConditionTriggered: null,
    status: "open" as const, entryAt: new Date(Date.now() - 1 * 3600 * 1000), exitAt: null, holdingMinutes: null
  },
  {
    tradeId: "TRD-004", symbol: "ETHUSDT", strategyId: "P1-3", direction: "LONG" as const,
    entryPrice: "1850.00", exitPrice: "1892.50", quantity: "1.2", leverage: 3,
    pnl: "152.40", pnlPercent: 2.30, fee: "4.45", mfe: 2.8, mae: -0.5,
    exitReason: "exit_condition_1", exitConditionTriggered: "price_momentum_5m < 0",
    status: "closed" as const, entryAt: new Date(Date.now() - 12 * 3600 * 1000), exitAt: new Date(Date.now() - 10 * 3600 * 1000), holdingMinutes: 145
  },
  {
    tradeId: "TRD-005", symbol: "BTCUSDT", strategyId: "P1-7", direction: "LONG" as const,
    entryPrice: "81200.00", exitPrice: "83450.00", quantity: "0.06", leverage: 5,
    pnl: "810.00", pnlPercent: 2.77, fee: "4.89", mfe: 3.2, mae: -0.8,
    exitReason: "exit_condition_1", exitConditionTriggered: "liquidation_rate < 0.1",
    status: "closed" as const, entryAt: new Date(Date.now() - 24 * 3600 * 1000), exitAt: new Date(Date.now() - 20 * 3600 * 1000), holdingMinutes: 240
  },
  {
    tradeId: "TRD-006", symbol: "BTCUSDT", strategyId: "P1-5", direction: "SHORT" as const,
    entryPrice: "84200.00", exitPrice: "84500.00", quantity: "0.02", leverage: 2,
    pnl: "-36.00", pnlPercent: -0.36, fee: "1.34", mfe: 0.2, mae: -0.6,
    exitReason: "stop_loss", exitConditionTriggered: "max_loss_guard",
    status: "closed" as const, entryAt: new Date(Date.now() - 36 * 3600 * 1000), exitAt: new Date(Date.now() - 35 * 3600 * 1000), holdingMinutes: 42
  },
];

export const MOCK_SYSTEM_EVENTS = [
  { eventType: "signal_triggered" as const, symbol: "BTCUSDT", strategyId: "P1-6", severity: "info" as const, title: "信号触发: P1-6 底部量能枯竭", message: "BTCUSDT 触发底部量能枯竭信号，dist_to_24h_low=0.87%, volume_vs_ma20=0.73", metadata: { entry_price: 82450, direction: "LONG" }, occurredAt: new Date(Date.now() - 30 * 60 * 1000) },
  { eventType: "trade_opened" as const, symbol: "BTCUSDT", strategyId: "P1-6", severity: "info" as const, title: "开仓: BTCUSDT LONG @82450.00", message: "限价单已成交，数量: 0.05 BTC，杠杆: 5x", metadata: { trade_id: "TRD-003", fill_rate: 1.0 }, occurredAt: new Date(Date.now() - 29 * 60 * 1000) },
  { eventType: "alpha_discovered" as const, symbol: "BTCUSDT", severity: "info" as const, title: "Alpha引擎发现新候选策略", message: "ALPHA-CAND-001: OOS胜率65.3%，平均收益+0.028%，样本量34", metadata: { candidate_id: "ALPHA-CAND-001" }, occurredAt: new Date(Date.now() - 2 * 3600 * 1000) },
  { eventType: "backtest_completed" as const, symbol: "BTCUSDT", strategyId: "P0-2", severity: "info" as const, title: "回测完成: P0-2 资金费率套利", message: "OOS胜率: 78.5%，夏普比率: 2.1，最大回撤: -3.2%", metadata: { oos_wr: 78.5, sharpe: 2.1 }, occurredAt: new Date(Date.now() - 3 * 3600 * 1000) },
  { eventType: "system_warning" as const, severity: "warning" as const, title: "限价单成交率偏低", message: "过去1小时限价单成交率: 62%，建议调整ENTRY_OFFSET参数", metadata: { fill_rate: 0.62 }, occurredAt: new Date(Date.now() - 4 * 3600 * 1000) },
  { eventType: "ws_connected" as const, severity: "info" as const, title: "WebSocket连接恢复", message: "4条数据流全部重新连接: liquidations, book_ticker, agg_trades, mark_price", metadata: { streams: 4 }, occurredAt: new Date(Date.now() - 5 * 3600 * 1000) },
  { eventType: "trade_closed" as const, symbol: "BTCUSDT", strategyId: "P1-6", severity: "info" as const, title: "平仓: BTCUSDT LONG +0.81%", message: "出场条件触发: mark_basis_ma10 > 0.005，盈利: +167.63 USDT", metadata: { trade_id: "TRD-001", pnl: 167.63 }, occurredAt: new Date(Date.now() - 2 * 3600 * 1000) },
];

export const MOCK_DEV_TASKS = [
  // 底层基础设施
  { category: "底层基础设施", title: "特征引擎 (52+ 特征)", status: "completed" as const, priority: "critical" as const, layer: "infrastructure", sortOrder: 1, description: "实现52+个市场微观结构特征的实时计算，包含PRICE、TRADE_FLOW、LIQUIDITY、POSITIONING、MICROSTRUCTURE、MARK_PRICE六个维度" },
  { category: "底层基础设施", title: "数据存储 (Parquet Hive分区)", status: "completed" as const, priority: "critical" as const, layer: "infrastructure", sortOrder: 2, description: "基于Parquet格式的分区存储，支持按日期/交易对快速检索" },
  { category: "底层基础设施", title: "WebSocket采集 (4条流)", status: "completed" as const, priority: "critical" as const, layer: "infrastructure", sortOrder: 3, description: "liquidations、book_ticker、agg_trades、mark_price四条实时数据流" },
  // 信号检测
  { category: "信号检测", title: "P1事件型检测器 (8个)", status: "completed" as const, priority: "high" as const, layer: "signal", sortOrder: 10, description: "P0-2资金费率套利、P1-2 VWAP/TWAP拆单、P1-3大单方向、P1-5盘口失衡、P1-6底部量能枯竭、P1-7清算瀑布反转等8个手工检测器" },
  { category: "信号检测", title: "Alpha IC扫描", status: "completed" as const, priority: "high" as const, layer: "signal", sortOrder: 11, description: "52+特征的信息系数(IC)扫描，识别高预测性特征" },
  { category: "信号检测", title: "AtomMiner单条件挖掘", status: "completed" as const, priority: "high" as const, layer: "signal", sortOrder: 12, description: "单条件入场规则的自动挖掘" },
  { category: "信号检测", title: "ComboScanner多条件组合", status: "completed" as const, priority: "high" as const, layer: "signal", sortOrder: 13, description: "种子条件+物理确认的多条件组合扫描" },
  { category: "信号检测", title: "Walk-Forward验证器", status: "completed" as const, priority: "high" as const, layer: "signal", sortOrder: 14, description: "OOS胜率>60%的滚动验证，防止过拟合" },
  { category: "信号检测", title: "ExitConditionMiner (MFE+Cohen's d)", status: "completed" as const, priority: "high" as const, layer: "signal", sortOrder: 15, description: "基于MFE和Cohen's d的出场条件挖掘，Top-3 combo" },
  // 执行与反馈
  { category: "执行与反馈", title: "执行引擎 (限价单)", status: "completed" as const, priority: "critical" as const, layer: "execution", sortOrder: 20, description: "Maker费0.04%的限价单执行引擎" },
  { category: "执行与反馈", title: "Outcome追踪 (MFE/MAE)", status: "completed" as const, priority: "high" as const, layer: "execution", sortOrder: 21, description: "最大有利偏移(MFE)和最大不利偏移(MAE)的实时追踪" },
  { category: "执行与反馈", title: "动态出场 (SmartExitPolicy)", status: "completed" as const, priority: "high" as const, layer: "execution", sortOrder: 22, description: "基于条件触发的动态出场，无固定持仓时间" },
  { category: "执行与反馈", title: "损失守卫 (亏损不执行动态出场)", status: "completed" as const, priority: "high" as const, layer: "execution", sortOrder: 23, description: "亏损状态下暂停动态出场，避免止损后反弹" },
  // 监控与诊断
  { category: "监控与诊断", title: "实时监控 (live_engine.py)", status: "completed" as const, priority: "medium" as const, layer: "monitoring", sortOrder: 30, description: "系统各环节的实时健康监控" },
  { category: "监控与诊断", title: "信号聚合 (fatigue检测)", status: "completed" as const, priority: "medium" as const, layer: "monitoring", sortOrder: 31, description: "信号疲劳检测，避免同方向信号过度叠加" },
  { category: "监控与诊断", title: "限价单成交率测试 (模拟盘)", status: "in_progress" as const, priority: "critical" as const, layer: "monitoring", sortOrder: 32, description: "验证限价单在实盘条件下的成交率，目标>70%" },
  { category: "监控与诊断", title: "产品化UI仪表盘 (本项目)", status: "in_progress" as const, priority: "high" as const, layer: "monitoring", sortOrder: 33, description: "全功能Web监控与交互平台，含Alpha引擎、策略池、实盘管理" },
  { category: "监控与诊断", title: "影子出场审计", status: "pending" as const, priority: "medium" as const, layer: "monitoring", sortOrder: 34, description: "记录每次出场决策的完整上下文，用于事后分析" },
  // 扩展功能
  { category: "扩展功能", title: "多交易对支持", status: "in_progress" as const, priority: "high" as const, layer: "extension", sortOrder: 40, description: "支持ETHUSDT、SOLUSDT等多个交易对的Alpha引擎自动跟进" },
  { category: "扩展功能", title: "实盘API集成 (Binance)", status: "pending" as const, priority: "critical" as const, layer: "extension", sortOrder: 41, description: "连接Binance实盘API，实现真实下单和持仓管理" },
  { category: "扩展功能", title: "LLM策略分析报告", status: "pending" as const, priority: "low" as const, layer: "extension", sortOrder: 42, description: "集成LLM模型，自动生成策略优化建议和市场洞察报告" },
  { category: "扩展功能", title: "策略相关性分析 (策略对打)", status: "pending" as const, priority: "medium" as const, layer: "extension", sortOrder: 43, description: "分析策略间的相关性，支持策略组合构建" },
];
