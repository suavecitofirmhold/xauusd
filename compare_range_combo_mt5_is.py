# -*- coding: utf-8 -*-
"""
Compare Python and MT5 RangeCombo IS trades.

Inputs:
  py_range_combo_is_full_trades.csv
  mt5_range_combo_is_trades.csv

The MT5 tester log uses broker server time. Convert it to UTC before matching.
"""
from difflib import SequenceMatcher
import sys

import pandas as pd

from box_channel_optimized import tmgm_server_offset_hours


def server_to_utc(value):
    server = pd.Timestamp(value)
    candidates = []
    for offset in (2, 3):
        utc = server - pd.Timedelta(hours=offset)
        if tmgm_server_offset_hours(utc) == offset:
            candidates.append(utc)
    return candidates[0] if candidates else server - pd.Timedelta(hours=3)


def nearest_match(mt5, py_df, minutes):
    used = set()
    matches = []
    for mt5_idx, row in mt5.iterrows():
        candidates = []
        for idx, other in py_df.iterrows():
            if idx in used or other["side"] != row["side"]:
                continue
            diff = abs((row["open_utc"] - other["open_utc"]).total_seconds())
            if diff <= minutes * 60:
                candidates.append((diff, idx, other))
        if candidates:
            diff, idx, other = min(candidates, key=lambda item: item[0])
            used.add(idx)
            matches.append((mt5_idx, row, other, diff / 60.0))
    return matches, used


def main():
    py_path = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "py_range_combo_is_full_trades.csv"
    )
    same_time = len(sys.argv) > 2 and sys.argv[2] == "server"
    py_df = pd.read_csv(py_path, parse_dates=["dtopen"])
    mt5 = pd.read_csv("mt5_range_combo_is_trades.csv", parse_dates=["open_datetime"])
    py_df = py_df.rename(columns={"dtopen": "open_utc", "pnl": "py_pnl"})
    py_df["open_utc"] = pd.to_datetime(py_df["open_utc"], utc=True).dt.tz_localize(None)
    mt5["open_utc"] = (
        mt5["open_datetime"].copy()
        if same_time
        else mt5["open_datetime"].apply(server_to_utc)
    )
    mt5["side"] = mt5["side"].replace({"buy": "long", "sell": "short"})

    print("=" * 88)
    print(
        "RangeCombo full IS alignment: "
        + ("server time vs server time" if same_time else "MT5 server time -> UTC")
    )
    print(f"Python trades file: {py_path}")
    print("=" * 88)
    print(f"Python trades: {len(py_df)}  MT5 trades: {len(mt5)}")
    print("Python sides:", py_df["side"].value_counts().to_dict())
    print("MT5 sides   :", mt5["side"].value_counts().to_dict())

    py_seq = py_df["side"].tolist()
    mt5_seq = mt5["side"].tolist()
    ratio = SequenceMatcher(None, py_seq, mt5_seq).ratio()
    print(f"side sequence similarity: {ratio:.3f}")

    for minutes in (15, 30, 60):
        matches, _ = nearest_match(mt5, py_df, minutes)
        print(
            f"same-side matches within +/-{minutes:>2} min: "
            f"{len(matches)} / MT5 {len(mt5)} / Python {len(py_df)}"
        )

    matches, used = nearest_match(mt5, py_df, 30)
    rows = []
    for mt5_idx, mt5_row, py_row, delay in matches:
        rows.append(dict(
            side=mt5_row["side"],
            mt5_open_utc=mt5_row["open_utc"],
            py_open_utc=py_row["open_utc"],
            delay_min=round(delay, 1),
            mt5_pnl=mt5_row["pnl"],
            py_pnl=py_row["py_pnl"],
        ))
    out = pd.DataFrame(rows)
    out.to_csv("range_combo_is_alignment.csv", index=False)
    if len(out):
        print(
            f"matched PnL: MT5={out['mt5_pnl'].sum():+.2f} USD "
            f"Python={out['py_pnl'].sum():+.2f} USD"
        )
        matched_mt5 = {item[0] for item in matches}
        unmatched_mt5 = mt5.loc[~mt5.index.isin(matched_mt5)]
        unmatched_py = py_df.loc[~py_df.index.isin(used)]
        print("\nUnmatched PnL:")
        print(
            "MT5   ",
            unmatched_mt5.groupby("side")["pnl"].agg(["count", "sum"]).to_dict("index"),
        )
        print(
            "Python",
            unmatched_py.groupby("side")["py_pnl"].agg(["count", "sum"]).to_dict("index"),
        )


if __name__ == "__main__":
    main()
