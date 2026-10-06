# -*- coding: utf-8 -*-
"""
XAU/USD 箱体通道策略 · 优化版 (Optimized)
============================================
在 xauusd_box_channel_strategy.py (box 核心) 基础上扩展:

[+] 多入场风格 (entry_style)
    - 'box'       : 原箱体重力(支撑/压力聚类 + 反转 K 线)  [震荡市均值回归]
    - 'ma_cross'  : EMA 快/慢线 趋势 + 回踩慢线  [趋势跟随/回踩]
    - 'boll'      : 布林带均值回归 (触下轨做多/触上轨做空)  [震荡市]
    - 'donchian'  : 唐奇安通道突破 (创新高做多/新低做空)  [突破]

[+] 风险管理增强
    - 止损: 固定美元 sl_buffer 或 ATR 倍数 atr_stop_mult (atr_stop=True)
    - 止盈: r_mult 固定倍数 或 trailing 追踪 (trailing=True)
    - 入场质量过滤: 反转 K 线 / RSI 极端 / 15m 短期方向 / 同方向冷却

[+] TMGM 隔夜控制
    - avoid_overnight=True: 持仓到 UTC 20:00 一律平掉(服务器零点前), 规避隔夜费
    - 强信号隔夜放行: 仅当通道相对斜率极强(|rel_slope|>strong_overnight_thresh)
      且当前持仓浮盈时才允许持过夜 (降低未来函数/过拟合与隔夜风险)

[+] 真实点差序列(对齐 EA SYMBOL_SPREAD)
    - clean 数据无逐笔点差; 沙箱无法访问 Dukascopy(需 VPN), 但 TMGM MT5 历史导出
      <SPREAD> 列本身含真实逐笔点差(单位 points, 与 EA 一致), 直接复用(同经纪商更对齐)。
      实测: 中位 8 / 均值 8.2 / p95 12 / 极端 max 150, 仅 8 根 > 50。

用法
----
    python box_channel_optimized.py --entry-style box --r-mult 2.0 --trailing
    python box_channel_optimized.py --entry-style ma_cross --ma-fast 5 --ma-slow 20
"""
import os
import argparse
import datetime as dt
import numpy as np
import pandas as pd
import backtrader as bt
from sr_levels import SRLevelsProvider   # 支撑压力位模块(Stage 1 新建)

CLEAN_DIR = r"D:\Data\stockdata\xauusd\clean"
FIG_DIR = os.path.join(CLEAN_DIR, "figs")
RAW_TMGM_15M = r"D:\Data\stockdata\xauusd\tmgm\XAUUSD_M15_202309140000_202609081600.csv"
RAW_TMGM_5M = r"D:\Data\stockdata\xauusd\tmgm\XAUUSD_M5_202504142040_202609111935.csv"
_SPREAD_CACHE = {}  # 真实点差 Series 缓存: {freq -> Series(UTC 索引, 单位 points)}


# --------------------------------------------------------------------------
# TMGM 隔夜费 (swap) 建模辅助
# --------------------------------------------------------------------------
def tmgm_server_offset_hours(utc_dt):
    """TMGM 服务器相对 UTC 偏移: 跟随美国 DST, 夏令 +3 / 冬令 +2。
    swap 在服务器零点(UTC 21:00 夏 / 22:00 冬)收取。"""
    year = utc_dt.year
    mar1 = dt.datetime(year, 3, 1)
    first_sun_mar = mar1 + dt.timedelta(days=(6 - mar1.weekday()) % 7)
    dst_start = (first_sun_mar + dt.timedelta(days=7)).replace(hour=7)
    nov1 = dt.datetime(year, 11, 1)
    first_sun_nov = nov1 + dt.timedelta(days=(6 - nov1.weekday()) % 7)
    dst_end = first_sun_nov.replace(hour=6)
    return 3 if (dst_start <= utc_dt < dst_end) else 2


# --------------------------------------------------------------------------
# 真实点差序列(TMGM MT5 历史导出 <SPREAD> 列, 单位 points, 与 EA SYMBOL_SPREAD 一致)
# 沙箱无法访问 Dukascopy(需 VPN), 但 TMGM 原始导出本身即含真实逐笔点差, 直接复用(同经纪商更对齐 EA)。
# --------------------------------------------------------------------------
_SPREAD_SRC = {"15m": RAW_TMGM_15M, "5m": RAW_TMGM_5M}


def _build_real_spread(freq="15m"):
    """按周期构建真实逐笔点差序列(单位 points)。15m / 5m 均支持。"""
    if freq in _SPREAD_CACHE:
        return _SPREAD_CACHE[freq]
    path = _SPREAD_SRC.get(freq)
    if path is None:
        return None
    raw = pd.read_csv(path, sep="\t", skiprows=1,
                      names=["date", "time", "open", "high", "low", "close",
                             "tickvol", "vol", "spread"])
    sd = pd.to_datetime(raw["date"] + " " + raw["time"], format="%Y.%m.%d %H:%M:%S")
    # 自洽解析 TMGM 服务器→UTC 偏移(跟随美国 DST): 先试 GMT+3, 用结果 UTC 回验 DST
    off = sd.apply(lambda s: tmgm_server_offset_hours(s - pd.Timedelta(hours=3)))
    utc = sd - pd.to_timedelta(off, unit="h")
    s = pd.Series(raw["spread"].astype(float).values, index=utc)
    s = s[~s.index.duplicated(keep="first")]
    _SPREAD_CACHE[freq] = s
    return s


def _build_real_spread_15m():
    """向后兼容旧调用点(等价于 _build_real_spread("15m"))。"""
    return _build_real_spread("15m")


class FixedLotSizer(bt.Sizer):
    params = dict(lot=0.01)

    def _getsizing(self, comminfo, cash, data, isbuy):
        return self.p.lot


