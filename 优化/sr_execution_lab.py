# -*- coding: utf-8 -*-
"""SR+ML P0 execution restructuring experiment.

The source ML project is read-only. All outputs are written next to this script.
"""
import os
import sys

import numpy as np
import pandas as pd
from sklearn.linear_model import RidgeCV
from sklearn.preprocessing import StandardScaler

ML = r"D:\workbuddy\tushare\黄金代码优化\ml"
SRC = os.path.join(ML, "src")
if SRC not in sys.path:
    sys.path.insert(0, SRC)

from t6_geometry import label_variant
from train_v2 import META, make_walk_forward_folds, select_l1_train

OUT = os.path.dirname(os.path.abspath(__file__))
M15 = os.path.join(ML, "data", "xauusd_duka_m15.csv.gz")
M1 = r"D:\Data\stockdata\xauusd\dukascopy\xauusd_duka_m1_bid.parquet"
EVENTS = os.path.join(ML, "artifacts", "events.parquet")
FEATURES = os.path.join(ML, "artifacts", "features.parquet")

POINT_VALUE = 0.01
CLASSIC_SPREAD_USD = 0.33
SLIP_MARKET_POINTS = 2.0
SLIP_STOP_POINTS = 4.0
SLIP_STOP_LIMIT_POINTS = 2.0
SLIP_LIMIT_POINTS = 0.0
SWAP_PER_NIGHT = 0.05
INIT_CASH = 1000.0
OZ = 1.0

GEOM_FADE = {"k_tgt": 1.5, "k_stop": 0.5, "horizon": 20}
GEOM_FOLLOW = {"k_tgt": 2.5, "k_stop": 1.0, "horizon": 40}


def load_data():
    bars = pd.read_csv(M15)
    bars["datetime_utc"] = pd.to_datetime(bars["datetime_utc"])
    events = pd.read_parquet(EVENTS)
    events["datetime_utc"] = pd.to_datetime(events["datetime_utc"])
    features = pd.read_parquet(FEATURES)
    m1 = pd.read_parquet(M1)
    m1["datetime_utc"] = pd.to_datetime(
        m1["datetime_utc"], utc=True
    ).dt.tz_localize(None)
    m1 = m1.sort_values("datetime_utc").reset_index(drop=True)
    return bars, events, features, m1


def build_selection(features, events, y_fade, y_follow):
    feature_cols = [
        col for col in features.columns
        if col not in META and not col.startswith("R_")
    ]
    X = features[feature_cols].astype(float).replace([np.inf, -np.inf], np.nan)
    X = X.drop(columns=X.isna().mean()[lambda s: s > 0.30].index.tolist())
    X = X.fillna(X.median(numeric_only=True))

    meta = features[["t_entry"]].merge(
        events[["t_entry", "bars_to_outcome"]].drop_duplicates("t_entry"),
        on="t_entry",
        how="left",
        validate="many_to_one",
    )
    bars_to_outcome = np.nan_to_num(
        meta["bars_to_outcome"].to_numpy(dtype=float),
        nan=float(GEOM_FADE["horizon"]),
    ).astype(np.int64)
    starts = meta["t_entry"].to_numpy(dtype=np.int64)
    ends = starts + np.maximum(bars_to_outcome, 0)
    folds = make_walk_forward_folds(starts, ends, 6, 20)

    def ridge_oof(target):
        oof = np.full(len(target), np.nan)
        fold_id = np.full(len(target), -1, dtype=int)
        target_binary = (target > 0).astype(int)
        for train_idx, valid_idx, fold_num, _ in folds:
            cols = select_l1_train(X.iloc[train_idx], target_binary[train_idx])
            scaler = StandardScaler().fit(X.iloc[train_idx][cols])
            model = RidgeCV(alphas=np.logspace(-3, 3, 20)).fit(
                scaler.transform(X.iloc[train_idx][cols]),
                target[train_idx],
            )
            oof[valid_idx] = model.predict(
                scaler.transform(X.iloc[valid_idx][cols])
            )
            fold_id[valid_idx] = fold_num
        return oof, fold_id

    def top20(oof, fold_id):
        selected = np.zeros(len(oof), dtype=bool)
        for fold in np.unique(fold_id):
            if fold < 0:
                continue
            idx = np.where(fold_id == fold)[0]
            valid = idx[np.isfinite(oof[idx])]
            count = max(1, int(len(valid) * 0.20))
            selected[valid[np.argsort(-oof[valid])[:count]]] = True
        return selected

    oof_fade, fold_id = ridge_oof(y_fade)
    oof_follow, _ = ridge_oof(y_follow)
    selections = {
        "fade_all": np.ones(len(y_fade), dtype=bool),
        "fade_ridge": top20(oof_fade, fold_id),
        "follow_all": np.ones(len(y_follow), dtype=bool),
        "follow_ridge": top20(oof_follow, fold_id),
    }
    scores = {
        "fade_all": np.zeros(len(y_fade), dtype=float),
        "fade_ridge": oof_fade,
        "follow_all": np.zeros(len(y_follow), dtype=float),
        "follow_ridge": oof_follow,
    }
    return selections, X, scores


