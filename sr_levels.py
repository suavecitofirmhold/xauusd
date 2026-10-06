# -*- coding: utf-8 -*-
"""
支撑压力位模块 (Support / Resistance levels)
=============================================

设计目标 (对应 Stage 1 要求):
  (1) 计算 短期(1h) / 中期(日) / 远期(月, 由日线重采样) 三档支撑压力位,
      并据"所处位置的强弱程度"打分 —— 核心规则: 多根连续蜡烛线都未突破
      (即该价位被反复测试却始终未被有效击穿) 的评分, 高于仅被单根/少数触及的价位。
  (2) 提供样本数据统计接口 (反弹率 / 突破率) 以验证支撑压力位的"准确度",
      并支持参数调节 (swing 窗口 / 聚类容差 / 接近容差)。

核心方法: 枢轴点 (Swing High / Swing Low) + 邻近价格聚类 + 强度打分。
  - pivot: 在 left=right=sw 的窗口内, 最高/最低点即一个候选极值。
  - cluster: 价位相近 (|Δ|/price < cluster_pct) 的枢轴合并成一个"水平",
             level 价 = 簇内枢轴中位价, 簇内枢轴数 = touches 初值。
  - strength: 0~100, 由三项合成 (权重可调):
        a) 簇内枢轴数 (被测试次数越多越强)
        b) 最长连续未突破根数 (用户强调的指标: 连续越多越强)
        c) 新鲜度 (越近被尊重越强)

本文件既可 `python sr_levels.py` 独立跑 Stage 1 报告, 也可被 box 策略导入:
  from sr_levels import SRLevelsProvider
  prov = SRLevelsProvider.build_from_csv("1h")   # 或 "daily" / "monthly"
  sup, res, sup_s, res_s = prov.nearest_as_of(dt)  # dt 为当前 15m bar 时间

⚠️ 全部只用"历史"数据: pivot/聚类/强度均基于截至当前 bar 的过去序列;
   nearest_as_of 用 .asof() 取 <= 当前时间的最新水平, 不引入未来函数。
"""

import os
import numpy as np
import pandas as pd

CLEAN_DIR = r"D:\Data\stockdata\xauusd\clean"

# 默认参数 (Stage 1 扫参会围绕这些微调)
DEFAULTS = dict(
    sw=3,            # swing 窗口 (左=右), 1h 默认 3
    cluster_pct=0.0015,   # 聚类容差 (相对价)
    tol=0.0010,      # 接近/未突破判定容差 (相对价)
    consec_cap=60,   # 连续未突破根数归一上限 (按该 timeframe 的 bar 数)
    recency_cap=120, # 新鲜度归一上限 (bar 数)
    min_pivots=2,    # 成簇最少枢轴数
    bounce_atr=1.0,  # 验证: 反弹判定 = 离开 >= bounce_atr*ATR
    break_atr=1.0,   # 验证: 突破判定 = 击穿 >= break_atr*ATR
)


# --------------------------------------------------------------------------
# 数据加载
# --------------------------------------------------------------------------
def load_tf(freq):
    """加载 clean/xauusd_{freq}_utc.csv -> DataFrame(索引 datetime)。"""
    path = os.path.join(CLEAN_DIR, f"xauusd_{freq}_utc.csv")
    df = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()
    return df


def monthly_from_daily():
    """由日线重采样出月线 OHLC (远期支撑压力位用, 无需额外数据)。"""
    d = load_tf("daily")
    m = d.resample("MS").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    m = m.dropna(subset=["open", "high", "low", "close"])
    return m


