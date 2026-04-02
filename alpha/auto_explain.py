"""
策略自动解释生成器 (Auto Explain)

将 CausalAtom 的机器语言（特征名 + 阈值 + 方向）
翻译为人类可读的市场机制描述。

设计思路:
  每个特征有一个对应的"物理含义"描述词典，
  结合 operator / direction / horizon 拼成完整解释。

用法:
  explainer = AutoExplainer()
  text = explainer.explain(atom)
  print(text)
"""

from alpha.causal_atoms import CausalAtom

# ── 特征含义词典 ─────────────────────────────────────────────────────────────
# key: 特征列名
# value: dict with:
#   "desc"     — 简短描述（中文）
#   "high"     — 值高时的市场含义
#   "low"      — 值低时的市场含义
#   "category" — 所属维度
FEATURE_META = {
    # TIME 维度
    "minute_in_hour": {
        "desc": "当前分钟在小时内的位置",
        "high": "临近整点/半点，算法拆单、基金调仓集中时段",
        "low":  "远离整点，散户自由交易时段，流动性相对分散",
        "category": "TIME",
    },
    "hour_in_day": {
        "desc": "UTC小时数",
        "high": "欧美交易时段（12~22 UTC），流动性最高",
        "low":  "亚洲夜间（0~6 UTC），流动性较低，gap风险高",
        "category": "TIME",
    },
    "minutes_to_funding": {
        "desc": "距下次资金费率结算的分钟数",
        "high": "远离结算时刻，资金费率套利行为较少",
        "low":  "临近结算（<30min），期货持仓方向性行为集中，可能引发价格偏移",
        "category": "TIME",
    },
    "hours_to_options_expiry": {
        "desc": "距下次BTC期权到期的小时数",
        "high": "远离期权到期，期权 Delta 对冲压力小",
        "low":  "临近到期（<48h），做市商 Delta 对冲需求大，成交量和波动率抬升",
        "category": "TIME",
    },
    "minutes_since_last_big_move": {
        "desc": "距上次大幅振幅（>5x均值）的分钟数",
        "high": "长时间未出现大振幅，波动率处于压缩状态，突破潜力积累",
        "low":  "刚刚发生过大振幅，余震效应或均值回归窗口",
        "category": "TIME",
    },
    "is_weekend": {
        "desc": "是否为UTC周末",
        "high": "周末时段，机构参与度低，价格易被散户情绪主导",
        "low":  "工作日，机构交易活跃",
        "category": "TIME",
    },

    # PRICE 维度
    "dist_to_round_1000": {
        "desc": "到最近1000整数关口的相对距离",
        "high": "远离整千关口，无显著心理阻力",
        "low":  "临近整千关口（如 90000/91000），大量挂单聚集，阻力/支撑明显",
        "category": "PRICE",
    },
    "dist_to_round_100": {
        "desc": "到最近100整数关口的相对距离",
        "high": "远离整百关口",
        "low":  "临近整百关口，短线挂单密集，易形成磁力效应",
        "category": "PRICE",
    },
    "dist_to_24h_high": {
        "desc": "当前价格距24小时最高点的相对距离",
        "high": "远低于24h高点，处于日内低位区域",
        "low":  "接近或突破24h高点，动量突破信号",
        "category": "PRICE",
    },
    "dist_to_24h_low": {
        "desc": "当前价格距24小时最低点的相对距离",
        "high": "远高于24h低点，处于日内高位区域",
        "low":  "接近24h低点，支撑区域，超卖反弹可能",
        "category": "PRICE",
    },
    "position_in_range_4h": {
        "desc": "价格在4小时高低区间内的相对位置（0=低点，1=高点）",
        "high": "处于4小时区间上方，短期趋势偏多",
        "low":  "处于4小时区间下方，短期趋势偏空",
        "category": "PRICE",
    },
    "position_in_range_24h": {
        "desc": "价格在24小时高低区间内的相对位置",
        "high": "日内相对强势，日内高位",
        "low":  "日内相对弱势，日内低位，超卖区间",
        "category": "PRICE",
    },
    "vwap_deviation": {
        "desc": "价格偏离24小时VWAP的程度",
        "high": "价格显著高于VWAP，短线溢价，均值回归压力",
        "low":  "价格显著低于VWAP，短线折价，均值回归潜力",
        "category": "PRICE",
    },

    # TRADE_FLOW 维度
    "taker_buy_sell_ratio": {
        "desc": "主动买入量 / 主动卖出量",
        "high": "多头主导，买方更激进，短期上涨动量",
        "low":  "空头主导，卖方更激进，短期下跌动量",
        "category": "TRADE_FLOW",
    },
    "taker_buy_pct": {
        "desc": "主动买入占总成交量比例",
        "high": "买方强势（>0.6），短期多头动量",
        "low":  "卖方强势（<0.4），短期空头动量",
        "category": "TRADE_FLOW",
    },
    "volume_vs_ma20": {
        "desc": "当前成交量 / 20根均量",
        "high": "成交量显著放大（>2x），大资金进场或重要事件驱动",
        "low":  "成交量萎缩，市场观望，趋势待确认",
        "category": "TRADE_FLOW",
    },
    "avg_trade_size": {
        "desc": "每笔均成交额（USDT）",
        "high": "大单为主，机构主导市场",
        "low":  "小单为主，散户驱动或流动性分散",
        "category": "TRADE_FLOW",
    },
    "volume_acceleration": {
        "desc": "成交量加速度（二阶导数）",
        "high": "成交量加速放大，短期爆发行情前兆",
        "low":  "成交量加速萎缩，行情尾声或反转信号",
        "category": "TRADE_FLOW",
    },
    "volume_autocorr_lag5": {
        "desc": "成交量5根自相关系数",
        "high": "成交量呈规律性节奏（>0.4），大概率为算法拆单（VWAP/TWAP）",
        "low":  "成交量随机分布，市场情绪驱动",
        "category": "TRADE_FLOW",
    },
    "avg_trade_size_cv_10m": {
        "desc": "10分钟单笔成交额变异系数（越小越均匀）",
        "high": "成交不均匀，散户或突发事件主导",
        "low":  "成交极均匀（<0.15），算法程序化交易特征",
        "category": "TRADE_FLOW",
    },
    "trade_interval_cv": {
        "desc": "成交间隔变异系数（用trades列近似）",
        "high": "成交节奏不均，情绪性交易",
        "low":  "成交节奏均匀，程序化特征",
        "category": "TRADE_FLOW",
    },

    # LIQUIDITY 维度
    "kyle_lambda": {
        "desc": "Kyle's Lambda 价格冲击系数（|Δprice| / volume）",
        "high": "市场深度薄，小额成交即可推动价格，滑点风险高",
        "low":  "市场深度厚，大额成交也不易推动价格，机构可低成本建仓",
        "category": "LIQUIDITY",
    },
    "spread_proxy": {
        "desc": "买卖价差代理（(high-low)/close）",
        "high": "价差扩大，流动性差，做市商在调整报价",
        "low":  "价差收窄，流动性好，套利交易活跃",
        "category": "LIQUIDITY",
    },
    "spread_vs_ma20": {
        "desc": "当前价差 / 20根均值价差",
        "high": "价差异常扩大（>1.5x），市场流动性突变，做市商库存调整",
        "low":  "价差低于均值，市场流动性充裕",
        "category": "LIQUIDITY",
    },
    "amplitude_1m": {
        "desc": "1分钟振幅 (high-low)/close",
        "high": "当前分钟价格波动剧烈，大额订单或清算事件",
        "low":  "价格平稳，低波动环境",
        "category": "LIQUIDITY",
    },

    # POSITIONING 维度
    "oi_change_rate_5m": {
        "desc": "持仓量5分钟变化率",
        "high": "5分钟持仓快速增加，新资金入场（顺势建仓）",
        "low":  "5分钟持仓急剧下降（<-0.05），大量爆仓或主动减仓",
        "category": "POSITIONING",
    },
    "oi_change_rate_1h": {
        "desc": "持仓量1小时变化率",
        "high": "持仓持续增加，趋势行情延续信号",
        "low":  "持仓持续减少，趋势衰减或反转预警",
        "category": "POSITIONING",
    },
    "ls_ratio_change_5m": {
        "desc": "多空比5分钟变化",
        "high": "多头仓位快速增加，散户情绪偏多（反向指标：散户多头拥挤时机构常做空）",
        "low":  "空头仓位快速增加，散户情绪偏空（反向指标）",
        "category": "POSITIONING",
    },
    "funding_rate_trend": {
        "desc": "资金费率连续3期方向（+1=连续正，-1=连续负，0=震荡）",
        "high": "资金费率持续为正，多头持续支付空头，做多成本累积",
        "low":  "资金费率持续为负，空头支付多头，做空成本累积",
        "category": "POSITIONING",
    },
    "consecutive_extreme_funding": {
        "desc": "连续极端资金费率计数（|fr|>0.05%）",
        "high": "持续极端费率，套利资金积累了大量反向仓位，结算后均值回归强",
        "low":  "费率恢复正常",
        "category": "POSITIONING",
    },
    "oi_price_divergence_duration": {
        "desc": "OI上涨但价格不涨的持续时间（分钟）",
        "high": "多头建仓但价格不跟涨，吸筹完成后上涨潜力大",
        "low":  "OI与价格同步，正常趋势延续",
        "category": "POSITIONING",
    },
}

