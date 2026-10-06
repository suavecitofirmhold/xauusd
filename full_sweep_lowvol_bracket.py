#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""二维扫描: ma_dist_lowvol_mult(<0.85) × max_ma_dist_atr(K)，找最接近「未过滤基线」的组合。

未过滤基线(参考): 541笔 / +763.34 / +76.77% / DD9.69 / PF1.37
当前默认(lowvol=0.90,K=2.0): 333笔 / +57.85% / DD8.80 / PF1.42

网格: lowvols=[0.75,0.80,0.85] × Ks=[1.0,1.5,2.0,2.5,3.0]
      + 两行参照: 未过滤(max_ma_dist_atr=0) 、当前默认(0.90/2.0)
用法: python full_sweep_lowvol_bracket.py
"""
import sys
import csv
sys.path.insert(0, r"D:\workbuddy\tushare\XAUUSD\xauusd策略")
import box_channel_optimized as M
import pandas as pd

FULL_START = pd.Timestamp("2024-03-04")
FULL_END   = pd.Timestamp("2026-08-26")
OUT_CSV = r"D:\workbuddy\tushare\XAUUSD\xauusd策略\_sweep_lowvol_bracket.csv"

# 未过滤基线: 关闭远离趋势过滤
BASE_NO_FILTER = dict(max_ma_dist_atr=0.0, ma_dist_regime_cond=False, ma_dist_lowvol_mult=1.0)
# 当前默认
CUR_DEF = dict(max_ma_dist_atr=2.0, ma_dist_regime_cond=True, ma_dist_lowvol_mult=0.90)

LOWVOLS = [0.75, 0.80, 0.85]
KS      = [1.0, 1.5, 2.0, 2.5, 3.0]

HEADER = ["tag", "lowvol", "K", "trades", "win%", "pnl$", "ret%", "DD%", "PF"]
ROWS = []

def run(tag, lowvol, K, extra):
    res = M.run_backtest(freq="15m",
        extra_args={**M.PROFILES['scalp'], 'atr_shift':1, **extra},
        cash=1000.0, fromdate=FULL_START, todate=FULL_END, quiet=True)
    n = res['closed']; wr = res['win_rate']*100
    pnl = res['realized_ret']*1000.0
    row = [tag, lowvol, K, n, round(wr,1), round(pnl,2),
           round(res['total_ret']*100,2), round(res['maxdd'],2), round(res['pf'],2)]
    print(f"{tag:<10}{str(lowvol):<7}{str(K):<6}{n:>7}{wr:>8.1f}{pnl:>+11.1f}"
          f"{res['total_ret']*100:>9.2f}{res['maxdd']:>8.2f}{res['pf']:>7.2f}", flush=True)
    ROWS.append(row)

print(f"{HEADER[0]:<10}{HEADER[1]:<7}{HEADER[2]:<6}{HEADER[3]:>7}{HEADER[4]:>8}"
      f"{HEADER[5]:>11}{HEADER[6]:>9}{HEADER[7]:>8}{HEADER[8]:>7}")
print('-'*72)

# 参照 1: 未过滤基线
run("NO_FILTER", "-", 0.0, BASE_NO_FILTER)
# 参照 2: 当前默认
run("DEFAULT", 0.90, 2.0, CUR_DEF)

# 扫描
for lv in LOWVOLS:
    for k in KS:
        run("SCAN", lv, k, dict(max_ma_dist_atr=k, ma_dist_regime_cond=True,
                                ma_dist_lowvol_mult=lv))

print("\n基线参考: NO_FILTER=541笔/+763/+76.77%/DD9.69/PF1.37 ; DEFAULT=333/+57.85%/DD8.80/PF1.42")
with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f); w.writerow(HEADER); w.writerows(ROWS)
print(f"输出: {OUT_CSV}")