# --------------------------------------------------------------------------
# 枢轴点检测
# --------------------------------------------------------------------------
def detect_pivots(df, sw):
    """返回 (high_idx, high_price, low_idx, low_price) numpy 数组。
    pivot 定义: 在 [i-sw, i+sw] 窗口内 high[i] 为最大(高枢轴) / low[i] 为最小(低枢轴)。"""
    high = df["high"].to_numpy()
    low = df["low"].to_numpy()
    n = len(df)
    hi_idx, hi_px, lo_idx, lo_px = [], [], [], []
    for i in range(sw, n - sw):
        w_h = high[i - sw:i + sw + 1]
        w_l = low[i - sw:i + sw + 1]
        if high[i] >= w_h.max() and high[i] > high[i - 1]:   # 严格最大, 避免平台重复
            hi_idx.append(i); hi_px.append(high[i])
        if low[i] <= w_l.min() and low[i] < low[i - 1]:
            lo_idx.append(i); lo_px.append(low[i])
    return (np.array(hi_idx), np.array(hi_px),
            np.array(lo_idx), np.array(lo_px))


# --------------------------------------------------------------------------
# 聚类成水平 + 强度打分
# --------------------------------------------------------------------------
def _cluster_side(px, idx, times, kind, sw, cluster_pct, tol, consec_cap, recency_cap, min_pivots):
    """对单侧 (支撑=low枢轴 / 压力=high枢轴) 做价格聚类, 返回水平列表 dict。"""
    levels = []
    if len(px) == 0:
        return levels
    order = np.argsort(px)
    px = px[order]; idx = idx[order]   # 仅重排价/索引, times 保持 df 全量索引(用绝对索引取时间)
    i = 0
    n = len(px)
    while i < n:
        j = i
        cluster_pxs = [px[i]]
        cluster_idx = [idx[i]]
        while j + 1 < n and abs(px[j + 1] - px[i]) / px[i] < cluster_pct:
            j += 1
            cluster_pxs.append(px[j]); cluster_idx.append(idx[j])
        # 簇中心价 = 中位价
        price = float(np.median(cluster_pxs))
        first_i = int(cluster_idx[0])
        last_i = int(cluster_idx[-1])
        # 连续未突破根数: 从簇最后一根起, 向后扫直到被突破
        max_consec = _consec_unbroken(kind, price, last_i, sw, tol)
        # 新鲜度: 距最后触及的 bar 数 (越小越新)
        recency = _recency_score(last_i, recency_cap)
        pivots = len(cluster_pxs)
        if pivots >= min_pivots:
            strength = _strength(pivots, max_consec, recency, consec_cap)
            levels.append(dict(
                price=price, kind=kind, pivots=pivots,
                max_consec=max_consec, recency=recency,
                first_i=first_i, last_i=last_i,
                first_t=times[first_i], last_t=times[last_i],
                strength=strength,
            ))
        i = j + 1
    return levels


def _consec_unbroken(kind, price, from_i, sw, tol):
    """从 from_i (簇最后一根, 含) 向后数连续未突破根数。
    kind='support': 被突破 = low < price*(1-tol); 'resistance': high > price*(1+tol)。"""
    # 需要全局序列, 用闭包外的缓存
    arr = _CONSEC_CACHE
    low = arr["low"]; high = arr["high"]; n = len(low)
    cnt = 0
    for k in range(from_i, n):
        if kind == "support":
            if low[k] < price * (1 - tol):
                break
        else:
            if high[k] > price * (1 + tol):
                break
        cnt += 1
    return cnt


def _recency_score(last_i, recency_cap):
    return min(last_i / recency_cap, 1.0)  # 越靠近序列末端(越大)越新


def _strength(pivots, max_consec, recency, consec_cap):
    s_piv = min(pivots / 5.0, 1.0)            # 5 次测试封顶
    s_con = min(max_consec / consec_cap, 1.0)  # 连续未突破根数 (用户强调)
    s_rec = recency
    raw = 0.35 * s_piv + 0.50 * s_con + 0.15 * s_rec
    return round(100.0 * max(0.0, min(raw, 1.0)), 1)


_CONSEC_CACHE = {}


