# -*- coding: utf-8 -*-
"""P2 model-layer experiment without a learned direction model.

The first layer remains the deterministic SR follow direction.  Ridge and
logistic models only rank/filter candidates.  Labels are the causal M1 replay
outcomes of the pre-registered follow_stop_limit execution geometry.
"""
import os

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression, RidgeCV
from sklearn.preprocessing import StandardScaler

from sr_execution_lab import (
    GEOM_FOLLOW,
    META,
    OUT,
    load_data,
    metrics,
    simulate_event,
)
from train_v2 import (
    eval_by_group,
    make_purged_kfold_folds,
    make_walk_forward_folds,
    select_l1_train,
)


TOP_FRAC = 0.20
EMBARGO_BARS = 20
P_THRESHOLD = 0.50


def replay_all_candidates(events, m1_ts, m1_open, m1_high, m1_low, m1_close, m15_close):
    pnl = np.full(len(events), np.nan, dtype=float)
    r_value = np.full(len(events), np.nan, dtype=float)
    fill = np.zeros(len(events), dtype=bool)
    rows = []
    for event_idx in range(len(events)):
        result = simulate_event(
            events.iloc[event_idx],
            "follow_stop_limit",
            m1_ts,
            m1_open,
            m1_high,
            m1_low,
            m1_close,
            m15_close,
            geometry=GEOM_FOLLOW,
        )
        if result is None or not result["filled"]:
            continue
        fill[event_idx] = True
        pnl[event_idx] = result["pnl"]
        r_value[event_idx] = result["R"]
        rows.append({
            "event_idx": event_idx,
            "entry_time": result["entry_time"],
            "exit_time": result["exit_time"],
            "pnl": result["pnl"],
            "R": result["R"],
            "reason": result["reason"],
        })
    return pnl, r_value, fill, pd.DataFrame(rows)


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


def make_folds(events, candidate_idx):
    starts = events.loc[candidate_idx, "t_entry"].to_numpy(dtype=np.int64)
    ends = starts + int(GEOM_FOLLOW["horizon"])
    return {
        "walk_forward": make_walk_forward_folds(
            starts, ends, n_splits=6, embargo_bars=EMBARGO_BARS
        ),
        "purged_kfold": make_purged_kfold_folds(
            starts, ends, n_splits=5, embargo_bars=EMBARGO_BARS
        ),
    }


def top20(scores, fold_id):
    selected = np.zeros(len(scores), dtype=bool)
    for fold in np.unique(fold_id):
        if fold < 0:
            continue
        idx = np.where(fold_id == fold)[0]
        valid = idx[np.isfinite(scores[idx])]
        count = max(1, int(len(valid) * TOP_FRAC))
        selected[valid[np.argsort(-scores[valid])[:count]]] = True
    return selected


def fit_oof(matrix, target, folds):
    ridge_oof = np.full(len(target), np.nan, dtype=float)
    logit_oof = np.full(len(target), np.nan, dtype=float)
    fold_id = np.full(len(target), -1, dtype=int)
    binary = (target > 0).astype(int)
    for train_idx, valid_idx, fold_num, _ in folds:
        cols = select_l1_train(matrix.iloc[train_idx], binary[train_idx])
        scaler = StandardScaler().fit(matrix.iloc[train_idx][cols])
        ridge = RidgeCV(alphas=np.logspace(-3, 3, 20)).fit(
            scaler.transform(matrix.iloc[train_idx][cols]),
            target[train_idx],
        )
        logit = LogisticRegression(max_iter=2000).fit(
            scaler.transform(matrix.iloc[train_idx][cols]),
            binary[train_idx],
        )
        ridge_oof[valid_idx] = ridge.predict(
            scaler.transform(matrix.iloc[valid_idx][cols])
        )
        logit_oof[valid_idx] = logit.predict_proba(
            scaler.transform(matrix.iloc[valid_idx][cols])
        )[:, 1]
        fold_id[valid_idx] = fold_num
    return ridge_oof, logit_oof, fold_id


def threshold_eval(scores, target, fold_id, threshold):
    selected = np.isfinite(scores) & (scores >= threshold)
    observed = float(target[selected].mean()) if selected.any() else np.nan
    rng = np.random.default_rng(0)
    nulls = []
    for _ in range(2000):
        permuted = scores.copy()
        for fold in np.unique(fold_id):
            idx = np.where(fold_id == fold)[0]
            permuted[idx] = scores[rng.permutation(idx)]
        sel = np.isfinite(permuted) & (permuted >= threshold)
        nulls.append(float(target[sel].mean()) if sel.any() else np.nan)
    return {
        "n": int(selected.sum()),
        "freq": float(selected.mean()),
        "E_R": observed,
        "p": float(np.nanmean(np.asarray(nulls) >= observed)),
    }


