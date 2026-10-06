//+------------------------------------------------------------------+
//| ExportXAUUSD_M5.mq5                                              |
//| 一键批量导出 XAUUSD M5 历史(默认 2020-01-01 起) 到单个 CSV。    |
//| 用法: 编译后拖到图表(或双击 Script)运行一次即可。                |
//| 输出: 终端数据目录 MQL5\Files\XAUUSD_M5_2020+.csv                 |
//|       (File → Open Data Folder → MQL5\Files)                     |
//| 列:  DATE,TIME,OPEN,HIGH,LOW,CLOSE,VOL  (UTC+服务器偏移, 见说明)  |
//+------------------------------------------------------------------+
#property script_show_inputs
#property strict

input string          InpSymbol    = "XAUUSD";      // 品种
input ENUM_TIMEFRAMES InpPeriod    = PERIOD_M5;     // 周期
input int             InpStartYear = 2020;          // 起始年
input string          InpOutFile   = "XAUUSD_M5_2020+.csv";

// 月份加 m 个月(处理跨年/闰月)
datetime AddMonths(datetime t, int m)
{
   MqlDateTime s; TimeToStruct(t, s);
   s.year  += (s.month - 1 + m) / 12;
   s.month  = (s.month - 1 + m) % 12 + 1;
   int days[12] = {31,28,31,30,31,30,31,31,30,31,30,31};
   if(s.month == 2 && (s.year % 4 == 0 && (s.year % 100 != 0 || s.year % 400 == 0)))
      days[1] = 29;
   if(s.day > days[s.month - 1]) s.day = days[s.month - 1];
   return StructToTime(s);
}

void OnStart()
{
   string sym = InpSymbol;
   if(StringLen(sym) == 0) sym = _Symbol;

   // 确保品种在 Market Watch(否则 CopyRates 取不到)
   if(!SymbolSelect(sym, true))
      Print("⚠️ SymbolSelect 失败, 请先在 Market Watch 加入 ", sym);

   int dig = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(dig <= 0) dig = 2;

   // 经纪商实际能提供的最早历史 —— 关键提示
   datetime first_avail = (datetime)SeriesInfoInteger(sym, InpPeriod, SERIES_TERMINAL_FIRSTDATE);
   datetime want_start  = StringToTime(IntegerToString(InpStartYear) + ".01.01 00:00");
   if(first_avail > 0 && first_avail > want_start)
      PrintFormat("⚠️ 经纪商 %s M5 最早只到 %s, 2020 段取不到(需用 HistData 补 2020~%s)",
                  sym, TimeToString(first_avail, TIME_DATE),
                  TimeToString(AddMonths(first_avail, -1), TIME_DATE));

   datetime end = TimeCurrent();
   if(end < want_start) { Print("终止时间早于起始时间"); return; }

   int fh = FileOpen(InpOutFile, FILE_WRITE | FILE_ANSI);
   if(fh == INVALID_HANDLE) { Print("⛔ FileOpen 失败: ", GetLastError()); return; }
   FileWriteString(fh, "DATE,TIME,OPEN,HIGH,LOW,CLOSE,VOL\r\n");

   MqlRates rates[];
   datetime cur = want_start;
   int total = 0;
   int months = 0;

   while(cur < end)
   {
      datetime nxt = AddMonths(cur, 1);
      if(nxt > end) nxt = end;

      int copied = CopyRates(sym, InpPeriod, cur, nxt, rates);
      if(copied > 0)
      {
         for(int i = 0; i < copied; i++)
         {
            if(rates[i].time >= nxt) continue;   // 跳过边界重复 bar
            string line = StringFormat("%s,%s,%s,%s,%s,%s,%d\r\n",
                           TimeToString(rates[i].time, TIME_DATE),
                           TimeToString(rates[i].time, TIME_MINUTES),
                           DoubleToString(rates[i].open,  dig),
                           DoubleToString(rates[i].high,  dig),
                           DoubleToString(rates[i].low,   dig),
                           DoubleToString(rates[i].close, dig),
                           rates[i].tick_volume);
            FileWriteString(fh, line);
            total++;
         }
      }
      else
      {
         PrintFormat("⚠️ CopyRates 失败 %s..%s  err=%d",
                     TimeToString(cur, TIME_DATE), TimeToString(nxt, TIME_DATE), GetLastError());
      }
      cur = nxt;
      months++;
      if((months % 12) == 0)
         PrintFormat("...已处理到 %s, 累计 %d 根", TimeToString(cur, TIME_DATE), total);
      Sleep(30);   // 避免高频请求被限流
   }

   FileClose(fh);
   PrintFormat("✅ 导出完成: %d 根 → MQL5\\Files\\%s", total, InpOutFile);
   Print("   之后把该 CSV 放到 build_extended_m5.py 的 --ext-path, 跑拼接。");
}
//+------------------------------------------------------------------+
