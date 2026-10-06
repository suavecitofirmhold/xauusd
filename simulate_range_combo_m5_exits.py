# -*- coding: utf-8 -*-
"""
Replay MT5 RangeCombo entries against broker M5 bars to test whether
intrabar stop/target ordering explains the OOS PnL gap.
"""
from pathlib import Path

import numpy as np
import pandas as pd


M5_RAW = Path(
    r"D:\Data\stockdata\xauusd\tmgm\XAUUSD_M5_202504142130_202609082355.csv"
)
TRADES = Path("mt5_range_combo_is_trades.csv")
OUT = Path("range_combo_m5_exit_replay.csv")


def load_m5():
    raw = pd.read_csv(
        M5_RAW, sep="\t", skiprows=1,
        names=[
            "date", "time", "open", "high", "low", "close",
            "tickvol", "vol", "spread",
        ],
    )
    idx = pd.to_datetime(raw["date"] + " " + raw["time"], format="%Y.%m.%d %H:%M:%S")
    df = raw[["open", "high", "low", "close"]].astype(float)
    df.index = idx
    return df[~df.index.duplicated(keep="first")].sort_index()


def replay(row, m5):
    entry_time = pd.Timestamp(row["open_datetime"])
    side = row["side"]
    entry = float(row["entry"])
    stop = float(row["stop"])
    target = float(row["target"])
    start = int(np.searchsorted(m5.index.values, np.datetime64(entry_time), side="left"))
    end = min(start + 36, len(m5))  # 180 minutes = 12 M15 bars
    exit_price = None
    reason = "time"
    for i in range(start, end):
        bar = m5.iloc[i]
        if bar.name.hour >= 22:
            exit_price = bar["open"]
            reason = "eod"
            break
        if side == "long":
            hit_stop = bar["low"] <= stop
            hit_tp = bar["high"] >= target
        else:
            hit_stop = bar["high"] >= stop
            hit_tp = bar["low"] <= target
        if hit_stop:
            exit_price = stop
            reason = "sl"
            break
        if hit_tp:
            exit_price = target
            reason = "tp"
            break
    if exit_price is None:
        idx = min(end - 1, len(m5) - 1)
        if idx < start:
            exit_price = entry
        else:
            exit_price = m5.iloc[idx]["close"]
    pnl = (exit_price - entry) if side == "long" else (entry - exit_price)
    return pd.Series(dict(replay_pnl=pnl, replay_exit=exit_price, replay_reason=reason))


def main():
    m5 = load_m5()
    trades = pd.read_csv(TRADES, parse_dates=["open_datetime"])
    trades["side"] = trades["side"].replace({"buy": "long", "sell": "short"})
    replay_cols = trades.apply(lambda row: replay(row, m5), axis=1)
    out = pd.concat([trades, replay_cols], axis=1)
    out["pnl_diff"] = out["replay_pnl"] - out["pnl"]
    out.to_csv(OUT, index=False)

    actual_gp = out.loc[out["pnl"] > 0, "pnl"].sum()
    actual_gl = -out.loc[out["pnl"] < 0, "pnl"].sum()
    replay_gp = out.loc[out["replay_pnl"] > 0, "replay_pnl"].sum()
    replay_gl = -out.loc[out["replay_pnl"] < 0, "replay_pnl"].sum()
    print(f"trades={len(out)}")
    print(f"MT5    PnL={out['pnl'].sum():+.2f} PF={actual_gp/actual_gl:.3f}")
    print(f"M5 sim PnL={out['replay_pnl'].sum():+.2f} PF={replay_gp/replay_gl:.3f}")
    print(f"mean |PnL diff|={out['pnl_diff'].abs().mean():.3f}")
    print(out.reindex(out["pnl_diff"].abs().sort_values(ascending=False).index)
          .head(20)[["open_datetime", "side", "entry", "stop", "target",
                     "pnl", "replay_pnl", "replay_reason", "pnl_diff"]]
          .to_string(index=False))


if __name__ == "__main__":
    main()