def nights_between(start, end):
    return max(
        0,
        int((pd.Timestamp(end).normalize() - pd.Timestamp(start).normalize()).days),
    )


def m15_nights(bars, t_entry, bars_to_outcome):
    entry_time = pd.to_datetime(
        bars["datetime_utc"].iloc[t_entry]
    ).reset_index(drop=True)
    exit_idx = np.clip(t_entry + bars_to_outcome, 0, len(bars) - 1)
    exit_time = pd.to_datetime(
        bars["datetime_utc"].iloc[exit_idx]
    ).reset_index(drop=True)
    return (
        exit_time.dt.normalize() - entry_time.dt.normalize()
    ).dt.days.to_numpy(dtype=float)


def simulate_event(
    event,
    mode,
    m1_ts,
    m1_open,
    m1_high,
    m1_low,
    m1_close,
    m15_close,
    geometry=None,
):
    t_entry = int(event["t_entry"])
    event_time = pd.Timestamp(event["datetime_utc"])
    window_start = event_time + pd.Timedelta(minutes=15)
    start_idx = int(np.searchsorted(m1_ts, np.datetime64(window_start), side="left"))
    if start_idx >= len(m1_ts):
        return None

    fade_long = int(event["direction"]) < 0
    follow_long = not fade_long
    atr0 = float(event["atr_at_entry"])
    zone_bottom = float(event["zone_bottom"])
    zone_top = float(event["zone_top"])
    event_close = float(m15_close[t_entry])
    geometry = geometry or (
        GEOM_FADE if mode.startswith("fade") else GEOM_FOLLOW
    )
    horizon = geometry["horizon"]
    window_end = window_start + pd.Timedelta(minutes=15 * horizon)
    end_idx = int(np.searchsorted(m1_ts, np.datetime64(window_end), side="left"))
    while end_idx > start_idx and m1_ts[end_idx - 1] > np.datetime64(window_end):
        end_idx -= 1
    if end_idx <= start_idx:
        return None

    fill_idx = -1
    filled = False
    entry_price = np.nan

    if mode in ("fade_close", "fade_next", "follow_close", "follow_next"):
        side_long = fade_long if mode.startswith("fade") else follow_long
        slip_points = SLIP_MARKET_POINTS
        fill_idx = start_idx
        filled = True
        entry_price = event_close if mode.endswith("close") else float(m1_open[start_idx])
    elif mode == "fade_limit_far":
        side_long = fade_long
        slip_points = SLIP_LIMIT_POINTS
        limit_price = zone_bottom if side_long else zone_top
        entry_price = limit_price
        for idx in range(start_idx, end_idx):
            open_price = float(m1_open[idx])
            if side_long and m1_low[idx] <= limit_price:
                fill_idx = idx
                entry_price = max(open_price, limit_price)
                filled = True
                break
            if not side_long and m1_high[idx] >= limit_price:
                fill_idx = idx
                entry_price = min(open_price, limit_price)
                filled = True
                break
    elif mode in ("follow_stop_break", "follow_stop_break_buffer"):
        side_long = follow_long
        slip_points = SLIP_STOP_POINTS
        buffer = 0.10 * atr0 if mode.endswith("buffer") else 0.0
        trigger = zone_top + buffer if side_long else zone_bottom - buffer
        entry_price = trigger
        for idx in range(start_idx, end_idx):
            open_price = float(m1_open[idx])
            if side_long and m1_high[idx] >= trigger:
                fill_idx = idx
                entry_price = max(open_price, trigger)
                filled = True
                break
            if not side_long and m1_low[idx] <= trigger:
                fill_idx = idx
                entry_price = min(open_price, trigger)
                filled = True
                break
    elif mode == "follow_stop_limit":
        side_long = follow_long
        slip_points = SLIP_STOP_LIMIT_POINTS
        trigger = zone_top if side_long else zone_bottom
        limit_price = trigger + 0.05 * atr0 if side_long else trigger - 0.05 * atr0
        entry_price = trigger
        for idx in range(start_idx, end_idx):
            open_price = float(m1_open[idx])
            if side_long:
                if open_price > limit_price:
                    continue
                if m1_high[idx] >= trigger and m1_low[idx] <= limit_price:
                    fill_idx = idx
                    entry_price = max(open_price, trigger)
                    filled = True
                    break
            else:
                if open_price < limit_price:
                    continue
                if m1_low[idx] <= trigger and m1_high[idx] >= limit_price:
                    fill_idx = idx
                    entry_price = min(open_price, trigger)
                    filled = True
                    break
    else:
        raise ValueError(mode)

    if not filled:
        return {
            "filled": False,
            "entry_price": entry_price,
            "entry_time": window_start,
            "exit_time": pd.NaT,
            "exit_price": np.nan,
            "pnl": 0.0,
            "R": np.nan,
            "nights": 0,
            "reason": "no_fill",
            "entry_improve_pts": np.nan,
        }

    entry_time = pd.Timestamp(m1_ts[fill_idx])
    if mode.startswith("fade"):
        k_tgt = geometry["k_tgt"]
        k_stop = geometry["k_stop"]
        if side_long:
            target = entry_price + k_tgt * atr0
            stop = zone_bottom - k_stop * atr0
        else:
            target = entry_price - k_tgt * atr0
            stop = zone_top + k_stop * atr0
        risk = abs(entry_price - stop)
    else:
        k_tgt = geometry["k_tgt"]
        k_stop = geometry["k_stop"]
        fade_target = entry_price + k_tgt * atr0 if fade_long else entry_price - k_tgt * atr0
        fade_stop = zone_bottom - k_stop * atr0 if fade_long else zone_top + k_stop * atr0
        target, stop = fade_stop, fade_target
        risk = k_tgt * atr0

    exit_price = None
    exit_time = None
    reason = None
    for idx in range(fill_idx, end_idx):
        open_price = float(m1_open[idx])
        if side_long:
            hit_target = m1_high[idx] >= target
            hit_stop = m1_low[idx] <= stop
        else:
            hit_target = m1_low[idx] <= target
            hit_stop = m1_high[idx] >= stop
        if hit_target and hit_stop:
            target_first = abs(open_price - target) <= abs(open_price - stop)
            exit_price = target if target_first else stop
            reason = "target" if target_first else "stop"
        elif hit_target:
            exit_price = target
            reason = "target"
        elif hit_stop:
            exit_price = stop
            reason = "stop"
        if exit_price is not None:
            exit_time = pd.Timestamp(m1_ts[idx])
            break

    if exit_price is None:
        exit_idx = end_idx - 1
        exit_price = float(m1_close[exit_idx])
        exit_time = pd.Timestamp(m1_ts[exit_idx])
        reason = "timeout"

    nights = nights_between(entry_time, exit_time)
    cost = (
        CLASSIC_SPREAD_USD
        + 2.0 * slip_points * POINT_VALUE
        + nights * SWAP_PER_NIGHT
    )
    sign = 1.0 if side_long else -1.0
    pnl = sign * (exit_price - entry_price) * OZ - cost
    r_value = pnl / risk if risk > 1e-9 else np.nan
    improvement = (
        event_close - entry_price if side_long else entry_price - event_close
    ) / POINT_VALUE
    return {
        "filled": True,
        "entry_price": entry_price,
        "entry_time": entry_time,
        "exit_time": exit_time,
        "exit_price": exit_price,
        "pnl": pnl,
        "R": r_value,
        "risk": risk,
        "nights": nights,
        "reason": reason,
        "entry_improve_pts": improvement,
    }


