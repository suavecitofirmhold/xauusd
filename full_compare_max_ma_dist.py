#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""全样本验证 max_ma_dist_atr: K=0 / 2.0 / 3.0, 含 5 段时间窗稳健性对比。
用法: python full_compare_max_ma_dist.py
"""
import sys, datetime, json
sys.path.insert(0, r"D:\workbuddy\tushare\XAUUSD\xauusd策略")
import box_channel_optimized as M
import pandas as pd

FULL_START = pd.Timestamp("2024-03-04")
FULL_END   = pd.Timestamp("2026-08-26")
# 变体: (标签, max_ma_dist_atr, ma_dist_regime_cond)
VARIANTS = [
    ("K=0 基线",      0.0, False),
    ("K=2.0 固定",    2.0, False),
    ("K=3.0 固定",    3.0, False),
    ("K=2.0 条件化",  2.0, True),
]
REASON = {'tp':'止盈','sl':'止损','trail':'追踪止损','eod':'隔夜强平','margin':'保证金','manual':'手动平'}

def build_trades(res):
    stack = []; out = []
    for t in res['trades']:
        ts = int(pd.Timestamp(t['dt']).value // 10**6)
        pr = round(float(t['price']), 2)
        if t['kind'] == 'open':
            stack.append({'t': ts, 'p': pr, 'side': t['side'], 'stop': t.get('stop')})
        else:
            o = stack.pop() if stack else None
            if o:
                ds = 1 if o['side'] == 'long' else -1
                pts = round((pr - o['p']) * ds, 2)
                out.append({'side': o['side'], 'entry_t': o['t'], 'exit_t': ts, 'pnl': pts,
                            'reason': t.get('reason')})
    return out

def seg_bounds(start, end, n=5):
    """把 [start,end] 均分 n 段, 返回 n+1 个边界时间戳(ms)。"""
    total = (end - start).total_seconds()
    b = [start + pd.Timedelta(seconds=total * i / n) for i in range(n + 1)]
    return [int(pd.Timestamp(x).value // 10**6) for x in b]

def main():
    bounds = seg_bounds(FULL_START, FULL_END, 5)
    # 运行各变体
    runs = {}
    for label, k, cond in VARIANTS:
        res = M.run_backtest(freq="15m",
                             extra_args={**M.PROFILES['scalp'], 'atr_shift': 1,
                                         'max_ma_dist_atr': k, 'ma_dist_regime_cond': cond},
                             cash=1000.0, fromdate=FULL_START, todate=FULL_END, quiet=True)
        tr = build_trades(res)
        runs[label] = (res, tr)

    # 总体表
    print("="*100)
    print(f"全样本 {FULL_START.date()} ~ {FULL_END.date()}  (max_ma_dist_atr 对比)")
    print("="*100)
    hdr = f"{'指标':<16}" + "".join(f"{lab:>20}" for lab in [v[0] for v in VARIANTS])
    print(hdr); print("-"*100)
    stats = {}
    for label, k, cond in VARIANTS:
        res, tr = runs[label]
        n=len(tr); wins=sum(1 for t in tr if t['pnl']>0)
        stats[label]=dict(n=n, win=(wins/n*100 if n else 0), pnl=sum(t['pnl'] for t in tr),
                          ret=res['total_ret']*100, dd=res['maxdd'], pf=res['pf'])
    def p(fmt, key):
        return "".join(f"{fmt(stats[lab][key]):>20}" for lab in [v[0] for v in VARIANTS])
    print(f"{'笔数':<16}{p(lambda v:str(v),'n')}")
    print(f"{'胜率%':<16}{p(lambda v:f'{v:.1f}','win')}")
    print(f"{'总盈亏$':<16}{p(lambda v:f'{v:+.2f}','pnl')}")
    print(f"{'收益率%':<16}{p(lambda v:f'{v:.2f}','ret')}")
    print(f"{'最大回撤%':<16}{p(lambda v:f'{v:.2f}','dd')}")
    print(f"{'PF':<16}{p(lambda v:f'{v:.2f}','pf')}")

    # 5 段稳健性: 每段净盈亏$
    print("\n" + "="*100)
    print("5 段时间窗净盈亏$ (稳健性):")
    print("="*100)
    seg_labels = []
    for s in range(5):
        a = datetime.datetime.fromtimestamp(bounds[s]/1000, datetime.timezone.utc).strftime('%y-%m')
        b = datetime.datetime.fromtimestamp(bounds[s+1]/1000, datetime.timezone.utc).strftime('%y-%m')
        seg_labels.append(f"{a}~{b}")
    print(f"{'变体':<14}" + "".join(f"{lab:>16}" for lab in seg_labels) + f"{'合计':>16}")
    for label, k, cond in VARIANTS:
        res, tr = runs[label]
        line_seg = f"{label:<14}"
        tot = 0.0
        for s in range(5):
            sub = [t for t in tr if bounds[s] <= t['entry_t'] < bounds[s+1]]
            pnl = sum(t['pnl'] for t in sub)
            tot += pnl
            line_seg += f"{pnl:+.1f}({len(sub)})".rjust(16)
        line_seg += f"{tot:+.1f}".rjust(16)
        print(line_seg)

    print("\n图例: 每段 = 净盈亏$(笔数); 正数=该段盈利, 负数=亏损。")

if __name__ == "__main__":
    main()