def build_levels(df, sw=3, cluster_pct=0.0015, tol=0.0010,
                 consec_cap=60, recency_cap=120, min_pivots=2):
    """对单个 timeframe df 计算全部支撑/压力水平 (含强度)。返回 list[dict]。"""
    global _CONSEC_CACHE
    _CONSEC_CACHE = dict(
        high=df["high"].to_numpy(), low=df["low"].to_numpy(),
        n=len(df),
    )
    times = df.index.to_numpy()
    hi_idx, hi_px, lo_idx, lo_px = detect_pivots(df, sw)
    sup = _cluster_side(lo_px, lo_idx, times, "support", sw, cluster_pct, tol, consec_cap, recency_cap, min_pivots)
    res = _cluster_side(hi_px, hi_idx, times, "resistance", sw, cluster_pct, tol, consec_cap, recency_cap, min_pivots)
    levels = sup + res
    levels.sort(key=lambda x: x["last_t"])
    return levels


# --------------------------------------------------------------------------
# 准确度验证: 接近后反弹率 vs 突破率
# --------------------------------------------------------------------------
def validate_levels(df, levels, tol=0.0010, bounce_atr=1.0, break_atr=1.0, atr_n=14):
    """对每档水平统计: 当价格接近(<=tol)该水平时, 后续是反弹还是突破。
    返回 results 列表, 每元素含 level + bounce/break 计数。
    向量化"接近事件"检测, 仅对稀疏事件做反应判定, 速度较逐水平扫全序列快几个数量级。"""
    close = df["close"].to_numpy(); high = df["high"].to_numpy(); low = df["low"].to_numpy()
    n = len(df)
    tr = np.empty(n); tr[0] = high[0] - low[0]
    for i in range(1, n):
        tr[i] = max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1]))
    atr = pd.Series(tr).rolling(atr_n, min_periods=1).mean().to_numpy()

    recs = []
    for lv in levels:
        p = lv["price"]; kind = lv["kind"]; start = lv["last_i"]
        if start >= n - 1:
            recs.append(dict(price=p, kind=kind, strength=lv["strength"], pivots=lv["pivots"],
                             bounces=0, breaks=0, total=0, bounce_rate=np.nan))
            continue
        c = close[start:]; h = high[start:]; l = low[start:]; m = len(c)
        if kind == "support":
            near = (np.abs(c - p) / p < tol) | (np.abs(l - p) / p < tol)
        else:
            near = (np.abs(c - p) / p < tol) | (np.abs(h - p) / p < tol)
        nidx = np.where(near)[0]
        bounces = 0; breaks = 0
        for off in nidx:
            gi = start + int(off)
            L = min(n, gi + 20)
            moved = False
            for j in range(gi + 1, L):
                aa = atr[j]
                if aa <= 0:
                    continue
                if kind == "support":
                    if close[j] - p >= bounce_atr * aa:
                        bounces += 1; moved = True; break
                    if low[j] < p - break_atr * aa:
                        breaks += 1; moved = True; break
                else:
                    if p - close[j] >= bounce_atr * aa:
                        bounces += 1; moved = True; break
                    if high[j] > p + break_atr * aa:
                        breaks += 1; moved = True; break
        total = bounces + breaks
        rate = bounces / total if total > 0 else np.nan
        recs.append(dict(price=p, kind=kind, strength=lv["strength"],
                         pivots=lv["pivots"], bounces=bounces, breaks=breaks,
                         total=total, bounce_rate=rate))
    return recs


def summarize_validation(recs, label=""):
    """按强度分桶汇总反弹率, 并给总体 (只在 total>=3 的水平上统计, 避免噪声)。"""
    use = [r for r in recs if r["total"] >= 3]
    if not use:
        return dict(label=label, n_levels=len(recs), n_valid=0,
                    overall_bounce=np.nan, strong_bounce=np.nan, weak_bounce=np.nan,
                    n_strong=0, n_weak=0)
    def br(rs):
        t = sum(r["total"] for r in rs); b = sum(r["bounces"] for r in rs)
        return b / t if t else np.nan
    strong = [r for r in use if r["strength"] >= 60]
    weak = [r for r in use if r["strength"] < 60]
    return dict(
        label=label, n_levels=len(recs), n_valid=len(use),
        overall_bounce=round(br(use), 3),
        strong_bounce=round(br(strong), 3) if strong else np.nan,
        weak_bounce=round(br(weak), 3) if weak else np.nan,
        n_strong=len(strong), n_weak=len(weak),
    )


