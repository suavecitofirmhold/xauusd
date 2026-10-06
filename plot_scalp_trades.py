# -*- coding: utf-8 -*-
"""
生成 scalp(极致短线)买卖交互图 —— 参照 TMGM 回测仪表盘风格:
GitHub 暗色主题 + 指标条 + 价格蜡烛图(大箭头开仓/空心环平仓/盈亏色带) + 资金曲线。
用法: python plot_scalp_trades.py [--profile scalp] --fromdate 2025-09-01 --todate 2025-12-01
"""
import os, sys, json, argparse
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from box_channel_optimized import run_backtest, PROFILES, load_data

DEF_FROM, DEF_TO = "2025-09-01", "2025-12-01"

# 内联 echarts(离线可用): 优先用本地抽取好的 echarts.inline.js, 退回从 dashboard 抽
DASHBOARD = "/d/Data/stockdata/xauusd/clean/figs/dashboard/dashboard_standalone.html"


def load_echarts_js():
    cand = os.path.join(HERE, "echarts.inline.js")
    if os.path.exists(cand):
        try:
            return open(cand, "r", encoding="utf-8", errors="ignore").read()
        except Exception:
            pass
    try:
        src = open(DASHBOARD, "r", encoding="utf-8", errors="ignore").read()
        i = src.index('!function(t,e){"object"==typeof exports')
        j = src.index("</script>", i)
        return src[i:j]
    except Exception:
        return ""  # 抽不到则退回 CDN


ECHARTS_JS = load_echarts_js()

# 页面级错误捕获: 脚本再出错就把错误显示出来, 而不是静默空白
ERR_HANDLER_JS = """
window.addEventListener('error', function(e){
  var b=document.getElementById('errbox');
  if(!b){b=document.createElement('div');b.id='errbox';
    b.style.cssText='position:fixed;left:8px;bottom:8px;max-width:92%;background:#2d1417;color:#ff8a80;font:12px/1.5 monospace;padding:8px 10px;border:1px solid #ef5350;white-space:pre-wrap;z-index:999;border-radius:4px;';
    document.body.appendChild(b);}
  b.textContent='JS错误: '+(e.message||e.error||e);
});
"""



