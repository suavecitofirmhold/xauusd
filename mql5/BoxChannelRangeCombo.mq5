//+------------------------------------------------------------------+
//|                                      BoxChannelRangeCombo.mq5    |
//|  XAUUSD M15 range false-break combo                              |
//|  Ported from xauusd_range_boundary_reversion.py                  |
//|  COMBINED_SELECTED                                                |
//+------------------------------------------------------------------+
#property copyright "ported from xauusd_range_boundary_reversion.py"
#property version   "1.00"
#property strict
#property description "XAUUSD M15 flat-range false-break long/short combo"

#include <Trade\Trade.mqh>

enum ENUM_ATR_MODE
{
   ATR_MODE_MT5_IATR = 0,
   ATR_MODE_WILDER_PREV = 1
};

input group "===== Range regime ====="
input int      InpTrendWindow        = 24;      // 1h regression window
input double   InpRelSlopeMax        = 0.0004;  // abs(1h rel slope) < this
input int      InpRangeLookback      = 24;      // 15m range window, signal bar excluded
input double   InpEfficiencyMax      = 0.35;    // 15m efficiency ratio < this
input double   InpRangeWidthMinAtr   = 2.0;     // range width / ATR min
input double   InpRangeWidthMaxAtr   = 6.0;     // range width / ATR max
input int      InpAtrPeriod          = 14;
input ENUM_ATR_MODE InpAtrMode       = ATR_MODE_WILDER_PREV;

input group "===== False-break long ====="
input double   InpLongBreakAtr       = 0.20;    // low pierces lower by this x ATR
input double   InpLongReclaimAtr     = 0.10;    // close reclaims lower by this x ATR
input double   InpLongRsiLow         = 35.0;    // RSI <= this
input double   InpLongMinRR          = 0.80;

input group "===== False-break short ====="
input double   InpShortBreakAtr      = 0.10;    // high pierces upper by this x ATR
input double   InpShortReclaimAtr    = 0.05;    // close reclaims upper by this x ATR
input double   InpShortRsiHigh       = 60.0;    // RSI >= this
input bool     InpShortBelowPrevDay  = true;    // 1h close < close 24 H1 bars ago
input double   InpShortMinRR         = 0.50;

input group "===== Risk / exits ====="
input double   InpStopBufferAtr      = 0.50;    // stop beyond false-break extreme
input bool     InpShortHalfMidTarget = true;    // short TP = half way to range mid
input int      InpMaxHoldBars        = 12;      // time stop
input int      InpCooldownBars       = 4;       // same-direction cooldown
input double   InpFixedLot           = 0.01;
input int      InpMaxSpreadPoints    = 50;

input group "===== Session / overnight ====="
input int      InpTradeStartHour     = 1;       // server GMT+3 DST base
input int      InpTradeEndHour       = 22;
input bool     InpAvoidOvernight     = true;
input int      InpServerCloseHour    = 22;
input double   InpStrongOvernight    = 0.0020;

input group "===== Execution / logs ====="
input bool     InpUseServerStops     = true;
input long     InpMagic              = 20260921;
input bool     InpLogTrades          = true;
input string   InpLogFile            = "BoxChannelRangeCombo_trades.csv";
input bool     InpHeartbeat          = false;
input bool     InpDebugSignals       = false;   // diagnostic: [SIGNAL] every new bar
input bool     InpDumpBars           = false;   // diagnostic: [BAR] every new bar

//=================== globals ===================
CTrade trade;

datetime g_lastBarTime = 0;
int      g_lastEntryBar = -9999;
int      g_lastEntrySide = 0;

int      g_ticket = 0;
int      g_dir = 0;
double   g_entryPrice = 0.0;
double   g_stop = 0.0;
double   g_target = 0.0;
int      g_entryBar = 0;
double   g_positionSwap = 0.0;
string   g_closeReason = "";

double   g_point = 0.0;
int      g_digits = 2;
double   g_volMin = 0.01, g_volStep = 0.01, g_volMax = 100.0;

int      g_hATR = INVALID_HANDLE;
int      g_hRSI = INVALID_HANDLE;

