# -*- coding: utf-8 -*-
"""A 方案 · 单入场逻辑评估框架(fixed IS/OOS)
- 只变 entry_style, 风险参数沿用 scalp 画像(atr_stop=1.5ATR, r_mult=1.75, trailing 等) → 隔离"入场信号"这一个变量。
- IS(训练段)= 数据起点 ~ 2025-06-30; OOS(样本外)= 2025-07-01 ~ 数据终点。
- 目的: 先看清每种入场逻辑的原生表现, 再决定是否值得迭代优化。
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

def row(tag, r):
    print(f"{tag:<22} 收益{r['total_ret']*100:+8.2f}%  年化{r['ann']*100:+7.2f}%  "
          f"回撤{r['maxdd']:6.2f}%  Sharpe{r['sharpe']:6.3f}  PF{r['pf']:6.3f}  "
          f"笔数{int(r['closed']):4d}  胜率{r['win_rate']*100:5.1f}%", flush=True)

def section(title, fromdate, todate):
    print()
    print("=" * 118)
    print(title)
    print("=" * 118)
    out = {}
    for st in ("box", "ma_cross", "boll", "donchian"):
        try:
            r = run(st, fromdate=fromdate, todate=todate)
            out[st] = r
            row(f"  {st}", r)
        except Exception as e:
            print(f"  {st:<20} 失败: {type(e).__name__}: {e}", flush=True)
            out[st] = None
    return out

print("#" * 118)
print("A 方案 · 单入场逻辑 IS/OOS 评估  (scalp 风险参数不变, 只换 entry_style)")
print("#" * 118)
is_res = section(f"[IS 训练段] 起点 ~ {IS_END}", None, IS_END)
oos_res = section(f"[OOS 样本外] {OOS_START} ~ 终点", OOS_START, None)

print()
print("=" * 118)
print("IS → OOS 一致性(关键: 看样本外是否还成立)")
print("=" * 118)
print(f"{'style':<12}{'IS收益':>12}{'OOS收益':>12}{'IS回撤':>10}{'OOS回撤':>10}"
      f"{'IS PF':>9}{'OOS PF':>9}{'OOS笔数':>10}{'判定':>14}")
print("-" * 118)
for st in ("box", "ma_cross", "boll", "donchian"):
    a, b = is_res.get(st), oos_res.get(st)
    if a is None or b is None:
        continue
    ok = "OOS可交易" if b["closed"] >= 20 else "OOS样本不足"
    if b["closed"] >= 20:
        ok = "OOS盈利" if b["total_ret"] > 0 and b["pf"] > 1.0 else "OOS不达标"
    print(f"{st:<12}{a['total_ret']*100:>11.2f}%{b['total_ret']*100:>11.2f}%"
          f"{a['maxdd']:>9.2f}%{b['maxdd']:>9.2f}%{a['pf']:>9.3f}{b['pf']:>9.3f}"
          f"{int(b['closed']):>10d}{ok:>14}")
