# -*- coding: utf-8 -*-
"""
导出 scalp(极致短线) 策略完整交易记录 → CSV。
逐笔配对开/平, 计算: 盈亏(USD/%)、R倍数、持仓时长、入场 UTC 时段/星期、止损止盈价等。
用法: python export_scalp_trades.py [--fromdate 2025-06-01] [--todate 2026-08-29] [--out scalp_trades_record.csv]
"""
import os, sys, argparse
import pandas as pd
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from box_channel_optimized import run_backtest, PROFILES, load_data

OZ = 0.01 * 100.0  # 0.01手 = 1 盎司


def session_of(hour):
    if 0 <= hour <= 6:
        return "亚洲(00-06)"
    if 7 <= hour <= 11:
        return "伦敦(07-11)"
    if 12 <= hour <= 16:
        return "伦/纽重叠(12-16)"
    if 17 <= hour <= 21:
        return "纽约(17-21)"
    return "悉尼(22-23)"


def build_record(m):
    """把 trades_log 配对成逐笔成交记录。"""
    rows = []
    pending = None
    tid = 0
    for t in m["trades"]:
        dt = pd.Timestamp(t["dt"])
        if t["kind"] == "open":
            pending = dict(side=t["side"], entry_dt=dt, entry=t["price"],
                           stop=t.get("stop"), target=t.get("target"),
                           r_mult=t.get("r_mult"))
        else:
            if pending is None:
                continue
            exit_dt = dt
            exit_p = t["price"]
            reason = t.get("reason")
            sign = 1 if pending["side"] == "long" else -1
            pnl = sign * (exit_p - pending["entry"]) * OZ
            risk = abs(pending["entry"] - (pending["stop"] or pending["entry"])) * OZ
            r_mult = (pnl / risk) if risk > 1e-9 else float("nan")
            hold_min = (exit_dt - pending["entry_dt"]).total_seconds() / 60.0
            hold_bars = int(round(hold_min / 15.0))
            notional = pending["entry"] * OZ
            pnl_pct = pnl / notional * 100.0 if notional > 0 else float("nan")
            tid += 1
            rows.append(dict(
                id=tid,
                side=pending["side"],
                entry_dt=pending["entry_dt"].strftime("%Y-%m-%d %H:%M"),
                exit_dt=exit_dt.strftime("%Y-%m-%d %H:%M"),
                entry_price=round(pending["entry"], 2),
                exit_price=round(exit_p, 2),
                stop_price=round(pending["stop"], 2) if pending["stop"] else "",
                target_price=round(pending["target"], 2) if pending["target"] else "",
                exit_reason=reason or "unknown",
                pnl_usd=round(pnl, 2),
                pnl_pct=round(pnl_pct, 3),
                r_multiple=round(r_mult, 3),
                r_mult=(round(pending["r_mult"], 2) if pending.get("r_mult") is not None else ""),
                holding_min=round(hold_min, 1),
                holding_bars=hold_bars,
                entry_hour_utc=pending["entry_dt"].hour,
                entry_dow=pending["entry_dt"].strftime("%a"),
                entry_session=session_of(pending["entry_dt"].hour),
                initial_risk_usd=round(risk, 2),
            ))
            pending = None
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fromdate", default="2025-06-01")
    ap.add_argument("--todate", default="2026-08-29")
    ap.add_argument("--profile", default="scalp",
                   choices=["scalp", "scalp_raw", "scalp_adapt", "scalp_tf48", "scalp_tf_atr", "scalp_rev_strong"],
                   help="导出哪个画像的逐笔记录(默认 scalp 无过滤基线; 对拍 EA 请用 scalp_rev_strong=rev_in_strong+cd=8)")
    ap.add_argument("--out", default=os.path.join(HERE, "scalp_trades_record.csv"))
    args = ap.parse_args()

    cfg = PROFILES[args.profile]
    m = run_backtest(extra_args=cfg, fromdate=args.fromdate, todate=args.todate, quiet=False)
    rows = build_record(m)
    df = pd.DataFrame(rows)
    cols = ["id", "side", "entry_dt", "exit_dt", "entry_price", "exit_price",
            "stop_price", "target_price", "exit_reason", "pnl_usd", "pnl_pct",
            "r_multiple", "holding_min", "holding_bars", "entry_hour_utc",
            "entry_dow", "entry_session", "initial_risk_usd"]
    df = df[cols]
    df.to_csv(args.out, index=False, encoding="utf-8-sig")

    gp = df[df.pnl_usd > 0].pnl_usd.sum()
    gl = -df[df.pnl_usd < 0].pnl_usd.sum()
    print(f"已导出: {args.out}")
    print(f"  区间 {args.fromdate}~{args.todate} | 成交 {len(df)} 笔 | "
          f"累计收益 {m['total_ret']*100:+.1f}% | 胜率 {m['win_rate']*100:.1f}%")
    print(f"  盈利合计 {gp:.1f} / 亏损合计 {gl:.1f} | 盈亏比(PF) "
          f"{(gp/gl if gl>0 else float('nan')):.2f}")


if __name__ == "__main__":
    main()