double   g_wilderAtr = 0.0;
double   g_wilderAtrPrev = 0.0;
bool     g_wilderReady = false;
datetime g_wilderLastBar = 0;

int g_d_newbar = 0;
int g_d_regime = 0;
int g_d_width = 0;
int g_d_rsi = 0;
int g_d_cool = 0;
int g_d_spread = 0;
int g_d_session = 0;
int g_d_conflict = 0;
int g_d_attempt = 0;
int g_d_open = 0;
int g_d_timestop = 0;

datetime g_lastSkip = 0;
string   g_lastSkipMsg = "";

//=================== helpers ===================
double GetATRVal(int shift = 1)
{
   double buf[1];
   if(g_hATR == INVALID_HANDLE) return 0.0;
   if(CopyBuffer(g_hATR, 0, shift, 1, buf) != 1) return 0.0;
   return buf[0];
}

void InitWilderATR()
{
   int bars = iBars(_Symbol, PERIOD_M15);
   int period = MathMax(InpAtrPeriod, 1);
   if(bars < period + 3) return;

   double prevClose = iClose(_Symbol, PERIOD_M15, bars - 1);
   double sum = 0.0;
   int count = 0;
   double atr = 0.0;
   double atrPrev1 = 0.0;

   for(int i = bars - 2; i >= 1; i--)
   {
      double h = iHigh(_Symbol, PERIOD_M15, i);
      double l = iLow(_Symbol, PERIOD_M15, i);
      double c = iClose(_Symbol, PERIOD_M15, i);
      double tr = MathMax(h - l, MathMax(MathAbs(h - prevClose), MathAbs(l - prevClose)));
      prevClose = c;

      if(count < period)
      {
         sum += tr;
         count++;
         if(count == period) atr = sum / period;
      }
      else
      {
         atrPrev1 = atr;
         atr = (atr * (period - 1) + tr) / period;
      }
   }

   if(count >= period && atr > 0.0)
   {
      g_wilderAtr = atr;
      g_wilderAtrPrev = (atrPrev1 > 0.0 ? atrPrev1 : atr);
      g_wilderReady = true;
      g_wilderLastBar = iTime(_Symbol, PERIOD_M15, 1);
   }
}

double GetSignalATR()
{
   if(InpAtrMode == ATR_MODE_MT5_IATR)
      return GetATRVal(1);

   int period = MathMax(InpAtrPeriod, 1);
   datetime signalBar = iTime(_Symbol, PERIOD_M15, 1);
   if(!g_wilderReady)
   {
      InitWilderATR();
      return g_wilderAtrPrev;
   }
   if(signalBar != g_wilderLastBar)
   {
      double prevClose = iClose(_Symbol, PERIOD_M15, 2);
      double h = iHigh(_Symbol, PERIOD_M15, 1);
      double l = iLow(_Symbol, PERIOD_M15, 1);
      double tr = MathMax(h - l, MathMax(MathAbs(h - prevClose), MathAbs(l - prevClose)));
      g_wilderAtrPrev = g_wilderAtr;
      g_wilderAtr = (g_wilderAtr * (period - 1) + tr) / period;
      g_wilderLastBar = signalBar;
   }
   return g_wilderAtrPrev;
}

double GetRSIVal(int shift = 1)
{
   double buf[1];
   if(g_hRSI == INVALID_HANDLE) return 0.0;
   if(CopyBuffer(g_hRSI, 0, shift, 1, buf) != 1) return 0.0;
   return buf[0];
}

double LinRegSlope(const double &y[], int n)
{
   if(n < 2) return 0.0;
   double xbar = (n - 1) / 2.0;
   double num = 0.0, den = 0.0;
   for(int i = 0; i < n; i++)
   {
      double dx = i - xbar;
      num += dx * y[i];
      den += dx * dx;
   }
   return (den > 0.0) ? (num / den) : 0.0;
}

double ArrayMean(const double &a[], int n)
{
   if(n <= 0) return 0.0;
   double s = 0.0;
   for(int i = 0; i < n; i++) s += a[i];
   return s / n;
}

void GetCloses(ENUM_TIMEFRAMES tf, int start_shift, int n, double &out[])
{
   ArrayResize(out, n);
   for(int i = 0; i < n; i++)
      out[i] = iClose(_Symbol, tf, start_shift + n - 1 - i);
}

