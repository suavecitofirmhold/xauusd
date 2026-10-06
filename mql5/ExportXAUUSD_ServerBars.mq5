//+------------------------------------------------------------------+
//| ExportXAUUSD_ServerBars.mq5                                      |
//| Export broker server-time bars with spread to CSV.               |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

input string          InpSymbol    = "XAUUSD";
input ENUM_TIMEFRAMES InpPeriod    = PERIOD_M15;
input string          InpStart     = "2023.09.13 00:00";
input string          InpEnd       = "2026.09.08 23:59";
input string          InpOutFile   = "XAUUSD_M15_server.csv";
input int             InpSleepMs   = 30;

datetime AddMonths(datetime t, int months)
{
   MqlDateTime s;
   TimeToStruct(t, s);
   int total = s.year * 12 + (s.mon - 1) + months;
   s.year = total / 12;
   s.mon = total % 12 + 1;
   int days[] = {31,28,31,30,31,30,31,31,30,31,30,31};
   int maxDay = days[s.mon - 1];
   if(s.mon == 2 && ((s.year % 4 == 0 && s.year % 100 != 0) || s.year % 400 == 0))
      maxDay = 29;
   if(s.day > maxDay) s.day = maxDay;
   return StructToTime(s);
}

void OnStart()
{
   string sym = InpSymbol;
   if(StringLen(sym) == 0) sym = _Symbol;
   if(!SymbolSelect(sym, true))
      Print("WARNING: SymbolSelect failed for ", sym);

   datetime start = StringToTime(InpStart);
   datetime end = StringToTime(InpEnd);
   if(start <= 0 || end <= start)
   {
      Print("ERROR: invalid start/end");
      return;
   }

   datetime firstAvailable = (datetime)SeriesInfoInteger(
      sym, InpPeriod, SERIES_TERMINAL_FIRSTDATE
   );
   PrintFormat("symbol=%s timeframe=%s firstAvailable=%s requested=%s..%s",
               sym, EnumToString(InpPeriod),
               TimeToString(firstAvailable, TIME_DATE | TIME_MINUTES),
               TimeToString(start, TIME_DATE | TIME_MINUTES),
               TimeToString(end, TIME_DATE | TIME_MINUTES));

   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(digits <= 0) digits = 2;

   int h = FileOpen(InpOutFile, FILE_WRITE | FILE_ANSI);
   if(h == INVALID_HANDLE)
   {
      Print("ERROR: FileOpen failed: ", GetLastError());
      return;
   }
   FileWriteString(
      h,
      "date,time,open,high,low,close,tickvol,realvol,spread\n"
   );

   MqlRates rates[];
   datetime cur = start;
   int total = 0;
   while(cur < end)
   {
      datetime next = AddMonths(cur, 1);
      if(next > end) next = end;
      int copied = CopyRates(sym, InpPeriod, cur, next, rates);
      if(copied > 0)
      {
         for(int i = 0; i < copied; i++)
         {
            if(rates[i].time < start || rates[i].time >= next) continue;
            string line = StringFormat(
               "%s,%s,%.*f,%.*f,%.*f,%.*f,%d,%d,%d\n",
               TimeToString(rates[i].time, TIME_DATE),
               TimeToString(rates[i].time, TIME_MINUTES),
               digits, rates[i].open,
               digits, rates[i].high,
               digits, rates[i].low,
               digits, rates[i].close,
               (int)rates[i].tick_volume,
               (int)rates[i].real_volume,
               (int)rates[i].spread
            );
            FileWriteString(h, line);
            total++;
         }
      }
      else
      {
         PrintFormat("CopyRates failed %s..%s err=%d",
                     TimeToString(cur, TIME_DATE),
                     TimeToString(next, TIME_DATE),
                     GetLastError());
      }
      cur = next;
      Sleep(InpSleepMs);
   }
   FileClose(h);
   PrintFormat("done: %d bars -> MQL5\\Files\\%s", total, InpOutFile);
}
//+------------------------------------------------------------------+
