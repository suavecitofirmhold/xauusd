# -*- coding: utf-8 -*-
"""
分族归因: 单独/组合开启 A(趋势回踩) / B(突破) / C(震荡回归), 看哪一族有优势。
用法: python m5_family_probe.py
"""
import time
from multiprocessing import Pool

import pandas as pd

from xauusd_m5_multisignal import run_m5_backtest, M5_PROFILES

FROM, TO = "2025-04-15", "2026-09-11"

# 用探针得到的最优出场区间
BASE = dict(stop_min_atr=1.5, stop_max_atr=2.5, k_A=1.2,
            pb_tol_atr=0.6, k_C=1.2, rg_span_max_atr=6.0)

COMBOS = [
    ("A only", dict(enable_A=True, enable_B=False, enable_C=False)),
    ("B only", dict(enable_A=False, enable_B=True, enable_C=False)),
    ("C only", dict(enable_A=False, enable_B=False, enable_C=True)),
    ("A+B", dict(enable_A=True, enable_B=True, enable_C=False)),
    ("A+C", dict(enable_A=True, enable_B=False, enable_C=True)),
    ("B+C", dict(enable_A=False, enable_B=True, enable_C=True)),
    ("A+B+C", dict(enable_A=True, enable_B=True, enable_C=True)),
]


def run_one(item):
    name, cfg = item
    t0 = time.time()
    extra = dict(M5_PROFILES["default"])
    extra.update(BASE)
    extra.update(cfg)
    try:
        r = run_m5_backtest(extra_args=extra, fromdate=FROM, todate=TO, quiet=True)
        return dict(family=name, ret=r["total_ret"] * 100, pf=r["pf"],
                    wr=r["win_rate"] * 100, closed=r["closed"],
                    tpd=r["trades_per_day"], pct_days=r["pct_days_traded"] * 100,
                    dd=r["maxdd"], avg_R=r["avg_R"], exp=r["expectancy"],
                    n_long=r["n_long"], n_short=r["n_short"],
                    pf_long=r["pf_long"], pf_short=r["pf_short"],
                    swap=r["swap_total"], final=r["final"],
                    secs=round(time.time() - t0, 1))
    except Exception as e:
        return dict(family=name, error=f"{type(e).__name__}: {e}")


def main():
    print(f"基线参数: {BASE}")
    print(f"{len(COMBOS)} 组, 多进程运行中...\n")
    with Pool(processes=7) as pool:
        rows = pool.map(run_one, COMBOS)
    df = pd.DataFrame(rows)
    pd.set_option("display.width", 220)
    pd.set_option("display.max_columns", 50)
    cols = ["family", "closed", "tpd", "pct_days", "ret", "pf", "wr",
            "avg_R", "exp", "dd", "n_long", "n_short", "pf_long", "pf_short",
            "swap", "final"]
    cols = [c for c in cols if c in df.columns]
    print("=" * 150)
    print("分族归因")
    print("=" * 150)
    print(df[cols].to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    df.to_csv("m5_family_probe.csv", index=False)
    print("\n已写出 m5_family_probe.csv")


if __name__ == "__main__":
    main()
