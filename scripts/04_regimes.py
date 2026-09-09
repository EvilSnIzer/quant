#!/usr/bin/env python3
"""
Step 4 — Volatility regime clustering (K-Means).

Input : data/processed/portfolio_metrics.csv, macro_daily.csv
Output: data/processed/
    regimes.csv           — daily features + regime label (raw & smoothed)
    regime_episodes.csv   — contiguous regime spells (on smoothed labels)
    regime_summary.csv    — per-regime statistics

Methodology
-----------
Features per date (all computed in earlier steps):
  * avg_vol30          — cross-asset mean annualized 30d volatility
  * vol30_dispersion   — cross-asset std dev of 30d volatility
                         (how unevenly risk is distributed across the book)
  * avg_pairwise_corr  — 90d average pairwise correlation
                         (diversification gauge; spikes in stress)
  * port_vol30         — equal-weighted portfolio 30d volatility
  * vix                — CBOE VIX level (macro context)

Features are z-scored using full-sample mean/std (a descriptive labeling
choice; documented in README "Limitations"), then clustered with K-Means
(k=4 -> Low / Normal / Elevated / Crisis, n_init=10, fixed random_state
for reproducibility). Cluster ids are re-ordered by centroid avg_vol30 so
id 0 is always the calmest regime.

If the smallest cluster captures < min_cluster_share of days the model is
refit with k-1 (avoids a degenerate 3-day "regime").

`regime_name_smoothed` applies a rolling 5-day majority filter to remove
1-day label flicker — used for the timeline visual and episode stats; the
raw label is retained for transparency.

The first ~90 trading days have incomplete rolling windows ("Warm-up")
and are excluded from clustering.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

REPO = Path(__file__).resolve().parents[1]
PROC = REPO / "data" / "processed"

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

REGIME_NAMES = ["Low", "Normal", "Elevated", "Crisis"]
WARMUP = "Warm-up"


def load_config() -> dict:
    with open(REPO / "config" / "portfolio.toml", "rb") as fh:
        return tomllib.load(fh)


def rolling_mode(labels: pd.Series, window: int) -> pd.Series:
    """Rolling majority vote over the last `window` labels."""
    vals = labels.values
    out = np.empty_like(vals, dtype=object)
    for i in range(len(vals)):
        lo = max(0, i - window + 1)
        window_vals = vals[lo:i + 1]
        window_vals = window_vals[~pd.isna(window_vals)]
        if len(window_vals) == 0:
            out[i] = None
        else:
            uniq, counts = np.unique(window_vals, return_counts=True)
            out[i] = uniq[np.argmax(counts)]
    return pd.Series(out, index=labels.index)


def episodes(labels: pd.Series) -> pd.DataFrame:
    """Contiguous runs of the same (non-null) label."""
    lab = labels.dropna()
    if lab.empty:
        return pd.DataFrame(columns=["regime_name", "start_date", "end_date", "n_days"])
    change = lab.ne(lab.shift())
    group = change.cumsum()
    rows = []
    for _, g in lab.groupby(group):
        rows.append({
            "regime_name": g.iloc[0],
            "start_date": g.index[0],
            "end_date": g.index[-1],
            "n_days": int(len(g)),
        })
    return pd.DataFrame(rows)


def main() -> int:
    cfg = load_config()
    rcfg = cfg["regimes"]
    k_max = int(rcfg["n_clusters"])
    random_state = int(rcfg["random_state"])
    n_init = int(rcfg["n_init"])
    min_share = float(rcfg["min_cluster_share"])
    smooth_window = int(rcfg["smooth_window"])

    pm = pd.read_csv(PROC / "portfolio_metrics.csv", index_col="date", parse_dates=True)
    macro = pd.read_csv(PROC / "macro_daily.csv", index_col="date", parse_dates=True)

    feats = pm[["avg_vol30", "vol30_dispersion", "avg_pairwise_corr", "port_vol30"]].copy()
    # VIX is optional macro context; FRED names it VIXCLS
    vix_col = next((c for c in macro.columns if str(c).upper().startswith("VIX")), None)
    if vix_col is not None:
        feats["vix"] = macro[vix_col]
    feats = feats.dropna()
    feature_cols = list(feats.columns)
    print(f"[regimes] clustering on {len(feats)} days x {len(feature_cols)} features: {feature_cols}", flush=True)

    # ---- Standardize ---------------------------------------------------------- #
    z = (feats - feats.mean()) / feats.std(ddof=0)

    # ---- Fit K-Means (with degenerate-cluster fallback) ----------------------- #
    chosen_k = k_max
    labels = None
    for k in range(k_max, 1, -1):
        km = KMeans(n_clusters=k, n_init=n_init, random_state=random_state)
        lab = km.fit_predict(z.values)
        shares = pd.Series(lab).value_counts(normalize=True)
        print(f"[regimes] k={k}: cluster shares = {shares.round(3).to_dict()}", flush=True)
        if k > 2:
            sil = silhouette_score(z.values, lab)
            print(f"[regimes] k={k}: silhouette = {sil:.3f}", flush=True)
        if shares.min() >= min_share or k == 2:
            labels = lab
            chosen_k = k
            centroids_raw = pd.DataFrame(km.cluster_centers_, columns=feature_cols)
            centroids_raw = centroids_raw.multiply(feats.std(ddof=0)).add(feats.mean())
            break
        print(f"[regimes] k={k}: smallest cluster below {min_share:.0%} share, refitting", flush=True)
    if labels is None:
        print("[regimes] ERROR: clustering failed", file=sys.stderr)
        return 1

    # ---- Order clusters by volatility level ------------------------------------ #
    order = centroids_raw["avg_vol30"].sort_values().index.tolist()
    remap = {old: new for new, old in enumerate(order)}
    regime_id = pd.Series(labels, index=feats.index).map(remap).rename("regime_id")
    names_for_k = REGIME_NAMES if chosen_k == 4 else ["Low", "Normal", "Crisis"][:chosen_k]
    regime_name = regime_id.map(dict(enumerate(names_for_k)))

    print("[regimes] centroid profile (annualized vol / avg corr / VIX):", flush=True)
    cents = centroids_raw.loc[order].copy()
    cents.index = names_for_k
    print(cents[["avg_vol30", "avg_pairwise_corr", "port_vol30"] + (["vix"] if "vix" in cents else [])].round(3), flush=True)

    # ---- Assemble daily regime table ------------------------------------------- #
    regimes = feats.copy()
    regimes["regime_id"] = regime_id
    regimes["regime_name"] = regime_name
    smoothed = rolling_mode(regime_name, smooth_window).rename("regime_name_smoothed")
    regimes["regime_name_smoothed"] = smoothed

    # Re-attach warm-up days so the timeline has no holes
    all_days = pm.index.to_series(name=None)
    regimes = regimes.reindex(all_days)
    regimes["regime_name"] = regimes["regime_name"].fillna(WARMUP)
    regimes["regime_name_smoothed"] = regimes["regime_name_smoothed"].fillna(WARMUP)
    regimes.index.name = "date"
    regimes.reset_index().to_csv(PROC / "regimes.csv", index=False)

    # ---- Episodes & summary ------------------------------------------------------ #
    eps = episodes(regimes["regime_name_smoothed"])
    eps.to_csv(PROC / "regime_episodes.csv", index=False)

    rows = []
    port = pm["port_return"]
    for name, g in regimes.groupby("regime_name_smoothed"):
        if name == WARMUP:
            continue
        days = g.dropna(subset=["avg_vol30"]).index
        sub_eps = eps[eps["regime_name"] == name]
        rows.append({
            "regime_name": name,
            "n_days": int(len(g)),
            "pct_days": float(len(g) / len(regimes)),
            "n_episodes": int(len(sub_eps)),
            "avg_episode_days": float(sub_eps["n_days"].mean()) if len(sub_eps) else np.nan,
            "max_episode_days": int(sub_eps["n_days"].max()) if len(sub_eps) else 0,
            "avg_port_vol30": float(g["port_vol30"].mean()),
            "avg_pairwise_corr": float(g["avg_pairwise_corr"].mean()),
            "avg_vix": float(g["vix"].mean()) if "vix" in g else np.nan,
            "avg_port_daily_return": float(port.loc[days].mean()) if len(days) else np.nan,
            "worst_port_day": float(port.loc[days].min()) if len(days) else np.nan,
        })
    summary = pd.DataFrame(rows)
    summary.to_csv(PROC / "regime_summary.csv", index=False)

    print(f"[regimes] chose k={chosen_k}; regimes.csv/regime_episodes.csv/regime_summary.csv written", flush=True)
    print(summary.round(3).to_string(index=False), flush=True)
    print("[regimes] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
