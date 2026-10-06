# -*- coding: utf-8 -*-
import csv
from collections import Counter
import pandas as pd
from box_channel_optimized import run_backtest, PROFILES, tmgm_server_offset_hours

FROM = pd.Timestamp("2026-07-01")
TO = pd.Timestamp("2026-08-01")


def s2u(srv):
    approx = srv - pd.Timedelta(hours=2)
    off = tmgm_server_offset_hours(approx.to_pydatetime())
    return srv - pd.Timedelta(hours=off)


EA = r"C:\Users\18628\AppData\Roaming\MetaQuotes\Tester\7643C0B96C7AD5841307C9E1EB0B9252\Agent-127.0.0.1-3000\MQL5\Files\BoxChannelScalp_trades.csv"
rows = list(csv.DictReader(open(EA, encoding="utf-8")))
opens, closes = [], []
for r in rows:
    srv = pd.Timestamp(r["time"].replace(".", "-").replace(" ", "T"))
    utc = s2u(srv)
    rec = dict(srv=srv, dt=utc, side=r["side"], price=float(r["price"]),
               profit=float(r["profit"]), reason=r["reason"])
    (opens if r["action"] == "open" else closes).append(rec)
ea = []
used = [False] * len(opens)
for c in closes:
    for i, o in enumerate(opens):
        if not used[i] and o["side"] == c["side"] and o["srv"] <= c["srv"]:
            used[i] = True
            ea.append(dict(side=o["side"], entry=o["price"], exit=c["price"],
                           profit=c["profit"], o_srv=o["srv"], o_utc=o["dt"],
                           reason=c["reason"]))
            break
win = [t for t in ea if FROM <= t["o_srv"] < TO]

print("EA 窗口 33 笔 by reason:")
for reason in ["broker", "eod"]:
    sub = [t for t in win if t["reason"] == reason]
    if not sub:
        continue
    w = sum(1 for t in sub if t["profit"] > 0)
    print(f"  {reason}: {len(sub)}笔  WR={w/len(sub)*100:.1f}%  "
          f"net=${sum(t['profit'] for t in sub):+.2f}")

# python opens
extra = {**PROFILES["scalp"], "atr_shift": 1,
         "trail_activate_atr": 0.0, "trail_atr_mult": 1.5, "trail_be_buffer": 0.0}
py = run_backtest(freq="15m", extra_args=extra, cash=1000.0,
                  fromdate=FROM, todate=TO, quiet=True)
pyo = []
for t in py["trades"]:
    if t.get("kind") != "open":
        continue
    d = pd.Timestamp(t["dt"])
    if d.tzinfo:
        d = d.tz_localize(None)
    pyo.append(dict(dt=d, side=t.get("side"), entry=t.get("price")))
pyo.sort(key=lambda x: x["dt"])

print()
print("未配对(>15min或方向不符)的 EA 开仓:")
for a in win:
    best, bd = None, 1e18
    for p in pyo:
        dd = abs((p["dt"] - a["o_utc"]).total_seconds()) / 60.0
        if dd < bd:
            bd, best = dd, p
    same_dir = best and best["side"] == a["side"]
    if not (same_dir and bd <= 15):
        print(f"  EA {a['o_srv']} {a['side']:>5} entry={a['entry']:.2f} "
              f"prof={a['profit']:+.2f} r={a['reason']} | nearestPy {best['dt']} "
              f"{best['side']:>5} dt={bd:.0f}min")
