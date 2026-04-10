"""
多维度组合扫描器 (Combo Scanner)

设计原理:
  单变量扫描 (scanner.py) 只能发现 "feature X op threshold" 形式的规则。
  TRADE_FLOW / LIQUIDITY / POSITIONING 特征单独使用时噪音太大（1分钟级），
  但作为 PRICE 规则的"确认条件"时，可以过滤掉噪音，大幅提升胜率。

  核心思路：种子 + 确认模式
    Step 1: 种子规则（已知 ROBUST 单变量规则）
    Step 2: 对每个跨维度确认特征，测试 IS 内的最佳分位数阈值
    Step 3: Walk-Forward 验证组合规则（IS / OOS 各独立评估）
    Step 4: OOS 胜率比种子显著更好 (>3%) → 输出组合规则

物理确定性约束（硬性要求，防止纯统计数据挖掘）:
  每一个候选确认特征必须有明确的物理因果解释，说明为什么它能提升信号质量。
  不符合物理解释的特征禁止作为确认条件，即使 OOS 统计数字好看。

  ✓ 允许的确认特征（PHYSICAL_CONFIRM_FEATURES）:
    TRADE_FLOW: taker_buy_sell_ratio, volume_vs_ma20, volume_acceleration, avg_trade_size
      → 买卖流量方向是价格运动的物理驱动力
    LIQUIDITY:  kyle_lambda, spread_vs_ma20
      → 流动性决定价格冲击成本和趋势可持续性
    POSITIONING: oi_change_rate_5m, oi_change_rate_1h, ls_ratio_change_5m
      → 杠杆仓位变化决定被迫平仓压力

  ✗ 禁止的确认特征:
    TIME: hour_in_day, minutes_to_funding, minute_in_hour 等所有时间特征
      → 时间特征是统计季节性，不是物理机制
      → "凌晨时段价格更可能下跌"没有可解释的物理因果链
      → 会随市场参与者习惯的改变而失效，不具备物理稳定性

OOS 判定标准（防多重比较过拟合）:
  - OOS 样本量 >= 20（极端分位数模式）
  - OOS 胜率比种子单独 OOS 胜率提升 >= 3%
  - OOS 盈亏比 >= 1.0
  - IS 样本量 >= 15
"""

import logging
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ── 种子规则（已知有效的 PRICE 维度规则）─────────────────────────────────────
SEED_RULES: list[dict] = []

# ── 候选确认特征（仅允许有物理因果解释的跨维度特征）────────────────────────
# 原则：每个特征必须对应可解释的物理机制，而非时间季节性统计。
# 禁止 TIME 维度特征（hour_in_day, minutes_to_funding 等）：
#   这些是统计相关性而非物理因果，会随市场习惯改变而失效。
CONFIRM_FEATURES = [
    # TRADE_FLOW — 买卖流量方向是价格运动的物理驱动力
    "taker_buy_sell_ratio",   # 主动买/卖比：卖方主导 → 下跌有真实供给压力
    "volume_vs_ma20",         # 成交量放大：流量确认动量真实性
    "volume_acceleration",    # 成交量二阶导：动量是否在加速或衰竭
    "avg_trade_size",         # 平均单笔规模：大单 = 机构参与
    # LIQUIDITY — 流动性决定价格冲击和趋势持续性
    "kyle_lambda",            # 价格冲击系数：流动性薄 → 下跌加速
    "spread_vs_ma20",         # 价差：紧 = 有序，宽 = 恐慌（性质不同）
    # POSITIONING — 杠杆仓位变化决定被迫平仓压力
    "oi_change_rate_5m",      # OI 5分钟变化：新仓位开仓 → 更多待清算头寸
    "oi_change_rate_1h",      # OI 1小时变化：趋势级别的杠杆变化
    "ls_ratio_change_5m",     # 多空比变化：多头/空头比例的快速移动
]

# 确认特征分位数扫描点（在 IS 数据内确定最佳阈值，防止 OOS 泄露）
PERCENTILES = [10, 20, 30, 40, 50, 60, 70, 80, 90]

# 极端分位数模式：种子特征使用更极端的分位数阈值
# 对动态种子做更极端的分位数重扫，寻找更纯净的信号
EXTREME_PERCENTILES = [1, 2, 3, 5, 7, 10]  # 左尾（<）或右尾（>）

# 组合规则最低样本量（IS + OOS 各自独立判断）
# crossing 模式下信号少（~1-3%触发率），但 OOS 不低于 20 防假阳性
MIN_SAMPLES_IS  = 15
MIN_SAMPLES_OOS = 20  # 极端分位数 OOS 必须 >= 20 才具备统计意义

