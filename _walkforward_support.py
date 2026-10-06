# -*- coding: utf-8 -*-
"""walk-forward 验证 short_win(支撑回看窗口): 跨 5 段不重叠 regime,
确认 sw=8 的改进是否只对 S5 过拟合。口径 atr_shift=1, 其余 scalp 不变。
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from box_channel_optimized import run_backtest, PROFILES

BASE = dict(PROFILES["scalp"])
BASE["atr_shift"] = 1
WINS = [4, 8, 12]
# 与 _walkforward_atr.py 相同的 5 段
SEGS = [
    ("S1 起步/震荡", "2023-09-14", "2024-02-01"),
    ("S2 首波拉升", "2024-02-01", "2024-10-01"),
    ("S3 回调/震荡", "2024-10-01", "2025-03-01"),
    ("S4 再起涨   ", "2025-03-01", "2025-09-01"),
    ("S5 主升浪   ", "2025-09-01", "2026-08-29"),
]

# results[sw][label] = (ret%, pf, n)
results = {w: {} for w in WINS}
for w in WINS:
    for label, frm, to in SEGS:
        args = dict(BASE)
        args["short_win"] = w
        t0 = time.time()
        m = run_backtest(extra_args=args, fromdate=frm, todate=to, quiet=True)
        results[w][label] = (m["total_ret"] * 100, m["pf"], m["closed"])
        print(f"  done sw={w} {label} ret={m['total_ret']*100:+.1f}% PF={m['pf']:.2f} n={m['closed']} ({time.time()-t0:.0f}s)", flush=True)

print()
print("=" * 100)
print("逐段矩阵 (每格 ret% / PF / n)")
print("=" * 100)
hdr = f"  {'segment':<14}" + "".join(f"{'sw='+str(w):>22}" for w in WINS)
print(hdr)
for label, _, _ in SEGS:
    row = f"  {label:<14}"
    for w in WINS:
        r, pf, n = results[w][label]
        row += f"{r:>+6.1f}%/{pf:.2f}/{n:>4}"
        row = row.ljust(len(row)) + "  " if w != WINS[-1] else row + "  "
    print(row)

print()
print("=" * 100)
print("汇总: 每段最佳 sw + 正段数(含/不含S5)")
print("=" * 100)
# 正段数
for w in WINS:
    pos = sum(1 for (label, _, _) in SEGS if results[w][label][0] > 0)
    pos_noS5 = sum(1 for (label, _, _) in SEGS[:-1] if results[w][label][0] > 0)  # 排除 S5
    nons5 = sum(results[w][label][0] for (label, _, _) in SEGS[:-1])  # 非S5合计
    print(f"  sw={w}: 正段 {pos}/5, 不含S5正段 {pos_noS5}/4, 非S5合计收益 {nons5:+.1f}%")

# 哪段 sw=8 击败 sw=4
print()
print("  sw=8 vs sw=4 逐段:")
for label, _, _ in SEGS:
    r8 = results[8][label][0]
    r4 = results[4][label][0]
    verdict = "sw8胜" if r8 > r4 else ("sw4胜" if r4 > r8 else "持平")
    print(f"    {label:<14} sw4={r4:+.1f}%  sw8={r8:+.1f}%  → {verdict}")
