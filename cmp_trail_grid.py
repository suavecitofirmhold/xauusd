# -*- coding: utf-8 -*-
"""
 scalp 画像四参数网格扫参: r_mult / trail_activate_atr / trail_atr_mult / trail_be_buffer
 =====================================================================================
 纪律: IS(2024-03~2025-06)选档, OOS(2025-07~2026-08)仅验证(防泄漏)。
 输出: trail_grid.out(可读表, 增量落盘) + trail_grid.json(机读)。
 当前已采纳基线 = r_mult=2.25 / act=1.0 / follow=1.0 / be=0.0(2026-09-14 Stage3.1)。
"""
import sys, json, time
sys.path.insert(0, r"D:\workbuddy\tushare\XAUUSD\xauusd策略")
from box_channel_optimized import run_backtest, PROFILES

IS = ("2024-03-01", "2025-06-30")
OOS = ("2025-07-01", "2026-08-31")

R_MULT = [1.75, 2.25, 2.5]
ACT = [0.5, 1.0, 1.5, 2.0]
FOLLOW = [0.5, 1.0, 1.5]
BE = [0.0, 0.5, 1.0]

OUT = r"D:\workbuddy\tushare\XAUUSD\xauusd策略\trail_grid.out"
JSONOUT = r"D:\workbuddy\tushare\XAUUSD\xauusd策略\trail_grid.json"


def run(extra, window):
    try:
        m = run_backtest(freq="15m", extra_args=extra, fromdate=window[0], todate=window[1], quiet=True)
        calmar = (m["ann"] / m["maxdd"] * 100.0) if m["maxdd"] > 0 else float("inf")
        return dict(ret=m["total_ret"] * 100, ann=m["ann"] * 100, dd=m["maxdd"],
                    sharpe=m["sharpe"], pf=m["pf"], trades=m["closed"],
                    win=m["win_rate"] * 100, calmar=calmar)
    except Exception as e:
        return dict(ret=float("nan"), ann=float("nan"), dd=float("nan"), sharpe=float("nan"),
                    pf=float("nan"), trades=-1, win=float("nan"), calmar=float("nan"), err=str(e)[:70])


def fmt(r):
    if r.get("err"):
        return f"ERROR {r['err']}"
    return (f"ret={r['ret']:+7.2f}% ann={r['ann']:+7.2f}% DD={r['dd']:5.2f}% "
            f"PF={r['pf']:4.2f} Sharpe={r['sharpe']:5.2f} win={r['win']:5.1f}% calmar={r['calmar']:6.2f} n={r['trades']}")


def main():
    f = open(OUT, "w", encoding="utf-8")
    results = []
    total = len(R_MULT) * len(ACT) * len(FOLLOW) * len(BE)
    print(f"网格: r_mult{len(R_MULT)} × act{len(ACT)} × follow{len(FOLLOW)} × be{len(BE)} = {total} 组合 × (IS+OOS)", file=f)
    print(f"基线(已采纳) = r_mult=2.25 / act=1.0 / follow=1.0 / be=0.0", file=f); f.flush()

    # 参考: 当前已采纳基线
    base = dict(PROFILES["scalp"])
    b_is = run(base, IS); b_oos = run(base, OOS)
    print(f"[REF] baseline IS : {fmt(b_is)}", file=f)
    print(f"[REF] baseline OOS: {fmt(b_oos)}", file=f); f.flush()

    i = 0
    t0 = time.time()
    for rm in R_MULT:
        for act in ACT:
            for fol in FOLLOW:
                for be in BE:
                    i += 1
                    e = dict(PROFILES["scalp"]); e["r_mult"] = rm
                    e["trail_activate_atr"] = act; e["trail_atr_mult"] = fol; e["trail_be_buffer"] = be
                    ris = run(e, IS); roos = run(e, OOS)
                    rec = dict(r_mult=rm, act=act, follow=fol, be=be, is_=ris, oos=roos)
                    results.append(rec)
                    tag = f"r={rm} act={act} fol={fol} be={be}"
                    print(f"[{i:3d}/{total}] {tag:34s} IS : {fmt(ris)}", file=f)
                    print(f"{'':41s} OOS: {fmt(roos)}", file=f); f.flush()
    print(f"\n总耗时 {time.time()-t0:.0f}s", file=f); f.flush()

    # 排序表
    def srt(key, win):
        return sorted(results, key=lambda r: (r[win].get(key) if not r[win].get("err") else -1e9), reverse=True)

    print("\n===== 按 IS calmar 排序(选档参考, IS 全段为负故看 least-bad) =====", file=f)
    for r in srt("calmar", "is_")[:12]:
        print(f"  r={r['r_mult']} act={r['act']} fol={r['follow']} be={r['be']} | IS {fmt(r['is_'])} | OOS {fmt(r['oos'])}", file=f)
    print("\n===== 按 OOS calmar 排序(仅展示, 选档须看 IS 防泄漏) =====", file=f)
    for r in srt("calmar", "oos")[:12]:
        print(f"  r={r['r_mult']} act={r['act']} fol={r['follow']} be={r['be']} | IS {fmt(r['is_'])} | OOS {fmt(r['oos'])}", file=f)
    f.flush(); f.close()

    with open(JSONOUT, "w", encoding="utf-8") as j:
        json.dump(dict(baseline=dict(is_=b_is, oos=b_oos), grid=results,
                       windows=dict(IS=IS, OOS=OOS)), j, ensure_ascii=False, indent=1)
    print("done")


if __name__ == "__main__":
    main()