# --------------------------------------------------------------------------
# 优化策略主体
# --------------------------------------------------------------------------
class BoxChannelOptStrategy(bt.Strategy):
    params = dict(
        # 通道 / 聚类
        trend_window=24, short_win=8, long_win=4,
        min_k=3, cluster_bins=20,
        r_mult=1.2, sl_buffer=2.0,
        rel_slope_thresh=0.0006,
        # 趋势过滤器(第5步, 专攻震荡/回调弱段): 仅在价格处于主趋势 favorable 侧才入场
        ma_filter=False, ma_filter_period=96,        # 价格需在 EMA(period) favorable 侧
        atr_regime_gate=False, atr_regime_period=48, atr_regime_mult=1.4,
        # 远离趋势过滤(防低波动期过度延伸入场): 入场价偏离 MA(period) 超过 max_ma_dist_atr×ATR 则跳过
        max_ma_dist_atr=0.0,           # 0=关闭; >0 时拒绝 long 入场价>MA+K×ATR / short 入场价<MA-K×ATR
        ma_dist_regime_cond=True,     # True=仅低波动段(atr_now/atr_avg<阈值)启用, 波动扩张/突破段放行延伸入场
        ma_dist_lowvol_mult=1.0,      # 低波动阈值: atr_now/atr_avg < 此值视为低波动(配合 ma_dist_regime_cond)
        # 自适应 r_mult(第4步): 1h 通道斜率幅度 |rel| 越大(趋势越强) → r_mult 越高
        adaptive_rmult=False,
        rmult_range=1.5, rmult_trend=2.3,
        rmult_ref_lo=0.0006, rmult_ref_hi=0.0025,
        rev_tol=0.0015, touch_count=2,
        # 质量过滤
        use_reversal_filter=True,
        # 条件化反转过滤(2026-09 落地: 仅强趋势段开启, 替代常开; 见 use_reversal_filter_ab_report.md)
        #   rev_mode: always_on / always_off / rev_in_strong / rev_in_weak
        #   阈值 5.1e-5 = MA96(EMA96) 20-bar 相对斜率绝对值, 训练段 2023-2025 校准(Round5/6 留出验证通过)
        rev_mode="always_on",          # 默认 always_on 保持历史行为一致(各现有画像不显式设此参数)
        rev_strong_thresh=5.1e-5,      # |MA96相对斜率|>=此值判为强趋势段(仅 rev_in_strong/rev_in_weak 使用)
        short_trend_n=20,
        cooldown_bars=4,
        rsi_period=14, rsi_overbought=70, rsi_oversold=30,
        min_wick_ratio=0.6,
        # 入场风格
        entry_style="box",       # box / ma_cross / boll / donchian
        ma_fast=5, ma_slow=20,
        boll_period=20, boll_dev=2.0,
        dc_period=20,            # 唐奇安通道周期(15m 根数)
        # ---- A 方案: 单入场逻辑结构开关(用于 IS/OOS 迭代评估) ----
        macross_require_channel=False,  # ma_cross: 要求 1h 通道严格同向(up 才多/down 才空), 禁用 range 里的回踩
        boll_any_channel=False,         # boll: 允许在非 range 通道也触发(默认仅 range)
        donchian_close_confirm=False,   # donchian: 用收盘价确认突破(c 越过前高/前低), 而非仅 high 触及
        # 止损 / 止盈
        atr_stop=False, atr_period=14, atr_stop_mult=2.0,
        # ATR 取数位移: 0=当前bar self.atr[0](backtrader 默认); 1=上一根已收盘bar self.atr[-1](与 EA GetATRVal(1) 对齐)
        atr_shift=0,
        trailing=False,          # 追踪止盈(突破入场/趋势增强用)
        # 两段式追踪(早激活 + 保本缓冲 + 收窄跟随): 默认值复现原行为(始终追踪/跟随1.5×ATR/无保本)
        trail_activate_atr=0.0,  # 浮盈达此倍数×ATR 才启动追踪; 0=始终追踪(原行为)
        trail_atr_mult=1.5,      # 追踪跟随距离(×ATR); < atr_stop_mult 则更早锁利
        trail_be_buffer=0.0,     # 启动后 SL 先弹到 entry±此倍数×ATR 保本带; 0=不启用
        partial_tp=0.0,          # >0 时半仓锁利(0~1)
        # 隔夜控制
        avoid_overnight=True,
        strong_overnight_thresh=0.0020,   # |rel_slope| 超此值且浮盈才放行隔夜
        server_close_hour=22,             # 服务器时间几点强平(对应 TMGM InpServerCloseHour=22)
        # 时段 / 点差过滤(对齐 EA InpTradeStartHour/EndHour/InpMaxSpreadPoints)
        session_filter=True,               # 开仓时段过滤(夏 GMT+3 起点=trade_start_hour, 冬 GMT+2 起点=trade_start_hour-1)
        trade_start_hour=1,                # 交易起点(服务器时, 夏令时 GMT+3 基准) = 北京 6:00
        trade_end_hour=22,                 # 交易终点(服务器时) 对应 EA InpTradeEndHour
        spread_filter=True,                # 开仓点差过滤(>max_spread_points 跳过)
        max_spread_points=50.0,            # 点差上限(points); 用 TMGM 真实逐笔点差(中位8)比对, 阈值50几乎不挡
        # 手续费
        swap_long_points=-72.5, swap_short_points=30.72,
        swap_point_value=0.01, contract_oz=100.0,
        print_log=False,
        # 多仓位(金字塔/并发): 最多同时持仓笔数(1=退化为原单仓位行为, 与 EA 一致)
        max_positions=1,
        concurrent=False,     # 真实并发模式: 逐槽位冷却(非全局) + 每信号bar允许多空闲槽位同开(金字塔加仓)
        stagger_bars=0,       # 错峰冷却(仅 concurrent 生效): 任意两笔开仓须相隔 >=X 根bar(slot k 比 slot k-1 晚 X 根)
        slot_styles=None,     # A 方案: 每槽位各自的 entry_style 列表(如 ["box","donchian"]); None=全部用 entry_style
        lot=0.01,               # 每笔固定手数(须与 sizer 一致; SL/TP/平仓按 size=lot 逐笔平仓)
        spread_series=None,    # 真实逐笔点差 Series(UTC 索引, points); None 则用 proxy 回退
        server_time=False,      # True=数据已是经纪商服务器时间, 跳过 UTC→server 偏移转换(对齐 EA, 消除时区 bar 偏移)
        # 多仓位并发: 每个槽位一份独立 15m 数据源(backtrader 对同一数据源净头寸, 须多源隔离)
        slot_feeds=None,       # list[DataBase]: 各持仓槽位的独立 15m feed(由 run_backtest 注入)
        ctx_data=None,         # 通道判定用的 1h 上下文 feed(由 run_backtest 注入; None 回退 self.data1)
        # 支撑压力位模块(Stage 1): 用新模块替换原 cluster_levels 计算
        #   None            = 原行为(15m histogram 聚类 + 1h 长窗), 与历史逐字节一致
        #   "1h"/"daily"/"monthly" = 仅用该 timeframe 的枢轴支撑压力位
        #   "combined"     = 三档中"最贴近当前价"的支撑(下方最近)/压力(上方最近)
        # sr_build_kw: 各 timeframe 的构建参数 {tf: {sw, cluster_pct, ...}}, 缺省用模块内置默认
        sr_source=None,
        sr_build_kw=None,
        # 限价单入场(Stage 3.2): 在当前买条件下, 挂限价单在更优价位(贴近支撑买/贴近阻力卖)而非市价立即买入
        #   limit_entry=True          -> 用 Limit 单(价=support+limit_buffer_atr*ATR)替代市价单
        #   limit_anchor_level=True    -> SL/TP 锚定到支撑/压力位(低买直接改善 R); False=沿用原 atr_stop 范式(限价零收益, 作对照)
        #   limit_valid_bars          -> 未成交的限价单在此根数后作废(防陈旧单)
        #   ⚠️ 仅用历史已知量(support/当前 ATR), 不引入未来函数, 模拟 EA 实时挂单
        limit_entry=False,
        limit_buffer_atr=0.5,
        limit_valid_bars=4,
        limit_anchor_level=True,
    )

    def __init__(self):
        self.open_positions = []     # 多仓位列表(避免与 backtrader 自带只读 positions 属性冲突): 每元素为一个独立交易的订单/状态字典
        self.n_open = 0              # 当前在仓交易数(=len(positions), 每根 bar 维护)
        # 槽位数据源: 每持仓绑定一份独立 15m feed(实现 broker 独立持仓, 互不净撮合)
        self.slot_feeds = list(self.p.slot_feeds) if self.p.slot_feeds else [self.data]
        self.ctx_data = self.p.ctx_data if self.p.ctx_data is not None else self.data1
        self.equity = []
        self.n_entries = 0
        self._n_session_skip = 0   # 时段过滤挡掉的开仓尝试
        self._n_spread_skip = 0    # 点差过滤挡掉的开仓尝试
        self.trades_log = []
        self.trade_pnls = []
        self.trade_episodes = []
        self._prev_pos = 0
        self._last_sd = None
        self.swap_total = 0.0
        self._last_entry_bar = -999
        self._last_entry_side = None
        # 逐槽位冷却记录(concurrent 模式用): {id(data_feed): bar} / {id(data_feed): side}
        self._last_entry_bar_by_data = {}
        self._last_entry_side_by_data = {}
        # 错峰(stagger)用: 最近一次任意槽位开仓的 bar
        self._last_open_bar = -999
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        self.ma_fast = bt.indicators.EMA(self.data.close, period=self.p.ma_fast)
        self.ma_slow = bt.indicators.EMA(self.data.close, period=self.p.ma_slow)
        self.boll = bt.indicators.BollingerBands(self.data.close,
                                                 period=self.p.boll_period,
                                                 devfactor=self.p.boll_dev)
        self.dc_high = bt.indicators.Highest(self.data.high, period=self.p.dc_period)
        self.dc_low = bt.indicators.Lowest(self.data.low, period=self.p.dc_period)
        # 趋势过滤器(第5步)
        self.ma_tf = bt.indicators.EMA(self.data.close, period=self.p.ma_filter_period)
        self.atr_avg = bt.indicators.SMA(self.atr, period=self.p.atr_regime_period)

        # 支撑压力位模块 provider(s): 仅在 sr_source 设置时构建, 默认 None 保持原行为
        self._sr_providers = {}
        if self.p.sr_source is not None:
            if self.p.sr_source == "combined":
                _tfs = ["1h", "daily", "monthly"]
            else:
                _tfs = [self.p.sr_source]
            _kw = self.p.sr_build_kw or {}
            for _tf in _tfs:
                _bkw = dict(_kw.get(_tf, {}))
                self._sr_providers[_tf] = SRLevelsProvider.build_from_csv(_tf, **_bkw)

    def _style_for_slot(self, idx):
        """A 方案: 返回槽位 idx 使用的 entry_style(未配置则用全局 entry_style)。"""
        ss = self.p.slot_styles
        if not ss:
            return self.p.entry_style
        try:
            if idx < len(ss):
                return ss[idx]
            return ss[-1]
        except Exception:
            return self.p.entry_style

    # ---------------- 支撑压力位获取 (原 cluster_levels / 新模块) ----------------
    def _get_sr(self):
        """返回 (support, resistance) 当前 bar 的支撑/压力价。
        sr_source=None -> 原行为(15m 短窗 + 1h 长窗 histogram 聚类);
        否则 -> 新支撑压力位模块的 nearest_as_of(无未来函数)。"""
        if self.p.sr_source is None:
            s_sup, s_res, _, _ = self.cluster_levels(self.data, self.p.short_win)
            l_sup, l_res, _, _ = self.cluster_levels(self.ctx_data, self.p.long_win)
            support = s_sup if s_sup is not None else l_sup
            resistance = s_res if s_res is not None else l_res
            return support, resistance
        _dt = self.data.datetime.datetime(0)
        _price = self.data.close[0]
        if self.p.sr_source != "combined":
            _sup, _res, _, _ = self._sr_providers[self.p.sr_source].nearest_as_of(_dt, _price)
            return _sup, _res
        # combined: 三档中取"最贴近当前价"的支撑(下方最大)/压力(上方最小)
        _best_sup = None; _best_res = None
        for _prov in self._sr_providers.values():
            _s, _r, _, _ = _prov.nearest_as_of(_dt, _price)
            if _s is not None and (_best_sup is None or _s > _best_sup):
                _best_sup = _s
            if _r is not None and (_best_res is None or _r < _best_res):
                _best_res = _r
        return _best_sup, _best_res

    # ---------------- 通道判定 (1h) ----------------
    def classify_channel(self):
        n = self.p.trend_window
        if len(self.ctx_data) < n + 1:
            return "warmup", 0.0
        closes = np.array(self.ctx_data.close.get(size=n), dtype=float)
        x = np.arange(n)
        slope = np.polyfit(x, closes, 1)[0]
        mid = float(np.mean(closes))
        rel = slope / mid if mid > 0 else 0.0
        cur = self.ctx_data.close[0]
        if rel > self.p.rel_slope_thresh and cur >= mid:
            return "up", rel
        if rel < -self.p.rel_slope_thresh and cur <= mid:
            return "down", rel
        return "range", rel

    # ---------------- 15m 短期方向 ----------------
    def classify_short_direction(self):
        n = self.p.short_trend_n
        if len(self.data) < n + 1:
            return 0
        closes = np.array(self.data.close.get(size=n), dtype=float)
        slope = np.polyfit(np.arange(n), closes, 1)[0]
        mid = float(np.mean(closes))
        rel = slope / mid if mid > 0 else 0.0
        th = self.p.rel_slope_thresh * 0.5
        if rel > th:
            return 1
        if rel < -th:
            return -1
        return 0

    # ---------------- 支撑/压力聚类 ----------------
    def cluster_levels(self, data, n):
        if len(data) < n + 1:
            return None, None, 0, 0
        highs = np.array(data.high.get(size=n), dtype=float)
        lows = np.array(data.low.get(size=n), dtype=float)
        mids = (highs + lows) / 2.0
        lo, hi = float(lows.min()), float(highs.max())
        if hi <= lo:
            return lo, hi, 0, 0
        counts, edges = np.histogram(mids, bins=self.p.cluster_bins, range=(lo, hi))
        best = int(np.argmax(counts))
        b_lo, b_hi = edges[best], edges[best + 1]
        mask = (mids >= b_lo) & (mids <= b_hi)
        if mask.sum() >= self.p.min_k:
            support = float(lows[mask].min())
            resistance = float(highs[mask].max())
        else:
            support, resistance = lo, hi
        rtol = self.p.rev_tol
        touch_high = int(np.sum(highs >= resistance * (1 - rtol)))
        touch_low = int(np.sum(lows <= support * (1 + rtol)))
        return support, resistance, touch_high, touch_low

    # ---------------- 反转 K 线 ----------------
    def detect_reversal(self, support, resistance):
        rev = {"short": False, "long": False}
        if support is None and resistance is None:
            return rev
        o, h, l, c = self.data.open[0], self.data.high[0], self.data.low[0], self.data.close[0]
        rng = h - l
        if rng <= 0:
            return rev
        upper = h - max(o, c)
        lower = min(o, c) - l
        hammer = (lower / rng >= self.p.min_wick_ratio) and (c >= o)
        hanging = (upper / rng >= self.p.min_wick_ratio) and (c <= o)
        bull_engulf = bear_engulf = False
        if len(self.data) >= 2:
            po, pc = self.data.open[-1], self.data.close[-1]
            if pc < po and c > o and c > po:
                bull_engulf = True
            if pc > po and c < o and c < po:
                bear_engulf = True
        near_sup = support is not None and abs(c - support) / support < self.p.rev_tol * 3
        near_res = resistance is not None and abs(c - resistance) / resistance < self.p.rev_tol * 3
        if (hammer or bull_engulf) and near_sup:
            rev["long"] = True
        if (hanging := hanging or bear_engulf) and near_res:
            rev["short"] = True
        return rev

    # ---------------- 条件化反转过滤(2026-09 落地) ----------------
    def _ma96_rel_slope(self):
        """MA96(EMA close, ma_filter_period) 的 20-bar 相对斜率 = 线性回归斜率 / 均线均值。
        与 EA MA96RelSlope() 及 Round5-6 校准阈值 5.1e-5 对齐; 取 shift=1..n 的已收盘 bar。"""
        n = 20
        arr = self.ma_tf.get(size=n + 1)   # 末位为当前形成中 bar
        if arr is None or len(arr) < n + 1:
            return 0.0
        vals = np.asarray(arr[:n], dtype=float)   # 排除当前bar, 取已收盘 n 根(shift 1..n)
        if vals.size < n:
            return 0.0
        x = np.arange(n)
        slope = np.polyfit(x, vals, 1)[0]
        mid = float(np.mean(vals))
        return slope / mid if mid > 0 else 0.0

    def _effective_rev_filter(self):
        """本根入场是否启用反转K线过滤(对齐 EA 入场判定的 revFilterOn)。
        use_reversal_filter=False 时作总开关强制恒关; 否则由 rev_mode 决定生效时段:
          always_on     → 恒开
          always_off    → 恒关
          rev_in_strong → 仅 |MA96相对斜率| >= rev_strong_thresh 时开
          rev_in_weak   → 仅 |MA96相对斜率| <  rev_strong_thresh 时开
        """
        if not self.p.use_reversal_filter:
            return False
        mode = self.p.rev_mode
        if mode == "always_off":
            return False
        if mode == "always_on":
            return True
        rs = abs(self._ma96_rel_slope())
        if mode == "rev_in_strong":
            return rs >= self.p.rev_strong_thresh
        if mode == "rev_in_weak":
            return rs < self.p.rev_strong_thresh
        return True

    # ---------------- 入场信号 dispatch ----------------
    def want_entry(self, channel, support, resistance, style=None):
        """返回 'long' / 'short' / None (仅信号, 不含质量过滤)。
        style: A 方案每槽位可指定不同入场逻辑; None 则用全局 self.p.entry_style。"""
        s = style if style is not None else self.p.entry_style
        o, h, l, c = self.data.open[0], self.data.high[0], self.data.low[0], self.data.close[0]
        if s == "box":
            rev = self.detect_reversal(support, resistance)
            eff = self._effective_rev_filter()   # 条件化反转过滤(常开/常关/仅强趋势段等)
            if channel == "up" and (not eff or rev["long"]) and l <= support <= c:
                return "long"
            if channel == "down" and (not eff or rev["short"]) and c <= resistance <= h:
                return "short"
            return None
        if s == "ma_cross":
            # 趋势向上 + 回踩慢线(未跌破)做多; 反之做空
            up = self.ma_fast[0] > self.ma_slow[0]
            if self.p.macross_require_channel:
                # 严格通道同向: 仅 up 通道做多 / down 通道做空(排除 range 里的假回踩)
                if channel == "up" and up and l <= self.ma_slow[0] <= c:
                    return "long"
                if channel == "down" and (not up) and c <= self.ma_slow[0] <= h:
                    return "short"
                return None
            if channel != "down" and up and l <= self.ma_slow[0] <= c:
                return "long"
            if channel != "up" and (not up) and c <= self.ma_slow[0] <= h:
                return "short"
            return None
        if s == "boll":
            # 均值回归; 触下轨反弹做多 / 触上轨回落做空(默认仅 range, 开关后可全通道)
            if channel == "range" or self.p.boll_any_channel:
                if c <= self.boll.lines.bot[0] and c > self.data.close[-1]:
                    return "long"
                if c >= self.boll.lines.top[0] and c < self.data.close[-1]:
                    return "short"
            return None
        if s == "donchian":
            # 突破: 创 N 周期新高做多 / 新低做空(可选收盘价确认)
            if self.p.donchian_close_confirm:
                if c > self.dc_high[-1]:
                    return "long"
                if c < self.dc_low[-1]:
                    return "short"
            else:
                if h >= self.dc_high[-1]:
                    return "long"
                if l <= self.dc_low[-1]:
                    return "short"
            return None
        return None

    # ---------------- 止损/止盈价 ----------------
    def risk_levels(self, side, entry, support, resistance, r_mult=None):
        rm = r_mult if r_mult is not None else self.p.r_mult
        if self.p.atr_stop:
            a = self.atr[-self.p.atr_shift]
            if side == "long":
                stop = entry - self.p.atr_stop_mult * a
                target = entry + rm * (entry - stop)
            else:
                stop = entry + self.p.atr_stop_mult * a
                target = entry - rm * (stop - entry)
        else:
            if side == "long":
                stop = support - self.p.sl_buffer
                target = entry + rm * (entry - stop)
            else:
                stop = resistance + self.p.sl_buffer
                target = entry - rm * (stop - entry)
        return stop, target

    # ---------------- 自适应 r_mult(第4步) ----------------
    def _adaptive_rmult(self, rel):
        if not self.p.adaptive_rmult:
            return self.p.r_mult
        a = abs(rel)
        lo, hi = self.p.rmult_range, self.p.rmult_trend
        rlo, rhi = self.p.rmult_ref_lo, self.p.rmult_ref_hi
        if a <= rlo:
            return lo
        if a >= rhi:
            return hi
        return lo + (a - rlo) / (rhi - rlo) * (hi - lo)

    # ---------------- 趋势过滤器(第5步) ----------------
    def _trend_filter_pass(self, want):
        """趋势对齐 / 波动率 regime 门。返回 False 则跳过本次入场。"""
        if self.p.ma_filter:
            ma = self.ma_tf[0]
            if want == "long" and self.data.close[0] <= ma:
                return False
            if want == "short" and self.data.close[0] >= ma:
                return False
        if self.p.atr_regime_gate:
            atr_now = self.atr[-self.p.atr_shift]
            atr_avg = self.atr_avg[-self.p.atr_shift]
            if atr_avg > 0 and atr_now / atr_avg > self.p.atr_regime_mult:
                return False
        # 远离趋势过滤: 拒绝价格已过度延伸(远离 MA96)的入场, 防低波动期毛刺扫损
        if self.p.max_ma_dist_atr > 0:
            atr_now = self.atr[-self.p.atr_shift]
            if atr_now > 0:
                apply = True
                if self.p.ma_dist_regime_cond:
                    # 仅"低波动"段启用: 当前 ATR 低于其均值(atr_now/atr_avg<阈值)→ 缺乏后续动能, 过度延伸易扫损;
                    # 波动扩张/突破段(ATR 高于均值)放行过度延伸入场(突破延续能兑现)
                    atr_avg = self.atr_avg[-self.p.atr_shift]
                    apply = (atr_avg > 0 and atr_now / atr_avg < self.p.ma_dist_lowvol_mult)
                if apply:
                    ma = self.ma_tf[0]
                    over = (self.data.close[0] - ma) / atr_now
                    if want == "long" and over > self.p.max_ma_dist_atr:
                        return False
                    if want == "short" and over < -self.p.max_ma_dist_atr:
                        return False
        return True

    # ---------------- 订单回调 ----------------
    # 注意: 本 backtrader 构建中 notify_order 传入的 order 对象与策略 self.xxx_order
    # 引用的身份不一致(确认过 isentry/isl/itp 均为 False), 故状态机改用 next() 轮询
    # 订单 .status 驱动(backtrader 会就地更新策略持有的订单对象的 .status)。notify 仅留空。
    def notify_order(self, order):
        pass

    # ---------------- broker 权威平仓事件(根治"幽灵平仓"/僵尸持仓) ----------------
    # 设计: 订单状态轮询(尤其追踪止损频繁 cancel+replace SL)不可靠——旧 SL 可能在
    # cancel 生效前已被 broker 撮合成交, 平掉多头, 但 self.open_positions 跟踪的是新 SL
    # (仍 alive) → 逻辑持仓变僵尸(broker 已平, 策略不知), 后续在僵尸仓上重挂卖出单 →
    # 平盘卖单成交转空。故平仓判定统一交由 notify_trade(broker 实际每笔成交回合),
    # next() 仅做对价对账 + 平盘保护。
    def notify_trade(self, trade):
        if trade.isclosed:
            dtopen = bt.num2date(trade.dtopen) if trade.dtopen else None
            dtclose = bt.num2date(trade.dtclose) if trade.dtclose else None
            self.trade_pnls.append(dict(
                side=("long" if trade.long else "short"),
                pnl=trade.pnlcomm,
            ))
            self.trade_episodes.append(dict(
                side=("long" if trade.long else "short"),
                pnl=trade.pnlcomm,
                bars=max(int(trade.barlen), 0),
                dtopen=dtopen,
                dtclose=dtclose,
            ))
        if trade.justopened:
            return
        if not trade.isclosed:
            return
        dirx = 1 if trade.size > 0 else -1
        # 按成交所属数据源匹配(每槽位独立数据源 → broker 独立持仓, trade.data 即对应槽位 feed)
        target = None
        for pos in self.open_positions:
            if pos.get("_closed"):
                continue
            if pos["data"] is trade.data:
                target = pos
                break
        if target is None:
            return  # 孤儿成交(理论上不该发生, 忽略以防双平)
        entry = trade.price
        pnl = trade.pnlcomm
        exit_px = (entry + pnl) if dirx == 1 else (entry - pnl)
        reason = target.get("_close_reason") or "manual"
        if reason == "manual":
            stop = target["trade_info"].get("stop")
            tgt = target["trade_info"].get("target")
            if stop is not None and abs(exit_px - stop) < 0.02:
                reason = "trail" if target.get("_trailing_active") else "sl"
            elif tgt is not None and abs(exit_px - tgt) < 0.02:
                reason = "tp"
        # 取消该笔兄弟挂单(防孤儿单后续成交)
        for _o in (target["sl_order"], target["tp_order"], target["manual_order"]):
            if _o is not None and _o.alive():
                self.cancel(_o)
        target["_close_reason"] = reason
        self._log_close(target, reason, exit_px)
        self._reset_trade(target)
        if target in self.open_positions:
            self.open_positions.remove(target)

    # ---------------- 入场成交(持仓已开)后挂 broker 端 SL/TP(盘中触发, 非 bar 收盘手动平) ----------------
    def _attach_sl_tp(self, pos):
        info = pos["trade_info"]
        # 用实际成交价更新 entry(notify 无法识别订单, 改从入场单读取)
        eo = pos["entry_order"]
        fill = eo.executed.price if (eo is not None and eo.executed.size != 0) else info.get("entry")
        info["entry"] = fill
        pos["_extreme"] = fill
        self.trades_log.append(dict(
            dt=bt.num2date(self.data.datetime[0]), price=fill,
            side=("long" if info["dir"] == 1 else "short"), kind="open", reason=None,
            stop=info.get("stop"), target=info.get("target"), r_mult=info.get("r_mult")))
        # 限价单 + 价位锚定(Stage 3.2 有益版): SL/TP 锚定到支撑/压力位, 低买直接改善 R
        #   ⚠️ 仅当 limit_entry=True 时生效(它是限价单功能的子选项); 基线(limit_entry=False)必须保持原 atr_stop 范式
        #   atr_stop 范式下 SL/TP 锚定入场价 -> 低买零收益; 价位锚定才让"低买"转化为更高 R
        if self.p.limit_entry and self.p.limit_anchor_level and info.get("support") is not None and info.get("resistance") is not None:
            _a = self.atr[-self.p.atr_shift]
            _sup, _res = info["support"], info["resistance"]
            if info["dir"] == 1:
                stop = _sup - self.p.sl_buffer
                target = _res - self.p.limit_buffer_atr * _a
            else:
                stop = _res + self.p.sl_buffer
                target = _sup + self.p.limit_buffer_atr * _a
            info["stop"], info["target"] = stop, target
        else:
            stop, target = info["stop"], info["target"]
        lot = self.p.lot
        d = pos["data"]
        if info["dir"] == 1:
            pos["sl_order"] = self.sell(data=d, exectype=bt.Order.Stop, price=stop, size=lot)
            pos["tp_order"] = self.sell(data=d, exectype=bt.Order.Limit, price=target, size=lot)
        else:
            pos["sl_order"] = self.buy(data=d, exectype=bt.Order.Stop, price=stop, size=lot)
            pos["tp_order"] = self.buy(data=d, exectype=bt.Order.Limit, price=target, size=lot)

    def _reset_trade(self, pos):
        """平仓/取消后清空该笔交易的订单引用与状态(取消仍挂的单, 避免孤儿单)。"""
        for _o in (pos["sl_order"], pos["tp_order"], pos["manual_order"], pos["entry_order"]):
            if _o is not None and _o.alive():
                self.cancel(_o)
        pos["entry_order"] = None
        pos["sl_order"] = None
        pos["tp_order"] = None
        pos["manual_order"] = None
        pos["trade_info"] = {}
        pos["_close_reason"] = None
        pos["_extreme"] = None
        pos["_trailing_active"] = False

    # ---------------- 持仓中: 修改 broker 端 SL 挂单(追踪) ----------------
    def _update_sl(self, pos, new_stop):
        """取消该笔 SL 挂单并以更优价位重挂(追踪止损)。broker 端盘中触发, 非 bar 收盘手动平。"""
        if pos["sl_order"] is not None and pos["sl_order"].alive():
            self.cancel(pos["sl_order"])
        lot = self.p.lot
        d = pos["data"]
        if pos["trade_info"]["dir"] == 1:
            pos["sl_order"] = self.sell(data=d, exectype=bt.Order.Stop, price=new_stop, size=lot)
        else:
            pos["sl_order"] = self.buy(data=d, exectype=bt.Order.Stop, price=new_stop, size=lot)
        pos["trade_info"]["stop"] = new_stop
        pos["_trailing_active"] = True

    # ---------------- 手动平(仅用于隔夜强平 / 强平, 经纪商无法按时自动平) ----------------
    def _manual_close(self, pos, reason):
        # 平盘保护: 若该方向 broker 仓位已不在(僵尸仓/旧SL已平), 不再下卖出单
        # (否则 self.close() 返回 None 或平盘卖单转空), 直接标记清理
        bsz = self.broker.getposition(pos["data"]).size
        d = pos["trade_info"].get("dir")
        if (d == 1 and bsz <= 1e-9) or (d == -1 and bsz >= -1e-9):
            pos["_close_reason"] = reason
            pos["_closed"] = True
            self._log_close(pos, reason, pos["data"].close[0])
            self._reset_trade(pos)
            return
        if pos["sl_order"] is not None and pos["sl_order"].alive():
            self.cancel(pos["sl_order"])
        if pos["tp_order"] is not None and pos["tp_order"].alive():
            self.cancel(pos["tp_order"])
        pos["manual_order"] = self.close(size=self.p.lot, data=pos["data"])
        pos["_close_reason"] = reason

    # ---------------- 持仓中: 追踪止损(改挂单, 只在更优方向推进 >0.05) ----------------
    def _maybe_trail(self, pos):
        """两段式追踪止损(仅 trailing=True 时由 next() 调用), 按笔独立:
        ① 激活门限: 浮盈未达 trail_activate_atr×ATR 前不追踪, 保住初始硬止损;
        ② 收窄跟随: 启动后 SL 紧贴 历史极值 ± trail_atr_mult×ATR(可 < 初始 1.5×ATR 以更早锁利);
        ③ 保本缓冲: 若启用 trail_be_buffer, 取 max(保本带, 紧跟随) 使 SL 尽早弹过 entry, 根除浮盈回吐亏损。
        默认(trail_activate_atr=0 / trail_atr_mult=1.5 / trail_be_buffer=0)复现原单段行为。"""
        info = pos["trade_info"]
        if info.get("dir") is None or pos["sl_order"] is None:
            return
        c = pos["data"].close[0]
        d = info["dir"]
        if d == 1:
            pos["_extreme"] = max(pos["_extreme"], c)
        else:
            pos["_extreme"] = min(pos["_extreme"], c)
        atr = self.atr[-self.p.atr_shift]
        entry = info["entry"]
        # ① 激活门限
        fav = (pos["_extreme"] - entry) if d == 1 else (entry - pos["_extreme"])
        if fav < self.p.trail_activate_atr * atr:
            return
        # ② 收窄跟随
        if d == 1:
            tight = pos["_extreme"] - self.p.trail_atr_mult * atr
        else:
            tight = pos["_extreme"] + self.p.trail_atr_mult * atr
        new_stop = tight
        # ③ 保本缓冲
        if self.p.trail_be_buffer > 0:
            if d == 1:
                be = entry + self.p.trail_be_buffer * atr
                new_stop = max(be, tight)
            else:
                be = entry - self.p.trail_be_buffer * atr
                new_stop = min(be, tight)
        # 仅在更优方向推进才重挂(>0.05 防抖动)
        if d == 1:
            if new_stop > info["stop"] + 0.05:
                self._update_sl(pos, new_stop)
        else:
            if new_stop < info["stop"] - 0.05:
                self._update_sl(pos, new_stop)

    # ---------------- 记录平仓(price 由调用方给出: 成交单 executed.price 或 notify_trade 推算) ----------------
    def _log_close(self, pos, reason, price=None):
        info = pos["trade_info"]
        side = "long" if info.get("dir") == 1 else "short"
        px = price if price is not None else self.data.close[0]
        self.trades_log.append(dict(
            dt=bt.num2date(self.data.datetime[0]), price=px,
            side=side, kind="close", reason=reason,
            stop=info.get("stop"), target=info.get("target"),
            r_mult=info.get("r_mult")))

    # ---------------- 开仓: 市价/限价入场单(broker 端 SL/TP 在成交后由 _attach_sl_tp 挂出) ----------------
    def _open_position(self, want, entry_ref, stop, target, rm, rsi, slot_feed=None,
                       support=None, resistance=None):
        """新建一笔独立持仓(自含订单/状态字典), 追加到 self.open_positions。
        每笔固定手数 self.p.lot, 与 sizer 一致; SL/TP/平仓均按 size=lot 逐笔处理(绑定独立槽位 feed)。
        每持仓一份独立 15m 数据源 → broker 独立持仓, 互不净撮合, 实现真正多仓位并发。
        limit_entry=True 时: 以 Limit 单在更优价位挂出(贴近支撑买/贴近阻力卖), 而非市价立即成交。
          仅用历史已知量(support / 当前 ATR), 不引入未来函数。"""
        long = want == "long"
        if slot_feed is None:
            slot_feed = self.data
        a = self.atr[-self.p.atr_shift]
        pos = dict(
            entry_order=None, sl_order=None, tp_order=None, manual_order=None,
            data=slot_feed,
            trade_info=dict(entry=entry_ref, stop=stop, target=target,
                            dir=1 if long else -1, partial=False, r_mult=rm,
                            support=support, resistance=resistance),
            _extreme=entry_ref, _close_reason=None, _trailing_active=False,
            _closed=False, _entry_bar=len(slot_feed),
        )
        if self.p.limit_entry and a > 0:
            # 限价价: 多头=支撑上方缓冲(更优买点); 空头=阻力下方缓冲(更优卖点)
            if long:
                limit_px = (support + self.p.limit_buffer_atr * a) if support is not None else entry_ref
                limit_px = min(limit_px, entry_ref - 0.01)   # 必须低于当前收盘才构成"更优", 否则退化为市价
            else:
                limit_px = (resistance - self.p.limit_buffer_atr * a) if resistance is not None else entry_ref
                limit_px = max(limit_px, entry_ref + 0.01)
            if (long and limit_px < entry_ref - 0.01) or (not long and limit_px > entry_ref + 0.01):
                pos["entry_order"] = self.buy(data=slot_feed, exectype=bt.Order.Limit, price=limit_px) if long \
                    else self.sell(data=slot_feed, exectype=bt.Order.Limit, price=limit_px)
                pos["_limit_px"] = limit_px
            else:
                pos["entry_order"] = self.buy(data=slot_feed) if long else self.sell(data=slot_feed)
                pos["_limit_px"] = None
        else:
            pos["entry_order"] = self.buy(data=slot_feed) if long else self.sell(data=slot_feed)
            pos["_limit_px"] = None
        self.open_positions.append(pos)
        self._last_entry_bar = len(slot_feed)
        self._last_entry_side = want
        # 逐槽位冷却记录(concurrent 模式)
        self._last_entry_bar_by_data[id(slot_feed)] = len(slot_feed)
        self._last_entry_side_by_data[id(slot_feed)] = want
        # 错峰(stagger)用
        self._last_open_bar = len(slot_feed)
        self.n_entries += 1
        self.log(f"{'BUY' if long else 'SELL'} @ {entry_ref:.2f} rsi={rsi:.1f} "
                 f"stop {stop:.2f} target {target:.2f} (broker SL/TP after fill, intrabar) "
                 f"[{len(self.open_positions)}/{self.p.max_positions}]")

    def _margin_call(self, maintenance=0.5):
        if not self.open_positions:
            return False
        ci = self.broker.getcommissioninfo(self.data)
        mult = getattr(ci.p, "mult", 1.0) or 1.0
        lev = getattr(ci.p, "leverage", 1.0) or 1.0
        # 逐笔累加占用保证金(每笔 = lot × 现价 × mult / lev; 与 EA 单仓位原公式一致, 不乘 contract_oz)
        used = 0.0
        for pos in self.open_positions:
            used += self.p.lot * pos["data"].close[0] * mult / lev
        if self.broker.get_value() < used * maintenance:
            for pos in self.open_positions:
                self._manual_close(pos, "margin")
            return True
        return False

    def _charge_swap(self):
        utc_dt = bt.num2date(self.data.datetime[0])
        if self.p.server_time:
            sd = utc_dt.date()          # 数据已是服务器时间, 直接用其日期判隔夜费
        else:
            off = tmgm_server_offset_hours(utc_dt)
            sd = (utc_dt + dt.timedelta(hours=off)).date()
        if self._last_sd is None:
            self._last_sd = sd
            return
        if sd == self._last_sd:
            return
        mult3 = 3 if sd.weekday() == 2 else 1
        # 分多/空逐笔累计持仓 oz(避免净头寸抵消导致隔夜费漏计)
        long_oz = 0.0
        short_oz = 0.0
        for pos in self.open_positions:
            oz = self.p.lot * self.p.contract_oz
            if pos["trade_info"].get("dir") == 1:
                long_oz += oz
            else:
                short_oz += oz
        usd = 0.0
        if long_oz > 0:
            usd += long_oz * self.p.swap_long_points * self.p.swap_point_value * mult3
        if short_oz > 0:
            usd += short_oz * self.p.swap_short_points * self.p.swap_point_value * mult3
        if usd != 0:
            self.broker.add_cash(usd)
            self.swap_total += usd
        self._last_sd = sd

    def log(self, txt):
        if self.p.print_log:
            print(f"{self.data.datetime.datetime(0)} {txt}")

    # ---------------- 主循环(轮询状态机: 不依赖 notify_order 身份) ----------------
    # ---------------- 当前 bar 点差(points) ----------------
    def current_spread_points(self, utc):
        """当前 bar 点差(points)。优先用真实逐笔点差(来自 TMGM 历史导出 <SPREAD> 列,
        单位=points, 与 EA 的 SYMBOL_SPREAD 一致); 无真实值(如 1h 数据或未取到)则回退到
        服务器小时流动性 proxy。实测真实序列: 中位 8 / 均值 8.2 / p95 12 / 极端 max 150,
        仅 8 根 >50; 故在默认阈值 50 下过滤几乎不触发(与 EA 实时 SYMBOL_SPREAD 远小于 50 同理)。"""
        if self.p.spread_series is not None:
            try:
                val = self.p.spread_series.get(pd.Timestamp(utc))
                if val is not None and not (isinstance(val, float) and np.isnan(val)):
                    return float(val)
            except Exception:
                pass
        # 回退: 服务器小时流动性 profile(仅无真实序列时使用)
        h = utc.hour % 24 if self.p.server_time else (utc.hour + tmgm_server_offset_hours(utc)) % 24
        if 15 <= h <= 21:       # 伦敦+纽约重叠(最流动)
            return 20.0
        if 8 <= h <= 14:        # 伦敦早段
            return 28.0
        if 22 <= h or h <= 7:   # 亚盘 + 美股尾盘/pre-swap(最宽)
            return 45.0
        return 30.0

    def next(self):
        self.equity.append((self.data.datetime.datetime(0), self.broker.getvalue()))
        self._charge_swap()
        utc = bt.num2date(self.data.datetime[0])
        off = tmgm_server_offset_hours(utc)
        server_hour = utc.hour % 24 if self.p.server_time else (utc.hour + off) % 24

        # A. 对账: 清理 notify_trade 已平仓标记(_closed)的持仓; 并逐笔核对 broker 该槽位实际仓位 —
        #    方向不符(已被平/转空, 如旧 SL 在 cancel 生效前成交、或 stuck 未触发) → 兜底清理(根治僵尸仓)。
        #    注: 实际平仓判定统一在 notify_trade(broker 权威每笔成交)。此处做净值对账 + 兜底, 对所有 max 生效。
        still_open = []
        for pos in self.open_positions:
            if pos.get("_closed"):
                continue
            bsz = self.broker.getposition(pos["data"]).size
            d = pos["trade_info"].get("dir")
            if (d == 1 and bsz <= 1e-9) or (d == -1 and bsz >= -1e-9):
                # broker 已无该方向仓位 → 旧 SL/手动等已平(或 stuck), 兜底清理(防扛单)
                reason = pos.get("_close_reason") or "sl"
                self._log_close(pos, reason, pos["data"].close[0])
                self._reset_trade(pos)
                continue
            still_open.append(pos)
        self.open_positions = still_open
        # 兜底: 任一槽位 broker 仍有孤儿仓位(未被 open_positions 引用) → 平掉(防 stuck 持仓无限扛单)
        used_data = {id(p["data"]) for p in self.open_positions}
        for d in self.slot_feeds:
            if id(d) in used_data:
                continue
            if abs(self.broker.getposition(d).size) > 1e-9:
                self.close(data=d)

        # B. 持仓中: 挂 SL/TP(若未挂) + 追踪 + 隔夜强平(逐笔独立)
        for pos in self.open_positions:
            if pos.get("_closed") or pos["trade_info"].get("dir") is None:
                if not pos.get("_closed"):
                    self._manual_close(pos, "manual")
                continue
            if pos["sl_order"] is None and pos["tp_order"] is None:
                # 入场单已知成交(本根之前) → 挂 broker 端 SL/TP; 仍在途则等下一根
                if pos["entry_order"] is not None and pos["entry_order"].status == bt.Order.Completed:
                    # 仅当 broker 在该槽位实际仍持有该方向仓位时才挂单(防平盘卖单转空)
                    bsz = self.broker.getposition(pos["data"]).size
                    d = pos["trade_info"].get("dir")
                    if (d == 1 and bsz > 1e-9) or (d == -1 and bsz < -1e-9):
                        self._attach_sl_tp(pos)
            else:
                if self.p.trailing and pos["sl_order"] is not None and pos["sl_order"].alive():
                    self._maybe_trail(pos)
                # 本根 SL/TP/手动单已成交(仓位正在平) → 交给 notify_trade 处理, 不再做 eod/其他
                closing = (
                    (pos["sl_order"] is not None and pos["sl_order"].status == bt.Order.Completed) or
                    (pos["tp_order"] is not None and pos["tp_order"].status == bt.Order.Completed) or
                    (pos["manual_order"] is not None and pos["manual_order"].status == bt.Order.Completed))
                if closing:
                    continue
                if self.p.avoid_overnight:
                    if server_hour >= self.p.server_close_hour - 2:
                        _, rel = self.classify_channel()
                        strong = abs(rel) > self.p.strong_overnight_thresh
                        c = self.data.close[0]
                        ti = pos["trade_info"]
                        in_profit = (c > ti["entry"]) if ti.get("dir") == 1 else (c < ti["entry"])
                        if not (strong and in_profit):
                            self._manual_close(pos, "eod")
                            self.log(f"EOD close @ {c:.2f} (overnight avoided)")

        # C. 入场单在途(拒单/取消/保证金不足) → 重置该笔; 否则等待成交
        _cur_bar = len(self.data)
        for pos in self.open_positions:
            eo = pos["entry_order"]
            if eo is not None and eo.status in (
                    bt.Order.Canceled, bt.Order.Rejected, bt.Order.Margin):
                self._reset_trade(pos)
                continue
            # 限价单未成交超期作废(防陈旧单在远离信号的价位成交)
            if (self.p.limit_entry and eo is not None and eo.alive()
                    and (_cur_bar - pos.get("_entry_bar", _cur_bar)) > self.p.limit_valid_bars):
                self.cancel(eo)
                self._reset_trade(pos)
        pending_entry = any(
            p["entry_order"] is not None and p["entry_order"].status in (
                bt.Order.Submitted, bt.Order.Accepted)
            for p in self.open_positions)

        # D. 信号开仓(在仓数 < 上限 且无入场单在途)
        if len(self.open_positions) < self.p.max_positions and not pending_entry:
            if self._margin_call():
                return
            # 时段过滤(对齐 EA InpTradeStartHour/EndHour, DST 自动: 夏 GMT+3 起点=trade_start_hour, 冬 GMT+2 起点-1)
            if self.p.session_filter:
                eff_start = (self.p.trade_start_hour
                             if (self.p.server_time or off == 3)
                             else self.p.trade_start_hour - 1)
                if not (eff_start <= server_hour < self.p.trade_end_hour):
                    self._n_session_skip += 1
                    return
            # 点差过滤(对齐 EA InpMaxSpreadPoints; 用 TMGM 真实逐笔点差比对, 无则回退 proxy)
            if self.p.spread_filter and self.current_spread_points(utc) > self.p.max_spread_points:
                self._n_spread_skip += 1
                return
            channel, rel = self.classify_channel()
            if channel == "warmup":
                return
            short_dir = self.classify_short_direction()
            if channel == "up" and short_dir < 0:
                return
            if channel == "down" and short_dir > 0:
                return

            support, resistance = self._get_sr()
            if support is None or resistance is None:
                return

            # 冷却 + 开仓
            # 非并发模式: 全局同方向冷却(任一槽位开仓后 cooldown_bars 内全市场禁同方向) + 每根只开1个空闲槽位
            # 并发模式(concurrent): 逐槽位冷却(仅阻止同一槽位短期重复) + 每信号bar允许所有空闲槽位同开(金字塔)
            # A 方案(slot_styles): 每槽位可用不同 entry_style, 故 want/趋势/RSI/冷却/止损 均逐槽位计算
            bar = len(self.data)
            o, h, l, c = self.data.open[0], self.data.high[0], self.data.low[0], self.data.close[0]
            rsi = self.rsi[0]
            rm = self._adaptive_rmult(rel)
            used = {id(p["data"]) for p in self.open_positions}
            for idx, d in enumerate(self.slot_feeds):
                if id(d) in used:
                    continue
                if abs(self.broker.getposition(d).size) > 1e-9:
                    continue
                # 该槽位自己的入场逻辑
                want = self.want_entry(channel, support, resistance,
                                       style=self._style_for_slot(idx))
                if want is None:
                    continue
                # 趋势过滤器(第5步)
                if not self._trend_filter_pass(want):
                    continue
                # RSI 极端过滤
                if rsi == rsi:
                    if channel == "up" and rsi > self.p.rsi_overbought:
                        continue
                    if channel == "down" and rsi < self.p.rsi_oversold:
                        continue
                if self.p.concurrent:
                    # 错峰: 本笔须距上一笔开仓 >=stagger_bars 根(slot k 比 slot k-1 晚 X 根才允许)
                    if self.p.stagger_bars > 0 and (bar - self._last_open_bar) < self.p.stagger_bars:
                        break   # 本根配额已用/未满间隔, 等后续 bar 再错峰开下一笔
                    leb = self._last_entry_bar_by_data.get(id(d))
                    if leb is not None and self._last_entry_side_by_data.get(id(d)) == want \
                            and (bar - leb) < self.p.cooldown_bars:
                        continue
                else:
                    if self._last_entry_side == want and (bar - self._last_entry_bar) < self.p.cooldown_bars:
                        return
                stop, target = self.risk_levels(want, c, support, resistance, r_mult=rm)
                # broker 端: 市价入场(绑定槽位 feed), SL/TP 由 broker 在持仓开后挂出(盘中扫损/止盈)
                self._open_position(want, c, stop, target, rm, rsi, slot_feed=d,
                                    support=support, resistance=resistance)
                if not self.p.concurrent:
                    break  # 原逻辑: 每根只开一个空闲槽位


