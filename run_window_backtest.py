#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
本地重跑 box_channel_optimized scalp 回测(可限定窗口), 生成同花顺风格交互图:
- 上栏: K线 + MA5/20/60/96 + 入场出场标记(出场按盈/亏分色, hover 显 P&L/R/原因)
- 中栏: 成交量 + 成交量MA
- 下栏: 权益曲线
- 顶部 OHLC/涨跌/权益信息条, 随十字线动态更新
- 鼠标悬停 tooltip + 十字虚线(横轴=时间, 纵轴=价位/量/权益)
- 图下方: 可滚动交易记录明细表(序号/方向/开平时间价/持仓/盈亏$/点/R/原因/止损价)

用法:
  python run_window_backtest.py [--from 2026-07-01] [--to 2026-08-02] [--cash 1000] [--atr-shift 1]
"""
import sys, json, argparse
sys.path.insert(0, r"D:\workbuddy\tushare\XAUUSD\xauusd策略")
import box_channel_optimized as M
import pandas as pd

HERE = r"D:\workbuddy\tushare\XAUUSD\xauusd策略"

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>BoxChannelScalp 回测 · __TITLE__</title>
<style>
  html,body{margin:0;height:100%;background:#0a0d14;color:#e8edf6;
    font-family:-apple-system,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif}
  #head{padding:8px 14px 4px;background:#0f1420;border-bottom:1px solid #1c2436}
  #head h1{font-size:16px;margin:0 0 6px;color:#cfe0ff}
  .bar{display:flex;gap:18px;align-items:center;font-size:13px;color:#9fb0c8;flex-wrap:wrap}
  .bar b{color:#e8edf6;font-weight:600}
  .sym{font-size:18px;font-weight:700;color:#fff}
  .price{font-size:24px;font-weight:700}
  .up{color:#ff4d4f} .down{color:#00b578}
  #chart{width:100%;height:52vh}
  #tradetable{height:30vh;overflow:auto;padding:0 14px;background:#0f1420;border-top:1px solid #1c2436}
  .tt{width:100%;border-collapse:collapse;font-size:12px;color:#c8d3e6}
  .tt th{position:sticky;top:0;background:#141b2b;color:#9fb0c8;padding:5px 7px;text-align:right;
    font-weight:600;border-bottom:1px solid #2a3550;white-space:nowrap;z-index:1}
  .tt td{padding:3px 7px;text-align:right;border-bottom:1px solid #121829;white-space:nowrap}
  .tt td:nth-child(2),.tt td:nth-child(3),.tt td:nth-child(5){text-align:left}
  .tt tr:hover td{background:#16203a}
  #foot{color:#5d6d85;font-size:11px;padding:5px 14px 10px}
  .sec-title{padding:6px 14px 2px;background:#0f1420;color:#9fb0c8;font-size:13px;font-weight:600}
</style>
</head>
<body>
<div id="head">
  <h1>BoxChannelScalp 回测 · <span style="color:#9fb0c8">__FROM__ → __TO__</span></h1>
  <div class="bar">
    <span class="sym">XAUUSD</span>
    <span class="price" id="h_price">--</span>
    <span id="h_change">--</span>
    <span>开<b id="h_o">--</b></span>
    <span>高<b id="h_h">--</b></span>
    <span>低<b id="h_l">--</b></span>
    <span>收<b id="h_c">--</b></span>
    <span>量<b id="h_vol">--</b></span>
    <span>权益<b id="h_eq">--</b></span>
    <span>MA20<b id="h_ma20">--</b></span>
    <span>MA60<b id="h_ma60">--</b></span>
    <span>MA96<b id="h_ma96">--</b></span>
  </div>
  <div class="bar" style="margin-top:4px;font-size:12px">
    <span>最终权益 <b style="color:#3ad29f">$__FINAL__</b></span>
    <span>收益 <b>__RET__%</b></span>
    <span>最大回撤 <b>__MAXDD__%</b></span>
    <span>笔数 <b>__TRADES__</b></span>
    <span>胜率 <b>__WIN__%</b></span>
    <span>PF <b>__PF__</b></span>
  </div>
</div>
<div id="chart"></div>
<div class="sec-title">交易记录（共 __TRADES__ 笔 · 盈红亏绿 · 点击表头可看明细）</div>
<div id="tradetable"></div>
<div id="foot">红K=涨 绿K=跌 ｜ 红三角=做多入场 绿三角=做空入场(均加大加白边) ｜ 出场点: 红=盈利 绿=亏损(hover 显 P&L/R/原因) ｜ <b>双击交易记录某行→图区缩放定位该笔</b> ｜ 中栏=成交量 下栏=权益 ｜ 底部可缩放</div>
<script>/*ECHARTS*/</script>
<script>
var D = /*DATA*/;

var chart = echarts.init(document.getElementById('chart'), null, {renderer:'canvas'});

function fmtTime(ms){
  var d=new Date(ms);var p=function(n){return (n<10?'0':'')+n;};
  return p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes());
}
var REASON={tp:'止盈',sl:'止损',trail:'追踪止损',eod:'隔夜强平',margin:'保证金',manual:'手动平'};

var maSeries = Object.keys(D.mas).map(function(name){
  var colors = {'MA5':'#ffffff','MA20':'#ffd700','MA60':'#9b59b6','MA96':'#3498db'};
  return {name:name, type:'line', data:D.mas[name], showSymbol:false,
    lineStyle:{width:1.1, color:colors[name] || '#9fb0c8'},
    xAxisIndex:0, yAxisIndex:0, z:2};
});

var volumeSeries = D.volume.map(function(d){return {value:[d[0],d[1]], itemStyle:{color:d[2]}};});

function opt(){
  return {
    backgroundColor:'#0a0d14',
    textStyle:{color:'#e8edf6'},
    animation:false,
    grid:[
      {left:56, right:72, top:56, height:'44%'},
      {left:56, right:72, top:'60%', height:'18%', bottom:''},
      {left:56, right:72, top:'81%', height:'14%', bottom:58}
    ],
    legend:{top:6, data:['XAUUSD','MA5','MA20','MA60','MA96','Vol','Equity','Entry Long','Entry Short','Exit Win','Exit Loss'],
      textStyle:{color:'#9fb0c8',fontSize:11}, itemWidth:14, itemHeight:8},
    tooltip:{
      trigger:'axis',
      axisPointer:{type:'cross', snap:false, link:{xAxisIndex:'all'},
        lineStyle:{type:'dashed', color:'#9fb0c8', width:1},
        crossStyle:{type:'dashed', color:'#9fb0c8', width:1},
        label:{backgroundColor:'#1a2233', borderColor:'#2a3550', borderWidth:1, color:'#e8edf6'}},
      backgroundColor:'rgba(15,20,32,0.96)', borderColor:'#2a3550', borderWidth:1,
      textStyle:{color:'#e8edf6', fontSize:12},
      formatter:function(ps){
        if(!ps||!ps.length) return '';
        var d = new Date(ps[0].axisValue);
        var p=function(n){return (n<10?'0':'')+n;};
        var s = d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+
                p(d.getHours())+':'+p(d.getMinutes());
        var lines=['<b>'+s+'</b>'];
        ps.forEach(function(x){
          var sn = x.seriesName;
          if(sn==='XAUUSD'){
            var k=x.data;
            var idx = ts2idx[k[0]];
            var prev = idx>0 ? D.ohlc[idx-1][2] : k[2];
            var chg = k[2]-prev;
            var pct = prev ? (chg/prev*100).toFixed(2)+'%' : '--';
            var cls = chg>=0 ? 'up':'down';
            lines.push('开 '+k[1].toFixed(2)+'  高 '+k[3].toFixed(2));
            lines.push('收 <b class="'+cls+'">'+k[2].toFixed(2)+'</b>  低 '+k[4].toFixed(2));
            lines.push('涨跌 <span class="'+cls+'">'+(chg>=0?'+':'')+chg.toFixed(2)+' ('+pct+')</span>');
          }else if(sn==='Vol'){
            lines.push('成交量 '+Number(x.data[1]).toFixed(0));
          }else if(sn==='Equity'){
            lines.push('权益 <b>$'+Number(x.data[1]).toFixed(2)+'</b>');
          }else if(sn.indexOf('MA')===0 || sn==='VolMA'){
            lines.push(sn+' '+Number(x.data[1]).toFixed(2));
          }else if(sn==='Entry Long'||sn==='Entry Short'){
            var sd=x.data;
            lines.push((sn==='Entry Long'?'做多入场':'做空入场')+' '+sd.value[1].toFixed(2));
            if(sd.stop!=null) lines.push('止损 '+Number(sd.stop).toFixed(2)+'  目标 '+Number(sd.target).toFixed(2));
          }else if(sn==='Exit Win'||sn==='Exit Loss'){
            var ed=x.data;
            lines.push((sn==='Exit Win'?'出场(盈)':'出场(亏)')+' '+ed.value[1].toFixed(2));
            if(ed.pnl!=null){
              lines.push('盈亏 <b class="'+(ed.pnl>=0?'up':'down')+'">'+(ed.pnl>=0?'+':'')+ed.pnl.toFixed(2)+
                '$ ('+(ed.pnl>=0?'+':'')+ed.pnl.toFixed(2)+'pt)</b>');
              lines.push('R '+(ed.r==null?'--':(ed.r>=0?'+':'')+ed.r.toFixed(2))+'  原因 '+(REASON[ed.reason]||ed.reason));
            }
          }else{
            lines.push(x.marker+sn+': '+Number(x.data[1]).toFixed(2));
          }
        });
        return lines.join('<br/>');
      }
    },
    xAxis:[
      {type:'time', gridIndex:0, axisLine:{lineStyle:{color:'#1c2436'}},
        axisLabel:{show:false}, splitLine:{show:false},
        axisPointer:{label:{show:false}}},
      {type:'time', gridIndex:1, axisLine:{lineStyle:{color:'#1c2436'}},
        axisLabel:{show:false}, splitLine:{show:false},
        axisPointer:{label:{show:false}}},
      {type:'time', gridIndex:2, axisLine:{lineStyle:{color:'#1c2436'}},
        axisLabel:{color:'#5d6d85', fontSize:11}, splitLine:{show:false},
        axisPointer:{label:{formatter:function(v){
          var d=new Date(v);var p=function(n){return (n<10?'0':'')+n;};
          return d.getFullYear()+'-'+p(d.getMonth()+1)+'-'+p(d.getDate())+' '+p(d.getHours())+':'+p(d.getMinutes());
        }}}}
    ],
    yAxis:[
      {type:'value', name:'Price', scale:true, gridIndex:0, position:'left',
        axisLine:{lineStyle:{color:'#5d6d85'}}, axisLabel:{color:'#9fb0c8', fontSize:11},
        splitLine:{lineStyle:{color:'#121829'}},
        axisPointer:{label:{formatter:function(v){return 'P '+Number(v).toFixed(2);}}}},
      {type:'value', name:'Vol', scale:true, gridIndex:1, position:'left',
        axisLine:{lineStyle:{color:'#5d6d85'}}, axisLabel:{color:'#9fb0c8', fontSize:10},
        splitLine:{lineStyle:{color:'#121829'}},
        axisPointer:{label:{formatter:function(v){return 'V '+Number(v).toFixed(0);}}}},
      {type:'value', name:'Equity $', scale:true, gridIndex:2, position:'left',
        axisLine:{lineStyle:{color:'#5d6d85'}}, axisLabel:{color:'#9fb0c8', fontSize:10},
        splitLine:{lineStyle:{color:'#121829'}},
        axisPointer:{label:{formatter:function(v){return 'E $'+Number(v).toFixed(0);}}}}
    ],
    dataZoom:[
      {type:'inside', xAxisIndex:[0,1,2]},
      {type:'slider', xAxisIndex:[0,1,2], bottom:8, height:20,
        borderColor:'#1c2436', textStyle:{color:'#9fb0c8'}, fillerColor:'rgba(255,77,79,0.12)'}
    ],
    series:[
      {name:'XAUUSD', type:'candlestick', data:D.ohlc, xAxisIndex:0, yAxisIndex:0, z:1,
        itemStyle:{color:'#ff4d4f', color0:'#00b578', borderColor:'#ff4d4f', borderColor0:'#00b578'}},
      {name:'Vol', type:'bar', data:volumeSeries, xAxisIndex:1, yAxisIndex:1, z:1},
      {name:'VolMA', type:'line', data:D.vol_ma, showSymbol:false, xAxisIndex:1, yAxisIndex:1, z:2,
        lineStyle:{width:1, color:'#ffd700'}},
      {name:'Equity', type:'line', data:D.equity, showSymbol:false, xAxisIndex:2, yAxisIndex:2, z:1,
        lineStyle:{width:1.3, color:'#00d4ff'}},
      {name:'Entry Long', type:'scatter',
        data:D.entries.filter(function(e){return e.side==='long';}).map(function(e){return {value:[e.t,e.p],stop:e.stop,target:e.target};}),
        symbol:'triangle', symbolSize:15, itemStyle:{color:'#ff4d4f', borderColor:'#ffffff', borderWidth:1.2}, xAxisIndex:0, yAxisIndex:0, z:6},
      {name:'Entry Short', type:'scatter',
        data:D.entries.filter(function(e){return e.side==='short';}).map(function(e){return {value:[e.t,e.p],stop:e.stop,target:e.target};}),
        symbol:'triangle', symbolRotate:180, symbolSize:15, itemStyle:{color:'#00b578', borderColor:'#ffffff', borderWidth:1.2}, xAxisIndex:0, yAxisIndex:0, z:6},
      {name:'Exit Win', type:'scatter',
        data:D.exits.filter(function(e){return e.pnl!=null && e.pnl>=0;}).map(function(e){return {value:[e.t,e.p],pnl:e.pnl,reason:e.reason,r:e.r};}),
        symbol:'circle', symbolSize:11, itemStyle:{color:'#ff4d4f', borderColor:'#ffffff', borderWidth:1.2}, xAxisIndex:0, yAxisIndex:0, z:6,
        label:{show:false}, emphasis:{label:{show:true,position:'top',fontSize:10,color:'#ff4d4f',
          formatter:function(p){return (p.data.pnl>=0?'+':'')+p.data.pnl.toFixed(2);}}}},
      {name:'Exit Loss', type:'scatter',
        data:D.exits.filter(function(e){return e.pnl!=null && e.pnl<0;}).map(function(e){return {value:[e.t,e.p],pnl:e.pnl,reason:e.reason,r:e.r};}),
        symbol:'circle', symbolSize:11, itemStyle:{color:'#00b578', borderColor:'#ffffff', borderWidth:1.2}, xAxisIndex:0, yAxisIndex:0, z:6,
        label:{show:false}, emphasis:{label:{show:true,position:'bottom',fontSize:10,color:'#00b578',
          formatter:function(p){return (p.data.pnl>=0?'+':'')+p.data.pnl.toFixed(2);}}}}
    ].concat(maSeries)
  };
}

var ts2idx = {};
D.ohlc.forEach(function(d,i){ ts2idx[d[0]] = i; });

function fmt(n, d){ if(n==null||isNaN(n)) return '--'; return Number(n).toFixed(d); }
function fmtInt(n){ if(n==null||isNaN(n)) return '--'; return Number(n).toFixed(0); }

function nearestIdx(ts){
  var a = D.ohlc.map(function(d){return d[0];});
  var lo=0, hi=a.length-1;
  while(lo<hi){ var mid=(lo+hi)>>1; if(a[mid]<ts) lo=mid+1; else hi=mid; }
  if(lo>0 && ts-a[lo-1] < a[lo]-ts) lo--;
  return lo;
}

function updateHeader(idx){
  var o = D.ohlc[idx];
  var prev = idx>0 ? D.ohlc[idx-1][2] : o[2];
  var chg = o[2]-prev;
  var pct = prev ? chg/prev*100 : 0;
  var up = chg>=0;
  var cls = up ? 'up':'down';
  var sign = up ? '+' : '';
  document.getElementById('h_price').className = 'price '+cls;
  document.getElementById('h_price').textContent = o[2].toFixed(2);
  document.getElementById('h_change').innerHTML = '<span class="'+cls+'">'+sign+chg.toFixed(2)+' ('+sign+pct.toFixed(2)+'%)</span>';
  document.getElementById('h_o').textContent = o[1].toFixed(2);
  document.getElementById('h_h').textContent = o[3].toFixed(2);
  document.getElementById('h_l').textContent = o[4].toFixed(2);
  document.getElementById('h_c').textContent = o[2].toFixed(2);
  document.getElementById('h_vol').textContent = fmtInt(D.volume[idx] ? D.volume[idx][1] : null);
  document.getElementById('h_eq').textContent = '$'+fmt(D.equity[idx] ? D.equity[idx][1] : null, 2);
  document.getElementById('h_ma20').textContent = fmt(D.mas['MA20'][idx] ? D.mas['MA20'][idx][1] : null, 2);
  document.getElementById('h_ma60').textContent = fmt(D.mas['MA60'][idx] ? D.mas['MA60'][idx][1] : null, 2);
  document.getElementById('h_ma96').textContent = fmt(D.mas['MA96'][idx] ? D.mas['MA96'][idx][1] : null, 2);
}

function resetHeader(){ updateHeader(D.ohlc.length-1); }

chart.setOption(opt());
chart.getZr().on('mousemove', function(params){
  var ts = chart.convertFromPixel({xAxisIndex:0}, [params.offsetX, params.offsetY]);
  if(ts==null || ts<D.ohlc[0][0] || ts>D.ohlc[D.ohlc.length-1][0]){ resetHeader(); return; }
  updateHeader(nearestIdx(ts));
});
chart.getZr().on('mouseout', resetHeader);
window.addEventListener('resize', function(){ chart.resize(); });
resetHeader();

// ---- 交易记录明细表 ----
(function(){
  var c = document.getElementById('tradetable');
  if(!c) return;
  var rows='';
  D.trades.forEach(function(t){
    var win = t.pnl>=0;
    var cls = win ? 'up':'down';
    var sideCls = t.side==='long' ? 'up':'down';
    var sideTxt = t.side==='long' ? '多':'空';
    var reason = REASON[t.reason] || t.reason || '平仓';
    var rtxt = (t.r==null)?'--':(t.r>=0?'+':'')+t.r.toFixed(2);
    var h = t.hold|0;
    var holdTxt = h>=60 ? (Math.floor(h/60)+'h'+((h%60)?(h%60)+'m':'')) : h+'m';
    rows += '<tr data-i="'+t.i+'" style="cursor:pointer" title="双击定位到图上该笔">'+
      '<td>'+(t.i+1)+'</td>'+
      '<td class="'+sideCls+'">'+sideTxt+'</td>'+
      '<td>'+fmtTime(t.entry_t)+'</td>'+
      '<td>'+t.entry_p.toFixed(2)+'</td>'+
      '<td>'+fmtTime(t.exit_t)+'</td>'+
      '<td>'+t.exit_p.toFixed(2)+'</td>'+
      '<td>'+holdTxt+'</td>'+
      '<td class="'+cls+'">'+(win?'+':'')+t.pnl.toFixed(2)+'</td>'+
      '<td class="'+cls+'">'+(win?'+':'')+t.pts.toFixed(2)+'</td>'+
      '<td class="'+cls+'">'+rtxt+'</td>'+
      '<td>'+reason+'</td>'+
      '<td>'+(t.stop==null?'--':t.stop.toFixed(2))+'</td>'+
      '</tr>';
  });
  c.innerHTML = '<table class="tt"><thead><tr>'+
    '<th>#</th><th>方向</th><th>开仓时间</th><th>开仓价</th><th>平仓时间</th><th>平仓价</th>'+
    '<th>持仓</th><th>盈亏$</th><th>盈亏点</th><th>R</th><th>原因</th><th>止损价</th>'+
    '</tr></thead><tbody>'+rows+'</tbody></table>';

  // 双击某行 -> 图区缩放定位到该笔(以开/平仓时间为中心 ±4h), 高亮十字光标 + 选中行
  function jumpToTrade(i){
    var t = D.trades[i]; if(!t) return;
    var lo = Math.min(t.entry_t, t.exit_t) - 4*3600*1000;
    var hi = Math.max(t.entry_t, t.exit_t) + 4*3600*1000;
    chart.dispatchAction({type:'dataZoom', startValue: lo, endValue: hi});
    var rowsAll = c.querySelectorAll('tbody tr');
    rowsAll.forEach(function(r){ r.style.background=''; });
    var cur = c.querySelector('tbody tr[data-i="'+i+'"]');
    if(cur){ cur.style.background='#22304f'; cur.scrollIntoView({block:'nearest'}); }
    document.getElementById('chart').scrollIntoView({block:'start'});
    var i0 = nearestIdx(t.entry_t);
    updateHeader(i0);
    chart.dispatchAction({type:'showTip', seriesIndex:0, dataIndex:i0});
  }
  c.querySelectorAll('tbody tr').forEach(function(tr){
    tr.addEventListener('dblclick', function(){ jumpToTrade(+tr.getAttribute('data-i')); });
  });
})();
</script>
</body>
</html>"""

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="2026-07-01")
    ap.add_argument("--to",   dest="end",   default="2026-08-02")
    ap.add_argument("--cash", type=float, default=1000.0)
    ap.add_argument("--atr-shift", type=int, default=1)
    ap.add_argument("--max-ma-dist", type=float, default=0.0,
                    help="max_ma_dist_atr: 0=关闭; >0 拒绝远离MA96超过K*ATR的入场")
    a = ap.parse_args()

    frm = pd.Timestamp(a.start)
    to  = pd.Timestamp(a.end)

    res = M.run_backtest(freq="15m",
                         extra_args={**M.PROFILES['scalp'], 'atr_shift': a.atr_shift,
                                     'max_ma_dist_atr': a.max_ma_dist},
                         cash=a.cash, fromdate=frm, todate=to, quiet=False)

    df15, _, _ = M.load_data("15m", frm, to)

    # K线数据 [ts_ms, open, close, low, high]
    ohlc = [[int(pd.Timestamp(ts).value // 10**6),
             round(float(o), 2), round(float(c), 2),
             round(float(l), 2), round(float(h), 2)]
            for ts, o, c, l, h in zip(df15.index, df15['open'], df15['close'],
                                     df15['low'], df15['high'])]

    # 均线(按收盘价)
    mas = {}
    for n in [5, 20, 60, 96]:
        s = df15['close'].rolling(window=n, min_periods=1).mean()
        mas['MA%d' % n] = [[int(pd.Timestamp(ts).value // 10**6), round(float(v), 2)]
                           for ts, v in s.items()]

    # 成交量与量均线, 带涨跌色(按收盘价)
    volume = []
    vol_ma = []
    vm = df15['volume'].rolling(window=5, min_periods=1).mean()
    for i, (ts, v, c, o) in enumerate(zip(df15.index, df15['volume'], df15['close'], df15['open'])):
        up = c >= o
        volume.append([int(pd.Timestamp(ts).value // 10**6), round(float(v), 2), '#ff4d4f' if up else '#00b578'])
        vol_ma.append([int(pd.Timestamp(ts).value // 10**6), round(float(vm.iloc[i]), 2)])

    # 权益曲线
    eq = [[int(pd.Timestamp(d).value // 10**6), round(float(v), 2)] for d, v in res['eq']]

    # 交易记录 (FIFO 配对, 每笔带止损/目标/盈亏点/R倍数/原因/持仓时长)
    # 注: 0.01 手 = 1oz, 故 $/点 = 1, 盈亏$ 用价格点近似(已含滑点/佣金的小幅差异)
    stack = []
    entries = []   # 图上入场标记
    exits = []     # 图上出场标记(含 pnl/reason)
    trades = []    # 交易明细表
    for t in res['trades']:
        ts = int(pd.Timestamp(t['dt']).value // 10**6)
        pr = round(float(t['price']), 2)
        if t['kind'] == 'open':
            rec = {'t': ts, 'p': pr, 'side': t['side'],
                   'stop': t.get('stop'), 'target': t.get('target'), 'r_mult': t.get('r_mult')}
            stack.append(rec)
            entries.append({'t': ts, 'p': pr, 'side': t['side'],
                            'stop': t.get('stop'), 'target': t.get('target')})
        else:
            o = stack.pop() if stack else None
            pnl = pts = risk = r = None
            if o:
                ds = 1 if o['side'] == 'long' else -1
                pts = round((pr - o['p']) * ds, 2)
                pnl = pts                      # 0.01 lot = 1oz -> $1/pt
                if o.get('stop') is not None:
                    risk = abs(o['p'] - o['stop'])
                    r = round(pts / risk, 2) if risk else None
            exits.append({'t': ts, 'p': pr, 'pnl': pnl, 'reason': t.get('reason'), 'side': t['side']})
            if o:
                trades.append({
                    'i': len(trades), 'side': o['side'],
                    'entry_t': o['t'], 'entry_p': round(o['p'], 2),
                    'exit_t': ts, 'exit_p': pr,
                    'pnl': pnl, 'pts': pts, 'r': r,
                    'risk': (round(risk, 2) if risk is not None else None),
                    'stop': (round(o['stop'], 2) if o.get('stop') is not None else None),
                    'target': (round(o['target'], 2) if o.get('target') is not None else None),
                    'reason': t.get('reason'),
                    'hold': int((ts - o['t']) // 60000),
                })

    data = {
        "ohlc": ohlc, "mas": mas, "volume": volume, "vol_ma": vol_ma,
        "equity": eq, "entries": entries, "exits": exits, "trades": trades,
        "meta": {
            "from": str(frm), "to": str(to),
            "final": round(res['final'], 2), "ret": round(res['total_ret']*100, 2),
            "maxdd": round(res['maxdd'], 2),
            "sharpe": (None if res['sharpe'] is None else round(res['sharpe'], 2)),
            "trades": res['closed'], "win": round(res['win_rate']*100, 1),
            "pf": round(res['pf'], 2)
        }
    }

    echarts = open(r"%s\echarts.min.js" % HERE, encoding="utf-8").read()
    html = (TEMPLATE
            .replace("/*ECHARTS*/", echarts)
            .replace("/*DATA*/", json.dumps(data, ensure_ascii=False))
            .replace("__TITLE__", "%s ~ %s" % (a.start, a.end))
            .replace("__FROM__", a.start).replace("__TO__", a.end)
            .replace("__FINAL__", "%.2f" % data['meta']['final'])
            .replace("__RET__", "%.2f" % data['meta']['ret'])
            .replace("__MAXDD__", "%.2f" % data['meta']['maxdd'])
            .replace("__SHARPE__", "%.2f" % (data['meta']['sharpe'] if data['meta']['sharpe'] is not None else float('nan')))
            .replace("__TRADES__", str(data['meta']['trades']))
            .replace("__WIN__", "%.1f" % data['meta']['win'])
            .replace("__PF__", "%.2f" % data['meta']['pf']))

    tag = ("_maxdist%.1f" % a.max_ma_dist) if a.max_ma_dist > 0 else ""
    out = r"%s\backtest_%s_%s%s.html" % (HERE, a.start.replace("-", ""), a.end.replace("-", ""), tag)
    open(out, "w", encoding="utf-8").write(html)
    print("WROTE", out)
    print("ohlc=%d mas=%s vol=%d equity=%d entries=%d exits=%d trades=%d" %
          (len(ohlc), list(mas.keys()), len(volume), len(eq), len(entries), len(exits), len(trades)))

if __name__ == "__main__":
    main()