double RangeRelSlope()
{
   int n = InpTrendWindow;
   double closes[];
   GetCloses(PERIOD_H1, 1, n, closes);
   double slope = LinRegSlope(closes, n);
   double mean = ArrayMean(closes, n);
   return (mean > 0.0) ? (slope / mean) : 0.0;
}

double EfficiencyRatio15m()
{
   int n = InpRangeLookback;
   double closes[];
   GetCloses(PERIOD_M15, 1, n + 1, closes);
   if(ArraySize(closes) < n + 1) return 1.0;
   double net = MathAbs(closes[n] - closes[0]);
   double path = 0.0;
   for(int i = 1; i <= n; i++)
      path += MathAbs(closes[i] - closes[i - 1]);
   return (path > 0.0) ? (net / path) : 1.0;
}

bool RangeStats(double &lower, double &upper, double &atr, double &widthAtr)
{
   lower = 0.0; upper = 0.0; atr = 0.0; widthAtr = 0.0;
   int n = InpRangeLookback;
   if(iBars(_Symbol, PERIOD_M15) < n + 3) return false;

   double lo = DBL_MAX, hi = -DBL_MAX;
   for(int i = 0; i < n; i++)
   {
      int shift = n + 1 - i;   // shifts n+1 ... 2, excludes signal bar shift 1
      double h = iHigh(_Symbol, PERIOD_M15, shift);
      double l = iLow(_Symbol, PERIOD_M15, shift);
      if(h > hi) hi = h;
      if(l < lo) lo = l;
   }
   atr = GetSignalATR();
   if(atr <= 0.0 || hi <= lo) return false;
   lower = lo;
   upper = hi;
   widthAtr = (upper - lower) / atr;
   return true;
}

bool IsFlatRange()
{
   double rel = RangeRelSlope();
   if(MathAbs(rel) >= InpRelSlopeMax) return false;
   if(EfficiencyRatio15m() >= InpEfficiencyMax) return false;
   double lower, upper, atr, widthAtr;
   if(!RangeStats(lower, upper, atr, widthAtr)) return false;
   if(widthAtr < InpRangeWidthMinAtr || widthAtr > InpRangeWidthMaxAtr) return false;
   return true;
}

bool BelowPrevDay()
{
   if(!InpShortBelowPrevDay) return true;
   if(iBars(_Symbol, PERIOD_H1) < 26) return false;
   return iClose(_Symbol, PERIOD_H1, 1) < iClose(_Symbol, PERIOD_H1, 25);
}

bool BuildLongRisk(double lower, double upper, double atr, double entry, double low,
                   double &stop, double &target)
{
   stop = low - InpStopBufferAtr * atr;
   target = (lower + upper) / 2.0;
   double risk = MathAbs(entry - stop);
   double reward = MathAbs(target - entry);
   if(risk <= 0.0 || reward <= 0.0) return false;
   return (reward / risk >= InpLongMinRR);
}

bool BuildShortRisk(double lower, double upper, double atr, double entry, double high,
                    double &stop, double &target)
{
   stop = high + InpStopBufferAtr * atr;
   double mid = (lower + upper) / 2.0;
   target = InpShortHalfMidTarget ? (entry - 0.5 * (entry - mid)) : mid;
   double risk = MathAbs(entry - stop);
   double reward = MathAbs(target - entry);
   if(risk <= 0.0 || reward <= 0.0) return false;
   return (reward / risk >= InpShortMinRR);
}

