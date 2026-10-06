# -*- coding: utf-8 -*-
"""诊断 S2(首波拉升, 亏损) vs S5(主升浪, 盈利): 多/空盈亏拆分 + 趋势特征。
目的: 定位 scalp 均值回归在 S2 为何失效, 为 regime 前置过滤提供依据。
口径: atr_shift=1(与 EA 对齐)。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from box_channel_optimized import run_backtest, PROFILES, load_data


def side_split(trades):
    long_s, short_s = [], []
    closed = {"long": [], "short": []}
    for t in trades:
        if t["kind"] == "open":
            (long_s if t["side"] == "long" else short_s).append(t)
        else:
            stk = long_s if t["side"] == "long" else short_s
            if stk:
                o = stk.pop()
                sign = 1 if t["side"] == "long" else -1
                closed[t["side"]].append(sign * (t["price"] - o["price"]))
    out = {}
    for side in ("long", "short"):
        pnls = closed[side]
        if not pnls:
            out[side] = dict(n=0, gp=0.0, gl=0.0, pf=float("nan"), wr=0.0)
            continue
        gp = sum(p for p in pnls if p > 0)
        gl = -sum(p for p in pnls if p < 0)
        pf = gp / gl if gl > 0 else float("inf")
        wr = 100.0 * sum(1 for p in pnls if p > 0) / len(pnls)
        out[side] = dict(n=len(pnls), gp=round(gp, 1), gl=round(gl, 1),
                         pf=round(pf, 2), wr=round(wr, 1))
    return out


def trend_stats(df):
    c = df["close"]
    def slope(n):
        return (c - c.shift(n)) / c.shift(n)
    s96 = slope(96)
    s240 = slope(240)
    up_frac = (s96 > 0.002).mean()           # 96-bar 内涨超 0.2% 的占比(偏强趋势)
    roll_max = c.shift(1).rolling(96).max()
    near_high = (c >= roll_max * (1 - 0.001)).mean()  # 贴近 96-bar 高点的占比(突破/趋势区)
    return dict(
        s96_mean=round(s96.mean() * 100, 2),
        s96_up_frac=round(up_frac * 100, 1),
        s240_mean=round(s240.mean() * 100, 2),
        near_high_frac=round(near_high * 100, 1),
        ret=round((c.iloc[-1] / c.iloc[0] - 1) * 100, 1))


# 与 _walkforward_atr.py 相同的 5 段
SEGMENTS = [
    ("S1 起步/震荡 23-09~24-02", "2023-09-14", "2024-02-01"),
    ("S2 首波拉升 24-02~24-10", "2024-02-01", "2024-10-01"),
    ("S3 回调/震荡 24-10~25-03", "2024-10-01", "2025-03-01"),
    ("S4 再起涨   25-03~25-09", "2025-03-01", "2025-09-01"),
    ("S5 主升浪   25-09~26-08", "2025-09-01", "2026-08-29"),
]
args = dict(PROFILES["scalp"])
args["atr_shift"] = 1

print(f"{'window':<24}{'ret%':>7}{'PF':>6}{'n':>5} | {'L_n':>5}{'L_PF':>6}{'L_WR%':>7} | {'S_n':>5}{'S_PF':>6}{'S_WR%':>7} | s96μ% up% nearHi% ret%")
print("-" * 116)
for label, frm, to in SEGMENTS:
    m = run_backtest(extra_args=dict(args), fromdate=frm, todate=to, quiet=True)
    ss = side_split(m["trades"])
    df, _, _ = load_data("15m", frm, to)
    ts = trend_stats(df)
    print(f"{label:<24}{m['total_ret']*100:>+7.1f}{m['pf']:>6.2f}{m['closed']:>5} | "
          f"{ss['long']['n']:>5}{ss['long']['pf']:>6.2f}{ss['long']['wr']:>7.1f} | "
          f"{ss['short']['n']:>5}{ss['short']['pf']:>6.2f}{ss['short']['wr']:>7.1f} | "
          f"{ts['s96_mean']:>+5.2f} {ts['s96_up_frac']:>4.1f} {ts['near_high_frac']:>6.1f} {ts['ret']:>+5.1f}")

# 全样本对照
print("-" * 116)
m = run_backtest(extra_args=dict(args), fromdate="2023-09-14", todate="2026-08-29", quiet=True)
ss = side_split(m["trades"])
print(f"{'FULL 全样本':<24}{m['total_ret']*100:>+7.1f}{m['pf']:>6.2f}{m['closed']:>5} | "
      f"{ss['long']['n']:>5}{ss['long']['pf']:>6.2f}{ss['long']['wr']:>7.1f} | "
      f"{ss['short']['n']:>5}{ss['short']['pf']:>6.2f}{ss['short']['wr']:>7.1f}")
