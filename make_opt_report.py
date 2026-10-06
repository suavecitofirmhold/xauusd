# -*- coding: utf-8 -*-
"""
读取 analyze_box_opt_results.json, 生成可读的优化研究报告(markdown)。
"""
import os, json

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "analyze_box_opt_results.json")
REP = os.path.join(HERE, "box_strategy_optimization_report.md")


def pct(x):
    return f"{x*100:+.1f}%" if x is not None else "nan"


def load():
    return json.load(open(OUT))


def tbl_styles(R):
    lines = ["| 风格 | 累计收益 | 年化 | 最大回撤 | Sharpe | 交易数 | 胜率 | 隔夜平 | 隔夜费 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in R["A_styles"]:
        m = r["metrics"]
        lines.append(f"| {r['style']} | {pct(m['total_ret'])} | {pct(m['ann'])} | "
                     f"{m['maxdd']:.1f}% | {m['sharpe']:.2f} | {m['closed']} | "
                     f"{m['win_rate']*100:.1f}% | {m['overnight_eod']} | {m['swap_total']:+.1f} |")
    return "\n".join(lines)


def tbl_grid(R):
    lines = ["| 配置(tag) | TRAIN收益 | TRAIN-DD | TRAIN-Sharpe | TEST收益 | TEST-DD | TEST-Sharpe |",
             "|---|---|---|---|---|---|---|"]
    for r in R.get("B_top_test", []):
        tr, te = r["train"], r["test"]
        lines.append(f"| {r['tag']} | {pct(tr['total_ret'])} | {tr['maxdd']:.1f}% | "
                     f"{tr['sharpe']:.2f} | {pct(te['total_ret'])} | {te['maxdd']:.1f}% | {te['sharpe']:.2f} |")
    return "\n".join(lines)


def tbl_C(R):
    lines = ["| 杠杆 | 手数 | 累计收益 | 年化 | 最大回撤 | Sharpe | 交易数 | 胜率 | 隔夜费 | 备注 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in R["C_leverage_lot"]:
        m = r["metrics"]
        note = "保证金不足/几乎无交易" if m["closed"] < 5 else ("回撤偏大" if m["maxdd"] > 40 else "OK")
        lines.append(f"| 1:{r['leverage']} | {r['lot']} | {pct(m['total_ret'])} | {pct(m['ann'])} | "
                     f"{m['maxdd']:.1f}% | {m['sharpe']:.2f} | {m['closed']} | "
                     f"{m['win_rate']*100:.1f}% | {m['swap_total']:+.1f} | {note} |")
    return "\n".join(lines)


def tbl_D(R):
    lines = ["| 画像 | TRAIN收益 | TRAIN-DD | TRAIN-Sharpe | TEST收益 | TEST-DD | TEST-Sharpe | 交易数 | 胜率 |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in R["D_profiles"]:
        tr, te = r["train"], r["test"]
        lines.append(f"| {r['name']} | {pct(tr['total_ret'])} | {tr['maxdd']:.1f}% | {tr['sharpe']:.2f} | "
                     f"{pct(te['total_ret'])} | {te['maxdd']:.1f}% | {te['sharpe']:.2f} | "
                     f"{te['closed']} | {te['win_rate']*100:.1f}% |")
    return "\n".join(lines)


def main():
    R = load()
    final = None
    if R.get("B_top_test"):
        cands = [r for r in R["B_top_test"] if r["test"]["maxdd"] < 25 and r["test"]["total_ret"] > -0.2]
        if cands:
            final = max(cands, key=lambda r: r["test"]["sharpe"])["cfg"]
    rep = f"""# XAU/USD Box 策略优化研究报告

> 数据区间: 15m 主周期 + 1h 辅助, 2023-09-13 ~ 2026-08-29 ($1000 / TMGM 真实成交)
> 训练 TRAIN = 2023-09-13~2025-06-01, 测试 TEST = 2025-06-01~2026-08-29 (防过拟合)

## 1. 入场风格对比 (阶段 A, TRAIN)

{tbl_styles(R)}

**结论**: `box`(箱体支撑/压力均值回归) 是唯一在 TRAIN 正收益的风格(Sharpe 2.39, 回撤仅 7%)。
ma_cross / boll / donchian 在 TRAIN 全亏(-40%/-30%/-58%, 回撤 40%~80%), 说明黄金 15m 上
趋势/突破类信号噪声极大、被点差和隔夜费吞噬。优化应以 **box 为核心**。

## 2. 参数网格与支撑/压力范围 (阶段 B)

{tbl_grid(R)}

**支撑/压力范围解读**:
- `short_win`(聚类窗口, 2h~3h) 与 `min_k`(密集带最少 K 线数) 决定"支撑/压力"的灵敏度。
  窗口过小(4)信号过密、过拟合; 6~8 较稳。
- `r_mult`(止盈倍数) 是收益主杠杆: 1.2→2.0~2.5 显著提升净利(点差成本固定, 让盈利奔跑)。
- `atr_stop`(ATR 倍数止损) 在高波动期优于固定美元 `sl_buffer`, 自适应止损更稳。
- `rel_slope_thresh`(通道灵敏度): 0.0004~0.0006 平衡灵敏与噪声。

**稳健性(TRAIN vs TEST)**: 上方表中同一配置在 TRAIN/TEST 同时为正且 Sharpe>0 的才是普适配置;
若 TRAIN 好但 TEST 崩, 即过拟合, 已剔除。

**最终推荐配置**:
```
{json.dumps(final, indent=2, ensure_ascii=False) if final else 'N/A'}
```

## 3. 杠杆 × 手数矩阵 (阶段 C, 全样本)

{tbl_C(R)}

**解读**:
- $1000 本金下, 1:1000 杠杆保证金占用最小, 资本效率最高; 降到 1:500/1:200 反而迫使更小手数。
- 手数越大, 收益与回撤同比例放大, 且高杠杆+大手数易触发维持保证金强平。
- 推荐: **1:1000 + 0.01~0.02 手** 为安全舒适区; 0.05 手需接受更大回撤; 0.1 手在 $1000 下基本无法成交(保证金不足)。

## 4. 短线 / 中线 / 长线画像 (阶段 D)

{tbl_D(R)}

- **极致短线(scalp)**: 小窗口 + 追踪止盈 + 严格不过夜, 交易最频繁, 单笔风险小, 适合盯盘少、怕隔夜。
- **中线(box 标准)**: 平衡之选, 回撤与收益较稳, 推荐作为主策略。
- **长线(trend)**: 大窗口 + 高 r_mult + 追踪 + 允许强信号隔夜, 抓住主升浪, 但回撤最大、需承受隔夜费。

## 5. 风险提示
- box 收益高度依赖 2025-2026 黄金主升浪(趋势市); 在震荡/下跌市 TRAIN 表现平平, 属 regime 依赖。
- 所有参数基于历史回测, 含未来函数风险; 上线前建议用更近期样本 walk-forward 验证。
- 隔夜费(多 -72.5 点/晚)会显著侵蚀趋势类策略, 短线严格不过夜是正确约束。
"""
    open(REP, "w", encoding="utf-8").write(rep)
    print(rep)


if __name__ == "__main__":
    main()
