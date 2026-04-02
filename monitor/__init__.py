"""
实时监控模块

组件:
  live_engine.py   — 滚动窗口特征引擎（从 Parquet 热启动 + WebSocket 增量更新）
  alpha_rules.py   — 8 条 Alpha 规则 + 冷却期追踪
  signal_runner.py — Phase 1 检测器 + Phase 2 Alpha 规则统一调度
  alert_handler.py — 控制台彩色输出 + 文件日志
"""

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()
