# -*- coding: utf-8 -*-
r"""RL 推荐组合可重跑脚本（K_MAX=2.0 + 回撤节流 θ=6%/f=0.5）
===============================================================
结论来自：
  - rl_sizing_experiments.py  全样本帕累托
  - rl_forward_validate.py    IS(60%)/OOS(40%) 前向验证（θ=6% 为 IS 最优，OOS 仍优于不节流）

用法：直接运行，或改 CONFIG 后运行。所有参数集中在顶部 CONFIG。
只读 D:\workbuddy\...\ml 与 xauusd_m5_scalp，产物写到本文件所在目录。

复现锚点（CONFIG 默认值）：
  全样本  net≈2868 / +286.8% / DD≈9.28% / Sharpe≈2.11 / Calmar≈7.97（口径同 rlopt_report）
  OOS 段  (2025-10-13 之后) net≈2487 / DD≈8.5% / Sharpe≈3.15 / Calmar≈18.2
Calmar 口径：全样本用 window_metrics.ann(按成交窗口年化)÷日频回撤；
            OOS 段沿用 rl_forward_validate 的日频序列年化口径。
"""
import json
import os
import sys

import numpy as np
import pandas as pd

EXT = r"D:\workbuddy\tushare\黄金代码优化\xauusd_m5_scalp"
SRC = r"D:\workbuddy\tushare\黄金代码优化\ml\src"
for p in (SRC, EXT):
    if p not in sys.path:
        sys.path.insert(0, p)

import box_channel_combo_py as bc  # noqa: E402
from bt_window import build_ctx, run_window, window_metrics  # noqa: E402
from regime_adaptive_sizing_expert import build_atr_series  # noqa: E402

OUT = os.path.dirname(os.path.abspath(__file__))

# ---------------- 可重跑配置 ----------------
CONFIG = {
    "from": "2024-03-01",
    "to": "2026-09-01",
    "oos_from": "2025-10-13",  # OOS 段起点（前向验证 60/40 切分的 OOS 首日）
    "k_min": 0.30,        # 仓位系数下限（RL 结构，防极端）
    "k_max": 2.00,        # 推荐组合：2.0
    "dd_theta": 0.06,     # 回撤节流阈值（trailing DD > 6% 触发，半仓）
    "dd_f": 0.50,         # 节流时的仓位保留系数（0.5 = 半仓）
    "dd_recover": None,   # None = 用 dd_theta/2 作为恢复迟滞（推荐）
    "atr_ref_mode": "expand",  # 因果 ATR_ref：入场前全历史 ATR 中位
    "min_hist": 200,      # 因果窗口最少根数（不足则回退到全样本中位，实际样本下不触发）
    "raw_delta": 0.0,     # 每 oz 成本差。0 = 经典账户真实口径；模拟 RAW 8点+佣金设 0.18
}


def expand_ref_factory(atr_values, cheat_ref, min_hist):
    cache = {}
    def ref_at(e):
        if e not in cache:
            w = atr_values[:e]
            cache[e] = float(np.nanmedian(w)) if len(w) >= min_hist else cheat_ref
        return cache[e]
    return ref_at


def simulate(trades, atr_values, ref_at, cfg):
    """entry 序推进；k = clip(ATR_e/ref, kmin, kmax) * 节流系数；RAW: net += raw_delta*k。
    节流状态由「该笔入场前已平仓」的权益曲线决定（事件堆：入场挂单、出场入账）。"""
    import heapq
    k_min, k_max = cfg["k_min"], cfg["k_max"]
    theta, f = cfg["dd_theta"], cfg["dd_f"]
    use_throttle = theta is not None
    recover = cfg["dd_recover"]
    if recover is None:
        recover = (theta / 2.0) if use_throttle else 0.0
    raw_delta = cfg["raw_delta"]

    order = sorted(range(len(trades)), key=lambda i: (int(trades[i][0]), int(trades[i][6])))
    pend = []
    eq, peak, seq = bc.START_CASH, bc.START_CASH, 0
    throttled = False
    net_k = [0.0] * len(trades)
    size_k = [1.0] * len(trades)
    for i in order:
        t = trades[i]
        e = int(t[0])
        while pend and pend[0][0] < e:
            _, _, xnet = heapq.heappop(pend)
            eq += xnet
            peak = max(peak, eq)
        dd = (peak - eq) / peak if peak > 0 else 0.0
        if use_throttle:
            if not throttled and dd > theta:
                throttled = True
            elif throttled and dd < recover:
                throttled = False
        ref = ref_at(e)
        atr_e = float(atr_values[e]) if e < len(atr_values) else ref
        k = (atr_e / ref) if ref > 1e-9 else 1.0
        k = float(np.clip(k, k_min, k_max))
        if throttled:
            k = max(k * f, 0.10)
        net_k[i] = k * float(t[2]) + raw_delta * k
        size_k[i] = k
        heapq.heappush(pend, (int(t[6]), seq, k * float(t[2])))
        seq += 1
    return net_k, size_k


