# -*- coding: utf-8 -*-
"""
第5步验证: 趋势过滤器(价格>EMA 方向门 ma_filter / 波动率regime门 atr_regime_gate)
专攻最弱段 S3(回调震荡), 看是否在不破坏 S5(主升浪)与 FULL 前提下改善 S3 的风险调整收益。

对比: 固定 scalp(无过滤) vs 各过滤器配置, 在 FULL + 5 个 walk-forward 段。
输出: scalp_trendfilter_sweep.csv + scalp_trendfilter_report.md
"""
import os, sys
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from box_channel_optimized import run_backtest, PROFILES
from analyze_scalp_trades import daily_sharpe, build_record
from walkforward_scalp_rmult import SEGMENTS

FD_ALL, TD_ALL = "2023-09-14", "2026-08-28"
OUT_CSV = os.path.join(HERE, "scalp_trendfilter_sweep.csv")
OUT_MD = os.path.join(HERE, "scalp_trendfilter_report.md")

# 过滤器配置候选
CONFIGS = [
    ("scalp(无过滤)", dict(PROFILES["scalp_raw"])),
    ("ma48",  {**PROFILES["scalp"], "ma_filter": True, "ma_filter_period": 48}),
    ("ma96",  {**PROFILES["scalp"], "ma_filter": True, "ma_filter_period": 96}),
    ("ma144", {**PROFILES["scalp"], "ma_filter": True, "ma_filter_period": 144}),
    ("ma200", {**PROFILES["scalp"], "ma_filter": True, "ma_filter_period": 200}),
    ("atr_gate", dict(PROFILES["scalp_tf_atr"])),
]
SPANS = [("FULL", FD_ALL, TD_ALL, "全样本")] + [(n, fd, td, reg) for n, fd, td, reg in SEGMENTS]


def metrics(extra, fd, td):
    m = run_backtest(extra_args=extra, fromdate=fd, todate=td, quiet=True)
    rec = build_record(m)
    sub = pd.DataFrame(rec)
    gp = sub[sub.pnl_usd > 0].pnl_usd.sum()
    gl = -sub[sub.pnl_usd < 0].pnl_usd.sum()
    pf = gp / gl if gl > 0 else float("nan")
    return dict(ret=m["total_ret"] * 100, win=m["win_rate"] * 100,
                sharpe=daily_sharpe(m["eq"]), dd=m["maxdd"], pf=pf, n=int(m["closed"]))


