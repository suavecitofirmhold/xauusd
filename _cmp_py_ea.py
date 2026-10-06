# -*- coding: utf-8 -*-
"""Python vs EA 逐项对拍: 收益/PF/笔数/胜率/毛利毛损/平均盈亏/多空分桶/持仓时长。
用法: python _cmp_py_ea.py
"""
import csv, statistics
from datetime import datetime
from box_channel_optimized import run_backtest, PROFILES

WIN_FROM, WIN_TO = "2024-03-04", "2026-08-26"

# ---- Python 侧(必须传 scalp profile, 否则跑默认参数) ----
# ATR_SHIFT=1 与 EA 的 GetATRVal(1)(上一根已收盘 bar)对齐; 改 0 回到 backtrader 默认口径
ATR_SHIFT = 1
_py_args = dict(PROFILES["scalp"])
_py_args["atr_shift"] = ATR_SHIFT
m = run_backtest(freq="15m", extra_args=_py_args,
                 fromdate=WIN_FROM, todate=WIN_TO, quiet=True)
print(f"[Python 口径] atr_shift={ATR_SHIFT} (1=上一根已收盘bar, 与 EA GetATRVal(1) 对齐)")

tl = m["trades"]
open_stack, paired = [], []
for t in tl:
    if t["kind"] == "open":
        open_stack.append(t)
    elif t["kind"] == "close":
        if open_stack:
            paired.append((open_stack.pop(), t))

def pnl_of(o, c):
    return (1 if o["side"] == "long" else -1) * (c["price"] - o["price"])

def stats(pnls):
    if not pnls:
        return None
    wins = [p for p in pnls if p >= 0]
    losses = [p for p in pnls if p < 0]
    gp, gl = sum(wins), abs(sum(losses))
    return dict(n=len(pnls), wr=len(wins) / len(pnls), gp=gp, gl=gl,
                pf=(gp / gl) if gl else float("inf"),
                avgw=(sum(wins) / len(wins)) if wins else 0.0,
                avgl=(sum(losses) / len(losses)) if losses else 0.0,
                net=sum(pnls), avg=sum(pnls) / len(pnls))

py_all = [pnl_of(o, c) for o, c in paired]
py_long = [pnl_of(o, c) for o, c in paired if o["side"] == "long"]
py_short = [pnl_of(o, c) for o, c in paired if o["side"] == "short"]
py_holds = [(c["dt"] - o["dt"]).total_seconds() / 60.0 for o, c in paired
            if o.get("dt") is not None and c.get("dt") is not None]

print("=" * 74)
print(f"PYTHON (scalp, atr_stop=1.5, {WIN_FROM} ~ {WIN_TO})")
print("=" * 74)
print(f"  收益 {m['total_ret']*100:+.2f}%  PF {m['pf']:.3f}  DD {m['maxdd']:.2f}%  Sharpe {m['sharpe']:.2f}")
s = stats(py_all)
print(f"  笔数 {s['n']}  胜率 {s['wr']*100:.1f}%  毛利 {s['gp']:.2f}  毛损 {s['gl']:.2f}  净 {s['net']:+.2f}")
print(f"  平均盈 {s['avgw']:+.3f}  平均亏 {s['avgl']:+.3f}  平均/笔 {s['avg']:+.3f}")
for nm, arr in (("Long ", py_long), ("Short", py_short)):
    t = stats(arr)
    print(f"  {nm} n={t['n']:>4}  wr={t['wr']*100:>5.1f}%  pf={t['pf']:.3f}  net={t['net']:>+8.2f}  avgw={t['avgw']:+.3f}  avgl={t['avgl']:+.3f}")
if py_holds:
    print(f"  持仓(min) mean={statistics.mean(py_holds):.1f} median={statistics.median(py_holds):.1f} max={max(py_holds):.1f}")

# ---- EA 侧(必须先做 eod/broker 去重, 与 analyze_ea_csv.py 一致) ----
EA = r"C:\Users\18628\AppData\Roaming\MetaQuotes\Tester\7643C0B96C7AD5841307C9E1EB0B9252\Agent-127.0.0.1-3000\MQL5\Files\BoxChannelScalp_trades.csv"
rows = list(csv.DictReader(open(EA, encoding="utf-8")))

