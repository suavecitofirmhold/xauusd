"""
对比 Python scalp 与用户 EA 回测(同期窗口)。
跑两套 Python 配置, 覆盖"EA 用新两段式"与"EA 用旧单段"两种可能:
  NEW = 锁定默认 trail_activate_atr=1.0 / trail_atr_mult=1.0 / trail_be_buffer=0.0
  OLD = trail_activate_atr=0.0 / trail_atr_mult=1.5 / trail_be_buffer=0.0  (终端旧代码口径)
两者均 atr_shift=1 (对齐 EA GetATRVal(1))。
用法: python cmp_ea_window.py
"""
import sys, os
import pandas as pd
import box_channel_optimized as M

FROM = pd.Timestamp("2026-07-01")
TO = pd.Timestamp("2026-08-01")


def run(tag, override):
    extra = {**M.PROFILES["scalp"], "atr_shift": 1}
    extra.update(override)
    res = M.run_backtest(freq="15m", extra_args=extra, cash=1000.0,
                         fromdate=FROM, todate=TO, quiet=True)
    print(f"[{tag}] net={res['total_ret']*100:+.1f}%  PF={res['pf']:.2f}  "
          f"WR={res['win_rate']*100:.1f}%  trades={res['closed']}  "
          f"entries={res['entries']}  DD={res['maxdd']:.1f}%  final=${res['final']:.1f}")
    return res


def main():
    print("=" * 78)
    print(f"窗口 {FROM.date()} ~ {TO.date()}  (15m, cash=$1000, atr_shift=1)")
    print("=" * 78)
    run("NEW 1.0/1.0/0.0 (若EA已同步两段式)", {})
    run("OLD 0.0/1.5/0.0 (终端旧代码=你刚跑的EA口径)",
        dict(trail_activate_atr=0.0, trail_atr_mult=1.5, trail_be_buffer=0.0))


if __name__ == "__main__":
    main()
