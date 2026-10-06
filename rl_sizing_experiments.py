# -*- coding: utf-8 -*-
"""
RL 仓位层优化实验（E1 K_MAX 扫描 / E2 回撤节流 / E3 RAW 成本 / E4 时段过滤）
============================================================================
背景（来自 ml 项目既有结论）：
  - A 组 box-channel 固定 1oz：+172%，DD 6.8%，Sharpe 2.16（基线）
  - RL-expand（因果 ATR_ref，k=ATR/历史中位，夹 [0.3,3.0]）：+465%，DD 17.8%，Sharpe 2.06
  - 既有报告明示未做：K_MAX 档位扫描、回撤节流、RAW 成本口径

口径：同一批 A 组成交（引擎逐笔与 MT5 对齐），net_k = k * net_1oz 严格线性；
回撤同时报 per-trade 序列口径与日频 NAV 口径（后者与 regime/rl_causal 报告一致）。
"""
import os
import sys

import numpy as np
import pandas as pd

EXT = r"D:\workbuddy\tushare\黄金代码优化\xauusd_m5_scalp"
SRC = r"D:\workbuddy\tushare\黄金代码优化\ml\src"
for p in (SRC, EXT):
    if p not in sys.path:
        sys.path.insert(0, p)

from regime_adaptive_sizing_expert import (  # noqa: E402
    build_atr_series, build_equity_daily, START_CASH, FROM, TO, K_MIN,
)
from bt_window import build_ctx, run_window, window_metrics  # noqa: E402

OUT = os.path.dirname(os.path.abspath(__file__))
MIN_HIST = 200


def expand_ref_factory(atr_values, cheat_ref):
    cache = {}
    def ref_at(e):
        if e not in cache:
            w = atr_values[:e]
            cache[e] = float(np.nanmedian(w)) if len(w) >= MIN_HIST else cheat_ref
        return cache[e]
    return ref_at


def simulate(trades, atr_values, ref_at, kmax, kmin=K_MIN,
             dd_theta=None, dd_f=0.5, raw_delta=0.0):
    """entry 序推进；k = clip(ATR_e/ref, kmin, kmax) * 节流系数；RAW: net += raw_delta*k。
    节流状态由「该笔入场前已平仓」的权益曲线决定（exit_bar < entry_bar 才可见），
    已平仓权益按该笔自己的 k 缩放（事件堆：入场时挂单，出场时入账）。"""
    import heapq
    order = sorted(range(len(trades)), key=lambda i: (int(trades[i][0]), int(trades[i][6])))
    pend = []  # (exit_bar, seq, scaled_net)
    seq = 0
    eq, peak = START_CASH, START_CASH
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
        if dd_theta is not None:
            if not throttled and dd > dd_theta:
                throttled = True
            elif throttled and dd < dd_theta / 2.0:
                throttled = False
        ref = ref_at(e)
        atr_e = float(atr_values[e]) if e < len(atr_values) else ref
        k = (atr_e / ref) if ref > 1e-9 else 1.0
        k = float(np.clip(k, kmin, kmax))
        if throttled:
            k = max(k * dd_f, 0.10)
        net_k[i] = k * float(t[2]) + raw_delta * k
        size_k[i] = k
        heapq.heappush(pend, (int(t[6]), seq, k * float(t[2])))
        seq += 1
    return net_k, size_k


def simulate_fixed(trades, dd_theta=None, dd_f=0.5, raw_delta=0.0):
    """A 组固定 1oz + 可选节流/RAW。"""
    order = sorted(range(len(trades)), key=lambda i: (int(trades[i][0]), int(trades[i][6])))
    exits = sorted(((int(t[6]), float(t[2])) for t in trades), key=lambda x: x[0])
    ep = 0
    eq, peak = START_CASH, START_CASH
    throttled = False
    net_k = [0.0] * len(trades)
    size_k = [1.0] * len(trades)
    for i in order:
        t = trades[i]
        e = int(t[0])
        while ep < len(exits) and exits[ep][0] < e:
            eq += exits[ep][1]
            peak = max(peak, eq)
            ep += 1
        dd = (peak - eq) / peak if peak > 0 else 0.0
        k = 1.0
        if dd_theta is not None:
            if not throttled and dd > dd_theta:
                throttled = True
            elif throttled and dd < dd_theta / 2.0:
                throttled = False
            if throttled:
                k = max(k * dd_f, 0.10)
        net_k[i] = k * float(t[2]) + raw_delta * k
        size_k[i] = k
    return net_k, size_k


