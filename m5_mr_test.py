# -*- coding: utf-8 -*-
"""
v2 均值回归策略 验证: 4 个 profile × IS/OOS。
重点看 IS→OOS 的 PF 是否一致(v1 的病就是参数维度 ρ≈0, 但结构维度可迁移)。
用法: python m5_mr_test.py
"""
from multiprocessing import Pool

import pandas as pd

from xauusd_m5_meanrev import run_mr_backtest, MR_PROFILES

IS_FROM, IS_TO = "2025-04-15", "2025-12-31"
OOS_FROM, OOS_TO = "2026-01-01", "2026-09-11"
FULL_FROM, FULL_TO = "2025-04-15", "2026-09-11"
WARMUP = 25


def _pf(v):
    return 99.0 if v == float("inf") or v != v else round(v, 3)


def _run(args):
    name, period = args
    cfg = dict(MR_PROFILES[name])
    if period == "IS":
        f, t = IS_FROM, IS_TO
    elif period == "OOS":
        f = (pd.Timestamp(OOS_FROM) - pd.Timedelta(days=WARMUP)).strftime("%Y-%m-%d")
        t = OOS_TO
        cfg["no_trade_before"] = OOS_FROM
    else:
        f, t = FULL_FROM, FULL_TO
    try:
        r = run_mr_backtest(extra_args=cfg, fromdate=f, todate=t, quiet=True)
        return {
            "profile": name, "段": period,
            "ret": round(r["total_ret"] * 100, 2), "pf": _pf(r["pf"]),
            "wr": round(r["win_rate"] * 100, 2), "closed": r["closed"],
            "tpd": round(r["trades_per_day"], 2),
            "pct_days": round(r["pct_days_traded"] * 100, 1),
            "dd": round(r["maxdd"], 2), "avg_R": round(r["avg_R"], 3),
            "exp": round(r["expectancy"], 3),
            "n_long": r["n_long"], "n_short": r["n_short"],
            "pf_long": _pf(r["pf_long"]), "pf_short": _pf(r["pf_short"]),
            "swap": round(r["swap_total"], 2),
        }
    except Exception as e:
        return {"profile": name, "段": period, "error": f"{type(e).__name__}: {e}"}


def main():
    jobs = [(n, p) for n in MR_PROFILES for p in ("IS", "OOS", "FULL")]
    print(f"{len(jobs)} 次回测运行中...")
    with Pool(processes=8) as pool:
        rows = pool.map(_run, jobs)
    df = pd.DataFrame(rows)
    err = df[df.get("error").notna()] if "error" in df.columns else df.iloc[0:0]
    if len(err):
        print(f"⚠️ {len(err)} 次报错:")
        for e in err["error"].head(4):
            print("   ", e)
        df = df[df.get("error").isna()]
    if not len(df):
        return
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    print("\n" + "=" * 140)
    print("v2 均值回归: IS / OOS / FULL")
    print("=" * 140)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    df.to_csv("m5_mr_test.csv", index=False)

    piv = df.pivot(index="profile", columns="段", values="pf")
    print("\n" + "=" * 140)
    print("PF 对照(IS vs OOS vs FULL) —— 看是否像 v1 那样 IS/OOS 脱节")
    print("=" * 140)
    print(piv.to_string(float_format=lambda x: f"{x:.2f}"))


if __name__ == "__main__":
    main()
