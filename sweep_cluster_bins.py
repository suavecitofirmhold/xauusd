"""
cluster_bins 灵敏度扫参: 看支撑带宽度(resistance-support) 与 入场频率 如何随桶数变化.
同时给净收益/PF/胜率作为对照.

支撑带 = 15m 短期聚类 densest bucket 的 [support=lows.min, resistance=highs.max].
仅记录 self.data(15m) 那次 cluster_levels 调用; 1h 长周期调用忽略(非入场用带).
"""
import sys, os
import pandas as pd
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import box_channel_optimized as M

FROM = pd.Timestamp("2024-03-04")
TO = pd.Timestamp("2026-08-26")

_all_bands = []   # 每根 bar(触发到聚类计算的 15m) 的支撑带宽度
_entry_bands = [] # 每笔入场时的支撑带宽度


def _install_patch():
    """闭包式 monkey-patch, 捕获原函数, 不依赖实例上的 _orig_* 属性."""
    cls = M.BoxChannelOptStrategy
    orig_cluster = cls.cluster_levels
    orig_open = cls._open_position

    def patched_cluster(self, data, n):
        sup, res, th, tl = orig_cluster(self, data, n)
        if data is self.data and sup is not None and res is not None and res > sup:
            _all_bands.append(res - sup)
            self._cur_band = res - sup
        else:
            if data is self.data:
                self._cur_band = None
        return sup, res, th, tl

    def patched_open(self, want, entry_ref, stop, target, rm, rsi):
        band = getattr(self, "_cur_band", None)
        if band is not None:
            _entry_bands.append(band)
        return orig_open(self, want, entry_ref, stop, target, rm, rsi)

    cls.cluster_levels = patched_cluster
    cls._open_position = patched_open


def run_one(cb):
    global _all_bands, _entry_bands
    _all_bands, _entry_bands = [], []
    extra = {**M.PROFILES["scalp"], "atr_shift": 1, "cluster_bins": cb}
    res = M.run_backtest(freq="15m", extra_args=extra, cash=1000.0,
                         fromdate=FROM, todate=TO, quiet=True)
    n_entries = res["entries"]
    all_b = np.array(_all_bands) if _all_bands else np.array([0.0])
    ent_b = np.array(_entry_bands) if _entry_bands else np.array([0.0])
    # 1 USD = 1 点 (XAUUSD 15m). 给 ATR 占比用入场时 ATR.
    atr_entry = []
    for t in res["trades"]:
        if t["kind"] == "open" and t.get("stop") is not None:
            a = abs(t["stop"] - t["price"]) / max(M.BoxChannelOptStrategy.params.atr_stop_mult, 1e-9)
            atr_entry.append(a)
    atr_avg = np.mean(atr_entry) if atr_entry else 0.0
    return dict(
        cluster_bins=cb,
        entries=n_entries,
        net=res["total_ret"] * 100, pf=res["pf"], wr=res["win_rate"] * 100,
        trades=res["closed"],
        band_all_mean=all_b.mean(), band_all_med=float(np.median(all_b)),
        band_entry_mean=ent_b.mean(), band_entry_med=float(np.median(ent_b)),
        band_entry_p95=float(np.percentile(ent_b, 95)) if len(ent_b) else 0.0,
        atr_avg=atr_avg,
        band_entry_p95_in_atr=(np.percentile(ent_b, 95) / atr_avg) if atr_avg else 0.0,
    )


def main():
    _install_patch()
    print("=" * 90)
    rows = []
    for cb in (10, 20, 30, 40):
        r = run_one(cb)
        rows.append(r)
        print(f"cluster_bins={cb:>2}: 入场 {r['entries']:>4} 笔 | "
              f"带全bar 均={r['band_all_mean']:.2f}/中位={r['band_all_med']:.2f} | "
              f"入场带 均={r['band_entry_mean']:.2f}/p95={r['band_entry_p95']:.2f} "
              f"(p95={r['band_entry_p95_in_atr']:.1f}×ATR) | "
              f"net={r['net']:+.1f}% PF={r['pf']:.2f} WR={r['wr']:.1f}% trades={r['trades']}")
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sweep_cluster_bins.csv")
    pd.DataFrame(rows).to_csv(out, index=False, float_format="%.4f")
    print("\n>> 存:", out)


if __name__ == "__main__":
    main()