def build_equity_daily(trades, net_k, tarr, w0, w1):
    nets_by_exit = {}
    for t, nk in zip(trades, net_k):
        b = int(t[6])
        nets_by_exit[b] = nets_by_exit.get(b, 0.0) + float(nk)
    cum = bc.START_CASH
    rows = []
    for b in range(w0, w1 + 1):
        if b in nets_by_exit:
            cum += nets_by_exit[b]
        rows.append((pd.Timestamp(tarr[b]), cum))
    df = pd.DataFrame(rows, columns=["date", "value"])
    df["day"] = df["date"].dt.floor("D")
    daily = (df.groupby("day")["value"].last().reset_index()
             .rename(columns={"day": "date"}))
    return daily


def daily_metrics(equity):
    vals = equity["value"].to_numpy(dtype=float)
    run_max = np.maximum.accumulate(vals)
    dd = float(((run_max - vals) / run_max).max())
    rets = vals[1:] / vals[:-1] - 1.0
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252.0)) if len(rets) > 1 and rets.std() > 0 else float("nan")
    return {"net": float(vals[-1] - vals[0]),
            "ret_pct": float(vals[-1] / vals[0] - 1.0) * 100,
            "maxDD_pct": dd * 100,
            "sharpe": sharpe}


def seg_metrics(equity, start_date, end_date=None):
    """OOS 段：日频序列年化口径（与 rl_forward_validate.seg_metrics 一致）。"""
    mask = equity["date"] >= pd.Timestamp(start_date)
    if end_date is not None:
        mask &= equity["date"] <= pd.Timestamp(end_date)
    vals = equity.loc[mask, "value"].to_numpy(dtype=float)
    if len(vals) < 2:
        return {"net": 0.0, "dd": 0.0, "sharpe": float("nan"), "calmar": float("nan")}
    run_max = np.maximum.accumulate(vals)
    dd = float(((run_max - vals) / run_max).max())
    rets = vals[1:] / vals[:-1] - 1.0
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252.0)) if rets.std() > 0 else float("nan")
    ndays = max(len(vals), 1)
    ann = (vals[-1] / vals[0]) ** (252.0 / ndays) - 1.0 if vals[0] > 0 else float("nan")
    calmar = float(ann / dd) if dd > 0 else float("nan")
    return {"net": float(vals[-1] - vals[0]), "dd": dd * 100, "sharpe": sharpe,
            "calmar": calmar, "ann_pct": ann * 100}


def build_trade_rows(trades, net_k, size_k, tarr):
    rows = []
    for t, nk, k in zip(trades, net_k, size_k):
        entry = float(t[5]); net = float(t[2]); stop0 = float(t[7])
        risk = abs(entry - stop0) * bc.OZ
        r = net / risk if risk > 1e-9 else float("nan")
        rows.append({
            "entry_date": str(pd.Timestamp(tarr[int(t[0])])),
            "exit_date": str(pd.Timestamp(tarr[int(t[6])])),
            "side": "long" if t[1] > 0 else "short",
            "size_oz": round(float(k), 4),
            "entry_price": entry,
            "exit_price": float(t[4]),
            "pnl": round(float(nk), 4),
            "pnl_R": round(float(r), 4),
            "holding_bars": int(t[6]) - int(t[0]),
        })
    return rows


def _ann_calmar(wm, daily_dd_pct):
    ann = wm["ann"]
    calmar = ann / (daily_dd_pct / 100.0) if daily_dd_pct > 0 else float("nan")
    return ann * 100.0, calmar