def evaluate_scheme(name, target, ridge_oof, logit_oof, fold_id):
    valid = np.isfinite(ridge_oof) & np.isfinite(logit_oof)
    rows = []
    for model_name, scores in (
        ("ridge_R_top20", ridge_oof),
        ("meta_logit_top20", logit_oof),
    ):
        stat = eval_by_group(scores[valid], target[valid], fold_id[valid])
        rows.append({
            "scheme": name,
            "model": model_name,
            "n": stat["n"],
            "E_R": stat["r_model"],
            "E_R_random": stat["r_base"],
            "lift": stat["lift"],
            "p": stat["p"],
        })
        print(
            f"[{name:14s}|{model_name:19s}] n={stat['n']:4d} "
            f"E[R]={stat['r_model']:7.4f} lift={stat['lift']:7.4f} p={stat['p']:5.3f}"
        )
    stat = threshold_eval(logit_oof[valid], target[valid], fold_id[valid], P_THRESHOLD)
    rows.append({
        "scheme": name,
        "model": "meta_logit_p>=0.50",
        "n": stat["n"],
        "E_R": stat["E_R"],
        "E_R_random": float(target[valid].mean()),
        "lift": stat["E_R"] - float(target[valid].mean()),
        "p": stat["p"],
    })
    print(
        f"[{name:14s}|meta_logit_p>=0.50 ] n={stat['n']:4d} "
        f"E[R]={stat['E_R']:7.4f} p={stat['p']:5.3f}"
    )
    return rows


def summarize_selection(selection_name, selection, pnl, r_value, fill, rows):
    pnls = np.where(fill & selection, pnl, 0.0)
    r_values = np.where(fill & selection, r_value, np.nan)
    result = metrics(
        pnls,
        fill & selection,
        r_values=r_values,
        candidate_count=int(fill.sum()),
    )
    result.update({"scheme": rows.get("scheme", ""), "model": selection_name})
    return result


