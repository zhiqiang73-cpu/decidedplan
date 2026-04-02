"""
策略卡片 (Strategy Card)

将 CausalAtom + 走前验证结果 + 自动解释 合并为一张"策略卡"，
支持格式化打印和 JSON 持久化。

用法:
  card = StrategyCard.build(atom, wf_report, explainer)
  card.print()
  StrategyCard.save_all(cards, "alpha/output/strategy_cards.json")
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from alpha.causal_atoms import CausalAtom
from alpha.auto_explain import AutoExplainer

logger = logging.getLogger(__name__)

_explainer = AutoExplainer()


@dataclass
class StrategyCard:
    """
    一张策略卡，包含规则定义、验证指标和人类可读解释。
    """
    # 规则标识
    rule:         str
    feature:      str
    operator:     str
    threshold:    float
    direction:    str
    horizon:      int

    # 样本内指标
    is_icir:       Optional[float]
    is_win_rate:   Optional[float]
    is_pf:         Optional[float]
    is_avg_ret:    Optional[float]
    is_n:          int

    # 样本外指标
    oos_icir:      Optional[float]
    oos_win_rate:  Optional[float]
    oos_pf:        Optional[float]
    oos_avg_ret:   Optional[float]
    oos_n:         int

    # 走前评估
    degradation:   float
    is_robust:     bool

    # 全量指标（挖掘时用全集计算）
    full_ic:       float
    full_icir:     float
    full_win_rate: float
    full_pf:       float
    full_avg_ret:  float
    full_n:        int
    trigger_rate:  float

    # 解释文本
    explanation:   str
    short_desc:    str

    # 生成时间
    generated_at:  str = ""

    # ── 构建 ────────────────────────────────────────────────────────────────
    @classmethod
    def build(
        cls,
        atom: CausalAtom,
        wf_report: dict,
        explainer: AutoExplainer = None,
    ) -> "StrategyCard":
        """
        从 CausalAtom 和走前报告构建策略卡。

        Args:
            atom:       CausalAtom 实例（含全量统计）
            wf_report:  WalkForwardValidator.validate_atom() 的返回值
            explainer:  AutoExplainer 实例；None 则用模块级默认实例
        """
        if explainer is None:
            explainer = _explainer

        is_m  = wf_report.get("IS",  {})
        oos_m = wf_report.get("OOS", {})

        return cls(
            rule       = atom.rule_str(),
            feature    = atom.feature,
            operator   = atom.operator,
            threshold  = atom.threshold,
            direction  = atom.direction,
            horizon    = atom.horizon,

            is_icir      = is_m.get("ICIR"),
            is_win_rate  = is_m.get("win_rate"),
            is_pf        = is_m.get("profit_factor"),
            is_avg_ret   = is_m.get("avg_return_pct"),
            is_n         = is_m.get("n_triggers", 0),

            oos_icir     = oos_m.get("ICIR"),
            oos_win_rate = oos_m.get("win_rate"),
            oos_pf       = oos_m.get("profit_factor"),
            oos_avg_ret  = oos_m.get("avg_return_pct"),
            oos_n        = oos_m.get("n_triggers", 0),

            degradation = wf_report.get("degradation", 0.0),
            is_robust   = wf_report.get("is_robust", False),

            full_ic       = atom.ic,
            full_icir     = atom.icir,
            full_win_rate = atom.win_rate,
            full_pf       = atom.profit_factor,
            full_avg_ret  = atom.avg_return,
            full_n        = atom.n_triggers,
            trigger_rate  = atom.trigger_rate,

            explanation = explainer.explain(atom),
            short_desc  = explainer.short_desc(atom),
            generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    # ── 打印 ────────────────────────────────────────────────────────────────
    def print(self) -> None:
        """格式化打印策略卡。"""
        robust_tag = "[ROBUST]" if self.is_robust else "[OVERFIT]"
        sep = "=" * 72

        print(sep)
        print(f"  策略卡  {robust_tag}")
        print(f"  {self.rule}")
        print(sep)
        print()
        print(self.explanation)
        print()

        # IS / OOS 对比表
        def fmt(v, pct=False):
            if v is None:
                return "   -  "
            return f"{v:+.2f}%" if pct else f"{v:+.4f}"

        print(f"  {'':20s} {'样本内 IS':>12s} {'样本外 OOS':>12s}")
        print(f"  {'-'*44}")
        print(f"  {'ICIR':20s} {fmt(self.is_icir):>12s} {fmt(self.oos_icir):>12s}")
        print(f"  {'胜率':20s} {fmt(self.is_win_rate, True):>12s} {fmt(self.oos_win_rate, True):>12s}")
        print(f"  {'盈亏比':20s} {str(round(self.is_pf,2)) if self.is_pf else '-':>12s}"
              f" {str(round(self.oos_pf,2)) if self.oos_pf else '-':>12s}")
        print(f"  {'均收益':20s} {fmt(self.is_avg_ret, True):>12s} {fmt(self.oos_avg_ret, True):>12s}")
        print(f"  {'触发次数':20s} {str(self.is_n):>12s} {str(self.oos_n):>12s}")
        print()
        print(f"  性能衰减: {self.degradation:+.2f}  "
              f"({'稳健' if self.is_robust else '过拟合'})")
        print(sep)

    # ── 序列化 ──────────────────────────────────────────────────────────────
    def to_dict(self) -> dict:
        return asdict(self)

    # ── 批量保存 ────────────────────────────────────────────────────────────
    @staticmethod
    def save_all(cards: List["StrategyCard"], path: str) -> None:
        """
        将所有策略卡保存为 JSON 文件。

        Args:
            cards: StrategyCard 列表
            path:  输出文件路径（支持不存在的父目录，自动创建）
        """
        out_path = Path(path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        data = {
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "total":        len(cards),
            "robust_count": sum(1 for c in cards if c.is_robust),
            "cards":        [c.to_dict() for c in cards],
        }

        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info(f"策略卡已保存: {out_path} ({len(cards)} 张)")

    # ── 简表打印（多张卡） ──────────────────────────────────────────────────
    @staticmethod
    def print_summary(cards: List["StrategyCard"]) -> None:
        """打印所有策略卡的简明汇总表。"""
        robust_cards  = [c for c in cards if c.is_robust]
        overfit_cards = [c for c in cards if not c.is_robust]

        header = (
            f"{'规则描述':<42} {'ICIR(IS)':>9} {'ICIR(OOS)':>10} "
            f"{'WR(OOS)':>8} {'PF(OOS)':>8} {'decay':>7} {'状态':>8}"
        )
        sep = "-" * len(header)

        print()
        print("=" * len(header))
        print("  Alpha 发现报告 — 策略卡汇总")
        print("=" * len(header))
        print(header)
        print(sep)

        def _row(c: StrategyCard) -> str:
            tag    = "ROBUST " if c.is_robust else "OVERFIT"
            is_ic  = f"{c.is_icir:+.3f}"  if c.is_icir  is not None else "   -  "
            oos_ic = f"{c.oos_icir:+.3f}" if c.oos_icir is not None else "   -  "
            oos_wr = f"{c.oos_win_rate:.1f}%" if c.oos_win_rate is not None else "  -  "
            oos_pf = f"{c.oos_pf:.2f}"    if c.oos_pf   is not None else "  -  "
            dec    = f"{c.degradation:+.2f}"
            return (
                f"{c.short_desc:<42} {is_ic:>9} {oos_ic:>10} "
                f"{oos_wr:>8} {oos_pf:>8} {dec:>7} {tag:>8}"
            )

        for c in robust_cards:
            print(_row(c))

        if overfit_cards:
            print(f"  (-- 过拟合，OOS 性能不足 --)")
            for c in overfit_cards:
                print(_row(c))

        print("=" * len(header))
        print(
            f"  共发现 {len(cards)} 条规则，其中稳健规则 {len(robust_cards)} 条"
        )
        print()
