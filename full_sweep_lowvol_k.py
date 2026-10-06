#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""2D 扫描: lowvol_mult × K, 找最接近基线的组合(基线条: 541笔 +763 PF1.37 DD9.69)。
用法: python full_sweep_lowvol_k.py
"""
import sys
sys.path.insert(0, r"D:\workbuddy\tushare\XAUUSD\xauusd策略")
import box_channel_optimized as M
import pandas as pd

FULL_START = pd.Timestamp("2024-03-04")
FULL_END   = pd.Timestamp("2026-08-26")

def build(res):
    stack=[]; out=[]
    for t in res['trades']:
        ts=int(pd.Timestamp(t['dt']).value//10**6); pr=round(float(t['price']),2)
        if t['kind']=='open': stack.append({'t':ts,'p':pr,'side':t['side'],'stop':t.get('stop')})
        else:
            o=stack.pop() if stack else None
            if o:
                ds=1 if o['side']=='long' else -1
                out.append({'pnl':round((pr-o['p'])*ds,2)})
    return out

def main():
    lowvols=[0.80,0.85]
    Ks=[2.5,3.0,4.0,5.0]
    print(f"{'lowvol':<8}{'K':>6}{'笔数':>8}{'胜率%':>9}{'总盈亏$':>12}{'收益%':>10}{'DD%':>9}{'PF':>8}")
    print('-'*70)
    for lv in lowvols:
        for k in Ks:
            res=M.run_backtest(freq="15m",
                extra_args={**M.PROFILES['scalp'],'atr_shift':1,'max_ma_dist_atr':k,
                            'ma_dist_regime_cond':True,'ma_dist_lowvol_mult':lv},
                cash=1000.0,fromdate=FULL_START,todate=FULL_END,quiet=True)
            tr=build(res); n=len(tr); wins=sum(1 for t in tr if t['pnl']>0)
            print(f"{lv:<8}{k:>6}{n:>8}{wins/n*100:>8.1f}{sum(t['pnl'] for t in tr):>+12.2f}"
                  f"{res['total_ret']*100:>10.2f}{res['maxdd']:>9.2f}{res['pf']:>8.2f}")
    print("\n基线参考: 541笔 +763.34 +76.77% DD9.69 PF1.37")

if __name__=="__main__":
    main()
