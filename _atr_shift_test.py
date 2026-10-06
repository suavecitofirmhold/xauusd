# -*- coding: utf-8 -*-
"""决定性测试: EA 的隐含 ATR 到底对应 Python 的 atr[0] 还是 atr[-1]?
方法: 算 Python M15 的 ATR(14) 序列, 对每笔配对成功的交易, 比较
      EA隐含ATR 与 Python 在 T / T-15min / T+15min 三个位置的 ATR。
"""
import csv, statistics
import numpy as np
import pandas as pd
from box_channel_optimized import (run_backtest, PROFILES, load_data,
                                   tmgm_server_offset_hours)

WIN_FROM, WIN_TO = "2024-03-04", "2026-08-26"

def server_to_utc(srv):
    approx = srv - pd.Timedelta(hours=2)
    off = tmgm_server_offset_hours(approx.to_pydatetime())
    return srv - pd.Timedelta(hours=off)

# ---- ATR 序列(Wilder, period 14) ----
df, _, _ = load_data("15m", WIN_FROM, WIN_TO)
h, l, c = df["high"].values, df["low"].values, df["close"].values
n = len(c)
tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
p = 14
atr = np.full(len(tr), np.nan)
atr[p - 1] = tr[:p].mean()
for i in range(p, len(tr)):
    atr[i] = (atr[i - 1] * (p - 1) + tr[i]) / p
atr_s = pd.Series(atr, index=df.index[1:])   # atr_s[t] = ATR 截至 bar t

def atr_at(t):
    try:
        v = atr_s.loc[t]
        return float(v) if not pd.isna(v) else None
    except KeyError:
        return None

# ---- Python 开仓(隐含 ATR = risk/1.5, 即 atr[0]) ----
m = run_backtest(freq="15m", extra_args=dict(PROFILES["scalp"]),
                 fromdate=WIN_FROM, todate=WIN_TO, quiet=True)
py = []
for t in m["trades"]:
    if t["kind"] != "open":
        continue
    e, s = t.get("price"), t.get("stop")
    if e is None or s is None:
        continue
    d = pd.Timestamp(t["dt"])
    py.append(dict(dt=d.tz_localize(None) if d.tzinfo else d, side=t["side"],
                   risk=abs(e - s), atr=abs(e - s) / 1.5))
py.sort(key=lambda x: x["dt"])

# ---- EA 开仓 ----
EA = r"C:\Users\18628\AppData\Roaming\MetaQuotes\Tester\7643C0B96C7AD5841307C9E1EB0B9252\Agent-127.0.0.1-3000\MQL5\Files\BoxChannelScalp_trades.csv"
rows = list(csv.DictReader(open(EA, encoding="utf-8")))
ea = []
for r in rows:
    if r["action"] != "open":
        continue
    try:
        e, s = float(r["price"]), float(r["stop"])
    except (ValueError, KeyError):
        continue
    srv = pd.Timestamp(r["time"].replace(".", "-").replace(" ", "T"))
    ea.append(dict(dt=server_to_utc(srv), side=r["side"], risk=abs(e - s),
                   atr=abs(e - s) / 1.5))

# ---- 逐笔比较: EA隐含ATR vs Python ATR 在 T-1bar / T / T+1bar ----
res = {"m15": [], "0": [], "p15": []}
for a in ea:
    best, bd = None, 1e18
    for q in py:
        d = abs((q["dt"] - a["dt"]).total_seconds()) / 60.0
        if d < bd:
            bd, best = d, q
    if best is None or bd > 15 or best["side"] != a["side"]:
        continue
    T = best["dt"]
    for key, off in (("m15", -15), ("0", 0), ("p15", 15)):
        v = atr_at(T + pd.Timedelta(minutes=off))
        if v and v > 0:
            res[key].append(a["atr"] / v)

print("=" * 72)
print("EA 隐含ATR / Python ATR 比值 (越接近 1.0 说明对应该位移)")
print("=" * 72)
for key, label in (("m15", "Python atr[-1] (T-15min, 上一根已收盘)"),
                   ("0",   "Python atr[0]  (T, 当前bar)"),
                   ("p15", "Python atr at T+15min")):
    v = res[key]
    if v:
        med = statistics.median(v)
        print(f"  {label:<42} n={len(v):>4}  median={med:.4f}  mean={statistics.mean(v):.4f}")
    else:
        print(f"  {label:<42} n=0")

print()
print("=" * 72)
print("判读")
print("=" * 72)
meds = {k: statistics.median(res[k]) for k in res if res[k]}
if meds:
    bestk = min(meds, key=lambda k: abs(meds[k] - 1.0))
    mapping = {"m15": "atr[-1](上一根已收盘 bar)", "0": "atr[0](当前 bar)", "p15": "T+15min"}
    print(f"  EA 的 ATR 最贴近: {mapping[bestk]}  (median 比值 {meds[bestk]:.4f})")
    if bestk == "m15":
        print("  → 与 EA 的 GetATRVal(1) 一致: Python 应改用 atr[-1] 才能对齐")
    elif bestk == "0":
        print("  → EA 已等价于 Python 的 atr[0], 位移不是根因, 需查其它(数据/滑点)")
