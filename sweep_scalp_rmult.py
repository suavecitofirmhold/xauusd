# -*- coding: utf-8 -*-
"""
scalp 策略 r_mult 细扫描(1.5→3.0, 步长0.25; 固定 atr_mult=1.0)。
指标: 累计收益% / 胜率% / 年化Sharpe(资金曲线日收益) / 最大回撤% / PF(配对成交) / 平均R。
输出: scalp_rmult_sweep.csv + scalp_rmult_sweep.html(内联 echarts 折线图)。
"""
import os, sys, json
import pandas as pd
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from box_channel_optimized import run_backtest, PROFILES
from analyze_scalp_trades import daily_sharpe, build_record
from plot_scalp_trades import ECHARTS_JS

FD, TD = "2025-06-01", "2026-08-29"
OUT_CSV = os.path.join(HERE, "scalp_rmult_sweep.csv")
OUT_HTML = os.path.join(HERE, "scalp_rmult_sweep.html")


def main():
    base = dict(PROFILES["scalp"])  # 已含 atr_stop_mult=1.0, trailing=True
    rows = []
    for rm in [round(x * 0.25, 2) for x in range(6, 13)]:  # 1.5..3.0
        cfg = dict(base, r_mult=rm)
        m = run_backtest(extra_args=cfg, fromdate=FD, todate=TD, quiet=True)
        rec = build_record(m)
        sub = pd.DataFrame(rec)
        gp = sub[sub.pnl_usd > 0].pnl_usd.sum()
        gl = -sub[sub.pnl_usd < 0].pnl_usd.sum()
        pf = gp / gl if gl > 0 else float("nan")
        sh = daily_sharpe(m["eq"])
        rows.append(dict(r_mult=rm, ret=m["total_ret"] * 100, win=m["win_rate"] * 100,
                         sharpe=sh, dd=m["maxdd"], pf=pf, avg_r=sub.r_multiple.mean()))
        print(f"  r_mult={rm:.2f}  ret={rows[-1]['ret']:+.1f}%  win={rows[-1]['win']:.1f}%  "
              f"Sharpe={sh:.2f}  DD={rows[-1]['dd']:.1f}%  PF={pf:.2f}  avgR={rows[-1]['avg_r']:.3f}")

    df = pd.DataFrame(rows)
    df.to_csv(OUT_CSV, index=False, encoding="utf-8-sig")

    # 最优判定
    best_sh = df.loc[df.sharpe.idxmax()]
    best_ret = df.loc[df.ret.idxmax()]
    best_ra = df.loc[(df.ret / df.dd).idxmax()]  # 收益/回撤 风险调整
    print("\n== 最优 ==")
    print(f"  Sharpe 最优: r_mult={best_sh.r_mult:.2f} (Sharpe={best_sh.sharpe:.2f}, ret={best_sh.ret:+.1f}%, DD={best_sh.dd:.1f}%)")
    print(f"  收益最优:   r_mult={best_ret.r_mult:.2f} (ret={best_ret.ret:+.1f}%, Sharpe={best_ret.sharpe:.2f}, DD={best_ret.dd:.1f}%)")
    print(f"  收益/回撤最优: r_mult={best_ra.r_mult:.2f} (ret/DD={best_ra.ret/best_ra.dd:.2f})")

    # ---- 出图(内联 echarts) ----
    x = [round(v, 2) for v in df.r_mult.tolist()]
    ret = [round(v, 1) for v in df.ret.tolist()]
    sh = [round(v, 2) if v == v else None for v in df.sharpe.tolist()]
    dd = [round(v, 1) for v in df.dd.tolist()]
    opt_x = float(best_sh.r_mult)
    html = f"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>scalp r_mult 扫描</title><script>__ECHARTS_JS__</script>
<style>html,body{{margin:0;height:100%;background:#0d1117;color:#e6edf3;
font-family:-apple-system,"Segoe UI",Roboto,"PingFang SC",sans-serif;}}
#app{{display:flex;flex-direction:column;height:100vh;}}
header{{padding:10px 16px;background:#161b22;border-bottom:1px solid #2b3340;}}
.title{{font-size:15px;font-weight:600;color:#f0b90b;}}
.sub{{font-size:12px;color:#8b98a9;}}
#c{{flex:1;}}</style></head>
<body><div id="app"><header>
<span class="title">scalp · r_mult 细扫描 (atr_mult=1.0, 区间 {FD}~{TD})</span>
<span class="sub">｜ 竖线=Sharpe 最优 ｜ 左轴收益%/回撤% 右轴Sharpe</span>
</header><div id="c"></div></div>
<script>
var x={json.dumps(x)};
var ret={json.dumps(ret)};
var sh={json.dumps(sh)};
var dd={json.dumps(dd)};
var optX={opt_x};
var c=echarts.init(document.getElementById('c'));
c.setOption({{backgroundColor:'#0d1117',
tooltip:{{trigger:'axis',backgroundColor:'#161b22',borderColor:'#30363d',textStyle:{{color:'#e6edf3'}}}},
legend:{{data:['累计收益%','最大回撤%','Sharpe'],textStyle:{{color:'#e6edf3'}},top:8}},
grid:{{left:60,right:60,top:48,bottom:40}},
xAxis:{{type:'category',data:x,name:'r_mult',nameTextStyle:{{color:'#8b98a9'}},
axisLine:{{lineStyle:{{color:'#30363d'}}}},axisLabel:{{color:'#8b98a9'}}}},
yAxis:[{{type:'value',name:'%',axisLine:{{lineStyle:{{color:'#30363d'}}}},
axisLabel:{{color:'#8b98a9'}},splitLine:{{lineStyle:{{color:'#21262d'}}}}}},
{{type:'value',name:'Sharpe',axisLine:{{lineStyle:{{color:'#30363d'}}}},
axisLabel:{{color:'#8b98a9'}},splitLine:{{show:false}}}}],
series:[
{{name:'累计收益%',type:'line',data:ret,smooth:true,symbolSize:8,
lineStyle:{{color:'#26a69a',width:2}},itemStyle:{{color:'#26a69a'}},
markLine:{{symbol:'none',data:[{{xAxis:optX}}],lineStyle:{{color:'#f0b90b',type:'dashed'}},
label:{{formatter:'Sharpe最优 '+optX,color:'#f0b90b'}}}}}},
{{name:'最大回撤%',type:'line',data:dd,smooth:true,symbolSize:8,
lineStyle:{{color:'#ef5350',width:1.5,type:'dashed'}},itemStyle:{{color:'#ef5350'}}}},
{{name:'Sharpe',type:'line',yAxisIndex:1,data:sh,smooth:true,symbolSize:8,
lineStyle:{{color:'#58a6ff',width:2}},itemStyle:{{color:'#58a6ff'}}}}
]}});
window.addEventListener('resize',function(){{c.resize();}});
</script></body></html>"""
    html = html.replace("__ECHARTS_JS__", ECHARTS_JS)
    open(OUT_HTML, "w", encoding="utf-8").write(html)
    print(f"\n已生成: {OUT_CSV}\n已生成: {OUT_HTML}")


if __name__ == "__main__":
    main()
