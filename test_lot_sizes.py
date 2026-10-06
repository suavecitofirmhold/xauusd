# -*- coding: utf-8 -*-
"""
实测不同手数(lot)下的 收益 / 回撤 / 爆仓风险。

背景: 当前 scalp(已采用 ma96) 用 lot=0.01(1盎司) + 1:1000 杠杆 + $1000 本金。
手数放大 N 倍 → 盈亏/回撤的"美元金额"放大 N 倍, 但本金不变, 故百分比回撤也约放大 N 倍,
接近或超过 100% 即触发 margin call(强平) 甚至账户归零。

统计口径:
  - 收益/回撤/Sharpe 沿用可靠口径(Sharpe 由资金曲线日收益年化)
  - 爆仓风险 = margin 强平次数 + 最低权益 + 最大美元回撤 + 是否曾触及接近归零

输出: lot_size_risk.csv + lot_size_risk.md
"""
import os, sys, argparse
import numpy as np
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from box_channel_optimized import run_backtest, PROFILES
from analyze_scalp_trades import daily_sharpe

DEF_LOTS = [0.01, 0.02, 0.05, 0.1]
WINDOWS = [("TEST", "2025-06-01", "2026-08-29"),
           ("FULL", "2023-09-14", "2026-08-28")]


def run_one(lot, fd, td, cash):
    m = run_backtest(extra_args=PROFILES["scalp"], fromdate=fd, todate=td,
                     quiet=True, lot=lot, cash=cash)
    # 爆仓/强平统计
    margin_calls = sum(1 for t in m["trades"]
                       if t["kind"] == "close" and t.get("reason") == "margin")
    # 单笔风险(美元) = |entry - stop| × 盎司数；盎司 = lot × contract_oz(100)
    oz = lot * 100.0
    risks = [abs(t["price"] - t["stop"]) * oz
             for t in m["trades"] if t["kind"] == "open" and t.get("stop") is not None]
    risk_avg = float(np.mean(risks)) if risks else float("nan")
    risk_max = float(np.max(risks)) if risks else float("nan")
    risk_last = float(risks[-1]) if risks else float("nan")  # 末期一笔(当前金价下的风险)
    # 权益轨迹: 最低点 / 最大美元回撤
    eq = m["eq"]
    peak, maxdd_usd, min_eq = -1e18, 0.0, 1e18
    for _, v in eq:
        peak = max(peak, v)
        maxdd_usd = max(maxdd_usd, peak - v)
        min_eq = min(min_eq, v)
    return dict(lot=lot, ret=m["total_ret"] * 100, ann=m["ann"] * 100,
                dd=m["maxdd"], sharpe=daily_sharpe(eq),
                closed=m["closed"], win=m["win_rate"] * 100,
                swap=m["swap_total"], margin_calls=margin_calls,
                final=m["final"], min_eq=min_eq, maxdd_usd=maxdd_usd,
                cash=cash, risk_avg=risk_avg, risk_max=risk_max, risk_last=risk_last,
                risk_avg_pct=risk_avg / cash * 100,
                risk_max_pct=risk_max / cash * 100,
                risk_last_pct=risk_last / cash * 100)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cash", type=float, default=1000.0, help="账户本金(美元)")
    ap.add_argument("--lots", type=float, nargs="+", default=DEF_LOTS,
                    help="要测的手数列表")
    ap.add_argument("--suffix", default="", help="输出文件名后缀")
    args = ap.parse_args()

    cash = args.cash
    lots = args.lots
    suffix = args.suffix or ("" if abs(cash - 1000.0) < 1e-9 else f"_{int(cash)}")
    out_csv = os.path.join(HERE, f"lot_size_risk{suffix}.csv")
    out_md = os.path.join(HERE, f"lot_size_risk{suffix}.md")

    rows = []
    for wname, fd, td in WINDOWS:
        for lot in lots:
            r = run_one(lot, fd, td, cash)
            r["window"] = wname
            rows.append(r)
            print(f"{wname} lot={lot:<5}: ret{r['ret']:+8.1f}% 年化{r['ann']:+7.1f}% "
                  f"DD{r['dd']:6.1f}% Sh{r['sharpe']:.2f} 笔数{int(r['closed']):4d} "
                  f"强平{int(r['margin_calls']):3d} 最低权益${r['min_eq']:8.2f} "
                  f"最终${r['final']:9.2f} | 单笔风险 均${r['risk_avg']:.2f}"
                  f"(本金{r['risk_avg_pct']:.1f}%) 末笔${r['risk_last']:.2f}"
                  f"(本金{r['risk_last_pct']:.1f}%)")

    df = pd.DataFrame(rows)
    cols = ["window", "lot", "cash", "ret", "ann", "dd", "sharpe", "closed", "win",
            "swap", "margin_calls", "final", "min_eq", "maxdd_usd",
            "risk_avg", "risk_avg_pct", "risk_max", "risk_max_pct",
            "risk_last", "risk_last_pct"]
    df = df[cols]
    df.to_csv(out_csv, index=False, encoding="utf-8-sig")

    lines = []
    lines.append("# 手数(lot)风险实测 — 收益 / 回撤 / 爆仓风险\n")
    lines.append(f"> 账户: **${cash:.0f} 本金** ｜ 杠杆 1:1000 ｜ 画像: scalp(已采用 ma96) ｜ 手数 0.01 手 = 1 盎司\n")
    lines.append("> 固定手数模式下, 手数放大 N 倍 → 每笔盈亏与回撤的**美元金额**放大 N 倍; 本金不变,"
                 "故百分比回撤也近似放大 N 倍, 超过 100% 即账户归零。\n")

    for wname, fd, td in WINDOWS:
        sub = df[df.window == wname]
        lines.append(f"\n## {wname} 窗口 ({fd} ~ {td})")
        lines.append("| 手数 | 盎司 | 累计收益% | 年化% | 最大回撤% | Sharpe | 成交 | 胜率% | 隔夜费 | **强平次数** | 最低权益$ | 最大回撤$ | 最终权益$ | **单笔风险$(均)** | **占本金%** | 末期单笔风险$ | 占本金% |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for _, r in sub.iterrows():
            oz = r["lot"] * 100
            lines.append(f"| {r['lot']} | {oz:.0f} | {r['ret']:+.1f} | {r['ann']:+.1f} | {r['dd']:.1f} | "
                         f"{r['sharpe']:.2f} | {int(r['closed'])} | {r['win']:.1f} | {r['swap']:+.1f} | "
                         f"**{int(r['margin_calls'])}** | {r['min_eq']:.2f} | {r['maxdd_usd']:.1f} | {r['final']:.2f} | "
                         f"{r['risk_avg']:.2f} | **{r['risk_avg_pct']:.1f}%** | {r['risk_last']:.2f} | {r['risk_last_pct']:.1f}% |")

    lines.append("\n## 结论与手数建议")
    for wname, _, _ in WINDOWS:
        sub = df[df.window == wname].sort_values("lot")
        base = sub[sub.lot == 0.01].iloc[0]
        lines.append(f"\n### {wname}")
        for _, r in sub.iterrows():
            if r["lot"] == 0.01:
                continue
            mult = r["lot"] / 0.01
            lines.append(f"- **lot={r['lot']} ({r['lot']*100:.0f}盎司, ×{mult:.0f})**: 收益 {base['ret']:+.1f}% → "
                         f"{r['ret']:+.1f}%, 回撤 {base['dd']:.1f}% → {r['dd']:.1f}%, "
                         f"Sharpe {base['sharpe']:.2f} → {r['sharpe']:.2f}, "
                         f"强平 {int(r['margin_calls'])} 次, 最低权益 ${r['min_eq']:.2f}")
    # 资金充足性评估(以最小手与末期风险=当前金价下的风险为准)
    lines.append("\n## 资金充足性评估")
    sub_t = df[df.window == "TEST"].sort_values("lot")
    if len(sub_t):
        r0 = sub_t.iloc[0]
        rl, pct = r0["risk_last"], r0["risk_last_pct"]
        lines.append(f"- 最小手 **{r0['lot']}**({r0['lot']*100:.0f}盎司) 在**末期/当前金价**下, 单笔风险 = "
                     f"**${rl:.2f}**, 占 ${cash:.0f} 本金的 **{pct:.1f}%**")
        verdict = ("在可接受范围 ✓" if pct <= 2 else
                   f"是上限的 **{pct/2:.1f}~{pct:.0f} 倍**, 属**资金不足 / 激进** ⚠️")
        lines.append(f"- 审慎资金管理要求单笔风险 ≤ **1~2%**。当前 {pct:.1f}% {verdict}")
        lines.append(f"- 若要把单笔风险压到 **1%**, 需本金约 **${rl/0.01:.0f}**; 压到 **2%** 需约 **${rl/0.02:.0f}**"
                     f"(在 {r0['lot']} 手、当前金价下)")
        lines.append(f"- 因最小手数即为 {r0['lot']}(无法再缩小仓位), **降风险的唯一办法是提高本金**, "
                     f"或改用支持更小手数(如 0.001)的账户类型。")

    lines.append("")
    lines.append("> 判读: ① 回撤% 随手数近似线性放大, 越接近 100% 越危险; "
                 "② 强平次数 >0 说明已触及保证金线; ③ 最低权益接近 0 即近乎爆仓; "
                 "④ Sharpe 在手数放大时**不应变化**(纯线性缩放), 若明显下降说明非线性效应(强平/保证金约束/负权益)已介入。")
    lines.append("")
    lines.append("> 注: 本模型 1:1000 杠杆下保证金占用极小($3~46), 故订单基本不会被拒; "
                 "真正约束来自 `_margin_call`(净值 < 已用保证金×0.5 强平), 触发时权益已所剩无几。")

    open(out_md, "w", encoding="utf-8").write("\n".join(lines))
    print(f"\n已生成: {out_csv}\n已生成: {out_md}")


if __name__ == "__main__":
    main()
