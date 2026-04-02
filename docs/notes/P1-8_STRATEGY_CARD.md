# Strategy Card: P1-8 VWAP偏离+量枯竭

**Direction**: LONG / SHORT (Both)  
**Physical Attribution**: FLOW_EXHAUSTION + ALGO_EXECUTION  
**Status**: Exit strategy documented (fixed hold recommended)

## ENTRY CONDITIONS

Detected in BTC USDT perpetual futures pipeline backtest.

**LONG**: Price pushed below VWAP with volume exhaustion  
**SHORT**: Price pulled above VWAP with volume exhaustion  

Sample: 964 total trades (377 LONG, 587 SHORT)  
Observation window: 120 bars (per detection)  
MFE > 0.04% rate: ~95% (high direction judgment accuracy)

## EXIT STRATEGY ANALYSIS

### MFE Characteristics
- Avg MFE: 0.7509% (LONG: 0.9078%, SHORT: 0.6501%)
- Avg MFE Offset: 68.8 bars (LONG: 71.6 bars, SHORT: 67.0 bars)
- **Key Finding**: Peak profit occurs far out (~70 bars), not clustered early

### Exit Approach: Fixed Hold (Not Dynamic Conditions)

**Tested Dynamic Conditions**: Top 3 feature-based combos were extracted but underperformed

Top feature deltas at MFE peak:
- LONG: vwap_deviation_vs_entry (d=1.004), position_in_range_24h_vs_entry (d=0.972)
- SHORT: position_in_range_24h_vs_entry (d=1.013), dist_to_24h_high_vs_entry (d=1.004)

**Result**: Dynamic combos trigger too early, exiting before actual recovery completes.

### Recommended Exit Rule: Fixed Hold

**LONG**: Hold 90 bars
- Win Rate: 63.4%
- Total Net P&L: +55.05%
- Profit Factor: 1.53
- Hard stop-loss: -0.3%
- Min hold before checks: 3 bars

**SHORT**: Hold 90 bars
- Win Rate: 56.7%
- Total Net P&L: +46.48%
- Profit Factor: 1.38
- Hard stop-loss: -0.3%
- Min hold before checks: 3 bars

## PHYSICAL EXPLANATION

**Entry**: Volume-weighted average price (VWAP) represents fair value determined by market makers' execution algorithms. When price diverges sharply from VWAP with sudden volume collapse, it signals:
1. A one-sided order imbalance has exhausted
2. Market makers have finished their large order execution
3. Price is far from equilibrium and traders are trapped in wrong positions

**Exit**: Unlike fast mean-reversion strategies (P0-2), P1-8's recovery from VWAP exhaustion is gradual. The reversion spans 70+ bars as:
- Trapped positions gradually close
- Fresh volume arrives at recovered price levels
- Market makers rebuild counterbalance

Fixed 90-bar hold captures this full reversion arc. Dynamic exits based on immediate feature changes trigger too early because they detect "price moved far from VWAP" (true at entry + 10 bars too), not "reversion is complete" (true only at bar 70+).

## GUARDRAILS

- Hard stop-loss: -0.3% per trade
- Max hold: 90 bars (1.5 hours)
- Min hold: 3 bars (avoid exit noise)
- Observation window for MFE analysis: 120 bars

## Notes

- P1-8 differs fundamentally from P0-2 in exit timing: use fixed hold instead of dynamic conditions
- Consider tighter stops only if slippage/spread costs justify it
- This strategy benefits from holding longer than initial instinct (most traders exit too early)
