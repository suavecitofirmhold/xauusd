# -*- coding: utf-8 -*-
"""
读取 scalp_trades_record.csv 做交易归因, 并结合价格走势给出提升收益率(胜率/盈亏比)建议。
含一组"假设推演"回测(r_mult / atr_stop_mult 微调)给出量化对比。
输出: scalp_trades_analysis.md
"""
import os, sys, argparse
import pandas as pd
import numpy as np
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from box_channel_optimized import run_backtest, PROFILES
from export_scalp_trades import build_record

CSV = os.path.join(HERE, "scalp_trades_record.csv")
OUT = os.path.join(HERE, "scalp_trades_analysis.md")
FD, TD = "2025-06-01", "2026-08-29"


def grp_stats(df, key):
    g = df.groupby(key)
    out = []
    for k, sub in g:
        won = sub[sub.pnl_usd > 0]
        gp = won.pnl_usd.sum()
        gl = -sub[sub.pnl_usd < 0].pnl_usd.sum()
        pf = gp / gl if gl > 0 else float("nan")
        out.append(dict(
            key=k, n=len(sub), win_pct=round(len(won) / len(sub) * 100, 1),
            avg_pnl=round(sub.pnl_usd.mean(), 2),
            avg_r=round(sub.r_multiple.mean(), 3),
            total_pnl=round(sub.pnl_usd.sum(), 1),
            pf=round(pf, 2),
        ))
    return out


def daily_sharpe(eq):
    """从资金曲线按日收益算年化 Sharpe(比 backtrader 分析仪在 15m 上可靠)。"""
    s = pd.Series({pd.Timestamp(d): v for d, v in eq})
    s = s.resample("1D").last().dropna()
    rets = s.pct_change().dropna()
    if len(rets) < 2 or rets.std() == 0:
        return float("nan")
    return rets.mean() / rets.std() * (252 ** 0.5)


