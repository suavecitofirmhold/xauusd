"""
分析 XAUUSD 15m scalp 回测: 统计"开仓后有浮盈、但未达止盈、最终跌到止损"的交易占比。

口径:
  - 浮盈 (MFE>0): 持仓期间, 价格相对入场价的最大有利偏移为正。
      long : MFE = max(high - entry)
      short: MFE = max(entry - low)
  - 未达止盈: 持仓期间价格从未触及 target (若触及则 reason 必为 "tp", 故 stop 结果已隐含未达止盈;
            这里额外校验 tp_hit 以暴露 backtrader 同根 bar 优先级异常)。
  - 跌到止损: 平仓 reason in {"sl","trail"} (trail = 追踪止损, 本质也是止损)。
  - "float-profit-then-stop" = MFE>0 且 reason in {sl,trail}。

用法: 直接运行 (python 用 managed venv)
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


def main():
    print(">> 运行回测 (scalp 默认 + atr_shift=1, 对齐 EA) ...")
    extra = {**M.PROFILES["scalp"], "atr_shift": 1}
    res = M.run_backtest(freq="15m", extra_args=extra, cash=1000.0,
                         fromdate=FROM, todate=TO, quiet=False)

    df15, _, _ = M.load_data("15m", FROM, TO)
    df15.index = df15.index.map(_tznaive)

    # FIFO 配对 open/close
    tl = res["trades"]
    open_stack = []
    paired = []
    for t in tl:
        if t["kind"] == "open":
            open_stack.append(t)
        elif t["kind"] == "close":
            if open_stack:
                paired.append((open_stack.pop(), t))

    rows = []
    for o, c in paired:
        edt = _tznaive(o["dt"])
        cdt = _tznaive(c["dt"])
        entry = float(o["price"])
        side = o["side"]  # "long"/"short"
        stop = float(o["stop"]) if o.get("stop") is not None else np.nan
        target = float(o["target"]) if o.get("target") is not None else np.nan
        exit_px = float(c["price"])
        reason = c.get("reason")

        # 持仓窗口 (含入场 bar 与平仓 bar)
        seg = df15.loc[edt:cdt]
        if len(seg) == 0:
            seg = df15.iloc[[df15.index.get_indexer([edt])[0]]] if edt in df15.index else df15.iloc[0:0]

        dir_sign = 1.0 if side == "long" else -1.0
        if side == "long":
            mfe = float((seg["high"] - entry).max()) if len(seg) else 0.0
            mae = float((seg["low"] - entry).min()) if len(seg) else 0.0
            tp_hit = bool(seg["high"].max() >= target) if not np.isnan(target) else False
            sl_dist = entry - stop if not np.isnan(stop) else np.nan
            tp_dist = target - entry if not np.isnan(target) else np.nan
        else:
            mfe = float((entry - seg["low"]).max()) if len(seg) else 0.0
            mae = float((entry - seg["high"]).min()) if len(seg) else 0.0
            tp_hit = bool(seg["low"].min() <= target) if not np.isnan(target) else False
            sl_dist = stop - entry if not np.isnan(stop) else np.nan
            tp_dist = entry - target if not np.isnan(target) else np.nan

        pnl = (exit_px - entry) * dir_sign
        is_stop = reason in ("sl", "trail")
        had_float = mfe > 1e-9
        # 浮盈曾达到止盈幅度之比 (仅对未 tp_hit 的有意义)
        mfe_pct_tp = (mfe / tp_dist) if (not np.isnan(tp_dist) and tp_dist > 0) else np.nan

        rows.append(dict(
            entry_dt=edt, side=side, entry=entry, stop=stop, target=target,
            exit_dt=cdt, exit_price=exit_px, reason=reason,
            hold_bars=len(seg), mfe=mfe, mae=mae, pnl=pnl,
            tp_hit=tp_hit, is_stop=is_stop, had_float=had_float,
            mfe_pct_tp=mfe_pct_tp,
        ))

    df = pd.DataFrame(rows)
    total = len(df)
    n_stop = int(df["is_stop"].sum())
    n_tp = int((df["reason"] == "tp").sum())
    n_other = total - n_stop - n_tp  # margin/manual/eod

    # 核心指标
    stop_df = df[df["is_stop"]]
    n_stop_float = int((stop_df["had_float"]).sum())
    # 全样本 "浮盈后跌止损"
    fp = df[df["had_float"] & df["is_stop"]]
    n_fp = len(fp)

    print("\n" + "=" * 64)
    print("  交易盈亏结构 / 浮盈回吐(stop) 统计")
    print("=" * 64)
    print(f"  总平仓交易        : {total}")
    print(f"  止盈(tp)          : {n_tp}  ({n_tp/total*100:.1f}%)")
    print(f"  止损(sl+trail)    : {n_stop}  ({n_stop/total*100:.1f}%)")
    print(f"  其他(margin/eod等): {n_other}  ({n_other/total*100:.1f}%)")
    print("-" * 64)
    print(f"  ★ 止损交易中 '曾浮盈'      : {n_stop_float} / {n_stop}  = {n_stop_float/n_stop*100:.1f}%")
    print(f"  ★ 全部交易中 '浮盈后跌止损': {n_fp} / {total}  = {n_fp/total*100:.1f}%")
    print("-" * 64)
    # 长/短拆分
    for s in ("long", "short"):
        sub = df[df["side"] == s]
        sub_stop = sub[sub["is_stop"]]
        nf = int(sub_stop["had_float"].sum())
        nall = len(sub)
        nfall = int((sub["had_float"] & sub["is_stop"]).sum())
        print(f"  [{s:5s}] 总{nall:4d}  止损{nf}/{len(sub_stop):4d}({nf/len(sub_stop)*100:.1f}%)  "
              f"浮盈后跌止损 {nfall}({nfall/nall*100:.1f}%)")

    # 异常: stop 结果但价格曾触及 target (同根 bar 优先级问题)
    anomaly = stop_df[stop_df["tp_hit"]]
    print("-" * 64)
    print(f"  ⚠ 止损结果却曾触及止盈价(tp_hit)的异常笔数: {len(anomaly)}")

    # 浮盈回吐交易的 MFE 深度分布
    if n_fp:
        print("-" * 64)
        print("  浮盈后跌止损交易: MFE 占止盈幅度比(mfe_pct_tp) 分布")
        mp = fp["mfe_pct_tp"].dropna()
        for lo, hi in [(0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0), (1.0, 1.01)]:
            cnt = int(((mp >= lo) & (mp < hi)).sum())
            print(f"    MFE/TPdist in [{lo:.2f},{hi:.2f}): {cnt}")
        print(f"    平均 MFE 价格幅度 : {fp['mfe'].mean():.2f} USD")
        print(f"    平均 MFE/TPdist   : {mp.mean()*100:.1f}%")
        print(f"    平均 最终亏损     : {fp[fp['pnl']<0]['pnl'].mean():.2f} USD  "
              f"(亏损笔 {int((fp['pnl']<0).sum())}/{n_fp})")
        # 其中 MFE 曾 >= 止盈幅度(即几乎要到但没到/异常)
        near_tp = int((mp >= 0.999).sum())
        print(f"    MFE 曾 >= 99%止盈幅度(差点就到): {near_tp}")

    # 保存明细
    out_csv = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "float_profit_then_stop_trades.csv")
    fp_out = fp.sort_values("mfe_pct_tp", ascending=False)
    cols = ["entry_dt", "side", "entry", "stop", "target", "exit_dt", "exit_price",
            "reason", "hold_bars", "mfe", "mae", "pnl", "tp_hit", "mfe_pct_tp"]
    fp_out[cols].to_csv(out_csv, index=False, float_format="%.4f")
    print("\n>> 浮盈后跌止损交易明细已存:", out_csv, f"({len(fp_out)} 笔)")

    return df


if __name__ == "__main__":
    main()
