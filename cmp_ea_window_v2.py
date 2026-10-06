# -*- coding: utf-8 -*-
"""
EA(BoxChannelScalp_trades.csv) vs Python(box_channel_optimized, scalp OLD 单段) 同期窗口对比。
窗口 2026-07-01 ~ 2026-08-01。
EA 配置(据 .ini): InpAtrStopMult=1.5 / InpTrailing=true, 无 InpTrail* => OLD 单段 <=> Python OLD。
EA 时间戳=服务器时间(UTC+2/+3 随美DST), 须转 UTC 才能与 Python 对齐。
注意: 附件 CSV 是累积文件(2024-07~2026-07), 此处只取窗口内那一段对比。
"""
import csv, statistics
import pandas as pd
from box_channel_optimized import run_backtest, PROFILES, tmgm_server_offset_hours

FROM = pd.Timestamp("2026-07-01")
TO = pd.Timestamp("2026-08-01")
EA = r"C:\Users\18628\AppData\Roaming\MetaQuotes\Tester\7643C0B96C7AD5841307C9E1EB0B9252\Agent-127.0.0.1-3000\MQL5\Files\BoxChannelScalp_trades.csv"


def server_to_utc(srv):
    approx = srv - pd.Timedelta(hours=2)
    off = tmgm_server_offset_hours(approx.to_pydatetime())
    return srv - pd.Timedelta(hours=off)


def pf_wr(trades_pl):
    """trades_pl: list of per-trade net profit($)"""
    if not trades_pl:
        return 0, 0, 0, 0, 0
    gp = sum(x for x in trades_pl if x > 0)
    gl = -sum(x for x in trades_pl if x < 0)
    n = len(trades_pl)
    w = sum(1 for x in trades_pl if x > 0)
    pf = (gp / gl) if gl > 0 else float("inf")
    # max DD on cumulative equity starting 1000
    eq = 1000.0
    peak = 1000.0
    mdd = 0.0
    for pl in trades_pl:
        eq += pl
        peak = max(peak, eq)
        mdd = max(mdd, (peak - eq) / peak)
    return pf, w / n, gp, gl, mdd


# ===================== Python OLD (窗口) =====================
print("=" * 80)
print(f"Python OLD 单段 | 窗口 {FROM.date()} ~ {TO.date()} | 15m, $1000, atr_shift=1")
print("=" * 80)
extra = {**PROFILES["scalp"], "atr_shift": 1,
         "trail_activate_atr": 0.0, "trail_atr_mult": 1.5, "trail_be_buffer": 0.0}
pyres = run_backtest(freq="15m", extra_args=extra, cash=1000.0,
                     fromdate=FROM, todate=TO, quiet=True)
py_open = []
for t in pyres["trades"]:
    if t.get("kind") != "open":
        continue
    d = pd.Timestamp(t["dt"])
    if d.tzinfo:
        d = d.tz_localize(None)
    py_open.append(dict(dt=d, side=t.get("side"), entry=t.get("price"), stop=t.get("stop")))
py_open.sort(key=lambda x: x["dt"])
print(f"  Python: net={pyres['total_ret']*100:+.1f}%  PF={pyres['pf']:.2f}  "
      f"WR={pyres['win_rate']*100:.1f}%  closed={pyres['closed']}  "
      f"entries={pyres['entries']}  DD={pyres['maxdd']:.1f}%  final=${pyres['final']:.1f}")
_py_pl = [t.get("pnl", 0) for t in pyres["trades"] if t.get("kind") == "close" and t.get("pnl") is not None]
if _py_pl:
    _gp = sum(x for x in _py_pl if x > 0)
    _gl = -sum(x for x in _py_pl if x < 0)
    _nw = sum(1 for x in _py_pl if x > 0)
    _nl = len(_py_pl) - _nw
    print(f"    gross_profit=${_gp:.2f}  gross_loss=${_gl:.2f}  "
          f"avg_win=${(_gp/_nw) if _nw else 0:.2f}  avg_loss=${(_gl/_nl) if _nl else 0:.2f}")

# ===================== EA 解析 =====================
print()
print("=" * 80)
print("EA trades.csv 解析")
print("=" * 80)
rows = list(csv.DictReader(open(EA, encoding="utf-8")))
opens, closes = [], []
for r in rows:
    srv = pd.Timestamp(r["time"].replace(".", "-").replace(" ", "T"))
    utc = server_to_utc(srv)
    rec = dict(srv=srv, dt=utc, side=r["side"],
               price=float(r["price"]), stop=float(r["stop"]), target=float(r["target"]),
               profit=float(r["profit"]), swap=float(r["swap"]), reason=r["reason"])
    (opens if r["action"] == "open" else closes).append(rec)
