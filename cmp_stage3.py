# -*- coding: utf-8 -*-
"""
Stage 3: 优化买卖逻辑 (基于 Stage 2 结论: 保留原 cluster_levels 基线)
=================================================================
3.1 仅调参(不改进逻辑): 扫 r_mult(止盈距离) 最大化"回调后止盈"收益, IS 选 / OOS 验证。
3.2 限价单更优成交: 在当前买条件下挂 Limit 单贴近支撑买(而非市价立即买), 模拟 EA 实时;
    含两版 —— anchor=True(价位锚定 SL/TP, 低买才改善 R) / anchor=False(沿用 atr_stop, 作对照)。
3.3 对比 (1) vs (2) 哪种更优 (以 OOS 收益/PF/回撤/calmar 为准)。
"""
import sys
sys.path.insert(0, r"D:\workbuddy\tushare\XAUUSD\xauusd策略")
from box_channel_optimized import run_backtest, PROFILES

IS = ("2024-03-01", "2025-06-30")
OOS = ("2025-07-01", "2026-08-31")


def run(extra, window, tag=""):
    try:
        m = run_backtest(freq="15m", extra_args=extra,
                         fromdate=window[0], todate=window[1], quiet=True)
        calmar = (m["ann"] / m["maxdd"] * 100.0) if m["maxdd"] > 0 else float("inf")
        return dict(tag=tag, ret=m["total_ret"] * 100, ann=m["ann"] * 100, dd=m["maxdd"],
                    sharpe=m["sharpe"], pf=m["pf"], trades=m["closed"], win=m["win_rate"] * 100,
                    calmar=calmar)
    except Exception as e:
        return dict(tag=tag, ret=float("nan"), ann=float("nan"), dd=float("nan"),
                    sharpe=float("nan"), pf=float("nan"), trades=-1, win=float("nan"),
                    calmar=float("nan"), err=str(e)[:80])


def fmt(r):
    pf = f"{r['pf']:.2f}" if r["pf"] != float("inf") else "inf"
    cal = f"{r['calmar']:.2f}" if r.get("calmar") not in (float("inf"), None) else "inf"
    if r.get("err"):
        return f"{r['tag']:22s} ERROR {r['err']}"
    return (f"{r['tag']:22s} ret={r['ret']:+7.2f}% ann={r['ann']:+7.2f}% DD={r['dd']:5.2f}% "
            f"PF={pf:>4s} Sharpe={r['sharpe']:5.2f} trades={r['trades']:4d} win={r['win']:5.1f}% calmar={cal:>6s}")


def base_extra():
    return dict(PROFILES["scalp"])


def main():
    print("=" * 100)
    print("Stage 3: 优化买卖逻辑 (保留原 cluster_levels 基线, scalp 画像)")
    print("=" * 100)
    rows = []  # (tag, is_row, oos_row)

    # ---- 基线 ----
    print("\n##### 基线 (scalp, 市价, atr_stop) #####")
    b_is = run(base_extra(), IS, "baseline")
    b_oos = run(base_extra(), OOS, "baseline")
    print(f"  IS : {fmt(b_is)}")
    print(f"  OOS: {fmt(b_oos)}")

    # ---- 3.1 r_mult 扫参 (止盈距离) ----
    print("\n##### 3.1 调参: r_mult 扫参 (市价, 其余同基线) #####")
    rmult_best = None
    for rm in (1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5):
        e = base_extra(); e["r_mult"] = rm
        ris = run(e, IS, f"r_mult={rm}")
        roos = run(e, OOS, f"r_mult={rm}")
        print(f"  IS : {fmt(ris)}")
        print(f"  OOS: {fmt(roos)}")
        # 选档: OOS 收益最高且 PF>=1.1 且 DD<=基线*1.3
        if (roos["pf"] >= 1.1 and roos["dd"] <= b_oos["dd"] * 1.3
                and (rmult_best is None or roos["ret"] > rmult_best[1]["ret"])):
            rmult_best = (rm, roos)
    rm_best = rmult_best[0] if rmult_best else 1.75
    print(f"  >> 3.1 推荐 r_mult={rm_best} (OOS ret={rmult_best[1]['ret']:+.2f}% PF={rmult_best[1]['pf']:.2f})")

    # ---- 3.2a 限价单 + 价位锚定 (有益版) ----
    print("\n##### 3.2a 限价单 + 价位锚定 SL/TP (低买改善 R) #####")
    anchor_best = None
    for buf in (0.25, 0.5, 0.75, 1.0):
        e = base_extra(); e["limit_entry"] = True; e["limit_anchor_level"] = True
        e["limit_buffer_atr"] = buf
        ris = run(e, IS, f"limit+anchor buf={buf}")
        roos = run(e, OOS, f"limit+anchor buf={buf}")
        print(f"  IS : {fmt(ris)}")
        print(f"  OOS: {fmt(roos)}")
        if (roos["pf"] >= 1.1 and (anchor_best is None or roos["ret"] > anchor_best[1]["ret"])):
            anchor_best = (buf, roos)
    buf_best = anchor_best[0] if anchor_best else 0.5
    if anchor_best:
        print(f"  >> 3.2a 推荐 limit_buffer_atr={buf_best} (OOS ret={anchor_best[1]['ret']:+.2f}% PF={anchor_best[1]['pf']:.2f})")
    else:
        print(f"  >> 3.2a 无 PF>=1.1 配置")

    # ---- 3.2b 限价单 + 沿用 atr_stop (对照: 限价零收益) ----
    print("\n##### 3.2b 限价单 + 沿用 atr_stop (对照) #####")
    e = base_extra(); e["limit_entry"] = True; e["limit_anchor_level"] = False; e["limit_buffer_atr"] = 0.5
    ris = run(e, IS, "limit+atr")
    roos = run(e, OOS, "limit+atr")
    print(f"  IS : {fmt(ris)}")
    print(f"  OOS: {fmt(roos)}")

    # ---- 3.3 对比总结 ----
    print("\n" + "=" * 100)
    print("Stage 3.3 对比 (OOS 决策):")
    print("=" * 100)
    print(f"  基线        OOS: ret={b_oos['ret']:+.2f}% PF={b_oos['pf']:.2f} DD={b_oos['dd']:.2f}% calmar={b_oos['calmar']:.2f} trades={b_oos['trades']}")
    r1 = run(dict(base_extra(), r_mult=rm_best), OOS, f"3.1 r_mult={rm_best}")
    print(f"  3.1(调参)   OOS: ret={r1['ret']:+.2f}% PF={r1['pf']:.2f} DD={r1['dd']:.2f}% calmar={r1['calmar']:.2f} trades={r1['trades']}")
    if anchor_best:
        r2 = run(dict(base_extra(), limit_entry=True, limit_anchor_level=True, limit_buffer_atr=buf_best), OOS,
                 f"3.2a buf={buf_best}")
        print(f"  3.2a(限价锚定) OOS: ret={r2['ret']:+.2f}% PF={r2['pf']:.2f} DD={r2['dd']:.2f}% calmar={r2['calmar']:.2f} trades={r2['trades']}")
    print(f"  3.2b(限价atr) OOS: ret={roos['ret']:+.2f}% PF={roos['pf']:.2f} DD={roos['dd']:.2f}% calmar={roos['calmar']:.2f} trades={roos['trades']}")
    print("-" * 100)
    print("结论: 以 OOS 收益/PF/回撤/calmar 综合判断; 任一方案须全面优于基线才采纳, 否则保持原样。")


if __name__ == "__main__":
    main()
