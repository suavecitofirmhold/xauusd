# -*- coding: utf-8 -*-
"""对比 max_positions=1(原单仓位基线) vs max_positions=5(并发) 在 scalp 画像下的回测表现。
直接复用 box_channel_optimized 的 run_backtest, 同一数据/同一画像, 仅并发上限不同。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import box_channel_optimized as m

PROFILE = "scalp"
KW = dict(freq="15m", cash=1000.0, leverage=1000.0, lot=0.01, spread=0.33,
          commission=0.0, quiet=True)

def run_cfg(n, concurrent):
    extra = dict(lot=0.01, max_positions=n, concurrent=concurrent)
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

print("=" * 78)
print(f"对比画像: {PROFILE}  数据: xauusd_15m_utc.csv(全样本)  cash=1000 lev=1000 lot=0.01")
print("=" * 78)

CONFIGS = [
    (1, False, "max=1 基线(非并发)"),
    (3, True,  "max=3 并发"),
    (5, True,  "max=5 并发"),
]

res = {}
for n, conc, label in CONFIGS:
    r = run_cfg(n, conc)
    r["_label"] = label
    r["_depth"] = max_depth(r["trades"])
    res[(n, conc)] = r

def col_vals(key, pct=False):
    return [res[(c[0], c[1])][key] * 100 if pct else res[(c[0], c[1])][key] for c in CONFIGS]

labels = [c[2] for c in CONFIGS]
hdr = f"{'指标':<18}" + "".join(f"{lab:>16}" for lab in labels)
print(hdr)
print("-" * 78)

def prow(name, vals, pct=False):
    if pct:
        cells = [f"{v:+.2f}%" for v in vals]
    else:
        cells = [f"{v:.3f}" for v in vals]
    print(f"{name:<18}" + "".join(f"{c:>16}" for c in cells))

def prow_dd(name, vals):
    cells = [f"{v:.2f}%" for v in vals]
    print(f"{name:<18}" + "".join(f"{c:>16}" for c in cells))

prow("累计收益", col_vals("total_ret", pct=True), pct=True)
prow("年化", col_vals("ann", pct=True), pct=True)
prow_dd("最大回撤", col_vals("maxdd"))
prow("Sharpe", col_vals("sharpe"))
prow("ProfitFactor", col_vals("pf"))
prow("交易笔数", col_vals("closed"))
prow("胜率", col_vals("win_rate", pct=True), pct=True)
prow("盈利笔数", col_vals("won"))
prow("亏损笔数", col_vals("lost"))
prow("入场次数", col_vals("entries"))
prow("最大并发深度", [res[(c[0], c[1])]["_depth"] for c in CONFIGS])
prow("隔夜平笔数", col_vals("overnight_eod"))
print("-" * 78)
finals = "  ".join(f"{lab.split()[0]}:{res[(c[0], c[1])]['final']:.2f}" for c, lab in zip(CONFIGS, labels))
print(f"最终权益(起始1000.00): {finals}")
print(f"备注: 并发模式=逐槽位冷却+每信号bar允许多空闲槽位同开(金字塔); 非并发=原全局冷却每根仅开1槽位")
