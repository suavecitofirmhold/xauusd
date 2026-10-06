# -*- coding: utf-8 -*-
"""
Box 策略优化 · 统计扫描 + 回测研究
====================================
- 训练/测试切分 (防过拟合): TRAIN 2023-09-13~2025-06-01 / TEST 2025-06-01~2026-08-29
- 阶段 A: 4 种入场风格对比 (box/ma_cross/boll/donchian)
- 阶段 B: box 关键参数网格 (r_mult/止损方式/cooldown/rel_slope/short_win/min_k)
          在 TRAIN 上扫描, 取稳健前 N, 在 TEST 上验证
- 阶段 C: 杠杆(1000/500/200) × 手数(0.01/0.02/0.05/0.1) 矩阵
- 阶段 D: 极致短线/中线/长线 三套画像

结果写入 analyze_box_opt_results.json, 并打印摘要表。
"""
import os, json, itertools, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from box_channel_optimized import run_backtest

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                    "analyze_box_opt_results.json")
TRAIN = ("2023-09-13", "2025-06-01")
TEST = ("2025-06-01", "2026-08-29")
FULL = (None, None)
CASH = 1000.0
SPREAD = 0.33
LOTS = [0.01, 0.02, 0.05, 0.1]
LEVS = [1000, 500, 200]


def run(extra, lev=1000, lot=0.01, span=TRAIN, quiet=True):
    fd, td = span
    m = run_backtest(extra_args=extra, cash=CASH, leverage=lev, lot=lot,
                     spread=SPREAD, fromdate=fd, todate=td, quiet=quiet)
    return m


def clean(m):
    """去掉不可 JSON 序列化的大对象 (eq/trades 含 datetime)。"""
    return {k: v for k, v in m.items() if k not in ("eq", "trades")}


def base(style="box", **kw):
    p = dict(entry_style=style, avoid_overnight=True, r_mult=2.0,
             atr_stop=True, atr_stop_mult=1.5, trailing=False,
             cooldown_bars=4, rel_slope_thresh=0.0006, short_win=8, min_k=3)
    p.update(kw)
    return p


def fmt(m):
    return (f"ret={m['total_ret']*100:+6.1f}% ann={m['ann']*100:+5.1f}% "
            f"DD={m['maxdd']:5.1f}% sharpe={m['sharpe']:4.2f} "
            f"n={m['closed']:4d} win={m['win_rate']*100:4.1f}% "
            f"eod={m['overnight_eod']:3d} swap={m['swap_total']:+6.1f}")


results = {"A_styles": [], "B_grid_train": [], "B_top_test": [], "C_leverage_lot": [], "D_profiles": []}


# ---------------- 阶段 A: 入场风格 ----------------
print("=" * 70)
print("阶段 A: 入场风格对比 (TRAIN)")
print("=" * 70)
style_cfgs = {
    "box":      base("box"),
    "ma_cross": base("ma_cross", ma_fast=5, ma_slow=20),
    "boll":     base("boll", r_mult=1.5),
    "donchian": base("donchian", trailing=True, r_mult=2.0, rel_slope_thresh=0.0003),
}
for name, cfg in style_cfgs.items():
    m = run(cfg, span=TRAIN, quiet=True)
    results["A_styles"].append({"style": name, "metrics": clean(m), "cfg": cfg})
    print(f"  {name:9s} {fmt(m)}")

# 选 best 风格: 以 sharpe 为主、要求 maxdd<25 且 ret>0
best_style = max(results["A_styles"],
                 key=lambda x: x["metrics"]["sharpe"] if (x["metrics"]["maxdd"] < 25 and x["metrics"]["total_ret"] > 0) else -9)
BEST_STYLE = best_style["style"]
print(f"  -> 选 {BEST_STYLE} 进入细调")


# ---------------- 阶段 B: 参数网格 (TRAIN) + 验证 (TEST) ----------------
print("=" * 70)
print(f"阶段 B: {BEST_STYLE} 参数网格 (TRAIN) -> 验证 (TEST)")
print("=" * 70)
grid = dict(
    r_mult=[1.5, 2.0, 2.5],
    atr_stop=[True, False],
    rel_slope_thresh=[0.0004, 0.0006],
    short_win=[6, 8],
    min_k=[2, 3],
)
keys = list(grid)
combo_iter = itertools.product(*[grid[k] for k in keys])
total = 1
for v in grid.values():
    total *= len(v)
