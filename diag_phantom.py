# -*- coding: utf-8 -*-
"""诊断 max_positions=1 下 trades_log 平仓数与 broker 真实成交回合数的差异(幽灵平仓)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import importlib.util
import box_channel_optimized as m
import backtrader as bt

# 用 SourceFileLoader 加载, 以便子类化并注入 notify_trade
SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "box_channel_optimized.py")
spec = importlib.util.spec_from_file_location("bco_diag", SRC)
mod = importlib.util.module_from_spec(spec)
sys.modules["bco_diag"] = mod
spec.loader.exec_module(mod)

class DiagStrat(mod.BoxChannelOptStrategy):
    def __init__(self):
        super().__init__()
        self.real_trades = []   # 真实 broker 成交回合: (dt_close, pnl, exit_price, dir)
        self._trade_open_dt = {}
    def notify_trade(self, trade):
        if trade.isclosed:
            exit_px = trade.price  # 入场价(仅参考)
            # 取最近一笔 close 价格近似: 用 trades_log 后续对不上时回退
            self.real_trades.append(dict(
                dt=bt.num2date(self.data.datetime[0]),
                pnl=trade.pnlcomm,
                size=trade.size,
                entry=trade.price,
            ))
        elif trade.justopened:
            pass

def run_diag(max_positions):
    from box_channel_optimized import run_backtest  # noqa
    cerebro = bt.Cerebro()
    cerebro.addsizer(m.FixedLotSizer, lot=0.01)
    df_primary, data_primary, sp_primary = m.load_data("15m")
    cerebro.adddata(data_primary, name="15m")
    _, data_cf, _ = m.load_data("1h")
    cerebro.adddata(data_cf, name="1h")
    extra = dict(lot=0.01, max_positions=max_positions)
    extra.update(m.PROFILES["scalp"])
    cerebro.addstrategy(DiagStrat, spread_series=sp_primary, **extra)
    comminfo = bt.CommissionInfo(commission=0.0, mult=100.0, leverage=1000.0, stocklike=False)
    cerebro.broker.addcommissioninfo(comminfo)
    cerebro.broker.setcash(1000.0)
    cerebro.broker.set_slippage_fixed(fixed=0.165)
    cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="ta")
    res = cerebro.run(optreturn=False)
    strat = res[0]
    ta = strat.analyzers.ta.get_analysis()
    broker_closed = ta["total"]["closed"]
    real = strat.real_trades
    tl = strat.trades_log
    # FIFO 配对
    open_stack = []
    paired = []
    for t in tl:
        if t["kind"] == "open":
            open_stack.append(t)
        elif t["kind"] == "close":
            if open_stack:
                paired.append((open_stack.pop(), t))
    # 统计 close reason
    from collections import Counter
    reasons = Counter(t["reason"] for _, t in paired)
    print(f"\n=== max_positions={max_positions} ===")
    print(f"broker TradeAnalyzer closed = {broker_closed}")
    print(f"notify_trade 真实回合        = {len(real)}")
    print(f"trades_log close 配对        = {len(paired)}")
    print(f"trades_log close reason 分布 = {dict(reasons)}")
    print(f"notify_trade 总盈亏          = {sum(r['pnl'] for r in real):+.2f}")
    print(f"trades_log 配对总盈亏        = {sum((c['price']-o['price'])*(1 if o['side']=='long' else -1) for o,c in paired):+.2f}")
    # 差异
    print(f"幽灵平仓(配对-close 数 - broker回合) = {len(paired) - broker_closed}")
    return broker_closed, len(real), len(paired), reasons

if __name__ == "__main__":
    run_diag(1)
