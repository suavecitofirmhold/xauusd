import os
paths = {
    "proj": r"D:\workbuddy\tushare\XAUUSD\xauusd策略\mql5\BoxChannelScalp.mq5",
    "term": r"C:\Users\18628\AppData\Roaming\MetaQuotes\Terminal\7643C0B96C7AD5841307C9E1EB0B9252\MQL5\Experts\BoxChannelScalp.mq5",
}
for k, p in paths.items():
    if os.path.exists(p):
        b = open(p, "rb").read()
        txt = b.decode("utf-16-le", errors="replace")
        out = p + ".utf8.tmp"
        open(out, "w", encoding="utf-8").write(txt)
        print(k, "bytes", len(b), "lines", txt.count("\n"), "->", out)
    else:
        print(k, "MISSING", p)
