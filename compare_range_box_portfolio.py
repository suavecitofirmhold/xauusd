# -*- coding: utf-8 -*-
"""
Compare the trend box strategy, the optimized range false-break portfolio,
and a single-position hybrid strategy on the same windows.

Outputs:
  - range_box_portfolio_comparison.csv
  - console summary with daily-return correlation and time overlap
"""
import math
from multiprocessing import Pool

import numpy as np
import pandas as pd

from box_channel_optimized import PROFILES, run_backtest
from xauusd_range_boundary_reversion import (
    COMBINED_SELECTED,
    DEFAULTS as RANGE_DEFAULTS,
    run_range_backtest,
)


WINDOWS = (
    ("IS", "2023-09-13", "2025-06-30"),
    ("OOS", "2025-07-01", "2026-09-08"),
    ("FULL", "2023-09-13", "2026-09-08"),
)

TREND_CFG = dict(
    PROFILES["scalp"],
    atr_shift=1,
    use_reversal_filter=True,
    rev_mode="rev_in_strong",
)


def _run_one(job):
    variant, label, start, end = job
    if variant == "trend_box":
        result = run_backtest(
            extra_args=dict(TREND_CFG),
            fromdate=start,
            todate=end,
            quiet=True,
        )
    elif variant == "range_combo":
        result = run_range_backtest(
            extra_args=dict(RANGE_DEFAULTS, **COMBINED_SELECTED),
            fromdate=start,
            todate=end,
            quiet=True,
        )
    elif variant == "hybrid_box_range":
        hybrid_cfg = {
            k: v for k, v in TREND_CFG.items()
            if k != "entry_style"
        }
        hybrid_cfg.update(COMBINED_SELECTED)
        hybrid_cfg["range_hybrid_with_box"] = True
        result = run_range_backtest(
            extra_args=hybrid_cfg,
            fromdate=start,
            todate=end,
            quiet=True,
        )
    else:
        raise ValueError(variant)
    return variant, label, result


def _equity_series(result):
    if not result.get("eq"):
        return pd.Series(dtype=float)
    idx = pd.to_datetime([item[0] for item in result["eq"]])
    series = pd.Series([float(item[1]) for item in result["eq"]], index=idx)
    series = series[~series.index.duplicated(keep="last")].sort_index()
    return series


def _episode_bars(episodes):
    bars = set()
    for episode in episodes:
        start = pd.Timestamp(episode["dtopen"])
        end = pd.Timestamp(episode["dtclose"])
        if end < start:
            start, end = end, start
        bars.update(pd.date_range(start, end, freq="15min", inclusive="both"))
    return bars


def _max_drawdown(equity):
    if equity.empty:
        return 0.0
    peak = equity.cummax()
    dd = (peak - equity) / peak.replace(0.0, np.nan)
    return float(dd.max() * 100.0)


def _pf(trade_pnls):
    gross_profit = sum(x["pnl"] for x in trade_pnls if x["pnl"] > 0.0)
    gross_loss = -sum(x["pnl"] for x in trade_pnls if x["pnl"] < 0.0)
    return gross_profit / gross_loss if gross_loss > 0.0 else float("inf")


def _compare_one_window(label, results):
    trend = results["trend_box"]
    rng = results["range_combo"]
    hybrid = results["hybrid_box_range"]

    trend_eq = _equity_series(trend)
    range_eq = _equity_series(rng)
    hybrid_eq = _equity_series(hybrid)
    aligned = pd.concat(
        [trend_eq.rename("trend"), range_eq.rename("range")],
        axis=1,
    ).ffill().dropna()
    combined_eq = aligned["trend"] + aligned["range"] - 1000.0

    trend_daily = aligned["trend"].resample("1D").last().ffill().pct_change().dropna()
    range_daily = aligned["range"].resample("1D").last().ffill().pct_change().dropna()
    common = pd.concat(
        [trend_daily.rename("trend"), range_daily.rename("range")],
        axis=1,
    ).dropna()
    corr = float(common["trend"].corr(common["range"])) if len(common) > 1 else float("nan")
    corr_spearman = (
        float(common["trend"].rank().corr(common["range"].rank()))
        if len(common) > 1 else float("nan")
    )

    trend_bars = _episode_bars(trend.get("trade_episodes", []))
    range_bars = _episode_bars(rng.get("trade_episodes", []))
    overlap_bars = len(trend_bars & range_bars)
    union_bars = len(trend_bars | range_bars)
    total_bars = max(len(trend_eq), 1)

    combined_pnls = list(trend.get("trade_pnls", [])) + list(rng.get("trade_pnls", []))
    row = {
        "window": label,
        "trend_ret": round(trend["total_ret"] * 100.0, 2),
        "range_ret": round(rng["total_ret"] * 100.0, 2),
        "hybrid_ret": round(hybrid["total_ret"] * 100.0, 2),
        "independent_ret": round((combined_eq.iloc[-1] / 1000.0 - 1.0) * 100.0, 2),
        "trend_pf": round(trend["pf"], 3),
        "range_pf": round(rng["pf"], 3),
        "hybrid_pf": round(hybrid["pf"], 3),
        "independent_pf": round(_pf(combined_pnls), 3),
        "trend_dd": round(trend["maxdd"], 2),
        "range_dd": round(rng["maxdd"], 2),
        "hybrid_dd": round(hybrid["maxdd"], 2),
        "independent_dd": round(_max_drawdown(combined_eq), 2),
        "trend_trades": trend["closed"],
        "range_trades": rng["closed"],
        "hybrid_trades": hybrid["closed"],
        "independent_trades": len(combined_pnls),
        "daily_ret_corr": round(corr, 4) if math.isfinite(corr) else None,
        "daily_ret_spearman": round(corr_spearman, 4) if math.isfinite(corr_spearman) else None,
        "trend_exposure_pct": round(len(trend_bars) / total_bars * 100.0, 3),
        "range_exposure_pct": round(len(range_bars) / total_bars * 100.0, 3),
        "union_exposure_pct": round(union_bars / total_bars * 100.0, 3),
        "overlap_bars": overlap_bars,
        "overlap_pct_of_range": round(
            overlap_bars / max(len(range_bars), 1) * 100.0, 2
        ),
    }
    return row, combined_eq, hybrid_eq


def main():
    jobs = [
        (variant, label, start, end)
        for label, start, end in WINDOWS
        for variant in ("trend_box", "range_combo", "hybrid_box_range")
    ]
    with Pool(processes=3) as pool:
        raw = pool.map(_run_one, jobs)

    grouped = {}
    for variant, label, result in raw:
        grouped.setdefault(label, {})[variant] = result

    rows = []
    curves = []
    for label, _, _ in WINDOWS:
        row, combined_eq, hybrid_eq = _compare_one_window(label, grouped[label])
        rows.append(row)
        curve = pd.DataFrame({
            "window": label,
            "combined_equity": combined_eq,
            "hybrid_equity": hybrid_eq,
        })
        curves.append(curve.reset_index().rename(columns={"index": "datetime"}))

    df = pd.DataFrame(rows)
    df.to_csv("range_box_portfolio_comparison.csv", index=False)
    pd.concat(curves, ignore_index=True).to_csv(
        "range_box_portfolio_equity.csv", index=False
    )
    pd.set_option("display.width", 260)
    pd.set_option("display.max_columns", 40)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()