# pair open->close (sequential, same side)
ea_trades = []
used = [False] * len(opens)
for c in closes:
    for i, o in enumerate(opens):
        if not used[i] and o["side"] == c["side"] and o["srv"] <= c["srv"]:
            used[i] = True
            ea_trades.append(dict(open=o, close=c,
                                  side=o["side"], entry=o["price"],
                                  exit=c["price"], profit=c["profit"],
                                  o_srv=o["srv"], o_utc=o["dt"], c_utc=c["dt"],
                                  reason=c["reason"]))
            break
ea_trades.sort(key=lambda x: x["o_srv"])
srv_all = [t["o_srv"] for t in ea_trades]
print(f"  CSV 总行={len(rows)}  open={len(opens)}  close={len(closes)}  paired={len(ea_trades)}")
print(f"  EA 全样本开仓区间: {srv_all[0].date()} ~ {srv_all[-1].date()}  (累积文件, 非仅窗口)")

# ---- 窗口过滤(按服务器时间, 忠实于用户在 MT5 配置的运行区间) ----
win = [t for t in ea_trades
       if FROM <= t["o_srv"] < TO]
win.sort(key=lambda x: x["o_srv"])
pl = [t["profit"] for t in win]
pf, wr, gp, gl, mdd = pf_wr(pl)
net = sum(pl)
print()
print(f"  窗口内 EA 开仓(服务器时间 {FROM.date()}~{TO.date()}): {len(win)} 笔")
print(f"    net=${net:+.2f}  net%={net/1000*100:+.1f}%  PF={pf:.2f}  "
      f"WR={wr*100:.1f}%  DD={mdd*100:.1f}%")
nw = sum(1 for x in pl if x > 0)
nl = len(pl) - nw
aw = (gp / nw) if nw else 0
al = (gl / nl) if nl else 0
print(f"    gross_profit=${gp:.2f}  gross_loss=${gl:.2f}  "
      f"avg_win=${aw:.2f}  avg_loss=${al:.2f}")
# reason 分布
from collections import Counter
rc = Counter(t["reason"] for t in win)
print(f"    出场原因: {dict(rc)}")

# ===================== 逐笔 UTC 配对(EA窗口 vs Python窗口) =====================
print()
print("=" * 80)
print("逐笔 UTC 配对 (EA窗口 -> Python窗口, 容差15min, 方向一致)")
print("=" * 80)
matched = []
for a in win:
    best, bd = None, 1e18
    for p in py_open:
        d = abs((p["dt"] - a["o_utc"]).total_seconds()) / 60.0
        if d < bd:
            bd, best = d, p
    if best is not None and bd <= 15 and best["side"] == a["side"]:
        matched.append((a, best, bd))
print(f"  配对成功 {len(matched)} / {len(win)} 笔")
if matched:
    dts = [x[2] for x in matched]
    print(f"  配对时差: median={statistics.median(dts):.0f}min  max={max(dts):.0f}min")
    ep = [abs(a["entry"] - p["entry"]) for a, p, _ in matched]
    print(f"  入场价差: median=${statistics.median(ep):.3f}  mean=${statistics.mean(ep):.3f}  max=${max(ep):.3f}")
    # 配对上的 EA 与 Python 各自盈亏(仅 EA 有 profit; Python 配对后取同笔盈亏)
    # Python 同笔 profit:
    py_pl = []
    for a, p, _ in matched:
        # 找 Python 该开仓对应的平仓 profit
        for t in pyres["trades"]:
            if t.get("kind") == "close" and abs((pd.Timestamp(t["dt"]).tz_localize(None) if pd.Timestamp(t["dt"]).tzinfo else pd.Timestamp(t["dt"])) - p["dt"]).total_seconds() < 16 * 60:
                py_pl.append(t.get("pnl", 0) or 0)
                break
    if py_pl:
        print(f"  配对笔 Python 盈亏: mean=${statistics.mean(py_pl):.3f}  EA同笔: mean=${statistics.mean([a['profit'] for a,p,_ in matched]):.3f}")

# ===================== 汇总表 =====================
print()
print("=" * 80)
print("汇总对比 (窗口 2026-07-01~2026-08-01)")
print("=" * 80)
print(f"  {'指标':<10} {'Python OLD':>14} {'EA(窗口)':>14}")
print(f"  {'收益':<10} {pyres['total_ret']*100:>+13.1f}% {net/1000*100:>+13.1f}%")
print(f"  {'PF':<10} {pyres['pf']:>14.2f} {pf:>14.2f}")
print(f"  {'胜率':<10} {pyres['win_rate']*100:>13.1f}% {wr*100:>13.1f}%")
print(f"  {'笔数':<10} {pyres['closed']:>14d} {len(win):>14d}")
print(f"  {'最大回撤':<10} {pyres['maxdd']:>13.1f}% {mdd*100:>13.1f}%")
