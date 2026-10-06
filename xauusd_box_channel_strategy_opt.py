#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
XAU/USD 优化版策略 (拷贝自 xauusd_box_channel_strategy.py, 原文件保持不变)

设计目标
--------
1. **可切换指标族** (signal_mode): 支撑压力反转 / RSI 极端反转 / ATR 通道突破 /
   布林回归 / MACD 交叉 / EMA 交叉 —— 用同一套风控与撮合框架做公平对比。
2. **双周期**: 主周期 self.data, 上一档周期 self.data1 (15m→1h, 1h→4h, 4h→1d, 1d→无)。
3. **隔夜控制**: TMGM 服务器零点(夏 UTC21:00/冬22:00)跨日前强制平仓, 规避隔夜费;
   可选"强信号破例"(多信号共振才允许持隔夜), 但默认关闭以防过拟合。
4. **防未来函数**: 所有信号用当前已收盘 bar 的确定数据; 突破用上一根通道边界;
   订单为市价单, 下一根开盘成交。
5. **统一 Sharpe 口径**: 由资金曲线自算(按周期年化), 避免 box/gold 两版口径不一致。

用法:
  python xauusd_box_channel_strategy_opt.py --freq 15m --preset ultra_short
"""
import os
import argparse
import datetime as dt

import numpy as np
import pandas as pd
import backtrader as bt

CLEAN_DIR = r"D:\Data\stockdata\xauusd\clean"

# 主周期 -> 上一档周期 (辅助周期, 用于通道方向判定)
HIGHER_TF = {"15m": "1h", "1h": "4h", "4h": "1d", "1d": None}
FREQ_FILE = {
    "15m": "xauusd_15m_utc.csv",
    "1h": "xauusd_1h_utc.csv",
    "4h": "xauusd_4h_utc.csv",
    "1d": "xauusd_daily_utc.csv",
}
# 每根 bar 的分钟数 (用于年化 Sharpe 与隔夜判定)
FREQ_MIN = {"15m": 15, "1h": 60, "4h": 240, "1d": 1440}


# --------------------------------------------------------------------------
# 工具
# --------------------------------------------------------------------------
def tmgm_server_offset_hours(utc_dt):
    """TMGM 服务器时区偏移(小时): 跟随美国夏令时, 夏 GMT+3 / 冬 GMT+2。
    服务器零点 = UTC 21:00(夏) / 22:00(冬), 隔夜费在跨服务器日时收取。"""
    year = utc_dt.year
    # 美国 DST: 3月第二个周日 02:00 起, 11月第一个周日 02:00 止
    mar = dt.date(year, 3, 1)
    dst_start = mar + dt.timedelta(days=(6 - mar.weekday()) % 7 + 7)
    nov = dt.date(year, 11, 1)
    dst_end = nov + dt.timedelta(days=(6 - nov.weekday()) % 7)
    d = utc_dt.date()
    return 3 if (d > dst_start and d < dst_end) else 2


class FixedLotSizer(bt.Sizer):
    """固定手数: 返回 lot (合约数), 配合 CommissionInfo(mult=100) 换算盎司。
    注意: 不能用 bt.sizers.FixedSize 替代 (其 stake 是单位数, 会与 mult 重复计算)。"""
    params = dict(lot=0.01)

    def _getsizing(self, comminfo, cash, data, isbuy):
        return self.p.lot


# --------------------------------------------------------------------------
# 策略主体
# --------------------------------------------------------------------------
class OptStrategy(bt.Strategy):
    params = dict(
        # ---- 信号族 ----
        signal_mode="sr_reversal",   # sr_reversal / rsi_reversal / atr_breakout
                                     # / boll_reversion / macd_cross / ema_cross
        freq="15m",                  # 主周期(由 run_backtest 注入), 隔夜判定需要
        # ---- 箱体/支撑压力 (原有) ----
        trend_window=24,       # 辅助周期根数 -> 通道判定窗口
        short_win=8,           # 主周期根数 -> 短期支撑压力
        long_win=4,            # 辅助周期根数 -> 长期支撑压力
        min_k=3,               # 密集带最少 K 线数
        cluster_bins=20,
        rel_slope_thresh=0.0006,
        rev_tol=0.0015,
        touch_count=2,
        min_wick_ratio=0.6,
        # ---- 风控 ----
        r_mult=1.5,            # 止盈/止损 风险倍数
        sl_buffer=2.0,         # 止损相对触发极值的美元外推
        atr_mult=2.0,          # ATR 止损倍数 (非箱体模式)
        atr_period=14,
        # ---- 过滤 ----
        use_reversal_filter=True,
        use_trend_filter=True,     # 是否要求大周期方向一致
        allow_long=True,           # 方向开关(用于检验隔夜费造成的方向偏斜:
        allow_short=True,          # TMGM 多单-72.5点/晚, 空单+30.72点/晚(返息))
        short_trend_n=20,          # 主周期短期方向窗口
        cooldown_bars=4,           # 同方向入场冷却(主周期根)
        rsi_period=14,
        rsi_overbought=70,
        rsi_oversold=30,
        session_start=0,           # 交易时段过滤(UTC 小时), 0~23 表示不限制时用 0/24
        session_end=24,
        # ---- 指标周期 ----
        donchian_n=20,         # 唐奇安通道窗口(突破模式)
        boll_period=20,
        boll_dev=2.0,
        macd_fast=12, macd_slow=26, macd_signal=9,
        ema_fast=8, ema_slow=21,
        # ---- 隔夜控制 ----
        force_close_overnight=True,   # 服务器零点前强制平仓(规避隔夜费)
        allow_overnight_override=False,  # 是否允许"强信号破例"持隔夜(默认关, 防过拟合)
        overnight_confluence=3,       # 破例所需的最小共振信号数
        # ---- 成本 ----
        swap_long_points=-72.5,
        swap_short_points=30.72,
        swap_point_value=0.01,
        contract_oz=100.0,
        print_log=False,
    )

    def __init__(self):
        self.order = None
        self.trade_info = {}
        self.equity = []
        self.n_entries = 0
        self.trades_log = []
        self._close_reason = None
        self._prev_pos = 0
        self._last_sd = None
        self.swap_total = 0.0
        self._last_entry_bar = -999
        self._last_entry_side = None
        self.n_forced_close = 0      # 因隔夜控制被强平的次数
        self.n_overnight_held = 0    # 破例持隔夜的次数

        has1 = len(self.datas) > 1
        d1 = self.datas[1] if has1 else self.data

        # 指标 (backtrader 惰性计算, 未用到的代价很小)
        self.rsi = bt.indicators.RSI(self.data.close, period=self.p.rsi_period)
        self.atr = bt.indicators.ATR(self.data, period=self.p.atr_period)
        self.boll = bt.indicators.BollingerBands(
            self.data.close, period=self.p.boll_period, devfactor=self.p.boll_dev)
        self.macd = bt.indicators.MACD(
            self.data.close, period_me1=self.p.macd_fast,
            period_me2=self.p.macd_slow, period_signal=self.p.macd_signal)
        self.macd_cross = bt.indicators.CrossOver(self.macd.macd, self.macd.signal)
        self.ema_f = bt.indicators.EMA(self.data.close, period=self.p.ema_fast)
        self.ema_s = bt.indicators.EMA(self.data.close, period=self.p.ema_slow)
        self.ema_cross = bt.indicators.CrossOver(self.ema_f, self.ema_s)
        # 唐奇安通道 (突破用上一根边界, 避免未来函数)
        self.don_h = bt.indicators.Highest(self.data.high, period=self.p.donchian_n)
        self.don_l = bt.indicators.Lowest(self.data.low, period=self.p.donchian_n)

        self._has1 = has1
        self._d1 = d1

    # ---------------- 通道方向 (辅助周期) ----------------
    def classify_channel(self):
        n = self.p.trend_window
        d = self._d1
        if len(d) < n + 1:
            return "warmup"
        closes = np.array(d.close.get(size=n), dtype=float)
        slope = np.polyfit(np.arange(n), closes, 1)[0]
        mid = float(np.mean(closes))
        rel = slope / mid if mid > 0 else 0.0
        cur = d.close[0]
        if rel > self.p.rel_slope_thresh and cur >= mid:
            return "up"
        if rel < -self.p.rel_slope_thresh and cur <= mid:
            return "down"
        return "range"

    # ---------------- 主周期短期方向 ----------------
    def classify_short_direction(self):
        n = self.p.short_trend_n
        if len(self.data) < n + 1:
            return 0
        closes = np.array(self.data.close.get(size=n), dtype=float)
        slope = np.polyfit(np.arange(n), closes, 1)[0]
        mid = float(np.mean(closes))
        rel = slope / mid if mid > 0 else 0.0
        th = self.p.rel_slope_thresh * 0.5
        return 1 if rel > th else (-1 if rel < -th else 0)

    # ---------------- 聚类支撑/压力 ----------------
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
        rev = {"long": False, "short": False}
        if support is None and resistance is None:
            return rev
        o, h, l, c = (self.data.open[0], self.data.high[0],
                      self.data.low[0], self.data.close[0])
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
        near_sup = (support is not None
                    and abs(c - support) / support < self.p.rev_tol * 3)
        near_res = (resistance is not None
                    and abs(c - resistance) / resistance < self.p.rev_tol * 3)
        if (hammer or bull_engulf) and near_sup:
            rev["long"] = True
        if (hanging or bear_engulf) and near_res:
            rev["short"] = True
        return rev

    # ---------------- 信号共振评分 (用于"强信号破例") ----------------
    def confluence(self, side, support, resistance, channel):
        """统计相互独立的同向信号数量 (全部只用已收盘 bar 的确定数据)。"""
        c = self.data.close[0]
        rsi = self.rsi[0]
        n = 0
        # 1) 大周期方向
        if (side == "long" and channel == "up") or (side == "short" and channel == "down"):
            n += 1
        # 2) RSI 极端
        if rsi == rsi:
            if side == "long" and rsi < self.p.rsi_oversold:
                n += 1
            if side == "short" and rsi > self.p.rsi_overbought:
                n += 1
        # 3) 触及支撑/压力
        if side == "long" and support is not None and c <= support * (1 + self.p.rev_tol * 3):
            n += 1
        if side == "short" and resistance is not None and c >= resistance * (1 - self.p.rev_tol * 3):
            n += 1
        # 4) 反转 K 线
        rev = self.detect_reversal(support, resistance)
        if rev[side]:
            n += 1
        # 5) 动量 (MACD 或 EMA)
        mc = self.macd_cross[0] if len(self.data) > self.p.macd_slow else 0
        ec = self.ema_cross[0] if len(self.data) > self.p.ema_slow else 0
        if side == "long" and (mc > 0 or ec > 0):
            n += 1
        if side == "short" and (mc < 0 or ec < 0):
            n += 1
        # 6) 唐奇安突破 (用上一根边界, 无未来函数)
        if len(self.data) > self.p.donchian_n + 1:
            if side == "long" and c > self.don_h[-1]:
                n += 1
            if side == "short" and c < self.don_l[-1]:
                n += 1
        return n

    # ---------------- 主信号 (按 signal_mode) ----------------
    def primary_signal(self, support, resistance, channel, rev):
        """返回 'long' / 'short' / None —— 该指标族的主入场信号。"""
        o, h, l, c = (self.data.open[0], self.data.high[0],
                      self.data.low[0], self.data.close[0])
        m = self.p.signal_mode
        rsi = self.rsi[0]

        if m == "sr_reversal":
            # 原箱体逻辑: 通道向上 + 触及支撑未跌破; 通道向下 + 触及压力未突破
            if channel == "up" and support is not None and (l <= support <= c):
                return "long"
            if channel == "down" and resistance is not None and (c <= resistance <= h):
                return "short"
            return None

        if m == "rsi_reversal":
            # RSI 从极端区回归
            if rsi != rsi or len(self.data) < 2:
                return None
            pr = self.rsi[-1]
            if pr < self.p.rsi_oversold and rsi >= self.p.rsi_oversold:
                return "long"
            if pr > self.p.rsi_overbought and rsi <= self.p.rsi_overbought:
                return "short"
            return None

        if m == "atr_breakout":
            # 唐奇安突破 (用上一根边界, 避免未来函数)
            if len(self.data) < self.p.donchian_n + 2:
                return None
            if c > self.don_h[-1]:
                return "long"
            if c < self.don_l[-1]:
                return "short"
            return None

        if m == "boll_reversion":
            if len(self.data) < self.p.boll_period + 2:
                return None
            if c < self.boll.bot[0]:
                return "long"
            if c > self.boll.top[0]:
                return "short"
            return None

        if m == "macd_cross":
            if len(self.data) < self.p.macd_slow + 2:
                return None
            if self.macd_cross[0] > 0:
                return "long"
            if self.macd_cross[0] < 0:
                return "short"
            return None

        if m == "ema_cross":
            if len(self.data) < self.p.ema_slow + 2:
                return None
            if self.ema_cross[0] > 0:
                return "long"
            if self.ema_cross[0] < 0:
                return "short"
            return None

        raise ValueError(f"未知 signal_mode: {m}")

    # ---------------- 止损/止盈 ----------------
    def compute_stop_target(self, side, support, resistance):
        c = self.data.close[0]
        m = self.p.signal_mode
        if m == "sr_reversal" and support is not None and resistance is not None:
            if side == "long":
                stop = support - self.p.sl_buffer
            else:
                stop = resistance + self.p.sl_buffer
        else:
            # 其余模式用 ATR 止损 (波动率自适应, 不同价位含义一致)
            atr = self.atr[0] if self.atr[0] == self.atr[0] else 0.0
            dist = max(atr * self.p.atr_mult, 0.5)   # 兜底避免过窄
            stop = c - dist if side == "long" else c + dist
        risk = abs(c - stop)
        if risk <= 0:
            return None, None
        target = c + self.p.r_mult * risk if side == "long" else c - self.p.r_mult * risk
        return stop, target

    # ---------------- 订单回调 ----------------
    def notify_order(self, order):
        if order.status in (order.Completed, order.Canceled,
                            order.Margin, order.Rejected):
            self.order = None
        if order.status == order.Completed:
            prev = self._prev_pos
            cur = self.position.size
            if order.isbuy():
                side, kind = ("long", "open") if prev == 0 else ("short", "close")
            else:
                side, kind = ("short", "open") if prev == 0 else ("long", "close")
            self._prev_pos = cur
            self.trades_log.append(dict(
                dt=bt.num2date(order.executed.dt),
                price=order.executed.price,
                side=side, kind=kind,
                reason=getattr(self, "_close_reason", None),
            ))
            if kind == "close":
                self._close_reason = None

    # ---------------- 保证金强平 / 隔夜费 ----------------
    def _margin_call(self, maintenance=0.5):
        if not self.position:
            return False
        ci = self.broker.getcommissioninfo(self.data)
        mult = getattr(ci.p, "mult", 1.0) or 1.0
        lev = getattr(ci.p, "leverage", 1.0) or 1.0
        used = abs(self.position.size) * self.data.close[0] * mult / lev
        if self.broker.get_value() < used * maintenance:
            self._close_reason = "margin"
            self.order = self.close()
            return True
        return False

    def _charge_swap(self):
        utc_dt = bt.num2date(self.data.datetime[0])
        off = tmgm_server_offset_hours(utc_dt)
        sd = (utc_dt + dt.timedelta(hours=off)).date()
        if self._last_sd is None:
            self._last_sd = sd
            return
        if sd == self._last_sd:
            return
        mult3 = 3 if sd.weekday() == 2 else 1
        pos = self.position
        if pos:
            oz = abs(pos.size) * self.p.contract_oz
            pts = (self.p.swap_long_points if pos.size > 0
                   else self.p.swap_short_points)
            usd = oz * pts * self.p.swap_point_value * mult3
            self.broker.add_cash(usd)
            self.swap_total += usd
        self._last_sd = sd

    # ---------------- 隔夜控制 ----------------
    def _is_last_bar_of_server_day(self):
        """当前 bar 是否为服务器日的最后一根 (下一根将跨服务器零点)。
        只用 bar 时间与周期推断, 不涉及未来价格 -> 无未来函数。"""
        utc_dt = bt.num2date(self.data.datetime[0])
        off = tmgm_server_offset_hours(utc_dt)
        mins = FREQ_MIN.get(self.p.freq, 15)
        nxt = utc_dt + dt.timedelta(minutes=mins)
        return (utc_dt + dt.timedelta(hours=off)).date() != \
               (nxt + dt.timedelta(hours=off)).date()

    def _overnight_guard(self, side):
        """返回 True 表示需要因隔夜控制平仓。"""
        if not self.p.force_close_overnight:
            return False
        if not self._is_last_bar_of_server_day():
            return False
        if self.p.allow_overnight_override:
            # 强信号破例: 共振信号数达到阈值才允许持隔夜
            sup, res, _, _ = self.cluster_levels(self.data, self.p.short_win)
            conf = self.confluence(side, sup, res, self.classify_channel())
            if conf >= self.p.overnight_confluence:
                self.n_overnight_held += 1
                return False
        self._close_reason = "eod"      # end-of-day 平仓(规避隔夜费)
        self.n_forced_close += 1
        return True

    def log(self, txt):
        if self.p.print_log:
            print(f"{self.data.datetime.datetime(0)} {txt}")

    # ---------------- 主循环 ----------------
    def next(self):
        self.equity.append((self.data.datetime.datetime(0), self.broker.getvalue()))
        self._charge_swap()
        if self.order:
            return
        if self.position and self._margin_call():
            return

        # ---- 持仓: 止损/止盈 / 隔夜控制 ----
        if self.position:
            c = self.data.close[0]
            info = self.trade_info
            is_long = info.get("dir") == 1
            side = "long" if is_long else "short"

            # 隔夜控制优先于 TP/SL 判定 (日内模式必须先于服务器零点平掉)
            if self._overnight_guard(side):
                self.order = self.close()
                self.log(f"CLOSE {side} @ {c:.2f} reason=eod (规避隔夜费)")
                return

            if is_long:
                if c <= info["stop"]:
                    self._close_reason = "sl"
                elif c >= info["target"]:
                    self._close_reason = "tp"
                else:
                    self._close_reason = None
            else:
                if c >= info["stop"]:
                    self._close_reason = "sl"
                elif c <= info["target"]:
                    self._close_reason = "tp"
                else:
                    self._close_reason = None

            # 布林回归模式: 回到中轨也算平仓信号
            if self._close_reason is None and self.p.signal_mode == "boll_reversion":
                mid = self.boll.mid[0]
                if is_long and c >= mid:
                    self._close_reason = "signal"
                elif (not is_long) and c <= mid:
                    self._close_reason = "signal"

            if self._close_reason:
                self.order = self.close()
                self.log(f"CLOSE {side} @ {c:.2f} reason={self._close_reason}")
            return

        # ---- 空仓: 入场判定 ----
        # 时段过滤 (黄金波动集中 UTC 12:00-21:00)
        if self.p.session_start < self.p.session_end:
            hh = bt.num2date(self.data.datetime[0]).hour
            if not (self.p.session_start <= hh < self.p.session_end):
                return

        channel = self.classify_channel()
        if channel in ("warmup", "range") and self.p.use_trend_filter:
            return

        # 大周期方向不逆 (避免逆势接刀)
        if self.p.use_trend_filter:
            sd = self.classify_short_direction()
            if channel == "up" and sd < 0:
                return
            if channel == "down" and sd > 0:
                return

        sup, res, th, tl = self.cluster_levels(self.data, self.p.short_win)
        if sup is None or res is None:
            return

        side = self.primary_signal(sup, res, channel, None)
        if side is None:
            return
        # 方向开关 (检验隔夜费导致的方向不对称)
        if side == "long" and not self.p.allow_long:
            return
        if side == "short" and not self.p.allow_short:
            return

        # 反转 K 线过滤 (仅箱体/RSI 反转类模式需要)
        if self.p.use_reversal_filter and self.p.signal_mode in (
                "sr_reversal", "rsi_reversal", "boll_reversion"):
            rev = self.detect_reversal(sup, res)
            if not rev[side]:
                return

        # RSI 极端过滤
        rsi = self.rsi[0]
        if rsi == rsi:
            if side == "long" and rsi > self.p.rsi_overbought:
                return
            if side == "short" and rsi < self.p.rsi_oversold:
                return

        # 同方向冷却
        bar = len(self.data)
        if (self._last_entry_side == side
                and (bar - self._last_entry_bar) < self.p.cooldown_bars):
            return

        stop, target = self.compute_stop_target(side, sup, res)
        if stop is None:
            return

        c = self.data.close[0]
        if side == "long":
            self.order = self.buy()
            self.trade_info = dict(entry=c, stop=stop, target=target, dir=1)
        else:
            self.order = self.sell()
            self.trade_info = dict(entry=c, stop=stop, target=target, dir=-1)
        self._last_entry_bar = bar
        self._last_entry_side = side
        self.n_entries += 1
        self.log(f"{'BUY ' if side=='long' else 'SELL'} @ {c:.2f} rsi={rsi:.1f} "
                 f"stop {stop:.2f} target {target:.2f}")


# --------------------------------------------------------------------------
# 三档策略预设 (在 Stage 6 由回测结果最终定稿)
# --------------------------------------------------------------------------
PRESETS = {
    "ultra_short": dict(          # 极致短线: 15m, 日内, 高频低持仓
        signal_mode="sr_reversal", freq="15m", r_mult=1.5, sl_buffer=1.5,
        cooldown_bars=4, force_close_overnight=True,
        allow_overnight_override=False, session_start=0, session_end=24,
    ),
    "medium": dict(               # 中线: 1h, 趋势突破, 允许强信号隔夜
        signal_mode="atr_breakout", freq="1h", r_mult=2.0, atr_mult=2.0,
        donchian_n=20, cooldown_bars=2, force_close_overnight=True,
        allow_overnight_override=True, overnight_confluence=3,
    ),
    "long_term": dict(            # 长线: 日线, 趋势为主, 宽止损低频
        signal_mode="atr_breakout", freq="1d", r_mult=2.5, atr_mult=3.0,
        donchian_n=20, cooldown_bars=1, force_close_overnight=False,
        use_trend_filter=False,
    ),
}


# --------------------------------------------------------------------------
# 数据加载
# --------------------------------------------------------------------------
def load_data(freq, start=None, end=None):
    fname = FREQ_FILE.get(freq)
    if fname is None:
        raise ValueError(f"不支持的周期: {freq} (可选 {list(FREQ_FILE)})")
    path = os.path.join(CLEAN_DIR, fname)
    if not os.path.exists(path):
        raise FileNotFoundError(path)
    df = pd.read_csv(path, parse_dates=["datetime"]).set_index("datetime")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if start is not None:
        df = df[df.index >= pd.Timestamp(start)]
    if end is not None:
        df = df[df.index <= pd.Timestamp(end)]
    return df, bt.feeds.PandasData(dataname=df)


# --------------------------------------------------------------------------
# 运行回测
# --------------------------------------------------------------------------
def run_backtest(freq="15m", extra_args=None, start=None, end=None,
                 cash=1000.0, leverage=1000.0, lot=0.01,
                 spread=0.33, commission=0.0, verbose=False):
    """返回 (eq_df, trades_log, metrics)。start/end 用于 IS/OOS 切分。"""
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.addsizer(FixedLotSizer, lot=lot)
    args = dict(extra_args or {})
    args["freq"] = freq             # 注入主周期, 供隔夜判定使用
    cerebro.addstrategy(OptStrategy, **args)

    df0, d0 = load_data(freq, start, end)
    cerebro.adddata(d0, name=freq)
    hf = HIGHER_TF.get(freq)
    if hf:                                   # 双周期: 主 + 上一档
        _, d1 = load_data(hf, start, end)
        cerebro.adddata(d1, name=hf)

    comminfo = bt.CommissionInfo(commission=commission, mult=100.0,
                                 leverage=leverage, stocklike=False)
    cerebro.broker.addcommissioninfo(comminfo)
    cerebro.broker.setcash(cash)
    cerebro.broker.set_slippage_fixed(fixed=spread / 2.0)

    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="ta")

    results = cerebro.run()
    strat = results[0]

    dd = strat.analyzers.dd.get_analysis()
    ta = strat.analyzers.ta.get_analysis()
    final = cerebro.broker.getvalue()
    total_ret = final / cash - 1.0
    days = max((df0.index[-1] - df0.index[0]).days, 1)
    years = days / 365.0
    ann = (1.0 + total_ret) ** (1.0 / years) - 1.0 if total_ret > -1 else -1.0

    closed = getattr(ta.total, "closed", 0) or 0
    won = getattr(ta.won, "total", 0) or 0
    lost = getattr(ta.lost, "total", 0) or 0
    win_rate = won / closed if closed else 0.0

    # 统一 Sharpe 口径: 由资金曲线自算, 按周期年化 (避免 box/gold 口径不一致)
    eq = pd.Series([v for _, v in strat.equity], dtype=float)
    if len(eq) > 2 and eq.std() > 0:
        r = eq.pct_change().dropna()
        bars_per_year = (365 * 24 * 60) / FREQ_MIN.get(freq, 15)
        sharpe = float(r.mean() / r.std() * np.sqrt(bars_per_year)) if r.std() > 0 else 0.0
    else:
        sharpe = 0.0

    eq_df = pd.DataFrame(strat.equity, columns=["datetime", "value"])
    metrics = dict(freq=freq, final=final, total_ret=total_ret, ann=ann,
                   maxdd=float(getattr(dd.max, "drawdown", 0.0) or 0.0),
                   sharpe=sharpe, closed=closed, won=won, lost=lost,
                   win_rate=win_rate, entries=strat.n_entries,
                   swap_total=strat.swap_total,
                   forced_close=strat.n_forced_close,
                   overnight_held=strat.n_overnight_held,
                   start=str(df0.index[0]), end=str(df0.index[-1]))

    if verbose:
        print("=" * 58)
        print(f"[{freq}] {metrics['start'][:10]} ~ {metrics['end'][:10]}  "
              f"lev=1:{leverage} lot={lot}")
        print(f"  累计 {total_ret*100:+.2f}%  年化 {ann*100:+.2f}%  "
              f"回撤 {metrics['maxdd']:.2f}%  Sharpe {sharpe:.3f}")
        print(f"  交易 {closed} (胜{won}/负{lost}) 胜率 {win_rate*100:.1f}%  "
              f"隔夜费 {strat.swap_total:+.2f}  日终平仓 {strat.n_forced_close}")
        print("=" * 58)
    return eq_df, strat.trades_log, metrics


# --------------------------------------------------------------------------
# 命令行入口
# --------------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--freq", default="15m", choices=list(FREQ_FILE))
    ap.add_argument("--preset", default=None, choices=list(PRESETS))
    ap.add_argument("--mode", default=None, help="覆盖 preset 的 signal_mode")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    ap.add_argument("--cash", type=float, default=1000.0)
    ap.add_argument("--leverage", type=float, default=1000.0)
    ap.add_argument("--lot", type=float, default=0.01)
    ap.add_argument("--spread", type=float, default=0.33)
    ap.add_argument("--r-mult", type=float, default=None)
    ap.add_argument("--log", action="store_true")
    args = ap.parse_args()

    extra = dict(print_log=args.log)
    freq = args.freq
    if args.preset:
        p = dict(PRESETS[args.preset])
        freq = p.pop("freq", freq)
        extra.update(p)
    if args.mode:
        extra["signal_mode"] = args.mode
    if args.r_mult is not None:
        extra["r_mult"] = args.r_mult

    run_backtest(freq=freq, extra_args=extra, start=args.start, end=args.end,
                 cash=args.cash, leverage=args.leverage, lot=args.lot,
                 spread=args.spread, verbose=True)