# horizon 对应的持仓时间描述
HORIZON_DESC = {
    5:  "5分钟",
    15: "15分钟",
    30: "30分钟",
    60: "1小时",
    120: "2小时",
    240: "4小时",
}


class AutoExplainer:
    """
    将 CausalAtom 翻译为人类可读的策略描述。
    """

    def explain(self, atom: CausalAtom) -> str:
        """生成完整策略解释文本。"""
        meta = FEATURE_META.get(atom.feature)
        hold_desc = HORIZON_DESC.get(atom.horizon, f"{atom.horizon}分钟")

        if meta is None:
            # 未知特征：使用通用模板
            return (
                f"当 {atom.feature} {atom.operator} {atom.threshold:.4g} 时，"
                f"持 {atom.direction} {hold_desc}。\n"
                f"（该特征暂无详细解释）"
            )

        category = meta["category"]
        desc = meta["desc"]
        market_context = meta["high"] if atom.operator == ">" else meta["low"]
        action = "做多" if atom.direction == "long" else "做空"
        signal_word = "偏高" if atom.operator == ">" else "偏低"

        lines = [
            f"【{category}维度】{desc}",
            f"",
            f"触发条件: {atom.feature} {atom.operator} {atom.threshold:.4g}（{signal_word}）",
            f"市场含义: {market_context}",
            f"",
            f"操作: {action}，持仓 {hold_desc}",
            f"",
            f"统计: IC={atom.ic:+.4f}  ICIR={atom.icir:+.3f}  "
            f"WR={atom.win_rate*100:.1f}%  PF={atom.profit_factor:.2f}  "
            f"n={atom.n_triggers}",
        ]
        return "\n".join(lines)

    def short_desc(self, atom: CausalAtom) -> str:
        """一行短描述，适合表格显示。"""
        meta = FEATURE_META.get(atom.feature, {})
        category = meta.get("category", "?")
        hold_desc = HORIZON_DESC.get(atom.horizon, f"{atom.horizon}m")
        action = "L" if atom.direction == "long" else "S"
        return (
            f"[{category}] {atom.feature} {atom.operator} "
            f"{atom.threshold:.3g} → {action} {hold_desc}"
        )
