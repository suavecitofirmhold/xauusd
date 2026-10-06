# -*- coding: utf-8 -*-
"""
Run RangeCombo on broker server-time bars and compare with MT5.

The source raw file is already timestamped in broker server time. No UTC
conversion is applied to signal bars.
"""
from multiprocessing import Pool
from pathlib import Path

import pandas as pd

from xauusd_range_boundary_reversion import (
    MT5_ALIGNED_CONFIG,
    run_range_backtest,
)


FROM = "2023-09-14"
TO = "2025-06-30"
DATA_DIR = Path("server_data")


def _load_server_data():
    df15 = pd.read_csv(
        DATA_DIR / "xauusd_15m_server.csv",
        parse_dates=["datetime"],
    ).set_index("datetime")
    df1h = pd.read_csv(
        DATA_DIR / "xauusd_1h_server.csv",
        parse_dates=["datetime"],
    ).set_index("datetime")
    spread = pd.read_csv(
        DATA_DIR / "xauusd_15m_server_spread.csv",
        parse_dates=["datetime"],
    ).set_index("datetime")["spread"]
    return df15, df1h, spread


def _run_one(shift):
    df15, df1h, spread = _load_server_data()
    cfg = dict(MT5_ALIGNED_CONFIG)
    cfg["atr_shift"] = shift
    cfg["range_debug_signals"] = True
    result = run_range_backtest(
        extra_args=cfg,
        fromdate=FROM,
        todate=TO,
        primary_df=df15,
        context_df=df1h,
        spread_series=spread,
        quiet=True,
    )
    row = {
        "atr_shift": shift,
        "ret_pct": round(result["total_ret"] * 100.0, 3),
        "pf": round(result["pf"], 3),
        "dd_pct": round(result["maxdd"], 3),
        "trades": result["closed"],
        "win_pct": round(result["win_rate"] * 100.0, 3),
        "exposure_pct": round(result["exposure"], 3),
    }
    trades = pd.DataFrame(result["trade_episodes"])
    if len(trades):
        counts = trades["side"].value_counts().to_dict()
        row["long"] = int(counts.get("long", 0))
        row["short"] = int(counts.get("short", 0))
    else:
        row["long"] = 0
        row["short"] = 0
    trades.to_csv(f"server_time_range_combo_atr{shift}_trades.csv", index=False)
    pd.DataFrame(result["range_signal_debug"]).to_csv(
        f"server_time_range_combo_atr{shift}_signals.csv", index=False
    )
    return row


def main():
    with Pool(processes=3) as pool:
        rows = pool.map(_run_one, (0, 1, 2))
    df = pd.DataFrame(rows).sort_values("atr_shift")
    df.to_csv("server_time_range_combo_grid.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.3f}"))


if __name__ == "__main__":
    main()
