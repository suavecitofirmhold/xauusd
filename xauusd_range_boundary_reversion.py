# -*- coding: utf-8 -*-
"""
XAUUSD 15m range-boundary reversion research strategy.

This file is intentionally independent from the production profiles in
box_channel_optimized.py. It reuses the mature broker/order/risk plumbing but
defines its own flat-range regime instead of trusting the existing "range"
channel label.

Research flow:
  1. Run fixed baseline on IS / OOS.
  2. Run a small IS-only sensitivity grid.
  3. Validate the selected configuration on OOS.

Default usage:
  python xauusd_range_boundary_reversion.py --mode baseline
  python xauusd_range_boundary_reversion.py --mode grid
"""
import argparse
import json
import math
from collections import Counter
from multiprocessing import Pool

import backtrader as bt
import numpy as np
import pandas as pd

from box_channel_optimized import (
    BoxChannelOptStrategy,
    FixedLotSizer,
    load_data,
)


class RangeBoundaryReversionStrategy(BoxChannelOptStrategy):
    """Trade confirmed rejections at the edges of a verified flat range."""

    params = dict(
        # Regime definition. These are deliberately separate from the parent
        # classify_channel() label.
        range_trend_window=24,
        range_rel_slope_max=0.0004,
        range_lookback=24,
        range_er_max=0.35,
        range_width_min_atr=2.0,
        range_width_max_atr=6.0,
        range_touch_metric_atr=0.10,
        range_min_boundary_touches=1,
        # Boundary/rejection definition.
        range_touch_atr=0.15,
        range_min_wick_ratio=0.40,
        range_require_reversal=True,
        range_require_rsi_extreme=False,
        range_rsi_low=35.0,
        range_rsi_high=65.0,
        range_allow_long=True,
        range_allow_short=True,
        # Entry mode: touch | false_break
        range_entry_mode="touch",
        range_false_break_atr=0.10,
        range_reclaim_atr=0.05,
        range_reclaim_max_bars=1,
        range_short_rel_slope_max=0.0004,
        range_short_rsi_high=65.0,
        range_short_target_mode="mid",
        range_short_min_reward_risk=0.50,
        range_short_context="slope",
        range_long_false_break_atr=None,
        range_long_reclaim_atr=None,
        range_short_false_break_atr=None,
        range_short_reclaim_atr=None,
        range_conflict_policy="skip",
        range_hybrid_with_box=False,
        range_debug_signals=False,
        atr_method="wilder",  # wilder | sma (SMA matches MT5 iATR in this test)
        range_ctx_completed=True,
        range_ctx_mt5=False,
        ctx_frame=None,
        # Risk and exit definition.
        range_stop_buffer_atr=0.50,
        range_target_mode="mid",  # mid | opposite
        range_min_reward_risk=0.80,
        range_max_hold_bars=12,
    )

    def __init__(self):
        super().__init__()
        self._range_context = None
        self._false_break_context = None
        self._break_watch = None
        self._pending_signal_kind = None
        self.range_conflict_bars = 0
        self.range_signal_debug = []
        if self.p.atr_method == "sma":
            tr = bt.indicators.TrueRange(self.data)
            self.range_atr = bt.indicators.SMA(tr, period=self.p.atr_period)
        else:
            self.range_atr = self.atr
        self.ctx_ema48 = bt.indicators.EMA(self.ctx_data.close, period=48)
        self.ctx_ema96 = bt.indicators.EMA(self.ctx_data.close, period=96)

    # ------------------------------------------------------------------
    # Range detection
    # ------------------------------------------------------------------
    def _range_stats(self):
        n = max(int(self.p.range_lookback), 3)
        if len(self.data) < n + 1:
            return None
        highs = np.asarray(self.data.high.get(size=n + 1), dtype=float)
        lows = np.asarray(self.data.low.get(size=n + 1), dtype=float)
        if len(highs) < n + 1 or len(lows) < n + 1:
            return None
        highs = highs[:-1]
        lows = lows[:-1]
        lower = float(np.min(lows))
        upper = float(np.max(highs))
        if not math.isfinite(lower) or not math.isfinite(upper) or upper <= lower:
            return None
        atr = float(self.range_atr[-self.p.atr_shift])
        if not math.isfinite(atr) or atr <= 0.0:
            return None
        touch = float(self.p.range_touch_metric_atr) * atr
        lower_touches = int(np.sum(lows <= lower + touch))
        upper_touches = int(np.sum(highs >= upper - touch))
        return {
            "lower": lower,
            "upper": upper,
            "lower_touches": lower_touches,
            "upper_touches": upper_touches,
            "atr": atr,
            "width_atr": (upper - lower) / atr,
        }

    def _range_rel_slope(self):
        n = max(int(self.p.range_trend_window), 3)
        if len(self.ctx_data) < n + 1:
            return None
        if self.p.range_ctx_completed:
            closes = np.asarray(self.ctx_data.close.get(size=n + 1), dtype=float)[:-1]
        else:
            closes = np.asarray(self.ctx_data.close.get(size=n), dtype=float)
        if self.p.range_ctx_mt5:
            mapped_closes = self._mt5_h1_closes(n)
            if mapped_closes is None:
                return None
            closes = mapped_closes
        if len(closes) < n or np.any(~np.isfinite(closes)):
            return None
        slope = float(np.polyfit(np.arange(n, dtype=float), closes, 1)[0])
        mid = float(np.mean(closes))
        if mid <= 0.0:
            return None
        return slope / mid

    def _efficiency_ratio(self):
        n = max(int(self.p.range_lookback), 3)
        if len(self.data) < n + 1:
            return None
        closes = np.asarray(self.data.close.get(size=n + 1), dtype=float)
        if len(closes) < n + 1 or np.any(~np.isfinite(closes)):
            return None
        net = abs(float(closes[-1] - closes[0]))
        path = float(np.sum(np.abs(np.diff(closes))))
        if path <= 0.0:
            return None
        return net / path

    def _is_flat_range(self):
        rel = self._range_rel_slope()
        if rel is None or abs(rel) >= float(self.p.range_rel_slope_max):
            return False
        er = self._efficiency_ratio()
        if er is None or er >= float(self.p.range_er_max):
            return False
        stats = self._range_stats()
        if stats is None:
            return False
        if stats["width_atr"] < float(self.p.range_width_min_atr):
            return False
        if stats["width_atr"] > float(self.p.range_width_max_atr):
            return False
        min_touches = max(int(self.p.range_min_boundary_touches), 1)
        if stats["lower_touches"] < min_touches:
            return False
        if stats["upper_touches"] < min_touches:
            return False
        return True

    # ------------------------------------------------------------------
    # Entry signal
    # ------------------------------------------------------------------
    def _rejection(self, side, o, h, l, c):
        rng = h - l
        if rng <= 0.0:
            return False
        lower_wick = (min(o, c) - l) / rng
        upper_wick = (h - max(o, c)) / rng
        if side == "long":
            return (lower_wick >= float(self.p.range_min_wick_ratio)
                    or (c > o and c > self.data.close[-1]))
        return (upper_wick >= float(self.p.range_min_wick_ratio)
                or (c < o and c < self.data.close[-1]))

    def _range_risk(self, side, entry, atr):
        stats = self._range_stats()
        if stats is None:
            return None
        lower, upper = stats["lower"], stats["upper"]
        mid = (lower + upper) / 2.0
        if side == "long":
            stop = lower - float(self.p.range_stop_buffer_atr) * atr
            target = mid if self.p.range_target_mode == "mid" else upper
        else:
            stop = upper + float(self.p.range_stop_buffer_atr) * atr
            target = mid if self.p.range_target_mode == "mid" else lower
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0.0 or reward <= 0.0:
            return None
        if reward / risk < float(self.p.range_min_reward_risk):
            return None
        return lower, upper, stop, target

    def _false_break_risk(self, side, entry, atr, lower, upper, extreme):
        if side == "long":
            stop = extreme - float(self.p.range_stop_buffer_atr) * atr
            target = (lower + upper) / 2.0 if self.p.range_target_mode == "mid" else upper
            min_rr = float(self.p.range_min_reward_risk)
        else:
            stop = extreme + float(self.p.range_stop_buffer_atr) * atr
            mid = (lower + upper) / 2.0
            mode = self.p.range_short_target_mode
            if mode == "opposite":
                target = lower
            elif mode == "half_mid":
                target = entry - 0.5 * (entry - mid)
            elif mode == "one_r":
                target = entry - abs(entry - stop)
            else:
                target = mid
            min_rr = float(self.p.range_short_min_reward_risk)
        risk = abs(entry - stop)
        reward = abs(target - entry)
        if risk <= 0.0 or reward <= 0.0:
            return None
        if reward / risk < min_rr:
            return None
        return stop, target

    def _short_context_ok(self):
        rel = self._range_rel_slope()
        mode = self.p.range_short_context
        if mode == "slope":
            return (rel is not None
                    and rel <= float(self.p.range_short_rel_slope_max))
        if len(self.ctx_data) < 97:
            return False
        c = self.ctx_data.close[0]
        ema48 = self.ctx_ema48[0]
        ema96 = self.ctx_ema96[0]
        if mode == "below_ema48":
            return c < ema48
        if mode == "below_ema96":
            return c < ema96
        if mode == "bear_ema":
            return ema48 < ema96 and c < ema96
        if mode == "below_prev_day":
            if self.p.range_ctx_mt5:
                mapped = self._mt5_h1_context()
                if mapped is None:
                    return False
                current_close, prev_close, _, _ = mapped
                return current_close < prev_close
            if self.p.range_ctx_completed:
                return self.ctx_data.close[-1] < self.ctx_data.close[-25]
            return c < self.ctx_data.close[-24]
        return False

    def _mt5_h1_pos(self):
        if self.p.ctx_frame is None:
            return None
        frame = self.p.ctx_frame
        if frame.empty:
            return None
        if not hasattr(self, "_ctx_index_values"):
            self._ctx_index_values = frame.index.values
            self._ctx_close_values = frame["close"].to_numpy(dtype=float)
        signal_time = self.data.datetime.datetime(0)
        current_hour = (
            pd.Timestamp(signal_time) + pd.Timedelta(minutes=15)
        ).floor("1h")
        pos = int(np.searchsorted(
            self._ctx_index_values, np.datetime64(current_hour), side="left"
        )) - 1
        return pos if pos >= 0 else None

    def _mt5_h1_closes(self, n):
        pos = self._mt5_h1_pos()
        if pos is None or pos < n - 1:
            return None
        return self._ctx_close_values[pos - n + 1:pos + 1]

    def _mt5_h1_context(self):
        """Return current close, previous-day close, and their H1 timestamps."""
        pos = self._mt5_h1_pos()
        if pos is None or pos < 24:
            return None
        return (
            self._ctx_close_values[pos],
            self._ctx_close_values[pos - 24],
            pd.Timestamp(self._ctx_index_values[pos]),
            pd.Timestamp(self._ctx_index_values[pos - 24]),
        )

    def _capture_range_debug(self):
        if not self.p.range_debug_signals:
            return
        stats = self._range_stats()
        if stats is None:
            return
        rel = self._range_rel_slope()
        er = self._efficiency_ratio()
        atr = stats["atr"]
        lower, upper = stats["lower"], stats["upper"]
        o, h, l, c = (
            self.data.open[0],
            self.data.high[0],
            self.data.low[0],
            self.data.close[0],
        )
        rsi = float(self.rsi[0])
        if self.p.range_ctx_mt5:
            mapped = self._mt5_h1_context()
            if mapped is None:
                h1_now = np.nan
                h1_prev = np.nan
                h1_now_time = None
                h1_prev_time = None
                below_prev_day = False
            else:
                h1_now, h1_prev, h1_now_time, h1_prev_time = mapped
                below_prev_day = h1_now < h1_prev
        elif self.p.range_ctx_completed:
            h1_now = self.ctx_data.close[-1] if len(self.ctx_data) >= 1 else np.nan
            h1_prev = self.ctx_data.close[-25] if len(self.ctx_data) >= 25 else np.nan
            h1_now_time = (
                self.ctx_data.datetime.datetime(-1) if len(self.ctx_data) >= 1 else None
            )
            h1_prev_time = (
                self.ctx_data.datetime.datetime(-25) if len(self.ctx_data) >= 25 else None
            )
            below_prev_day = (
                len(self.ctx_data) >= 26
                and self.ctx_data.close[-1] < self.ctx_data.close[-25]
            )
        else:
            h1_now = self.ctx_data.close[0] if len(self.ctx_data) >= 1 else np.nan
            h1_prev = self.ctx_data.close[-24] if len(self.ctx_data) >= 24 else np.nan
            h1_now_time = (
                self.ctx_data.datetime.datetime(0) if len(self.ctx_data) >= 1 else None
            )
            h1_prev_time = (
                self.ctx_data.datetime.datetime(-24) if len(self.ctx_data) >= 24 else None
            )
            below_prev_day = (
                len(self.ctx_data) >= 25
                and self.ctx_data.close[0] < self.ctx_data.close[-24]
            )
        flat = (
            rel is not None and abs(rel) < float(self.p.range_rel_slope_max)
            and er is not None and er < float(self.p.range_er_max)
            and float(self.p.range_width_min_atr) <= stats["width_atr"]
            <= float(self.p.range_width_max_atr)
        )
        long_break = self._side_value(
            "long", "false_break_atr", self.p.range_false_break_atr
        ) * atr
        long_reclaim = self._side_value(
            "long", "reclaim_atr", self.p.range_reclaim_atr
        ) * atr
        short_break = self._side_value(
            "short", "false_break_atr", self.p.range_false_break_atr
        ) * atr
        short_reclaim = self._side_value(
            "short", "reclaim_atr", self.p.range_reclaim_atr
        ) * atr
        long_setup = (
            flat and l <= lower - long_break
            and c >= lower + long_reclaim and c <= upper
            and rsi <= float(self.p.range_rsi_low)
        )
        short_setup = (
            flat and h >= upper + short_break
            and c <= upper - short_reclaim and c >= lower
            and rsi >= float(self.p.range_short_rsi_high)
            and below_prev_day
        )
        long_risk = (
            self._false_break_risk("long", c, atr, lower, upper, l)
            if long_setup else None
        )
        short_risk = (
            self._false_break_risk("short", c, atr, lower, upper, h)
            if short_setup else None
        )
        self.range_signal_debug.append(dict(
            datetime=self.data.datetime.datetime(0),
            rel=rel if rel is not None else np.nan,
            er=er if er is not None else np.nan,
            width=stats["width_atr"],
            atr=atr,
            lower=lower,
            upper=upper,
            high=h,
            low=l,
            close=c,
            rsi=rsi,
            h1_close=h1_now,
            h1_prev_day=h1_prev,
            h1_close_time=h1_now_time,
            h1_prev_time=h1_prev_time,
            below_prev_day=int(below_prev_day),
            flat=int(flat),
            long_setup=int(long_setup),
            short_setup=int(short_setup),
            long_risk=int(long_risk is not None),
            short_risk=int(short_risk is not None),
        ))

    def _side_value(self, side, name, fallback):
        value = getattr(self.p, f"range_{side}_{name}")
        return float(fallback if value is None else value)

    def _false_breakout_signal(self):
        o, h, l, c = (
            self.data.open[0],
            self.data.high[0],
            self.data.low[0],
            self.data.close[0],
        )
        rsi = float(self.rsi[0])
        long_rsi_ok = (not self.p.range_require_rsi_extreme
                       or (math.isfinite(rsi) and rsi <= float(self.p.range_rsi_low)))
        short_rsi_ok = (not self.p.range_require_rsi_extreme
                        or (math.isfinite(rsi) and rsi >= float(self.p.range_short_rsi_high)))
        short_context_ok = self._short_context_ok()
        # A breakout bar was recorded. Allow the next N bars to reclaim the
        # stored boundary, which is not polluted by the breakout bar itself.
        watch = self._break_watch
        if watch is not None:
            atr = float(self.range_atr[-self.p.atr_shift])
            age = len(self.data) - int(watch["bar"])
            if math.isfinite(atr) and atr > 0.0:
                reclaim = self._side_value(
                    watch["side"], "reclaim_atr", self.p.range_reclaim_atr
                ) * atr
                if (watch["side"] == "long" and long_rsi_ok
                        and c >= watch["lower"] + reclaim):
                    risk = self._false_break_risk(
                        "long", c, atr, watch["lower"], watch["upper"], watch["extreme"]
                    )
                    if risk is not None:
                        self._false_break_context = risk
                        self._break_watch = None
                        return "long"
                if (watch["side"] == "short" and short_rsi_ok and short_context_ok
                        and c <= watch["upper"] - reclaim):
                    risk = self._false_break_risk(
                        "short", c, atr, watch["lower"], watch["upper"], watch["extreme"]
                    )
                    if risk is not None:
                        self._false_break_context = risk
                        self._break_watch = None
                        return "short"
            if age >= max(int(self.p.range_reclaim_max_bars), 0):
                self._break_watch = None
            return None

        # Start a new candidate only inside a verified flat range.
        if not self._is_flat_range():
            return None
        stats = self._range_stats()
        if stats is None:
            return None
        lower, upper, atr = stats["lower"], stats["upper"], stats["atr"]
        long_break = self._side_value(
            "long", "false_break_atr", self.p.range_false_break_atr
        ) * atr
        long_reclaim = self._side_value(
            "long", "reclaim_atr", self.p.range_reclaim_atr
        ) * atr
        short_break = self._side_value(
            "short", "false_break_atr", self.p.range_false_break_atr
        ) * atr
        short_reclaim = self._side_value(
            "short", "reclaim_atr", self.p.range_reclaim_atr
        ) * atr

        # Same-bar false breakout: the candle pierces the boundary and closes
        # back inside. This is the cleanest form of the hypothesis.
        long_setup = (
            self.p.range_allow_long and long_rsi_ok
            and l <= lower - long_break
            and c >= lower + long_reclaim and c <= upper
        )
        short_setup = (
            self.p.range_allow_short and short_rsi_ok and short_context_ok
            and h >= upper + short_break
            and c <= upper - short_reclaim and c >= lower
        )
        long_risk = (
            self._false_break_risk("long", c, atr, lower, upper, l)
            if long_setup else None
        )
        short_risk = (
            self._false_break_risk("short", c, atr, lower, upper, h)
            if short_setup else None
        )
        if long_risk is not None and short_risk is not None:
            self.range_conflict_bars += 1
            policy = self.p.range_conflict_policy
            if policy == "skip":
                return None
            if policy == "long":
                short_risk = None
            elif policy == "short":
                long_risk = None
        if long_risk is not None:
            self._false_break_context = long_risk
            return "long"
        if short_risk is not None:
            self._false_break_context = short_risk
            return "short"

        # Delayed false breakout: first bar closes outside, a later bar must
        # close back inside.
        if int(self.p.range_reclaim_max_bars) > 0:
            if self.p.range_allow_long and l <= lower - long_break and c < lower:
                self._break_watch = dict(
                    side="long", lower=lower, upper=upper, extreme=l,
                    bar=len(self.data),
                )
            elif (self.p.range_allow_short and short_context_ok
                  and h >= upper + short_break and c > upper):
                self._break_watch = dict(
                    side="short", lower=lower, upper=upper, extreme=h,
                    bar=len(self.data),
                )
        return None

    def want_entry(self, channel, support, resistance, style=None):
        s = style if style is not None else self.p.entry_style
        self._pending_signal_kind = None
        if s != "range_revert":
            return super().want_entry(channel, support, resistance, style)
        if self.p.range_entry_mode == "false_break":
            if channel == "range":
                signal = self._false_breakout_signal()
                if signal is not None:
                    self._pending_signal_kind = "range"
                return signal
            if self.p.range_hybrid_with_box:
                self._pending_signal_kind = "box"
                return super().want_entry(channel, support, resistance, style="box")
            return None
        if channel != "range" or not self._is_flat_range():
            return None
        stats = self._range_stats()
        if stats is None:
            return None
        lower, upper, atr = stats["lower"], stats["upper"], stats["atr"]
        tol = float(self.p.range_touch_atr) * atr
        o, h, l, c = self.data.open[0], self.data.high[0], self.data.low[0], self.data.close[0]
        long_setup = l <= lower + tol and c >= lower and c <= upper
        short_setup = h >= upper - tol and c <= upper and c >= lower
        rsi = float(self.rsi[0])
        long_rsi_ok = (not self.p.range_require_rsi_extreme
                       or (math.isfinite(rsi) and rsi <= float(self.p.range_rsi_low)))
        short_rsi_ok = (not self.p.range_require_rsi_extreme
                        or (math.isfinite(rsi) and rsi >= float(self.p.range_short_rsi_high)))
        if (self.p.range_allow_long and long_setup and long_rsi_ok
                and (not self.p.range_require_reversal or self._rejection("long", o, h, l, c))):
            if self._range_risk("long", c, atr) is not None:
                self._range_context = (lower, upper)
                return "long"
        if (self.p.range_allow_short and short_setup and short_rsi_ok
                and (not self.p.range_require_reversal or self._rejection("short", o, h, l, c))):
            if self._range_risk("short", c, atr) is not None:
                self._range_context = (lower, upper)
                return "short"
        return None

    def _trend_filter_pass(self, want):
        if self._pending_signal_kind == "range":
            return True
        return super()._trend_filter_pass(want)

    def _open_position(self, want, entry_ref, stop, target, rm, rsi,
                       slot_feed=None, support=None, resistance=None):
        super()._open_position(
            want, entry_ref, stop, target, rm, rsi, slot_feed, support, resistance
        )
        if self.open_positions:
            self.open_positions[-1]["_signal_kind"] = self._pending_signal_kind

    def risk_levels(self, side, entry, support, resistance, r_mult=None):
        if self._false_break_context is not None:
            stop, target = self._false_break_context
            self._false_break_context = None
            return stop, target
        if self._range_context is None:
            return super().risk_levels(side, entry, support, resistance, r_mult)
        atr = float(self.range_atr[-self.p.atr_shift])
        levels = self._range_risk(side, entry, atr)
        if levels is None:
            return super().risk_levels(side, entry, support, resistance, r_mult)
        _, _, stop, target = levels
        return stop, target

    # ------------------------------------------------------------------
    # Time stop
    # ------------------------------------------------------------------
    def next(self):
        self._capture_range_debug()
        super().next()
        hold = max(int(self.p.range_max_hold_bars), 0)
        if hold <= 0 or not self.open_positions:
            return
        for pos in list(self.open_positions):
            if pos.get("_closed"):
                continue
            if pos.get("_signal_kind") != "range":
                continue
            entry_order = pos.get("entry_order")
            if entry_order is None or entry_order.status != bt.Order.Completed:
                continue
            if len(self.data) - int(pos.get("_entry_bar", len(self.data))) >= hold:
                self._manual_close(pos, "time")


