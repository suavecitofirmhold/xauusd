# -*- coding: utf-8 -*-
"""
M5 多信号策略 参数扫描 / Pareto 前沿。

阶段:
  probe : 小网格定向探针(止损宽度 × R倍数 × 回踩容差), 快速看方向
  s1    : 单族 + 频率旋钮
  s2    : regime router
  s3    : 风控/出场

用法:
  python m5_sweep_pareto.py --grid probe
  python m5_sweep_pareto.py --grid probe --workers 8 --out m5_probe.csv
"""
import argparse
import itertools
import os
import sys
import time
from multiprocessing import Pool

import pandas as pd

from xauusd_m5_multisignal import run_m5_backtest, M5_PROFILES

FROM = "2025-04-15"
TO = "2026-09-11"


# ------------------------------ 网格定义 ------------------------------
def grid_probe():
    """定向探针: 止损宽度 / R倍数 / 回踩容差 / 冷却。"""
    g = []
    for stop_min, k_A in itertools.product((0.9, 1.5, 2.0), (1.0, 1.5, 2.0)):
        g.append(dict(stop_min_atr=stop_min, k_A=k_A))
    g.append(dict(stop_min_atr=1.5, k_A=1.2, pb_tol_atr=0.6))
    g.append(dict(stop_min_atr=1.5, k_A=1.2, pb_tol_atr=0.6, cooldown_bars=12))
    g.append(dict(stop_min_atr=1.5, k_A=1.2, enable_B=False, enable_C=False))
    g.append(dict(stop_min_atr=1.5, k_A=1.2, k_C=1.0, rg_span_max_atr=6.0))
    return g


def grid_s1():
    """单族 + 频率旋钮。"""
    g = []
    for pb in (0.3, 0.5, 0.8):
        for dep in (0.15, 0.25):
            g.append(dict(pb_tol_atr=pb, pb_min_depth_atr=dep))
    for dc in (90, 120, 180, 240):
        for buf in (0.05, 0.1, 0.2):
            g.append(dict(dc_minutes=dc, bo_buf_atr=buf))
    for rg in (240, 360, 480):
        for span in (3.0, 5.0, 8.0):
            g.append(dict(rg_minutes=rg, rg_span_max_atr=span))
    for vm in (1.0, 1.3, 1.6):
        g.append(dict(vol_mult=vm))
    return g


def grid_s2():
    """regime router。"""
    g = []
    for rel in (0.0004, 0.0006, 0.0010):
        for adx in (18.0, 20.0, 25.0):
            g.append(dict(rel_th=rel, adx_th=adx))
    for cf in (1, 3, 6):
        g.append(dict(regime_confirm_bars=cf))
    for lo, hi in ((0.0, 99.0), (0.5, 1.8), (0.7, 2.2)):
        g.append(dict(atr_lo=lo, atr_hi=hi))
    return g


def grid_bfreq():
    """B(突破, 唯一有优势的族) 频率 + regime 松紧。"""
    g = []
    for dc in (60, 90, 120):
        for buf in (0.05, 0.10):
            g.append(dict(dc_minutes=dc, bo_buf_atr=buf))
    for vm in (1.0, 1.3):
        g.append(dict(dc_minutes=90, bo_buf_atr=0.05, vol_mult=vm))
    for k in (1.8, 2.2, 2.8):
        g.append(dict(dc_minutes=90, bo_buf_atr=0.05, vol_mult=1.0, k_B=k))
    for rel, adx in ((0.0004, 18.0), (0.0010, 25.0)):
        g.append(dict(dc_minutes=90, bo_buf_atr=0.05, vol_mult=1.0,
                      rel_th=rel, adx_th=adx))
    g.append(dict(dc_minutes=90, bo_buf_atr=0.05, vol_mult=1.0, enable_fade=False))
    # 只留 B+C(去掉无优势的 A)
    g.append(dict(enable_A=False, dc_minutes=90, bo_buf_atr=0.05, vol_mult=1.0))
    g.append(dict(enable_A=False, dc_minutes=60, bo_buf_atr=0.05, vol_mult=1.0,
                  enable_fade=True, max_trades_day=99))
    return g