def main():
    rows = []
    base = {}  # span -> baseline metrics
    for cname, cfg in CONFIGS:
        for name, fd, td, reg in SPANS:
            r = metrics(cfg, fd, td)
            if cname.startswith("scalp(无过滤)"):
                base[name] = r
            rows.append(dict(config=cname, span=name, regime=reg,
                             ret=r["ret"], sharpe=r["sharpe"], dd=r["dd"],
                             pf=r["pf"], n=r["n"],
                             dret=r["ret"] - base.get(name, {}).get("ret", float("nan")),
                             dsh=r["sharpe"] - base.get(name, {}).get("sharpe", float("nan"))))
            print(f"{cname:14s} {name:4s} {reg:10s}: ret{r['ret']:+.1f}% Sh{r['sharpe']:.2f} "
                  f"DD{r['dd']:.1f}% PF{r['pf']:.2f} n={r['n']}")

    df = pd.DataFrame(rows)
    cols = ["config", "span", "regime", "ret", "sharpe", "dd", "pf", "n", "dret", "dsh"]
    df = df[cols]
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    # 报告: 以 S3 / S5 / FULL 为焦点
    lines = []
    lines.append("# 趋势过滤器验证 (价格>EMA 门 / 波动率regime门)\n")
    lines.append("> 目标: 专攻最弱段 S3(回调震荡), 在不破坏 S5(主升浪)与 FULL 前提下改善 S3 风险调整收益。\n")
    lines.append("> 机制: ma_filter 要求价格位于 EMA(period) favorable 侧才入场(多头需 price>EMA, 空头需 price<EMA); "
                 "atr_regime_gate 在 ATR/其均值 比值过高(高波动chop)时跳过。\n")

    # 每个配置一张表
    lines.append("## 一、逐配置 × 逐段")
    for cname, _ in CONFIGS:
        sub = df[df.config == cname]
        lines.append(f"\n### {cname}")
        lines.append("| 段 | regime | 收益% | Sharpe | DD% | PF | 笔数 | Δ收益%(vs无过滤) | ΔSharpe |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for _, r in sub.iterrows():
            dn = "" if (pd.isna(r["dret"])) else f"{r['dret']:+.1f}"
            dsh = "" if (pd.isna(r["dsh"])) else f"{r['dsh']:+.2f}"
            lines.append(f"| {r['span']} | {r['regime']} | {r['ret']:+.1f} | {r['sharpe']:.2f} | "
                         f"{r['dd']:.1f} | {r['pf']:.2f} | {int(r['n'])} | {dn} | {dsh} |")

    # 焦点段横向对比(各过滤器在 S3/S5/FULL 的 Δ收益)
    lines.append("\n## 二、焦点段横向对比 (Δ收益% vs 无过滤)")
    lines.append("| 配置 | S3(回调震荡) | S5(主升浪) | FULL | S3 PF | S5 PF | FULL PF |")
    lines.append("|---|---|---|---|---|---|---|")
    pivot = df.pivot_table(index="config", columns="span", values="dret")
    pivotpf = df.pivot_table(index="config", columns="span", values="pf")
    for cname, _ in CONFIGS:
        s3 = pivot.loc[cname, "S3"] if "S3" in pivot.columns else float("nan")
        s5 = pivot.loc[cname, "S5"] if "S5" in pivot.columns else float("nan")
        fl = pivot.loc[cname, "FULL"] if "FULL" in pivot.columns else float("nan")
        s3p = pivotpf.loc[cname, "S3"] if "S3" in pivotpf.columns else float("nan")
        s5p = pivotpf.loc[cname, "S5"] if "S5" in pivotpf.columns else float("nan")
        flp = pivotpf.loc[cname, "FULL"] if "FULL" in pivotpf.columns else float("nan")
        lines.append(f"| {cname} | {s3:+.1f} | {s5:+.1f} | {fl:+.1f} | {s3p:.2f} | {s5p:.2f} | {flp:.2f} |")

    # 结论
    lines.append("\n## 三、结论")
    # 找 S3 改善且 S5/FULL 不恶化的配置
    best = None
    for cname, _ in CONFIGS:
        if cname.startswith("scalp(无过滤)"):
            continue
        s3 = pivot.loc[cname, "S3"]
        s5 = pivot.loc[cname, "S5"]
        fl = pivot.loc[cname, "FULL"]
        s3p = pivotpf.loc[cname, "S3"]
        # 标准: S3 收益或 PF 改善, 且 S5/FULL 不显著恶化(Δret 不低于 -3)
        if (s3 > 0 or s3p > base["S3"]["pf"]) and fl >= -3.0 and s5 >= -3.0:
            score = s3 + fl * 0.2  # 偏重 S3 改善 + FULL 稳定
            if best is None or score > best[1]:
                best = (cname, score, s3, s5, fl, s3p, s5p, flp)
    if best:
        lines.append(f"- **推荐**: `{best[0]}` — S3 Δ收益 {best[2]:+.1f}% (PF {best[5]:.2f} vs 无过滤 {base['S3']['pf']:.2f}), "
                     f"S5 Δ收益 {best[3]:+.1f}%, FULL Δ收益 {best[4]:+.1f}% (PF {best[7]:.2f}). "
                     f"在改善最弱段的同时未显著破坏趋势段与全样本。")
        lines.append(f"- 基线 S3: 收益 {base['S3']['ret']:+.1f}% / Sharpe {base['S3']['sharpe']:.2f} / PF {base['S3']['pf']:.2f} / 笔数 {int(base['S3']['n'])}")
        lines.append(f"- 基线 S5: 收益 {base['S5']['ret']:+.1f}% / Sharpe {base['S5']['sharpe']:.2f} / PF {base['S5']['pf']:.2f}")
        lines.append(f"- 基线 FULL: 收益 {base['FULL']['ret']:+.1f}% / Sharpe {base['FULL']['sharpe']:.2f} / PF {base['FULL']['pf']:.2f}")
    else:
        lines.append("- 未找到在改善 S3 同时不破坏 S5/FULL 的配置 → 趋势过滤器对此样本帮助有限, 维持无过滤 scalp(1.75)。")
        lines.append(f"- 基线 S3: 收益 {base['S3']['ret']:+.1f}% / PF {base['S3']['pf']:.2f}; "
                     f"基线 S5: 收益 {base['S5']['ret']:+.1f}% / PF {base['S5']['pf']:.2f}; "
                     f"基线 FULL: 收益 {base['FULL']['ret']:+.1f}% / PF {base['FULL']['pf']:.2f}")
    lines.append("")
    lines.append("> 局限: 过滤器参数(MA 周期 / ATR 阈值)由本样本扫描选定, 存在过拟合风险; "
                 "若采用, 建议留一段真正未见数据(final OOS)做最终确认。ma_filter 本质=趋势对齐门, "
                 "在震荡段减少逆势/假突破交易, 但也可能滤掉震荡中有效的均值回归单, 故需权衡交易笔数下降。")

    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines))
    print(f"\n已生成: {OUT_CSV}\n已生成: {OUT_MD}")


if __name__ == "__main__":
    main()