def _fmt_ms(ms):
    """ms 时间戳 -> UTC 字符串 YYYY-MM-DD HH:MM(原始数据本身是 UTC)。"""
    return pd.to_datetime(ms, unit="ms", utc=True).strftime("%Y-%m-%d %H:%M")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="scalp", choices=list(PROFILES.keys()))
    ap.add_argument("--fromdate", default=DEF_FROM)
    ap.add_argument("--todate", default=DEF_TO)
    ap.add_argument("--out", default=os.path.join(HERE, "scalp_trades_chart.html"))
    args = ap.parse_args()

    cfg = dict(PROFILES[args.profile])  # 副本, 避免污染全局 PROFILES
    # 与本次会话对齐基线一致: 用已收盘 bar 的 ATR (atr[-1]), 而非当前 bar (atr[0] 乐观口径)
    cfg["atr_shift"] = 1
    m = run_backtest(extra_args=cfg, fromdate=args.fromdate, todate=args.todate, quiet=True)
    df15, _, _ = load_data("15m", args.fromdate, args.todate)
    df15 = df15[df15.index <= args.todate]

    # 蜡烛
    candles, clow, chigh = [], {}, {}
    for ts, row in df15.iterrows():
        t = int(ts.timestamp() * 1000)
        candles.append([t, round(row["open"], 2), round(row["close"], 2),
                        round(row["low"], 2), round(row["high"], 2)])
        clow[t], chigh[t] = round(row["low"], 2), round(row["high"], 2)

    # 配对成交 → 开仓/平仓/盈亏
    oz = (cfg.get("contract_oz") or 100.0) * 0.01  # 0.01手=1oz
    pending = None
    long_open, short_open, exits, bands = [], [], [], []
    wins = 0
    for t in m["trades"]:
        dt = pd.Timestamp(t["dt"]).round("15min")
        if dt not in df15.index:
            dt = df15.index[df15.index.get_indexer([dt], method="nearest")[0]]
        x = int(dt.timestamp() * 1000)
        p = round(t["price"], 2)
        if t["kind"] == "open":
            pending = dict(side=t["side"], x=x, p=p, dt=_fmt_ms(x))
        else:
            if pending is None:
                continue
            sign = 1 if pending["side"] == "long" else -1
            pnl = sign * (p - pending["p"]) * oz
            if pnl > 0:
                wins += 1
            col = "rgba(38,166,154,.14)" if pnl > 0 else "rgba(239,83,80,.16)"
            bands.append([{"xAxis": pending["x"], "itemStyle": {"color": col}}, {"xAxis": x}])
            lbl = "多" if pending["side"] == "long" else "空"
            side = pending["side"]
            entry_px = pending["p"]
            opendt = pending["dt"]
            reason = t.get("reason")
            pnl_r = round(pnl, 2)
            if side == "long":
                long_open.append({"value": [pending["x"], round(clow[pending["x"]] - p * 0.0015, 2)],
                                  "lbl": lbl, "side": side, "kind": "开仓", "dt": opendt,
                                  "px": entry_px, "entry_px": entry_px, "pnl": pnl_r, "reason": reason})
            else:
                short_open.append({"value": [pending["x"], round(chigh[pending["x"]] + p * 0.0015, 2)],
                                   "lbl": lbl, "side": side, "kind": "开仓", "dt": opendt,
                                   "px": entry_px, "entry_px": entry_px, "pnl": pnl_r, "reason": reason})
            exits.append({"value": [x, p], "side": side, "kind": "平仓", "dt": _fmt_ms(x),
                          "px": p, "entry_px": entry_px, "pnl": pnl_r, "reason": reason})
            pending = None

    # 资金曲线
    eq = [[int(pd.Timestamp(d).timestamp() * 1000), round(v, 2)] for d, v in m["eq"]]

    ret = m["total_ret"] * 100
    ann = m["ann"] * 100
    sh = m["sharpe"]
    sh_txt = f"{sh:.2f}" if sh is not None else "nan"
    stats = [
        ("累计收益", f"{ret:+.1f}%", "pos" if ret >= 0 else "neg"),
        ("年化", f"{ann:+.1f}%", "pos" if ann >= 0 else "neg"),
        ("最大回撤", f"{m['maxdd']:.1f}%", "neg"),
        ("Sharpe", sh_txt, ""),
        ("成交", f"{m['closed']} 笔", ""),
        ("胜率", f"{m['win_rate']*100:.1f}%", ""),
        ("多开/空开", f"{len(long_open)}/{len(short_open)}", ""),
        ("盈利笔", f"{wins} 笔", "pos"),
        ("隔夜费", f"{m['swap_total']:+.1f}", "neg" if m['swap_total'] < 0 else "pos"),
    ]
    stat_html = "".join(
        f'<div class="stat"><span class="k">{k}</span><span class="v {c}">{v}</span></div>'
        for k, v, c in stats)

    cfg_txt = (f"box, r_mult={cfg['r_mult']}, atr_stop(×{cfg['atr_stop_mult']}), "
               f"trailing={cfg['trailing']}, 不过夜={cfg['avoid_overnight']}, "
               f"short_win={cfg['short_win']}, cooldown={cfg['cooldown_bars']}")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>XAU/USD · {args.profile} 买卖交互</title>
<script>__ECHARTS_JS__</script>
<script>
 if (typeof echarts === "undefined") {{
   document.write('<scr'+'ipt src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"><\\/scr'+'ipt>');
 }}