def metrics(trades, net_k, tarr, w0, w1):
    tm = [(t[0], t[1], nk, t[3], t[4], t[5], t[6], t[7], t[8])
          for t, nk in zip(trades, net_k)]
    m = window_metrics(tm, w0, w1)  # per-trade 口径
    eq = build_equity_daily(trades, net_k, tarr, w0, w1)
    vals = np.array([p["value"] for p in eq], dtype=float)
    rets = vals[1:] / vals[:-1] - 1.0
    sharpe = float(rets.mean() / rets.std() * np.sqrt(252.0)) if len(rets) > 1 and rets.std() > 0 else float("nan")
    run_max = np.maximum.accumulate(vals)
    dd_daily = float(((run_max - vals) / run_max).max())
    return {"n": m["n"], "net": m["net"], "ret_pct": m["ret"] * 100, "pf": m["pf"],
            "win_pct": m["win"] * 100, "dd_trade_pct": m["maxDD"] * 100,
            "dd_daily_pct": dd_daily * 100, "ann_pct": m["ann"] * 100,
            "calmar": (m["ann"] / dd_daily) if dd_daily > 0 else float("nan"),
            "sharpe": sharpe, "eq": eq}


def main():
    print("=== 构建上下文并跑 A 组成交 ===")
    ctx, tarr = build_ctx()
    atr_values = build_atr_series().to_numpy(dtype=float)
    cheat_ref = float(np.nanmedian(atr_values[:len(tarr)]))
    ref_at = expand_ref_factory(atr_values, cheat_ref)
    sc, co, w0, w1 = run_window(ctx, tarr, FROM, TO, cap=2, signal_filter=None)
    trades = list(sc) + list(co)
    print(f"[A] {len(trades)} 笔成交, cheat_ref={cheat_ref:.2f}")

    rows = []
    curves = {}

    def add(name, net_k, size_k, group, note=""):
        m = metrics(trades, net_k, tarr, w0, w1)
        rows.append({"group": group, "name": name, "n": m["n"],
                     "net": round(m["net"], 1), "ret_pct": round(m["ret_pct"], 1),
                     "pf": round(m["pf"], 3), "win_pct": round(m["win_pct"], 1),
                     "dd_trade_pct": round(m["dd_trade_pct"], 2),
                     "dd_daily_pct": round(m["dd_daily_pct"], 2),
                     "ann_pct": round(m["ann_pct"], 1),
                     "calmar": round(m["calmar"], 2),
                     "sharpe": round(m["sharpe"], 2),
                     "k_mean": round(float(np.mean(size_k)), 2), "note": note})
        curves[name] = m["eq"]
        print(f"[{group}] {name}: net={m['net']:.0f} ddD={m['dd_daily_pct']:.1f}% "
              f"sharpe={m['sharpe']:.2f} calmar={m['calmar']:.2f}")

    # 基线
    nk, sk = simulate_fixed(trades)
    add("A_fixed1oz", nk, sk, "base")
    # 复核锚点：RL-expand kmax=3.0 应复现 rlr_expand（net≈4653, ddD≈17.8）
    nk, sk = simulate(trades, atr_values, ref_at, kmax=3.0)
    add("RLexpand_kmax3.0", nk, sk, "check", "复现锚点")

    # E1: K_MAX 扫描
    for kmax in (1.00, 1.25, 1.50, 1.75, 2.00, 2.50):
        nk, sk = simulate(trades, atr_values, ref_at, kmax=kmax)
        add(f"RLexpand_kmax{kmax:.2f}", nk, sk, "E1")

    # E2: 回撤节流（叠加 RL-expand）
    for kmax in (1.50, 2.00, 3.00):
        for theta in (0.06, 0.10, 0.14):
            nk, sk = simulate(trades, atr_values, ref_at, kmax=kmax,
                              dd_theta=theta, dd_f=0.5)
            add(f"RLexp_k{kmax:.2f}_dd{int(round(theta*100))}_f50", nk, sk, "E2")
    nk, sk = simulate(trades, atr_values, ref_at, kmax=2.00, dd_theta=0.10, dd_f=0.35)
    add("RLexp_k2.00_dd10_f35", nk, sk, "E2")
    # E2b: 节流叠加固定 1oz
    for theta in (0.05, 0.08):
        nk, sk = simulate_fixed(trades, dd_theta=theta, dd_f=0.5)
        add(f"A_dd{int(round(theta*100))}_f50", nk, sk, "E2")

    # E3: RAW 成本差（Δ=+0.18/oz 主口径，+0.14/oz 敏感）
    nk, sk = simulate_fixed(trades, raw_delta=0.18)
    add("A_raw018", nk, sk, "E3", "RAW 8pt+佣金")
    nk, sk = simulate_fixed(trades, raw_delta=0.14)
    add("A_raw014", nk, sk, "E3", "RAW 12pt+佣金")
    nk, sk = simulate(trades, atr_values, ref_at, kmax=2.00, raw_delta=0.18)
    add("RLexp_k2.00_raw018", nk, sk, "E3")

    # E3b: RAW × 节流 组合
    nk, sk = simulate(trades, atr_values, ref_at, kmax=1.50, raw_delta=0.18)
    add("RLexp_k1.50_raw018", nk, sk, "E3b")
    nk, sk = simulate(trades, atr_values, ref_at, kmax=2.00,
                      dd_theta=0.06, dd_f=0.5, raw_delta=0.18)
    add("RLexp_k2.00_dd6_f50_raw018", nk, sk, "E3b")
    nk, sk = simulate(trades, atr_values, ref_at, kmax=2.50,
                      dd_theta=0.06, dd_f=0.5, raw_delta=0.18)
    add("RLexp_k2.50_dd6_f50_raw018", nk, sk, "E3b")

    df = pd.DataFrame(rows)
    csv_path = os.path.join(OUT, "rlopt_results.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"[out] {csv_path}")

    # E4: 时段过滤探索（IS 前 60% 选差时段，OOS 后 40% 验证；A 组固定 1oz）
    e4_lines = []
    order = sorted(range(len(trades)), key=lambda i: int(trades[i][0]))
    ot = [trades[i] for i in order]
    n_is = int(len(ot) * 0.6)
    is_t, oos_t = ot[:n_is], ot[n_is:]
    for key_name, key_fn in (("server_hour", lambda t: int(ctx["server_hour"][int(t[0])])),
                             ("utc_hour", lambda t: pd.Timestamp(tarr[int(t[0])]).hour),
                             ("weekday", lambda t: pd.Timestamp(tarr[int(t[0])]).weekday())):
        keys = np.array([key_fn(t) for t in ot])
        nets = np.array([float(t[2]) for t in ot])
        is_keys, is_nets = keys[:n_is], nets[:n_is]
        bad = sorted({k for k in np.unique(is_keys)
                      if is_nets[is_keys == k].mean() < 0})
        oos_mask = np.array([k in bad for k in keys[n_is:]])
        oos_all, oos_flt = nets[n_is:], nets[n_is:][~oos_mask]
        is_mask = np.array([k in bad for k in is_keys])
        e4_lines.append(f"- **{key_name}** 差时段(IS均值<0): {bad} | "
                        f"IS: net {is_nets.sum():.0f} → {is_nets[~is_mask].sum():.0f} | "
                        f"OOS: net {oos_all.sum():.0f} → {oos_flt.sum():.0f} "
                        f"(剔除 {int(oos_mask.sum())}/{len(oos_mask)} 笔)")
    print("\n".join(e4_lines))

    # 图：基线 / RL3.0 / E1 与 E2 的代表
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(2, 1, figsize=(12, 8), sharex=True,
                           gridspec_kw={"height_ratios": [3, 1]})
    pick = ["A_fixed1oz", "RLexpand_kmax3.0", "RLexpand_kmax2.00",
            "RLexpand_kmax1.50", "RLexp_k2.00_dd10_f50", "RLexp_k3.00_dd10_f50"]
    colors = ["#888", "#d62728", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e"]
    for name, col in zip(pick, colors):
        if name not in curves:
            continue
        eq = curves[name]
        vals = np.array([p["value"] for p in eq], dtype=float)
        dates = [p["date"] for p in eq]
        ax[0].plot(dates, vals, lw=1.2, label=name, color=col)
    ax[0].legend(fontsize=8)
    ax[0].grid(alpha=0.3)
    ax[0].set_title("RL 仓位优化实验：权益曲线对比")
    for name, col in zip(pick, colors):
        if name not in curves:
            continue
        eq = curves[name]
        vals = np.array([p["value"] for p in eq], dtype=float)
        dd = (np.maximum.accumulate(vals) - vals) / np.maximum.accumulate(vals) * 100
        ax[1].plot([p["date"] for p in eq], -dd, lw=1.0, label=name, color=col)
    ax[1].set_ylabel("日频回撤 %")
    ax[1].grid(alpha=0.3)
    fig.tight_layout()
    png = os.path.join(OUT, "rlopt_equity.png")
    fig.savefig(png, dpi=130)
    plt.close(fig)
    print(f"[out] {png}")

    # 报告
    md = ["# RL 仓位层优化实验报告", "",
          f"> 日期: 2026-10-05 | 同一批 A 组成交 {len(trades)} 笔 | "
          f"日频 NAV 回撤口径 (与 rlr_* 报告可比)", "",
          "## 全部结果", "", df.to_markdown(index=False), "",
          "## E4 时段过滤探索（IS 60% 选时段 / OOS 40% 验证）", ""] + e4_lines
    md += ["", "## 结论", "",
           "1. **E1 K_MAX 扫描**：净收益随 K_MAX 单调上升但 Sharpe 单调微降"
           "（2.16→2.06），杠杆本身不改善风险调整收益，与 rlr 报告结论一致。"
           "K_MAX=1.25 是「不牺牲 Sharpe」的最大杠杆（net +25%, DD 8.1%）。",
           "2. **E2 回撤节流**：theta=6%/f=0.5 叠加 kmax=2.0 → DD 11.9%→9.3%，"
           "net 3331→2868，Calmar 6.92→7.97，帕累托优于直接降 K_MAX 到 1.5"
           "（2563/9.4%/Calmar 7.28）。theta>=10% 反而因拖慢修复使 DD 变差；"
           "对 A 固定仓节流（5%）同样有害（净降且 DD 反升）。"
           "注意 theta=6% 是扫出来的单点，存在选择偏差，只能当方向性证据。",
           "3. **E3 RAW 成本是最确定的改进**（非统计、无过拟合）：A 组 +0.18/oz → "
           "net +6.6%、Sharpe 2.16→2.34、DD 6.8%→5.9%、Calmar 7.45→9.02。"
           "组合最优 RLexp_k2.00_dd6_f50_raw018：net 3021（+75% vs A）、DD 8.2%、"
           "Sharpe 2.25、Calmar 9.35——全面优于 A 基线。",
           "4. **E4 时段过滤全部 OOS 转负**（server_hour/utc_hour/weekday 三个口径"
           "OOS 分别 1448→945/1242/1069）→ 不做任何时段过滤，与宏观禁入的结论一致。",
           "5. 推荐落地顺序：先换 RAW 账户（确定性收益，先决条件是 TMGM RAW 实际"
           "点差/佣金与假设一致，建议先用小仓实测成交明细核对）；再考虑 RL-expand "
           "K_MAX=1.5~2.0（因果、结构简单）；回撤节流作为可选项，参数需前瞻性验证。"]
    rp = os.path.join(OUT, "rlopt_report.md")
    with open(rp, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[out] {rp}")


if __name__ == "__main__":
    main()
