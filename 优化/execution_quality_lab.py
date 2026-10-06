# -*- coding: utf-8 -*-
"""Execution-quality modelling for the deterministic SR follow direction.

The direction is not learned.  Causal models predict:
1. whether the stop-limit entry fills,
2. whether the post-fill path reaches target,
3. adverse slippage of the stop-limit fill.

The same OOF predictions drive a small set of pre-registered execution
filters / order-type selectors.
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, RidgeCV
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.preprocessing import StandardScaler

from sr_execution_lab import (
    GEOM_FOLLOW,
    GEOM_FADE,
    META,
    OUT,
    POINT_VALUE,
    build_selection,
    load_data,
    metrics,
    simulate_event,
)
from train_v2 import (
    make_purged_kfold_folds,
    make_walk_forward_folds,
    select_l1_train,
)
from t6_geometry import label_variant


EMBARGO_BARS = 20
FILL_THRESHOLD = 0.50
TARGET_THRESHOLD = 0.50
CAUSAL_EXEC_COLS = [
    "atr_pct",
    "atr_rel",
    "vol_ratio",
    "z_width_atr",
    "z_approach_dist_atr",
    "sess_asia",
    "sess_london",
    "sess_ny",
    "sess_late",
]


def causal_cols(x_train, y_binary):
    cols = select_l1_train(x_train, y_binary)
    return cols if cols else [col for col in CAUSAL_EXEC_COLS if col in x_train.columns]


def build_execution_features(features, events):
    base = build_feature_matrix(features)
    timestamps = pd.to_datetime(events["datetime_utc"])
    hour = timestamps.dt.hour.to_numpy(dtype=float)
    dow = timestamps.dt.dayofweek.to_numpy(dtype=float)
    base = base.copy()
    base["atr_at_entry"] = events["atr_at_entry"].to_numpy(dtype=float)
    base["zone_width_atr"] = events["width_atr"].to_numpy(dtype=float)
    base["hour_sin"] = np.sin(2.0 * np.pi * hour / 24.0)
    base["hour_cos"] = np.cos(2.0 * np.pi * hour / 24.0)
    base["dow_sin"] = np.sin(2.0 * np.pi * dow / 7.0)
    base["dow_cos"] = np.cos(2.0 * np.pi * dow / 7.0)
    base["sess_asia"] = ((hour >= 0) & (hour < 7)).astype(float)
    base["sess_london"] = ((hour >= 7) & (hour < 12)).astype(float)
    base["sess_ny"] = ((hour >= 12) & (hour < 17)).astype(float)
    base["sess_late"] = (hour >= 17).astype(float)
    return base


def build_feature_matrix(features):
    feature_cols = [
        col for col in features.columns
        if col not in META and not col.startswith("R_")
    ]
    matrix = features[feature_cols].astype(float).replace(
        [np.inf, -np.inf], np.nan
    )
    matrix = matrix.drop(
        columns=matrix.isna().mean()[lambda series: series > 0.30].index.tolist()
    )
    matrix = matrix.fillna(matrix.median(numeric_only=True))
    return matrix


def assign_session(timestamp):
    hour = timestamp.hour
    if hour < 7:
        return "Asia"
    if hour < 12:
        return "London"
    if hour < 17:
        return "NewYork"
    return "Late"


def compute_stop_limit_slip_pts(event, entry_price):
    trigger = (
        float(event["zone_top"])
        if int(event["direction"]) > 0
        else float(event["zone_bottom"])
    )
    return abs(float(entry_price) - trigger) / POINT_VALUE


def compute_path_extremes(
    event,
    entry_price,
    entry_time,
    exit_time,
    m1_ts,
    m1_high,
    m1_low,
):
    atr = float(event["atr_at_entry"])
    risk = GEOM_FOLLOW["k_tgt"] * atr
    start_idx = int(np.searchsorted(m1_ts, np.datetime64(entry_time), side="left"))
    end_idx = int(np.searchsorted(m1_ts, np.datetime64(exit_time), side="right"))
    if end_idx <= start_idx or risk <= 1e-9:
        return np.nan, np.nan
    high = float(m1_high[start_idx:end_idx].max())
    low = float(m1_low[start_idx:end_idx].min())
    if int(event["direction"]) > 0:
        mfe = (high - float(entry_price)) / risk
        mae = (low - float(entry_price)) / risk
    else:
        mfe = (float(entry_price) - low) / risk
        mae = (float(entry_price) - high) / risk
    return float(mfe), float(mae)


def replay_orders(events, m1_ts, m1_open, m1_high, m1_low, m1_close, m15_close):
    rows = []
    modes = ("follow_next", "follow_stop_limit")
    for event_idx in range(len(events)):
        event = events.iloc[event_idx]
        event_time = pd.Timestamp(event["datetime_utc"])
        for mode in modes:
            result = simulate_event(
                event,
                mode,
                m1_ts,
                m1_open,
                m1_high,
                m1_low,
                m1_close,
                m15_close,
                geometry=GEOM_FOLLOW,
            )
            if result is None:
                row = {
                    "event_idx": event_idx,
                    "mode": mode,
                    "filled": False,
                    "entry_time": pd.NaT,
                    "exit_time": pd.NaT,
                    "entry_price": np.nan,
                    "exit_price": np.nan,
                    "pnl": 0.0,
                    "R": np.nan,
                    "reason": "out_of_range",
                    "actual_slip_pts": np.nan,
                    "fill_minutes": np.nan,
                    "postfill_minutes": np.nan,
                    "mfe_R": np.nan,
                    "mae_R": np.nan,
                }
            else:
                filled = bool(result["filled"])
                if filled and mode == "follow_stop_limit":
                    actual_slip = compute_stop_limit_slip_pts(
                        event, result["entry_price"]
                    )
                elif filled:
                    actual_slip = 0.0
                else:
                    actual_slip = np.nan
                if filled:
                    fill_minutes = max(
                        0.0,
                        (
                            pd.Timestamp(result["entry_time"])
                            - (event_time + pd.Timedelta(minutes=15))
                        ).total_seconds() / 60.0,
                    )
                    postfill_minutes = (
                        pd.Timestamp(result["exit_time"])
                        - pd.Timestamp(result["entry_time"])
                    ).total_seconds() / 60.0
                    mfe, mae = compute_path_extremes(
                        event,
                        result["entry_price"],
                        result["entry_time"],
                        result["exit_time"],
                        m1_ts,
                        m1_high,
                        m1_low,
                    )
                else:
                    fill_minutes = np.nan
                    postfill_minutes = np.nan
                    mfe = np.nan
                    mae = np.nan
                row = {
                    "event_idx": event_idx,
                    "mode": mode,
                    "filled": filled,
                    "entry_time": result["entry_time"],
                    "exit_time": result["exit_time"],
                    "entry_price": result["entry_price"],
                    "exit_price": result["exit_price"],
                    "pnl": result["pnl"],
                    "R": result["R"],
                    "reason": result["reason"],
                    "actual_slip_pts": actual_slip,
                    "fill_minutes": fill_minutes,
                    "postfill_minutes": postfill_minutes,
                    "mfe_R": mfe,
                    "mae_R": mae,
                }
            rows.append(row)
    return pd.DataFrame(rows)


def fit_oof_execution_models(matrix, labels, folds):
    n = len(matrix)
    pred_fill = np.full(n, np.nan, dtype=float)
    pred_target = np.full(n, np.nan, dtype=float)
    pred_post_r = np.full(n, np.nan, dtype=float)
    pred_slip = np.full(n, np.nan, dtype=float)
    slip_threshold = np.full(n, np.nan, dtype=float)
    fold_id = np.full(n, -1, dtype=int)
    filled_mask = labels["filled"].to_numpy(dtype=bool)

    for train_idx, valid_idx, fold_num, _ in folds:
        cols_fill = causal_cols(
            matrix.iloc[train_idx],
            labels["filled"].to_numpy(dtype=int)[train_idx],
        )
        scaler_fill = StandardScaler().fit(matrix.iloc[train_idx][cols_fill])
        fill_model = LogisticRegression(max_iter=2000).fit(
            scaler_fill.transform(matrix.iloc[train_idx][cols_fill]),
            labels["filled"].to_numpy(dtype=int)[train_idx],
        )
        pred_fill[valid_idx] = fill_model.predict_proba(
            scaler_fill.transform(matrix.iloc[valid_idx][cols_fill])
        )[:, 1]

        train_filled = train_idx[filled_mask[train_idx]]
        valid_filled = valid_idx[filled_mask[valid_idx]]
        if len(train_filled) >= 200 and len(valid_filled) > 0:
            target_y = labels["target_hit"].to_numpy(dtype=int)
            cols_target = causal_cols(matrix.iloc[train_filled], target_y[train_filled])
            scaler_target = StandardScaler().fit(matrix.iloc[train_filled][cols_target])
            target_model = LogisticRegression(max_iter=2000).fit(
                scaler_target.transform(matrix.iloc[train_filled][cols_target]),
                target_y[train_filled],
            )
            pred_target[valid_filled] = target_model.predict_proba(
                scaler_target.transform(matrix.iloc[valid_filled][cols_target])
            )[:, 1]

            post_r_y = labels["post_R"].to_numpy(dtype=float)
            cols_post = causal_cols(
                matrix.iloc[train_filled], (post_r_y[train_filled] > 0).astype(int)
            )
            scaler_post = StandardScaler().fit(matrix.iloc[train_filled][cols_post])
            post_model = RidgeCV(alphas=np.logspace(-3, 3, 20)).fit(
                scaler_post.transform(matrix.iloc[train_filled][cols_post]),
                post_r_y[train_filled],
            )
            pred_post_r[valid_filled] = post_model.predict(
                scaler_post.transform(matrix.iloc[valid_filled][cols_post])
            )

            slip_y = labels["actual_slip_pts"].to_numpy(dtype=float)
            slip_median = float(np.nanmedian(slip_y[train_filled]))
            cols_slip = causal_cols(
                matrix.iloc[train_filled], (slip_y[train_filled] > slip_median).astype(int)
            )
            scaler_slip = StandardScaler().fit(matrix.iloc[train_filled][cols_slip])
            slip_model = RidgeCV(alphas=np.logspace(-3, 3, 20)).fit(
                scaler_slip.transform(matrix.iloc[train_filled][cols_slip]),
                slip_y[train_filled],
            )
            pred_slip[valid_filled] = slip_model.predict(
                scaler_slip.transform(matrix.iloc[valid_filled][cols_slip])
            )
            slip_threshold[valid_filled] = slip_median

        fold_id[valid_idx] = fold_num

    return {
        "pred_fill": pred_fill,
        "pred_target": pred_target,
        "pred_post_R": pred_post_r,
        "pred_slip": pred_slip,
        "slip_threshold": slip_threshold,
        "fold_id": fold_id,
    }


def model_metrics(labels, predictions):
    valid = np.isfinite(predictions["pred_fill"])
    filled_valid = valid & labels["filled"].to_numpy(dtype=bool)
    target_y = labels["target_hit"].to_numpy(dtype=int)
    post_r_y = labels["post_R"].to_numpy(dtype=float)
    slip_y = labels["actual_slip_pts"].to_numpy(dtype=float)
    target_valid = filled_valid & np.isfinite(predictions["pred_target"])
    post_valid = filled_valid & np.isfinite(predictions["pred_post_R"])
    slip_valid = filled_valid & np.isfinite(predictions["pred_slip"])
    return {
        "fill_auc": roc_auc_score(labels["filled"].to_numpy(dtype=int)[valid], predictions["pred_fill"][valid]),
        "fill_brier": brier_score_loss(labels["filled"].to_numpy(dtype=int)[valid], predictions["pred_fill"][valid]),
        "target_auc": roc_auc_score(target_y[target_valid], predictions["pred_target"][target_valid]),
        "target_brier": brier_score_loss(target_y[target_valid], predictions["pred_target"][target_valid]),
        "post_R_spearman": pd.Series(predictions["pred_post_R"][post_valid]).corr(
            pd.Series(post_r_y[post_valid]), method="spearman"
        ),
        "slip_spearman": pd.Series(predictions["pred_slip"][slip_valid]).corr(
            pd.Series(slip_y[slip_valid]), method="spearman"
        ),
        "slip_mae": float(np.mean(np.abs(predictions["pred_slip"][slip_valid] - slip_y[slip_valid]))),
        "n_valid": int(valid.sum()),
        "n_filled_valid": int(filled_valid.sum()),
    }


def evaluate_policy(
    name,
    selected_mask,
    stop_result,
    next_result,
    fallback_to_next,
    force_next=False,
):
    n = len(selected_mask)
    chosen_pnl = np.zeros(n, dtype=float)
    chosen_r = np.full(n, np.nan, dtype=float)
    filled = np.zeros(n, dtype=bool)
    chosen_mode = np.full(n, "none", dtype=object)
    stop_filled = stop_result["filled"].to_numpy(dtype=bool)
    stop_pnl = stop_result["pnl"].to_numpy(dtype=float)
    stop_r = stop_result["R"].to_numpy(dtype=float)
    next_pnl = next_result["pnl"].to_numpy(dtype=float)
    next_r = next_result["R"].to_numpy(dtype=float)
    for idx in range(n):
        if force_next:
            chosen_pnl[idx] = next_pnl[idx]
            chosen_r[idx] = next_r[idx]
            filled[idx] = True
            chosen_mode[idx] = "next"
        elif selected_mask[idx] and stop_filled[idx]:
            chosen_pnl[idx] = stop_pnl[idx]
            chosen_r[idx] = stop_r[idx]
            filled[idx] = True
            chosen_mode[idx] = "stop_limit"
        elif fallback_to_next:
            chosen_pnl[idx] = next_pnl[idx]
            chosen_r[idx] = next_r[idx]
            filled[idx] = True
            chosen_mode[idx] = "next"
    result = metrics(chosen_pnl, filled, r_values=chosen_r, candidate_count=n)
    result.update({
        "policy": name,
        "n_selected": int(selected_mask.sum()),
        "stop_limit_share": float(np.mean(chosen_mode == "stop_limit")),
        "next_share": float(np.mean(chosen_mode == "next")),
    })
    return result, chosen_pnl, filled, chosen_mode


def build_policies(labels, predictions):
    pred_fill = predictions["pred_fill"]
    pred_target = predictions["pred_target"]
    pred_slip = predictions["pred_slip"]
    slip_threshold = predictions["slip_threshold"]
    policies = {
        "next_all": (np.ones(len(pred_fill), dtype=bool), False),
        "stop_limit_all": (np.ones(len(pred_fill), dtype=bool), False),
        "stop_limit_fallback_next": (np.ones(len(pred_fill), dtype=bool), True),
        "fill_filter": (np.isfinite(pred_fill) & (pred_fill >= FILL_THRESHOLD), False),
        "target_filter": (
            np.isfinite(pred_fill) & (pred_fill >= FILL_THRESHOLD)
            & np.isfinite(pred_target) & (pred_target >= TARGET_THRESHOLD),
            False,
        ),
        "slip_target_filter": (
            np.isfinite(pred_fill) & (pred_fill >= FILL_THRESHOLD)
            & np.isfinite(pred_target) & (pred_target >= TARGET_THRESHOLD)
            & np.isfinite(pred_slip) & np.isfinite(slip_threshold)
            & (pred_slip <= slip_threshold),
            False,
        ),
        "selector_with_fallback": (
            np.isfinite(pred_fill) & (pred_fill >= FILL_THRESHOLD)
            & np.isfinite(pred_target) & (pred_target >= TARGET_THRESHOLD)
            & np.isfinite(pred_slip) & np.isfinite(slip_threshold)
            & (pred_slip <= slip_threshold),
            True,
        ),
    }
    return policies


def build_acceptance(policy_stats):
    rows = []
    for policy_name in policy_stats["policy"].unique():
        if policy_name in {"next_all", "stop_limit_all"}:
            continue
        wf = policy_stats[
            (policy_stats["scheme"] == "walk_forward")
            & (policy_stats["policy"] == policy_name)
        ].iloc[0]
        pk = policy_stats[
            (policy_stats["scheme"] == "purged_kfold")
            & (policy_stats["policy"] == policy_name)
        ].iloc[0]
        wf_base = policy_stats[
            (policy_stats["scheme"] == "walk_forward")
            & (policy_stats["policy"] == "stop_limit_all")
        ].iloc[0]
        pk_base = policy_stats[
            (policy_stats["scheme"] == "purged_kfold")
            & (policy_stats["policy"] == "stop_limit_all")
        ].iloc[0]
        net_better = (
            wf["net"] > wf_base["net"] and pk["net"] > pk_base["net"]
        )
        dd_better = (
            wf["maxDD_pct"] < wf_base["maxDD_pct"] * 0.90
            and pk["maxDD_pct"] < pk_base["maxDD_pct"] * 0.90
        )
        net_retained = (
            wf["net"] >= wf_base["net"] * 0.70
            and pk["net"] >= pk_base["net"] * 0.70
        )
        accepted = (net_better or dd_better) and net_retained
        rows.append({
            "policy": policy_name,
            "wf_net": wf["net"],
            "pk_net": pk["net"],
            "wf_dd_pct": wf["maxDD_pct"],
            "pk_dd_pct": pk["maxDD_pct"],
            "wf_net_vs_base": wf["net"] - wf_base["net"],
            "pk_net_vs_base": pk["net"] - pk_base["net"],
            "wf_dd_vs_base_pct": wf["maxDD_pct"] - wf_base["maxDD_pct"],
            "pk_dd_vs_base_pct": pk["maxDD_pct"] - pk_base["maxDD_pct"],
            "net_better_both_cv": net_better,
            "dd_better_both_cv": dd_better,
            "net_retained_70pct": net_retained,
            "accepted": accepted,
        })
    return pd.DataFrame(rows)


def session_table(events, stop_result):
    timestamps = pd.to_datetime(events["datetime_utc"])
    sessions = timestamps.map(assign_session)
    filled = stop_result["filled"].to_numpy(dtype=bool)
    slip = stop_result["actual_slip_pts"].to_numpy(dtype=float)
    r_value = stop_result["R"].to_numpy(dtype=float)
    pnl = stop_result["pnl"].to_numpy(dtype=float)
    rows = []
    for session in ("Asia", "London", "NewYork", "Late"):
        mask = (sessions == session).to_numpy()
        pos = pnl[mask & filled & (pnl > 0)].sum()
        neg = -pnl[mask & filled & (pnl < 0)].sum()
        rows.append({
            "session": session,
            "n_events": int(mask.sum()),
            "fill_rate": float(filled[mask].mean()),
            "mean_slip_pts": float(np.nanmean(slip[mask & filled])),
            "median_slip_pts": float(np.nanmedian(slip[mask & filled])),
            "mean_R": float(np.nanmean(r_value[mask & filled])),
            "pf": pos / neg if neg > 0 else np.inf,
        })
    return pd.DataFrame(rows)


def main():
    print("=== SR+ML execution quality layer ===")
    bars, events, features, m1 = load_data()
    m15_close = bars["close"].to_numpy(dtype=float)
    m1_ts = m1["datetime_utc"].to_numpy(dtype="datetime64[ns]")
    m1_open = m1["open"].to_numpy(dtype=float)
    m1_high = m1["high"].to_numpy(dtype=float)
    m1_low = m1["low"].to_numpy(dtype=float)
    m1_close = m1["close"].to_numpy(dtype=float)

    print("[0/6] Reproduce causal follow_ridge population ...")
    _, y_follow, _, _, _, _ = label_variant(
        bars,
        events,
        anchor="real",
        k_tgt=GEOM_FOLLOW["k_tgt"],
        k_stop=GEOM_FOLLOW["k_stop"],
        horizon=GEOM_FOLLOW["horizon"],
    )
    y_fade, _, _, _, _, _ = label_variant(
        bars,
        events,
        anchor="real",
        k_tgt=GEOM_FADE["k_tgt"],
        k_stop=GEOM_FADE["k_stop"],
        horizon=GEOM_FADE["horizon"],
    )
    selections, _, _ = build_selection(features, events, y_fade, y_follow)
    selected = selections["follow_ridge"]
    events = events.loc[selected].reset_index(drop=True)
    features = features.loc[selected].reset_index(drop=True)

    print(
        f"[data] events={len(events):,} features={features.shape} "
        f"M1={len(m1):,}"
    )

    print("[1/6] Replay next and stop-limit orders ...")
    replay = replay_orders(
        events, m1_ts, m1_open, m1_high, m1_low, m1_close, m15_close
    )
    next_result = replay[replay["mode"] == "follow_next"].sort_values("event_idx").reset_index(drop=True)
    stop_result = replay[replay["mode"] == "follow_stop_limit"].sort_values("event_idx").reset_index(drop=True)
    replay.to_parquet(os.path.join(OUT, "execution_quality_replay.parquet"), index=False)

    labels = pd.DataFrame({
        "filled": stop_result["filled"].astype(bool),
        "target_hit": (stop_result["filled"] & (stop_result["reason"] == "target")).astype(bool),
        "stop_hit": (stop_result["filled"] & (stop_result["reason"] == "stop")).astype(bool),
        "post_R": stop_result["R"].astype(float),
        "actual_slip_pts": stop_result["actual_slip_pts"].astype(float),
    })
    print(
        f"[labels] stop_limit fill={labels['filled'].mean():.3f} "
        f"target_hit={labels['target_hit'].mean():.3f} "
        f"slip_median={labels['actual_slip_pts'].median():.2f}pt"
    )

    print("[2/6] Build causal execution features ...")
    matrix = build_execution_features(features, events)
    starts = events["t_entry"].to_numpy(dtype=np.int64)
    ends = starts + int(GEOM_FOLLOW["horizon"])
    folds_by_scheme = {
        "walk_forward": make_walk_forward_folds(
            starts, ends, n_splits=6, embargo_bars=EMBARGO_BARS
        ),
        "purged_kfold": make_purged_kfold_folds(
            starts, ends, n_splits=5, embargo_bars=EMBARGO_BARS
        ),
    }

    model_rows = []
    policy_rows = []
    policy_curves = {}
    for scheme_name, folds in folds_by_scheme.items():
        print(f"[3/6] Fit {scheme_name} execution models ...")
        predictions = fit_oof_execution_models(matrix, labels, folds)
        stat = model_metrics(labels, predictions)
        stat["scheme"] = scheme_name
        model_rows.append(stat)
        print(
            f"[metrics] fill_auc={stat['fill_auc']:.3f} "
            f"target_auc={stat['target_auc']:.3f} "
            f"slip_rho={stat['slip_spearman']:.3f}"
        )

        print(f"[4/6] Evaluate {scheme_name} execution policies ...")
        policies = build_policies(labels, predictions)
        for policy_name, (selected_mask, fallback) in policies.items():
            force_next = policy_name == "next_all"
            result, chosen_pnl, filled, chosen_mode = evaluate_policy(
                policy_name,
                selected_mask,
                stop_result,
                next_result,
                fallback,
                force_next=force_next,
            )
            result["scheme"] = scheme_name
            policy_rows.append(result)
            policy_curves[(scheme_name, policy_name)] = pd.DataFrame({
                "event_idx": np.arange(len(chosen_pnl)),
                "pnl": chosen_pnl,
                "filled": filled,
                "mode": chosen_mode,
            })
            print(
                f"[{policy_name:28s}] n={result['n_trades']:4d} "
                f"net={result['net']:9.1f} PF={result['pf']:5.3f} "
                f"E[R]={result['E_R']:7.4f} DD={result['maxDD_pct']:5.1f}%"
            )

    print("[5/6] Save tables and chart ...")
    model_stats = pd.DataFrame(model_rows)
    policy_stats = pd.DataFrame(policy_rows)
    acceptance_stats = build_acceptance(policy_stats)
    session_stats = session_table(events, stop_result)
    model_stats.to_csv(os.path.join(OUT, "execution_quality_models.csv"), index=False, encoding="utf-8-sig")
    policy_stats.to_csv(os.path.join(OUT, "execution_quality_policies.csv"), index=False, encoding="utf-8-sig")
    acceptance_stats.to_csv(os.path.join(OUT, "execution_quality_acceptance.csv"), index=False, encoding="utf-8-sig")
    session_stats.to_csv(os.path.join(OUT, "execution_quality_sessions.csv"), index=False, encoding="utf-8-sig")

    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(13, 6))
    for (scheme_name, policy_name), curve in policy_curves.items():
        if scheme_name != "walk_forward":
            continue
        equity = 1000.0 + curve.sort_values("event_idx")["pnl"].cumsum()
        ax.plot(equity.index, equity.values, lw=1.1, label=policy_name)
    ax.axhline(1000.0, color="#666", lw=0.8, ls="--")
    ax.set_title("SR+ML execution-quality policies")
    ax.set_xlabel("Event sequence")
    ax.set_ylabel("Equity $")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "execution_quality_equity.png"), dpi=140)
    plt.close(fig)

    print("[6/6] Write report ...")
    wf_policies = policy_stats[policy_stats["scheme"] == "walk_forward"]
    pk_policies = policy_stats[policy_stats["scheme"] == "purged_kfold"]
    baseline_wf = wf_policies[wf_policies["policy"] == "stop_limit_all"].iloc[0]
    best_wf = wf_policies.loc[wf_policies["net"].idxmax()]
    best_dd_wf = wf_policies.loc[wf_policies["maxDD_pct"].idxmin()]
    accepted_policies = acceptance_stats[acceptance_stats["accepted"]]
    report = [
        "# SR+ML 执行质量层实验",
        "",
        "## 设计",
        "",
        "- 第一层方向固定为 SR follow，不学习涨跌方向。",
        "- 模型只做三件事：预测 stop-limit 是否成交、成交后是否先到 target、成交滑点是否偏高。",
        "- 成交滑点代理 = M1 实际成交价相对触发价的不利偏移；这是 M1 代理，不是 tick 级真实滑点。",
        "- 路径质量还记录 MFE/MAE、target/stop/timeout 和持仓分钟数。",
        "- 所有模型按 purged walk-forward 和 purged K-fold 双口径生成 OOF 预测。",
        "- 策略只允许切换订单类型或过滤事件，不允许改方向。",
        "",
        "## 会话执行画像",
        "",
        session_stats.to_markdown(index=False),
        "",
        "## OOF 模型质量",
        "",
        model_stats.to_markdown(index=False),
        "",
        "## 策略汇总",
        "",
        policy_stats.to_markdown(index=False),
        "",
        "## 判读",
        "",
        f"- 固定 stop-limit 基线：net={baseline_wf['net']:.1f}，PF={baseline_wf['pf']:.3f}，回撤={baseline_wf['maxDD_pct']:.1f}%。",
        f"- walk-forward 最高净利策略：{best_wf['policy']}，net={best_wf['net']:.1f}，PF={best_wf['pf']:.3f}，回撤={best_wf['maxDD_pct']:.1f}%。",
        f"- walk-forward 最低回撤策略：{best_dd_wf['policy']}，net={best_dd_wf['net']:.1f}，PF={best_dd_wf['pf']:.3f}，回撤={best_dd_wf['maxDD_pct']:.1f}%。",
        "- 验收标准：执行质量策略相对固定 stop-limit，必须同时改善净利或回撤，且在两个 CV 口径方向一致。",
        (
            f"- 最终判定：通过；策略={', '.join(accepted_policies['policy'])}。"
            if not accepted_policies.empty
            else "- 最终判定：不通过；当前执行质量模型不能同时保留足够净利并一致改善净利或回撤，不建议作为实盘过滤或订单选择器。"
        ),
    ]
    with open(os.path.join(OUT, "execution_quality_report.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(report))
    print(f"[out] {os.path.join(OUT, 'execution_quality_report.md')}")


if __name__ == "__main__":
    main()