# ----------------------------------------------------------------------
# Independent runner
# ----------------------------------------------------------------------
def run_range_backtest(extra_args=None, cash=1000.0, leverage=1000.0, lot=0.01,
                       spread=0.33, commission=0.0,
                       fromdate=None, todate=None, quiet=True,
                       primary_df=None, context_df=None, spread_series=None):
    cerebro = bt.Cerebro()
    cerebro.addsizer(FixedLotSizer, lot=lot)

    if primary_df is None:
        df_primary, data_primary, spread_series = load_data(
            "15m", fromdate=fromdate, todate=todate, spread_source="real"
        )
    else:
        df_primary = primary_df.copy()
        if fromdate:
            df_primary = df_primary[df_primary.index >= pd.Timestamp(fromdate)]
        if todate:
            df_primary = df_primary[df_primary.index <= pd.Timestamp(todate)]
        data_primary = bt.feeds.PandasData(dataname=df_primary)
    cerebro.adddata(data_primary, name="15m")
    if context_df is None:
        df_context, data_context, _ = load_data(
            "1h", fromdate=fromdate, todate=todate
        )
    else:
        df_context = context_df.copy()
        if fromdate:
            df_context = df_context[df_context.index >= pd.Timestamp(fromdate)]
        if todate:
            df_context = df_context[df_context.index <= pd.Timestamp(todate)]
        data_context = bt.feeds.PandasData(dataname=df_context)
    cerebro.adddata(data_context, name="1h")

    strategy_args = dict(
        entry_style="range_revert",
        atr_shift=1,
        max_positions=1,
        lot=lot,
        slot_feeds=[data_primary],
        ctx_data=data_context,
        spread_series=spread_series,
    )
    strategy_args.update(extra_args or {})
    if (extra_args or {}).get("range_ctx_mt5"):
        strategy_args.setdefault("ctx_frame", df_context)
    cerebro.addstrategy(RangeBoundaryReversionStrategy, **strategy_args)

    cerebro.broker.addcommissioninfo(
        bt.CommissionInfo(commission=commission, mult=100.0,
                          leverage=leverage, stocklike=False)
    )
    cerebro.broker.setcash(cash)
    cerebro.broker.set_slippage_fixed(fixed=spread / 2.0)
    cerebro.addanalyzer(bt.analyzers.Returns, _name="returns")
    cerebro.addanalyzer(bt.analyzers.DrawDown, _name="dd")
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="ta")
    cerebro.addanalyzer(bt.analyzers.SharpeRatio, _name="sharpe")

    results = cerebro.run(optreturn=False)
    strat = results[0]
    dd = strat.analyzers.dd.get_analysis()
    ta = strat.analyzers.ta.get_analysis()
    sharpe = strat.analyzers.sharpe.get_analysis()
    sh_v = sharpe.get("sharperatio", float("nan"))
    if sh_v is None:
        sh_v = float("nan")

    final = cerebro.broker.getvalue()
    total_ret = final / cash - 1.0
    days = max((df_primary.index[-1] - df_primary.index[0]).days, 1)
    years = days / 365.0
    ann = (1.0 + total_ret) ** (1.0 / years) - 1.0 if total_ret > -1 else -1.0

    trade_pnls = list(getattr(strat, "trade_pnls", []))
    if trade_pnls:
        closed = len(trade_pnls)
        won = sum(1 for entry in trade_pnls if entry["pnl"] >= 0.0)
        lost = closed - won
        win_rate = won / closed
        gross_profit = sum(entry["pnl"] for entry in trade_pnls if entry["pnl"] > 0.0)
        gross_loss = -sum(entry["pnl"] for entry in trade_pnls if entry["pnl"] < 0.0)
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    else:
        closed = ta.get("total", {}).get("closed", 0)
        won = ta.get("won", {}).get("total", 0)
        lost = ta.get("lost", {}).get("total", 0)
        win_rate = won / closed if closed > 0 else 0.0
        pnl_won = ta.get("won", {}).get("pnl", {}).get("total", 0.0) or 0.0
        pnl_lost = ta.get("lost", {}).get("pnl", {}).get("total", 0.0) or 0.0
        gross_profit = pnl_won
        gross_loss = -pnl_lost if pnl_lost < 0 else pnl_lost
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    realized_ret = (gross_profit - gross_loss) / cash
    episodes = list(getattr(strat, "trade_episodes", []))
    data_bars = max(len(df_primary), 1)
    exposure = sum(episode["bars"] for episode in episodes) / data_bars * 100.0
    overnight = sum(
        1 for t in strat.trades_log
        if t["kind"] == "close" and t.get("reason") == "eod"
    )
    res = dict(
        final=final,
        total_ret=total_ret,
        ann=ann,
        maxdd=dd.max.drawdown,
        sharpe=sh_v,
        closed=closed,
        won=won,
        lost=lost,
        win_rate=win_rate,
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        pf=pf,
        realized_ret=realized_ret,
        entries=strat.n_entries,
        swap_total=strat.swap_total,
        overnight_eod=overnight,
        session_skip=strat._n_session_skip,
        spread_skip=strat._n_spread_skip,
        trades=strat.trades_log,
        trade_pnls=trade_pnls,
        trade_episodes=episodes,
        range_signal_debug=list(getattr(strat, "range_signal_debug", [])),
        exposure=exposure,
        conflict_bars=int(getattr(strat, "range_conflict_bars", 0)),
        eq=strat.equity,
    )
    if not quiet:
        print(
            f"[RANGE] ret={total_ret*100:+.2f}% ann={ann*100:+.2f}% "
            f"DD={dd.max.drawdown:.2f}% Sharpe={sh_v:.2f} PF={pf:.2f} "
            f"trades={closed} win={win_rate*100:.1f}% "
            f"eod={overnight} swap={strat.swap_total:+.2f} "
            f"session_skip={strat._n_session_skip} spread_skip={strat._n_spread_skip}"
        )
    return res


