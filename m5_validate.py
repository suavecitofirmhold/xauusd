# -*- coding: utf-8 -*-
"""
M5 策略验证(对应计划 Step 7)。

  A: Holdout     —— IS 2025-04-15~2025-12-31 / OOS 2026-01-01~2026-09-11(参数冻结, OOS 只碰一次)
  B: 滚动 WF     —— 参数冻结, 逐月 OOS(前 25 天作指标预热, 预热期不开仓)
  C: 分段        —— 按季度 + 多/空分开
  D: 成本压力    —— spread 0.33/0.50/0.66/0.99, 求盈亏平衡点差 spread_be
  E: 序列 bootstrap —— 打乱成交顺序 1000 次, 看终值/回撤分布

用法:
  python m5_validate.py --parts A
  python m5_validate.py --parts ABCDE
"""
import argparse
from multiprocessing import Pool

import numpy as np
import pandas as pd

from xauusd_m5_multisignal import run_m5_backtest, M5_PROFILES

FULL_FROM, FULL_TO = "2025-04-15", "2026-09-11"
IS_FROM, IS_TO = "2025-04-15", "2025-12-31"
OOS_FROM, OOS_TO = "2026-01-01", "2026-09-11"
WARMUP_DAYS = 25

def _pf(v):
    return 99.0 if v == float("inf") or v != v else round(v, 3)


def brief(r):
    """把 run_m5_backtest 的结果键名映射成简表列。"""
    return {
        "ret": round(r["total_ret"] * 100, 2),
        "pf": _pf(r["pf"]),
        "wr": round(r["win_rate"] * 100, 2),
        "closed": r["closed"],
        "tpd": round(r["trades_per_day"], 2),
        "pct_days": round(r["pct_days_traded"] * 100, 1),
        "dd": round(r["maxdd"], 2),
        "avg_R": round(r["avg_R"], 3),
        "exp": round(r["expectancy"], 3),
        "n_long": r["n_long"],
        "n_short": r["n_short"],
        "pf_long": _pf(r["pf_long"]),
        "pf_short": _pf(r["pf_short"]),
    }


def _in_window_freq(r, win_from, win_to):
    """只按窗口内天数重算 笔/天 与 交易日占比(排除预热期)。"""
    closes = [t["dt"].date() for t in r["trades"] if t["kind"] == "close"]
    start = pd.Timestamp(win_from).date()
    end = pd.Timestamp(win_to).date()
    n_days = max((end - start).days + 1, 1)
    if not closes:
        return 0.0, 0.0
    inw = [d for d in closes if start <= d <= end]
    return round(len(inw) / n_days, 2), round(len(set(inw)) / n_days * 100, 1)


def show(title, rows, index_name="配置"):
    print("\n" + "=" * 130)
    print(title)
    print("=" * 130)
    df = pd.DataFrame(rows)
    if index_name in df.columns:
        df = df.set_index(index_name)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 60)
    print(df.to_string(float_format=lambda x: f"{x:.2f}"))
    return df


# ------------------------------------------------------------------ A: Holdout
def part_A(profiles):
    rows = []
    for name in profiles:
        cfg = dict(M5_PROFILES[name])
        for tag, f, t in (("IS ", IS_FROM, IS_TO), ("OOS", OOS_FROM, OOS_TO),
                          ("FULL", FULL_FROM, FULL_TO)):
            r = run_m5_backtest(extra_args=cfg, fromdate=f, todate=t, quiet=True)
            row = {"配置": f"{name}/{tag}"}
            row.update(brief(r))
            row["final"] = round(r["final"], 2)
            rows.append(row)
    show("A. Holdout: IS(2025-04~12) vs OOS(2026-01~09) —— 参数冻结, OOS 只碰一次",
         rows)
    return rows


