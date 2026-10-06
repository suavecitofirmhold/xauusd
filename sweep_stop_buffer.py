import sys
sys.path.insert(0, r"D:\workbuddy\tushare\XAUUSD\xauusd策略")
import box_channel_optimized as m

WINDOWS = {
    "TEST 2025-06~2026-08": ("2025-06-01", "2026-08-29"),
    "FULL 2023-09~2026-08": ("2023-09-14", "2026-08-29"),
}

# 只扫 atr_stop_mult(ATR 止损缓冲倍数); 其余沿用 scalp 画像
MULTS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]

for wname, (fd, td) in WINDOWS.items():
    print("=" * 78)
    print(f"{wname}   (atr_stop=True, trailing=True, r_mult=1.75, MA96 过滤)")
    print("=" * 78)
    print(f"{'mult':>5} {'ret%':>8} {'real%':>7} {'DD%':>7} {'PF':>6} {'Sharpe':>7} {'trades':>7} {'win%':>6} {'swap':>8}")
    rows = []
    for mv in MULTS:
        extra = dict(m.PROFILES["scalp"])
        extra["atr_stop_mult"] = mv
        r = m.run_backtest(freq="15m", extra_args=extra, cash=1000.0, leverage=1000.0,
                           lot=0.01, spread=0.33, commission=0.0,
                           fromdate=fd, todate=td, quiet=True)
        rows.append((mv, r))
        print(f"{mv:5.2f} {r['total_ret']*100:7.1f} {r['realized_ret']*100:7.1f} {r['maxdd']:7.1f} "
              f"{r['pf']:6.2f} {r['sharpe']:7.2f} {r['closed']:7d} {r['win_rate']*100:6.1f} {r['swap_total']:7.1f}")
    # 推荐: 已实现收益×PF 综合排序(避免期末浮仓干扰)
    best = max(rows, key=lambda x: (x[1]['realized_ret'] * max(x[1]['pf'], 0)))
    print(f">> 该窗(已实现收益×PF)最优: atr_stop_mult={best[0]:.2f} "
          f"(账户 {best[1]['total_ret']*100:.1f}% / 已实现 {best[1]['realized_ret']*100:.1f}% / "
          f"PF {best[1]['pf']:.2f} / DD {best[1]['maxdd']:.1f}%)")
    print()