# ----------------------------------------------------------------------
# Research utilities
# ----------------------------------------------------------------------
DEFAULTS = dict(
    range_trend_window=24,
    range_rel_slope_max=0.0004,
    range_lookback=24,
    range_er_max=0.35,
    range_width_min_atr=2.0,
    range_width_max_atr=6.0,
    range_touch_metric_atr=0.10,
    range_min_boundary_touches=1,
    range_touch_atr=0.15,
    range_min_wick_ratio=0.40,
    range_require_reversal=True,
    range_require_rsi_extreme=False,
    range_rsi_low=35.0,
    range_rsi_high=65.0,
    range_allow_long=True,
    range_allow_short=True,
    range_entry_mode="touch",
    range_false_break_atr=0.10,
    range_reclaim_atr=0.05,
    range_reclaim_max_bars=1,
    range_short_rel_slope_max=0.0004,
    range_short_rsi_high=65.0,
    range_short_target_mode="mid",
    range_short_min_reward_risk=0.50,
    range_short_context="slope",
    range_long_false_break_atr=None,
    range_long_reclaim_atr=None,
    range_short_false_break_atr=None,
    range_short_reclaim_atr=None,
    range_conflict_policy="skip",
    range_hybrid_with_box=False,
    range_debug_signals=False,
    atr_method="wilder",
    range_ctx_completed=True,
    range_ctx_mt5=False,
    ctx_frame=None,
    range_stop_buffer_atr=0.50,
    range_target_mode="mid",
    range_min_reward_risk=0.80,
    range_max_hold_bars=12,
    cooldown_bars=4,
    avoid_overnight=True,
    session_filter=True,
    spread_filter=True,
    max_spread_points=50.0,
    swap_long_points=-72.5,
    swap_short_points=30.72,
    swap_point_value=0.01,
    contract_oz=100.0,
)

