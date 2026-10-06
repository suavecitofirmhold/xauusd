#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""对比 scalp 基线 vs 加 max_ma_dist_atr 过滤的交易记录与绩效。
用法: python compare_max_ma_dist.py [--from 2026-07-01] [--to 2026-08-02] [--k 2.0]
"""
import sys, argparse
sys.path.insert(0, r"D:\workbuddy\tushare\XAUUSD\xauusd策略")
import box_channel_optimized as M
import pandas as pd

REASON = {'tp':'止盈','sl':'止损','trail':'追踪止损','eod':'隔夜强平','margin':'保证金','manual':'手动平'}

def build_trades(res):
    """从 res['trades'] FIFO 配对成交记录, 返回带盈亏/R/原因的列表(FIFO)。"""
    stack = []
    trades = []
    for t in res['trades']:
        ts = int(pd.Timestamp(t['dt']).value // 10**6)
        pr = round(float(t['price']), 2)
        if t['kind'] == 'open':
            stack.append({'t': ts, 'p': pr, 'side': t['side'], 'stop': t.get('stop')})
        else:
            o = stack.pop() if stack else None
            pnl = pts = risk = r = None
            if o:
                ds = 1 if o['side'] == 'long' else -1
                pts = round((pr - o['p']) * ds, 2)
                pnl = pts
                if o.get('stop') is not None:
                    risk = abs(o['p'] - o['stop'])
                    r = round(pts / risk, 2) if risk else None
                trades.append({'side': o['side'], 'entry_t': o['t'], 'entry_p': round(o['p'], 2),
                               'exit_t': ts, 'exit_p': pr, 'pnl': pnl, 'pts': pts, 'r': r,
                               'reason': t.get('reason'), 'hold': int((ts - o['t']) // 60000)})
    return trades

def stats(trades, meta):
    n = len(trades)
    wins = sum(1 for t in trades if t['pnl'] > 0)
    pnl = sum(t['pnl'] for t in trades)
    return dict(n=n, win=(wins / n * 100 if n else 0), pnl=pnl,
                ret=meta['total_ret']*100, dd=meta['maxdd'], pf=meta['pf'])

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2026-07-01")
    ap.add_argument("--to", dest="end", default="2026-08-02")
    ap.add_argument("--k", type=float, default=2.0)
    a = ap.parse_args()
    frm = pd.Timestamp(a.start); to = pd.Timestamp(a.end)

    base = M.run_backtest(freq="15m", extra_args={**M.PROFILES['scalp'], 'atr_shift': 1, 'max_ma_dist_atr': 0.0},
                          cash=1000.0, fromdate=frm, todate=to, quiet=True)
    filt = M.run_backtest(freq="15m", extra_args={**M.PROFILES['scalp'], 'atr_shift': 1, 'max_ma_dist_atr': a.k},
                          cash=1000.0, fromdate=frm, todate=to, quiet=True)
    tb = build_trades(base)
    tf = build_trades(filt)

    sb = stats(tb, base); sf = stats(tf, filt)
    print("="*92)
    print(f"窗口 {a.start} ~ {a.end}   max_ma_dist_atr={a.k}")
    print("="*92)
    print(f"{'指标':<14}{'基线(关闭)':>22}{'过滤(K=%.1f)'%a.k:>22}{'差异':>22}")
    print("-"*92)
    print(f"{'笔数':<14}{sb['n']:>22}{sf['n']:>22}{sf['n']-sb['n']:>+22}")
    print(f"{'胜率%':<14}{sb['win']:>21.1f}{sf['win']:>21.1f}{sf['win']-sb['win']:>+21.1f}")
    print(f"{'总盈亏$':<14}{sb['pnl']:>22.2f}{sf['pnl']:>22.2f}{sf['pnl']-sb['pnl']:>+22.2f}")
    print(f"{'收益率%':<14}{sb['ret']:>21.2f}{sf['ret']:>21.2f}{sf['ret']-sb['ret']:>+21.2f}")
    print(f"{'最大回撤%':<14}{sb['dd']:>21.2f}{sf['dd']:>21.2f}{sf['dd']-sb['dd']:>+21.2f}")
    print(f"{'PF':<14}{sb['pf']:>22.2f}{sf['pf']:>22.2f}{sf['pf']-sb['pf']:>+22.2f}")

    # 被过滤掉的交易(基线有、过滤无): 按入场时间戳匹配
    base_keys = {(t['entry_t'], t['side']) for t in tb}
    removed = [t for t in tb if (t['entry_t'], t['side']) not in {(x['entry_t'], x['side']) for x in tf}]
    print("\n" + "="*92)
    print(f"被过滤掉的入场共 {len(removed)} 笔 (基线有 → 过滤无):")
    print("="*92)
    cum = 0.0
    for i, t in enumerate(removed, 1):
        cum += t['pnl']
        import datetime
        ets = datetime.datetime.fromtimestamp(t['entry_t']/1000, datetime.timezone.utc).strftime('%m-%d %H:%M')
        print(f"{i:2d} {t['side']:5s} {ets} {t['entry_p']:7.2f} -> {t['exit_p']:7.2f} PnL={t['pnl']:+7.2f} "
              f"R={t['r']:5.2f} {REASON.get(t['reason'],t['reason'])}  (累计省下 {cum:+.2f})")

if __name__ == "__main__":
    main()
