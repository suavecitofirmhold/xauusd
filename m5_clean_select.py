# -*- coding: utf-8 -*-
"""
干净重选验证: 只在 IS 期扫参选优 → 拿去 OOS 验(解决 best_v1 全样本选参污染 OOS 的问题)。

同时回答一个更根本的问题: **IS 排名到底能不能预测 OOS 排名?**
若 IS→OOS 相关性接近 0, 说明在这个样本上"调参"本身没有预测力, +61.6% 只是拟合噪声。

用法: python m5_clean_select.py [--workers 8]
"""
import argparse
from multiprocessing import Pool

import numpy as np
import pandas as pd

from xauusd_m5_multisignal import run_m5_backtest, M5_PROFILES
from m5_sweep_pareto import grid_bfreq, grid_cfreq


def spearman(x, y):
    """Spearman 秩相关(不依赖 scipy): 先取秩再算 Pearson。"""
    x = pd.Series(x).astype(float)
    y = pd.Series(y).astype(float)
    return float(x.rank().corr(y.rank()))

IS_FROM, IS_TO = "2025-04-15", "2025-12-31"
OOS_FROM, OOS_TO = "2026-01-01", "2026-09-11"
WARMUP_DAYS = 25
MIN_TRADES_IS = 100   # IS 期最少成交数(低于此不参与选优, 避免选中噪声)


def _pf(v):
    return 99.0 if v == float("inf") or v != v else round(v, 3)


def _row(cfg, r):
    return {
        "ret": round(r["total_ret"] * 100, 2),
        "pf": _pf(r["pf"]),
        "wr": round(r["win_rate"] * 100, 2),
        "closed": r["closed"],
        "tpd": round(r["trades_per_day"], 2),
        "pct_days": round(r["pct_days_traded"] * 100, 1),
        "dd": round(r["maxdd"], 2),
        "exp": round(r["expectancy"], 3),
        "n_long": r["n_long"], "n_short": r["n_short"],
        "pf_long": _pf(r["pf_long"]), "pf_short": _pf(r["pf_short"]),
    }


