# -*- coding: utf-8 -*-
"""
RL 仓位层前向验证（IS 60% / OOS 40%，时间切分，与 T6_geometry 同口径）
=====================================================================
检验两条结论是否只在全样本上成立：
  - K_MAX=1.5~2.0 的收益/回撤帕累托
  - 回撤节流 θ=6% 叠加 K_MAX=2.0 的 Calmar 改善

方法：用 IS 段选参（按 Calmar），在 OOS 段报告同参数表现；并列出全部变体的
IS/OOS 对照表，看排序是否稳定。所有 sizing 本身完全因果（ATR_ref=入场前历史中位），
只有「参数选择」走 IS/OOS 纪律。
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
    build_atr_series, build_equity_daily, START_CASH, FROM, TO,
)
from bt_window import build_ctx, run_window  # noqa: E402
from rl_sizing_experiments import (  # noqa: E402
    expand_ref_factory, simulate, simulate_fixed,
)

OUT = os.path.dirname(os.path.abspath(__file__))


def seg_metrics(eq, start_date, end_date):
    """在 [start_date, end_date] 区间内切日频权益，算 net/dd/sharpe/calmar。"""
    vals = np.array([p["value"] for p in eq
                     if start_date <= p["date"] <= end_date], dtype=float)
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
            "calmar": calmar, "ann": ann * 100}


def main():
    print("=== 构建 A 组成交 ===")
    ctx, tarr = build_ctx()
    atr_values = build_atr_series().to_numpy(dtype=float)
    cheat_ref = float(np.nanmedian(atr_values[:len(tarr)]))
    ref_at = expand_ref_factory(atr_values, cheat_ref)
    sc, co, w0, w1 = run_window(ctx, tarr, FROM, TO, cap=2, signal_filter=None)
    trades = list(sc) + list(co)
    print(f"[A] {len(trades)} 笔")

    # 按 entry 时间切分 IS/OOS（60/40）
    order = sorted(range(len(trades)), key=lambda i: int(trades[i][0]))
    n_is = int(len(order) * 0.6)
    is_end_bar = int(trades[order[n_is - 1]][0])
    is_end_date = str(pd.Timestamp(tarr[is_end_bar]).date())
    is_start_date = str(pd.Timestamp(tarr[int(trades[order[0]][0])]).date())
    oos_start_date = str(pd.Timestamp(tarr[int(trades[order[n_is]][0])]).date())
    oos_end_date = str(pd.Timestamp(tarr[w1]).date())
    print(f"[split] IS {is_start_date}..{is_end_date} ({n_is} 笔) | "
          f"OOS {oos_start_date}..{oos_end_date} ({len(order)-n_is} 笔)")

    rows = []
    curves = {}

    def run(name, net_k, size_k, group):
        eq = build_equity_daily(trades, net_k, tarr, w0, w1)
        is_m = seg_metrics(eq, is_start_date, is_end_date)
        oos_m = seg_metrics(eq, oos_start_date, oos_end_date)
        rows.append({"group": group, "name": name,
                      "IS_net": round(is_m["net"]), "IS_dd": round(is_m["dd"], 1),
                      "IS_sharpe": round(is_m["sharpe"], 2), "IS_calmar": round(is_m["calmar"], 2),
                      "OOS_net": round(oos_m["net"]), "OOS_dd": round(oos_m["dd"], 1),
                      "OOS_sharpe": round(oos_m["sharpe"], 2),
                      "OOS_calmar": round(oos_m["calmar"], 2),
                      "k_mean": round(float(np.mean(size_k)), 2)})
        curves[name] = eq
        print(f"[{group}] {name}: IS net={is_m['net']:.0f} dd={is_m['dd']:.1f}% "
              f"sh={is_m['sharpe']:.2f} cal={is_m['calmar']:.2f} | "
              f"OOS net={oos_m['net']:.0f} dd={oos_m['dd']:.1f}% "
              f"sh={oos_m['sharpe']:.2f} cal={oos_m['calmar']:.2f}")

    # 基线
    nk, sk = simulate_fixed(trades)
    run("A_fixed1oz", nk, sk, "base")

    # E1: K_MAX 扫描
    for kmax in (1.00, 1.25, 1.50, 1.75, 2.00, 2.50, 3.00):
        nk, sk = simulate(trades, atr_values, ref_at, kmax=kmax)
        run(f"RLexp_k{kmax:.2f}", nk, sk, "E1_kmax")

    # E2: 回撤节流网格（K_MAX=2.0 固定）
    for theta in (0.06, 0.08, 0.10, 0.12, 0.14):
        for f in (0.50, 0.35):
            nk, sk = simulate(trades, atr_values, ref_at, kmax=2.00,
                              dd_theta=theta, dd_f=f)
            run(f"RLexp_k2.00_dd{int(round(theta*100))}_f{int(f*100)}", nk, sk, "E2_throttle")

    df = pd.DataFrame(rows)
    csv_path = os.path.join(OUT, "rlfv_results.csv")
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"[out] {csv_path}")

    # IS 选参 → OOS 报告
    e1 = df[df.group == "E1_kmax"]
    e2 = df[df.group == "E2_throttle"]
    e1_pick = e1.loc[e1.IS_calmar.idxmax()]
    e2_pick = e2.loc[e2.IS_calmar.idxmax()]
    print(f"\n[IS 选参] E1 最佳 Calmar: {e1_pick['name']} -> OOS net={e1_pick['OOS_net']} "
          f"dd={e1_pick['OOS_dd']}% sharpe={e1_pick['OOS_sharpe']} calmar={e1_pick['OOS_calmar']}")
    print(f"[IS 选参] E2 最佳 Calmar: {e2_pick['name']} -> OOS net={e2_pick['OOS_net']} "
          f"dd={e2_pick['OOS_dd']}% sharpe={e2_pick['OOS_sharpe']} calmar={e2_pick['OOS_calmar']}")

    # 图
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(12, 6))
    pick = ["A_fixed1oz", "RLexp_k2.00", "RLexp_k1.50",
            e2_pick["name"], "RLexp_k2.00_dd6_f50"]
    colors = ["#888", "#1f77b4", "#2ca02c", "#9467bd", "#ff7f0e"]
    for name, col in zip(pick, colors):
        if name not in curves:
            continue
        vals = np.array([p["value"] for p in curves[name]], dtype=float)
        ax.plot([p["date"] for p in curves[name]], vals, lw=1.2,
                label=name, color=col)
    ax.axvline(oos_start_date, ls="--", color="#d62728", alpha=0.7)
    ax.text(oos_start_date, ax.get_ylim()[0], " OOS起点", color="#d62728", va="bottom")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.3)
    ax.set_title("RL 仓位前向验证：IS/OOS 权益曲线（虚线为切分点）")
    fig.tight_layout()
    png = os.path.join(OUT, "rlfv_equity.png")
    fig.savefig(png, dpi=130)
    plt.close(fig)
    print(f"[out] {png}")

    # 报告
    base_oos = df[df.name == "A_fixed1oz"].iloc[0]
    md = ["# RL 仓位层前向验证报告", "",
          f"> 日期: 2026-10-05 | A 组 {len(trades)} 笔 | IS 60% ({is_start_date}..{is_end_date}) "
          f"/ OOS 40% ({oos_start_date}..{oos_end_date}) | 日频 NAV 口径", "",
          "## 全部变体 IS/OOS 对照", "", df.to_markdown(index=False), "",
          "## IS 选参 → OOS 结果", "",
          f"- **E1 K_MAX**：IS 最佳 Calmar = `{e1_pick['name']}` "
          f"(IS calmar {e1_pick['IS_calmar']}) → OOS net {e1_pick['OOS_net']} / "
          f"dd {e1_pick['OOS_dd']}% / sharpe {e1_pick['OOS_sharpe']} / calmar {e1_pick['OOS_calmar']}",
          f"- **E2 节流**：IS 最佳 Calmar = `{e2_pick['name']}` "
          f"(IS calmar {e2_pick['IS_calmar']}) → OOS net {e2_pick['OOS_net']} / "
          f"dd {e2_pick['OOS_dd']}% / sharpe {e2_pick['OOS_sharpe']} / calmar {e2_pick['OOS_calmar']}",
          f"- **A 基线 OOS**：net {base_oos['OOS_net']} / dd {base_oos['OOS_dd']}% / "
          f"sharpe {base_oos['OOS_sharpe']} / calmar {base_oos['OOS_calmar']}", "",
          "## 判读要点", "",
          "1. 看 K_MAX 排序在 OOS 是否单调（净收益升、Sharpe 微降）—— 若是，则杠杆效应稳健；"
          "若 OOS 排序乱了，说明 K_MAX 选择是过拟合。",
          "2. IS 选的 θ 是否落在先前报告的 θ=6% 附近；以及该 θ 的 OOS Calmar 是否仍优于不节流的同 K_MAX。",
          "3. 切记 OOS 段是 2025 下半年~2026（高波动强年），所有杠杆变体都会被这段行情放大，"
          "不要把 OOS 绝对收益当未来预期。"]
    rp = os.path.join(OUT, "rlfv_report.md")
    with open(rp, "w", encoding="utf-8") as fp:
        fp.write("\n".join(md))
    print(f"[out] {rp}")


if __name__ == "__main__":
    main()