def metrics(pnls, filled_mask, r_values=None, candidate_count=None):
    pnls = np.asarray(pnls, dtype=float)
    filled_mask = np.asarray(filled_mask, dtype=bool)
    filled_pnls = pnls[filled_mask]
    equity = INIT_CASH + np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    drawdown = float(((peak - equity) / peak).max()) if len(equity) else 0.0
    gross_profit = float(filled_pnls[filled_pnls > 0].sum())
    gross_loss = float(-filled_pnls[filled_pnls < 0].sum())
    trade_count = int(filled_mask.sum())
    if r_values is None:
        r_values = pnls
    r_values = np.asarray(r_values, dtype=float)
    candidate_count = len(filled_mask) if candidate_count is None else candidate_count
    return {
        "net": float(pnls.sum()),
        "net_per_trade": float(pnls.sum() / trade_count) if trade_count else np.nan,
        "pf": gross_profit / gross_loss if gross_loss > 0 else np.inf,
        "E_R": float(np.nanmean(r_values[filled_mask])) if trade_count else np.nan,
        "maxDD_pct": drawdown * 100.0,
        "n_trades": trade_count,
        "fill_rate": float(filled_mask.sum() / candidate_count) if candidate_count else 0.0,
    }


def append_summary(rows, mode, selection, pnls, filled_mask, improvements=None, r_values=None, candidate_count=None):
    result = metrics(pnls, filled_mask, r_values=r_values, candidate_count=candidate_count)
    result.update({
        "mode": mode,
        "selection": selection,
        "avg_entry_improve_pts": (
            float(np.mean(improvements))
            if improvements is not None and len(improvements)
            else np.nan
        ),
    })
    rows.append(result)
    return result