# OOS 胜率必须比种子单独 OOS 胜率高于此值
MIN_WR_IMPROVEMENT = 3.0   # 百分点

# Walk-Forward 切分比例
TRAIN_FRAC = 0.67

# Override the legacy confirm pool with a wider physics-first universe.
# We still exclude time features such as funding countdown because those are
# schedule descriptors, not force creation/decay descriptors.
CONFIRM_FEATURES = [
    # Trade flow
    "taker_buy_sell_ratio",
    "volume_vs_ma20",
    "volume_acceleration",
    "avg_trade_size",
    # Liquidity
    "kyle_lambda",
    "spread_vs_ma20",
    # Positioning
    "oi_change_rate_5m",
    "oi_change_rate_1h",
    "ls_ratio_change_5m",
    # Microstructure
    "quote_imbalance",
    "bid_depth_ratio",
    "spread_anomaly",
    # Order flow
    "large_trade_buy_ratio",
    "direction_net_1m",
    "sell_notional_share_1m",
    "trade_burst_index",
    "direction_autocorr",
    # Liquidation pressure
    "btc_liq_net_pressure",
    "total_liq_usd_5m",
    "liq_size_p90_5m",
    # Basis / funding state
    "rt_funding_rate",
    "mark_basis",
    "mark_basis_ma10",
    # Sustained state (block counts) -- P 系列核心能力
    "vol_drought_blocks_5m",
    "vol_drought_blocks_10m",
    "price_compression_blocks_5m",
    "price_compression_blocks_10m",
]