int RangeSignal(double &stop, double &target)
{
   stop = 0.0; target = 0.0;
   double rel = RangeRelSlope();
   double er = EfficiencyRatio15m();
   double lower, upper, atr, widthAtr;
   bool statsOk = RangeStats(lower, upper, atr, widthAtr);
   bool flat = statsOk
      && MathAbs(rel) < InpRelSlopeMax
      && er < InpEfficiencyMax
      && widthAtr >= InpRangeWidthMinAtr
      && widthAtr <= InpRangeWidthMaxAtr;

   if(!statsOk)
   {
      g_d_regime++;
      return 0;
   }

   double h = iHigh(_Symbol, PERIOD_M15, 1);
   double l = iLow(_Symbol, PERIOD_M15, 1);
   double c = iClose(_Symbol, PERIOD_M15, 1);
   double rsi = GetRSIVal(1);

   bool longSetup = flat &&
      (l <= lower - InpLongBreakAtr * atr) &&
      (c >= lower + InpLongReclaimAtr * atr) &&
      (c <= upper) &&
      (rsi <= InpLongRsiLow);

   bool shortSetup = flat &&
      (h >= upper + InpShortBreakAtr * atr) &&
      (c <= upper - InpShortReclaimAtr * atr) &&
      (c >= lower) &&
      (rsi >= InpShortRsiHigh) &&
      BelowPrevDay();

   double longStop = 0.0, longTarget = 0.0, shortStop = 0.0, shortTarget = 0.0;
   bool longRisk = longSetup && BuildLongRisk(lower, upper, atr, c, l, longStop, longTarget);
   bool shortRisk = shortSetup && BuildShortRisk(lower, upper, atr, c, h, shortStop, shortTarget);

   if(InpDebugSignals)
      PrintFormat(
         "[SIGNAL] time=%s rel=%.8f er=%.5f width=%.5f atr=%.3f lower=%.3f upper=%.3f "
         "h=%.3f l=%.3f c=%.3f rsi=%.2f h1ct=%s h1pt=%s h1c=%.3f h1p=%.3f "
         "belowPrevDay=%d flat=%d longSetup=%d "
         "shortSetup=%d longRisk=%d shortRisk=%d",
         TimeToString(iTime(_Symbol, PERIOD_M15, 1), TIME_DATE | TIME_SECONDS),
         rel, er, widthAtr, atr, lower, upper, h, l, c, rsi,
         TimeToString(iTime(_Symbol, PERIOD_H1, 1), TIME_DATE | TIME_SECONDS),
         TimeToString(iTime(_Symbol, PERIOD_H1, 25), TIME_DATE | TIME_SECONDS),
         iClose(_Symbol, PERIOD_H1, 1), iClose(_Symbol, PERIOD_H1, 25),
         (int)BelowPrevDay(), (int)flat, (int)longSetup,
         (int)shortSetup, (int)longRisk, (int)shortRisk
      );

   if(!flat)
   {
      g_d_regime++;
      return 0;
   }

   if(longRisk && shortRisk)
   {
      g_d_conflict++;
      return 0;   // conservative policy: skip same-bar long/short conflict
   }
   if(longRisk)
   {
      stop = longStop;
      target = longTarget;
      return 1;
   }
   if(shortRisk)
   {
      stop = shortStop;
      target = shortTarget;
      return -1;
   }
   return 0;
}

double NormalizeLot(double lot)
{
   double v = lot;
   if(v < g_volMin) v = g_volMin;
   if(v > g_volMax) v = g_volMax;
   if(g_volStep > 0.0)
      v = g_volMin + MathFloor((v - g_volMin) / g_volStep + 0.5) * g_volStep;
   return NormalizeDouble(v, 2);
}

bool SpreadOK()
{
   long spread = 0;
   if(!SymbolInfoInteger(_Symbol, SYMBOL_SPREAD, spread)) return true;
   return ((int)spread <= InpMaxSpreadPoints);
}

void SkipLog(string msg)
{
   datetime now = TimeCurrent();
   if(now - g_lastSkip < 30 && g_lastSkipMsg == msg) return;
   g_lastSkip = now;
   g_lastSkipMsg = msg;
   Print("[SKIP] " + msg);
}

int EffectiveStartHour()
{
   if(TimeGMTOffset() >= 3 * 3600) return InpTradeStartHour;
   return InpTradeStartHour - 1;
}