def _run(args):
    cfg, period = args
    if period == "IS":
        f, t, ntb = IS_FROM, IS_TO, None
    else:
        f = (pd.Timestamp(OOS_FROM) - pd.Timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
        t, ntb = OOS_TO, OOS_FROM
    c = dict(cfg)
    if ntb:
        c["no_trade_before"] = ntb
    try:
        r = run_m5_backtest(extra_args=c, fromdate=f, todate=t, quiet=True)
        row = {"period": period}
        row.update(_row(cfg, r))
        return row
    except Exception as e:
        return {"period": period, "error": f"{type(e).__name__}: {e}"}


def _score(d):
    """选优分数: Calmar 式(收益/回撤) + 期望×20。"""
    return d["ret"] / max(d["dd"], 0.5) + d["exp"] * 20


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()

    # 合并两个有效网格并去重(保持顺序)
    seen, grid = set(), []
    for g in (grid_bfreq(), grid_cfreq()):
        for c in g:
            key = tuple(sorted(c.items()))
            if key not in seen:
                seen.add(key)
                grid.append(c)
    print(f"网格: {len(grid)} 组配置 × 2 段(IS/OOS) = {len(grid)*2} 次回测")

    jobs = [(c, p) for c in grid for p in ("IS", "OOS")]
    with Pool(processes=a.workers) as pool:
        rows = pool.map(_run, jobs)

    df = pd.DataFrame(rows)
    err = df[df.get("error").notna()] if "error" in df.columns else df.iloc[0:0]
    if len(err):
        print(f"⚠️ {len(err)} 次报错(已剔除): {err['error'].iloc[0]}")
        df = df[df.get("error").isna()]

    is_df = df[df["period"] == "IS"].reset_index(drop=True)
    oos_df = df[df["period"] == "OOS"].reset_index(drop=True)
    is_df["cfg_id"] = range(len(is_df))
    oos_df["cfg_id"] = range(len(oos_df))
    is_df["score"] = is_df.apply(_score, axis=1)
    oos_df["score"] = oos_df.apply(_score, axis=1)

    merged = is_df.merge(oos_df, on="cfg_id", suffixes=("_is", "_oos"))
    merged.to_csv("m5_clean_select.csv", index=False)

    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 60)

    # ---------- 1. 相关性: IS 能否预测 OOS ----------
    print("\n" + "=" * 120)
    print("1. IS → OOS 预测力(核心问题)")
    print("=" * 120)
    ok = merged[(merged["closed_is"] >= MIN_TRADES_IS) & (merged["closed_oos"] >= 30)]
    print(f"  有效配置: {len(ok)} / {len(merged)}  (IS≥{MIN_TRADES_IS}笔 且 OOS≥30笔)")
    if len(ok) >= 5:
        for m_is, m_oos, lbl in (("ret_is", "ret_oos", "收益"),
                                 ("pf_is", "pf_oos", "PF"),
                                 ("score_is", "score_oos", "选优分数"),
                                 ("exp_is", "exp_oos", "期望$")):
            rho = spearman(ok[m_is], ok[m_oos])
            # n>=10 时 rho 的显著性近似临界值(|rho| > 0.648 → p<0.05, 双尾)
            crit = 0.648 if len(ok) >= 10 else 0.90
            sig = abs(rho) > crit
            print(f"  {lbl:<8} Spearman ρ = {rho:+.3f}   "
                  f"(n={len(ok)}, |ρ|>{crit:.2f} 才算显著)  "
                  f"{'✅ 有预测力' if (rho > 0 and sig) else '⚠️ 预测力弱/不显著'}")

    # ---------- 2. IS 选优 → OOS 表现 ----------
    print("\n" + "=" * 120)
    print("2. 按 IS 选优的前 8 名 → 它们在 OOS 的表现(诚实结果)")
    print("=" * 120)
    sel = merged[merged["closed_is"] >= MIN_TRADES_IS].sort_values("score_is", ascending=False)
    cols = ["ret_is", "pf_is", "closed_is", "tpd_is", "dd_is",
            "ret_oos", "pf_oos", "closed_oos", "tpd_oos", "dd_oos"]
    print(sel[cols].head(8).to_string(index=False, float_format=lambda x: f"{x:.2f}"))

    if len(sel):
        top = sel.iloc[0]
        print(f"\n  ★ IS 选出的第 1 名:")
        print(f"     IS : {top['ret_is']:+.2f}%  PF {top['pf_is']:.2f}  "
              f"{top['closed_is']}笔  DD {top['dd_is']:.1f}%")
        print(f"     OOS: {top['ret_oos']:+.2f}%  PF {top['pf_oos']:.2f}  "
              f"{top['closed_oos']}笔  DD {top['dd_oos']:.1f}%")
        decay = (top["exp_oos"] / top["exp_is"]) if top["exp_is"] else float("nan")
        print(f"     期望衰减 OOS/IS = {decay:.2f}  "
              f"({'✅ ≥0.6 可接受' if decay >= 0.6 else '❌ <0.6 衰减过大'})")

    # ---------- 3. IS 前 5 名在 OOS 的平均表现 ----------
    print("\n" + "=" * 120)
    print("3. IS 前 5 名 / 后 5 名 在 OOS 的平均表现(检验选优是否真的挑到好的)")
    print("=" * 120)
    if len(sel) >= 10:
        top5 = sel.head(5)
        bot5 = sel.tail(5)
        for name, grp in (("IS 前5名", top5), ("IS 后5名", bot5)):
            print(f"  {name}: OOS 收益均值 {grp['ret_oos'].mean():+.2f}%  "
                  f"PF均值 {grp['pf_oos'].mean():.2f}  "
                  f"期望均值 ${grp['exp_oos'].mean():.2f}")

    # ---------- 4. 对照: 全样本选出的 best_v1 ----------
    print("\n" + "=" * 120)
    print("4. 对照: 全样本选出的 best_v1(有污染) 在同分段的表现")
    print("=" * 120)
    for tag, f, t, ntb in (("IS ", IS_FROM, IS_TO, None),
                           ("OOS", (pd.Timestamp(OOS_FROM) - pd.Timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d"),
                            OOS_TO, OOS_FROM)):
        c = dict(M5_PROFILES["best_v1"])
        if ntb:
            c["no_trade_before"] = ntb
        r = run_m5_backtest(extra_args=c, fromdate=f, todate=t, quiet=True)
        d = _row(c, r)
        print(f"  best_v1/{tag}: {d['ret']:+.2f}%  PF {d['pf']:.2f}  "
              f"{d['closed']}笔  {d['tpd']}笔/天  {d['pct_days']}%交易日  DD {d['dd']:.1f}%")

    print("\n已写出 m5_clean_select.csv")


if __name__ == "__main__":
    main()