IS_FROM = "2023-09-13"
IS_TO = "2025-06-30"
OOS_FROM = "2025-07-01"
OOS_TO = "2026-09-08"


def _brief(result):
    return {
        "ret": round(result["total_ret"] * 100.0, 2),
        "ann": round(result["ann"] * 100.0, 2),
        "dd": round(result["maxdd"], 2),
        "sharpe": round(result["sharpe"], 3) if math.isfinite(result["sharpe"]) else None,
        "pf": round(result["pf"], 3) if math.isfinite(result["pf"]) else None,
        "trades": result["closed"],
        "win": round(result["win_rate"] * 100.0, 2),
        "swap": round(result["swap_total"], 2),
        "eod": result["overnight_eod"],
        "exposure": round(result.get("exposure", 0.0), 2),
        "conflicts": result.get("conflict_bars", 0),
    }


def _pair_trades(trades):
    stack = []
    pairs = []
    for trade in trades:
        if trade["kind"] == "open":
            stack.append(trade)
        elif trade["kind"] == "close" and stack:
            pairs.append((stack.pop(), trade))
    return pairs


def _diagnose(result):
    pairs = _pair_trades(result["trades"])
    reasons = Counter(close.get("reason") for _, close in pairs)
    sides = Counter(open_trade["side"] for open_trade, _ in pairs)
    hours = Counter(open_trade["dt"].hour for open_trade, _ in pairs)
    rs = []
    wins = []
    losses = []
    side_stats = {
        "long": {"wins": 0, "losses": 0, "gp": 0.0, "gl": 0.0},
        "short": {"wins": 0, "losses": 0, "gp": 0.0, "gl": 0.0},
    }
    direct_pnls = result.get("trade_pnls", [])
    aligned_pnls = direct_pnls if len(direct_pnls) == len(pairs) else []
    for entry in direct_pnls:
        stats = side_stats[entry["side"]]
        if entry["pnl"] >= 0.0:
            stats["wins"] += 1
            stats["gp"] += entry["pnl"]
        else:
            stats["losses"] += 1
            stats["gl"] += -entry["pnl"]
    for idx, (open_trade, close_trade) in enumerate(pairs):
        pnl = (aligned_pnls[idx]["pnl"] if aligned_pnls else
               (1.0 if open_trade["side"] == "long" else -1.0)
               * (close_trade["price"] - open_trade["price"]))
        risk = abs(open_trade["price"] - open_trade["stop"])
        if risk > 0.0:
            rs.append(pnl / risk)
        if pnl >= 0.0:
            wins.append(pnl)
        else:
            losses.append(-pnl)
    print("=" * 72)
    print("Range strategy diagnostics")
    print("=" * 72)
    print("reasons:", dict(reasons))
    print("sides  :", dict(sides))
    print(f"paired={len(pairs)} direct_pnls={len(direct_pnls)}")
    print("hours  :", dict(sorted(hours.items())))
    for side, stats in side_stats.items():
        pf = stats["gp"] / stats["gl"] if stats["gl"] > 0.0 else float("inf")
        print(
            f"{side:<5}: wins={stats['wins']} losses={stats['losses']} "
            f"PF={pf:.2f}"
        )
    if rs:
        arr = np.asarray(rs, dtype=float)
        print(
            f"R mean={arr.mean():+.3f} median={np.median(arr):+.3f} "
            f"p10={np.percentile(arr, 10):+.3f} p90={np.percentile(arr, 90):+.3f}"
        )
    if wins or losses:
        avg_win = float(np.mean(wins)) if wins else 0.0
        avg_loss = float(np.mean(losses)) if losses else 0.0
        print(f"avg win={avg_win:.3f} USD avg loss={avg_loss:.3f} USD")