//=================== logs ===================
void LogTrade(string action, double price, double profit = 0.0,
              string reason = "", double swap = 0.0, double commission = 0.0)
{
   if(!InpLogTrades) return;
   int h = FileOpen(InpLogFile,
                    FILE_CSV | FILE_READ | FILE_WRITE | FILE_ANSI | FILE_SHARE_READ, ',');
   if(h == INVALID_HANDLE)
      h = FileOpen(InpLogFile,
                   FILE_CSV | FILE_WRITE | FILE_ANSI, ',');
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   if(FileSize(h) == 0)
      FileWrite(h, "time", "magic", "action", "side", "price", "stop", "target",
                "lot", "profit", "swap", "commission", "reason");
   FileWrite(h,
             TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
             IntegerToString(InpMagic),
             action,
             (g_dir == 1 ? "long" : (g_dir == -1 ? "short" : "")),
             DoubleToString(price, g_digits),
             DoubleToString(g_stop, g_digits),
             DoubleToString(g_target, g_digits),
             DoubleToString(NormalizeLot(InpFixedLot), 2),
             DoubleToString(profit, 2),
             DoubleToString(swap, 2),
             DoubleToString(commission, 2),
             reason);
   FileClose(h);
}

void PrintCumProfit(double lastProfit)
{
   double total = 0.0;
   if(HistorySelect(0, TimeCurrent()))
   {
      int n = HistoryDealsTotal();
      for(int i = 0; i < n; i++)
      {
         ulong dt = HistoryDealGetTicket(i);
         if(dt == 0) continue;
         if(HistoryDealGetString(dt, DEAL_SYMBOL) != _Symbol) continue;
         long mg = 0;
         if(!HistoryDealGetInteger(dt, DEAL_MAGIC, mg)) continue;
         if(mg != InpMagic) continue;
         total += HistoryDealGetDouble(dt, DEAL_PROFIT);
         total += HistoryDealGetDouble(dt, DEAL_COMMISSION);
      }
   }
   PrintFormat("[PNL] trade=%.2f USD cumulative=%.2f USD", lastProfit, total);
}

void LogClosedByHistory(ulong ticket, string reasonOverride = "")
{
   double closePrice = 0.0, profit = 0.0, swapTotal = 0.0, commission = 0.0;
   if(HistorySelectByPosition(ticket))
   {
      int total = HistoryDealsTotal();
      for(int i = 0; i < total; i++)
      {
         ulong dt = HistoryDealGetTicket(i);
         if(dt == 0) continue;
         swapTotal += HistoryDealGetDouble(dt, DEAL_SWAP);
         long entry = 0;
         if(HistoryDealGetInteger(dt, DEAL_ENTRY, entry) && entry == DEAL_ENTRY_OUT)
         {
            closePrice = HistoryDealGetDouble(dt, DEAL_PRICE);
            profit = HistoryDealGetDouble(dt, DEAL_PROFIT);
            commission = HistoryDealGetDouble(dt, DEAL_COMMISSION);
         }
      }
   }
   profit += commission + swapTotal;
   string reason = (reasonOverride != "" ? reasonOverride : "broker");
   LogTrade("close", closePrice, profit, reason, swapTotal, commission);
   PrintCumProfit(profit);
}

//=================== trading ===================
void OpenPosition(int want, double price, double stop, double target)
{
   double lot = NormalizeLot(InpFixedLot);
   double sl = InpUseServerStops ? NormalizeDouble(stop, g_digits) : 0.0;
   double tp = InpUseServerStops ? NormalizeDouble(target, g_digits) : 0.0;
   bool ok = false;

   if(want == 1)
      ok = trade.Buy(lot, _Symbol, 0.0, sl, tp, "rangeCombo_long");
   else
      ok = trade.Sell(lot, _Symbol, 0.0, sl, tp, "rangeCombo_short");

   if(ok || trade.ResultRetcode() == TRADE_RETCODE_PLACED ||
      trade.ResultRetcode() == TRADE_RETCODE_DONE)
   {
      g_d_open++;
      g_dir = want;
      g_entryPrice = price;
      g_stop = stop;
      g_target = target;
      g_entryBar = iBars(_Symbol, PERIOD_M15);
      g_ticket = (int)trade.ResultDeal();
      double dealt = trade.ResultPrice();
      if(dealt > 0.0) g_entryPrice = dealt;
      LogTrade("open", g_entryPrice);
      PrintFormat("[OPEN] %s lot=%.2f entry=%.2f sl=%.2f tp=%.2f",
                  (want == 1 ? "BUY" : "SELL"), lot, g_entryPrice, stop, target);
   }
   else
   {
      PrintFormat("[OPEN FAILED] retcode=%d %s",
                  trade.ResultRetcode(), trade.ResultRetcodeDescription());
   }
}

