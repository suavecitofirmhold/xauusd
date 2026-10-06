"""
诊断 2024-04-08 05:00 UTC(截图 13:00 北京) 这笔 long 的开仓逻辑.
"""
import sys, os
import pandas as pd
import datetime as dt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import box_channel_optimized as M
import backtrader as bt

TARGET = dt.datetime(2024, 4, 8, 4, 45)  # 信号产生bar; 市价单下一bar 05:00 fill(=截图13:00北京)
FROM = pd.Timestamp("2024-04-01")
TO = pd.Timestamp("2024-04-15")


def patched_classify_channel(self):
    ch, rel = self._original_classify_channel()
    self._last_rel = rel
    self._last_channel = ch
    return ch, rel


def patched_classify_short_direction(self):
    d = self._original_classify_short_direction()
    self._last_short_dir = d
    return d


def patched_want_entry(self, channel, support, resistance):
    sig = self._original_want_entry(channel, support, resistance)
    self._last_support = support
    self._last_resistance = resistance
    self._last_signal = sig
    return sig


def patched_trend_filter_pass(self, want):
    ok = self._original_trend_filter_pass(want)
    self._last_trend_ok = ok
    return ok


def patched_open_position(self, want, entry_ref, stop, target, rm, rsi):
    cur = bt.num2date(self.data.datetime[0])
    if abs((cur - TARGET).total_seconds()) < 900:
        o, h, l, c = self.data.open[0], self.data.high[0], self.data.low[0], self.data.close[0]
        print("\n=== 2024-04-08 13:00 北京(信号bar 04:45 UTC, fill 05:00 UTC) 开仓诊断 ===")
        print(f"  服务器/北京     : 2024-04-08 13:00 (UTC+8 fill, 信号bar 12:45)")
        print(f"  方向            : {want}")
        print(f"  o/h/l/c         : {o:.2f} / {h:.2f} / {l:.2f} / {c:.2f}")
        print(f"  入场价(c)       : {entry_ref:.2f}")
        print(f"  1h 通道/斜率    : {getattr(self,'_last_channel','?')} / rel={getattr(self,'_last_rel',0):.6f}")
        print(f"  15m 短方向      : {getattr(self,'_last_short_dir','?')}")
        print(f"  支撑/阻力       : {getattr(self,'_last_support',None):.2f} / {getattr(self,'_last_resistance',None):.2f}")
        print(f"  反转过滤信号    : {getattr(self,'_last_signal','?')}")
        print(f"  EMA96           : {self.ma_tf[0]:.2f}")
        print(f"  ATR14(shift={self.p.atr_shift}) : {self.atr[-self.p.atr_shift]:.2f}")
        print(f"  ATR14 avg48     : {self.atr_avg[-self.p.atr_shift]:.2f}")
        print(f"  RSI14           : {self.rsi[0]:.1f}")
        print(f"  止损/止盈       : {stop:.2f} / {target:.2f}  (r_mult={rm:.2f})")
        # 时段/点差
        off = M.tmgm_server_offset_hours(cur)
        server_hour = (cur.hour + off) % 24
        eff_start = self.p.trade_start_hour if off == 3 else self.p.trade_start_hour - 1
        print(f"  时段            : server_h={server_hour} off={off} eff_start={eff_start} ok={eff_start <= server_hour < self.p.trade_end_hour}")
        print(f"  点差            : {self.current_spread_points(cur):.1f} points <= {self.p.max_spread_points}")
        # trend_filter detail
        if self.p.ma_filter:
            ma = self.ma_tf[0]
            over = (c - ma) / self.atr[-self.p.atr_shift] if self.atr[-self.p.atr_shift] > 0 else 0
            print(f"  MA过滤          : close({c:.2f}) {'>' if c>ma else '<='} EMA96({ma:.2f})  over_ATR={over:.2f}")
        if self.p.atr_regime_gate:
            an = self.atr[-self.p.atr_shift]
            aa = self.atr_avg[-self.p.atr_shift]
            ratio = an/aa if aa>0 else 0
            print(f"  ATR_regime      : atr/avg={ratio:.2f} <= {self.p.atr_regime_mult} ? {ratio <= self.p.atr_regime_mult}")
        if self.p.max_ma_dist_atr > 0:
            ma = self.ma_tf[0]
            atr_now = self.atr[-self.p.atr_shift]
            over = (c - ma) / atr_now if atr_now > 0 else 0
            atr_avg = self.atr_avg[-self.p.atr_shift]
            lowvol = (atr_avg > 0 and atr_now / atr_avg < self.p.ma_dist_lowvol_mult)
            print(f"  远离趋势过滤    : over={over:.2f} max={self.p.max_ma_dist_atr} lowvol={lowvol} apply={lowvol}")
    return self._original_open_position(want, entry_ref, stop, target, rm, rsi)


def main():
    cls = M.BoxChannelOptStrategy
    cls._original_classify_channel = cls.classify_channel
    cls.classify_channel = patched_classify_channel
    cls._original_classify_short_direction = cls.classify_short_direction
    cls.classify_short_direction = patched_classify_short_direction
    cls._original_want_entry = cls.want_entry
    cls.want_entry = patched_want_entry
    cls._original_trend_filter_pass = cls._trend_filter_pass
    cls._trend_filter_pass = patched_trend_filter_pass
    cls._original_open_position = cls._open_position
    cls._open_position = patched_open_position

    extra = {**M.PROFILES["scalp"], "atr_shift": 1}
    res = M.run_backtest(freq="15m", extra_args=extra, cash=1000.0,
                         fromdate=FROM, todate=TO, quiet=True)
    print(f"\n窗口内总平仓数: {res['closed']}")


if __name__ == "__main__":
    main()
