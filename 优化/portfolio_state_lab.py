# -*- coding: utf-8 -*-
"""Prospective portfolio-state validation for the SR follow stop-limit line."""

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


FIXED_CONFIGS = [
    {"name": "no_controls", "pos_limit": 999},
    {"name": "pos1", "pos_limit": 1},
    {"name": "pos3_dedupe", "pos_limit": 3, "zone_dedupe": True},
    {
        "name": "pos3_dedupe_heat2",
        "pos_limit": 3,
        "zone_dedupe": True,
        "heat_cap": 0.02,
    },
    {
        "name": "pos3_dedupe_heat15",
        "pos_limit": 3,
        "zone_dedupe": True,
        "heat_cap": 0.015,
    },
    {
        "name": "pos3_dedupe_dd6",
        "pos_limit": 3,
        "zone_dedupe": True,
        "dd_throttle": True,
        "dd_theta": 0.06,
        "dd_factor": 0.5,
    },
    {
        "name": "full_controls",
        "pos_limit": 2,
        "zone_dedupe": True,
        "heat_cap": 0.02,
        "margin_cap": True,
        "dd_throttle": True,
        "dd_theta": 0.06,
        "dd_factor": 0.5,
    },
]


WALK_CONFIGS = [
    {"name": "pos1_dedupe", "pos_limit": 1, "zone_dedupe": True},
    {"name": "pos2_dedupe", "pos_limit": 2, "zone_dedupe": True},
    {"name": "pos3_dedupe", "pos_limit": 3, "zone_dedupe": True},
    {
        "name": "pos2_dedupe_heat15",
        "pos_limit": 2,
        "zone_dedupe": True,
        "heat_cap": 0.015,
    },
    {
        "name": "pos2_dedupe_heat20",
        "pos_limit": 2,
        "zone_dedupe": True,
        "heat_cap": 0.020,
    },
    {
        "name": "pos3_dedupe_heat20",
        "pos_limit": 3,
        "zone_dedupe": True,
        "heat_cap": 0.020,
    },
    {
        "name": "pos2_dedupe_dd6",
        "pos_limit": 2,
        "zone_dedupe": True,
        "dd_throttle": True,
        "dd_theta": 0.06,
        "dd_factor": 0.5,
    },
    {
        "name": "pos3_dedupe_dd6",
        "pos_limit": 3,
        "zone_dedupe": True,
        "dd_throttle": True,
        "dd_theta": 0.06,
        "dd_factor": 0.5,
    },
    {
        "name": "pos2_full",
        "pos_limit": 2,
        "zone_dedupe": True,
        "heat_cap": 0.020,
        "margin_cap": True,
        "dd_throttle": True,
        "dd_theta": 0.06,
        "dd_factor": 0.5,
    },
    {
        "name": "pos3_full",
        "pos_limit": 3,
        "zone_dedupe": True,
        "heat_cap": 0.020,
        "margin_cap": True,
        "dd_throttle": True,
        "dd_theta": 0.06,
        "dd_factor": 0.5,
    },
]


def load_candidates():
    trades = pd.read_parquet(TRADES)
    trades = trades[
        (trades["mode"] == "follow_stop_limit")
        & (trades["selection"] == "follow_ridge")
        & trades["exit_time"].notna()
    ].copy()
    events = pd.read_parquet(EVENTS).reset_index().rename(
        columns={"index": "event_idx"}
    )
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
    trades["side"] = np.where(trades["direction"] < 0, 1, -1)
    trades["zone_key"] = (
        trades["zone_bottom"].round(6).astype(str)
        + "|"
        + trades["zone_top"].round(6).astype(str)
    )
    trades = trades.sort_values(
        ["entry_time", "event_idx"], kind="stable"
    ).reset_index(drop=True)
    return trades