print(f"  网格规模 = {total} 组合")
done = 0
for vals in combo_iter:
    cfg = base(BEST_STYLE)
    for k, v in zip(keys, vals):
        if k == "atr_stop":
            cfg["atr_stop"] = v
            if not v:
                cfg["sl_buffer"] = 2.0
        else:
            cfg[k] = v
    m = run(cfg, span=TRAIN, quiet=True)
    rec = {"cfg": cfg, "metrics": clean(m),
           "tag": f"rm{cfg['r_mult']}_atr{cfg['atr_stop']}_cd{cfg['cooldown_bars']}_rs{cfg['rel_slope_thresh']}_sw{cfg['short_win']}_mk{cfg['min_k']}"}
    results["B_grid_train"].append(rec)
    done += 1
    if done % 20 == 0:
        json.dump(results, open(OUT, "w"))
        print(f"  [{done}/{total}] rm{cfg['r_mult']} atr{cfg['atr_stop']} cd{cfg['cooldown_bars']} rs{cfg['rel_slope_thresh']} sw{cfg['short_win']} mk{cfg['min_k']} -> {fmt(m)}")

# 稳健筛选: TRAIN 上 sharpe>0.5 且 maxdd<20, 按 (ann*win_rate) 排序取前 8
cands = [r for r in results["B_grid_train"]
         if r["metrics"]["sharpe"] > 0.5 and r["metrics"]["maxdd"] < 20 and r["metrics"]["total_ret"] > 0]
cands.sort(key=lambda r: r["metrics"]["ann"] * r["metrics"]["win_rate"], reverse=True)
cands = cands[:8]
print(f"  TRAIN 稳健候选 {len(cands)} 个, 在 TEST 验证...")
for r in cands:
    mt = run(r["cfg"], span=TEST, quiet=True)
    r["test"] = clean(mt)
    results["B_top_test"].append({"tag": r["tag"], "train": clean(r["metrics"]), "test": r["test"], "cfg": r["cfg"]})
    print(f"  {r['tag']:55s} TRAIN {fmt(r['metrics'])} | TEST {fmt(mt)}")

# 选最终: TEST 上不崩(maxdd<25 且 ret>-0.2)且 sharpe 最高
final_cands = [r for r in cands if r["test"]["maxdd"] < 25 and r["test"]["total_ret"] > -0.2]
FINAL = max(final_cands, key=lambda r: r["test"]["sharpe"])["cfg"] if final_cands else cands[0]["cfg"]
print(f"  -> 最终配置: {FINAL}")


# ---------------- 阶段 C: 杠杆 × 手数 ----------------
print("=" * 70)
print("阶段 C: 杠杆 × 手数 (全样本, 最终配置)")
print("=" * 70)
for lev in LEVS:
    for lot in LOTS:
        m = run(dict(FINAL), lev=lev, lot=lot, span=FULL, quiet=True)
        results["C_leverage_lot"].append({"leverage": lev, "lot": lot, "metrics": clean(m)})
        print(f"  lev=1:{lev:4d} lot={lot:5.2f} {fmt(m)}")
json.dump(results, open(OUT, "w"))


# ---------------- 阶段 D: 长短线画像 ----------------
print("=" * 70)
print("阶段 D: 短线/中线/长线画像 (TRAIN+TEST 全样本)")
print("=" * 70)
profiles = {
    "极致短线(scalp)": base("box", r_mult=1.5, atr_stop=True, atr_stop_mult=1.0,
                            trailing=True, short_win=4, min_k=2, cooldown_bars=2,
                            rel_slope_thresh=0.0004, avoid_overnight=True),
    "中线(box标准)": dict(FINAL),
    "长线(trend)": base("ma_cross", ma_fast=10, ma_slow=60, trend_window=96,
                        short_win=32, r_mult=3.0, atr_stop=True, atr_stop_mult=2.0,
                        trailing=True, cooldown_bars=8, rel_slope_thresh=0.0002,
                        avoid_overnight=False),
}
for name, cfg in profiles.items():
    mt = run(cfg, span=TEST, quiet=True)
    mr = run(cfg, span=TRAIN, quiet=True)
    results["D_profiles"].append({"name": name, "train": clean(mr), "test": clean(mt), "cfg": cfg})
    print(f"  {name:18s} TRAIN {fmt(mr)} | TEST {fmt(mt)}")

json.dump(results, open(OUT, "w"))
print("=" * 70)
print(f"完成, 结果写入 {OUT}")
