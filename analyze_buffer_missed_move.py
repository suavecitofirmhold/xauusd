"""
保本缓冲"错过更大上涨"代价(精确版, 响应追加约束):
  识别"缓冲平仓"(最终SL落在 entry±buffer×ATR 保本带), 且要求:
   (a) 持仓期间价格从未触及【原始硬止损】o["stop"](entry±1.5×ATR) —— 即那次小幅回调
       连原始止损都没碰到, 原始止损本可扛住;
   (b) 出场后价格触及原 TP(4h窗口 + to-end上界) —— 即错过后面更大上涨。
  满足(a)+(b)的交易 = 缓冲严格比原始止损更狠、凭空牺牲的赢家。

运行: python analyze_buffer_missed_move.py
"""
import sys, os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import box_channel_optimized as M

FROM = pd.Timestamp("2024-03-04")
TO = pd.Timestamp("2026-08-26")
FWD_BARS = 16  # 向前 4 小时(15m×16)


def _tznaive(ts):
    ts = pd.Timestamp(ts)
    return ts.tz_localize(None) if ts.tz is not None else ts


def analyze(activate, tmult, buf, label):
    extra = {**M.PROFILES["scalp"], "atr_shift": 1,
             "trail_activate_atr": activate, "trail_atr_mult": tmult,
             "trail_be_buffer": buf}
    df15, _, _ = M.load_data("15m", FROM, TO)
    df15.index = df15.index.map(_tznaive)
    res = M.run_backtest(freq="15m", extra_args=extra, cash=1000.0,
                         fromdate=FROM, todate=TO, quiet=True)

    tl = res["trades"]
    open_stack, paired = [], []
    for t in tl:
        if t["kind"] == "open":
            open_stack.append(t)
        elif t["kind"] == "close":
            if open_stack:
                paired.append((open_stack.pop(), t))

    n_total = len(paired)
    n_stop = 0
    n_buf = 0                 # 精确缓冲平仓
    n_buf_shallow = 0         # 缓冲平仓 且 持仓未触原始止损(回调浅)
    n_buf_shallow_miss4h = 0  # 浅回调缓冲平仓 且 出场后4h触TP
    n_buf_shallow_miss_end = 0
    forgone4h = 0.0
    forgone_end = 0.0

    for o, c in paired:
        edt, cdt = _tznaive(o["dt"]), _tznaive(c["dt"])
        entry = float(o["price"]); side = o["side"]
        target = float(o["target"]) if o.get("target") is not None else np.nan
        o_stop = float(o["stop"]) if o.get("stop") is not None else np.nan
        final_stop = float(c["stop"]) if c.get("stop") is not None else np.nan
        exit_px = float(c["price"])
        d = 1.0 if side == "long" else -1.0
        reason = c.get("reason")
        if reason not in ("sl", "trail"):
            continue
        n_stop += 1
        # 精确识别缓冲平仓: 最终SL落在 entry±buffer×ATR 保本带(±0.15×ATR容差)
        seg = df15.loc[edt:cdt]
        if len(seg) == 0 or buf <= 0 or np.isnan(final_stop) or np.isnan(o_stop):
            continue
        # 原始止损距离 = |o_stop-entry| = atr_stop_mult×ATR; 保本带 = entry ± buf×ATR
        atr_e = abs(o_stop - entry) / M.PROFILES["scalp"]["atr_stop_mult"]
        be_level = entry + buf * atr_e if d == 1 else entry - buf * atr_e
        if abs(final_stop - be_level) > 0.15 * atr_e:
            continue
        n_buf += 1
        # (a) 持仓期间是否触及原始硬止损
        reached_orig = (seg["low"].min() <= o_stop) if d == 1 else (seg["high"].max() >= o_stop)
        if reached_orig:
            continue  # 原始止损也会被触发, 非缓冲独有代价
        n_buf_shallow += 1
        # (b) 出场后是否触及原 TP
        fwd = df15.loc[cdt:].iloc[1:]
        if len(fwd) == 0 or np.isnan(target):
            continue
        hit4h = (fwd["high"].iloc[:FWD_BARS].max() >= target) if d == 1 else \
                (fwd["low"].iloc[:FWD_BARS].min() <= target)
        hit_end = (fwd["high"].max() >= target) if d == 1 else (fwd["low"].min() <= target)
        if hit4h:
            n_buf_shallow_miss4h += 1
            forgone4h += (target - exit_px) if d == 1 else (exit_px - target)
        if hit_end:
            n_buf_shallow_miss_end += 1
            forgone_end += (target - exit_px) if d == 1 else (exit_px - target)

    net = res["total_ret"] * 100
    print(f"[{label}] act={activate} tmult={tmult} buf={buf}")
    print(f"   总 {n_total}  止损 {n_stop}  | 缓冲平仓(精确)={n_buf}")
    print(f"   其中 持仓未触原始止损(浅回调)={n_buf_shallow}")
    print(f"     → 且出场后4h触TP(错过更大涨, 缓冲独有代价)={n_buf_shallow_miss4h} "
          f"({n_buf_shallow_miss4h/n_total*100:.1f}% of all)  未实现≈${forgone4h:+.1f}")
    print(f"     → 且出场后至末尾触TP(上界)={n_buf_shallow_miss_end}  未实现≈${forgone_end:+.1f}")
    print(f"   净收益 {net:+.1f}% (≈${net/100*1000:+.0f})")
    return dict(label=label, total=n_total, stop=n_stop, buf_exit=n_buf,
                shallow=n_buf_shallow, miss4h=n_buf_shallow_miss4h,
                miss_end=n_buf_shallow_miss_end, forgone4h=forgone4h,
                forgone_end=forgone_end, net=net)


def main():
    print("=" * 74)
    cfgs = [
        (0.5, 1.0, 0.0, "推荐(无缓冲)"),
        (0.5, 1.0, 0.2, "加缓冲0.2"),
        (0.0, 1.5, 0.2, "宽跟随+缓冲0.2"),
    ]
    rows = [analyze(a, m, b, l) for a, m, b, l in cfgs]
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "analyze_buffer_missed_move.csv")
    pd.DataFrame(rows).to_csv(out, index=False, float_format=".4f")
    print("\n>> 存:", out)


if __name__ == "__main__":
    main()