# --------------------------------------------------------------------------
# 数据加载 (支持训练/测试切分)
# --------------------------------------------------------------------------
def load_data(freq, fromdate=None, todate=None, spread_source="real"):
    path = os.path.join(CLEAN_DIR, f"xauusd_{freq}_utc.csv")
    df = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if fromdate:
        df = df[df.index >= fromdate]
    if todate:
        df = df[df.index <= todate]
    spread_series = None
    if freq in ("15m", "5m") and spread_source == "real":
        # 真实逐笔点差(单位 points): 来自 TMGM 历史导出, 与 EA SYMBOL_SPREAD 同口径
        spread_series = _build_real_spread(freq).reindex(df.index)
    return df, bt.feeds.PandasData(dataname=df), spread_series


def load_server_data(freq, fromdate=None, todate=None):
    """加载经纪商服务器时间 bar(不做 UTC 偏移转换), 与 EA 一致。
    数据来自 server_data/xauusd_{freq}_server.csv(由 build_server_time_data.py 从 TMGM 原生
    M15/H1 导出构建); 点差用同目录 xauusd_{freq}_server_spread.csv(服务器时间索引)。"""
    sdir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "server_data")
    path = os.path.join(sdir, f"xauusd_{freq}_server.csv")
    df = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if fromdate:
        df = df[df.index >= fromdate]
    if todate:
        df = df[df.index <= todate]
    spread_series = None
    if freq in ("15m", "5m"):
        sp_path = os.path.join(sdir, f"xauusd_{freq}_server_spread.csv")
        if os.path.exists(sp_path):
            spread_series = pd.read_csv(sp_path, parse_dates=["datetime"]).set_index("datetime")["spread"].astype(float).reindex(df.index)
    return df, bt.feeds.PandasData(dataname=df), spread_series


