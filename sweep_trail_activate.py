"""
trail_activate_atr (呼吸空间) 灵敏度扫参: 固定 trailing=True / trail_atr_mult=1.0 / trail_be_buffer=0.0,
只扫 激活门限 ∈ {0, 0.3, 0.5, 0.8, 1.0}.

指标:
  - net/PF/WR/trades: 性能
  - fp   = 浮盈后跌止损(曾 MFE>0 且 reason in sl/trail) 占全部 %  —— 回吐发生面
  - fp_loss = 浮盈后跌止损 且 最终亏损(pnl<0) 占全部 %          —— 真实回吐亏损面

口径: 与 analyze_float_profit_then_stop.py 同(MFE 用 15m OHLC 持仓窗口)
"""
import sys, os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import box_channel_optimized as M

FROM = pd.Timestamp("2024-03-04")
TO = pd.Timestamp("2026-08-26")


def _tznaive(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize(None) if ts.tz is not None else ts


def _fp_metrics(res):
    """复用 MFE 配对, 返回 (n_fp, n_fp_loss, total)."""
    df15, _, _ = M.load_data("15m", FROM, TO)
    df15.index = df15.index.map(_tznaive)
    tl = res["trades"]
    open_stack, paired = [], []
    for t in tl:
        if t["kind"] == "open":
            open_stack.append(t)
        elif t["kind"] == "close" and open_stack:
            paired.append((open_stack.pop(), t))
    n_fp = n_fp_loss = 0
    for o, c in paired:
        edt, cdt = _tznaive(o["dt"]), _tznaive(c["dt"])
        entry = float(o["price"]); side = o["side"]
        target = float(o["target"]) if o.get("target") is not None else np.nan
        seg = df15.loc[edt:cdt]
        ds = 1.0 if side == "long" else -1.0
        if side == "long":
            mfe = float((seg["high"] - entry).max()) if len(seg) else 0.0
        else:
            mfe = float((entry - seg["low"]).max()) if len(seg) else 0.0
        pnl = (float(c["price"]) - entry) * ds
        reason = c.get("reason")
        is_stop = reason in ("sl", "trail")
        if mfe > 1e-9 and is_stop:
            n_fp += 1
            if pnl < 0:
                n_fp_loss += 1
    total = len(paired)
    return n_fp, n_fp_loss, total


def run_one(act):
    extra = {**M.PROFILES["scalp"], "atr_shift": 1,
             "trailing": True,
             "trail_activate_atr": act,
             "trail_atr_mult": 1.0,
             "trail_be_buffer": 0.0}
    res = M.run_backtest(freq="15m", extra_args=extra, cash=1000.0,
                         fromdate=FROM, todate=TO, quiet=True)
    n_fp, n_fp_loss, total = _fp_metrics(res)
    return dict(
        trail_activate_atr=act,
        entries=res["entries"],
        net=res["total_ret"] * 100, pf=res["pf"], wr=res["win_rate"] * 100,
        trades=res["closed"],
        fp_pct=n_fp / total * 100 if total else 0.0,
        fp_loss_pct=n_fp_loss / total * 100 if total else 0.0,
    )


def main():
    print("=" * 92)
    print("trail_activate_atr 扫参 (trailing=True / trail_atr_mult=1.0 / trail_be_buffer=0.0)")
    print("=" * 92)
    rows = []
    for act in (0.0, 0.3, 0.5, 0.8, 1.0):
        r = run_one(act)
        rows.append(r)
        print(f"激活={act:.1f}×ATR: 入场 {r['entries']:>4} | net={r['net']:+.1f}% "
              f"PF={r['pf']:.2f} WR={r['wr']:.1f}% trades={r['trades']} | "
              f"fp={r['fp_pct']:.1f}% fp_loss={r['fp_loss_pct']:.1f}%")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sweep_trail_activate.csv")
    pd.DataFrame(rows).to_csv(out, index=False, float_format="%.4f")
    print("\n>> 存:", out)


if __name__ == "__main__":
    main()
