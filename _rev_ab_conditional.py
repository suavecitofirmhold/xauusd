# -*- coding: utf-8 -*-
"""第二步：反转过滤条件化开关的 walk-forward 验证。
不修改生产文件：用子类 ConditionalRevStrategy 覆盖 want_entry，
按 regime 动态决定 use_reversal_filter 是否生效。
模式:
  weak_ma96             : MA96 相对斜率弱(|rel|<thr) 时启用过滤
  high_vol              : ATR/ATR均值 > mult 时启用过滤
  weak_ma96_or_high_vol : 二者任一满足即启用 (用户原提议)
基线(常开/常关)沿用 _rev_ab_walkforward_result.json，本脚本只跑 3 个条件模式。
"""
import os, sys, json
import numpy as np
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import box_channel_optimized as bc

FOLDS = [
    ("F1 23H2", "2023-09-13", "2024-03-31"),
    ("F2 24H1", "2024-04-01", "2024-09-30"),
    ("F3 24H2", "2024-10-01", "2025-03-31"),
    ("F4 25H1", "2025-04-01", "2025-09-30"),
    ("F5 25H2", "2025-10-01", "2026-03-31"),
    ("F6 26H1", "2026-04-01", "2026-08-29"),
]
MODES = ["weak_ma96", "high_vol", "weak_ma96_or_high_vol"]


class ConditionalRevStrategy(bc.BoxChannelOptStrategy):
    params = dict(
        rev_mode="always_on",
        rev_weak_ma_thresh=0.0003,   # |MA96 相对斜率| 低于此=弱趋势 -> 启用过滤
        rev_highvol_mult=1.2,        # atr/atr_avg 高于此=高波动 -> 启用过滤(对齐策略既有 atr_regime_mult)
    )

    def _ma96_rel_slope(self):
        n = 20
        if len(self.ma_tf) < n + 1:
            return 0.0
        y = np.array(self.ma_tf.get(size=n), dtype=float)
        x = np.arange(n)
        slope = np.polyfit(x, y, 1)[0]
        mid = float(np.mean(y))
        return slope / mid if mid > 0 else 0.0

    def _effective_rev_filter(self):
        m = self.p.rev_mode
        if m == "always_on":
            return True
        if m == "always_off":
            return False
        weak = abs(self._ma96_rel_slope()) < self.p.rev_weak_ma_thresh
        if m == "weak_ma96":
            return weak
        atr_now = self.atr[0]
        atr_avg = self.atr_avg[0]
        highvol = (atr_avg > 0 and atr_now / atr_avg > self.p.rev_highvol_mult)
        if m == "high_vol":
            return highvol
        if m == "weak_ma96_or_high_vol":
            return weak or highvol
        return True

    def want_entry(self, channel, support, resistance):
        eff = self._effective_rev_filter()
        if self.p.entry_style != "box":
            return super().want_entry(channel, support, resistance)
        o, h, l, c = self.data.open[0], self.data.high[0], self.data.low[0], self.data.close[0]
        rev = self.detect_reversal(support, resistance)
        if channel == "up" and (not eff or rev["long"]) and l <= support <= c:
            return "long"
        if channel == "down" and (not eff or rev["short"]) and c <= resistance <= h:
            return "short"
        return None


# 让 bc.run_backtest 使用子类（其内部按模块全局名查找策略类）
bc.BoxChannelOptStrategy = ConditionalRevStrategy


def run_one(mode, f, t):
    extra = dict(bc.PROFILES["scalp"])
    extra["rev_mode"] = mode
    m = bc.run_backtest(freq="15m", extra_args=extra,
                        cash=1000.0, leverage=1000.0, lot=0.01,
                        spread=0.33, commission=0.0,
                        fromdate=pd.Timestamp(f), todate=pd.Timestamp(t),
                        quiet=True)
    return dict(
        mode=mode,
        total_ret=round(m["total_ret"] * 100, 2),
        maxdd=round(m["maxdd"], 2),
        sharpe=round(m["sharpe"], 3) if m["sharpe"] == m["sharpe"] else None,
        closed=m["closed"],
        win_rate=round(m["win_rate"] * 100, 1),
        pf=round(m["pf"], 3) if m["pf"] not in (float("inf"),) else None,
    )


if __name__ == "__main__":
    all_res = []
    print("\n" + "=" * 104)
    for fname, f, t in FOLDS:
        row = dict(fold=fname, from_=f, to_=t, results=[])
        print(f"[{fname}] " + "-" * 80)
        for mode in MODES:
            r = run_one(mode, f, t)
            row["results"].append(r)
            pf = 'inf' if r["pf"] is None else f'{r["pf"]:.3f}'
            sh = 'nan' if r["sharpe"] is None else f'{r["sharpe"]:.3f}'
            print(f"  {mode:<22} 收益={r['total_ret']:>7.2f}%  PF={pf:>7}  DD={r['maxdd']:>6.2f}%  "
                  f"Sharpe={sh:>6}  笔数={r['closed']:>4}  胜率={r['win_rate']:>5.1f}%")
        all_res.append(row)
    with open(os.path.join(HERE, "_rev_ab_conditional_result.json"), "w", encoding="utf-8") as fp:
        json.dump(all_res, fp, ensure_ascii=False, indent=2)
    print("=" * 104)
    print("saved -> _rev_ab_conditional_result.json")
