"""
两段式追踪止损 扫参验证.

对 (trail_activate_atr, trail_atr_mult, trail_be_buffer) 做网格, 每档跑 scalp 全样本回测,
输出: 净收益% / PF / 胜率 / 笔数 / 浮盈回吐(stop)率, 用于对比原行为.

运行: python sweep_trailing.py   (managed venv)
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


def giveback_metrics(res, df15):
    """FIFO 配对 + MFE, 返回 (total, n_stop, n_fp, win_rate, pf, net)."""
    tl = res["trades"]
    open_stack, paired = [], []
    for t in tl:
        if t["kind"] == "open":
            open_stack.append(t)
        elif t["kind"] == "close":
            if open_stack:
                paired.append((open_stack.pop(), t))
    df15idx = df15.index.map(_tznaive)
    df15i = df15.copy()
    df15i.index = df15idx
    n_stop = 0
    n_fp = 0
    for o, c in paired:
        edt, cdt = _tznaive(o["dt"]), _tznaive(c["dt"])
        entry = float(o["price"])
        side = o["side"]
        stop = float(o["stop"]) if o.get("stop") is not None else np.nan
        target = float(o["target"]) if o.get("target") is not None else np.nan
        seg = df15i.loc[edt:cdt]
        dir_sign = 1.0 if side == "long" else -1.0
        if side == "long":
            mfe = float((seg["high"] - entry).max()) if len(seg) else 0.0
        else:
            mfe = float((entry - seg["low"]).max()) if len(seg) else 0.0
        reason = c.get("reason")
        is_stop = reason in ("sl", "trail")
        if is_stop:
            n_stop += 1
            if mfe > 1e-9:
                n_fp += 1
    total = len(paired)
    return total, n_stop, n_fp


def main():
    df15, _, _ = M.load_data("15m", FROM, TO)
    grid = []
    for activate in (0.0, 0.5):
        for tmult in (0.6, 0.8, 1.0, 1.5):
            for buf in (0.0, 0.2, 0.4):
                grid.append((activate, tmult, buf))

    rows = []
    print(f"{'act':>4} {'tmult':>6} {'buf':>5} | {'net%':>7} {'PF':>5} {'WR%':>6} "
          f"{'trades':>6} {'stop':>5} {'fp':>4} {'fp%':>6}")
    print("-" * 64)
    for activate, tmult, buf in grid:
        extra = {**M.PROFILES["scalp"], "atr_shift": 1,
                 "trail_activate_atr": activate, "trail_atr_mult": tmult,
                 "trail_be_buffer": buf}
        res = M.run_backtest(freq="15m", extra_args=extra, cash=1000.0,
                             fromdate=FROM, todate=TO, quiet=True)
        total, n_stop, n_fp = giveback_metrics(res, df15)
        net = res["total_ret"] * 100
        pf = res["pf"]
        wr = res["win_rate"] * 100
        fp_pct = (n_fp / total * 100) if total else 0.0
        rows.append(dict(activate=activate, tmult=tmult, buf=buf, net=net, pf=pf,
                         wr=wr, trades=total, stop=n_stop, fp=n_fp, fp_pct=fp_pct))
        print(f"{activate:>4.1f} {tmult:>6.1f} {buf:>5.1f} | "
              f"{net:>+7.1f} {pf:>5.2f} {wr:>6.1f} {total:>6d} {n_stop:>5d} "
              f"{n_fp:>4d} {fp_pct:>6.1f}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sweep_trailing.csv")
    pd.DataFrame(rows).to_csv(out, index=False, float_format="%.4f")
    print("\n>> 结果已存:", out)
    # 推荐: 净收益>=基线且 fp% 最低
    base = [r for r in rows if r["activate"] == 0.0 and r["tmult"] == 1.5 and r["buf"] == 0.0][0]
    better = [r for r in rows if r["net"] >= base["net"] - 2.0]
    if better:
        best = min(better, key=lambda r: r["fp_pct"])
        print(f">> 基线(原行为) net={base['net']:.1f}% fp%={base['fp_pct']:.1f}%")
        print(f">> 推荐(净收益>=基线-2pp 中 fp% 最低): "
              f"act={best['activate']} tmult={best['tmult']} buf={best['buf']} "
              f"-> net={best['net']:.1f}% PF={best['pf']:.2f} WR={best['wr']:.1f}% "
              f"fp%={best['fp_pct']:.1f}%")
    return rows


if __name__ == "__main__":
    main()
