# -*- coding: utf-8 -*-
"""验证 ATR 平滑方式差异: 在同一份 M15 数据上算 Wilder / SMA / EMA 三种 ATR(14),
看哪一种分别对应 Python 隐含 ATR(6.6211) 与 EA 隐含 ATR(6.1063)。
"""
import numpy as np, pandas as pd
from box_channel_optimized import load_data

df, _, _ = load_data("15m", "2024-03-04", "2026-08-26")
h, l, c = df["high"].values, df["low"].values, df["close"].values
n = len(c)

# True Range
tr = np.empty(n - 1)
tr[:] = np.maximum(h[1:] - l[1:],
                   np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))

period = 14

def wilder(tr, p):
    out = np.full(len(tr), np.nan)
    out[p - 1] = tr[:p].mean()
    for i in range(p, len(tr)):
        out[i] = (out[i - 1] * (p - 1) + tr[i]) / p
    return out

s = pd.Series(tr)
atr_wilder = wilder(tr, period)
atr_sma = s.rolling(period).mean().values
atr_ema = s.ewm(span=period, adjust=False).mean().values

def rep(name, a):
    a = a[~np.isnan(a)]
    print(f"  {name:<14} mean={a.mean():.4f}  median={np.median(a):.4f}")

print("=" * 66)
print("同一份 M15 数据上三种 ATR(14) 的均值")
print("=" * 66)
rep("Wilder (RMA)", atr_wilder)
rep("SMA(TR,14)", atr_sma)
rep("EMA(TR,14)", atr_ema)

print()
print("=" * 66)
print("对照")
print("=" * 66)
print(f"  Python 隐含 ATR = 6.6211")
print(f"  EA     隐含 ATR = 6.1063")
print(f"  EA/Python       = {6.1063/6.6211:.4f}  (EA 比 Python 小 {(1-6.1063/6.6211)*100:.1f}%)")
w, sm = atr_wilder[~np.isnan(atr_wilder)].mean(), atr_sma[~np.isnan(atr_sma)].mean()
print()
print(f"  SMA/Wilder 比值 = {sm/w:.4f}  (SMA 比 Wilder {(sm/w-1)*100:+.1f}%)")
print(f"  EMA/Wilder 比值 = {atr_ema[~np.isnan(atr_ema)].mean()/w:.4f}")
