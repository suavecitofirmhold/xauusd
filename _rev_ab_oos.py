# -*- coding: utf-8 -*-
"""样本外复验：在 TRAIN / TEST 分窗上重跑 use_reversal_filter 开/关 A/B。
TRAIN = 2023-09-13~2025-06-01 (调参区, IS)
TEST  = 2025-06-01~2026-08-29 (样本外区, OOS)
复用 box_channel_optimized.run_backtest + PROFILES['scalp']。
"""
import os, sys, json
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import box_channel_optimized as bc

WINDOWS = [
    ("TRAIN(IS)",  "2023-09-13", "2025-06-01"),
    ("TEST(OOS)",  "2025-06-01", "2026-08-29"),
]

def run_one(use_rev, fromdate, todate):
    extra = dict(bc.PROFILES["scalp"])
    extra["use_reversal_filter"] = use_rev
    m = bc.run_backtest(freq="15m", extra_args=extra,
                        cash=1000.0, leverage=1000.0, lot=0.01,
                        spread=0.33, commission=0.0,
                        fromdate=pd.Timestamp(fromdate), todate=pd.Timestamp(todate),
                        quiet=True)
    return dict(
        use_reversal_filter=use_rev,
        final=round(m["final"], 2),
        total_ret=round(m["total_ret"] * 100, 2),
        maxdd=round(m["maxdd"], 2),
        sharpe=round(m["sharpe"], 3) if m["sharpe"] == m["sharpe"] else None,
        closed=m["closed"],
        win_rate=round(m["win_rate"] * 100, 1),
        pf=round(m["pf"], 3) if m["pf"] not in (float("inf"),) else None,
        swap_total=round(m["swap_total"], 2),
    )

if __name__ == "__main__":
    all_res = []
    print("\n" + "=" * 96)
    print(f"{'窗口':<12}{'rev_filter':<12}{'收益%':>9}{'DD%':>8}{'Sharpe':>9}{'笔数':>7}{'胜率%':>8}{'PF':>8}")
    print("=" * 96)
    for wname, f, t in WINDOWS:
        res = [run_one(True, f, t), run_one(False, f, t)]
        all_res.append(dict(window=wname, from_=f, to_=t, results=res))
        for r in res:
            pf = 'inf' if r["pf"] is None else f'{r["pf"]:.3f}'
            sh = 'nan' if r["sharpe"] is None else f'{r["sharpe"]:.3f}'
            print(f'{wname:<12}{str(r["use_reversal_filter"]):<12}{r["total_ret"]:>9.2f}'
                  f'{r["maxdd"]:>8.2f}{sh:>9}{r["closed"]:>7}{r["win_rate"]:>8.1f}{pf:>8}')
        t, fa = res[0], res[1]
        better = "关更优" if fa["total_ret"] > t["total_ret"] else "开更优"
        print(f"  -> 收益差(开-关)= {t['total_ret']-fa['total_ret']:+.2f}pp | PF差= {((t['pf'] or 0)-(fa['pf'] or 0)):+.3f} | OOS方向: {better}")
        print("-" * 96)
    with open(os.path.join(HERE, "_rev_ab_oos_result.json"), "w", encoding="utf-8") as fp:
        json.dump(all_res, fp, ensure_ascii=False, indent=2)
    print("saved -> _rev_ab_oos_result.json")
