# -*- coding: utf-8 -*-
import backtrader as bt
import pandas as pd
import numpy as np, traceback

LOG = r"D:\workbuddy\tushare\XAUUSD\xauusd策略\test_cancel.out"
def w(*a):
    with open(LOG, "a") as f:
        f.write(" ".join(str(x) for x in a) + "\n")

class T(bt.Strategy):
    def __init__(self):
        w("INIT ok")
        self.i = 0
        self.sl = None
        self.tp = None
        self.phase = "enter"
    def next(self):
        try:
            self.i += 1
            pos = self.broker.getposition(self.data)
            if self.phase == "enter" and not pos:
                self.buy()
                self.phase = "waitfill"
            elif self.phase == "waitfill" and pos:
                self.sl = self.sell(exectype=bt.Order.Stop, price=self.data.close[0]-5)
                self.tp = self.sell(exectype=bt.Order.Limit, price=self.data.close[0]+20)
                self.phase = "canceltest"
            elif self.phase == "canceltest":
                if self.sl is not None and self.sl.alive():
                    self.cancel(self.sl)
                if self.tp is not None and self.tp.alive():
                    self.cancel(self.tp)
                self.close()
                self.phase = "done"
            w(f"bar={self.i} pos={pos.size:.4f} sl_status={self.sl.status if self.sl else '-'} sl_alive={self.sl.alive() if self.sl else None}")
        except Exception as e:
            w("NEXT EXC:", repr(e), traceback.format_exc())
    def notify_trade(self, trade):
        w(f"  >>> CLOSED bar={self.i} pnl={trade.pnlcomm:+.2f} size={trade.size}")

open(LOG, "w").close()
idx = pd.date_range("2024-01-01 00:00", periods=120, freq="15min")
price = 2000 + np.concatenate([np.arange(40), np.arange(40)[::-1], np.arange(40)])
df = pd.DataFrame({"open":price,"high":price+1,"low":price-1,"close":price,"volume":1.0}, index=idx)
data = bt.feeds.PandasData(dataname=df)
cerebro = bt.Cerebro()
cerebro.addstrategy(T)
cerebro.broker.setcash(1000.0)
cerebro.broker.set_slippage_fixed(fixed=0.0)
comminfo = bt.CommissionInfo(commission=0.0, mult=100.0, leverage=1000.0, stocklike=False)
cerebro.broker.addcommissioninfo(comminfo)
cerebro.run()
w("DONE")