void ClosePosition(string reason)
{
   if(g_ticket == 0 && !PositionSelect(_Symbol)) return;
   bool ok = trade.PositionClose(_Symbol);
   if(ok)
   {
      PrintFormat("[CLOSE] reason=%s", reason);
      g_closeReason = reason;
   }
}

bool ManagePosition(double closePrice)
{
   if(!PositionSelect(_Symbol)) return false;

   int dir = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) ? 1 : -1;
   double entry = PositionGetDouble(POSITION_PRICE_OPEN);

   if(InpMaxHoldBars > 0 && g_entryBar > 0 &&
      (iBars(_Symbol, PERIOD_M15) - g_entryBar) >= InpMaxHoldBars)
   {
      g_d_timestop++;
      ClosePosition("time");
      return true;
   }

   if(!InpUseServerStops)
   {
      bool stopHit = (dir == 1) ? (closePrice <= g_stop) : (closePrice >= g_stop);
      bool tpHit   = (dir == 1) ? (closePrice >= g_target) : (closePrice <= g_target);
      if(stopHit) { ClosePosition("sl"); return true; }
      if(tpHit)   { ClosePosition("tp"); return true; }
   }

   if(InpAvoidOvernight)
   {
      MqlDateTime st;
      TimeToStruct(TimeCurrent(), st);
      if(st.hour >= InpServerCloseHour)
      {
         double rel = RangeRelSlope();
         bool strong = (MathAbs(rel) > InpStrongOvernight);
         bool inProfit = (dir == 1) ? (closePrice > entry) : (closePrice < entry);
         if(!(strong && inProfit))
         {
            ClosePosition("eod");
            return true;
         }
      }
   }
   return false;
}

//=================== main strategy ===================
void OnNewBar()
{
   g_d_newbar++;
   if(InpDumpBars)
      PrintFormat(
         "[BAR] time=%s o=%.3f h=%.3f l=%.3f c=%.3f atr=%.6f rsi=%.4f",
         TimeToString(iTime(_Symbol, PERIOD_M15, 1), TIME_DATE | TIME_SECONDS),
         iOpen(_Symbol, PERIOD_M15, 1),
         iHigh(_Symbol, PERIOD_M15, 1),
         iLow(_Symbol, PERIOD_M15, 1),
         iClose(_Symbol, PERIOD_M15, 1),
         GetSignalATR(),
         GetRSIVal(1)
      );
   if(InpHeartbeat)
   {
      MqlDateTime hb;
      TimeToStruct(iTime(_Symbol, PERIOD_M15, 1), hb);
      int sp = (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD);
      PrintFormat("[HB] %04d-%02d-%02d %02d:%02d newbar=%d spread=%d",
                  hb.year, hb.mon, hb.day, hb.hour, hb.min, g_d_newbar, sp);
   }

   double closePrice = iClose(_Symbol, PERIOD_M15, 1);
   if(PositionSelect(_Symbol))
   {
      ManagePosition(closePrice);
      return;
   }

   MqlDateTime sig;
   TimeToStruct(iTime(_Symbol, PERIOD_M15, 1), sig);
   if(sig.hour < EffectiveStartHour() || sig.hour >= InpTradeEndHour)
   {
      g_d_session++;
      SkipLog(StringFormat("session hour=%d", sig.hour));
      return;
   }

   if(!SpreadOK())
   {
      g_d_spread++;
      SkipLog(StringFormat("spread=%d>%d",
              (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD), InpMaxSpreadPoints));
      return;
   }

   double stop = 0.0, target = 0.0;
   int want = RangeSignal(stop, target);
   if(want == 0) return;

   if(g_lastEntrySide == want &&
      (iBars(_Symbol, PERIOD_M15) - g_lastEntryBar) < InpCooldownBars)
   {
      g_d_cool++;
      SkipLog(StringFormat("cooldown want=%d", want));
      return;
   }

   g_lastEntryBar = iBars(_Symbol, PERIOD_M15);
   g_lastEntrySide = want;
   g_d_attempt++;
   PrintFormat("[ATTEMPT] want=%d price=%.2f sl=%.2f tp=%.2f",
               want, closePrice, stop, target);
   OpenPosition(want, closePrice, stop, target);
}

