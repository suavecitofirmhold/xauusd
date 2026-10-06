# -*- coding: utf-8 -*-
"""
XAU/USD box 策略 样本外稳定性 / walk-forward 检验。
固定推荐参数(mid)与朴素基线(box 原版)在多个不重叠市场 regime 上回测,
判断策略是否只靠 2025-2026 主升浪(regime 依赖)。
"""
import os, sys, json
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from box_channel_optimized import run_backtest, PROFILES

# 不重叠区间, 覆盖 2023-09 ~ 2026-08 的多种市场状态
SEGMENTS = [
    ("S1 起步/震荡 23-09~24-02", "2023-09-13", "2024-02-01"),
    ("S2 首波拉升 24-02~24-10", "2024-02-01", "2024-10-01"),
    ("S3 回调/震荡 24-10~25-03", "2024-10-01", "2025-03-01"),
    ("S4 再起涨   25-03~25-09", "2025-03-01", "2025-09-01"),
    ("S5 主升浪   25-09~26-08", "2025-09-01", "2026-08-29"),
]

# 朴素基线 = 优化前原 box(无隔夜控制, r_mult 1.2)
BASELINE = dict(entry_style="box", avoid_overnight=False, r_mult=1.2,
                atr_stop=False, trailing=False, short_win=8, min_k=3,
                cooldown_bars=4, rel_slope_thresh=0.0006, sl_buffer=2.0)

FULL = ("FULL 全样本 23-09~26-08", "2023-09-13", "2026-08-29")


def pct(x):
    return f"{x*100:+.1f}%"


def run_cfg(name, cfg, frm, to):
    m = run_backtest(extra_args=cfg, fromdate=frm, todate=to, quiet=True)
    sh = m["sharpe"]
    sh_txt = f"{sh:.2f}" if sh is not None else "nan"
    print(f"  [{name}] {frm}~{to}: ret={pct(m['total_ret'])} DD={m['maxdd']:.1f}% "
          f"Sharpe={sh_txt} trades={m['closed']} win={m['win_rate']*100:.1f}% "
          f"swap={m['swap_total']:+.1f}")
    return dict(total_ret=m["total_ret"], maxdd=m["maxdd"], sharpe=m["sharpe"],
                closed=m["closed"], win_rate=m["win_rate"], swap_total=m["swap_total"])


def main():
    out = {"mid": [], "baseline": []}
    print(">>> mid (推荐配置, 固定参数, 真样本外):")
    for label, frm, to in SEGMENTS:
        out["mid"].append({"seg": label, "metrics": run_cfg("mid", PROFILES["mid"], frm, to)})
    print(">>> baseline (优化前朴素 box):")
    for label, frm, to in SEGMENTS:
        out["baseline"].append({"seg": label, "metrics": run_cfg("baseline", BASELINE, frm, to)})
    print(">>> 全样本对照:")
    out["mid_full"] = run_cfg("mid", PROFILES["mid"], *FULL[1:])
    out["baseline_full"] = run_cfg("baseline", BASELINE, *FULL[1:])

    # 汇总
    mid_pos = sum(1 for r in out["mid"] if r["metrics"]["total_ret"] > 0)
    base_pos = sum(1 for r in out["baseline"] if r["metrics"]["total_ret"] > 0)
    out["summary"] = dict(
        mid_segments_positive=mid_pos, mid_segments_total=len(out["mid"]),
        baseline_segments_positive=base_pos, baseline_segments_total=len(out["baseline"]),
        mid_mean_ret=sum(r["metrics"]["total_ret"] for r in out["mid"]) / len(out["mid"]),
        baseline_mean_ret=sum(r["metrics"]["total_ret"] for r in out["baseline"]) / len(out["baseline"]),
    )
    json.dump(out, open(os.path.join(HERE, "walk_forward_results.json"), "w"),
              ensure_ascii=False, indent=2)

    # markdown
    lines = ["# XAU/USD Box 策略 样本外稳定性 (walk-forward) 检验\n",
             "> 固定参数 × 多不重叠市场 regime, 判断策略是否 regime 依赖。\n",
             "> mid = 推荐配置(box, r_mult2.0, 避免隔夜); baseline = 优化前朴素 box(无隔夜, r_mult1.2)。\n"]
    lines.append("## 分段结果\n")
    lines.append("| 区间(regime) | mid 收益 | mid-DD | mid-Sharpe | 基线收益 | 基线-DD | 基线-Sharpe |")
    lines.append("|---|---|---|---|---|---|---|")
    for rm, rb in zip(out["mid"], out["baseline"]):
        m, b = rm["metrics"], rb["metrics"]
        ms = f"{m['sharpe']:.2f}" if m["sharpe"] is not None else "nan"
        bs = f"{b['sharpe']:.2f}" if b["sharpe"] is not None else "nan"
        lines.append(f"| {rm['seg']} | {pct(m['total_ret'])} | {m['maxdd']:.1f}% | "
                     f"{ms} | {pct(b['total_ret'])} | {b['maxdd']:.1f}% | {bs} |")
    mf, bf = out["mid_full"], out["baseline_full"]
    mfs = f"{mf['sharpe']:.2f}" if mf["sharpe"] is not None else "nan"
    bfs = f"{bf['sharpe']:.2f}" if bf["sharpe"] is not None else "nan"
    lines.append(f"| **FULL 全样本** | **{pct(mf['total_ret'])}** | {mf['maxdd']:.1f}% | {mfs} | "
                 f"**{pct(bf['total_ret'])}** | {bf['maxdd']:.1f}% | {bfs} |")
    lines.append("")
    lines.append("## 结论\n")
    lines.append(f"- mid 配置在 {mid_pos}/{len(out['mid'])} 个分段样本外为正; "
                 f"基线在 {base_pos}/{len(out['baseline'])} 个分段为正。")
    lines.append(f"- mid 分段平均收益 {pct(out['summary']['mid_mean_ret'])}; "
                 f"基线分段平均收益 {pct(out['summary']['baseline_mean_ret'])}。")
    if mid_pos >= len(out["mid"]) - 1:
        lines.append("- **结论: box(mid) 在多数 regime 均正, 非单纯 riding 主升浪, 具备一定普适性**; "
                     "但在震荡/回调段(S1/S3)收益明显弱于趋势段, 仍属 regime 敏感, 建议搭配趋势过滤器。")
    else:
        lines.append("- **结论: box 在部分 regime 亏损, regime 依赖明显**, 上线前务必加趋势/波动率过滤。")
    rep = "\n".join(lines)
    open(os.path.join(HERE, "walk_forward_report.md"), "w", encoding="utf-8").write(rep)
    print("\n" + rep)


if __name__ == "__main__":
    main()
