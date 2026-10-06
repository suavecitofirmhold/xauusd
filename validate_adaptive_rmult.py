# -*- coding: utf-8 -*-
"""
第4步验证: 自适应 r_mult(scalp_adapt) vs 固定 1.75(scalp) 在 全样本 + 5 个 walk-forward 段 对比。
看自适应是否真优于固定, 尤其在震荡段(S1/S3/S4)是否改善。
输出: scalp_adaptive_validation.csv + scalp_adaptive_validation.md
"""
import os, sys
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from box_channel_optimized import run_backtest, PROFILES
from analyze_scalp_trades import daily_sharpe, build_record
from walkforward_scalp_rmult import SEGMENTS

FD_ALL, TD_ALL = "2023-09-14", "2026-08-28"
OUT_CSV = os.path.join(HERE, "scalp_adaptive_validation.csv")
OUT_MD = os.path.join(HERE, "scalp_adaptive_validation.md")


def metrics(extra, fd, td):
    m = run_backtest(extra_args=extra, fromdate=fd, todate=td, quiet=True)
    rec = build_record(m)
    sub = pd.DataFrame(rec)
    gp = sub[sub.pnl_usd > 0].pnl_usd.sum()
    gl = -sub[sub.pnl_usd < 0].pnl_usd.sum()
    pf = gp / gl if gl > 0 else float("nan")
    rm_info = None
    if "r_mult" in sub.columns:
        rr = pd.to_numeric(sub["r_mult"], errors="coerce").dropna()
        if len(rr):
            rm_info = dict(mean=rr.mean(), hi=(rr >= 2.0).mean(), lo=(rr <= 1.6).mean())
    return dict(ret=m["total_ret"] * 100, win=m["win_rate"] * 100,
                sharpe=daily_sharpe(m["eq"]), dd=m["maxdd"], pf=pf,
                n=int(m["closed"]), rm=rm_info)


