# -*- coding: utf-8 -*-
"""
Step 2 回归测试: 验证 box_channel_optimized.py 的 5m 补丁对既有 15m 调用零影响。
  1) 默认调用(primary_freq=None) 与 显式 15m+1h 调用 结果必须完全一致
  2) 结果与打补丁前的既有基线一致(PROFILES["scalp"] 锁定配置)
  3) 5m 数据可正常加载(行数/起止)
用法: python m5_regression_check.py
"""
import box_channel_optimized as M

WIN_FROM, WIN_TO = "2024-03-04", "2026-08-26"


def show(tag, r):
    print(f"  {tag:<26} ret={r['total_ret']*100:+.2f}%  PF={r['pf']:.3f}  "
          f"WR={r['win_rate']*100:.1f}%  closed={r['closed']:>4}  DD={r['maxdd']:.2f}%  "
          f"final=${r['final']:.2f}")


def main():
    print("=" * 100)
    print("A. PROFILES['scalp'] 当前锁定参数(关键项)")
    print("=" * 100)
    p = M.PROFILES["scalp"]
    for k in ("max_ma_dist_atr", "ma_dist_regime_cond", "ma_dist_lowvol_mult",
              "trailing", "trail_activate_atr", "trail_atr_mult", "trail_be_buffer",
              "atr_stop_mult", "r_mult", "atr_regime_gate", "atr_regime_mult"):
        print(f"  {k:<24} = {p.get(k)}")

    print()
    print("=" * 100)
    print(f"B. 回归: 默认调用 vs 显式 15m+1h  窗口 {WIN_FROM} ~ {WIN_TO}")
    print("=" * 100)
    extra = {**M.PROFILES["scalp"], "atr_shift": 1}
    r_default = M.run_backtest(freq="15m", extra_args=extra, cash=1000.0,
                               fromdate=WIN_FROM, todate=WIN_TO, quiet=True)
    show("默认(primary_freq=None)", r_default)

    r_explicit = M.run_backtest(freq="15m", extra_args=extra, cash=1000.0,
                                fromdate=WIN_FROM, todate=WIN_TO, quiet=True,
                                primary_freq="15m", context_freqs=("1h",))
    show("显式 primary=15m,ctx=(1h,)", r_explicit)

    same = (r_default["closed"] == r_explicit["closed"]
            and abs(r_default["total_ret"] - r_explicit["total_ret"]) < 1e-12
            and abs(r_default["final"] - r_explicit["final"]) < 1e-9)
    print(f"\n  → 两条路径完全一致: {same}")

    print()
    print("=" * 100)
    print("C. 5m 数据加载检查")
    print("=" * 100)
    df5, _, sp5 = M.load_data("5m")
    print(f"  xauusd_5m_utc.csv 行数={len(df5)}  "
          f"{df5.index[0]} -> {df5.index[-1]}")
    print(f"  real spread(5m) 非空={sp5 is not None and sp5.notna().sum() > 0}  "
          f"中位={None if sp5 is None else sp5.median()}")
    df15, _, sp15 = M.load_data("15m")
    print(f"  xauusd_15m_utc.csv 行数={len(df15)} (未被本次改动影响)")


if __name__ == "__main__":
    main()