# ------------------------------------------------------------------ B: 滚动 WF
def _wf_one(args):
    name, win_from, win_to = args
    cfg = dict(M5_PROFILES[name])
    warm_from = (pd.Timestamp(win_from) - pd.Timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    cfg["no_trade_before"] = win_from
    try:
        r = run_m5_backtest(extra_args=cfg, fromdate=warm_from, todate=win_to,
                            quiet=True)
        row = {"窗口": f"{win_from[:7]}"}
        row.update(brief(r))
        # 修正频率口径: 分母只算窗口内天数(预热期本就不开仓)
        tpd, pdays = _in_window_freq(r, win_from, win_to)
        row["tpd"], row["pct_days"] = tpd, pdays
        row["final"] = round(r["final"], 2)
        return row
    except Exception as e:
        return {"窗口": f"{win_from[:7]}", "error": f"{type(e).__name__}: {e}"}


def part_B(profiles):
    months = pd.date_range("2025-10-01", "2026-09-01", freq="MS")
    wins = []
    for m in months:
        wf = m.strftime("%Y-%m-%d")
        wt = (m + pd.offsets.MonthEnd(0)).strftime("%Y-%m-%d")
        if pd.Timestamp(wt) > pd.Timestamp(FULL_TO):
            wt = FULL_TO
        if pd.Timestamp(wf) >= pd.Timestamp(FULL_TO):
            continue
        wins.append((profiles[0], wf, wt))
    print(f"\n滚动 WF: {len(wins)} 个 OOS 窗口(参数冻结={profiles[0]}, 预热 {WARMUP_DAYS} 天)")
    with Pool(processes=8) as pool:
        rows = pool.map(_wf_one, wins)
    df = show("B. 滚动 walk-forward(逐月 OOS, 参数冻结)", rows, "窗口")
    ok = sum(1 for r in rows if isinstance(r.get("ret"), (int, float)) and r["ret"] > 0)
    print(f"\n  → 正收益窗口: {ok}/{len(rows)}")
    df.to_csv("m5_wf.csv", index=False)
    return rows


# ------------------------------------------------------------------ C: 分段
def _seg_one(args):
    name, f, t, label = args
    cfg = dict(M5_PROFILES[name])
    warm_from = (pd.Timestamp(f) - pd.Timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
    cfg["no_trade_before"] = f
    try:
        r = run_m5_backtest(extra_args=cfg, fromdate=warm_from, todate=t, quiet=True)
        row = {"段": label}
        row.update(brief(r))
        return row
    except Exception as e:
        return {"段": label, "error": f"{type(e).__name__}: {e}"}


def part_C(profiles):
    quarters = [("2025Q2", "2025-04-15", "2025-06-30"),
                ("2025Q3", "2025-07-01", "2025-09-30"),
                ("2025Q4", "2025-10-01", "2025-12-31"),
                ("2026Q1", "2026-01-01", "2026-03-31"),
                ("2026Q2", "2026-04-01", "2026-06-30"),
                ("2026Q3", "2026-07-01", "2026-09-11")]
    jobs = [(profiles[0], f, t, lb) for lb, f, t in quarters]
    with Pool(processes=6) as pool:
        rows = pool.map(_seg_one, jobs)
    show("C. 分段(按季度, 参数冻结)", rows, "段")
    return rows


# ------------------------------------------------------------------ D: 成本压力
def _cost_one(args):
    name, sp = args
    cfg = dict(M5_PROFILES[name])
    try:
        r = run_m5_backtest(extra_args=cfg, fromdate=FULL_FROM, todate=FULL_TO,
                            spread=sp, quiet=True)
        return {"spread": sp, "ret": round(r["total_ret"] * 100, 2),
                "pf": round(r["pf"], 3), "closed": r["closed"],
                "final": round(r["final"], 2), "dd": round(r["maxdd"], 2),
                "exp": round(r["expectancy"], 3)}
    except Exception as e:
        return {"spread": sp, "error": f"{type(e).__name__}: {e}"}


def part_D(profiles):
    spreads = (0.33, 0.50, 0.66, 0.99, 1.50)
    jobs = [(profiles[0], s) for s in spreads]
    with Pool(processes=5) as pool:
        rows = pool.map(_cost_one, jobs)
    df = show("D. 成本压力(往返点差, 目标: 0.66 下仍盈利 ⇒ spread_be ≥ 2×0.33)",
              rows, "spread")
    # 线性插值求盈亏平衡点差
    ok = [r for r in rows if isinstance(r.get("ret"), (int, float))]
    be = None
    for i in range(1, len(ok)):
        a, b = ok[i - 1], ok[i]
        if a["ret"] > 0 >= b["ret"]:
            be = a["spread"] + (b["spread"] - a["spread"]) * a["ret"] / (a["ret"] - b["ret"])
            break
    if be:
        print(f"\n  → 盈亏平衡点差 spread_be ≈ {be:.2f}")
    elif ok and ok[-1]["ret"] > 0:
        print(f"\n  → 最严档 {ok[-1]['spread']} 仍盈利, spread_be > {ok[-1]['spread']}")
    return rows


# ------------------------------------------------------------------ E: bootstrap
def part_E(profiles):
    cfg = dict(M5_PROFILES[profiles[0]])
    r = run_m5_backtest(extra_args=cfg, fromdate=FULL_FROM, todate=FULL_TO, quiet=True)
    tl = r["trades"]
    stack, pnls = [], []
    for t in tl:
        if t["kind"] == "open":
            stack.append(t)
        elif t["kind"] == "close" and stack:
            o = stack.pop()
            sign = 1 if o["side"] == "long" else -1
            pnls.append((t["price"] - o["price"]) * sign)
    pnls = np.array(pnls)
    if not len(pnls):
        print("E. 无成交, 跳过")
        return
    real_final = r["final"]
    real_dd = r["maxdd"]
    rng = np.random.default_rng(42)
    finals, dds = [], []
    for _ in range(1000):
        s = rng.permutation(pnls)
        eq = 1000.0 + np.cumsum(s)
        peak = np.maximum.accumulate(np.concatenate([[1000.0], eq]))
        dd = ((peak[1:] - eq) / peak[1:]).max() * 100
        finals.append(eq[-1])
        dds.append(dd)
    finals = np.array(finals)
    dds = np.array(dds)
    print("\n" + "=" * 130)
    print("E. 成交序列 bootstrap(打乱顺序 1000 次; 检验的是序列稳健性, 非入场是否有优势)")
    print("=" * 130)
    print(f"  实际: final=${real_final:.2f}  DD={real_dd:.2f}%")
    print(f"  打乱后 final: p5={np.percentile(finals,5):.0f}  "
          f"median={np.median(finals):.0f}  p95={np.percentile(finals,95):.0f}")
    print(f"  打乱后 DD%  : p5={np.percentile(dds,5):.1f}  "
          f"median={np.median(dds):.1f}  p95={np.percentile(dds,95):.1f}")
    print(f"  实际 final 分位: {(finals < real_final).mean()*100:.0f}%")
    print(f"  实际 DD   分位: {(dds < real_dd).mean()*100:.0f}%"
          f"  (越低越好; >95 说明实际回撤比随机序列还差)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profiles", default="best_v1")
    ap.add_argument("--parts", default="A")
    a = ap.parse_args()
    profiles = [p.strip() for p in a.profiles.split(",") if p.strip()]
    print(f"验证 profile: {profiles}")
    if "A" in a.parts:
        part_A(profiles)
    if "B" in a.parts:
        part_B(profiles)
    if "C" in a.parts:
        part_C(profiles)
    if "D" in a.parts:
        part_D(profiles)
    if "E" in a.parts:
        part_E(profiles)


if __name__ == "__main__":
    main()