def main():
    print("=== SR+ML P2 model layer ===")
    bars, events, features, m1 = load_data()
    m15_close = bars["close"].to_numpy(dtype=float)
    m1_ts = m1["datetime_utc"].to_numpy(dtype="datetime64[ns]")
    m1_open = m1["open"].to_numpy(dtype=float)
    m1_high = m1["high"].to_numpy(dtype=float)
    m1_low = m1["low"].to_numpy(dtype=float)
    m1_close = m1["close"].to_numpy(dtype=float)
    print(f"[data] events={len(events):,} features={features.shape} M1={len(m1):,}")

    print("[1/6] Replay all follow_stop_limit candidates ...")
    pnl, r_value, fill, candidate_trades = replay_all_candidates(
        events, m1_ts, m1_open, m1_high, m1_low, m1_close, m15_close
    )
    candidate_idx = np.where(fill)[0]
    target = r_value[candidate_idx]
    print(f"[candidates] filled={len(candidate_idx):,} E[R]={target.mean():.4f}")

    print("[2/6] Prepare causal features ...")
    matrix_all = build_feature_matrix(features)
    matrix = matrix_all.iloc[candidate_idx].reset_index(drop=True)
    folds_by_scheme = make_folds(events, candidate_idx)

    stat_rows = []
    selections = {}
    selection_meta = {}
    for scheme_number, (scheme_name, folds) in enumerate(folds_by_scheme.items()):
        print(f"[3/6] Fit {scheme_name} OOF models ...")
        ridge_oof, logit_oof, fold_id = fit_oof(matrix, target, folds)
        stat_rows.extend(
            evaluate_scheme(scheme_name, target, ridge_oof, logit_oof, fold_id)
        )
        selections[f"{scheme_name}_ridge"] = top20(ridge_oof, fold_id)
        selections[f"{scheme_name}_logit"] = top20(logit_oof, fold_id)
        selections[f"{scheme_name}_logit_p50"] = (
            np.isfinite(logit_oof) & (logit_oof >= P_THRESHOLD)
        )
        for selection_name, selection in selections.items():
            if selection_name.startswith(scheme_name):
                selection_meta[selection_name] = scheme_name

    print("[4/6] Summarize OOF trade performance ...")
    trade_rows = []
    trade_rows.append({
        "scheme": "none",
        "model": "all_candidates",
        **metrics(pnl[candidate_idx], fill[candidate_idx], r_value[candidate_idx]),
    })
    for selection_name, selection in selections.items():
        full_selection = np.zeros(len(fill), dtype=bool)
        full_selection[candidate_idx[selection]] = True
        pnls = np.where(fill & full_selection, pnl, 0.0)
        r_values = np.where(fill & full_selection, r_value, np.nan)
        result = metrics(
            pnls,
            fill & full_selection,
            r_values=r_values,
            candidate_count=int(fill.sum()),
        )
        result.update({
            "scheme": selection_meta[selection_name],
            "model": selection_name,
        })
        trade_rows.append(result)
        print(
            f"[{selection_name:32s}] n={result['n_trades']:4d} "
            f"net={result['net']:9.1f} PF={result['pf']:5.3f} "
            f"E[R]={result['E_R']:7.4f} DD={result['maxDD_pct']:5.1f}%"
        )

    print("[5/6] Save tables and equity chart ...")
    stats = pd.DataFrame(stat_rows)
    trades = pd.DataFrame(trade_rows)
    stats.to_csv(os.path.join(OUT, "model_cv_stats.csv"), index=False, encoding="utf-8-sig")
    trades.to_csv(os.path.join(OUT, "model_summary.csv"), index=False, encoding="utf-8-sig")
    candidate_trades.to_parquet(os.path.join(OUT, "model_all_candidate_trades.parquet"), index=False)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(13, 6))
    for selection_name, selection in selections.items():
        chosen = candidate_idx[selection]
        sub = candidate_trades[candidate_trades["event_idx"].isin(chosen)].copy()
        sub = sub.sort_values("exit_time")
        if sub.empty:
            continue
        ax.plot(
            sub["exit_time"],
            1000.0 + sub["pnl"].cumsum(),
            lw=1.1,
            label=selection_name,
        )
    ax.axhline(1000.0, color="#666", lw=0.8, ls="--")
    ax.set_title("SR+ML causal model selections (follow_stop_limit)")
    ax.set_ylabel("Equity $")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(OUT, "model_equity.png"), dpi=140)
    plt.close(fig)

    print("[6/6] Write report ...")
    wf_stats = stats[stats["scheme"] == "walk_forward"]
    pk_stats = stats[stats["scheme"] == "purged_kfold"]
    significant = []
    for model in wf_stats["model"].unique():
        wf = wf_stats[wf_stats["model"] == model].iloc[0]
        pk = pk_stats[pk_stats["model"] == model].iloc[0]
        passed = wf["lift"] > 0 and pk["lift"] > 0 and wf["p"] < 0.05 and pk["p"] < 0.05
        significant.append({
            "model": model,
            "wf_lift": wf["lift"],
            "wf_p": wf["p"],
            "pk_lift": pk["lift"],
            "pk_p": pk["p"],
            "passed": passed,
        })
    significance = pd.DataFrame(significant)
    significance.to_csv(
        os.path.join(OUT, "model_significance.csv"), index=False, encoding="utf-8-sig"
    )
    report = [
        "# SR+ML P2 模型层重构实验",
        "",
        "## 口径",
        "",
        "- 第一层方向不学习：全部为确定性 follow 方向。",
        "- 标签为 `follow_stop_limit` 在 M1 全成本重放后的净 R；未成交候选不进入训练与评估。",
        "- 模型只用 ridge 回归净 R 或 logistic 元标签做 top20% 选单；另测预注册 p≥0.50 过滤。",
        "- 验证同时使用 purged walk-forward 与 purged K-fold，embargo=20 bars。",
        "- 验收：两个口径 lift 均为正且 p<0.05；最终全成本 net>0、PF≥1.02。",
        "",
        "## CV 显著性",
        "",
        stats.to_markdown(index=False),
        "",
        "## OOF 交易表现",
        "",
        trades.to_markdown(index=False),
        "",
        "## 判读",
        "",
        significance.to_markdown(index=False),
        "",
        "- 若没有任何模型同时满足两口径显著，则 P2 未通过，不应继续把 LightGBM 加回主线。",
        "- 若只有 CV 显著但 OOF 交易表现不达 PF 门槛，也应降级为仓位/过滤层，而不是方向模型。",
    ]
    with open(os.path.join(OUT, "model_report.md"), "w", encoding="utf-8") as handle:
        handle.write("\n".join(report))
    print(f"[out] {os.path.join(OUT, 'model_cv_stats.csv')}")
    print(f"[out] {os.path.join(OUT, 'model_summary.csv')}")
    print(f"[out] {os.path.join(OUT, 'model_report.md')}")


if __name__ == "__main__":
    main()