class ComboScanner:
    """
    多维度组合扫描器：种子 + 确认模式。

    Args:
        seed_rules:       种子规则列表（必须由外部动态传入）
        confirm_features: 候选确认特征（默认使用 CONFIRM_FEATURES）
        train_frac:       IS 数据占比（walk-forward 切分）
    """

    def __init__(
        self,
        seed_rules:       Optional[List[dict]] = None,
        confirm_features: Optional[List[str]]  = None,
        train_frac:       float                = TRAIN_FRAC,
    ):
        self.seed_rules       = list(seed_rules) if seed_rules else []
        self.confirm_features = confirm_features or CONFIRM_FEATURES
        self.train_frac       = train_frac
        self._bias_cache: dict[tuple, bool] = {}

    def _check_directional_bias(
        self, df: pd.DataFrame, feature: str, op: str, direction: str, horizon: int,
    ) -> bool:
        """确认因子方向性检验: Spearman 相关 >= 0.005 才接受。

        涨跌都会触发的因子（如 spread_vs_ma20）不被接受。
        """
        cache_key = (feature, op, direction, horizon)
        if cache_key in self._bias_cache:
            return self._bias_cache[cache_key]

        fwd_col = f"fwd_ret_{horizon}"
        if fwd_col not in df.columns or feature not in df.columns:
            self._bias_cache[cache_key] = True  # 无法检验时放行
            return True

        try:
            from scipy.stats import spearmanr
            valid = df[[feature, fwd_col]].dropna()
            if len(valid) < 200:
                self._bias_cache[cache_key] = True
                return True
            corr, _ = spearmanr(valid[feature], valid[fwd_col])

            # 做空时 confirm_op=">" 意味着"因子高时做空"
            # 需要: 因子高 → 收益负 → corr < -0.02
            if direction == "short" and op == ">":
                ok = corr < -0.005
            elif direction == "short" and op == "<":
                ok = corr > 0.005
            elif direction == "long" and op == ">":
                ok = corr > 0.005
            else:  # long and <
                ok = corr < -0.005

            if not ok:
                logger.debug(
                    "  [BIAS] rejected %s %s for %s: corr=%.4f (no directional edge)",
                    feature, op, direction, corr,
                )
            self._bias_cache[cache_key] = ok
            return ok
        except ImportError:
            self._bias_cache[cache_key] = True
            return True

    def scan(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        对 df 执行完整的组合扫描。

        Args:
            df: 已计算特征的 DataFrame（需含 close + timestamp 列）

        Returns:
            按 OOS WR 提升排序的结果 DataFrame
        """
        # Walk-Forward 切分
        if not self.seed_rules:
            logger.info("ComboScanner.scan skipped: no dynamic seed rules supplied")
            return pd.DataFrame()

        split = int(len(df) * self.train_frac)
        train_df = df.iloc[:split].copy()
        test_df  = df.iloc[split:].copy()

        is_start  = _ts_date(train_df)
        oos_start = _ts_date(test_df)
        logger.info(
            f"数据切分: IS {is_start} ({len(train_df):,}行) | "
            f"OOS {oos_start} ({len(test_df):,}行)"
        )

        results = []
        total = len(self.seed_rules) * len(self.confirm_features) * len(PERCENTILES) * 2
        tested = 0

        for seed in self.seed_rules:
            horizon   = seed["horizon"]
            direction = seed["direction"]
            fwd_col   = f"fwd_ret_{horizon}"

            if fwd_col not in df.columns:
                logger.warning(f"缺少前向收益列 {fwd_col}，跳过种子 {seed['name']}")
                continue

            # 种子单独的 IS / OOS 基准
            seed_train_df, seed_test_df = self._split_for_seed(
                seed, df, fwd_col, train_df, test_df
            )
            seed_is  = self._eval_seed(seed, seed_train_df, fwd_col)
            seed_oos = self._eval_seed(seed, seed_test_df,  fwd_col)

            if seed_is["n"] < MIN_SAMPLES_IS:
                logger.debug(f"种子 {seed['name']} IS 样本不足 ({seed_is['n']})，跳过")
                continue

            logger.info(
                f"种子 [{seed['name']}] IS: WR={seed_is['wr']:.1f}% "
                f"n={seed_is['n']} | OOS: WR={seed_oos['wr']:.1f}% n={seed_oos['n']}"
            )

            confirm_features = seed.get("confirm_features") or self.confirm_features
            for feat in confirm_features:
                if feat not in seed_train_df.columns:
                    continue
                if seed_train_df[feat].isna().mean() > 0.5:
                    continue

                for pct in PERCENTILES:
                    # 阈值只在 IS 数据上计算，避免 OOS 泄露
                    thresh = float(seed_train_df[feat].quantile(pct / 100))

                    for op in ["<", ">"]:
                        tested += 1

                        # IS 评估
                        combo_is = self._eval_combo(
                            seed, seed_train_df, fwd_col, feat, op, thresh
                        )
                        if combo_is["n"] < MIN_SAMPLES_IS:
                            continue

                        # IS 胜率必须比种子好（防止 IS 拟合后 OOS 崩溃）
                        if combo_is["wr"] - seed_is["wr"] < 2.0:
                            continue

                        # OOS 评估（用 IS 确定的阈值，直接应用到 OOS）
                        combo_oos = self._eval_combo(
                            seed, seed_test_df, fwd_col, feat, op, thresh
                        )
                        if combo_oos["n"] < MIN_SAMPLES_OOS:
                            continue

                        wr_improvement = combo_oos["wr"] - seed_oos["wr"]
                        if wr_improvement < MIN_WR_IMPROVEMENT:
                            continue

                        if combo_oos["pf"] < 1.0:
                            continue

                        # -- 方向性检验: 确认因子必须与交易方向有统计偏见 --
                        if not self._check_directional_bias(
                            df, feat, op, direction, horizon
                        ):
                            continue

                        results.append({
                            "seed_name":        seed["name"],
                            "seed_feature":     seed["feature"],
                            "seed_op":          seed["op"],
                            "seed_threshold":   seed["threshold"],
                            "confirm_feature":  feat,
                            "confirm_op":       op,
                            "confirm_pct":      pct,
                            "confirm_threshold": thresh,
                            "horizon":          horizon,
                            "direction":        direction,
                            # IS 指标
                            "is_n":             combo_is["n"],
                            "is_wr":            round(combo_is["wr"], 2),
                            "is_avg_ret":       round(combo_is["avg_ret"] * 100, 4),
                            "is_pf":            round(combo_is["pf"], 3),
                            # OOS 指标
                            "oos_n":            combo_oos["n"],
                            "oos_wr":           round(combo_oos["wr"], 2),
                            "oos_avg_ret":      round(combo_oos["avg_ret"] * 100, 4),
                            "oos_pf":           round(combo_oos["pf"], 3),
                            # 对比种子
                            "seed_is_wr":       round(seed_is["wr"], 2),
                            "seed_oos_wr":      round(seed_oos["wr"], 2),
                            "wr_improvement":   round(wr_improvement, 2),
                        })

        logger.info(f"扫描完成: 共测试 {tested}/{total} 组合，找到 {len(results)} 个候选规则")

        if not results:
            return pd.DataFrame()

        out = pd.DataFrame(results)
        out = out.sort_values("wr_improvement", ascending=False).reset_index(drop=True)
        return out

    def scan_extreme_seeds(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        极端分位数种子扫描。

        不使用外部传入种子的原始阈值，而是在 IS 数据上
        自动搜索 1-10th 百分位的最优种子阈值，再组合确认特征。

        为什么更极端的阈值可能更好：
          - 固定阈值（如 range_pos < 0.2847）在 IS 触发率 ~23%，噪音多
          - 1th 百分位阈值触发率 ~1%，每次触发都是极端事件，信号更纯
          - 极端事件的 Alpha 更稳定（物理约束更强）

        Returns:
            按 OOS WR 提升排序的结果 DataFrame
        """
        if not self.seed_rules:
            logger.info("ComboScanner.scan_extreme_seeds skipped: no dynamic seed rules supplied")
            return pd.DataFrame()

        split = int(len(df) * self.train_frac)
        train_df = df.iloc[:split].copy()
        test_df  = df.iloc[split:].copy()

        logger.info(
            f"极端分位数扫描: IS {len(train_df):,}行 | OOS {len(test_df):,}行"
        )

        results = []
        # 种子特征来自当前动态种子（方向和 horizon 保留，阈值重新扫描）
        seed_templates: list[dict] = []
        seen_templates: set[tuple[str, str, int, str]] = set()
        for seed in self.seed_rules:
            try:
                feature = str(seed["feature"])
                op = str(seed["op"])
                horizon = int(seed["horizon"])
                direction = str(seed["direction"])
            except (KeyError, TypeError, ValueError):
                continue
            template_key = (feature, op, horizon, direction)
            if template_key in seen_templates:
                continue
            seen_templates.add(template_key)
            seed_templates.append(
                {
                    "feature": feature,
                    "op": op,
                    "horizon": horizon,
                    "direction": direction,
                    "name": str(seed.get("name") or feature),
                    "group": str(seed.get("group") or feature),
                    "cooldown": int(seed.get("cooldown", 60) or 60),
                }
            )

        for tmpl in seed_templates:
            feat    = tmpl["feature"]
            op      = tmpl["op"]
            horizon = tmpl["horizon"]
            direction = tmpl["direction"]
            fwd_col = f"fwd_ret_{horizon}"

            if feat not in train_df.columns or fwd_col not in df.columns:
                continue

            for pct in EXTREME_PERCENTILES:
                # op="<" → 左尾；op=">" → 右尾
                q = pct / 100 if op == "<" else 1 - pct / 100
                thresh = float(train_df[feat].quantile(q))

                seed = {
                    **tmpl,
                    "threshold": thresh,
                    "name": f"{tmpl['name']}_extreme_p{pct}",
                }

                # IS 评估
                seed_is = self._eval_seed(seed, train_df, fwd_col)
                if seed_is["n"] < MIN_SAMPLES_IS:
                    continue

                # OOS 评估
                seed_oos = self._eval_seed(seed, test_df, fwd_col)

                logger.debug(
                    f"极端种子 [{feat} p{pct}] IS WR={seed_is['wr']:.1f}% n={seed_is['n']} "
                    f"| OOS WR={seed_oos['wr']:.1f}% n={seed_oos['n']}"
                )

                # 确认特征扫描
                confirm_features = seed.get("confirm_features") or self.confirm_features
                for conf_feat in confirm_features:
                    if conf_feat not in train_df.columns:
                        continue
                    if train_df[conf_feat].isna().mean() > 0.5:
                        continue

                    for conf_pct in PERCENTILES:
                        conf_thresh = float(train_df[conf_feat].quantile(conf_pct / 100))
                        for conf_op in ["<", ">"]:
                            # IS 组合
                            combo_is = self._eval_combo(
                                seed, train_df, fwd_col, conf_feat, conf_op, conf_thresh
                            )
                            if combo_is["n"] < MIN_SAMPLES_IS:
                                continue
                            if combo_is["wr"] - seed_is["wr"] < 2.0:
                                continue

                            # OOS 组合
                            combo_oos = self._eval_combo(
                                seed, test_df, fwd_col, conf_feat, conf_op, conf_thresh
                            )
                            if combo_oos["n"] < MIN_SAMPLES_OOS:
                                continue

                            wr_improvement = combo_oos["wr"] - seed_oos["wr"]
                            if wr_improvement < MIN_WR_IMPROVEMENT:
                                continue
                            if combo_oos["pf"] < 1.0:
                                continue

                            # -- 方向性检验 --
                            if not self._check_directional_bias(
                                df, conf_feat, conf_op, direction, horizon
                            ):
                                continue

                            results.append({
                                "seed_name":         seed["name"],
                                "seed_feature":      feat,
                                "seed_op":           op,
                                "seed_pct":          pct,
                                "seed_threshold":    round(thresh, 6),
                                "confirm_feature":   conf_feat,
                                "confirm_op":        conf_op,
                                "confirm_pct":       conf_pct,
                                "confirm_threshold": round(conf_thresh, 6),
                                "horizon":           horizon,
                                "direction":         direction,
                                "is_n":              combo_is["n"],
                                "is_wr":             round(combo_is["wr"], 2),
                                "is_avg_ret":        round(combo_is["avg_ret"] * 100, 4),
                                "is_pf":             round(combo_is["pf"], 3),
                                "oos_n":             combo_oos["n"],
                                "oos_wr":            round(combo_oos["wr"], 2),
                                "oos_avg_ret":       round(combo_oos["avg_ret"] * 100, 4),
                                "oos_pf":            round(combo_oos["pf"], 3),
                                "seed_is_wr":        round(seed_is["wr"], 2),
                                "seed_oos_wr":       round(seed_oos["wr"], 2),
                                "wr_improvement":    round(wr_improvement, 2),
                            })

        logger.info(f"极端分位数扫描完成，找到 {len(results)} 个候选")
        if not results:
            return pd.DataFrame()

        out = pd.DataFrame(results)
        return out.sort_values("wr_improvement", ascending=False).reset_index(drop=True)

    # ── 内部评估方法 ─────────────────────────────────────────────────────────

    def _split_for_seed(
        self,
        seed: dict,
        df: pd.DataFrame,
        fwd_col: str,
        train_df: pd.DataFrame,
        test_df: pd.DataFrame,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        if seed.get("origin") != "realtime_seed_miner":
            return train_df, test_df

        feat = str(seed.get("feature", "") or "")
        if feat not in df.columns or fwd_col not in df.columns:
            return train_df.iloc[:0].copy(), test_df.iloc[:0].copy()

        valid_df = df[df[feat].notna() & df[fwd_col].notna()].copy()
        if valid_df.empty:
            return train_df.iloc[:0].copy(), test_df.iloc[:0].copy()

        split = int(len(valid_df) * self.train_frac)
        if split <= 0 or split >= len(valid_df):
            return train_df.iloc[:0].copy(), test_df.iloc[:0].copy()

        return valid_df.iloc[:split].copy(), valid_df.iloc[split:].copy()

    def _get_seed_mask(self, seed: dict, df: pd.DataFrame) -> pd.Series:
        """
        种子信号掩码：检测带冷却期的首次穿越（crossing detection）。

        物理原理：
          Alpha 胜率 87.5% 来自"价格首次跌破阈值"这一事件，
          而不是"价格持续低于阈值"这一状态。
          持续状态下触发率 21-25%（信号稀释），
          首次穿越触发率约 0.5-2%（信号纯净）。
        """
        feat   = seed["feature"]
        op     = seed["op"]
        thresh = seed["threshold"]
        if feat not in df.columns:
            return pd.Series(False, index=df.index)

        col = df[feat].values
        return pd.Series(
            _crossing_mask(col, op, thresh, cooldown=seed.get("cooldown", 60)),
            index=df.index,
        )

    def _eval_seed(self, seed: dict, df: pd.DataFrame, fwd_col: str) -> dict:
        mask = self._get_seed_mask(seed, df)
        return self._compute_stats(mask, df, fwd_col, seed["direction"])

    def _eval_combo(
        self, seed: dict, df: pd.DataFrame,
        fwd_col: str, conf_feat: str, conf_op: str, conf_thresh: float,
    ) -> dict:
        seed_mask = self._get_seed_mask(seed, df)

        col = df[conf_feat]
        if conf_op == "<":
            conf_mask = col < conf_thresh
        else:
            conf_mask = col > conf_thresh

        combo_mask = seed_mask & conf_mask & col.notna()
        return self._compute_stats(combo_mask, df, fwd_col, seed["direction"])

    @staticmethod
    def _compute_stats(
        mask: pd.Series, df: pd.DataFrame,
        fwd_col: str, direction: str,
    ) -> dict:
        """计算触发条件下的收益统计。"""
        valid = mask & df[fwd_col].notna()
        n = int(valid.sum())
        if n == 0:
            return {"n": 0, "wr": 0.0, "avg_ret": 0.0, "pf": 0.0}

        fwd = df.loc[valid, fwd_col].values

        # short: 收益 = -fwd_ret；long: 收益 = +fwd_ret
        rets = -fwd if direction == "short" else fwd

        wins   = rets[rets > 0]
        losses = rets[rets <= 0]
        wr = len(wins) / n * 100

        avg_win  = float(wins.mean())   if len(wins)   > 0 else 0.0
        avg_loss = float(abs(losses.mean())) if len(losses) > 0 else 0.0
        pf = (
            (avg_win * len(wins)) / (avg_loss * len(losses))
            if len(losses) > 0 and avg_loss > 0
            else float("inf")
        )

        return {
            "n":       n,
            "wr":      wr,
            "avg_ret": float(rets.mean()),
            "pf":      pf,
        }


# ── 输出格式化 ────────────────────────────────────────────────────────────────

def print_combo_results(results: pd.DataFrame, top_n: int = 20) -> None:
    """打印组合扫描结果表。"""
    if results.empty:
        print("\n  [COMBO] 未找到满足条件的组合规则")
        return

    display = results.head(top_n)

    print()
    print("=" * 100)
    print("  多维度组合规则扫描结果（种子 + 确认）")
    print(f"  共找到 {len(results)} 个候选，展示前 {min(top_n, len(results))} 个")
    print("=" * 100)
    header = (
        f"{'种子规则':<28} {'确认条件':<32} {'周期':>5} "
        f"{'IS WR':>7} {'IS N':>5} "
        f"{'OOS WR':>7} {'OOS N':>5} {'OOS PF':>7} "
        f"{'WR提升':>7}"
    )
    print(header)
    print("-" * 100)

    for _, row in display.iterrows():
        confirm_str = f"{row['confirm_feature']} {row['confirm_op']} p{row['confirm_pct']:.0f}"
        print(
            f"  {row['seed_name']:<26} {confirm_str:<32} {int(row['horizon']):>5} "
            f"  {row['is_wr']:>5.1f}% {int(row['is_n']):>5} "
            f"  {row['oos_wr']:>5.1f}% {int(row['oos_n']):>5} "
            f"  {row['oos_pf']:>5.2f}x "
            f"  +{row['wr_improvement']:>4.1f}%"
        )

    print("=" * 100)
    print()

    # 按种子分组汇总
    print("  按种子规则汇总（最佳组合）:")
    print("-" * 60)
    for seed_name, grp in results.groupby("seed_name"):
        best = grp.iloc[0]
        confirm_str = f"{best['confirm_feature']} {best['confirm_op']} p{best['confirm_pct']:.0f}"
        print(
            f"  {seed_name:<28} 最佳确认: {confirm_str:<30} "
            f"OOS WR {best['oos_wr']:.1f}% (+{best['wr_improvement']:.1f}%)"
        )
    print()


def _crossing_mask(
    col: np.ndarray, op: str, thresh: float, cooldown: int = 60
) -> np.ndarray:
    """
    带冷却期的首次穿越检测。

    只标记"首次进入条件"的 bar（从不满足 → 满足），
    之后 cooldown 根 K 线内不再触发（无论条件是否仍然成立）。

    这模拟了实时信号系统的行为，避免持续状态对信号的稀释。

    Args:
        col:      特征列（numpy 数组）
        op:       '<' 或 '>'
        thresh:   触发阈值
        cooldown: 触发后冷却 K 线数

    Returns:
        bool 数组，True = 该 bar 是首次穿越触发
    """
    n = len(col)
    result = np.zeros(n, dtype=bool)

    if op == "<":
        cond = col < thresh
    else:
        cond = col > thresh

    # 前移一格得到前一 bar 的状态（首格视为 False）
    prev_cond = np.empty(n, dtype=bool)
    prev_cond[0] = False
    prev_cond[1:] = cond[:-1]

    # 原始穿越点（当前满足 AND 前一 bar 不满足）
    raw_crossings = cond & ~prev_cond

    # 应用冷却期（O(k) 遍历穿越点，k << n）
    crossing_indices = np.where(raw_crossings)[0]
    last_trigger = -cooldown - 1
    for idx in crossing_indices:
        if idx - last_trigger > cooldown:
            result[idx] = True
            last_trigger = idx

    return result


def _ts_date(df: pd.DataFrame) -> str:
    try:
        ts = df["timestamp"].iloc[0]
        return str(pd.to_datetime(ts, unit="ms", utc=True).date())
    except Exception:
        return "?"
