"""
细化"浮盈回吐"指标: 区分
  fp      = 持仓曾浮盈(MFE>0) 且 最终以止损(stop)出场
  fp_loss = 其中最终 P&L<0 (真·利润回吐成亏损)   <-- 用户真正的痛点
  fp_win  = 其中最终 P&L>=0 (追踪锁住的小盈利/保本)  <-- 良性

仅对关键配置跑, 避免重复 24 档全扫.
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


def detail(res, df15):
    tl = res["trades"]
    open_stack, paired = [], []
    for t in tl:
        if t["kind"] == "open":
            open_stack.append(t)
        elif t["kind"] == "close":
            if open_stack:
                paired.append((open_stack.pop(), t))
    di = df15.copy()
    di.index = di.index.map(_tznaive)
    total = len(paired)
    n_stop = n_fp = n_fp_loss = n_fp_win = 0
    for o, c in paired:
        edt, cdt = _tznaive(o["dt"]), _tznaive(c["dt"])
        entry = float(o["price"]); side = o["side"]
        d = 1.0 if side == "long" else -1.0
        seg = di.loc[edt:cdt]
        mfe = float((seg["high"] - entry).max()) if side == "long" else float((entry - seg["low"]).max())
        if len(seg) == 0:
            mfe = 0.0
        pnl = (float(c["price"]) - entry) * d
        reason = c.get("reason")
        is_stop = reason in ("sl", "trail")
        if is_stop:
            n_stop += 1
            if mfe > 1e-9:
                n_fp += 1
                if pnl < 0:
                    n_fp_loss += 1
                else:
                    n_fp_win += 1
    return total, n_stop, n_fp, n_fp_loss, n_fp_win


def run(activate, tmult, buf, label):
    extra = {**M.PROFILES["scalp"], "atr_shift": 1,
             "trail_activate_atr": activate, "trail_atr_mult": tmult,
             "trail_be_buffer": buf}
    df15, _, _ = M.load_data("15m", FROM, TO)
    res = M.run_backtest(freq="15m", extra_args=extra, cash=1000.0,
                         fromdate=FROM, todate=TO, quiet=True)
    total, n_stop, n_fp, n_fp_loss, n_fp_win = detail(res, df15)
    net = res["total_ret"] * 100
    pf = res["pf"]; wr = res["win_rate"] * 100
    print(f"[{label:22s}] act={activate} tmult={tmult} buf={buf}")
    print(f"   net={net:+6.1f}%  PF={pf:.2f}  WR={wr:5.1f}%  trades={total}")
    print(f"   stop={n_stop}  fp(浮盈后止损)={n_fp}({n_fp/total*100:.1f}%)  "
          f"fp_loss(回吐成亏)={n_fp_loss}({n_fp_loss/total*100:.1f}%)  "
          f"fp_win(锁小利)={n_fp_win}({n_fp_win/total*100:.1f}%)")
    return dict(label=label, net=net, pf=pf, wr=wr, trades=total,
                stop=n_stop, fp=n_fp, fp_loss=n_fp_loss, fp_win=n_fp_win)


def main():
    print("=" * 70)
    cfgs = [
        (0.0, 1.5, 0.0, "基线(原行为)"),
        (0.5, 1.0, 0.0, "最佳净收益候选"),
        (0.0, 1.5, 0.2, "保本缓冲0.2(高PF)"),
        (0.5, 1.0, 0.2, "激活0.5+跟随1.0+缓冲0.2"),
        (0.5, 0.8, 0.2, "激活0.5+跟随0.8+缓冲0.2"),
    ]
    rows = [run(a, m, b, l) for a, m, b, l in cfgs]
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "refine_giveback.csv")
    pd.DataFrame(rows).to_csv(out, index=False, float_format="%.4f")
    print("\n>> 明细存:", out)


if __name__ == "__main__":
    main()
