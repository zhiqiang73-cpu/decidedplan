"""
第二阶段: Alpha 发现引擎

模块结构:
  scanner.py       — 特征 IC/ICIR 全量扫描
  causal_atoms.py  — 因果原子挖掘（阈值规则）
  walk_forward.py  — 样本内/样本外走前验证
  auto_explain.py  — 策略机制自动解释生成
  strategy_card.py — 策略卡片数据类 + 报告输出
"""

from runtime_bootstrap import bootstrap_runtime

bootstrap_runtime()
