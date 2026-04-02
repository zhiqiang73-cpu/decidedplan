"""
Alpha 规则验证脚本
按照6个原则逐条审核候选规则
"""

import json
import pandas as pd
from pathlib import Path

# 加载候选规则
with open("alpha/output/pending_rules.json", "r", encoding="utf-8") as f:
    rules = json.load(f)

print(f"总候选规则数: {len(rules)}")
print("=" * 80)


# 验证原则
def validate_rule(rule):
    """验证单条规则"""

    entry = rule["entry"]
    feature = entry["feature"]
    direction = entry["direction"]
    threshold = entry["threshold"]
    stats = rule["stats"]

    issues = []

    # 原则1: 物理因果检查
    # VWAP下方偏离应该LONG，不是SHORT
    if (
        feature == "vwap_deviation"
        and entry["operator"] == "<"
        and direction == "short"
    ):
        issues.append("方向错误: VWAP下方偏离应该LONG")

    # 低位应该LONG，不是SHORT（除非有额外机制）
    if feature in ["position_in_range_24h", "position_in_range_4h"]:
        if entry["operator"] == "<" and direction == "short":
            issues.append("方向存疑: 低位SHORT需要额外机制解释")

    # 原则3: OOS验证标准
    oos_wr = stats.get("oos_win_rate", 0)
    n_oos = stats.get("n_oos", 0)
    oos_pf = stats.get("oos_pf", 0)

    if n_oos < 20:
        issues.append(f"样本不足: n={n_oos} < 20")
    if oos_wr < 60:
        issues.append(f"胜率不达标: {oos_wr:.1f}% < 60%")
    if oos_pf < 1.0:
        issues.append(f"盈亏比不达标: {oos_pf:.2f} < 1.0")

    # 原则6: 过拟合警告
    if oos_pf > 20 and n_oos < 50:
        issues.append(f"过拟合警告: PF={oos_pf:.2f} > 20 且 n={n_oos} < 50")
    if oos_wr > 90 and n_oos < 30:
        issues.append(f"过拟合警告: WR={oos_wr:.1f}% > 90% 且 n={n_oos} < 30")

    # 判断结论
    if len(issues) == 0:
        conclusion = "APPROVE"
    elif any("方向错误" in issue for issue in issues):
        conclusion = "REJECT"
    else:
        conclusion = "FLAG"

    return {
        "rule_id": rule["id"],
        "rule_str": rule["rule_str"],
        "direction": direction,
        "oos_wr": oos_wr,
        "n_oos": n_oos,
        "oos_pf": oos_pf,
        "conclusion": conclusion,
        "issues": issues,
    }


# 验证所有规则
results = []
for i, rule in enumerate(rules, 1):
    result = validate_rule(rule)
    results.append(result)

    if i % 20 == 0:
        print(f"已验证 {i}/{len(rules)} 条规则...")

print(f"\n完成验证: {len(rules)} 条规则")

# 汇总统计
print("\n" + "=" * 80)
print("汇总统计")
print("=" * 80)

df = pd.DataFrame(results)
print(df["conclusion"].value_counts())
print()

# 输出APPROVE的规则
approved = df[df["conclusion"] == "APPROVE"]
if len(approved) > 0:
    print(f"\nAPPROVE规则 ({len(approved)}条):")
    for _, row in approved.iterrows():
        print(f"  - {row['rule_str'][:60]}")