def run_backtest(freq="15m", extra_args=None, cash=1000.0, leverage=1000.0,
                 lot=0.01, spread=0.33, commission=0.0,
                 fromdate=None, todate=None, quiet=True,
                 primary_freq=None, context_freqs=None,
                 server_time=False, strategy_cls=BoxChannelOptStrategy):
    """primary_freq/context_freqs 用于非 15m 主周期(如 M5 策略)。
    向后兼容: 不传 primary_freq 时 context_freqs 默认 ("1h",), 与历史行为逐字节一致。"""
    cerebro = bt.Cerebro()
    cerebro.addsizer(FixedLotSizer, lot=lot)

    pfreq = primary_freq or freq or "15m"
    if context_freqs is None:
        context_freqs = ("1h",)

    if server_time:
        df_primary, _, sp_primary = load_server_data(pfreq, fromdate, todate)
    else:
        df_primary, _, sp_primary = load_data(pfreq, fromdate, todate)
    # 主周期按 max_positions 克隆多份「独立数据源」: backtrader 对同一数据源是净头寸模式,
    # 多笔"独立持仓"会被合并为单一净仓位, 导致逐笔 SL/TP 与 notify_trade 配对错乱(巨亏)。
    # 每槽位一份独立 feed → broker 为每槽位维护独立持仓, 互不净撮合, 实现真正的多仓位并发。
    _ss = (extra_args or {}).get("slot_styles")
    n_slots = max(int((extra_args or {}).get("max_positions", 1)), 1,
                  len(_ss) if _ss else 1)
    slot_feeds = []
    for i in range(n_slots):
        feed = bt.feeds.PandasData(dataname=df_primary)
        cerebro.adddata(feed, name=f"slot{i}")
        slot_feeds.append(feed)
    ctx_map = {}
    for cf in context_freqs:
        if server_time:
            _, data_cf, _ = load_server_data(cf, fromdate, todate)
        else:
            _, data_cf, _ = load_data(cf, fromdate, todate)
        cerebro.adddata(data_cf, name=cf)
        ctx_map[cf] = data_cf
    # 上下文通道 feed: 优先 "1h"(与主周期 15m 对应); 无则取第一个上下文周期
    ctx_data = ctx_map.get("1h")
    if ctx_data is None and ctx_map:
        ctx_data = next(iter(ctx_map.values()))

    strat_kwargs = dict(extra_args or {})
    strat_kwargs["server_time"] = server_time
    cerebro.addstrategy(strategy_cls, slot_feeds=slot_feeds,
                        ctx_data=ctx_data, spread_series=sp_primary, **strat_kwargs)

    comminfo = bt.CommissionInfo(commission=commission, mult=100.0,
                                 leverage=leverage, stocklike=False)
    cerebro.broker.addcommissioninfo(comminfo)
    cerebro.broker.setcash(cash)
    cerebro.broker.set_slippage_fixed(fixed=spread / 2.0)

    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="ta")
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")

    results = cerebro.run(optreturn=False)
    strat = results[0]
    ret = strat.analyzers.returns.get_analysis()
    dd = strat.analyzers.dd.get_analysis()
    sharpe = strat.analyzers.sharpe.get_analysis()
    sh_v = sharpe.get("sharperatio", float("nan"))
    if sh_v is None:
        sh_v = float("nan")

    final = cerebro.broker.getvalue()
    total_ret = final / cash - 1.0
    days = (df_primary.index[-1] - df_primary.index[0]).days
    years = max(days, 1) / 365.0
    ann = (1.0 + total_ret) ** (1.0 / years) - 1.0 if total_ret > -1 else -1.0

    # 交易统计: 主指标(笔数/胜率/PF/盈亏)取自 broker 权威 TradeAnalyzer, 不受 trades_log
    # 边缘用例影响; 隔夜平笔数(eod)等归因仍用策略自维护 trades_log。
    ta = strat.analyzers.ta.get_analysis()
    closed = ta.get("total", {}).get("closed", 0)
    won = ta.get("won", {}).get("total", 0)
    lost = ta.get("lost", {}).get("total", 0)
    win_rate = won / closed if closed > 0 else 0.0
    pnl_won = ta.get("won", {}).get("pnl", {}).get("total", 0.0) or 0.0
    pnl_lost = ta.get("lost", {}).get("pnl", {}).get("total", 0.0) or 0.0
    gross_profit = pnl_won
    gross_loss = -pnl_lost if pnl_lost < 0 else pnl_lost
    pf = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")
    realized_ret = (gross_profit - gross_loss) / cash
    # FIFO 配对(仅用于交叉核验, 不参与主指标)
    tl = strat.trades_log
    open_stack = []
    paired = []
    for t in tl:
        if t["kind"] == "open":
            open_stack.append(t)
        elif t["kind"] == "close":
            if open_stack:
                paired.append((open_stack.pop(), t))
    overnight = sum(1 for t in tl if t["kind"] == "close" and t.get("reason") == "eod")
    if not quiet:
        print(f"[{freq}] ret={total_ret*100:+.1f}% ann={ann*100:+.1f}% "
              f"DD={dd.max.drawdown:.1f}% sharpe={sh_v:.2f} PF={pf:.2f} "
              f"trades={closed} win={win_rate*100:.1f}% eod={overnight} swap={strat.swap_total:+.1f} "
              f"sess_skip={strat._n_session_skip} spread_skip={strat._n_spread_skip}")
    return dict(final=final, total_ret=total_ret, ann=ann,
                maxdd=dd.max.drawdown, sharpe=sh_v,
                closed=closed, won=won, lost=lost, win_rate=win_rate,
                gross_profit=gross_profit, gross_loss=gross_loss, pf=pf,
                realized_ret=realized_ret,
                entries=strat.n_entries, swap_total=strat.swap_total,
                overnight_eod=overnight, eq=strat.equity, trades=strat.trades_log,
                trade_pnls=getattr(strat, "trade_pnls", []),
                trade_episodes=getattr(strat, "trade_episodes", []),
                session_skip=strat._n_session_skip, spread_skip=strat._n_spread_skip)


