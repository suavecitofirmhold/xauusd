# -*- coding: utf-8 -*-
"""
cooldown 对齐 A/B — 备忘录 §4 (EA↔回测参数对齐) 的 cooldown 决策对拍

背景：EA `InpCooldownBars=8` 与回测 scalp 画像 `cooldown_bars=2` 不一致(实打实 bug)。
用户在 README §三 决定统一到 8(2→8)。本脚本对
  4 反转模式 × cooldown{2,8} × 2 隔夜设置
跑 TEST 窗口(2025-06-01~2026-08-29), 起始 $1000/0.01 手, 验证:
  (1) rev_in_strong 在 cooldown=8 下仍最优(含 swap 压力情景);
  (2) cooldown=8 vs 2 的 rev_in_strong 表现 head-to-head, 支撑"统一到 8"决策。

阈值 5.1e-5 = MA96 20-bar 相对斜率绝对值, Round6 训练段(2023-2025)校准。
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import box_channel_optimized as bc

TEST_FROM, TEST_TO = "2025-06-01", "2026-08-29"
THRESH = 5.1e-5  # Round6 训练段校准
MODES = ["always_on", "always_off", "rev_in_weak", "rev_in_strong"]
LABEL = {
    "always_on": "always_on(常开)",
    "always_off": "always_off(常关)",
    "rev_in_weak": "rev_in_weak(仅弱趋势开)",
    "rev_in_strong": "rev_in_strong(仅强趋势开)",
}
COOLDOWNS = [2, 8]


def run_mode(mode, cooldown, avoid_overnight):
    extra = dict(bc.PROFILES["scalp"])
    extra["rev_mode"] = mode
    extra["rev_strong_thresh"] = THRESH
    extra["avoid_overnight"] = avoid_overnight
    extra["cooldown_bars"] = cooldown
    m = bc.run_backtest(
        freq="15m", extra_args=extra, cash=1000.0, leverage=1000.0,
        lot=0.01, spread=0.33, fromdate=TEST_FROM, todate=TEST_TO, quiet=True,
    )
    return dict(
        mode=mode, cooldown=cooldown, avoid_overnight=avoid_overnight,
        total_ret=m["total_ret"], pf=m["pf"], maxdd=m["maxdd"],
        sharpe=m["sharpe"], closed=m["closed"], win_rate=m["win_rate"],
        swap_total=m["swap_total"],
    )


results = []
for cd in COOLDOWNS:
    for overnight in (True, False):
        for mode in MODES:
            r = run_mode(mode, cd, overnight)
            results.append(r)
            print(f"[cd={cd} overnight={overnight}] {LABEL[mode]:<20} "
                  f"ret={r['total_ret']*100:+.2f}% PF={r['pf']:.3f} "
                  f"DD={r['maxdd']:.2f}% swap={r['swap_total']:+.2f}USD "
                  f"trades={r['closed']} win={r['win_rate']*100:.1f}%")

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "_cooldown_ab_result.json"), "w") as f:
    json.dump(results, f, indent=2, ensure_ascii=False)
print("\n已写出 _cooldown_ab_result.json")
