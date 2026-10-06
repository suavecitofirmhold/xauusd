# -*- coding: utf-8 -*-
"""对比 Python 与 EA 的止损/止盈距离(即 ATR 尺度与 R 倍数是否一致)。"""
import csv, statistics
from box_channel_optimized import run_backtest, PROFILES

WIN_FROM, WIN_TO = "2024-03-04", "2026-08-26"

def summarize(name, risks, rewards, ratios):
    print(f"\n--- {name} ---")
    if not risks:
        print("  (无数据)")
        return
    print(f"  risk  (|entry-stop|)   mean={statistics.mean(risks):.4f}  median={statistics.median(risks):.4f}")
    if rewards:
        print(f"  reward(|target-entry|) mean={statistics.mean(rewards):.4f}  median={statistics.median(rewards):.4f}")
    if ratios:
        print(f"  reward/risk            mean={statistics.mean(ratios):.4f}  median={statistics.median(ratios):.4f}  min={min(ratios):.3f}  max={max(ratios):.3f}")

# ---------- Python ----------
ATR_SHIFT = 1   # 与 EA GetATRVal(1) 对齐
_py = dict(PROFILES["scalp"])
_py["atr_shift"] = ATR_SHIFT
m = run_backtest(freq="15m", extra_args=_py, fromdate=WIN_FROM, todate=WIN_TO, quiet=True)
py_risk, py_rew, py_ratio = [], [], []
for t in m["trades"]:
    if t["kind"] != "open":
        continue
    e, s, tg = t.get("price"), t.get("stop"), t.get("target")
    if e is None or s is None:
        continue
    r = abs(e - s)
    py_risk.append(r)
    if tg is not None:
        rw = abs(tg - e)
        py_rew.append(rw)
        if r > 0:
            py_ratio.append(rw / r)
print("=" * 70)
print(f"止损/止盈距离对比  ({WIN_FROM} ~ {WIN_TO})")
print("=" * 70)
summarize("Python (scalp, atr_stop_mult=1.5, r_mult=1.75)", py_risk, py_rew, py_ratio)
print(f"  n={len(py_risk)}  隐含 ATR = risk/1.5 → mean={statistics.mean(py_risk)/1.5:.4f}")

# ---------- EA ----------
EA = r"C:\Users\18628\AppData\Roaming\MetaQuotes\Tester\7643C0B96C7AD5841307C9E1EB0B9252\Agent-127.0.0.1-3000\MQL5\Files\BoxChannelScalp_trades.csv"
rows = list(csv.DictReader(open(EA, encoding="utf-8")))
ea_risk, ea_rew, ea_ratio = [], [], []
for r in rows:
    if r["action"] != "open":
        continue
    try:
        e, s, tg = float(r["price"]), float(r["stop"]), float(r["target"])
    except (ValueError, KeyError):
        continue
    rk = abs(e - s)
    ea_risk.append(rk)
    rw = abs(tg - e)
    ea_rew.append(rw)
    if rk > 0:
        ea_ratio.append(rw / rk)
summarize("EA (InpAtrStopMult=1.5, InpRMult=1.75)", ea_risk, ea_rew, ea_ratio)
print(f"  n={len(ea_risk)}  隐含 ATR = risk/1.5 → mean={statistics.mean(ea_risk)/1.5:.4f}")

# ---------- 结论 ----------
print("\n" + "=" * 70)
print("结论")
print("=" * 70)
if py_risk and ea_risk:
    pr, er = statistics.mean(py_risk), statistics.mean(ea_risk)
    print(f"  risk:  Python {pr:.4f}  vs  EA {er:.4f}   → EA/Python = {er/pr:.4f}  (差 {(er/pr-1)*100:+.1f}%)")
    print(f"  隐含ATR: {pr/1.5:.4f} vs {er/1.5:.4f}")
if py_ratio and ea_ratio:
    prr, err = statistics.mean(py_ratio), statistics.mean(ea_ratio)
    print(f"  reward/risk: Python {prr:.4f} vs EA {err:.4f}  (设定 r_mult=1.75)")
    if abs(err - 1.75) > 0.05:
        print(f"  ⚠️ EA reward/risk 中位={statistics.median(ea_ratio):.3f} 偏离设定 1.75 —— 检查 EA 的 target 计算")
    if abs(prr - 1.75) > 0.05:
        print(f"  ⚠️ Python reward/risk 中位={statistics.median(py_ratio):.3f} 偏离设定 1.75")