# 推荐画像(来自 analyze_box_opt 扫描的 TEST 验证结果, 供 --profile 与 walk_forward 复用)
# ★ 最终采用(2026-09-05): scalp = 趋势过滤 MA96(ma_filter_period=96) 为主策略,
#   在 FULL/趋势段S5/最弱段S3 同改善且无段受损(见 scalp_trendfilter_report.md)。
#   scalp_raw = 无过滤基线对照(历史口径, 保留供比较); scalp_tf48 = 纯S3靶向变体(ma48)。
PROFILES = {
    "scalp": dict(entry_style="box", avoid_overnight=True, r_mult=2.5,  # ★ Stage3.2 网格(2026-09-17): r=2.5/act=2.0/fol=1.0/be=0.0 → OOS+105.9% DD9.47% calmar9.05(全网格最高), 且IS转正+7.4%(act=2.0唯一跨regime稳健区); EA须同步 InpRmult + 激活门限
                  atr_stop=True, atr_stop_mult=1.5, trailing=True,
                  trail_activate_atr=2.0, trail_atr_mult=1.0, trail_be_buffer=0.0,  # ★ 两段式追踪: 激活门限从1.0→2.0(让盈利达2×ATR才启动追踪, IS转正+跨regime); 跟随1.0/缓冲0(缓冲实证无效/早激活有害)
                  cooldown_bars=8, rel_slope_thresh=0.0004, short_win=8, min_k=2,
                  ma_filter=True, ma_filter_period=96,
                  atr_regime_gate=True, atr_regime_mult=1.2,
                  max_ma_dist_atr=2.5, ma_dist_regime_cond=True, ma_dist_lowvol_mult=0.75),  # ★ 远离趋势过滤(仅低波动段 atr_now/atr_avg<0.75 启用, 趋势/突破段放行)防毛刺扫损; 0.75/K2.5=贴未过滤基线最佳折中(464笔/+73.1%/DD8.39/PF1.40, 距基线541仅77笔)
    "scalp_raw": dict(entry_style="box", avoid_overnight=True, r_mult=1.75,
                  atr_stop=True, atr_stop_mult=1.0, trailing=True,
                  cooldown_bars=2, rel_slope_thresh=0.0004, short_win=4, min_k=2),  # 无过滤基线对照
    "scalp_adapt": dict(entry_style="box", avoid_overnight=True, r_mult=1.75,
                  atr_stop=True, atr_stop_mult=1.0, trailing=True,
                  cooldown_bars=2, rel_slope_thresh=0.0004, short_win=4, min_k=2,
                  adaptive_rmult=True, rmult_range=1.5, rmult_trend=2.3,
                  rmult_ref_lo=0.0006, rmult_ref_hi=0.0025),
    "scalp_tf48": dict(entry_style="box", avoid_overnight=True, r_mult=1.75,
                  atr_stop=True, atr_stop_mult=1.0, trailing=True,
                  cooldown_bars=2, rel_slope_thresh=0.0004, short_win=4, min_k=2,
                  ma_filter=True, ma_filter_period=48),  # ma48(12h) 纯S3靶向变体: S3 改善最大, FULL 几乎不变
    # ★ 反转过滤条件化(2026-09 落地, 替代 scalp 的常开): 仅强趋势段(|MA96相对斜率|>=5.1e-5)开启反转K线确认
    #   训练段校准 + 留出验证(Round5/6) 全胜, 样本外 +24.6pp 累计收益(见 use_reversal_filter_ab_report.md)。
    "scalp_rev_strong": dict(entry_style="box", avoid_overnight=True, r_mult=1.75,
                  atr_stop=True, atr_stop_mult=1.5, trailing=True,
                  trail_activate_atr=1.0, trail_atr_mult=1.0, trail_be_buffer=0.0,
                  cooldown_bars=8, rel_slope_thresh=0.0004, short_win=8, min_k=2,
                  ma_filter=True, ma_filter_period=96,
                  atr_regime_gate=True, atr_regime_mult=1.2,
                  max_ma_dist_atr=2.5, ma_dist_regime_cond=True, ma_dist_lowvol_mult=0.75,
                  use_reversal_filter=True, rev_mode="rev_in_strong", rev_strong_thresh=5.1e-5),
    "scalp_tf_atr": dict(entry_style="box", avoid_overnight=True, r_mult=1.75,
                  atr_stop=True, atr_stop_mult=1.0, trailing=True,
                  cooldown_bars=2, rel_slope_thresh=0.0004, short_win=4, min_k=2,
                  atr_regime_gate=True, atr_regime_period=48, atr_regime_mult=1.4),
    "mid":   dict(entry_style="box", avoid_overnight=True, r_mult=2.0,
                  atr_stop=False, atr_stop_mult=1.5, trailing=False,
                  cooldown_bars=4, rel_slope_thresh=0.0006, short_win=6, min_k=3,
                  sl_buffer=2.0),
    "long":  dict(entry_style="ma_cross", avoid_overnight=False, r_mult=3.0,
                  atr_stop=True, atr_stop_mult=2.0, trailing=True, cooldown_bars=8,
                  rel_slope_thresh=0.0002, short_win=32, min_k=3,
                  ma_fast=10, ma_slow=60, trend_window=96),
}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry-style", default="box", choices=["box", "ma_cross", "boll", "donchian"])
    ap.add_argument("--freq", default="15m")
    ap.add_argument("--cash", type=float, default=1000.0)
    ap.add_argument("--leverage", type=float, default=1000.0)
    ap.add_argument("--lot", type=float, default=0.01)
    ap.add_argument("--max-positions", type=int, default=1,
                    help="最大并发持仓数(1=原单仓位行为, 与 EA 一致; >1 允许金字塔/并发, "
                         "当前在仓数<该值即可开新仓, 每笔固定 lot 手)")
    ap.add_argument("--spread", type=float, default=0.33)
    ap.add_argument("--r-mult", type=float, default=2.0,
                    help="止盈倍数(风险回报), 推荐 2.0")
    ap.add_argument("--sl-buffer", type=float, default=2.0,
                    help="固定美元止损缓冲(仅 atr_stop 关时生效)")
    ap.add_argument("--trend-window", type=int, default=24)
    ap.add_argument("--short-win", type=int, default=6,
                    help="支撑/压力聚类窗口(根数), 推荐 6")
    ap.add_argument("--atr-stop", action="store_true",
                    help="用 ATR 倍数止损(否则固定美元 sl_buffer)")
    ap.add_argument("--atr-stop-mult", type=float, default=1.5)
    ap.add_argument("--atr-shift", type=int, default=0, choices=[0, 1],
                    help="ATR 取数位移: 0=当前bar(默认, backtrader 口径); "
                         "1=上一根已收盘bar(与 EA GetATRVal(1) 对齐)")
    ap.add_argument("--trailing", action="store_true", help="追踪止盈")
    ap.add_argument("--trail-activate-atr", type=float, default=0.0,
                    help="两段式追踪: 浮盈达此×ATR 才启动(0=始终; 原行为)")
    ap.add_argument("--trail-atr-mult", type=float, default=1.5,
                    help="两段式追踪: 跟随距离(×ATR), <1.5 更早锁利")
    ap.add_argument("--trail-be-buffer", type=float, default=0.0,
                    help="两段式追踪: 启动后 SL 弹到 entry±此×ATR 保本带(0=关)")
    ap.add_argument("--partial-tp", type=float, default=0.0,
                    help="半仓锁利比例 0~1, 0=关")
    ap.add_argument("--rel-slope", type=float, default=0.0006,
                    help="通道灵敏度阈值")
    ap.add_argument("--cooldown-bars", type=int, default=4,
                    help="同方向再次入场冷却(根数)")
    ap.add_argument("--min-k", type=int, default=3,
                    help="密集支撑/压力带最少 K 线数")
    ap.add_argument("--allow-overnight", action="store_true",
                    help="允许隔夜(默认避免隔夜, 仅强信号+浮盈才放行)")
    ap.add_argument("--profile", default=None, choices=["scalp", "scalp_raw", "scalp_adapt", "scalp_tf48", "scalp_tf_atr", "scalp_rev_strong", "mid", "long"],
                    help="一键套用推荐画像: scalp=★已采用短线(趋势过滤MA96) / scalp_raw=无过滤基线 / scalp_tf48=纯S3靶向(MA48) / scalp_rev_strong=反转过滤仅强趋势段开启(2026-09落地) / mid=中线 / long=长线")
    ap.add_argument("--sr-source", default=None, choices=["1h", "daily", "monthly", "combined"],
                    help="支撑压力位来源: 不传=原cluster_levels; 1h/daily/monthly=新模块单档; combined=三档最近水平")
    ap.add_argument("--limit-entry", dest="limit_entry", action="store_true", default=False,
                    help="限价单入场(Stage 3.2): 挂 Limit 单在更优价位贴近支撑买/贴近阻力卖, 而非市价买入")
    ap.add_argument("--no-limit-anchor", dest="limit_anchor_level", action="store_false", default=True,
                    help="限价单 SL/TP 不锚定价位(沿用原 atr_stop, 作对照: 限价零收益)")
    ap.add_argument("--limit-buffer-atr", type=float, default=0.5,
                    help="限价单缓冲(×ATR): 买价=支撑+此×ATR; 及止盈贴近阻力/支撑的缓冲")
    ap.add_argument("--limit-valid-bars", type=int, default=4,
                    help="未成交限价单作废根数")
    ap.add_argument("--fromdate", default=None, help="开始日期 YYYY-MM-DD")
    ap.add_argument("--todate", default=None, help="结束日期 YYYY-MM-DD")
    ap.add_argument("--session-filter", dest="session_filter", action="store_true", default=True,
                    help="开仓时段过滤(对齐 EA; 夏起点=trade-start-hour, 冬自动-1)")
    ap.add_argument("--no-session-filter", dest="session_filter", action="store_false",
                    help="关闭时段过滤")
    ap.add_argument("--spread-filter", dest="spread_filter", action="store_true", default=True,
                    help="开仓点差过滤(>max-spread-points 跳过, 对齐 EA InpMaxSpreadPoints)")
    ap.add_argument("--no-spread-filter", dest="spread_filter", action="store_false",
                    help="关闭点差过滤")
    ap.add_argument("--max-spread-points", type=float, default=50.0,
                    help="点差上限(points), >则跳过入场(默认 50; 用 TMGM 真实逐笔点差比对, 中位8几乎不挡)")
    ap.add_argument("--trade-start-hour", type=int, default=1,
                    help="交易起点(服务器时, 夏令时 GMT+3 基准)=北京6:00; 冬令时自动-1")
    ap.add_argument("--trade-end-hour", type=int, default=22,
                    help="交易终点(服务器时) 对应 EA InpTradeEndHour")
    ap.add_argument("--rev-mode", default="always_on",
                    choices=["always_on", "always_off", "rev_in_strong", "rev_in_weak"],
                    help="反转K线过滤生效模式: always_on=恒开(默认, 对齐历史) / always_off=恒关 / "
                         "rev_in_strong=仅强趋势段开启(MA96相对斜率>=阈值) / rev_in_weak=仅弱趋势段开启")
    ap.add_argument("--rev-strong-thresh", type=float, default=5.1e-5,
                    help="强趋势判定阈值(|MA96 20-bar 相对斜率|), 默认 5.1e-5(训练段2023-2025校准)")
    ap.add_argument("--server-time", dest="server_time", action="store_true", default=False,
                    help="用经纪商服务器时间 bar 回测(读 server_data/xauusd_*_server.csv), 对齐 EA, 消除时区 bar 偏移")
    args = ap.parse_args()
    extra = dict(
        entry_style=args.entry_style, r_mult=args.r_mult, sl_buffer=args.sl_buffer,
        lot=args.lot, max_positions=args.max_positions,
        trend_window=args.trend_window, short_win=args.short_win,
        atr_stop=args.atr_stop, atr_stop_mult=args.atr_stop_mult,
        atr_shift=args.atr_shift,
        trailing=args.trailing, partial_tp=args.partial_tp,
        trail_activate_atr=args.trail_activate_atr,
        trail_atr_mult=args.trail_atr_mult,
        trail_be_buffer=args.trail_be_buffer,
        rel_slope_thresh=args.rel_slope, cooldown_bars=args.cooldown_bars,
        min_k=args.min_k, avoid_overnight=not args.allow_overnight,
        session_filter=args.session_filter, spread_filter=args.spread_filter,
        max_spread_points=args.max_spread_points,
        trade_start_hour=args.trade_start_hour, trade_end_hour=args.trade_end_hour,
        rev_mode=args.rev_mode, rev_strong_thresh=args.rev_strong_thresh,
        sr_source=args.sr_source,
        limit_entry=args.limit_entry, limit_anchor_level=args.limit_anchor_level,
        limit_buffer_atr=args.limit_buffer_atr, limit_valid_bars=args.limit_valid_bars,
    )
    if args.profile:
        extra.update(PROFILES[args.profile])
    m = run_backtest(freq=args.freq, extra_args=extra, cash=args.cash,
                     leverage=args.leverage, lot=args.lot, spread=args.spread,
                     fromdate=args.fromdate if args.fromdate else None,
                     todate=args.todate if args.todate else None,
                     server_time=args.server_time, quiet=False)
    print("=" * 56)
    if args.profile:
        print(f"画像: {args.profile}  (entry={extra.get('entry_style')})")
    print(f"累计收益 {m['total_ret']*100:+.2f}%  年化 {m['ann']*100:+.2f}%  "
          f"最大回撤 {m['maxdd']:.2f}%  Sharpe {m['sharpe']:.2f}")
    print(f"过滤: 时段挡掉 {m['session_skip']} 笔开仓尝试, 点差挡掉 {m['spread_skip']} 笔开仓尝试 "
          f"(session={args.session_filter}, spread={args.spread_filter}, max_spread={args.max_spread_points})")
    print(f"交易 {m['closed']} 笔  胜率 {m['win_rate']*100:.1f}%  "
          f"隔夜平 {m['overnight_eod']} 笔  隔夜费 {m['swap_total']:+.2f}USD")