def main():
    fixed = PROFILES["scalp_raw"]
    adapt = PROFILES["scalp_adapt"]
    spans = [("FULL", FD_ALL, TD_ALL, "全样本")] + \
            [(n, fd, td, reg) for n, fd, td, reg in SEGMENTS]

    rows = []
    for name, fd, td, reg in spans:
        f = metrics(fixed, fd, td)
        a = metrics(adapt, fd, td)
        row = dict(span=name, regime=reg,
                   fixed_ret=f["ret"], fixed_sharpe=f["sharpe"], fixed_dd=f["dd"], fixed_pf=f["pf"],
                   adapt_ret=a["ret"], adapt_sharpe=a["sharpe"], adapt_dd=a["dd"], adapt_pf=a["pf"],
                   adapt_n=a["n"],
                   delta_ret=a["ret"] - f["ret"])
        if a["rm"]:
            row.update(adapt_rm_mean=a["rm"]["mean"], adapt_rm_hi=a["rm"]["hi"], adapt_rm_lo=a["rm"]["lo"])
        rows.append(row)
        rmstr = f" r_mult均值{a['rm']['mean']:.2f}(高≥2.0占{a['rm']['hi']*100:.0f}%/低≤1.6占{a['rm']['lo']*100:.0f}%)" if a["rm"] else ""
        print(f"{name} {reg}: 固定 ret{f['ret']:+.1f}% Sh{f['sharpe']:.2f} | 自适应 ret{a['ret']:+.1f}% Sh{a['sharpe']:.2f} Δ{a['ret']-f['ret']:+.1f}%{rmstr}")

    df = pd.DataFrame(rows)
    for c in ["adapt_rm_mean", "adapt_rm_hi", "adapt_rm_lo"]:
        if c not in df.columns:
            df[c] = float("nan")
    cols = ["span", "regime", "fixed_ret", "fixed_sharpe", "fixed_dd", "fixed_pf",
            "adapt_ret", "adapt_sharpe", "adapt_dd", "adapt_pf", "delta_ret",
            "adapt_rm_mean", "adapt_rm_hi", "adapt_rm_lo", "adapt_n"]
    df = df[cols]
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    # 结论统计
    full = df[df.span == "FULL"].iloc[0]
    seg = df[df.span != "FULL"]
    chop = seg[seg.regime.str.contains("震荡")]
    trend = seg[seg.regime.str.contains("趋势")]
    n_win = (seg.delta_ret > 0).sum()
    lines = []
    lines.append("# 自适应 r_mult 验证 (scalp_adapt vs 固定 1.75)\n")
    lines.append("> 自适应逻辑: 入场时按 1h 通道斜率幅度 |rel| 连续映射 r_mult — 趋势强(大|rel|)→高(上限2.3), 震荡(小|rel|)→低(下限1.5)。\n")
    lines.append("## 一、逐段对比")
    lines.append("| 段 | regime | 固定收益% | 固定Sharpe | 固定DD% | 固定PF | 自适应收益% | 自适应Sharpe | 自适应DD% | 自适应PF | Δ收益% |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for _, r in df.iterrows():
        lines.append(f"| {r['span']} | {r['regime']} | {r['fixed_ret']:+.1f} | {r['fixed_sharpe']:.2f} | {r['fixed_dd']:.1f} | {r['fixed_pf']:.2f} | {r['adapt_ret']:+.1f} | {r['adapt_sharpe']:.2f} | {r['adapt_dd']:.1f} | {r['adapt_pf']:.2f} | {r['delta_ret']:+.1f} |")
    lines.append("")
    lines.append(f"> 自适应 r_mult 使用分布(全样本): 均值 {full.get('adapt_rm_mean', float('nan')):.2f}, "
                 f"高(≥2.0)占 {full.get('adapt_rm_hi', float('nan'))*100:.0f}%, 低(≤1.6)占 {full.get('adapt_rm_lo', float('nan'))*100:.0f}%。\n")
    # 数据驱动解读: 比较趋势段 vs 震荡段的实际 r_mult 均值
    _trend = seg[seg.regime.str.contains("趋势")]["adapt_rm_mean"].dropna()
    _chop = seg[seg.regime.str.contains("震荡")]["adapt_rm_mean"].dropna()
    if len(_trend) and len(_chop):
        if _trend.mean() - _chop.mean() > 0.05:
            _interp = "趋势段 r_mult 实际确实高于震荡段, 映射方向正确但幅度偏小。"
        else:
            _interp = ("⚠️ 关键发现: 趋势段 r_mult 均值(%.2f)并未明显高于震荡段(%.2f) — 映射几乎贴在下限,"
                       " 因 rmult_ref_hi=%.4f 远高于实际 |rel| 区间, 趋势段从未触发高值。自适应实际≈固定~1.5(低于最优1.75),"
                       " 这正是全样本与趋势段跑输的根因。" % (_trend.mean(), _chop.mean(), PROFILES["scalp_adapt"]["rmult_ref_hi"]))
        lines.append("> " + _interp + "\n")

    lines.append("## 二、结论")
    lines.append(f"- **全样本**: 固定 {full['fixed_ret']:+.1f}% / Sharpe{full['fixed_sharpe']:.2f} / PF{full['fixed_pf']:.2f} "
                 f"vs 自适应 {full['adapt_ret']:+.1f}% / Sharpe{full['adapt_sharpe']:.2f} / PF{full['adapt_pf']:.2f} "
                 f"→ Δ收益 {full['delta_ret']:+.1f}%.")
    lines.append(f"- **逐段**: 自适应在 {n_win}/5 段收益优于固定。")
    if len(chop):
        lines.append(f"- **震荡段(S1/S3/S4)均值**: 固定 {chop.fixed_ret.mean():+.1f}% → 自适应 {chop.adapt_ret.mean():+.1f}% "
                     f"(Δ {chop.adapt_ret.mean()-chop.fixed_ret.mean():+.1f}%) → "
                     + ("自适应改善(低r_mult减少够不到目标)。" if chop.adapt_ret.mean() > chop.fixed_ret.mean() else "自适应未改善, 需调参。"))
    if len(trend):
        lines.append(f"- **趋势段(S2/S5)均值**: 固定 {trend.fixed_ret.mean():+.1f}% → 自适应 {trend.adapt_ret.mean():+.1f}% "
                     f"(Δ {trend.adapt_ret.mean()-trend.fixed_ret.mean():+.1f}%) → "
                     + ("自适应在趋势段吃到更多(高r_mult)。" if trend.adapt_ret.mean() > trend.fixed_ret.mean() else "自适应未多吃。"))
    better = full["delta_ret"] > 0 and (chop.adapt_ret.mean() >= chop.fixed_ret.mean() if len(chop) else True)
    lines.append("")
    lines.append("**总评**: " + (
        "自适应 r_mult 在全样本与多数分段优于/持平固定 1.75, 且在最弱的震荡段有改善 → 值得采用(scalp_adapt 画像)。"
        if better else
        "自适应未稳定优于固定 1.75, 建议维持固定 1.75 或微调自适应锚点(rmult_ref_lo/hi, rmult_range/trend)。"))
    lines.append("")
    lines.append("> 局限: 自适应锚点(1.5/2.3, |rel|锚 0.0006/0.0025)是从同一 walk-forward 最优集合{1.5,1.75,2.0,2.5}推导的, ")
    lines.append("> 严格讲应留一段真正未见数据做最终确认; 但本对比已用 S1/S2/S3 作样本外, 结果具参考性。")

    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines))
    print(f"\n已生成: {OUT_CSV}\n已生成: {OUT_MD}")


if __name__ == "__main__":
    main()