def run_single(fromdate=None, todate=None, diagnostic=False, entry_mode="touch"):
    settings = dict(DEFAULTS)
    settings["range_entry_mode"] = entry_mode
    result = run_range_backtest(
        extra_args=settings, fromdate=fromdate, todate=todate, quiet=False
    )
    print(json.dumps(_brief(result), ensure_ascii=False, indent=2))
    if diagnostic:
        _diagnose(result)
    return result


def run_false_break_candidate_diag(fromdate=None, todate=None):
    cfg = dict(DEFAULTS, **FALSE_BREAK_CANDIDATE)
    result = run_range_backtest(
        extra_args=cfg, fromdate=fromdate or IS_FROM, todate=todate or OOS_TO,
        quiet=False,
    )
    print(json.dumps(_brief(result), ensure_ascii=False, indent=2))
    _diagnose(result)
    return result


def run_grid():
    jobs = []
    for target_mode in ("mid", "opposite"):
        for cooldown in (4, 12):
            for require_reversal in (False, True):
                for rel in (0.0002, 0.0004):
                    jobs.append(dict(
                        target_mode=target_mode,
                        cooldown_bars=cooldown,
                        require_reversal=require_reversal,
                        rel_slope_max=rel,
                    ))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_grid_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_boundary_sensitivity.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _grid_one(job):
    cfg = dict(
        DEFAULTS,
        range_target_mode=job["target_mode"],
        cooldown_bars=job["cooldown_bars"],
        range_require_reversal=job["require_reversal"],
        range_rel_slope_max=job["rel_slope_max"],
    )
    result = run_range_backtest(
        extra_args=cfg, fromdate=IS_FROM, todate=IS_TO, quiet=True
    )
    row = {
        "target": job["target_mode"],
        "cooldown": job["cooldown_bars"],
        "reversal": job["require_reversal"],
        "rel_max": job["rel_slope_max"],
    }
    row.update(_brief(result))
    return row


def run_quality_grid():
    jobs = []
    for touches in (1, 2, 3):
        for require_rsi in (False, True):
            jobs.append(dict(touches=touches, require_rsi=require_rsi))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_quality_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_boundary_quality_grid.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _quality_one(job):
    cfg = dict(
        DEFAULTS,
        range_min_boundary_touches=job["touches"],
        range_require_rsi_extreme=job["require_rsi"],
    )
    result = run_range_backtest(
        extra_args=cfg, fromdate=IS_FROM, todate=IS_TO, quiet=True
    )
    row = {
        "min_boundary_touches": job["touches"],
        "require_rsi": job["require_rsi"],
    }
    row.update(_brief(result))
    return row


def run_lookback_grid():
    jobs = []
    for lookback in (24, 36, 48):
        for touches in (2, 3):
            jobs.append(dict(lookback=lookback, touches=touches))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_lookback_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_boundary_lookback_grid.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _lookback_one(job):
    cfg = dict(
        DEFAULTS,
        range_lookback=job["lookback"],
        range_min_boundary_touches=job["touches"],
    )
    result = run_range_backtest(
        extra_args=cfg, fromdate=IS_FROM, todate=IS_TO, quiet=True
    )
    row = {
        "range_lookback": job["lookback"],
        "min_boundary_touches": job["touches"],
    }
    row.update(_brief(result))
    return row


def run_holdout_grid():
    jobs = []
    for touches in (2, 3):
        for label, start, end in (
            ("IS", IS_FROM, IS_TO),
            ("OOS", OOS_FROM, OOS_TO),
            ("FULL", IS_FROM, OOS_TO),
        ):
            jobs.append(dict(touches=touches, label=label, start=start, end=end))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_holdout_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_boundary_holdout.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _holdout_one(job):
    cfg = dict(DEFAULTS, range_min_boundary_touches=job["touches"])
    result = run_range_backtest(
        extra_args=cfg, fromdate=job["start"], todate=job["end"], quiet=True
    )
    row = {
        "min_boundary_touches": job["touches"],
        "window": job["label"],
    }
    row.update(_brief(result))
    return row


def run_false_break_grid():
    jobs = []
    for break_atr in (0.05, 0.10, 0.20):
        for reclaim_atr in (0.0, 0.05, 0.10):
            for max_bars in (0, 1):
                jobs.append(dict(
                    break_atr=break_atr,
                    reclaim_atr=reclaim_atr,
                    max_bars=max_bars,
                ))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_false_break_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_grid.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _false_break_one(job):
    cfg = dict(
        DEFAULTS,
        range_entry_mode="false_break",
        range_false_break_atr=job["break_atr"],
        range_reclaim_atr=job["reclaim_atr"],
        range_reclaim_max_bars=job["max_bars"],
    )
    result = run_range_backtest(
        extra_args=cfg, fromdate=IS_FROM, todate=IS_TO, quiet=True
    )
    row = {
        "false_break_atr": job["break_atr"],
        "reclaim_atr": job["reclaim_atr"],
        "reclaim_max_bars": job["max_bars"],
    }
    row.update(_brief(result))
    return row


