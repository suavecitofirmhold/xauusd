# -*- coding: utf-8 -*-
r"""
Build Python backtest CSVs using TMGM broker server-time bars.

Input:
  D:\Data\stockdata\xauusd\tmgm\XAUUSD_M15_202309140000_202609081600.csv

Output:
  server_data/xauusd_15m_server.csv
  server_data/xauusd_1h_server.csv
  server_data/xauusd_15m_server_spread.csv
"""
from pathlib import Path

import pandas as pd


RAW_M15 = Path(
    r"D:\Data\stockdata\xauusd\tmgm\XAUUSD_M15_202308010000_202609082345.csv"
)
RAW_H1 = Path(
    r"D:\Data\stockdata\xauusd\tmgm\XAUUSD_H1_202308010000_202609082300.csv"
)
OUT_DIR = Path("server_data")


def _load_raw(path):
    raw = pd.read_csv(
        path,
        sep="\t",
        skiprows=1,
        names=[
            "date", "time", "open", "high", "low", "close",
            "tickvol", "vol", "spread",
        ],
    )
    idx = pd.to_datetime(raw["date"] + " " + raw["time"], format="%Y.%m.%d %H:%M:%S")
    df = raw[["open", "high", "low", "close", "tickvol", "spread"]].astype(float)
    df = df.rename(columns={"tickvol": "volume"})
    df.index = idx
    df = df[~df.index.duplicated(keep="first")].sort_index()
    return df


def main():
    df15 = _load_raw(RAW_M15)
    df1h = _load_raw(RAW_H1)

    OUT_DIR.mkdir(exist_ok=True)
    df15[["open", "high", "low", "close", "volume"]].to_csv(
        OUT_DIR / "xauusd_15m_server.csv", index_label="datetime"
    )
    df1h[["open", "high", "low", "close", "volume"]].to_csv(
        OUT_DIR / "xauusd_1h_server.csv", index_label="datetime"
    )
    df15[["spread"]].to_csv(
        OUT_DIR / "xauusd_15m_server_spread.csv", index_label="datetime"
    )

    print(f"15m: {len(df15)} rows  {df15.index[0]} -> {df15.index[-1]}")
    print(f"1h : {len(df1h)} rows  {df1h.index[0]} -> {df1h.index[-1]}")
    print(f"output: {OUT_DIR.resolve()}")


if __name__ == "__main__":
    main()