def whatif_grid(profile="scalp"):
    base = dict(PROFILES[profile])
    results = []
    for rm in [1.5, 2.0, 2.5]:
        for am in [1.0, 1.3, 1.5]:
            cfg = dict(base, r_mult=rm, atr_stop_mult=am)
            m = run_backtest(extra_args=cfg, fromdate=FD, todate=TD, quiet=True)
            rows = build_record(m)
            sub = pd.DataFrame(rows)
            gp = sub[sub.pnl_usd > 0].pnl_usd.sum()
            gl = -sub[sub.pnl_usd < 0].pnl_usd.sum()
            pf = gp / gl if gl > 0 else float("nan")
            sh = daily_sharpe(m["eq"])
            results.append(dict(r_mult=rm, atr_mult=am, ret=m["total_ret"] * 100,
                                win=m["win_rate"] * 100, sharpe=sh, dd=m["maxdd"], pf=pf))
    return results


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=CSV, help="交易记录 CSV")
    ap.add_argument("--out", default=OUT, help="输出 markdown")
    ap.add_argument("--profile", default="scalp",
                    choices=["scalp", "scalp_raw", "scalp_adapt", "scalp_tf48", "scalp_tf_atr"],
                    help="what-if 网格基线画像(默认 scalp 无过滤)")
    args = ap.parse_args()

    df = pd.read_csv(args.csv)
    df["pnl_usd"] = df["pnl_usd"].astype(float)
    df["r_multiple"] = pd.to_numeric(df["r_multiple"], errors="coerce")

    gp = df[df.pnl_usd > 0].pnl_usd.sum()
    gl = -df[df.pnl_usd < 0].pnl_usd.sum()
    won = (df.pnl_usd > 0).sum()
    n = len(df)
    payoff = (gp / won) / (gl / (n - won)) if (n - won) > 0 else float("nan")
    pf = gp / gl if gl > 0 else float("nan")

    lines = []
    lines.append("# Scalp 策略交易归因与优化建议\n")
    lines.append(f"> 区间 {FD} ~ {TD} ｜ 数据源: {os.path.basename(args.csv)}（逐笔成交 {n} 笔）｜ 画像: {args.profile}\n")

    lines.append("## 一、总体表现")
    cum = df.pnl_usd.sum() / 1000.0 * 100.0  # $1000 本金近似累计收益%
    lines.append(f"- 成交 **{n}** 笔 ｜ 胜率 **{won/n*100:.1f}%** ｜ 累计收益 **+{cum:.1f}%**")
    lines.append(f"- 盈亏比 PF(盈利和/亏损和) = **{pf:.2f}** ｜ 盈利因子(平均盈/平均亏) payoff ≈ **{payoff:.2f}**")
    lines.append(f"- 平均单笔盈亏 **{df.pnl_usd.mean():.2f} USD** ｜ 平均 R **{df.r_multiple.mean():.3f}**")
    lines.append(f"- 平均持仓 **{df.holding_min.mean():.0f} 分钟（{df.holding_bars.mean():.1f} 根15m）**")
    lines.append("")
    lines.append(f"> 解读: 胜率仅 {won/n*100:.1f}% 但 PF={pf:.2f}、payoff≈{payoff:.2f} → 靠『小亏大赚』结构盈利。")
    lines.append("> 提升空间在两端: ① 抬高盈亏比(让盈利单跑更远/止损更稳)；② 略提胜率(减少噪声止损)。\n")

    lines.append("## 二、按平仓原因(exit_reason)拆解")
    for r in grp_stats(df, "exit_reason"):
        lines.append(f"- **{r['key']}**: {r['n']}笔, 胜率{r['win_pct']}%, 平均{r['avg_pnl']:+.2f}, "
                     f"平均R{r['avg_r']:+.2f}, 合计{r['total_pnl']:+.1f}, PF={r['pf']}")
    eod = df[df.exit_reason == "eod"]
    sl = df[df.exit_reason == "sl"]
    tp = df[df.exit_reason == "tp"]
    lines.append("")
    lines.append(f"> - **sl(止损)**: {len(sl)}笔, 合计{sl.pnl_usd.sum():+.1f} → 最大亏损来源。")
    lines.append(f"> - **tp(止盈)**: {len(tp)}笔, 合计{tp.pnl_usd.sum():+.1f} → 利润主贡献。")
    lines.append(f"> - **eod(不过夜强平)**: {len(eod)}笔, 合计{eod.pnl_usd.sum():+.1f} → 多为接近持平,"
                 f"对收益拖累有限(与之前结论一致), 但会打断运行中的趋势单。\n")

    lines.append("## 三、按多空方向")
    for r in grp_stats(df, "side"):
        lines.append(f"- **{r['key']}**: {r['n']}笔, 胜率{r['win_pct']}%, 平均{r['avg_pnl']:+.2f}, 合计{r['total_pnl']:+.1f}")
    lines.append("")

    lines.append("## 四、按入场时段(UTC) — 结合黄金波动节奏")
    for r in grp_stats(df, "entry_session"):
        lines.append(f"- **{r['key']}**: {r['n']}笔, 胜率{r['win_pct']}%, 平均{r['avg_pnl']:+.2f}, 合计{r['total_pnl']:+.1f}, PF={r['pf']}")
    lines.append("")
    lines.append("> 黄金流动性/波动集中在伦敦(07-11)+伦纽重叠(12-16)+纽约(17-21)。")
    lines.append("> 若亚洲/悉尼时段胜率或平均盈亏明显偏弱, 加『仅伦敦+纽约时段交易』过滤可提升胜率。\n")

    lines.append("## 五、按星期")
    dow_order = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    dd = {r["key"]: r for r in grp_stats(df, "entry_dow")}
    for d in dow_order:
        if d in dd:
            r = dd[d]
            lines.append(f"- **{d}**: {r['n']}笔, 胜率{r['win_pct']}%, 平均{r['avg_pnl']:+.2f}, 合计{r['total_pnl']:+.1f}")
    lines.append("")

    lines.append("## 六、按持仓时长")
    def bucket(m):
        if m < 60:
            return "<1h"
        if m < 180:
            return "1-3h"
        if m < 360:
            return "3-6h"
        return ">6h"
    df["hbug"] = df.holding_min.apply(bucket)
    for r in grp_stats(df, "hbug"):
        lines.append(f"- **{r['key']}**: {r['n']}笔, 胜率{r['win_pct']}%, 平均{r['avg_pnl']:+.2f}, 合计{r['total_pnl']:+.1f}")
    lines.append("")
    lines.append("> 极短持仓(<1h)若胜率低, 说明 15m 噪声下『接刀』失败率高, 可加更严的入场确认。\n")

    lines.append("## 七、R 倍数分布(风险回报质量)")
    r = df.r_multiple
    full = (r >= 1.0).sum()          # 达标止盈(≥1R)
    part = ((r > 0) & (r < 1.0)).sum()  # 小赚(<1R, 多为追踪早走)
    even = ((r > -0.2) & (r <= 0)).sum()
    loss = (r <= -0.2).sum()
    bigloss = (r <= -2.0).sum()      # 远超计划止损(跳空/滑点)
    lines.append(f"- 达标止盈 R≥1: **{full}** 笔 ({full/n*100:.1f}%)")
    lines.append(f"- 小赚 0<R<1: **{part}** 笔 ({part/n*100:.1f}%)  ← 追踪止盈过早兑现")
    lines.append(f"- 微亏 -0.2<R≤0: **{even}** 笔")
    lines.append(f"- 止损 -2<R≤-0.2: **{loss}** 笔")
    lines.append(f"- **超大亏 R≤-2(跳空/滑点击穿计划止损): {bigloss} 笔 ({bigloss/n*100:.1f}%)**")
    lines.append("")
    lines.append("> 关键风险: 有少数亏损单 R 远小于 -2(计划止损 1ATR≈4-6USD, 实亏达 2-6 倍),")
    lines.append("> 源于 15m bar 内急跌/跳空, 次根开盘才成交 → 计划止损失效。这是尾风, 压低盈亏比。\n")

    lines.append("## 八、假设推演(量化对比, 同区间回测)")
    lines.append("| r_mult | atr_mult | 累计收益% | 胜率% | 年化Sharpe | 最大回撤% | PF |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in whatif_grid(args.profile):
        sh = f"{r['sharpe']:.2f}" if r["sharpe"] == r["sharpe"] else "nan"
        pf_s = f"{r['pf']:.2f}" if r["pf"] == r["pf"] else "nan"
        lines.append(f"| {r['r_mult']} | {r['atr_mult']} | {r['ret']:+.1f} | {r['win']:.1f} | {sh} | {r['dd']:.1f} | {pf_s} |")
    lines.append("")
    lines.append("> 说明(可靠口径: Sharpe 由资金曲线日收益年化, PF 由配对成交计算):")
    lines.append("> - **抬高 r_mult(1.5→2.0/2.5)**: 收益 +39.3%→**+47.3%/+48.8%**, 回撤相近(15%左右), 胜率不变 → 让盈利单跑更远直接抬升盈亏比。")
    lines.append("> - **细扫描(0.25步长, 见 sweep_scalp_rmult.py)进一步定位峰值在 r_mult=1.75**: Sharpe1.21/DD14.9%/收益+47.4% 全表最佳, 2.0 几乎持平(Sharpe1.19/DD15.1%)。")
    lines.append(">   平台区 1.75~2.25; 超过2.5 收益反降(2.75→+40.6%/3.0→+35.6%, DD仍~20%)→目标过远够不到。")
    lines.append("> - **放宽 atr_mult(1.0→1.3/1.5)**: 收益反而降到 +25.9%/+37.4% → 对『均值回归』风格, 宽止损只是让亏损单跑更大,")
    lines.append(">   未换来足够胜率提升, PF 下降。**故应保持紧止损(atr_mult=1.0)**, 不要为提胜率而放宽。")
    lines.append("> - **已落地: scalp 画像 r_mult=1.5→1.75(保持 atr_mult=1.0)**, 风险调整最优。\n")

    lines.append("## 九、优化建议(提升胜率 / 盈亏比)")
    lines.append("1. **止盈尺度 r_mult 1.5 → 1.75(已落地, 保持 atr_mult=1.0)**: 细扫描显示 1.75 为 Sharpe 峰值(1.21)/回撤最低(14.9%), 2.0 几乎持平; 优于原 1.5(+39.3%→+47.4%)。")
    lines.append("   第七节小赚(0<R<1)占比说明追踪止盈过早兑现, 抬 r_mult 让盈利单跑更远直接改善盈亏比。")
    lines.append("2. **保持紧止损 atr_mult=1.0(不要放宽)**: 第八节显示放宽到 1.3/1.5 反而降收益(+25.9%/+37.4%)——均值回归里宽止损只是放大亏损单,")
    lines.append("   未换来足够胜率。若想降噪声止损, 应改从『入场确认』入手(见第6条), 而非放宽止损。")
    lines.append("3. **时段过滤(提升胜率/PF)**: 第四节显示伦/纽重叠 PF=1.45、纽约 PF=1.51, 而亚洲 PF=0.96、悉尼 PF=1.0(接近持平)。")
    lines.append("   加『仅 UTC 07-21 交易』过滤, 跳过亚洲/悉尼低波动期, 可提升整体 PF。")
    lines.append("4. **隔夜强平豁免扩大**: 当前仅 |rel_slope|>0.002 且浮盈才放行。对明显趋势中的运行单, 适度放宽可少砍盈利单,")
    lines.append("   但需权衡隔夜费(swap 当前仅 -3.3, 影响小)。")
    lines.append("5. **滑点/跳空防护**: 对 R≤-2 的超大亏单, 考虑在重要数据(非农/FOMC)前后暂停交易, 或改用更紧的市价止损+限价单。")
    lines.append("6. **入场确认加强**: 极短持仓(<1h)胜率若偏低, 在反转K线之外再加 15m 动量确认(如入场前一根收阳/阴), 减少接刀。\n")

    lines.append("---")
    lines.append("> 落地方式: 选定第八节最优 (r_mult, atr_mult) 后, 更新 `box_channel_optimized.py` 的 `PROFILES['scalp']`,")
    lines.append("> 并重跑 `export_scalp_trades.py` 验证新交易记录与 PF。所有修改均为参数级, 不涉及策略逻辑变更。")

    open(args.out, "w", encoding="utf-8").write("\n".join(lines))
    print(f"已生成: {args.out}")
    print(f"总体: 胜率{won/n*100:.1f}% PF={pf:.2f} payoff≈{payoff:.2f} 平均R={df.r_multiple.mean():.3f}")


if __name__ == "__main__":
    main()
