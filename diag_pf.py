# -*- coding: utf-8 -*-
"""验证 run_backtest 中 pnl 提取是否真的取到值。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import backtrader as bt
import box_channel_optimized as m

PROFILE = "scalp"
extra = dict(lot=0.01, max_positions=1)
extra.update(m.PROFILES[PROFILE])
from box_channel_optimized import load_data
cerebro = bt.Cerebro()
cerebro.addsizer(m.FixedLotSizer, lot=0.01)
df_primary, _, sp_primary = load_data("15m")
feed = bt.feeds.PandasData(dataname=df_primary)
cerebro.adddata(feed, name="slot0")
_, data_cf, _ = load_data("1h")
cerebro.adddata(data_cf, name="1h")
cerebro.addstrategy(m.BoxChannelOptStrategy, slot_feeds=[feed], ctx_data=data_cf,
                    spread_series=sp_primary, **extra)
comminfo = bt.CommissionInfo(commission=0.0, mult=100.0, leverage=1000.0, stocklike=False)
cerebro.broker.addcommissioninfo(comminfo)
cerebro.broker.setcash(1000.0)
cerebro.broker.set_slippage_fixed(fixed=0.165)
cerebro.addanalyzer(bt.analyzers.TradeAnalyzer, _name="ta")
strat = cerebro.run(optreturn=False)[0]
ta = strat.analyzers.ta.get_analysis()
pnl = ta.get("pnl", {})
print("type(pnl)=", type(pnl))
print("pnl keys=", list(pnl.keys()) if isinstance(pnl, dict) else pnl)
won = pnl.get("won", {})
print("won keys=", list(won.keys()) if isinstance(won, dict) else won)
won_pnl = won.get("pnl", {})
print("won_pnl=", won_pnl)
val = won_pnl.get("total", 0.0) if isinstance(won_pnl, dict) else None
print("extracted pnl_won =", val)
print("gross_profit =", val, " gross_loss =", -ta.get('pnl',{}).get('lost',{}).get('pnl',{}).get('total',0.0))
