# -*- coding: utf-8 -*-
"""
时段聚焦测试: 既然"调参"被证明无预测力(ρ≈0), 改测**结构性**假设——
优势是否只集中在高波动时段(伦敦/纽约重叠, 服务器 15-18)?

对 best_v1 施加不同交易时段窗口, 在 IS / OOS 各跑一遍。
用法: python m5_session_test.py
"""
from multiprocessing import Pool

import pandas as pd

from xauusd_m5_multisignal import run_m5_backtest, M5_PROFILES

IS_FROM, IS_TO = "2025-04-15", "2025-12-31"
OOS_FROM, OOS_TO = "2026-01-01", "2026-09-11"
WARMUP = 25

# (标签, 起始服务器小时, 结束服务器小时)
WINDOWS = [
    ("全时段 1-22", 1, 22),
    ("白天 8-20", 8, 20),
    ("重叠 13-19", 13, 19),
    ("核心 15-18", 15, 18),
]


def _pf(v):
    return 99.0 if v == float("inf") or v != v else round(v, 3)


def _run(args):
    label, h0, h1, period = args
    cfg = dict(M5_PROFILES["best_v1"])
    cfg["trade_start_hour"] = h0
    cfg["trade_end_hour"] = h1
    if period == "IS":
        f, t = IS_FROM, IS_TO
    else:
        f = (pd.Timestamp(OOS_FROM) - pd.Timedelta(days=WARMUP)).strftime("%Y-%m-%d")
        t = OOS_TO
        cfg["no_trade_before"] = OOS_FROM
    try:
        r = run_m5_backtest(extra_args=cfg, fromdate=f, todate=t, quiet=True)
        return {
            "时段": label, "段": period,
            "ret": round(r["total_ret"] * 100, 2), "pf": _pf(r["pf"]),
            "wr": round(r["win_rate"] * 100, 2), "closed": r["closed"],
            "tpd": round(r["trades_per_day"], 2),
            "pct_days": round(r["pct_days_traded"] * 100, 1),
            "dd": round(r["maxdd"], 2), "exp": round(r["expectancy"], 3),
            "pf_long": _pf(r["pf_long"]), "pf_short": _pf(r["pf_short"]),
        }
    except Exception as e:
        return {"时段": label, "段": period, "error": f"{type(e).__name__}: {e}"}


def main():
    jobs = [(lb, h0, h1, p) for (lb, h0, h1) in WINDOWS for p in ("IS", "OOS")]
    print(f"{len(jobs)} 次回测运行中...")
    with Pool(processes=8) as pool:
        rows = pool.map(_run, jobs)
    df = pd.DataFrame(rows)
    err = df[df.get("error").notna()] if "error" in df.columns else df.iloc[0:0]
    if len(err):
        print(f"⚠️ {len(err)} 次报错: {err['error'].iloc[0]}")
        df = df[df.get("error").isna()]
    pd.set_option("display.width", 240)
    pd.set_option("display.max_columns", 40)
    print("\n" + "=" * 130)
    print("时段聚焦测试(best_v1 基础上只改交易时段)")
    print("=" * 130)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    df.to_csv("m5_session_test.csv", index=False)

    piv = df.pivot(index="时段", columns="段", values=["pf", "ret"])
    print("\n" + "=" * 130)
    print("IS vs OOS 对照(PF / 收益)")
    print("=" * 130)
    print(piv.to_string(float_format=lambda x: f"{x:.2f}"))


if __name__ == "__main__":
    main()
