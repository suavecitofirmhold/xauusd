# -*- coding: utf-8 -*-
"""
诊断 M5 策略 v1: 打印出场原因分布、R 分布、以及前 N 笔成交明细(入场/止损/目标/出场/盈亏/R)。
用法: python m5_debug_trades.py [--n 15]
"""
import argparse
import numpy as np
from xauusd_m5_multisignal import run_m5_backtest, M5_PROFILES

ap = argparse.ArgumentParser()
ap.add_argument("--from", dest="frm", default="2025-04-15")
ap.add_argument("--to", dest="to", default="2026-09-11")
ap.add_argument("--n", type=int, default=15)
a = ap.parse_args()

r = run_m5_backtest(extra_args=dict(M5_PROFILES["default"]),
                    fromdate=a.frm, todate=a.to, quiet=False)

print("\n" + "=" * 90)
print("出场原因分布")
print("=" * 90)
for k, v in sorted(r["reason_hist"].items(), key=lambda kv: -kv[1]):
    print(f"  {str(k):<10} {v:>5}  ({v/max(r['closed'],1)*100:.1f}%)")

# FIFO 配对
tl = r["trades"]
stack, paired = [], []
for t in tl:
    if t["kind"] == "open":
        stack.append(t)
    elif t["kind"] == "close" and stack:
        paired.append((stack.pop(), t))

Rs = []
rows = []
for o, c in paired:
    sign = 1 if o["side"] == "long" else -1
    pnl = (c["price"] - o["price"]) * sign
    risk = abs(o["price"] - (o["stop"] or 0))
    R = pnl / risk if risk > 0 else float("nan")
    Rs.append(R)
    rows.append((o["dt"], o["side"], o["price"], o["stop"], o["target"],
                 c["dt"], c["price"], pnl, R, c.get("reason")))

Rs = np.array([x for x in Rs if x == x])
print("\n" + "=" * 90)
print(f"R 分布  n={len(Rs)}")
print("=" * 90)
if len(Rs):
    print(f"  mean={Rs.mean():+.3f}  median={np.median(Rs):+.3f}  "
          f"p10={np.percentile(Rs,10):+.2f}  p90={np.percentile(Rs,90):+.2f}  "
          f"min={Rs.min():+.2f}  max={Rs.max():+.2f}")
    print(f"  |R|>2 占比: {(np.abs(Rs)>2).mean()*100:.1f}%   "
          f"|R|>3 占比: {(np.abs(Rs)>3).mean()*100:.1f}%")

print("\n" + "=" * 90)
print(f"前 {a.n} 笔成交")
print("=" * 90)
print(f"  {'open_dt':<19}{'side':<6}{'entry':>9}{'stop':>9}{'target':>9}"
      f"{'exit':>9}{'pnl':>9}{'R':>8}  {'rsn':<7}{'hold'}")
for i, (odt, side, e, s, t, cdt, xp, pnl, R, rsn) in enumerate(rows[:a.n]):
    hold = (cdt - odt).total_seconds() / 60.0
    print(f"  {str(odt)[:19]:<19}{side:<6}{e:>9.2f}{(s or 0):>9.2f}{(t or 0):>9.2f}"
          f"{xp:>9.2f}{pnl:>+9.2f}{R:>+8.2f}  {str(rsn):<7}{hold:.0f}m")

print("\n" + "=" * 90)
print("拒绝漏斗 TOP10")
print("=" * 90)
for k, v in sorted(r["rejects"].items(), key=lambda kv: -kv[1])[:10]:
    print(f"  {k:<20} {v}")
