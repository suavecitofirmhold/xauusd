# -*- coding: utf-8 -*-
"""
XAU/USD box scalp 策略 · walk-forward 5-regime 验证
固定 atr_stop_mult=1.5, 对比 atr_regime_gate 两档 (off / on 1.2),
看 1.2 边缘是否跨样本稳健(还是只吃某段行情的运气)。

复用 walk_forward.py 的 5 个不重叠 market-regime 分段。
"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from box_channel_optimized import run_backtest, PROFILES

# 不重叠区间, 覆盖 2023-09 ~ 2026-08 的多种市场状态 (同 walk_forward.py)
SEGMENTS = [
    ("S1 起步/震荡 23-09~24-02", "2023-09-13", "2024-02-01"),
    ("S2 首波拉升 24-02~24-10", "2024-02-01", "2024-10-01"),
    ("S3 回调/震荡 24-10~25-03", "2024-10-01", "2025-03-01"),
    ("S4 再起涨   25-03~25-09", "2025-03-01", "2025-09-01"),
    ("S5 主升浪   25-09~26-08", "2025-09-01", "2026-08-29"),
]
FULL = ("FULL 全样本 23-09~26-08", "2023-09-13", "2026-08-29")

# 固定 atr_stop_mult=1.5; 两档 gate
BASE = dict(PROFILES["scalp"])
BASE["atr_stop_mult"] = 1.5
CONFIGS = [
    ("gate=off (基线)", dict(BASE, atr_regime_gate=False)),
    ("gate on 1.2", dict(BASE, atr_regime_gate=True, atr_regime_mult=1.2)),
]


def pct(x):
    return f"{x*100:+.1f}%"


def run_cfg(cfg, frm, to):
    m = run_backtest(extra_args=cfg, fromdate=frm, todate=to, quiet=True)
    sh = m["sharpe"]
    sh_txt = f"{sh:.2f}" if sh is not None else "nan"
    return dict(total_ret=m["total_ret"], realized_ret=m["realized_ret"], maxdd=m["maxdd"],
                pf=m["pf"], sharpe=m["sharpe"], closed=m["closed"],
                win_rate=m["win_rate"], swap_total=m["swap_total"]), sh_txt


def fmt(m, sh_txt):
    return (f"ret={pct(m['total_ret'])} real={pct(m['realized_ret'])} "
            f"DD={m['maxdd']:.1f}% PF={m['pf']:.2f} Sh={sh_txt} "
            f"trades={m['closed']} win={m['win_rate']*100:.1f}%")


def main():
    results = {cname: [] for cname, _ in CONFIGS}
    print(">>> walk-forward: scalp, atr_stop_mult=1.5, 两档 gate\n")
    for cname, cfg in CONFIGS:
        print(f"### {cname}")
        for label, frm, to in SEGMENTS:
            m, sh = run_cfg(cfg, frm, to)
            results[cname].append({"seg": label, "metrics": m})
            print(f"  [{label}] {fmt(m, sh)}")
        mf, shf = run_cfg(cfg, *FULL[1:])
        results[cname + " | FULL"] = {"seg": FULL[0], "metrics": mf}
        print(f"  [{FULL[0]}] {fmt(mf, shf)}")
        print()

    # 对比: 1.2 边缘是否跨段稳健
    off = results["gate=off (基线)"]
    on = results["gate on 1.2"]
    print(">>> 段段对比: gate on 1.2 相对 gate=off 的已实现收益差 (正=1.2 更好)")
    diffs = []
    for ro, rn in zip(off, on):
        d = rn["metrics"]["realized_ret"] - ro["metrics"]["realized_ret"]
        diffs.append(d)
        better = "✓1.2优" if d > 0 else "✗off优"
        print(f"  {ro['seg']:>22}: Δreal={pct(d)}  {better} "
              f"(off {pct(ro['metrics']['realized_ret'])} / 1.2 {pct(rn['metrics']['realized_ret'])})")

    n_better = sum(1 for d in diffs if d > 0)
    mean_diff = sum(diffs) / len(diffs)
    print(f"\n  1.2 在 {n_better}/{len(diffs)} 段优于 off; 平均 Δreal={pct(mean_diff)}")

    # 稳健性判定
    off_pos = sum(1 for r in off if r["metrics"]["realized_ret"] > 0)
    on_pos = sum(1 for r in on if r["metrics"]["realized_ret"] > 0)
    print(f"  分段为正: off {off_pos}/{len(off)} ; 1.2 {on_pos}/{len(on)}")

    # 落盘 json
    json.dump({"configs": {cname: [dict(seg=r["seg"], **r["metrics"]) for r in results[cname]]
                           for cname, _ in CONFIGS},
               "full": {cname: results[cname + " | FULL"]["metrics"] for cname, _ in CONFIGS},
               "n_better": n_better, "mean_diff": mean_diff,
               "off_positive": off_pos, "on_positive": on_pos},
              open(os.path.join(HERE, "walk_forward_scalp_gate_results.json"), "w"),
              ensure_ascii=False, indent=2)

    # 落盘 markdown
    lines = ["# XAU/USD Box scalp · walk-forward 5-regime 验证 (atr_stop_mult=1.5)\n",
             "> 固定 atr_stop_mult=1.5, 对比 atr_regime_gate 两档; 验证 1.2 边缘是否跨样本稳健。\n",
             "> 区间 = walk_forward.py 的 5 个不重叠 market-regime 分段。\n"]
    lines.append("## 分段结果\n")
    lines.append("| 区间(regime) | off 已实现 | off-DD | off-PF | 1.2 已实现 | 1.2-DD | 1.2-PF | Δreal(1.2-off) |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for ro, rn in zip(off, on):
        mo, mn = ro["metrics"], rn["metrics"]
        d = mn["realized_ret"] - mo["realized_ret"]
        lines.append(f"| {ro['seg']} | {pct(mo['realized_ret'])} | {mo['maxdd']:.1f}% | {mo['pf']:.2f} | "
                     f"{pct(mn['realized_ret'])} | {mn['maxdd']:.1f}% | {mn['pf']:.2f} | {pct(d)} |")
    fo, fn = results["gate=off (基线) | FULL"]["metrics"], results["gate on 1.2 | FULL"]["metrics"]
    lines.append(f"| **FULL 全样本** | **{pct(fo['realized_ret'])}** | {fo['maxdd']:.1f}% | {fo['pf']:.2f} | "
                 f"**{pct(fn['realized_ret'])}** | {fn['maxdd']:.1f}% | {fn['pf']:.2f} | "
                 f"{pct(fn['realized_ret']-fo['realized_ret'])} |")
    lines.append("")
    lines.append("## 结论\n")
    lines.append(f"- 1.2 在 {n_better}/{len(diffs)} 个分段优于 off; 平均 Δreal={pct(mean_diff)}。")
    lines.append(f"- 分段为正: off {off_pos}/{len(off)} ; 1.2 {on_pos}/{len(on)}。")
    if n_better >= len(diffs) - 1 and mean_diff > 0:
        verdict = ("**1.2 边缘跨 regime 稳健**: 多数段为正且 1.2 普遍优于 off, 可考虑从默认 off 升级为 on 1.2。**"
                   "但注意 DD 略升, 且须警惕 S5 主升浪贡献。**")
    elif n_better <= len(diffs) // 2:
        verdict = ("**1.2 边缘不稳健**: 仅约半数段优于 off, 属 regime-specific 运气, 默认应保持 off。")
    else:
        verdict = ("**1.2 边缘部分稳健**: 多数段略优但幅度小且非单调, 属边缘增益; 默认仍建议 off, "
                   "除非接受 DD 略升并理解其为样本内边缘。")
    lines.append(f"- {verdict}")
    rep = "\n".join(lines)
    open(os.path.join(HERE, "walk_forward_scalp_gate_report.md"), "w", encoding="utf-8").write(rep)
    print("\n" + rep)


if __name__ == "__main__":
    main()