def run_false_break_quality_grid():
    jobs = []
    for touches in (1, 2, 3):
        for target_mode in ("mid", "opposite"):
            for require_rsi in (False, True):
                jobs.append(dict(
                    touches=touches,
                    target_mode=target_mode,
                    require_rsi=require_rsi,
                ))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_false_break_quality_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_quality_grid.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _false_break_quality_one(job):
    cfg = dict(
        DEFAULTS,
        range_entry_mode="false_break",
        range_false_break_atr=0.20,
        range_reclaim_atr=0.10,
        range_reclaim_max_bars=0,
        range_min_boundary_touches=job["touches"],
        range_target_mode=job["target_mode"],
        range_require_rsi_extreme=job["require_rsi"],
    )
    result = run_range_backtest(
        extra_args=cfg, fromdate=IS_FROM, todate=IS_TO, quiet=True
    )
    row = {
        "min_boundary_touches": job["touches"],
        "target_mode": job["target_mode"],
        "require_rsi": job["require_rsi"],
    }
    row.update(_brief(result))
    return row


def run_false_break_holdout():
    jobs = []
    for target_mode in ("mid", "opposite"):
        for label, start, end in (
            ("IS", IS_FROM, IS_TO),
            ("OOS", OOS_FROM, OOS_TO),
            ("FULL", IS_FROM, OOS_TO),
        ):
            jobs.append(dict(
                target_mode=target_mode,
                label=label,
                start=start,
                end=end,
            ))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_false_break_holdout_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_holdout.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _false_break_holdout_one(job):
    cfg = dict(
        DEFAULTS,
        range_entry_mode="false_break",
        range_false_break_atr=0.20,
        range_reclaim_atr=0.10,
        range_reclaim_max_bars=0,
        range_min_boundary_touches=1,
        range_require_rsi_extreme=True,
        range_target_mode=job["target_mode"],
    )
    result = run_range_backtest(
        extra_args=cfg, fromdate=job["start"], todate=job["end"], quiet=True
    )
    row = {
        "target_mode": job["target_mode"],
        "window": job["label"],
    }
    row.update(_brief(result))
    return row


FALSE_BREAK_CANDIDATE = dict(
    range_entry_mode="false_break",
    range_false_break_atr=0.20,
    range_reclaim_atr=0.10,
    range_reclaim_max_bars=0,
    range_min_boundary_touches=1,
    range_require_rsi_extreme=True,
    range_target_mode="mid",
)
FALSE_BREAK_LONG_CANDIDATE = dict(
    FALSE_BREAK_CANDIDATE,
    range_allow_short=False,
)


def run_false_break_walkforward():
    folds = (
        ("F1 23H2", "2023-09-13", "2024-03-31"),
        ("F2 24H1", "2024-04-01", "2024-09-30"),
        ("F3 24H2", "2024-10-01", "2025-03-31"),
        ("F4 25H1", "2025-04-01", "2025-09-30"),
        ("F5 25H2", "2025-10-01", "2026-03-31"),
        ("F6 26H1", "2026-04-01", OOS_TO),
    )
    jobs = [
        dict(label=label, start=start, end=end)
        for label, start, end in folds
    ]
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_false_break_window_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_walkforward.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _false_break_window_one(job):
    cfg = dict(DEFAULTS, **FALSE_BREAK_CANDIDATE)
    result = run_range_backtest(
        extra_args=cfg, fromdate=job["start"], todate=job["end"], quiet=True
    )
    row = {"window": job["label"]}
    row.update(_brief(result))
    return row


