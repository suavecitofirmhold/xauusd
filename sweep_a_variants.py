# -*- coding: utf-8 -*-
"""A 方案 · 单入场逻辑结构变体扫描(phase1: IS 选优 → phase2: OOS 验证)
只变 entry_style + 结构开关/关键参数; 风险参数沿用 scalp 画像。
严格先 IS 选、后 OOS 验, 避免在样本外挑选导致结论虚高。
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import box_channel_optimized as m

IS_END = "2025-06-30"
OOS_START = "2025-07-01"
KW = dict(freq="15m", cash=1000.0, leverage=1000.0, lot=0.01, spread=0.33,
          commission=0.0, quiet=True)

def run(style, overrides=None, fromdate=None, todate=None):
    extra = dict(lot=0.01, max_positions=1)
    extra.update(m.PROFILES["scalp"])
    extra["entry_style"] = style
    if overrides:
        extra.update(overrides)
    return m.run_backtest(extra_args=extra, fromdate=fromdate, todate=todate, **KW)

VARIANTS = [
    # (style, 标签, overrides)
    ("ma_cross", "mc_base",      {}),
    ("ma_cross", "mc_通道同向",   {"macross_require_channel": True}),
    ("ma_cross", "mc_冷却20",     {"cooldown_bars": 20}),
    ("ma_cross", "mc_同向+冷却20", {"macross_require_channel": True, "cooldown_bars": 20}),

    ("boll", "bl_全通道",        {"boll_any_channel": True}),
    ("boll", "bl_全通道_dev1.5", {"boll_any_channel": True, "boll_dev": 1.5}),
    ("boll", "bl_全通道_dev2.5", {"boll_any_channel": True, "boll_dev": 2.5}),

    ("donchian", "dc_base",       {}),
    ("donchian", "dc_周期40",     {"dc_period": 40}),
    ("donchian", "dc_周期80",     {"dc_period": 80}),
    ("donchian", "dc_收盘确认",   {"donchian_close_confirm": True}),
    ("donchian", "dc_40+收盘确认", {"dc_period": 40, "donchian_close_confirm": True}),
]

MIN_TRADES = 20

print("#" * 110)
print("PHASE 1 · IS 训练段(起点 ~ %s) 结构变体扫描" % IS_END)
print("#" * 110)
print(f"{'style':<10}{'变体':<18}{'收益':>10}{'回撤':>9}{'Sharpe':>8}{'PF':>8}{'笔数':>7}{'胜率':>8}")
print("-" * 110)

is_out = {}
for style, label, ov in VARIANTS:
    try:
        r = run(style, ov, todate=IS_END)
        is_out[(style, label)] = (r, ov)
        print(f"{style:<10}{label:<18}{r['total_ret']*100:>9.2f}%{r['maxdd']:>8.2f}%"
              f"{r['sharpe']:>8.3f}{r['pf']:>8.3f}{int(r['closed']):>7d}{r['win_rate']*100:>7.1f}%",
              flush=True)
    except Exception as e:
        print(f"{style:<10}{label:<18} 失败 {type(e).__name__}: {e}", flush=True)

# 每种 style 按 IS PF 选最优(要求样本量足够)
print()
print("#" * 110)
print("PHASE 2 · 各 style 的 IS 最优变体 → OOS 验证(%s ~ 终点)" % OOS_START)
print("#" * 110)
print(f"{'style':<10}{'选中变体':<18}{'IS收益':>10}{'IS PF':>8}{'OOS收益':>10}{'OOS回撤':>9}"
      f"{'OOS PF':>8}{'OOS笔数':>8}{'判定':>12}")
print("-" * 110)

for style in ("ma_cross", "boll", "donchian"):
    cands = [(k, v) for k, v in is_out.items() if k[0] == style and v[0]["closed"] >= MIN_TRADES]
    if not cands:
        cands = [(k, v) for k, v in is_out.items() if k[0] == style]
    if not cands:
        print(f"{style:<10} 无有效变体")
        continue
    # 按 IS PF 降序, 同 PF 取回撤小
    cands.sort(key=lambda kv: (kv[1][0]["pf"], -kv[1][0]["maxdd"]), reverse=True)
    (_, label), (r_is, ov) = cands[0]
    try:
        r_oos = run(style, ov, fromdate=OOS_START)
        ok = "达标" if (r_oos["pf"] > 1.0 and r_oos["total_ret"] > 0 and r_oos["maxdd"] < 25) else "不达标"
        print(f"{style:<10}{label:<18}{r_is['total_ret']*100:>9.2f}%{r_is['pf']:>8.3f}"
              f"{r_oos['total_ret']*100:>9.2f}%{r_oos['maxdd']:>8.2f}%{r_oos['pf']:>8.3f}"
              f"{int(r_oos['closed']):>8d}{ok:>12}", flush=True)
    except Exception as e:
        print(f"{style:<10}{label:<18} OOS 失败 {type(e).__name__}: {e}", flush=True)

print()
print("参考: box 基线 IS -6.04%/PF0.911/DD9.33%; OOS +89.02%/PF1.622/DD10.70%")
print("达标门槛: OOS 收益>0 且 PF>1.0 且 回撤<25%")