</script>
<style>
 :root{{--bg:#0d1117;--panel:#161b22;--panel2:#1c2230;--border:#2b3340;
   --text:#e6edf3;--muted:#8b98a9;--accent:#58a6ff;--long:#26a69a;--short:#ef5350;--gold:#f0b90b;}}
 *{{box-sizing:border-box;}}
 html,body{{margin:0;height:100%;background:var(--bg);color:var(--text);
   font-family:-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif;}}
 #app{{display:flex;flex-direction:column;height:100vh;}}
 header{{padding:10px 16px;background:var(--panel);border-bottom:1px solid var(--border);}}
 .titlebar{{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;}}
 .title{{font-size:15px;font-weight:600;color:var(--gold);}}
 .sub{{font-size:12px;color:var(--muted);}}
 #stats{{padding:8px 16px;background:var(--panel2);border-bottom:1px solid var(--border);
   display:flex;gap:18px;flex-wrap:wrap;font-size:12px;}}
 .stat{{display:flex;flex-direction:column;gap:2px;min-width:74px;}}
 .stat .k{{color:var(--muted);font-size:11px;}}
 .stat .v{{font-size:14px;font-weight:600;}}
 .pos{{color:var(--long);}} .neg{{color:var(--short);}}
 #charts{{flex:1;display:flex;flex-direction:column;min-height:0;}}
 .chart{{width:100%;}}
 #priceChart{{flex:1.7;min-height:340px;}}
 #retChart{{flex:1;min-height:200px;border-top:1px solid var(--border);}}
 .legend{{display:flex;gap:16px;padding:6px 16px;font-size:12px;color:var(--muted);
   background:var(--panel2);border-bottom:1px solid var(--border);}}
 .legend i{{display:inline-block;width:12px;height:12px;margin-right:5px;vertical-align:middle;border-radius:3px;}}
 .lg-long{{background:var(--long);}} .lg-short{{background:var(--short);}}
 .lg-exit{{background:transparent;border:2px solid #ffa726;}}
 .lg-band-up{{background:rgba(38,166,154,.5);}} .lg-band-dn{{background:rgba(239,83,80,.5);}}
</style>
</head>
<body>
<div id="app">
 <header>
   <div class="titlebar">
     <span class="title">XAU/USD · {args.profile} 极致短线 买卖交互</span>
     <span class="sub">区间 {args.fromdate} ~ {args.todate} ｜ 配置: {cfg_txt}</span>
   </div>
 </header>
 <div id="stats">{stat_html}</div>
 <div class="legend">
   <span><i class="lg-long"></i>多开(▲下)</span>
   <span><i class="lg-short"></i>空开(▼上)</span>
   <span><i class="lg-exit"></i>平仓</span>
   <span><i class="lg-band-up"></i>盈利持仓</span>
   <span><i class="lg-band-dn"></i>亏损持仓</span>
 </div>
 <div id="charts">
   <div id="priceChart" class="chart"></div>
   <div id="retChart" class="chart"></div>
 </div>
</div>
<script>
__ERR_HANDLER__
var candles={json.dumps(candles)};
var longOpen={json.dumps(long_open)};
var shortOpen={json.dumps(short_open)};
var exits={json.dumps(exits)};
var bands={json.dumps(bands)};
var eq={json.dumps(eq)};
var pc=echarts.init(document.getElementById('priceChart'));
var rc=echarts.init(document.getElementById('retChart'));
function fmtUTC(ms){{
  var dt=new Date(ms);
  function pad(n){{return (n<10?'0':'')+n;}}
  return dt.getUTCFullYear()+'-'+pad(dt.getUTCMonth()+1)+'-'+pad(dt.getUTCDate())+' '
        +pad(dt.getUTCHours())+':'+pad(dt.getUTCMinutes());
}}
function mkTip(p){{
  var d=p.data;
  if(!d) return '';
  if(Array.isArray(d)){{ // 蜡烛 K 线
    var o=d[1],c=d[2],l=d[3],h=d[4];
    return fmtUTC(d[0])+'<br/>开 '+o+' / 收 '+c+'<br/>高 '+h+' / 低 '+l;
  }}
  if(d.px===undefined) return '';
  var dir = d.side==='long'?'多':'空';
  var act = d.kind==='开仓'?'开仓':'平仓';
  var s = act+' · '+dir+'<br/>时间 '+d.dt+' (UTC)';
  if(act==='开仓'){{
    s += '<br/>开仓价 '+d.px;
  }} else {{
    s += '<br/>平仓价 '+d.px;
    s += '<br/>开仓价 '+d.entry_px;
  }}
  s += '<br/>盈亏 '+(d.pnl>=0?'+':'')+d.pnl+' USD';
  s += '<br/>原因 '+(d.reason||'-');
  return s;
}}
pc.setOption({{
  backgroundColor:'#0d1117',
  tooltip:{{trigger:'item',backgroundColor:'#161b22',borderColor:'#30363d',
    textStyle:{{color:'#e6edf3'}},formatter:mkTip}},
  grid:{{left:62,right:22,top:16,bottom:64}},
  xAxis:{{type:'time',axisLine:{{lineStyle:{{color:'#30363d'}}}},
    axisLabel:{{color:'#8b98a9'}},splitLine:{{show:false}}}},
  yAxis:{{scale:true,axisLine:{{lineStyle:{{color:'#30363d'}}}},
    axisLabel:{{color:'#8b98a9'}},splitLine:{{lineStyle:{{color:'#21262d'}}}}}},
  dataZoom:[{{type:'inside',xAxisIndex:0}},{{type:'slider',xAxisIndex:0,bottom:14,
    height:20,textStyle:{{color:'#8b98a9'}},borderColor:'#30363d'}}],
  series:[
    {{type:'candlestick',name:'K',data:candles,
      itemStyle:{{color:'#26a69a',color0:'#ef5350',borderColor:'#26a69a',borderColor0:'#ef5350'}},
      encode:{{x:0,y:[1,2,3,4]}},
      markArea:{{silent:true,data:bands}}}},
    {{type:'scatter',name:'多开',data:longOpen,symbol:'triangle',symbolSize:20,
      symbolOffset:[0,16],itemStyle:{{color:'#26a69a'}},z:6,
      label:{{show:true,formatter:function(p){{return p.data.lbl;}},position:'bottom',
        color:'#26a69a',fontSize:11,fontWeight:'bold'}}}},
    {{type:'scatter',name:'空开',data:shortOpen,symbol:'triangle',symbolSize:20,
      symbolRotate:180,symbolOffset:[0,-16],itemStyle:{{color:'#ef5350'}},z:6,
      label:{{show:true,formatter:function(p){{return p.data.lbl;}},position:'top',
        color:'#ef5350',fontSize:11,fontWeight:'bold'}}}},
    {{type:'scatter',name:'平仓',data:exits,symbol:'circle',symbolSize:15,
      itemStyle:{{color:'transparent',borderColor:'#ffa726',borderWidth:2.5}},z:5}}
  ]
}});
rc.setOption({{
  backgroundColor:'#0d1117',
  tooltip:{{trigger:'axis',backgroundColor:'#161b22',borderColor:'#30363d',
    textStyle:{{color:'#e6edf3'}}}},
  grid:{{left:62,right:22,top:14,bottom:28}},
  xAxis:{{type:'time',axisLine:{{lineStyle:{{color:'#30363d'}}}},
    axisLabel:{{color:'#8b98a9'}},splitLine:{{show:false}}}},
  yAxis:{{scale:true,axisLine:{{lineStyle:{{color:'#30363d'}}}},
    axisLabel:{{color:'#8b98a9',formatter:function(v){{return v.toFixed(0);}}}},
    splitLine:{{lineStyle:{{color:'#21262d'}}}}}},
  series:[{{type:'line',data:eq,showSymbol:false,
    lineStyle:{{color:'#58a6ff',width:1.5}},
    areaStyle:{{color:new echarts.graphic.LinearGradient(0,0,0,1,
      [{{offset:0,color:'rgba(88,166,255,.25)'}},{{offset:1,color:'rgba(88,166,255,0)'}}])}}}}]
}});
window.addEventListener('resize',function(){{pc.resize();rc.resize();}});
</script>
</body></html>"""
    html = html.replace("__ECHARTS_JS__", ECHARTS_JS)
    html = html.replace("__ERR_HANDLER__", ERR_HANDLER_JS)
    open(args.out, "w", encoding="utf-8").write(html)
    print(f"已生成: {args.out}")
    print(f"  蜡烛 {len(candles)} 根 | 多开 {len(long_open)} / 空开 {len(short_open)} / 平仓 {len(exits)} | 盈利 {wins} 笔")


if __name__ == "__main__":
    main()
