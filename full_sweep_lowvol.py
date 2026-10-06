#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""扫描 ma_dist_lowvol_mult: 在低波动条件化模式下找最佳阈值。
用法: python full_sweep_lowvol.py
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
    mults=[0.90,0.95,1.00,1.05,1.10,1.15,1.20]
    print(f"{'lowvol_mult':<14}{'笔数':>8}{'胜率%':>9}{'总盈亏$':>12}{'收益%':>10}{'DD%':>9}{'PF':>8}")
    print('-'*70)
    for m in mults:
        res=M.run_backtest(freq="15m",
            extra_args={**M.PROFILES['scalp'],'atr_shift':1,'max_ma_dist_atr':2.0,
                        'ma_dist_regime_cond':True,'ma_dist_lowvol_mult':m},
            cash=1000.0,fromdate=FULL_START,todate=FULL_END,quiet=True)
        tr=build(res); n=len(tr); wins=sum(1 for t in tr if t['pnl']>0)
        print(f"{m:<14}{n:>8}{wins/n*100:>8.1f}{sum(t['pnl'] for t in tr):>+12.2f}"
              f"{res['total_ret']*100:>10.2f}{res['maxdd']:>9.2f}{res['pf']:>8.2f}")

if __name__=="__main__":
    main()