def grid_cfreq():
    """解锁 C(震荡回归) + 允许 B 在 TRANSITION 开火。"""
    g = []
    for rrm in (1.0, 1.5, 2.5):
        g.append(dict(enable_A=False, range_rel_mult=rrm))
    for rrm in (1.0, 1.5):
        g.append(dict(enable_A=False, range_rel_mult=rrm,
                      allow_B_in_transition=True))
    g.append(dict(enable_A=False, range_rel_mult=1.5,
                  allow_B_in_transition=True, dc_minutes=60, bo_buf_atr=0.05))
    g.append(dict(enable_A=True, range_rel_mult=1.5, allow_B_in_transition=True))
    return g


GRIDS = {"probe": grid_probe, "s1": grid_s1, "s2": grid_s2,
         "bfreq": grid_bfreq, "cfreq": grid_cfreq}


# ------------------------------ 单次运行 ------------------------------
def run_one(cfg):
    t0 = time.time()
    try:
        extra = dict(M5_PROFILES["default"])
        extra.update(cfg)
        r = run_m5_backtest(extra_args=extra, fromdate=FROM, todate=TO, quiet=True)
        row = dict(cfg)
        row.update(
            ret=r["total_ret"] * 100, pf=r["pf"], wr=r["win_rate"] * 100,
            closed=r["closed"], tpd=r["trades_per_day"],
            pct_days=r["pct_days_traded"] * 100, dd=r["maxdd"],
            avg_R=r["avg_R"], exp=r["expectancy"],
            n_long=r["n_long"], n_short=r["n_short"],
            pf_long=r["pf_long"], pf_short=r["pf_short"],
            final=r["final"], secs=round(time.time() - t0, 1),
        )
        return row
    except Exception as e:
        row = dict(cfg)
        row["error"] = f"{type(e).__name__}: {e}"
        return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", default="probe", choices=list(GRIDS))
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()

    grid = GRIDS[a.grid]()
    print(f"网格 {a.grid}: {len(grid)} 组配置, workers={a.workers}")
    t0 = time.time()
    if a.workers > 1:
        with Pool(processes=a.workers) as pool:
            rows = pool.map(run_one, grid)
    else:
        rows = [run_one(c) for c in grid]
    print(f"耗时 {time.time()-t0:.0f}s")

    df = pd.DataFrame(rows)
    if "error" in df.columns:
        err = df[df["error"].notna()]
        if len(err):
            print(f"\n⚠️ {len(err)} 组报错:")
            for _, e in err.head(3).iterrows():
                print("   ", e.get("error"))
        df = df[df["error"].isna()]
    if not len(df):
        return

    # 决策分数: Calmar 式(收益/回撤) + 期望, 频率作为硬门槛参与筛选
    df["score"] = df["ret"] / df["dd"].clip(lower=0.5) + df["exp"] * 20
    df = df.sort_values("score", ascending=False)

    cols = ["ret", "pf", "wr", "closed", "tpd", "pct_days", "dd",
            "avg_R", "exp", "score"]
    show = [c for c in df.columns if c not in cols]
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 50)
    print("\n" + "=" * 120)
    print(f"TOP 15 (按 score=收益/回撤 + 期望×20)")
    print("=" * 120)
    print(df[cols + show].head(15).to_string(index=False,
                                             float_format=lambda x: f"{x:.2f}"))

    out = a.out or f"m5_{a.grid}_pareto.csv"
    df.to_csv(out, index=False)
    print(f"\n已写出: {os.path.abspath(out)}")

    # Pareto 前沿(频率↑ 收益↑ 回撤↓ 三维非支配)
    sub = df[["tpd", "ret", "dd", "pf", "score"]].copy()
    pareto = []
    for i, r in sub.iterrows():
        dominated = ((sub["tpd"] >= r["tpd"]) & (sub["ret"] >= r["ret"]) &
                     (sub["dd"] <= r["dd"]) &
                     ((sub["tpd"] > r["tpd"]) | (sub["ret"] > r["ret"]) |
                      (sub["dd"] < r["dd"]))).any()
        if not dominated:
            pareto.append(i)
    print("\n" + "=" * 120)
    print(f"Pareto 前沿(频率↑/收益↑/回撤↓ 非支配) {len(pareto)} 个:")
    print("=" * 120)
    print(df.loc[pareto, cols + show].to_string(index=False,
                                                float_format=lambda x: f"{x:.2f}"))


if __name__ == "__main__":
    main()
