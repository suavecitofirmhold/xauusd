# -*- coding: utf-8 -*-
"""按【UTC】把 Python 与 EA 的开仓逐笔配对, 比较同一时点的止损距离(即 ATR 尺度)。
EA CSV 是服务器时间(= UTC + 2/3, 随美国 DST), 必须先转回 UTC 才能与 Python 数据对齐。
"""
import csv, statistics
import pandas as pd
from box_channel_optimized import run_backtest, PROFILES, tmgm_server_offset_hours

WIN_FROM, WIN_TO = "2024-03-04", "2026-08-26"

def server_to_utc(srv):
    """EA 服务器时间 -> UTC(服务器 = UTC + offset, offset 2/3 随美国 DST)"""
    approx = srv - pd.Timedelta(hours=2)
    off = tmgm_server_offset_hours(approx.to_pydatetime())
    return srv - pd.Timedelta(hours=off)

# ---- Python(数据本身已是 UTC) ----
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
    py.append(dict(dt=d.tz_localize(None) if d.tzinfo else d,
                   side=t["side"], entry=e, stop=s, risk=abs(e - s)))
py.sort(key=lambda x: x["dt"])

# ---- EA(服务器时间 -> UTC) ----
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
    ea.append(dict(srv=srv, dt=server_to_utc(srv), side=r["side"],
                   entry=e, stop=s, risk=abs(e - s)))
ea.sort(key=lambda x: x["dt"])

print("=" * 72)
print("前 6 笔对照(EA 服务器时间 / 转 UTC / Python UTC)")
print("=" * 72)
for a in ea[:6]:
    best, bd = None, 1e18
    for p in py:
        d = abs((p["dt"] - a["dt"]).total_seconds()) / 60.0
        if d < bd:
            bd, best = d, p
    print(f"  EA srv={a['srv']}  utc={a['dt']}  {a['side']:>5} entry={a['entry']:.2f} risk={a['risk']:.3f}")
    print(f"  PY utc={best['dt']}  {best['side']:>5} entry={best['entry']:.2f} risk={best['risk']:.3f}   Δt={bd:.0f}min")

# ---- 全局配对 ----
print()
print("=" * 72)
print("逐笔配对比较止损距离(UTC 对齐后)")
print("=" * 72)
matched = []
for a in ea:
    best, bd = None, 1e18
    for p in py:
        d = abs((p["dt"] - a["dt"]).total_seconds()) / 60.0
        if d < bd:
            bd, best = d, p
    if best is not None and bd <= 15 and best["side"] == a["side"]:
        matched.append((a, best, bd))

print(f"  配对成功 {len(matched)} / {len(ea)} 笔 (EA 开仓, 容差 15min 且方向一致)")
if matched:
    dts = [x[2] for x in matched]
    print(f"  配对时差: median={statistics.median(dts):.0f}min  mean={statistics.mean(dts):.1f}min  max={max(dts):.0f}min")
    ea_r = [x[0]["risk"] for x in matched]
    py_r = [x[1]["risk"] for x in matched]
    print(f"  EA     risk mean={statistics.mean(ea_r):.4f}  median={statistics.median(ea_r):.4f}")
    print(f"  Python risk mean={statistics.mean(py_r):.4f}  median={statistics.median(py_r):.4f}")
    print(f"  → EA/Python risk = {statistics.mean(ea_r)/statistics.mean(py_r):.4f}  "
          f"({(statistics.mean(ea_r)/statistics.mean(py_r)-1)*100:+.1f}%)")
    ratios = [a["risk"] / p["risk"] for a, p, _ in matched if p["risk"] > 0]
    if ratios:
        sr = sorted(ratios)
        print(f"  逐笔 risk 比值: median={statistics.median(ratios):.4f}  "
              f"p10={sr[int(len(sr)*0.1)]:.3f}  p90={sr[int(len(sr)*0.9)]:.3f}")
        same = sum(1 for r in ratios if abs(r - 1.0) < 0.02)
        print(f"  比值≈1(±2%)占比: {same/len(ratios)*100:.1f}%")
    ep = [abs(a["entry"] - p["entry"]) for a, p, _ in matched]
    print(f"  入场价差: median={statistics.median(ep):.4f}  mean={statistics.mean(ep):.4f}  max={max(ep):.4f}")