def main():
    print("=== RL 推荐组合回测（可重跑） ===")
    print("[cfg]", json.dumps(CONFIG, ensure_ascii=False))
    ctx, tarr = build_ctx()
    atr_values = build_atr_series().to_numpy(dtype=float)
    cheat_ref = float(np.nanmedian(atr_values[:len(tarr)]))
    ref_at = expand_ref_factory(atr_values, cheat_ref, CONFIG["min_hist"])
    sc, co, w0, w1 = run_window(ctx, tarr, CONFIG["from"], CONFIG["to"],
                                cap=2, signal_filter=None)
    trades = list(sc) + list(co)
    print(f"[A] {len(trades)} 笔成交, cheat_ref={cheat_ref:.2f}")

    net_k, size_k = simulate(trades, atr_values, ref_at, CONFIG)
    equity = build_equity_daily(trades, net_k, tarr, w0, w1)
    d = daily_metrics(equity)
    tm = [(t[0], t[1], nk, t[3], t[4], t[5], t[6], t[7], t[8])
          for t, nk in zip(trades, net_k)]
    wm = window_metrics(tm, w0, w1)
    ann_pct, calmar = _ann_calmar(wm, d["maxDD_pct"])
    print(f"[combo] n={len(trades)} net=${d['net']:.0f} (+{d['ret_pct']:.1f}%) "
          f"maxDD={d['maxDD_pct']:.2f}% ann={ann_pct:.1f}% sharpe={d['sharpe']:.2f} "
          f"calmar={calmar:.2f} pf={wm['pf']:.3f} win={wm['win']*100:.1f}% "
          f"avg_k={np.mean(size_k):.2f}")

    oos = seg_metrics(equity, CONFIG["oos_from"])
    print(f"[oos ] from {CONFIG['oos_from']} net=${oos['net']:.0f} "
          f"maxDD={oos['dd']:.2f}% sharpe={oos['sharpe']:.2f} "
          f"calmar={oos['calmar']:.2f}")

    cfg_a = dict(CONFIG, k_max=1.0, k_min=1.0, dd_theta=None, dd_f=1.0, raw_delta=0.0)
    net_a, size_a = simulate(trades, atr_values, ref_at, cfg_a)
    eq_a = build_equity_daily(trades, net_a, tarr, w0, w1)
    d_a = daily_metrics(eq_a)
    tm_a = [(t[0], t[1], nk, t[3], t[4], t[5], t[6], t[7], t[8])
            for t, nk in zip(trades, net_a)]
    wm_a = window_metrics(tm_a, w0, w1)
    ann_a, calmar_a = _ann_calmar(wm_a, d_a["maxDD_pct"])
    print(f"[A1oz ] net=${d_a['net']:.0f} (+{d_a['ret_pct']:.1f}%) "
          f"maxDD={d_a['maxDD_pct']:.2f}% ann={ann_a:.1f}% sharpe={d_a['sharpe']:.2f} "
          f"calmar={calmar_a:.2f}")

    equity.to_csv(os.path.join(OUT, "combo_equity.csv"), index=False, encoding="utf-8-sig")
    pd.DataFrame(build_trade_rows(trades, net_k, size_k, tarr)).to_csv(
        os.path.join(OUT, "combo_trades.csv"), index=False, encoding="utf-8-sig")
    summary = {
        "config": CONFIG,
        "n": len(trades),
        "net": round(d["net"], 2),
        "ret_pct": round(d["ret_pct"], 2),
        "maxDD_pct": round(d["maxDD_pct"], 2),
        "ann_pct": round(ann_pct, 2),
        "sharpe": round(d["sharpe"], 2),
        "calmar": round(calmar, 2),
        "pf": round(wm["pf"], 3),
        "win_pct": round(wm["win"] * 100, 2),
        "R_mean": round(wm["R_mean"], 3),
        "avg_k": round(float(np.mean(size_k)), 3),
        "oos": {k: round(v, 2) if isinstance(v, float) else v for k, v in oos.items()},
        "baseline_A1oz_net": round(d_a["net"], 2),
        "baseline_A1oz_maxDD_pct": round(d_a["maxDD_pct"], 2),
        "baseline_A1oz_sharpe": round(d_a["sharpe"], 2),
        "baseline_A1oz_calmar": round(calmar_a, 2),
    }
    with open(os.path.join(OUT, "combo_summary.json"), "w", encoding="utf-8") as fp:
        json.dump(summary, fp, ensure_ascii=False, indent=2)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                           gridspec_kw={"height_ratios": [3, 1]})
    for eq, lbl in ((equity, "combo K_MAX=2.0+节流6%"), (eq_a, "A 固定 1oz")):
        col = "#1f77b4" if eq is equity else "#888"
        v = eq["value"].to_numpy(dtype=float)
        ax[0].plot(eq["date"], v, lw=1.2, color=col, label=lbl)
        ddline = (np.maximum.accumulate(v) - v) / np.maximum.accumulate(v) * 100
        ax[1].plot(eq["date"], ddline, lw=1.0, color=col)
    ax[0].legend(fontsize=9); ax[0].grid(alpha=0.3); ax[0].set_ylabel("权益 $")
    ax[1].set_ylabel("日频回撤 %"); ax[1].grid(alpha=0.3)
    ax[1].set_xlabel("UTC")
    fig.suptitle("RL 推荐组合 vs A 固定 1oz（日频 NAV）")
    fig.tight_layout()
    png = os.path.join(OUT, "combo_equity.png")
    fig.savefig(png, dpi=130)
    plt.close(fig)
    print("[out]", os.path.join(OUT, "combo_equity.csv"))
    print("[out]", os.path.join(OUT, "combo_trades.csv"))
    print("[out]", os.path.join(OUT, "combo_summary.json"))
    print("[out]", png)


if __name__ == "__main__":
    main()