def run_false_break_cost():
    jobs = [dict(spread=spread) for spread in (0.33, 0.50, 0.66, 0.99, 1.50)]
    with Pool(processes=min(5, len(jobs))) as pool:
        rows = pool.map(_false_break_cost_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_cost.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _false_break_cost_one(job):
    cfg = dict(DEFAULTS, **FALSE_BREAK_CANDIDATE)
    result = run_range_backtest(
        extra_args=cfg,
        fromdate=IS_FROM,
        todate=OOS_TO,
        spread=job["spread"],
        quiet=True,
    )
    row = {"spread": job["spread"]}
    row.update(_brief(result))
    return row


def run_false_break_long_holdout():
    jobs = [
        dict(label=label, start=start, end=end)
        for label, start, end in (
            ("IS", IS_FROM, IS_TO),
            ("OOS", OOS_FROM, OOS_TO),
            ("FULL", IS_FROM, OOS_TO),
        )
    ]
    with Pool(processes=min(3, len(jobs))) as pool:
        rows = pool.map(_false_break_long_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_long_holdout.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _false_break_long_one(job):
    cfg = dict(DEFAULTS, **FALSE_BREAK_LONG_CANDIDATE)
    result = run_range_backtest(
        extra_args=cfg, fromdate=job["start"], todate=job["end"], quiet=True
    )
    row = {"window": job["label"]}
    row.update(_brief(result))
    return row


def run_false_break_long_walkforward():
    folds = (
        ("F1 23H2", "2023-09-13", "2024-03-31"),
        ("F2 24H1", "2024-04-01", "2024-09-30"),
        ("F3 24H2", "2024-10-01", "2025-03-31"),
        ("F4 25H1", "2025-04-01", "2025-09-30"),
        ("F5 25H2", "2025-10-01", "2026-03-31"),
        ("F6 26H1", "2026-04-01", OOS_TO),
    )
    jobs = [dict(label=label, start=start, end=end) for label, start, end in folds]
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_false_break_long_window_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_long_walkforward.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _false_break_long_window_one(job):
    cfg = dict(DEFAULTS, **FALSE_BREAK_LONG_CANDIDATE)
    result = run_range_backtest(
        extra_args=cfg, fromdate=job["start"], todate=job["end"], quiet=True
    )
    row = {"window": job["label"]}
    row.update(_brief(result))
    return row


def run_false_break_long_cost():
    jobs = [dict(spread=spread) for spread in (0.33, 0.50, 0.66, 0.99, 1.50)]
    with Pool(processes=min(5, len(jobs))) as pool:
        rows = pool.map(_false_break_long_cost_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_long_cost.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _false_break_long_cost_one(job):
    cfg = dict(DEFAULTS, **FALSE_BREAK_LONG_CANDIDATE)
    result = run_range_backtest(
        extra_args=cfg,
        fromdate=IS_FROM,
        todate=OOS_TO,
        spread=job["spread"],
        quiet=True,
    )
    row = {"spread": job["spread"]}
    row.update(_brief(result))
    return row


def run_false_break_short_grid():
    jobs = []
    for rel_max in (0.0004, 0.0002, 0.0, -0.0002):
        for rsi_high in (65.0, 70.0, 75.0, 80.0):
            for target_mode in ("mid", "opposite"):
                jobs.append(dict(
                    rel_max=rel_max,
                    rsi_high=rsi_high,
                    target_mode=target_mode,
                ))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_false_break_short_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_short_grid.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _false_break_short_one(job):
    cfg = dict(
        DEFAULTS,
        range_entry_mode="false_break",
        range_false_break_atr=0.20,
        range_reclaim_atr=0.10,
        range_reclaim_max_bars=0,
        range_min_boundary_touches=1,
        range_require_rsi_extreme=True,
        range_allow_long=False,
        range_allow_short=True,
        range_short_rel_slope_max=job["rel_max"],
        range_short_rsi_high=job["rsi_high"],
        range_target_mode=job["target_mode"],
    )
    result = run_range_backtest(
        extra_args=cfg, fromdate=IS_FROM, todate=IS_TO, quiet=True
    )
    row = {
        "short_rel_slope_max": job["rel_max"],
        "short_rsi_high": job["rsi_high"],
        "target_mode": job["target_mode"],
    }
    row.update(_brief(result))
    return row


def run_false_break_short_exit_grid():
    jobs = []
    for target_mode in ("mid", "half_mid", "one_r", "opposite"):
        for stop_buffer in (0.50, 1.00):
            for break_atr, reclaim_atr in ((0.20, 0.10), (0.30, 0.10), (0.30, 0.15)):
                jobs.append(dict(
                    target_mode=target_mode,
                    stop_buffer=stop_buffer,
                    break_atr=break_atr,
                    reclaim_atr=reclaim_atr,
                ))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_false_break_short_exit_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_short_exit_grid.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _false_break_short_exit_one(job):
    cfg = dict(
        DEFAULTS,
        range_entry_mode="false_break",
        range_false_break_atr=job["break_atr"],
        range_reclaim_atr=job["reclaim_atr"],
        range_reclaim_max_bars=0,
        range_min_boundary_touches=1,
        range_require_rsi_extreme=True,
        range_allow_long=False,
        range_allow_short=True,
        range_short_rel_slope_max=0.0004,
        range_short_rsi_high=65.0,
        range_short_target_mode=job["target_mode"],
        range_stop_buffer_atr=job["stop_buffer"],
    )
    result = run_range_backtest(
        extra_args=cfg, fromdate=IS_FROM, todate=IS_TO, quiet=True
    )
    row = {
        "short_target_mode": job["target_mode"],
        "stop_buffer_atr": job["stop_buffer"],
        "false_break_atr": job["break_atr"],
        "reclaim_atr": job["reclaim_atr"],
    }
    row.update(_brief(result))
    return row


def run_false_break_short_context_grid():
    jobs = []
    for context in (
        "slope", "below_ema48", "below_ema96",
        "bear_ema", "below_prev_day",
    ):
        for target_mode in ("mid", "half_mid"):
            for stop_buffer in (0.50, 1.00):
                jobs.append(dict(
                    context=context,
                    target_mode=target_mode,
                    stop_buffer=stop_buffer,
                ))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_false_break_short_context_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_short_context_grid.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _false_break_short_context_one(job):
    cfg = dict(
        DEFAULTS,
        range_entry_mode="false_break",
        range_false_break_atr=0.20,
        range_reclaim_atr=0.10,
        range_reclaim_max_bars=0,
        range_min_boundary_touches=1,
        range_require_rsi_extreme=True,
        range_allow_long=False,
        range_allow_short=True,
        range_short_context=job["context"],
        range_short_rsi_high=65.0,
        range_short_target_mode=job["target_mode"],
        range_stop_buffer_atr=job["stop_buffer"],
    )
    result = run_range_backtest(
        extra_args=cfg, fromdate=IS_FROM, todate=IS_TO, quiet=True
    )
    row = {
        "short_context": job["context"],
        "short_target_mode": job["target_mode"],
        "stop_buffer_atr": job["stop_buffer"],
    }
    row.update(_brief(result))
    return row


def run_false_break_short_daily_grid():
    jobs = []
    for break_atr, reclaim_atr in ((0.10, 0.05), (0.20, 0.10), (0.30, 0.15)):
        for target_mode in ("mid", "half_mid"):
            for rsi_high in (60.0, 65.0, 70.0):
                jobs.append(dict(
                    break_atr=break_atr,
                    reclaim_atr=reclaim_atr,
                    target_mode=target_mode,
                    rsi_high=rsi_high,
                ))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_false_break_short_daily_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_short_daily_grid.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _false_break_short_daily_one(job):
    cfg = dict(
        DEFAULTS,
        range_entry_mode="false_break",
        range_false_break_atr=job["break_atr"],
        range_reclaim_atr=job["reclaim_atr"],
        range_reclaim_max_bars=0,
        range_min_boundary_touches=1,
        range_require_rsi_extreme=True,
        range_allow_long=False,
        range_allow_short=True,
        range_short_context="below_prev_day",
        range_short_rsi_high=job["rsi_high"],
        range_short_target_mode=job["target_mode"],
        range_stop_buffer_atr=0.50,
    )
    result = run_range_backtest(
        extra_args=cfg, fromdate=IS_FROM, todate=IS_TO, quiet=True
    )
    row = {
        "false_break_atr": job["break_atr"],
        "reclaim_atr": job["reclaim_atr"],
        "short_target_mode": job["target_mode"],
        "short_rsi_high": job["rsi_high"],
    }
    row.update(_brief(result))
    return row


SHORT_CANDIDATES = {
    "S_mid_rsi60": dict(
        range_short_target_mode="mid",
        range_short_rsi_high=60.0,
    ),
    "S_half_rsi60": dict(
        range_short_target_mode="half_mid",
        range_short_rsi_high=60.0,
    ),
    "S_mid_rsi65": dict(
        range_short_target_mode="mid",
        range_short_rsi_high=65.0,
    ),
}

COMBINED_BASE = dict(
    range_entry_mode="false_break",
    range_reclaim_max_bars=0,
    range_min_boundary_touches=1,
    range_require_rsi_extreme=True,
    range_rsi_low=35.0,
    range_short_rsi_high=60.0,
    range_short_context="below_prev_day",
    range_target_mode="mid",
    range_short_target_mode="half_mid",
    range_short_min_reward_risk=0.50,
    range_stop_buffer_atr=0.50,
    range_long_false_break_atr=0.20,
    range_long_reclaim_atr=0.10,
    range_short_false_break_atr=0.10,
    range_short_reclaim_atr=0.05,
)

COMBINED_VARIANTS = {
    "long_only": dict(range_allow_long=True, range_allow_short=False),
    "short_only": dict(range_allow_long=False, range_allow_short=True),
    "combined_skip": dict(
        range_allow_long=True, range_allow_short=True,
        range_conflict_policy="skip",
    ),
    "combined_long_priority": dict(
        range_allow_long=True, range_allow_short=True,
        range_conflict_policy="long",
    ),
    "combined_short_priority": dict(
        range_allow_long=True, range_allow_short=True,
        range_conflict_policy="short",
    ),
}


def run_combined_holdout():
    jobs = []
    for name, overrides in COMBINED_VARIANTS.items():
        for label, start, end in (
            ("IS", IS_FROM, IS_TO),
            ("OOS", OOS_FROM, OOS_TO),
            ("FULL", IS_FROM, OOS_TO),
        ):
            jobs.append(dict(
                variant=name,
                overrides=overrides,
                label=label,
                start=start,
                end=end,
            ))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_combined_holdout_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_combined_holdout.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _combined_holdout_one(job):
    cfg = dict(DEFAULTS, **COMBINED_BASE, **job["overrides"])
    result = run_range_backtest(
        extra_args=cfg, fromdate=job["start"], todate=job["end"], quiet=True
    )
    row = {
        "variant": job["variant"],
        "window": job["label"],
    }
    row.update(_brief(result))
    return row


COMBINED_SELECTED = dict(
    COMBINED_BASE,
    **COMBINED_VARIANTS["combined_skip"],
)

# Reproducible alignment target for the MT5 EA:
#   Wilder ATR at signal-bar previous bar + explicit MT5 H1 mapping.
MT5_ALIGNED_CONFIG = dict(
    DEFAULTS,
    **COMBINED_SELECTED,
    atr_shift=1,
    atr_method="wilder",
    range_ctx_mt5=True,
    range_ctx_completed=False,
)


def run_combined_selected_walkforward():
    folds = (
        ("F1 23H2", "2023-09-13", "2024-03-31"),
        ("F2 24H1", "2024-04-01", "2024-09-30"),
        ("F3 24H2", "2024-10-01", "2025-03-31"),
        ("F4 25H1", "2025-04-01", "2025-09-30"),
        ("F5 25H2", "2025-10-01", "2026-03-31"),
        ("F6 26H1", "2026-04-01", OOS_TO),
    )
    jobs = [dict(label=label, start=start, end=end) for label, start, end in folds]
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_combined_selected_window_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_combined_walkforward.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _combined_selected_window_one(job):
    cfg = dict(DEFAULTS, **COMBINED_SELECTED)
    result = run_range_backtest(
        extra_args=cfg, fromdate=job["start"], todate=job["end"], quiet=True
    )
    row = {"window": job["label"]}
    row.update(_brief(result))
    return row


def run_combined_selected_cost():
    jobs = [dict(spread=spread) for spread in (0.33, 0.50, 0.66, 0.99, 1.50)]
    with Pool(processes=min(5, len(jobs))) as pool:
        rows = pool.map(_combined_selected_cost_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_combined_cost.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _combined_selected_cost_one(job):
    cfg = dict(DEFAULTS, **COMBINED_SELECTED)
    result = run_range_backtest(
        extra_args=cfg,
        fromdate=IS_FROM,
        todate=OOS_TO,
        spread=job["spread"],
        quiet=True,
    )
    row = {"spread": job["spread"]}
    row.update(_brief(result))
    return row


def run_combined_atr_grid():
    jobs = [dict(atr_shift=shift) for shift in (0, 1, 2)]
    with Pool(processes=3) as pool:
        rows = pool.map(_combined_atr_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_combined_atr_grid.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _combined_atr_one(job):
    cfg = dict(DEFAULTS, **COMBINED_SELECTED)
    cfg["atr_shift"] = job["atr_shift"]
    result = run_range_backtest(
        extra_args=cfg, fromdate=IS_FROM, todate=IS_TO, quiet=True
    )
    row = {"atr_shift": job["atr_shift"]}
    row.update(_brief(result))
    return row


def run_short_candidate_holdout():
    jobs = []
    for name, overrides in SHORT_CANDIDATES.items():
        for label, start, end in (
            ("IS", IS_FROM, IS_TO),
            ("OOS", OOS_FROM, OOS_TO),
            ("FULL", IS_FROM, OOS_TO),
        ):
            jobs.append(dict(
                candidate=name,
                overrides=overrides,
                label=label,
                start=start,
                end=end,
            ))
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_short_candidate_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_short_candidate_holdout.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _short_candidate_one(job):
    cfg = dict(
        DEFAULTS,
        range_entry_mode="false_break",
        range_false_break_atr=0.10,
        range_reclaim_atr=0.05,
        range_reclaim_max_bars=0,
        range_min_boundary_touches=1,
        range_require_rsi_extreme=True,
        range_allow_long=False,
        range_allow_short=True,
        range_short_context="below_prev_day",
        range_stop_buffer_atr=0.50,
        **job["overrides"],
    )
    result = run_range_backtest(
        extra_args=cfg, fromdate=job["start"], todate=job["end"], quiet=True
    )
    row = {
        "candidate": job["candidate"],
        "window": job["label"],
    }
    row.update(_brief(result))
    return row


def run_short_selected_walkforward():
    folds = (
        ("F1 23H2", "2023-09-13", "2024-03-31"),
        ("F2 24H1", "2024-04-01", "2024-09-30"),
        ("F3 24H2", "2024-10-01", "2025-03-31"),
        ("F4 25H1", "2025-04-01", "2025-09-30"),
        ("F5 25H2", "2025-10-01", "2026-03-31"),
        ("F6 26H1", "2026-04-01", OOS_TO),
    )
    jobs = [dict(label=label, start=start, end=end) for label, start, end in folds]
    with Pool(processes=min(6, len(jobs))) as pool:
        rows = pool.map(_short_selected_window_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_short_selected_walkforward.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _short_selected_window_one(job):
    cfg = dict(
        DEFAULTS,
        range_entry_mode="false_break",
        range_false_break_atr=0.10,
        range_reclaim_atr=0.05,
        range_reclaim_max_bars=0,
        range_min_boundary_touches=1,
        range_require_rsi_extreme=True,
        range_allow_long=False,
        range_allow_short=True,
        range_short_context="below_prev_day",
        range_stop_buffer_atr=0.50,
        **SHORT_CANDIDATES["S_half_rsi60"],
    )
    result = run_range_backtest(
        extra_args=cfg, fromdate=job["start"], todate=job["end"], quiet=True
    )
    row = {"window": job["label"]}
    row.update(_brief(result))
    return row


def run_short_selected_cost():
    jobs = [dict(spread=spread) for spread in (0.33, 0.50, 0.66, 0.99, 1.50)]
    with Pool(processes=min(5, len(jobs))) as pool:
        rows = pool.map(_short_selected_cost_one, jobs)
    df = pd.DataFrame(rows)
    df.to_csv("range_false_break_short_selected_cost.csv", index=False)
    print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))
    return rows


def _short_selected_cost_one(job):
    cfg = dict(
        DEFAULTS,
        range_entry_mode="false_break",
        range_false_break_atr=0.10,
        range_reclaim_atr=0.05,
        range_reclaim_max_bars=0,
        range_min_boundary_touches=1,
        range_require_rsi_extreme=True,
        range_allow_long=False,
        range_allow_short=True,
        range_short_context="below_prev_day",
        range_stop_buffer_atr=0.50,
        **SHORT_CANDIDATES["S_half_rsi60"],
    )
    result = run_range_backtest(
        extra_args=cfg,
        fromdate=IS_FROM,
        todate=OOS_TO,
        spread=job["spread"],
        quiet=True,
    )
    row = {"spread": job["spread"]}
    row.update(_brief(result))
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--mode",
        choices=[
            "baseline", "false_break", "grid", "false_break_grid",
            "false_break_quality", "false_break_holdout",
            "false_break_walkforward", "false_break_cost", "false_break_diag",
            "false_break_long_holdout",
            "false_break_long_walkforward", "false_break_long_cost",
            "false_break_short_grid",
            "false_break_short_exit_grid",
            "false_break_short_context_grid",
            "false_break_short_daily_grid",
            "false_break_short_candidate_holdout",
            "false_break_short_selected_walkforward",
            "false_break_short_selected_cost",
            "false_break_combined_holdout",
            "false_break_combined_walkforward",
            "false_break_combined_cost",
            "false_break_combined_atr_grid",
            "quality", "lookback", "holdout", "diag",
        ],
        default="baseline",
    )
    ap.add_argument("--fromdate", default=None)
    ap.add_argument("--todate", default=None)
    args = ap.parse_args()
    if args.mode == "grid":
        run_grid()
    elif args.mode == "false_break_grid":
        run_false_break_grid()
    elif args.mode == "false_break_quality":
        run_false_break_quality_grid()
    elif args.mode == "false_break_holdout":
        run_false_break_holdout()
    elif args.mode == "false_break_walkforward":
        run_false_break_walkforward()
    elif args.mode == "false_break_cost":
        run_false_break_cost()
    elif args.mode == "false_break_diag":
        run_false_break_candidate_diag(args.fromdate, args.todate)
    elif args.mode == "false_break_long_holdout":
        run_false_break_long_holdout()
    elif args.mode == "false_break_long_walkforward":
        run_false_break_long_walkforward()
    elif args.mode == "false_break_long_cost":
        run_false_break_long_cost()
    elif args.mode == "false_break_short_grid":
        run_false_break_short_grid()
    elif args.mode == "false_break_short_exit_grid":
        run_false_break_short_exit_grid()
    elif args.mode == "false_break_short_context_grid":
        run_false_break_short_context_grid()
    elif args.mode == "false_break_short_daily_grid":
        run_false_break_short_daily_grid()
    elif args.mode == "false_break_short_candidate_holdout":
        run_short_candidate_holdout()
    elif args.mode == "false_break_short_selected_walkforward":
        run_short_selected_walkforward()
    elif args.mode == "false_break_short_selected_cost":
        run_short_selected_cost()
    elif args.mode == "false_break_combined_holdout":
        run_combined_holdout()
    elif args.mode == "false_break_combined_walkforward":
        run_combined_selected_walkforward()
    elif args.mode == "false_break_combined_cost":
        run_combined_selected_cost()
    elif args.mode == "false_break_combined_atr_grid":
        run_combined_atr_grid()
    elif args.mode == "quality":
        run_quality_grid()
    elif args.mode == "lookback":
        run_lookback_grid()
    elif args.mode == "holdout":
        run_holdout_grid()
    else:
        entry_mode = "false_break" if args.mode == "false_break" else "touch"
        run_single(
            args.fromdate,
            args.todate,
            diagnostic=(args.mode == "diag"),
            entry_mode=entry_mode,
        )


if __name__ == "__main__":
    main()
