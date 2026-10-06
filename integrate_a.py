# -*- coding: utf-8 -*-
"""A 方案 · 整合测试: 把通过 IS/OOS 筛选的入场逻辑按槽位整合进原箱体策略, 与原策略对比。
每槽位绑定不同 entry_style(slot_styles) + concurrent 逐槽位冷却 → 真正的多信号并发(非同信号复制)。
对比基准: 原 box 单策略基线。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import box_channel_optimized as m

IS_END = "2025-06-30"
OOS_START = "2025-07-01"
KW = dict(freq="15m", cash=1000.0, leverage=1000.0, lot=0.01, spread=0.33,
          commission=0.0, quiet=True)

# 通过筛选的逻辑各自的最优参数(仅影响对应 style, 互不冲突)
BEST = dict(dc_period=80, macross_require_channel=True)

CONFIGS = [
    ("box 基线(原策略)",   None,                          1, False, {}),
    ("box+donchian",      ["box", "donchian"],           2, True,  {"dc_period": 80}),
    ("box+ma_cross",      ["box", "ma_cross"],           2, True,  {"macross_require_channel": True}),
    ("box+donch+ma_cross", ["box", "donchian", "ma_cross"], 3, True,
                          {"dc_period": 80, "macross_require_channel": True}),
]

def run(slot_styles, n, concurrent, overrides, fromdate=None, todate=None):
    extra = dict(lot=0.01, max_positions=n, concurrent=concurrent)
    extra.update(m.PROFILES["scalp"])
    if slot_styles:
        extra["slot_styles"] = slot_styles
    extra.update(overrides)
    return m.run_backtest(extra_args=extra, fromdate=fromdate, todate=todate, **KW)

def max_depth(trades):
    depth = mx = 0
    for t in trades:
        if t["kind"] == "open":
            depth += 1; mx = max(mx, depth)
        else:
            depth -= 1
    return mx

def section(title, fromdate, todate):
    print()
    print("=" * 116)
    print(title)
    print("=" * 116)
    print(f"{'配置':<22}{'收益':>11}{'年化':>10}{'回撤':>9}{'Sharpe':>8}{'PF':>8}"
          f"{'Calmar':>8}{'笔数':>7}{'深度':>6}")
    print("-" * 116)
    out = {}
    for label, ss, n, conc, ov in CONFIGS:
        try:
            r = run(ss, n, conc, ov, fromdate=fromdate, todate=todate)
            d = max_depth(r["trades"])
            calmar = r["ann"] / (r["maxdd"] / 100.0) if r["maxdd"] > 0 else 0.0
            out[label] = (r, d, calmar)
            print(f"{label:<22}{r['total_ret']*100:>10.2f}%{r['ann']*100:>9.2f}%"
                  f"{r['maxdd']:>8.2f}%{r['sharpe']:>8.3f}{r['pf']:>8.3f}{calmar:>8.3f}"
                  f"{int(r['closed']):>7d}{d:>6d}", flush=True)
        except Exception as e:
            print(f"{label:<22} 失败 {type(e).__name__}: {e}", flush=True)
    return out

print("#" * 116)
print("A 方案 · 整合对比(每槽位不同 entry_style, concurrent 逐槽位冷却)")
print("#" * 116)
is_res = section(f"[IS 训练段] 起点 ~ {IS_END}", None, IS_END)
oos_res = section(f"[OOS 样本外] {OOS_START} ~ 终点", OOS_START, None)

print()
print("=" * 116)
print("IS → OOS 一致性 + 相对 box 基线的增量")
print("=" * 116)
print(f"{'配置':<22}{'IS收益':>11}{'OOS收益':>11}{'IS PF':>8}{'OOS PF':>8}"
      f"{'OOS回撤':>9}{'OOS Calmar':>11}{'vs基线Calmar':>13}{'判定':>10}")
print("-" * 116)
base_oos = oos_res.get("box 基线(原策略)")
for label, _, _, _, _ in CONFIGS:
    a = is_res.get(label); b = oos_res.get(label)
    if not a or not b:
        continue
    ra, da, ca = a
    rb, db, cb = b
    delta = cb - base_oos[2] if base_oos else 0.0
    if label.startswith("box 基线"):
        verdict = "基准"
    else:
        verdict = "更优" if (cb > base_oos[2] and rb["pf"] > 1.0) else "未超越"
    print(f"{label:<22}{ra['total_ret']*100:>10.2f}%{rb['total_ret']*100:>10.2f}%"
          f"{ra['pf']:>8.3f}{rb['pf']:>8.3f}{rb['maxdd']:>8.2f}%{cb:>11.3f}"
          f"{delta:>+13.3f}{verdict:>10}")
print()
print("判定说明: 以 OOS Calmar(年化/最大回撤) 是否超越 box 基线为准, 且要求 OOS PF>1.0")
