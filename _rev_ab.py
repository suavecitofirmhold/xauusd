# -*- coding: utf-8 -*-
"""A/B 对照：box_channel_optimized.py --profile scalp 在
use_reversal_filter=True vs False 下的收益 / PF 对比。
轻量封装，不修改原脚本；直接复用其 run_backtest 与 PROFILES。
"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import box_channel_optimized as bc

def run_one(use_rev):
    extra = dict(bc.PROFILES["scalp"])          # 复制 scalp 画像
    extra["use_reversal_filter"] = use_rev      # 仅切换该开关
    m = bc.run_backtest(freq="15m", extra_args=extra,
                        cash=1000.0, leverage=1000.0, lot=0.01,
                        spread=0.33, commission=0.0, quiet=True)
    return dict(
        use_reversal_filter=use_rev,
        final=round(m["final"], 2),
        total_ret=round(m["total_ret"] * 100, 2),
        ann=round(m["ann"] * 100, 2),
        maxdd=round(m["maxdd"], 2),
        sharpe=round(m["sharpe"], 3) if m["sharpe"] == m["sharpe"] else None,
        closed=m["closed"],
        win_rate=round(m["win_rate"] * 100, 1),
        pf=round(m["pf"], 3) if m["pf"] not in (float("inf"),) else None,
        entries=m["entries"],
        swap_total=round(m["swap_total"], 2),
    )

if __name__ == "__main__":
    res = [run_one(True), run_one(False)]
    print("\n" + "=" * 92)
    hdr = f"{'use_reversal_filter':<22}{'收益%':>9}{'年化%':>9}{'DD%':>8}{'Sharpe':>9}{'笔数':>7}{'胜率%':>8}{'PF':>8}{'隔夜费':>10}"
    print(hdr)
    print("=" * 92)
    for r in res:
        pf = 'inf' if r["pf"] is None else f'{r["pf"]:.3f}'
        sh = 'nan' if r["sharpe"] is None else f'{r["sharpe"]:.3f}'
        print(f'{str(r["use_reversal_filter"]):<22}{r["total_ret"]:>9.2f}{r["ann"]:>9.2f}'
              f'{r["maxdd"]:>8.2f}{sh:>9}{r["closed"]:>7}{r["win_rate"]:>8.1f}{pf:>8}{r["swap_total"]:>10.2f}')
    print("=" * 92)
    # 差额
    t, f = res[0], res[1]
    print(f"收益差 (开-关) : {t['total_ret'] - f['total_ret']:+.2f} %")
    print(f"PF 差  (开-关) : {((t['pf'] or 0) - (f['pf'] or 0)):+.3f}")
    print(f"DD 差  (开-关) : {t['maxdd'] - f['maxdd']:+.2f} %")
    with open(os.path.join(HERE, "_rev_ab_result.json"), "w", encoding="utf-8") as fp:
        json.dump(res, fp, ensure_ascii=False, indent=2)
    print("saved -> _rev_ab_result.json")
