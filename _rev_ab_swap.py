# -*- coding: utf-8 -*-
"""
use_reversal_filter A/B — 含 TMGM 隔夜费(swap)重跑 (备忘录 §4 第 2 项)

目的：在 run_backtest 已内置的 TMGM 隔夜费模型(策略 _charge_swap,
swap_long_points=-72.5 / swap_short_points=+30.72 / 周三3倍 / 服务器零点收取)
之上，确认 rev_in_strong(仅强趋势段开启反转过滤)在"含 swap"下仍最优。

背景：scalp 画像默认 avoid_overnight=True，会在 server_close_hour-2 (=20 服务器时)
强制平掉非强趋势/非盈利持仓，故真实 scalp 几乎不跨过服务器零点 -> swap≈0。
为真正检验 swap 影响，本脚本对两种隔夜设置都跑：
  avoid_overnight=True  (真实 scalp 行为, swap 应≈0)
  avoid_overnight=False (持仓过夜, swap 真实生效, 作为压力情景)

4 模式 × 2 隔夜设置，均在 TEST 窗口(2025-06-01~2026-08-29) 跑，起始 $1000/0.01 手。
阈值 5.1e-5 = MA96 20-bar 相对斜率绝对值, Round6 训练段(2023-2025)校准。
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import box_channel_optimized as bc

TEST_FROM, TEST_TO = "2025-06-01", "2026-08-29"
THRESH = 5.1e-5  # Round6 训练段校准
MODES = ["always_on", "always_off", "rev_in_weak", "rev_in_strong"]
LABEL = {
    "always_on": "always_on(常开,当前EA)",
    "always_off": "always_off(常关)",
    "rev_in_weak": "rev_in_weak(仅弱趋势开)",
    "rev_in_strong": "rev_in_strong(仅强趋势开)",
}

base = dict(bc.PROFILES["scalp"])


def run_mode(mode, avoid_overnight):
    extra = dict(base)
    extra["rev_mode"] = mode
    extra["rev_strong_thresh"] = THRESH
    extra["avoid_overnight"] = avoid_overnight
    m = bc.run_backtest(
        freq="15m", extra_args=extra, cash=1000.0, leverage=1000.0,
        lot=0.01, spread=0.33, fromdate=TEST_FROM, todate=TEST_TO, quiet=True,
    )
    return dict(
        mode=mode, avoid_overnight=avoid_overnight,
        total_ret=m["total_ret"], pf=m["pf"], maxdd=m["maxdd"],
        sharpe=m["sharpe"], closed=m["closed"], win_rate=m["win_rate"],
        swap_total=m["swap_total"],
    )


results = []
for overnight in (True, False):
    for mode in MODES:
        r = run_mode(mode, overnight)
        results.append(r)
        print(f"[overnight={overnight}] {LABEL[mode]:<22} "
              f"ret={r['total_ret']*100:+.2f}% PF={r['pf']:.3f} "
              f"DD={r['maxdd']:.2f}% swap={r['swap_total']:+.2f}USD "
              f"trades={r['closed']} win={r['win_rate']*100:.1f}%")

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_rev_ab_swap_result.json"), "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print("\n已写出 _rev_ab_swap_result.json")