cleaned, i = [], 0
while i < len(rows):
    r = rows[i]
    if r["action"] == "open":
        cleaned.append(r)
        i += 1
    elif r["action"] == "close":
        group, j = [r], i + 1
        while j < len(rows) and rows[j]["action"] == "close" and rows[j]["side"] == r["side"]:
            group.append(rows[j])
            j += 1
        for c in group:
            try:
                c["profit_f"] = float(c["profit"])
            except ValueError:
                c["profit_f"] = 0.0
        brokers = [c for c in group if c.get("reason") == "broker"]
        best = max(brokers, key=lambda x: abs(x["profit_f"])) if brokers else max(group, key=lambda x: abs(x["profit_f"]))
        cleaned.append(best)
        i = j
    else:
        cleaned.append(r)
        i += 1

ea_open, ea_close = [r for r in cleaned if r["action"] == "open"], [r for r in cleaned if r["action"] == "close"]
pairs = list(zip(ea_open, ea_close))
ea_all = [float(c["profit"]) for o, c in pairs]
ea_long = [float(c["profit"]) for o, c in pairs if o["side"] == "long"]
ea_short = [float(c["profit"]) for o, c in pairs if o["side"] == "short"]

def eparse(t):
    return datetime.strptime(t, "%Y.%m.%d %H:%M:%S")
ea_holds = [(eparse(c["time"]) - eparse(o["time"])).total_seconds() / 60.0 for o, c in pairs]
ea_holds = [h for h in ea_holds if h >= 0]

bal, peak, maxdd = 1000.0, 1000.0, 0.0
for p in ea_all:
    bal += p
    peak = max(peak, bal)
    maxdd = max(maxdd, (peak - bal) / peak)

print()
print("=" * 74)
print(f"EA (atr_stop=1.5, {WIN_FROM} ~ {WIN_TO})")
print("=" * 74)
s = stats(ea_all)
print(f"  收益 {(bal/1000-1)*100:+.2f}%  PF {s['pf']:.3f}  DD {maxdd*100:.2f}%")
print(f"  笔数 {s['n']}  胜率 {s['wr']*100:.1f}%  毛利 {s['gp']:.2f}  毛损 {s['gl']:.2f}  净 {s['net']:+.2f}")
print(f"  平均盈 {s['avgw']:+.3f}  平均亏 {s['avgl']:+.3f}  平均/笔 {s['avg']:+.3f}")
for nm, arr in (("Long ", ea_long), ("Short", ea_short)):
    t = stats(arr)
    print(f"  {nm} n={t['n']:>4}  wr={t['wr']*100:>5.1f}%  pf={t['pf']:.3f}  net={t['net']:>+8.2f}  avgw={t['avgw']:+.3f}  avgl={t['avgl']:+.3f}")
if ea_holds:
    print(f"  持仓(min) mean={statistics.mean(ea_holds):.1f} median={statistics.median(ea_holds):.1f} max={max(ea_holds):.1f}")

# ---- 差异 ----
print()
print("=" * 74)
print("差异 (EA - Python)")
print("=" * 74)
pa, pb = stats(py_all), stats(ea_all)
print(f"  净收益   {pb['net']-pa['net']:+.2f} USD   (EA/Python = {pb['net']/pa['net']:.2f}x)")
print(f"  收益%    {(bal/1000-1)*100:+.2f}% vs {m['total_ret']*100:+.2f}%   Δ{((bal/1000-1)-m['total_ret'])*100:+.2f}pp")
print(f"  PF       {pb['pf']:.3f} vs {pa['pf']:.3f}   Δ{pb['pf']-pa['pf']:+.3f}")
print(f"  笔数     {pb['n']} vs {pa['n']}   Δ{pb['n']-pa['n']:+d}  ({(pb['n']/pa['n']-1)*100:+.1f}%)")
print(f"  胜率     {pb['wr']*100:.1f}% vs {pa['wr']*100:.1f}%   Δ{(pb['wr']-pa['wr'])*100:+.1f}pp")
print(f"  平均盈   {pb['avgw']:+.3f} vs {pa['avgw']:+.3f}   Δ{pb['avgw']-pa['avgw']:+.3f}")
print(f"  平均亏   {pb['avgl']:+.3f} vs {pa['avgl']:+.3f}   Δ{pb['avgl']-pa['avgl']:+.3f}")
print(f"  毛利     {pb['gp']:.2f} vs {pa['gp']:.2f}   Δ{pb['gp']-pa['gp']:+.2f}")
print(f"  毛损     {pb['gl']:.2f} vs {pa['gl']:.2f}   Δ{pb['gl']-pa['gl']:+.2f}")
