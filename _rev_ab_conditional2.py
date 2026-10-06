# -*- coding: utf-8 -*-
"""Round 5: 条件化反转过滤的修正 walk-forward 验证。
修正 Step-2(_rev_ab_conditional.py) 的两处退化:
  (1) weak_ma96 阈值 0.0003 过低 -> |slope|<0.0003 几乎恒真 -> 退化成常开(ON)。
      本轮改用【全样本校准】阈值 = median(|MA96 相对斜率|), 使"弱趋势"成为真实少数段。
  (2) high_vol 与策略既有 atr_regime_gate 撞车 -> 高波动段策略已禁交易 -> 退化成常关(OFF)。
      本轮改绑与交易共存的 regime 轴(MA96 斜率强弱), 并加互补模式 rev_in_strong
      以隔离"过滤到底在哪个 regime 有用"。
模式(均复用 scalp 画像):
  always_on      : 全段开过滤 (= 原 ON 基线 / EA 默认)
  always_off     : 全段关过滤 (= 原 OFF 基线)
  rev_in_weak    : 仅 |slope|<thr(弱趋势/震荡) 开过滤
  rev_in_strong  : 仅 |slope|>=thr(强趋势) 开过滤
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
MODES = ["always_on", "always_off", "rev_in_weak", "rev_in_strong"]


class ConditionalRevStrategy2(bc.BoxChannelOptStrategy):
    params = dict(
        rev_mode="always_on",
        rev_weak_ma_thresh=0.0,   # 由 calibrate_threshold() 全样本 median 注入
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
        if m == "rev_in_weak":
            return weak
        if m == "rev_in_strong":
            return not weak
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
bc.BoxChannelOptStrategy = ConditionalRevStrategy2


def calibrate_threshold():
    """全样本 15m -> EMA(close,96) -> 每根 |rel_slope|, 取 median 作为弱/强分界。"""
    df, _, _ = bc.load_data("15m")   # 全样本(无 fromdate/todate)
    close = df["close"].values.astype(float)
    ma = pd.Series(close).ewm(span=96, adjust=False).mean().values
    n = 20
    slopes = []
    for i in range(n, len(ma)):
        y = ma[i - n:i]
        s = np.polyfit(np.arange(n), y, 1)[0]
        mid = float(np.mean(y))
        if mid > 0:
            slopes.append(abs(s / mid))
    return float(np.median(np.array(slopes)))


def run_one(mode, thr, f, t):
    extra = dict(bc.PROFILES["scalp"])
    extra["rev_mode"] = mode
    extra["rev_weak_ma_thresh"] = thr
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
        pf=round(m["pf"], 3),
    )


if __name__ == "__main__":
    thr = calibrate_threshold()
    print(f"[calibrate] median(|MA96 rel slope|) = {thr:.6f}  -> 弱趋势=底50%段")
    all_res = []
    print("\n" + "=" * 110)
    for fname, f, t in FOLDS:
        row = dict(fold=fname, from_=f, to_=t, threshold=round(thr, 6), results=[])
        print(f"[{fname}] thr={thr:.5f} " + "-" * 78)
        for mode in MODES:
            r = run_one(mode, thr, f, t)
            row["results"].append(r)
            pf = 'inf' if r["pf"] is None else f'{r["pf"]:.3f}'
            sh = 'nan' if r["sharpe"] is None else f'{r["sharpe"]:.3f}'
            print(f"  {mode:<14} 收益={r['total_ret']:>7.2f}%  PF={pf:>7}  DD={r['maxdd']:>6.2f}%  "
                  f"Sharpe={sh:>6}  笔数={r['closed']:>4}  胜率={r['win_rate']:>5.1f}%")
        all_res.append(row)
    with open(os.path.join(HERE, "_rev_ab_conditional2_result.json"), "w", encoding="utf-8") as fp:
        json.dump(all_res, fp, ensure_ascii=False, indent=2)
    print("=" * 110)
    print("saved -> _rev_ab_conditional2_result.json")