def interval_metrics(equity_points, start=None, end=None):
    points = pd.DataFrame(equity_points).sort_values("time")
    points["time"] = pd.to_datetime(points["time"])
    points = points.drop_duplicates("time", keep="last")
    if start is None:
        start = points["time"].min()
    if end is None:
        end = points["time"].max()
    before = points[points["time"] <= start]
    start_equity = float(before["equity"].iloc[-1]) if len(before) else INIT_EQUITY
    piece = points[(points["time"] > start) & (points["time"] <= end)].copy()
    if piece.empty:
        return {
            "start_equity": start_equity,
            "end_equity": start_equity,
            "return_pct": 0.0,
            "cagr_pct": 0.0,
            "maxDD_pct": 0.0,
            "sharpe_monthly": np.nan,
        }
    values = np.r_[start_equity, piece["equity"].to_numpy(dtype=float)]
    peak = np.maximum.accumulate(values)
    drawdown = (peak - values) / peak
    elapsed_days = max((piece["time"].iloc[-1] - start).days, 1)
    years = elapsed_days / 365.25
    total_return = values[-1] / values[0] - 1.0
    cagr = (1.0 + total_return) ** (1.0 / years) - 1.0
    monthly = piece.set_index("time")["equity"].resample("ME").last()
    monthly_returns = monthly.pct_change().dropna()
    monthly_sharpe = (
        float(monthly_returns.mean() / monthly_returns.std(ddof=0) * np.sqrt(12.0))
        if len(monthly_returns) > 1 and monthly_returns.std(ddof=0) > 0
        else np.nan
    )
    return {
        "start_equity": start_equity,
        "end_equity": float(values[-1]),
        "return_pct": float(total_return * 100.0),
        "cagr_pct": float(cagr * 100.0),
        "maxDD_pct": float(drawdown.max() * 100.0),
        "sharpe_monthly": monthly_sharpe,
    }


def replay(candidates, config):
    open_positions = []
    equity = INIT_EQUITY
    high_water = INIT_EQUITY
    equity_points = [{
        "time": candidates["entry_time"].iloc[0],
        "equity": equity,
    }]
    trade_rows = []
    counts = {
        "accepted": 0,
        "skip_concurrency": 0,
        "skip_heat": 0,
        "skip_margin": 0,
        "skip_zone": 0,
    }

    for candidate in candidates.itertuples(index=False):
        due = [pos for pos in open_positions if pos["exit_time"] <= candidate.entry_time]
        if due:
            for pos in due:
                equity += pos["lots"] * pos["pnl_per_oz"]
                equity_points.append({"time": pos["exit_time"], "equity": equity})
            open_positions = [pos for pos in open_positions if pos not in due]
            high_water = max(high_water, equity)

        if len(open_positions) >= config["pos_limit"]:
            counts["skip_concurrency"] += 1
            continue
        if config.get("zone_dedupe") and any(
            pos["zone_key"] == candidate.zone_key for pos in open_positions
        ):
            counts["skip_zone"] += 1
            continue

        risk_scale = 1.0
        if config.get("dd_throttle"):
            current_dd = (high_water - equity) / high_water
            if current_dd >= config["dd_theta"]:
                risk_scale = config["dd_factor"]

        risk_amount = equity * BASE_RISK_PCT * risk_scale
        lots = risk_amount / max(candidate.risk_per_oz, 1e-9)
        open_heat = sum(
            pos["risk_amount"] / max(pos["equity_at_entry"], 1.0)
            for pos in open_positions
        )
        if (
            "heat_cap" in config
            and open_heat + BASE_RISK_PCT * risk_scale > config["heat_cap"]
        ):
            counts["skip_heat"] += 1
            continue

        if config.get("margin_cap", False):
            open_notional = sum(
                pos["lots"] * pos["entry_price"] for pos in open_positions
            )
            new_notional = lots * candidate.entry_price
            if open_notional + new_notional > equity * MARGIN_CAP:
                counts["skip_margin"] += 1
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
        counts["accepted"] += 1
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

    equity_table = pd.DataFrame(equity_points).sort_values("time")
    stats = interval_metrics(equity_table)
    stats.update({
        "config": config["name"],
        "accepted": counts["accepted"],
        "skipped_concurrency": counts["skip_concurrency"],
        "skipped_zone": counts["skip_zone"],
        "skipped_heat": counts["skip_heat"],
        "skipped_margin": counts["skip_margin"],
        "n_candidate": len(candidates),
    })
    return stats, pd.DataFrame(trade_rows), equity_table


