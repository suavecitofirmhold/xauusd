# -*- coding: utf-8 -*-
"""
Parse BoxChannelRangeCombo MT5 Tester logs.

The EA prints:
  [OPEN] BUY/SELL ...
  [PNL] trade=... USD cumulative=... USD
  [DIAG] ...
  Tester final balance ...

Because the EA is single-position, a simple FIFO pairing is sufficient.
"""
import codecs
import csv
import re
import sys
from pathlib import Path

import pandas as pd


DEFAULT_LOG = (
    r"C:\Users\18628\AppData\Roaming\MetaQuotes\Tester"
    r"\7643C0B96C7AD5841307C9E1EB0B9252\Agent-127.0.0.1-3000"
    r"\logs\20260920.log"
)

OPEN_RE = re.compile(
    r"\[OPEN\]\s+(BUY|SELL)\s+lot=([\d.]+)\s+entry=([\d.]+)\s+"
    r"sl=([\d.]+)\s+tp=([\d.]+)"
)
PNL_RE = re.compile(
    r"\[PNL\]\s+trade=(-?[\d.]+)\s+USD\s+cumulative=(-?[\d.]+)\s+USD"
)
FINAL_RE = re.compile(r"final balance\s+([\d.]+)\s+USD")
DIAG_RE = re.compile(r"\[DIAG\]\s+(.*)")
SIGNAL_RE = re.compile(
    r"\[SIGNAL\]\s+time=(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"rel=(-?[\d.]+)\s+er=(-?[\d.]+)\s+width=(-?[\d.]+)\s+"
    r"atr=(-?[\d.]+)\s+lower=(-?[\d.]+)\s+upper=(-?[\d.]+)\s+"
    r"h=(-?[\d.]+)\s+l=(-?[\d.]+)\s+c=(-?[\d.]+)\s+"
    r"rsi=(-?[\d.]+)\s+"
    r"h1ct=(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"h1pt=(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"h1c=(-?[\d.]+)\s+h1p=(-?[\d.]+)\s+"
    r"belowPrevDay=(-?\d+)\s+flat=(-?\d+)\s+"
    r"longSetup=(-?\d+)\s+shortSetup=(-?\d+)\s+"
    r"longRisk=(-?\d+)\s+shortRisk=(-?\d+)"
)
BAR_RE = re.compile(
    r"\[BAR\]\s+time=(\d{4}\.\d{2}\.\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"o=(-?[\d.]+)\s+h=(-?[\d.]+)\s+l=(-?[\d.]+)\s+c=(-?[\d.]+)\s+"
    r"atr=(-?[\d.]+)\s+rsi=(-?[\d.]+)"
)


def _timestamp(line):
    match = re.search(r"\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}:\d{2}", line)
    return pd.Timestamp(match.group(0)) if match else pd.NaT


def parse_log(path):
    opens = []
    pnls = []
    signals = []
    bars = []
    final_balance = None
    diag = None

    with codecs.open(path, "r", encoding="utf-16", errors="ignore") as fh:
        for line in fh:
            match = OPEN_RE.search(line)
            if match:
                opens.append(dict(
                    datetime=_timestamp(line),
                    side=match.group(1).lower(),
                    lot=float(match.group(2)),
                    entry=float(match.group(3)),
                    stop=float(match.group(4)),
                    target=float(match.group(5)),
                ))
                continue

            match = PNL_RE.search(line)
            if match:
                pnls.append(dict(
                    datetime=_timestamp(line),
                    pnl=float(match.group(1)),
                    cumulative=float(match.group(2)),
                ))
                continue

            match = FINAL_RE.search(line)
            if match:
                final_balance = float(match.group(1))

            match = DIAG_RE.search(line)
            if match:
                diag = match.group(1).strip()
                continue

            match = SIGNAL_RE.search(line)
            if match:
                signals.append(dict(
                    datetime=pd.Timestamp(match.group(1)),
                    rel=float(match.group(2)),
                    er=float(match.group(3)),
                    width=float(match.group(4)),
                    atr=float(match.group(5)),
                    lower=float(match.group(6)),
                    upper=float(match.group(7)),
                    high=float(match.group(8)),
                    low=float(match.group(9)),
                    close=float(match.group(10)),
                    rsi=float(match.group(11)),
                    h1c_time=pd.Timestamp(match.group(12)),
                    h1p_time=pd.Timestamp(match.group(13)),
                    h1c=float(match.group(14)),
                    h1p=float(match.group(15)),
                    below_prev_day=int(match.group(16)),
                    flat=int(match.group(17)),
                    long_setup=int(match.group(18)),
                    short_setup=int(match.group(19)),
                    long_risk=int(match.group(20)),
                    short_risk=int(match.group(21)),
                ))
                continue

            match = BAR_RE.search(line)
            if match:
                bars.append(dict(
                    datetime=pd.Timestamp(match.group(1)),
                    open=float(match.group(2)),
                    high=float(match.group(3)),
                    low=float(match.group(4)),
                    close=float(match.group(5)),
                    atr=float(match.group(6)),
                    rsi=float(match.group(7)),
                ))

    return opens, pnls, signals, bars, final_balance, diag


def analyze(path):
    opens, pnls, signals, bars, final_balance, diag = parse_log(path)
    opened_match = re.search(r"opened=(\d+)", diag or "")
    bars_match = re.search(r"newbar=(\d+)", diag or "")
    if bars_match and len(bars) > int(bars_match.group(1)):
        bars = bars[-int(bars_match.group(1)):]
    if bars:
        start_dt = bars[0]["datetime"]
        end_dt = bars[-1]["datetime"]
        signals = [
            item for item in signals
            if start_dt <= item["datetime"] <= end_dt
        ]
    if opened_match:
        expected = int(opened_match.group(1))
        if len(opens) > expected:
            opens = opens[-expected:]
        if len(pnls) > expected:
            pnls = pnls[-expected:]
    n = min(len(opens), len(pnls))
    rows = []
    balance = 1000.0
    peak = balance
    max_dd = 0.0
    for i in range(n):
        pnl = pnls[i]["pnl"]
        balance += pnl
        peak = max(peak, balance)
        dd = (peak - balance) / peak if peak > 0.0 else 0.0
        max_dd = max(max_dd, dd)
        rows.append(dict(
            id=i + 1,
            open_datetime=opens[i]["datetime"],
            close_datetime=pnls[i]["datetime"],
            side=opens[i]["side"],
            lot=opens[i]["lot"],
            entry=opens[i]["entry"],
            stop=opens[i]["stop"],
            target=opens[i]["target"],
            pnl=pnl,
            balance=balance,
            cumulative_logged=pnls[i]["cumulative"],
        ))

    df = pd.DataFrame(rows)
    out_csv = Path("mt5_range_combo_is_trades.csv")
    df.to_csv(out_csv, index=False)
    signal_df = pd.DataFrame(signals)
    signal_df.to_csv("mt5_range_combo_signals.csv", index=False)
    bar_df = pd.DataFrame(bars)
    bar_df.to_csv("mt5_range_combo_bars.csv", index=False)

    wins = df[df["pnl"] > 0.0] if len(df) else df
    losses = df[df["pnl"] < 0.0] if len(df) else df
    gross_profit = float(wins["pnl"].sum()) if len(wins) else 0.0
    gross_loss = float(-losses["pnl"].sum()) if len(losses) else 0.0
    pf = gross_profit / gross_loss if gross_loss > 0.0 else float("inf")
    total_ret = final_balance / 1000.0 - 1.0 if final_balance is not None else balance / 1000.0 - 1.0
    win_rate = len(wins) / len(df) if len(df) else 0.0

    print("=" * 88)
    print("BoxChannelRangeCombo MT5 Tester result")
    print("=" * 88)
    print(f"log              : {path}")
    print(f"final balance    : {final_balance:.2f} USD")
    print(f"realized PnL     : {balance - 1000.0:+.2f} USD")
    print(f"total return     : {total_ret*100:+.2f}%")
    print(f"opens / pnl rows : {len(opens)} / {len(pnls)}")
    print(f"signal rows      : {len(signal_df)}")
    print(f"bar rows         : {len(bar_df)}")
    if len(signal_df):
        print(f"signal time range: {signal_df['datetime'].min()} -> {signal_df['datetime'].max()}")
    print(f"paired trades    : {len(df)}")
    print(f"win rate         : {win_rate*100:.2f}%")
    print(f"profit factor    : {pf:.3f}")
    print(f"max drawdown     : {max_dd*100:.2f}%")
    print(f"final diag       : {diag}")

    if len(df):
        side_rows = []
        for side, group in df.groupby("side"):
            gp = float(group.loc[group["pnl"] > 0.0, "pnl"].sum())
            gl = float(-group.loc[group["pnl"] < 0.0, "pnl"].sum())
            side_rows.append(dict(
                side=side,
                trades=len(group),
                wins=int((group["pnl"] > 0.0).sum()),
                win_rate=float((group["pnl"] > 0.0).mean() * 100.0),
                pnl=float(group["pnl"].sum()),
                pf=gp / gl if gl > 0.0 else float("inf"),
            ))
        side_df = pd.DataFrame(side_rows)
        print("\nBy side:")
        print(side_df.to_string(index=False, float_format=lambda x: f"{x:.3f}"))

    print(f"\nCSV written: {out_csv.resolve()}")
    print(f"Signals CSV  : {Path('mt5_range_combo_signals.csv').resolve()}")
    print(f"Bars CSV     : {Path('mt5_range_combo_bars.csv').resolve()}")
    return dict(
        final_balance=final_balance,
        total_ret=total_ret,
        realized_pnl=balance - 1000.0,
        trades=len(df),
        win_rate=win_rate,
        pf=pf,
        max_dd=max_dd,
        opens=len(opens),
        pnl_rows=len(pnls),
        signal_rows=len(signal_df),
        bar_rows=len(bar_df),
        diag=diag,
    )


if __name__ == "__main__":
    analyze(sys.argv[1] if len(sys.argv) > 1 else DEFAULT_LOG)
