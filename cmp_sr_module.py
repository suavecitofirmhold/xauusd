# -*- coding: utf-8 -*-
"""
Stage 2: 新支撑压力位模块 vs 原 cluster_levels 基线对比
========================================================
- 基线: scalp 画像 + sr_source=None (原 15m/1h histogram 聚类)
- 变体: scalp 画像 + sr_source in {1h, daily, monthly, combined} (新枢轴模块)
- 切分: IS = 2024-03-01~2025-06-30 (训练/选档), OOS = 2025-07-01~2026-08-31 (仅验证)
- 决策: 仅当某变体 OOS 全面优于基线(收益/PF/回撤/calmar)才采纳, 否则保持原样。
- 注意: 不调参选档, 避免 OOS 过拟合; 选档只看 OOS。
"""
import sys
sys.path.insert(0, r"D:\workbuddy\tushare\XAUUSD\xauusd策略")
from box_channel_optimized import run_backtest, PROFILES

IS = ("2024-03-01", "2025-06-30")
OOS = ("2025-07-01", "2026-08-31")

# 各 timeframe 构建参数(Stage 1 设定的合理默认, 非 ML 调参; monthly 须放宽 min_pivots)
SR_BUILD_KW = {
    "1h": dict(sw=3, cluster_pct=0.0015, consec_cap=60, min_pivots=2),
    "daily": dict(sw=2, cluster_pct=0.0020, consec_cap=40, min_pivots=2),
    "monthly": dict(sw=2, cluster_pct=0.0050, consec_cap=24, min_pivots=1),
}

CONFIGS = [
    ("baseline", None),
    ("sr_1h", "1h"),
    ("sr_daily", "daily"),
    ("sr_monthly", "monthly"),
    ("sr_combined", "combined"),
]


def run_cfg(name, sr_source, window):
    extra = dict(PROFILES["scalp"])
    if sr_source is not None:
        extra["sr_source"] = sr_source
        extra["sr_build_kw"] = SR_BUILD_KW
    m = run_backtest(freq="15m", extra_args=extra,
                     fromdate=window[0], todate=window[1], quiet=True)
    calmar = (m["ann"] / m["maxdd"] * 100.0) if m["maxdd"] > 0 else float("inf")
    ret_dd = (m["total_ret"] / m["maxdd"] * 100.0) if m["maxdd"] > 0 else float("inf")
    return dict(name=name, sr=sr_source, window=window,
                ret=m["total_ret"] * 100, ann=m["ann"] * 100, dd=m["maxdd"],
                sharpe=m["sharpe"], pf=m["pf"], trades=m["closed"],
                win=m["win_rate"] * 100, entries=m["entries"],
                calmar=calmar, ret_dd=ret_dd)


def fmt(r):
    pf = f"{r['pf']:.2f}" if r["pf"] != float("inf") else "inf"
    cal = f"{r['calmar']:.2f}" if r["calmar"] != float("inf") else "inf"
    return (f"{r['name']:11s} {r['window'][0]}~{r['window'][1]} | "
            f"ret={r['ret']:+7.2f}% ann={r['ann']:+7.2f}% DD={r['dd']:5.2f}% "
            f"PF={pf:>4s} Sharpe={r['sharpe']:5.2f} trades={r['trades']:4d} "
            f"win={r['win']:5.1f}% calmar={cal:>6s} ret/DD={r['ret_dd']:.1f}")


def main():
    print("=" * 100)
    print("Stage 2: 支撑压力位模块 vs 原基线 (scalp 画像, IS/OOS)")
    print("=" * 100)
    results = []
    for wname, window in (("IS", IS), ("OOS", OOS)):
        print(f"\n##### 窗口 {wname}: {window[0]} ~ {window[1]} #####")
        for name, sr in CONFIGS:
            r = run_cfg(name, sr, window)
            results.append(r)
            print(fmt(r))
    # 汇总对比表
    print("\n" + "=" * 100)
    print("汇总 (OOS 决策依据):")
    print("=" * 100)
    base_oos = {r["name"]: r for r in results if r["window"] == OOS and r["name"] == "baseline"}
    bo = base_oos["baseline"]
    print(f"基线 OOS: ret={bo['ret']:+.2f}% PF={bo['pf']:.2f} DD={bo['dd']:.2f}% calmar={bo['calmar']:.2f} trades={bo['trades']}")
    print("-" * 100)
    for name, sr in CONFIGS:
        if name == "baseline":
            continue
        r = {x["name"]: x for x in results if x["window"] == OOS}[name]
        better = (r["ret"] >= bo["ret"] and r["pf"] >= bo["pf"]
                  and r["dd"] <= bo["dd"] and r["calmar"] >= bo["calmar"]
                  and r["trades"] >= 20)
        verdict = "✅ 全面优于基线(建议采纳)" if better else "❌ 未全面优于基线(保持原样)"
        print(f"{name:11s}: ret={r['ret']:+.2f}% PF={r['pf']:.2f} DD={r['dd']:.2f}% "
              f"calmar={r['calmar']:.2f} trades={r['trades']} -> {verdict}")


if __name__ == "__main__":
    main()