def yearly_boundaries(candidates, first_test_year):
    years = pd.to_datetime(candidates["entry_time"]).dt.year
    boundaries = [
        pd.Timestamp(f"{year}-01-01")
        for year in range(first_test_year, int(years.max()) + 1)
    ]
    boundaries.append(
        pd.to_datetime(candidates["entry_time"].max()) + pd.Timedelta(days=1)
    )
    return boundaries


def walk_forward(candidates, config_results, boundaries):
    rows = []
    usable = boundaries
    for test_idx, test_start in enumerate(usable[:-1], start=1):
        test_end = usable[test_idx]
        train_end = test_start
        train_scores = []
        for result in config_results:
            points = result["equity_points"]
            stats = interval_metrics(points, start=None, end=train_end)
            trades = result["trades"]
            accepted = int(
                (pd.to_datetime(trades["entry_time"]) < train_end).sum()
            ) if not trades.empty else 0
            denominator = max(stats["maxDD_pct"], 1.0)
            score = stats["return_pct"] / denominator
            if accepted < 20:
                score = -np.inf
            train_scores.append((score, result["config"]["name"]))
        max_score = max(score for score, _ in train_scores)
        if not np.isfinite(max_score):
            selected_name = "pos3_full"
        else:
            selected_name = next(
                name for score, name in reversed(train_scores) if score == max_score
            )
        selected = next(
            result for result in config_results
            if result["config"]["name"] == selected_name
        )
        test_stats = interval_metrics(
            selected["equity_points"], start=test_start, end=test_end
        )
        rows.append({
            "train_end": train_end,
            "test_start": test_start,
            "test_end": test_end,
            "selected_config": selected_name,
            "train_score": max_score,
            **test_stats,
        })
    return pd.DataFrame(rows)


def sensitivity(candidates):
    base_config = {
        "pos_limit": 3,
        "zone_dedupe": True,
    }
    rows = []
    for theta in (0.04, 0.06, 0.08, 0.10, 0.12):
        for factor in (0.25, 0.50, 0.75):
            config = {
                **base_config,
                "name": f"theta{theta:.2f}_factor{factor:.2f}",
                "dd_throttle": True,
                "dd_theta": theta,
                "dd_factor": factor,
            }
            stats, _, _ = replay(candidates, config)
            rows.append({
                "theta": theta,
                "factor": factor,
                "return_pct": stats["return_pct"],
                "cagr_pct": stats["cagr_pct"],
                "maxDD_pct": stats["maxDD_pct"],
                "sharpe_monthly": stats["sharpe_monthly"],
                "accepted": stats["accepted"],
            })
    return pd.DataFrame(rows)