def main():
    print("=== SR+ML P0 execution restructuring ===")
    bars, events, features, m1 = load_data()
    m15_close = bars["close"].to_numpy(dtype=float)
    m1_ts = m1["datetime_utc"].to_numpy(dtype="datetime64[ns]")
    m1_open = m1["open"].to_numpy(dtype=float)
    m1_high = m1["high"].to_numpy(dtype=float)
    m1_low = m1["low"].to_numpy(dtype=float)
    m1_close = m1["close"].to_numpy(dtype=float)
    print(
        f"[data] M15={len(bars):,} events={len(events):,} "
        f"features={features.shape} M1={len(m1):,}"
    )

    print("[1/5] Relabel pre-registered geometries ...")
    _, y_follow, _, bars_follow, _, _ = label_variant(
        bars,
        events,
        anchor="real",
        k_tgt=GEOM_FOLLOW["k_tgt"],
        k_stop=GEOM_FOLLOW["k_stop"],
        horizon=GEOM_FOLLOW["horizon"],
    )
    y_fade, _, _, bars_fade, _, _ = label_variant(
        bars,
        events,
        anchor="real",
        k_tgt=GEOM_FADE["k_tgt"],
        k_stop=GEOM_FADE["k_stop"],
        horizon=GEOM_FADE["horizon"],
    )

    print("[2/5] Build causal ridge selections ...")
    selections, _, _ = build_selection(features, events, y_fade, y_follow)

    atr = events["atr_at_entry"].to_numpy(dtype=float)
    follow_risk = 2.5 * atr
    follow_nights = m15_nights(
        bars,
        events["t_entry"].to_numpy(dtype=int),
        bars_follow,
    )
    slippage_cost = 2.0 * SLIP_MARKET_POINTS * POINT_VALUE
    legacy_extra = slippage_cost + SWAP_PER_NIGHT * np.nan_to_num(follow_nights)
    legacy_pnl = (
        y_follow - legacy_extra / np.maximum(follow_risk, 1e-9)
    ) * follow_risk
    gross_pnl = (
        y_follow * follow_risk
        + events["cost_usd"].to_numpy(dtype=float)
    )
    classic_pnl = (
        gross_pnl
        - CLASSIC_SPREAD_USD
        - slippage_cost
        - SWAP_PER_NIGHT * np.nan_to_num(follow_nights)
    )

    rows = []
    trades = []
    selected = selections["follow_ridge"]
    legacy_mask = selected & np.isfinite(legacy_pnl)
    classic_mask = selected & np.isfinite(classic_pnl)
    legacy_r = legacy_pnl / np.maximum(follow_risk, 1e-9)
    classic_r = classic_pnl / np.maximum(follow_risk, 1e-9)
    append_summary(
        rows,
        "m15_legacy_cost",
        "follow_ridge",
        np.where(legacy_mask, legacy_pnl, 0.0),
        legacy_mask,
        r_values=np.where(legacy_mask, legacy_r, np.nan),
        candidate_count=int(selected.sum()),
    )
    append_summary(
        rows,
        "m15_classic_cost",
        "follow_ridge",
        np.where(classic_mask, classic_pnl, 0.0),
        classic_mask,
        r_values=np.where(classic_mask, classic_r, np.nan),
        candidate_count=int(selected.sum()),
    )
    print(
        f"[calibration] legacy={legacy_pnl[legacy_mask].sum():.1f} "
        f"classic={classic_pnl[classic_mask].sum():.1f}"
    )

    modes = [
        ("fade_close", "fade_ridge"),
        ("fade_next", "fade_ridge"),
        ("fade_limit_far", "fade_ridge"),
        ("follow_close", "follow_ridge"),
        ("follow_next", "follow_ridge"),
        ("follow_stop_break", "follow_ridge"),
        ("follow_stop_break_buffer", "follow_ridge"),
        ("follow_stop_limit", "follow_ridge"),
        ("follow_close", "follow_all"),
        ("follow_stop_break", "follow_all"),
    ]

    print("[3/5] Replay orders on M1 ...")
    for mode, selection_name in modes:
        selected = selections[selection_name]
        selected_indices = np.where(selected)[0]
        pnls = np.zeros(len(selected), dtype=float)
        r_values = np.full(len(selected), np.nan, dtype=float)
        filled = np.zeros(len(selected), dtype=bool)
        improvements = []
        for event_idx in selected_indices:
            result = simulate_event(
                events.iloc[event_idx],
                mode,
                m1_ts,
                m1_open,
                m1_high,
                m1_low,
                m1_close,
                m15_close,
            )
            if result is None:
                continue
            filled[event_idx] = bool(result["filled"])
            pnls[event_idx] = float(result["pnl"])
            r_values[event_idx] = float(result["R"])
            if result["filled"]:
                improvements.append(float(result["entry_improve_pts"]))
                trades.append({
                    "mode": mode,
                    "selection": selection_name,
                    "event_idx": int(event_idx),
                    "entry_time": result["entry_time"],
                    "exit_time": result["exit_time"],
                    "entry_price": result["entry_price"],
                    "exit_price": result["exit_price"],
                    "pnl": result["pnl"],
                    "R": result["R"],
                    "reason": result["reason"],
                    "nights": result["nights"],
                    "entry_improve_pts": result["entry_improve_pts"],
                })
        summary = append_summary(
            rows,
            mode,
            selection_name,
            pnls,
            filled,
            improvements,
            r_values=r_values,
            candidate_count=len(selected_indices),
        )
        print(
            f"[{mode:26s}|{selection_name:12s}] "
            f"n={summary['n_trades']:4d} fill={summary['fill_rate'] * 100:5.1f}% "
            f"net={summary['net']:8.1f} pf={summary['pf']:5.3f} "
            f"E[R]={summary['E_R']:7.3f} DD={summary['maxDD_pct']:5.1f}% "
            f"improve={summary['avg_entry_improve_pts']:7.1f}pt"
        )

    print("[4/5] Save tables and equity chart ...")
    summary = pd.DataFrame(rows)
    summary.to_csv(
        os.path.join(OUT, "execution_summary.csv"),
        index=False,
        encoding="utf-8-sig",
    )
    trade_table = pd.DataFrame(trades)
    if not trade_table.empty:
        trade_table.to_parquet(
            os.path.join(OUT, "execution_trades.parquet"),
            index=False,
        )

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(13, 6))
    for mode in [
        "fade_close",
        "fade_next",
        "fade_limit_far",
        "follow_close",
        "follow_next",
        "follow_stop_break",
        "follow_stop_break_buffer",
        "follow_stop_limit",
    ]:
        sub = trade_table[trade_table["mode"] == mode]
        if sub.empty:
            continue
        sub = sub.sort_values("exit_time")
        ax.plot(
            pd.to_datetime(sub["exit_time"]),
            INIT_CASH + sub["pnl"].cumsum(),
            lw=1.2,
            label=mode,
        )
    ax.axhline(INIT_CASH, color="#666", lw=0.8, ls="--")
    ax.set_title("SR+ML execution comparison (ridge top20%)")
    ax.set_ylabel("Equity $")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "execution_equity.png"), dpi=140)
    plt.close(fig)

    print("[5/5] Write report ...")
    baseline = summary[summary["mode"] == "m15_legacy_cost"].iloc[0]
    required_points = -baseline["net"] / baseline["n_trades"] / POINT_VALUE
    report = [
        "# SR+ML P0 执行层重构实验",
        "",
        "## 口径",
        "",
        "- 源工程只读；所有输出保存在本目录。",
        "- 因果选单：每折 ridge OOF 后取 top20%，沿用原工程 walk-forward/purge/embargo。",
        "- 经典账户成本：固定点差 $0.33/oz；市价单 2 点/边，停损市价单 4 点/边，停损限价单 2 点/边，限价单 0 点/边；隔夜 $0.05/oz/自然日。",
        "- M1 同 bar 双触时，用距离该 M1 开盘更近的障碍做确定性消歧；这是代理，不是 tick 级真值。",
        "- `m15_legacy_cost` 复现原工程事件级真实点差 + 2点滑点 + 隔夜成本；`m15_classic_cost` 把点差统一为经典账户 $0.33。",
        "",
        "## 校准",
        "",
        f"- 目标基线：net 约 -92.4、PF 约 0.986、E[R] 约 -0.0333。",
        f"- 实际复现：net={baseline['net']:.1f}、PF={baseline['pf']:.3f}、E[R]={baseline['E_R']:.3f}、n={baseline['n_trades']}。",
        f"- 转正所需改善：{required_points:.1f} 点/笔（按原始基线交易数折算）。",
        "",
        "## 汇总",
        "",
        summary.to_markdown(index=False),
        "",
        "## 判读",
        "",
        "1. `follow_close` 与 `m15_classic_cost` 的差异主要来自 M1 路径消歧和入场时刻近似。",
        "2. `fade_limit_far` 检验区域边界限价能否获得足够入场改善并保持可接受成交率。",
        "3. `follow_stop_break` / buffer / stop-limit 检验突破确认是否能过滤提前进场；方向和触发边已按 follow 侧修正。",
        "4. 只有在全成本后 net > 0 且 PF ≥ 1.02，且成交率可接受时，才进入 P1 组合层验证。",
        "5. 若 P0 仍不能接近转正，不应继续调 LightGBM；应转向 P2/P3 信息源重构或停止该线。",
    ]
    with open(os.path.join(OUT, "execution_report.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(report))

    print(f"[out] {os.path.join(OUT, 'execution_summary.csv')}")
    print(f"[out] {os.path.join(OUT, 'execution_trades.parquet')}")
    print(f"[out] {os.path.join(OUT, 'execution_equity.png')}")
    print(f"[out] {os.path.join(OUT, 'execution_report.md')}")


if __name__ == "__main__":
    main()