# --------------------------------------------------------------------------
# Stage 2 集成用: 每根 bar 的"最近支撑/压力 + 强度"查表
# --------------------------------------------------------------------------
class SRLevelsProvider:
    """给定 timeframe 的水平集合, 构建 per-bar 的 nearest support/resistance 查表。
    nearest_as_of(dt, price) 用单调游标 + 二分, 取 <= dt 的最新水平 (无未来函数),
    并在该 bar 已形成的水平中按 price 二分取"下方最近支撑/上方最近压力"。
    性能: 每根 15m bar 仅 O(log 活跃水平), 1h×10万 bar 也可承受。"""

    def __init__(self, freq, levels, df_index):
        self.freq = freq
        self.levels = levels
        self.df_index = df_index
        self._idx_vals = np.array([pd.Timestamp(t).value for t in df_index])  # int64 便于游标搜索
        self._sup_prices = []   # 每根 TF bar: 已形成的支撑价(升序 numpy)
        self._sup_str = []
        self._res_prices = []
        self._res_str = []
        self._cursor = 0
        self._build_table()

    @classmethod
    def build_from_csv(cls, freq, **kw):
        if freq == "monthly":
            df = monthly_from_daily()
        else:
            df = load_tf(freq)
        # 各 timeframe 的合理 consec_cap (连续未突破根数上限); kw 若已带则优先
        cap = {"1h": 60, "daily": 40, "monthly": 24}.get(freq, 60)
        kw2 = dict(kw); kw2.setdefault("consec_cap", cap)
        levels = build_levels(df, **kw2)
        return cls(freq, levels, df.index)

    def _build_table(self):
        import bisect
        by_time = {}
        for lv in self.levels:
            by_time.setdefault(lv["last_t"], []).append(lv)
        n = len(self.df_index)
        run_sup = []  # (price, strength) 升序
        run_res = []
        for bi in range(n):
            t = self.df_index[bi]
            for lv in by_time.get(t, []):
                entry = (lv["price"], lv["strength"])
                if lv["kind"] == "support":
                    bisect.insort(run_sup, entry)
                else:
                    bisect.insort(run_res, entry)
            self._sup_prices.append(np.array([p for p, _ in run_sup], dtype=float))
            self._sup_str.append(np.array([s for _, s in run_sup], dtype=float))
            self._res_prices.append(np.array([p for p, _ in run_res], dtype=float))
            self._res_str.append(np.array([s for _, s in run_res], dtype=float))

    def _tf_idx(self, dt):
        """返回最后一个 <= dt 的 TF bar 索引(单调游标, dt 递增时 O(1)均摊)。"""
        val = pd.Timestamp(dt).value
        i = self._cursor
        arr = self._idx_vals
        while i < len(arr) and arr[i] <= val:
            i += 1
        i -= 1
        if i < 0:
            return -1
        self._cursor = i
        return i

    def nearest_as_of(self, dt, price):
        """返回 (support_price, resistance_price, sup_strength, res_strength)。
        price = 当前 15m close (判定上下方)。无可用水平时返回 None。"""
        bi = self._tf_idx(dt)
        if bi < 0:
            return None, None, 0.0, 0.0
        sup_p = self._sup_prices[bi]; sup_s = self._sup_str[bi]
        res_p = self._res_prices[bi]; res_s = self._res_str[bi]
        best_sup = best_sup_s = None
        if sup_p.size:
            j = int(np.searchsorted(sup_p, price)) - 1   # 最后一个 < price
            if j >= 0:
                best_sup = float(sup_p[j]); best_sup_s = float(sup_s[j])
        best_res = best_res_s = None
        if res_p.size:
            j = int(np.searchsorted(res_p, price))        # 第一个 >= price
            if j < res_p.size:
                best_res = float(res_p[j]); best_res_s = float(res_s[j])
        return best_sup, best_res, (best_sup_s or 0.0), (best_res_s or 0.0)


