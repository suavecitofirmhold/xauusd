# -*- coding: utf-8 -*-
"""敏感性: scalp 支撑/压力聚类窗口 short_win 加长, 是否提升 PF(尤其 S2 亏损段)。
口径: atr_shift=1, 其余 scalp 参数不变。
"""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from box_channel_optimized import run_backtest, PROFILES

BASE = dict(PROFILES["scalp"])
BASE["atr_shift"] = 1
WINS = [4, 8, 12, 16, 24]
SEGS = [
    ("FULL 23-09~26-08", "2023-09-14", "2026-08-29"),
    ("S2 首波拉升", "2024-02-01", "2024-10-01"),
    ("S5 主升浪", "2025-09-01", "2026-08-29"),
    ("JUL 26-07", "2026-07-01", "2026-08-01"),
]

hdr = [s[0].split()[0] for s in SEGS]
print(f"{'short_win':<10}" + "".join(f"{h:>16}" for h in hdr))
print("-" * (10 + 16 * len(SEGS)))
for sw in WINS:
    cells = []
    for label, frm, to in SEGS:
        args = dict(BASE)
        args["short_win"] = sw
        t0 = time.time()
        m = run_backtest(extra_args=args, fromdate=frm, todate=to, quiet=True)
        dt = time.time() - t0
        cells.append(f"ret{m['total_ret']*100:+.1f}/PF{m['pf']:.2f}/n{m['closed']}")
    print(f"{sw:<10}" + "".join(f"{c:>16}" for c in cells))
    print(f"{'':<10}" + "".join(f"{'('+hdr[i]+')':>16}" for i in range(len(SEGS))), flush=True)
