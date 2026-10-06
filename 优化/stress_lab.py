# -*- coding: utf-8 -*-
"""Cost-stress replay for the selected P0 execution mode."""
import os

import numpy as np
import pandas as pd

import sr_execution_lab as lab


OUT = lab.OUT


SCENARIOS = [
    {"name": "base", "spread": lab.CLASSIC_SPREAD_USD,
     "slip": lab.SLIP_STOP_LIMIT_POINTS, "swap": lab.SWAP_PER_NIGHT},
    {"name": "slip_x2", "spread": lab.CLASSIC_SPREAD_USD,
     "slip": lab.SLIP_STOP_LIMIT_POINTS * 2, "swap": lab.SWAP_PER_NIGHT},
    {"name": "spread_x1.5", "spread": lab.CLASSIC_SPREAD_USD * 1.5,
     "slip": lab.SLIP_STOP_LIMIT_POINTS, "swap": lab.SWAP_PER_NIGHT},
    {"name": "spread_x2", "spread": lab.CLASSIC_SPREAD_USD * 2,
     "slip": lab.SLIP_STOP_LIMIT_POINTS, "swap": lab.SWAP_PER_NIGHT},
    {"name": "swap_x2", "spread": lab.CLASSIC_SPREAD_USD,
     "slip": lab.SLIP_STOP_LIMIT_POINTS, "swap": lab.SWAP_PER_NIGHT * 2},
    {"name": "combined", "spread": lab.CLASSIC_SPREAD_USD * 1.5,
     "slip": lab.SLIP_STOP_LIMIT_POINTS * 2, "swap": lab.SWAP_PER_NIGHT * 2},
]


def main():
    print("=== SR+ML execution cost stress ===")
    trades = pd.read_parquet(os.path.join(OUT, "execution_trades.parquet"))
    selected = trades[
        (trades["mode"] == "follow_stop_limit")
        & (trades["selection"] == "follow_ridge")
    ]
    event_idx = selected["event_idx"].to_numpy(dtype=int)
    bars, events, _, m1 = lab.load_data()
    m15_close = bars["close"].to_numpy(dtype=float)
    m1_ts = m1["datetime_utc"].to_numpy(dtype="datetime64[ns]")
    m1_open = m1["open"].to_numpy(dtype=float)
    m1_high = m1["high"].to_numpy(dtype=float)
    m1_low = m1["low"].to_numpy(dtype=float)
    m1_close = m1["close"].to_numpy(dtype=float)

    rows = []
    replayed = []
    for scenario in SCENARIOS:
        lab.CLASSIC_SPREAD_USD = scenario["spread"]
        lab.SLIP_STOP_LIMIT_POINTS = scenario["slip"]
        lab.SWAP_PER_NIGHT = scenario["swap"]
        pnls = np.zeros(len(events), dtype=float)
        r_values = np.full(len(events), np.nan, dtype=float)
        filled = np.zeros(len(events), dtype=bool)
        for idx in event_idx:
            result = lab.simulate_event(
                events.iloc[idx],
                "follow_stop_limit",
                m1_ts,
                m1_open,
                m1_high,
                m1_low,
                m1_close,
                m15_close,
                geometry=lab.GEOM_FOLLOW,
            )
            if result is None or not result["filled"]:
                continue
            filled[idx] = True
            pnls[idx] = result["pnl"]
            r_values[idx] = result["R"]
            if scenario["name"] == "base":
                replayed.append({
                    "event_idx": idx,
                    "entry_time": result["entry_time"],
                    "exit_time": result["exit_time"],
                    "pnl": result["pnl"],
                    "R": result["R"],
                })
        stat = lab.metrics(
            pnls, filled, r_values=r_values, candidate_count=len(event_idx)
        )
        stat.update(scenario)
        rows.append(stat)
        print(
            f"[{scenario['name']:12s}] n={stat['n_trades']:3d} "
            f"net={stat['net']:8.1f} PF={stat['pf']:5.3f} "
            f"E[R]={stat['E_R']:7.4f} DD={stat['maxDD_pct']:5.1f}%"
        )

    summary = pd.DataFrame(rows)[[
        "name", "spread", "slip", "swap", "n_trades", "net", "net_per_trade",
        "pf", "E_R", "maxDD_pct", "fill_rate",
    ]]
    summary.to_csv(os.path.join(OUT, "stress_summary.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(replayed).to_parquet(
        os.path.join(OUT, "stress_base_trades.parquet"), index=False
    )

    is_cutoff = pd.Timestamp("2020-12-01")
    base = pd.DataFrame(replayed)
    base["entry_time"] = pd.to_datetime(base["entry_time"])
    is_trades = base[base["entry_time"] < is_cutoff]
    oos_trades = base[base["entry_time"] >= is_cutoff]
    split_rows = []
    for name, sub in (("IS", is_trades), ("OOS", oos_trades)):
        gross_profit = sub.loc[sub["pnl"] > 0, "pnl"].sum()
        gross_loss = -sub.loc[sub["pnl"] < 0, "pnl"].sum()
        equity = 1000.0 + sub.sort_values("exit_time")["pnl"].cumsum()
        drawdown = ((equity.cummax() - equity) / equity.cummax()).max()
        split_rows.append({
            "split": name,
            "cutoff": is_cutoff,
            "n": len(sub),
            "net": sub["pnl"].sum(),
            "pf": gross_profit / gross_loss if gross_loss > 0 else np.inf,
            "E_R": sub["R"].mean(),
            "maxDD_pct": drawdown * 100.0,
        })
    split = pd.DataFrame(split_rows)
    split.to_csv(os.path.join(OUT, "stress_is_oos.csv"), index=False, encoding="utf-8-sig")
    print(
        f"[IS ] n={len(is_trades):3d} net={is_trades['pnl'].sum():8.1f} "
        f"PF={split.iloc[0]['pf']:5.3f}"
    )
    print(
        f"[OOS] n={len(oos_trades):3d} net={oos_trades['pnl'].sum():8.1f} "
        f"PF={split.iloc[1]['pf']:5.3f}"
    )
    print(f"[out] {os.path.join(OUT, 'stress_summary.csv')}")


if __name__ == "__main__":
    main()
