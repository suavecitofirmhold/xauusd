# -*- coding: utf-8 -*-
"""ATR 止损倍数的 walk-forward / 分 regime 验证。
目的: 检验敏感性扫描里 1.3 的优势是跨市场状态稳定, 还是靠单一时段撑起(过拟合)。
口径: atr_shift=1(与 EA GetATRVal(1) 对齐)。
"""
import statistics
from box_channel_optimized import run_backtest, PROFILES

# 与 walk_forward.py 相同的 5 段非重叠 regime
SEGMENTS = [
    ("S1 起步/震荡 23-09~24-02", "2023-09-14", "2024-02-01"),
    ("S2 首波拉升 24-02~24-10", "2024-02-01", "2024-10-01"),
    ("S3 回调/震荡 24-10~25-03", "2024-10-01", "2025-03-01"),
    ("S4 再起涨   25-03~25-09", "2025-03-01", "2025-09-01"),
    ("S5 主升浪   25-09~26-08", "2025-09-01", "2026-08-29"),
]
MULTS = [1.3, 1.4, 1.5, 1.6]

results = {m: [] for m in MULTS}

for m in MULTS:
    args = dict(PROFILES["scalp"])
    args["atr_shift"] = 1
    args["atr_stop_mult"] = m
    for label, frm, to in SEGMENTS:
        r = run_backtest(extra_args=args, fromdate=frm, todate=to, quiet=True)
        results[m].append(dict(seg=label, ret=r["total_ret"] * 100, pf=r["pf"],
                               dd=r["maxdd"], trades=r["closed"],
                               wr=r["win_rate"] * 100, sharpe=r["sharpe"]))
    print(f"  完成 mult={m}", flush=True)

print()
print("=" * 100)
print("分 regime 结果 (atr_shift=1, 与 EA 对齐)")
print("=" * 100)
hdr = f"  {'segment':<26}" + "".join(f"{'m=' + str(m):>18}" for m in MULTS)
print(hdr)
print("  " + "-" * 96)
for i, (label, _, _) in enumerate(SEGMENTS):
    row = f"  {label:<26}"
    for m in MULTS:
        r = results[m][i]
        row += f"{r['ret']:>+9.1f}% PF{r['pf']:>5.2f}"
    print(row)

print()
print("=" * 100)
print("各 mult 汇总(跨 5 段)")
print("=" * 100)
print(f"  {'mult':<7}{'正段数':>8}{'均值':>10}{'中位':>10}{'最差段':>10}{'最好段':>10}{'PF均值':>9}{'DD均值':>9}")
print("  " + "-" * 74)
summ = {}
for m in MULTS:
    rets = [r["ret"] for r in results[m]]
    pfs = [r["pf"] for r in results[m]]
    dds = [r["dd"] for r in results[m]]
    pos = sum(1 for v in rets if v > 0)
    summ[m] = dict(pos=pos, mean=statistics.mean(rets), med=statistics.median(rets),
                   worst=min(rets), best=max(rets), pf=statistics.mean(pfs),
                   dd=statistics.mean(dds))
    s = summ[m]
    print(f"  {m:<7.1f}{s['pos']:>6}/5{s['mean']:>+10.2f}{s['med']:>+10.2f}"
          f"{s['worst']:>+10.2f}{s['best']:>+10.2f}{s['pf']:>9.3f}{s['dd']:>9.2f}")

print()
print("=" * 100)
print("1.3 vs 1.5(现用)逐段正面对比")
print("=" * 100)
print(f"  {'segment':<26}{'m=1.3':>12}{'m=1.5':>12}{'Δ(1.3-1.5)':>14}{'胜者':>8}")
print("  " + "-" * 72)
win13 = 0
for i, (label, _, _) in enumerate(SEGMENTS):
    a, b = results[1.3][i]["ret"], results[1.5][i]["ret"]
    w = "1.3" if a > b else "1.5"
    if a > b:
        win13 += 1
    print(f"  {label:<26}{a:>+11.1f}%{b:>+11.1f}%{a-b:>+13.1f}pp{w:>8}")
print(f"\n  1.3 胜出段数: {win13}/5")

print()
print("=" * 100)
print("判读")
print("=" * 100)
best_by_pos = max(MULTS, key=lambda m: (summ[m]["pos"], summ[m]["mean"]))
print(f"  按[正段数, 均值]最优: mult={best_by_pos}")
for m in MULTS:
    s = summ[m]
    stable = "✅ 跨段稳定" if s["pos"] >= 4 else ("⚠️ 部分段亏损" if s["pos"] >= 3 else "❌ regime 依赖强")
    print(f"    mult={m}: 正段 {s['pos']}/5, 均值 {s['mean']:+.2f}%, "
          f"最差 {s['worst']:+.2f}%, PF均值 {s['pf']:.3f}  → {stable}")
