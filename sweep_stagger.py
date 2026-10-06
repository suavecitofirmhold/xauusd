# -*- coding: utf-8 -*-
"""B 方案验证: 错峰冷却扫描 —— slot k 须比 slot k-1 晚 X 根才能开。
X ∈ {1,2,4,8}, 与「max=1 非并发基线」和「并发 X=0(同 bar 全开)」对照, 比较收益/回撤/并发深度。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import box_channel_optimized as m

PROFILE = "scalp"
KW = dict(freq="15m", cash=1000.0, leverage=1000.0, lot=0.01, spread=0.33,
          commission=0.0, quiet=True)

CONFIGS = [
    ("max=1基线", 1, False, 0),
    ("并发X=0",   5, True,  0),
    ("错峰X=1",   5, True,  1),
    ("错峰X=2",   5, True,  2),
    ("错峰X=4",   5, True,  4),
    ("错峰X=8",   5, True,  8),
]

def run_cfg(n, concurrent, stagger):
    extra = dict(lot=0.01, max_positions=n, concurrent=concurrent, stagger_bars=stagger)
    extra.update(m.PROFILES[PROFILE])
    return m.run_backtest(extra_args=extra, **KW)

def max_depth(trades):
    depth = mx = 0
    for t in trades:
        if t["kind"] == "open":
            depth += 1; mx = max(mx, depth)
        else:
            depth -= 1
    return mx

print("=" * 96)
print(f"错峰冷却扫描(scalp 全样本, $1000/lev1000/lot0.01)  X = slot k 比 slot k-1 晚几根bar才能开")
print("=" * 96)

res = []
for label, n, conc, stag in CONFIGS:
    r = run_cfg(n, conc, stag)
    r["_depth"] = max_depth(r["trades"])
    res.append(r)
    print(f"[完成] {label:<10} 收益{r['total_ret']*100:+8.2f}%  回撤{r['maxdd']:6.2f}%  "
          f"深度{r['_depth']}  笔数{int(r['closed'])}", flush=True)

labels = [c[0] for c in CONFIGS]
print()
print(f"{'指标':<16}" + "".join(f"{lab:>14}" for lab in labels))
print("-" * 96)

def prow(name, key, pct=False, raw=None):
    vals = raw if raw is not None else [r[key] * 100 if pct else r[key] for r in res]
    if pct:
        cells = [f"{v:+.2f}%" for v in vals]
    elif key == "closed" or key == "entries" or key == "overnight_eod":
        cells = [f"{int(v)}" for v in vals]
    else:
        cells = [f"{v:.3f}" for v in vals]
    print(f"{name:<16}" + "".join(f"{c:>14}" for c in cells))

def prow_dd(name):
    cells = [f"{r['maxdd']:.2f}%" for r in res]
    print(f"{name:<16}" + "".join(f"{c:>14}" for c in cells))

prow("累计收益", "total_ret", pct=True)
prow("年化", "ann", pct=True)
prow_dd("最大回撤")
prow("Sharpe", "sharpe")
prow("ProfitFactor", "pf")
prow("Calmar(年化/DD)", None, raw=[r["ann"] / (r["maxdd"]/100.0) if r["maxdd"] > 0 else 0.0 for r in res])
prow("交易笔数", "closed")
prow("胜率", "win_rate", pct=True)
prow("入场次数", "entries")
prow("最大并发深度", None, raw=[r["_depth"] for r in res])
prow("隔夜平笔数", "overnight_eod")
print("-" * 96)
print("最终权益(起始1000.00): " + "  ".join(f"{lab}:{r['final']:.2f}" for lab, r in zip(labels, res)))
print("备注: X=0 为同bar全开(纯金字塔复制); X 越大开仓越错开, 趋于基线")