def main():
    print("=== SR+ML portfolio-state prospective validation ===")
    candidates = load_candidates()
    print(f"[data] candidates={len(candidates):,}")

    fixed_stats = []
    fixed_trades = {}
    fixed_points = {}
    for config in FIXED_CONFIGS:
        stats, trades, points = replay(candidates, config)
        fixed_stats.append(stats)
        fixed_trades[config["name"]] = trades
        fixed_points[config["name"]] = points
        print(
            f"[fixed:{config['name']:20s}] accepted={stats['accepted']:3d} "
            f"ret={stats['return_pct']:7.1f}% DD={stats['maxDD_pct']:5.1f}% "
            f"SharpeM={stats['sharpe_monthly']:5.2f}"
        )

    boundaries = yearly_boundaries(candidates, 2023)
    split_rows = []
    edges = [None] + boundaries
    for config_name, points in fixed_points.items():
        for start, end in zip(edges[:-1], edges[1:]):
            stats = interval_metrics(points, start=start, end=end)
            split_rows.append({
                "config": config_name,
                "period_start": start,
                "period_end": end,
                **stats,
            })
    split_table = pd.DataFrame(split_rows)

    walk_results = []
    for config in WALK_CONFIGS:
        _, trades, points = replay(candidates, config)
        walk_results.append({
            "config": config,
            "equity_points": points,
            "trades": trades,
        })
    walk_table = walk_forward(candidates, walk_results, boundaries)

    sens_table = sensitivity(candidates)

    pd.DataFrame(fixed_stats).to_csv(
        os.path.join(OUT, "portfolio_state_summary.csv"),
        index=False,
        encoding="utf-8-sig",
    )
    split_table.to_csv(
        os.path.join(OUT, "portfolio_state_splits.csv"),
        index=False,
        encoding="utf-8-sig",
    )
    walk_table.to_csv(
        os.path.join(OUT, "portfolio_state_walk_forward.csv"),
        index=False,
        encoding="utf-8-sig",
    )
    sens_table.to_csv(
        os.path.join(OUT, "portfolio_state_sensitivity.csv"),
        index=False,
        encoding="utf-8-sig",
    )
    for name, trades in fixed_trades.items():
        trades.to_parquet(
            os.path.join(OUT, f"portfolio_state_trades_{name}.parquet"),
            index=False,
        )

    fig, ax = plt.subplots(figsize=(13, 6))
    for name, points in fixed_points.items():
        points = points.sort_values("time")
        ax.plot(points["time"], points["equity"], lw=1.1, label=name)
    ax.axhline(INIT_EQUITY, color="#666", lw=0.8, ls="--")
    for boundary in boundaries:
        ax.axvline(boundary, color="#999", lw=0.7, ls=":")
    ax.set_title("Portfolio-state prospective validation")
    ax.set_ylabel("Equity $")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "portfolio_state_equity.png"), dpi=140)
    plt.close(fig)

    fixed_summary = pd.DataFrame(fixed_stats)
    baseline = fixed_summary[fixed_summary["config"] == "no_controls"].iloc[0]
    accepted = fixed_summary[fixed_summary["config"] != "no_controls"]
    dd_improvers = accepted[
        accepted["maxDD_pct"] < baseline["maxDD_pct"] * 0.80
    ].copy()
    report = [
        "# SR+ML 组合状态机前瞻验证",
        "",
        "## 口径",
        "",
        "- 输入是 P0 因果 `ridge top20% + follow_stop_limit` 的 911 笔候选成交。",
        "- 状态机只控制仓位：并发上限、同区域去重、风险热、保证金上限、回撤节流。",
        "- 所有决策按候选入场时间因果推进；评估分段时状态连续，不重置权益。",
        "- 前瞻选择从 2023 年开始，每年只用截止前权益表现选择下一次参数，不允许用未来收益调参。",
        "- 回撤节流敏感性只测试 θ=4/6/8/10/12% 和 f=0.25/0.50/0.75，避免继续单点挖掘。",
        "",
        "## 固定控制",
        "",
        fixed_summary.to_markdown(index=False),
        "",
        "## 分段表现",
        "",
        split_table.to_markdown(index=False),
        "",
        "## 前瞻选择",
        "",
        walk_table.to_markdown(index=False),
        "",
        "## 回撤节流敏感性",
        "",
        sens_table.to_markdown(index=False),
        "",
        "## 判定",
        "",
        f"- 无控制基线：收益 {baseline['return_pct']:.1f}%，回撤 {baseline['maxDD_pct']:.1f}%。",
        f"- 回撤降幅超过 20% 的固定控制：{', '.join(dd_improvers['config']) if not dd_improvers.empty else '无'}。",
        "- 验收标准：相对无控制，回撤下降至少 20%，且收益损失不成比例；关键参数需要在相邻值上表现同向，前瞻选择不能只在单一年份偶然有效。",
    ]
    with open(
        os.path.join(OUT, "portfolio_state_report.md"), "w", encoding="utf-8"
    ) as handle:
        handle.write("\n".join(report))
    print("[out] portfolio_state_report.md")


if __name__ == "__main__":
    main()