# --------------------------------------------------------------------------
# Stage 1 主流程: 计算 + 验证 + 扫参
# --------------------------------------------------------------------------
def run_stage1(sweep=False, out_path=None):
    print("=" * 70)
    print("Stage 1: 支撑压力位模块 — 计算 / 验证 / 参数调节")
    print("=" * 70)
    freqs = {"1h": load_tf("1h"), "daily": load_tf("daily"), "monthly": monthly_from_daily()}
    # 各 timeframe 的合理构建参数 (月度样本仅37根, 须放宽 min_pivots/容差)
    build_kw = {
        "1h": dict(sw=3, cluster_pct=0.0015, consec_cap=60, min_pivots=2),
        "daily": dict(sw=2, cluster_pct=0.0020, consec_cap=40, min_pivots=2),
        "monthly": dict(sw=2, cluster_pct=0.0050, consec_cap=24, min_pivots=1),
    }

    reports = {}
    # ---- (1) 默认参数计算 + 验证 ----
    for freq, df in freqs.items():
        kw = build_kw[freq]
        levels = build_levels(df, **kw)
        recs = validate_levels(df, levels)
        summ = summarize_validation(recs, label=freq)
        reports[freq] = (levels, recs, summ)
        n_sup = sum(1 for l in levels if l["kind"] == "support")
        n_res = sum(1 for l in levels if l["kind"] == "resistance")
        print(f"\n[{freq}] bars={len(df)} 水平数={len(levels)} (支撑{n_sup}/压力{n_res})")
        print(f"   有效水平(total>=3)={summ['n_valid']}  总体反弹率={summ['overall_bounce']}")
        print(f"   强水平(>=60)反弹率={summ['strong_bounce']} (n={summ['n_strong']}) | "
              f"弱水平反弹率={summ['weak_bounce']} (n={summ['n_weak']})")

    # ---- (2) 参数调节 (swing窗口 + 聚类容差), 仅对 1h/daily 有统计意义 ----
    if sweep:
        print("\n" + "-" * 70)
        print("参数扫掠 (目标: 强水平反弹率最高且稳定; 月度样本过少不参与)")
        grid = []
        for sw in (2, 3, 4):
            for cp in (0.0010, 0.0015, 0.0020):
                row = dict(sw=sw, cluster_pct=cp)
                rates = []
                for freq in ("1h", "daily"):
                    kw = dict(build_kw[freq]); kw.update(sw=sw, cluster_pct=cp)
                    levels = build_levels(freqs[freq], **kw)
                    recs = validate_levels(freqs[freq], levels)
                    summ = summarize_validation(recs, label=freq)
                    rates.append(summ["strong_bounce"] if summ["strong_bounce"] == summ["strong_bounce"] else 0)
                row["mean_strong_bounce"] = round(float(np.nanmean(rates)), 3)
                grid.append(row)
                print(f"   sw={sw} cluster_pct={cp} -> 强水平平均反弹率={row['mean_strong_bounce']}")
        best = max(grid, key=lambda r: r["mean_strong_bounce"])
        print(f"\n★ 推荐参数: sw={best['sw']}, cluster_pct={best['cluster_pct']} "
              f"(平均强水平反弹率={best['mean_strong_bounce']})")
        reports["best_params"] = best

    # 保存报告
    if out_path:
        import json
        dump = {k: v[2] for k, v in reports.items() if k in ("1h", "daily", "monthly")}
        if "best_params" in reports:
            dump["best_params"] = reports["best_params"]
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(dump, f, ensure_ascii=False, indent=2, default=str)
        print(f"\n报告已存: {out_path}")
    return reports


if __name__ == "__main__":
    import sys
    do_sweep = "--sweep" in sys.argv
    run_stage1(sweep=do_sweep,
               out_path=os.path.join(os.path.dirname(__file__), "sr_stage1_report.json"))
