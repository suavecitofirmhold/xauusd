# -*- coding: utf-8 -*-
"""P1 portfolio-layer experiment for the restructured SR+ML follow line.

The source ML project remains read-only.  This script replays the causal
follow_stop_limit trade list produced by sr_execution_lab.py and tests a small
pre-registered set of portfolio controls.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


OUT = os.path.dirname(os.path.abspath(__file__))
TRADES = os.path.join(OUT, "execution_trades.parquet")
EVENTS = r"D:\workbuddy\tushare\黄金代码优化\ml\artifacts\events.parquet"
INIT_EQUITY = 1000.0
BASE_RISK_PCT = 0.01
MARGIN_CAP = 5.0
DD_THETA = 0.06
DD_FACTOR = 0.5


CONFIGS = [
    {"name": "risk1_no_controls", "pos_limit": 999},
    {"name": "pos_cap1", "pos_limit": 1},
    {"name": "pos_cap2", "pos_limit": 2},
    {"name": "pos_cap3", "pos_limit": 3},
    {"name": "pos_cap3_zone_dedupe", "pos_limit": 3, "zone_dedupe": True},
    {"name": "pos_cap3_heat2", "pos_limit": 3, "heat_cap": 0.02},
    {
        "name": "pos_cap3_dd_throttle",
        "pos_limit": 3,
        "dd_throttle": True,
    },
    {
        "name": "full_controls",
        "pos_limit": 2,
        "heat_cap": 0.02,
        "margin_cap": True,
        "zone_dedupe": True,
        "dd_throttle": True,
    },
]


def load_candidates():
    trades = pd.read_parquet(TRADES)
    trades = trades[
        (trades["mode"] == "follow_stop_limit")
        & (trades["selection"] == "follow_ridge")
        & trades["exit_time"].notna()
    ].copy()
    events = pd.read_parquet(EVENTS).reset_index().rename(columns={"index": "event_idx"})
    trades = trades.merge(
        events[[
            "event_idx",
            "datetime_utc",
            "direction",
            "zone_bottom",
            "zone_top",
            "atr_at_entry",
        ]],
        on="event_idx",
        how="left",
        validate="one_to_one",
    )
    trades["entry_time"] = pd.to_datetime(trades["entry_time"])
    trades["exit_time"] = pd.to_datetime(trades["exit_time"])
    trades["risk_per_oz"] = 2.5 * trades["atr_at_entry"]
    trades["side"] = np.where(
        trades["direction"] < 0, 1, -1
    )
    trades["zone_key"] = trades["zone_bottom"].round(6).astype(str) + "|" + trades[
        "zone_top"
    ].round(6).astype(str)
    trades = trades.sort_values(
        ["entry_time", "event_idx"], kind="stable"
    ).reset_index(drop=True)
    return trades


def realized_metrics(equity_points):
    equity = pd.Series(
        [point["equity"] for point in equity_points],
        index=pd.DatetimeIndex([point["time"] for point in equity_points]),
    ).sort_index()
    equity = equity[~equity.index.duplicated(keep="last")]
    if len(equity) < 2:
        return {
            "final_equity": INIT_EQUITY,
            "total_return_pct": 0.0,
            "cagr_pct": 0.0,
            "maxDD_pct": 0.0,
            "sharpe_monthly": np.nan,
        }
    peak = equity.cummax()
    drawdown = (peak - equity) / peak
    monthly = equity.resample("ME").last().pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    cagr = (equity.iloc[-1] / equity.iloc[0]) ** (1.0 / years) - 1.0
    return {
        "final_equity": float(equity.iloc[-1]),
        "total_return_pct": float((equity.iloc[-1] / INIT_EQUITY - 1.0) * 100.0),
        "cagr_pct": float(cagr * 100.0),
        "maxDD_pct": float(drawdown.max() * 100.0),
        "sharpe_monthly": float(
            monthly.mean() / monthly.std(ddof=0) * np.sqrt(12.0)
        ) if len(monthly) > 1 and monthly.std(ddof=0) > 0 else np.nan,
    }


def replay(candidates, config):
    open_positions = []
    equity = INIT_EQUITY
    high_water = INIT_EQUITY
    equity_points = [{"time": candidates["entry_time"].iloc[0], "equity": equity}]
    trade_rows = []
    event_counts = {"accepted": 0, "skip_concurrency": 0, "skip_heat": 0,
                    "skip_margin": 0, "skip_zone": 0}

    for candidate in candidates.itertuples(index=False):
        due = [pos for pos in open_positions if pos["exit_time"] <= candidate.entry_time]
        if due:
            for pos in due:
                equity += pos["lots"] * pos["pnl_per_oz"]
                equity_points.append({"time": pos["exit_time"], "equity": equity})
            open_positions = [pos for pos in open_positions if pos not in due]
            high_water = max(high_water, equity)

        if len(open_positions) >= config["pos_limit"]:
            event_counts["skip_concurrency"] += 1
            continue
        if config.get("zone_dedupe") and any(
            pos["zone_key"] == candidate.zone_key for pos in open_positions
        ):
            event_counts["skip_zone"] += 1
            continue

        risk_scale = 1.0
        if config.get("dd_throttle"):
            current_dd = (high_water - equity) / high_water
            if current_dd >= DD_THETA:
                risk_scale = DD_FACTOR

        risk_amount = equity * BASE_RISK_PCT * risk_scale
        lots = risk_amount / max(candidate.risk_per_oz, 1e-9)
        open_heat = sum(pos["risk_amount"] / max(pos["equity_at_entry"], 1.0)
                        for pos in open_positions)
        if "heat_cap" in config and open_heat + BASE_RISK_PCT * risk_scale > config["heat_cap"]:
            event_counts["skip_heat"] += 1
            continue

        if config.get("margin_cap", False):
            open_notional = sum(pos["lots"] * pos["entry_price"] for pos in open_positions)
            new_notional = lots * candidate.entry_price
            if open_notional + new_notional > equity * MARGIN_CAP:
                event_counts["skip_margin"] += 1
                continue

        open_positions.append({
            "exit_time": candidate.exit_time,
            "lots": lots,
            "pnl_per_oz": candidate.pnl,
            "risk_amount": risk_amount,
            "equity_at_entry": equity,
            "zone_key": candidate.zone_key,
            "entry_price": candidate.entry_price,
        })
        event_counts["accepted"] += 1
        trade_rows.append({
            "event_idx": candidate.event_idx,
            "entry_time": candidate.entry_time,
            "exit_time": candidate.exit_time,
            "lots": lots,
            "risk_amount": risk_amount,
            "equity_at_entry": equity,
            "pnl": lots * candidate.pnl,
        })

    for pos in sorted(open_positions, key=lambda item: item["exit_time"]):
        equity += pos["lots"] * pos["pnl_per_oz"]
        equity_points.append({"time": pos["exit_time"], "equity": equity})

    stats = realized_metrics(equity_points)
    stats.update({
        "config": config["name"],
        "accepted": event_counts["accepted"],
        "skipped_concurrency": event_counts["skip_concurrency"],
        "skipped_zone": event_counts["skip_zone"],
        "skipped_heat": event_counts["skip_heat"],
        "skipped_margin": event_counts["skip_margin"],
        "n_candidate": len(candidates),
    })
    return stats, pd.DataFrame(trade_rows)


def main():
    print("=== SR+ML P1 portfolio controls ===")
    candidates = load_candidates()
    print(f"[data] follow_stop_limit candidates={len(candidates):,}")
    all_stats = []
    all_trades = {}
    for config in CONFIGS:
        stats, trades = replay(candidates, config)
        all_stats.append(stats)
        all_trades[config["name"]] = trades
        print(
            f"[{stats['config']:26s}] accepted={stats['accepted']:3d} "
            f"final=${stats['final_equity']:9.2f} ret={stats['total_return_pct']:7.1f}% "
            f"CAGR={stats['cagr_pct']:6.1f}% DD={stats['maxDD_pct']:5.1f}% "
            f"SharpeM={stats['sharpe_monthly']:5.2f}"
        )

    summary = pd.DataFrame(all_stats)
    columns = [
        "config", "n_candidate", "accepted", "final_equity", "total_return_pct",
        "cagr_pct", "maxDD_pct", "sharpe_monthly", "skipped_concurrency",
        "skipped_zone", "skipped_heat", "skipped_margin",
    ]
    summary = summary[columns]
    summary.to_csv(os.path.join(OUT, "portfolio_summary.csv"), index=False, encoding="utf-8-sig")
    for name, trades in all_trades.items():
        trades.to_parquet(os.path.join(OUT, f"portfolio_trades_{name}.parquet"), index=False)

    fig, ax = plt.subplots(figsize=(13, 6))
    for config in CONFIGS:
        trades = all_trades[config["name"]]
        if trades.empty:
            continue
        curve = (
            trades.sort_values("exit_time")
            .groupby("exit_time", as_index=False)["pnl"].sum()
        )
        curve["equity"] = INIT_EQUITY + curve["pnl"].cumsum()
        ax.plot(curve["exit_time"], curve["equity"], lw=1.1, label=config["name"])
    ax.axhline(INIT_EQUITY, color="#666", lw=0.8, ls="--")
    ax.set_title("SR+ML portfolio controls (follow_stop_limit)")
    ax.set_ylabel("Equity $")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "portfolio_equity.png"), dpi=140)
    plt.close(fig)

    baseline = summary.loc[summary["config"] == "risk1_no_controls"].iloc[0]
    best = summary.loc[summary["maxDD_pct"].idxmin()]
    report = [
        "# SR+ML P1 组合层重构实验",
        "",
        "## 口径",
        "",
        "- 输入为 P0 中因果 ridge top20% + `follow_stop_limit` 的逐笔结果。",
        "- 单笔风险预算固定为当前权益的 1%；仓位由单笔风险和事件 ATR 决定。",
        "- 风险暴露为开放仓位的风险预算合计；保证金上限为 5 倍权益名义敞口。",
        "- 重叠事件去重指同一支撑/阻力区域已有未平仓交易时跳过新事件。",
        "- 回撤节流使用预注册 θ=6%、f=0.5；当权益回撤达到 6% 后减半风险，直到创出新高。",
        "- 组合层按成交时间因果推进；平仓后在候选入场时间前结算，用于下一笔仓位决策。",
        "- 局限：这里主要用已实现权益计算回撤，不逐分钟标记未实现浮亏。",
        "",
        "## 预注册配置",
        "",
        summary.to_markdown(index=False),
        "",
        "## 判读",
        "",
        f"- 基线：{baseline['config']}，总收益 {baseline['total_return_pct']:.1f}%，"
        f"最大回撤 {baseline['maxDD_pct']:.1f}%。",
        f"- 最低回撤配置：{best['config']}，总收益 {best['total_return_pct']:.1f}%，"
        f"最大回撤 {best['maxDD_pct']:.1f}%。",
        "- 验收：组合控制应明显降低最大回撤，同时净利润下降不成比例；",
        "  若只降低收益而没有降低回撤，或净利润损失远大于回撤改善，则不采纳。",
    ]
    with open(os.path.join(OUT, "portfolio_report.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(report))
    print(f"[out] {os.path.join(OUT, 'portfolio_summary.csv')}")
    print(f"[out] {os.path.join(OUT, 'portfolio_report.md')}")


if __name__ == "__main__":
    main()
