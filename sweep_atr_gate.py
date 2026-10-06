import sys
sys.path.insert(0, r"D:\workbuddy\tushare\XAUUSD\xauusd策略")
import box_channel_optimized as m

WINDOWS = {
    "TEST 2025-06~2026-08": ("2025-06-01", "2026-08-29"),
    "FULL 2023-09~2026-08": ("2023-09-14", "2026-08-29"),
}

# 固定 atr_stop_mult=1.5; 扫 atr_regime_gate(开/关) × atr_regime_mult
CONFIGS = [
    ("gate=off (基线)", dict(atr_regime_gate=False)),
    ("gate on 1.2", dict(atr_regime_gate=True, atr_regime_mult=1.2)),
    ("gate on 1.4", dict(atr_regime_gate=True, atr_regime_mult=1.4)),
    ("gate on 1.6", dict(atr_regime_gate=True, atr_regime_mult=1.6)),
    ("gate on 1.8", dict(atr_regime_gate=True, atr_regime_mult=1.8)),
    ("gate on 2.0", dict(atr_regime_gate=True, atr_regime_mult=2.0)),
]

for wname, (fd, td) in WINDOWS.items():
    print("=" * 80)
    print(f"{wname}   (atr_stop_mult=1.5, trailing=True, r_mult=1.75, MA96 过滤)")
    print("=" * 80)
    print(f"{'config':>14} {'ret%':>8} {'real%':>7} {'DD%':>7} {'PF':>6} {'Sharpe':>7} {'trades':>7} {'win%':>6}")
    rows = []
    for cname, cov in CONFIGS:
        extra = dict(m.PROFILES["scalp"])
        extra["atr_stop_mult"] = 1.5
        extra.update(cov)
        r = m.run_backtest(freq="15m", extra_args=extra, cash=1000.0, leverage=1000.0,
                           lot=0.01, spread=0.33, commission=0.0,
                           fromdate=fd, todate=td, quiet=True)
        rows.append((cname, r))
        print(f"{cname:>14} {r['total_ret']*100:7.1f} {r['realized_ret']*100:7.1f} {r['maxdd']:7.1f} "
              f"{r['pf']:6.2f} {r['sharpe']:7.2f} {r['closed']:7d} {r['win_rate']*100:6.1f}")
    best = max(rows, key=lambda x: (x[1]['realized_ret'] * max(x[1]['pf'], 0), -x[1]['maxdd']))
    print(f">> 该窗(已实现×PF, DD小优先)最优: {best[0]} "
          f"(账户 {best[1]['total_ret']*100:.1f}% / 已实现 {best[1]['realized_ret']*100:.1f}% / "
          f"PF {best[1]['pf']:.2f} / DD {best[1]['maxdd']:.1f}%)")
    print()
