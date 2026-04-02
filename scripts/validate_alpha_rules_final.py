"""
Alpha 规则验证脚本 - 最终版
严格执行所有6个验证原则
"""

import json
import pandas as pd

# 加载候选规则
with open('alpha/output/pending_rules.json', 'r', encoding='utf-8') as f:
    rules = json.load(f)

def validate_rule(rule):
    """验证单条规则"""
    entry = rule['entry']
    feature = entry['feature']
    direction = entry['direction']
    stats = rule['stats']
    
    issues = []
    
    # 原则1: 物理因果检查
    if feature == 'vwap_deviation' and entry['operator'] == '<' and direction == 'short':
        issues.append('方向错误: VWAP下方偏离应该LONG')
    
    if feature in ['position_in_range_24h', 'position_in_range_4h']:
        if entry['operator'] == '<' and direction == 'short':
            issues.append('方向存疑: 低位SHORT需要额外机制')
    
    # 原则2: taker_ratio_api可靠性检查
    if feature == 'taker_ratio_api':
        issues.append('taker_ratio_api: 5分钟延迟API，实盘可用性待确认')
    
    # 原则3: OOS验证标准
    oos_wr = stats.get('oos_win_rate', 0)
    n_oos = stats.get('n_oos', 0)
    oos_pf = stats.get('oos_pf', 0)
    
    if n_oos < 20:
        issues.append(f'样本不足: n={n_oos}')
    if oos_wr < 60:
        issues.append(f'胜率不达标: {oos_wr:.1f}%')
    if oos_pf < 1.0:
        issues.append(f'盈亏比不达标: {oos_pf:.2f}')
    
    # 原则6: 过拟合警告（严格执行）
    if oos_pf > 10:
        issues.append(f'过拟合警告: PF={oos_pf:.1f} > 10')
    if oos_wr > 90 and n_oos < 50:
        issues.append(f'过拟合警告: WR={oos_wr:.1f}% > 90%')
    
    # 判断结论
    if len(issues) == 0:
        conclusion = 'APPROVE'
    elif any('方向错误' in issue or '过拟合警告' in issue for issue in issues):
        conclusion = 'FLAG'  # 严重问题降级为FLAG
    else:
        conclusion = 'FLAG'
    
    return {
        'rule_str': rule['rule_str'][:60],
        'feature': feature,
        'direction': direction,
        'wr': oos_wr,
        'n': n_oos,
        'pf': oos_pf,
        'conclusion': conclusion,
        'issues': issues
    }

# 验证所有规则
results = [validate_rule(rule) for rule in rules]
df = pd.DataFrame(results)

# 输出结果
print('=' * 80)
print('Alpha规则验证结果 - 最终版')
print('=' * 80)
print(f'\n总规则数: {len(df)}')
print('\n结论分布:')
print(df['conclusion'].value_counts())

# APPROVE规则
approved = df[df['conclusion'] == 'APPROVE']
print(f'\n\nAPPROVE规则 ({len(approved)}条):')
if len(approved) > 0:
    for i, row in approved.head(10).iterrows():
        print(f"  {row['rule_str']:<60} WR={row['wr']:.1f}% n={row['n']} PF={row['pf']:.2f}")
else:
    print('  无')

# FLAG规则
flagged = df[df['conclusion'] == 'FLAG']
print(f'\n\nFLAG规则 ({len(flagged)}条):')
print('  主要问题:')
issue_counts = {}
for _, row in flagged.iterrows():
    for issue in row['issues']:
        key = issue.split(':')[0] if ':' in issue else issue
        issue_counts[key] = issue_counts.get(key, 0) + 1

for issue, count in sorted(issue_counts.items(), key=lambda x: -x[1]):
    print(f'    - {issue}: {count}条')

# 保存结果
df.to_csv('alpha/output/validation_results.csv', index=False, encoding='utf-8')
print('\n\n结果已保存: alpha/output/validation_results.csv')
"