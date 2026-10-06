# -*- coding: utf-8 -*-
"""留出验证(holdout): 门槛只用训练段(2023-09-13~2025-06-01)校准,
在样本外段(2025-06-01~2026-08-29, 含 F5/F6)验证 rev_in_strong 是否仍优于常开(always_on)。
目的: 排除 Round5 全样本校准的轻度泄漏, 确认 +24pp 优势是真实泛化而非门槛偷看测试折。
"""
import os, sys, json
import numpy as np
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import box_channel_optimized as bc

TRAIN_FROM, TRAIN_TO = "2023-09-13", "2025-06-01"
TEST_FROM, TEST_TO = "2025-06-01", "2026-08-29"
# 细分(均完全在 TEST 内, 无校准重叠): F5 25H2 / F6 26H1
TEST_SUB = [("F5 25H2", "2025-10-01", "2026-03-31"),
            ("F6 26H1", "2026-04-01", "2026-08-29")]
MODES = ["always_on", "always_off", "rev_in_weak", "rev_in_strong"]


class ConditionalRevStrategy3(bc.BoxChannelOptStrategy):
    params = dict(rev_mode="always_on", rev_weak_ma_thresh=0.0)

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


# 让 bc.run_backtest 使用子类
bc.BoxChannelOptStrategy = ConditionalRevStrategy3


def calibrate_threshold_train():
    """仅训练段算 median|MA96 相对斜率| (留出验证的关键: 测试段数据不参与校准)。"""
    df, _, _ = bc.load_data("15m", fromdate=pd.Timestamp(TRAIN_FROM), todate=pd.Timestamp(TRAIN_TO))
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
    return dict(mode=mode,
                total_ret=round(m["total_ret"] * 100, 2),
                maxdd=round(m["maxdd"], 2),
                sharpe=round(m["sharpe"], 3) if m["sharpe"] == m["sharpe"] else None,
                closed=m["closed"], win_rate=round(m["win_rate"] * 100, 1),
                pf=round(m["pf"], 3))


def _print_row(r):
    pf = 'inf' if r["pf"] is None else f'{r["pf"]:.3f}'
    sh = 'nan' if r["sharpe"] is None else f'{r["sharpe"]:.3f}'
    print(f"  {r['mode']:<14} 收益={r['total_ret']:>7.2f}%  PF={pf:>7}  DD={r['maxdd']:>6.2f}%  "
          f"Sharpe={sh:>6}  笔数={r['closed']:>4}  胜率={r['win_rate']:>5.1f}%")


if __name__ == "__main__":
    thr = calibrate_threshold_train()
    print(f"[训练段校准] median(|MA96 相对斜率|) = {thr:.6f}  (对比 Round5 全样本=6.1e-05)")
    all_res = []
    print("\n" + "=" * 100)
    # 整段 TEST (留出集)
    block = dict(scope="TEST(全段留出)", from_=TEST_FROM, to_=TEST_TO,
                 threshold=round(thr, 6), results=[])
    print(f"[TEST 全段 2025-06-01~2026-08-29] thr={thr:.5f}")
    for mode in MODES:
        r = run_one(mode, thr, TEST_FROM, TEST_TO)
        block["results"].append(r)
        _print_row(r)
    all_res.append(block)
    # 细分 F5 / F6 (均完全在测试段内, 无校准重叠)
    for fname, f, t in TEST_SUB:
        row = dict(scope=fname, from_=f, to_=t, threshold=round(thr, 6), results=[])
        print(f"[{fname}]")
        for mode in MODES:
            r = run_one(mode, thr, f, t)
            row["results"].append(r)
            _print_row(r)
        all_res.append(row)
    with open(os.path.join(HERE, "_rev_ab_holdout_result.json"), "w", encoding="utf-8") as fp:
        json.dump(all_res, fp, ensure_ascii=False, indent=2)
    print("=" * 100)
    print("saved -> _rev_ab_holdout_result.json")