int OnInit()
{
   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(20);
   trade.SetTypeFillingBySymbol(_Symbol);

   g_point = SymbolInfoDouble(_Symbol, SYMBOL_POINT);
   g_digits = (int)SymbolInfoInteger(_Symbol, SYMBOL_DIGITS);
   SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN, g_volMin);
   SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP, g_volStep);
   SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX, g_volMax);

   g_hATR = iATR(_Symbol, PERIOD_M15, InpAtrPeriod);
   g_hRSI = iRSI(_Symbol, PERIOD_M15, 14, PRICE_CLOSE);
   if(g_hATR == INVALID_HANDLE || g_hRSI == INVALID_HANDLE)
   {
      Print("ERROR: indicator handle creation failed");
      return INIT_FAILED;
   }
   if(_Period != PERIOD_M15)
      Print("WARNING: this EA is designed for XAUUSD M15");

   PrintFormat("BoxChannelRangeCombo initialized | lot=%.2f range=%d ER<%.2f width[%.2f,%.2f] atrMode=%s log=%s",
               NormalizeLot(InpFixedLot), InpRangeLookback, InpEfficiencyMax,
               InpRangeWidthMinAtr, InpRangeWidthMaxAtr,
               EnumToString(InpAtrMode), InpLogFile);

   if(InpLogTrades)
   {
      int h = FileOpen(InpLogFile,
                       FILE_CSV | FILE_WRITE | FILE_ANSI, ',');
      if(h != INVALID_HANDLE)
      {
         FileWrite(h, "time", "magic", "action", "side", "price", "stop", "target",
                   "lot", "profit", "swap", "commission", "reason");
         FileClose(h);
      }
   }
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   PrintFormat("[DIAG] newbar=%d regime=%d width=%d rsi=%d cool=%d spread=%d session=%d conflict=%d attempt=%d opened=%d timestop=%d",
               g_d_newbar, g_d_regime, g_d_width, g_d_rsi, g_d_cool,
               g_d_spread, g_d_session, g_d_conflict, g_d_attempt,
               g_d_open, g_d_timestop);
   if(g_hATR != INVALID_HANDLE) IndicatorRelease(g_hATR);
   if(g_hRSI != INVALID_HANDLE) IndicatorRelease(g_hRSI);
}

void OnTick()
{
   static datetime lastTick = 0;
   if(InpHeartbeat && TimeCurrent() - lastTick >= 60)
   {
      lastTick = TimeCurrent();
      PrintFormat("[TICK] alive spread=%d",
                  (int)SymbolInfoInteger(_Symbol, SYMBOL_SPREAD));
   }

   if(PositionSelect(_Symbol))
      g_positionSwap = PositionGetDouble(POSITION_SWAP);

   if(g_ticket != 0 && !PositionSelect(_Symbol))
   {
      LogClosedByHistory((ulong)g_ticket, g_closeReason);
      g_ticket = 0; g_dir = 0; g_entryPrice = 0.0;
      g_stop = 0.0; g_target = 0.0; g_entryBar = 0;
      g_closeReason = ""; g_positionSwap = 0.0;
   }

   datetime t = iTime(_Symbol, PERIOD_M15, 0);
   if(t == g_lastBarTime) return;
   g_lastBarTime = t;
   OnNewBar();
}

double OnTester()
{
   PrintFormat("[DIAG] newbar=%d regime=%d width=%d rsi=%d cool=%d spread=%d session=%d conflict=%d attempt=%d opened=%d timestop=%d",
               g_d_newbar, g_d_regime, g_d_width, g_d_rsi, g_d_cool,
               g_d_spread, g_d_session, g_d_conflict, g_d_attempt,
               g_d_open, g_d_timestop);
   return 0.0;
}
//+------------------------------------------------------------------+
