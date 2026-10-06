# -*- coding: utf-8 -*-
"""walk-forward 多折版：把全样本切成 6 个约半年的非重叠折，
每折内跑 use_reversal_filter 开/关 A/B（scalp 画像），看方向在各 regime 是否一致。
复用 box_channel_optimized.run_backtest + PROFILES['scalp']。
"""
import os, sys, json
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import box_channel_optimized as bc

# 6 折（半年/折，覆盖 2023-09-13 ~ 2026-08-29）
FOLDS = [
    ("F1 23H2", "2023-09-13", "2024-03-31"),
    ("F2 24H1", "2024-04-01", "2024-09-30"),
    ("F3 24H2", "2024-10-01", "2025-03-31"),
    ("F4 25H1", "2025-04-01", "2025-09-30"),
    ("F5 25H2", "2025-10-01", "2026-03-31"),
    ("F6 26H1", "2026-04-01", "2026-08-29"),
]

def run_one(use_rev, f, t):
    extra = dict(bc.PROFILES["scalp"])
    extra["use_reversal_filter"] = use_rev
    m = bc.run_backtest(freq="15m", extra_args=extra,
                        cash=1000.0, leverage=1000.0, lot=0.01,
                        spread=0.33, commission=0.0,
                        fromdate=pd.Timestamp(f), todate=pd.Timestamp(t),
                        quiet=True)
    return dict(
        use_reversal_filter=use_rev,
        total_ret=round(m["total_ret"] * 100, 2),
        maxdd=round(m["maxdd"], 2),
        sharpe=round(m["sharpe"], 3) if m["sharpe"] == m["sharpe"] else None,
        closed=m["closed"],
        win_rate=round(m["win_rate"] * 100, 1),
        pf=round(m["pf"], 3) if m["pf"] not in (float("inf"),) else None,
    )

if __name__ == "__main__":
    all_res = []
    print("\n" + "=" * 110)
    print(f"{'折':<10}{'rev':<6}{'收益%':>9}{'DD%':>8}{'Sharpe':>9}{'笔数':>7}{'胜率%':>8}{'PF':>8}")
    print("=" * 110)
    on_ret_wins = off_ret_wins = on_pf_wins = off_pf_wins = 0
    for fname, f, t in FOLDS:
        res = [run_one(True, f, t), run_one(False, f, t)]
        all_res.append(dict(fold=fname, from_=f, to_=t, results=res))
        for r in res:
            pf = 'inf' if r["pf"] is None else f'{r["pf"]:.3f}'
            sh = 'nan' if r["sharpe"] is None else f'{r["sharpe"]:.3f}'
            print(f'{fname:<10}{("ON" if r["use_reversal_filter"] else "OFF"):<6}'
                  f'{r["total_ret"]:>9.2f}{r["maxdd"]:>8.2f}{sh:>9}{r["closed"]:>7}'
                  f'{r["win_rate"]:>8.1f}{pf:>8}')
        on, off = res[0], res[1]
        ret_win = "ON" if on["total_ret"] > off["total_ret"] else "OFF"
        pf_win = "ON" if (on["pf"] or 0) > (off["pf"] or 0) else "OFF"
        if ret_win == "ON": on_ret_wins += 1
        else: off_ret_wins += 1
        if pf_win == "ON": on_pf_wins += 1
        else: off_pf_wins += 1
        print(f"  -> 收益优胜: {ret_win} (Δ{(on['total_ret']-off['total_ret']):+.2f}pp) | PF优胜: {pf_win} "
              f"(Δ{((on['pf'] or 0)-(off['pf'] or 0)):+.3f})")
        print("-" * 110)
    print(f"收益优胜统计: ON 胜 {on_ret_wins} 折 / OFF 胜 {off_ret_wins} 折")
    print(f"PF 优胜统计  : ON 胜 {on_pf_wins} 折 / OFF 胜 {off_pf_wins} 折")
    print("=" * 110)
    with open(os.path.join(HERE, "_rev_ab_walkforward_result.json"), "w", encoding="utf-8") as fp:
        json.dump(all_res, fp, ensure_ascii=False, indent=2)
    print("saved -> _rev_ab_walkforward_result.json")
