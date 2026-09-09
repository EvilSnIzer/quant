#!/usr/bin/env python3
"""
Step 6 — Generate dashboard-style charts (PNG) used in the README and memo.

Input : data/processed/*.csv
Output: reports/charts/*.png

These are static analytic exhibits; the interactive version of the same
views is built in Power BI (see powerbi/BUILD_GUIDE.md).
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.patches import Patch
from matplotlib.ticker import FuncFormatter

REPO = Path(__file__).resolve().parents[1]
PROC = REPO / "data" / "processed"
OUT = REPO / "reports" / "charts"

REGIME_COLORS = {
    "Low": "#43a047",
    "Normal": "#1e88e5",
    "Elevated": "#fb8c00",
    "Crisis": "#e53935",
    "Warm-up": "#bdbdbd",
}
CLASS_COLORS = {"Equity": "#1e88e5", "Crypto": "#f9a825", "Portfolio": "#6a1b9a"}

plt.rcParams.update({
    "figure.facecolor": "white",
    "axes.facecolor": "white",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "axes.grid": True,
    "grid.alpha": 0.30,
    "grid.linewidth": 0.6,
    "font.size": 9.5,
    "axes.titlesize": 12,
    "axes.titleweight": "bold",
    "axes.labelsize": 9.5,
    "legend.frameon": False,
    "figure.dpi": 150,
})

pct = FuncFormatter(lambda x, _: f"{x:.0%}")
pct1 = FuncFormatter(lambda x, _: f"{x:.0%}" if abs(x) >= 0.005 or x == 0 else f"{x:.1%}")


def load():
    pm = pd.read_csv(PROC / "portfolio_metrics.csv", index_col="date", parse_dates=True)
    # Combined daily table (portfolio + benchmarks + macro + regime) from step 5
    port = pd.read_csv(PROC / "powerbi" / "portfolio_daily.csv", index_col="date", parse_dates=True)
    bench = pd.read_csv(PROC / "benchmark_daily.csv", index_col="date", parse_dates=True)
    regimes = pd.read_csv(PROC / "regimes.csv", index_col="date", parse_dates=True)
    eps = pd.read_csv(PROC / "regime_episodes.csv", parse_dates=["start_date", "end_date"])
    corr_full = pd.read_csv(PROC / "corr_full.csv")
    corr_roll = pd.read_csv(PROC / "corr_rolling.csv", index_col="date", parse_dates=True)
    risk = pd.read_csv(PROC / "risk_summary.csv")
    var = pd.read_csv(PROC / "var_daily.csv", parse_dates=["date"])
    summary = pd.read_csv(PROC / "regime_summary.csv")
    return pm, port, bench, regimes, eps, corr_full, corr_roll, risk, var, summary


def regime_bands(ax, eps: pd.DataFrame, alpha: float = 0.16):
    for _, row in eps.iterrows():
        color = REGIME_COLORS.get(row["regime_name"], "#bdbdbd")
        ax.axvspan(row["start_date"], row["end_date"], color=color, alpha=alpha, lw=0)


def regime_legend(ax, eps: pd.DataFrame, ncol: int = 5, loc: str = "lower left"):
    names = list(dict.fromkeys(eps["regime_name"]))
    handles = [Patch(facecolor=REGIME_COLORS.get(n, "#bdbdbd"), alpha=0.35, label=f"{n} vol regime") for n in names]
    ax.legend(handles=handles, loc=loc, ncol=ncol, fontsize=8.5)


def save(fig, name: str):
    OUT.mkdir(parents=True, exist_ok=True)
    fig.savefig(OUT / name, bbox_inches="tight")
    plt.close(fig)
    print(f"[charts] wrote {name}", flush=True)


def main() -> int:
    pm, port, bench, regimes, eps, corr_full, corr_roll, risk, var, summary = load()
    span = f"{pm.index.min():%b %Y} – {pm.index.max():%b %Y}"

    # ---- 1. Cumulative returns ------------------------------------------------ #
    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.plot(port.index, port["port_index"], color=CLASS_COLORS["Portfolio"], lw=1.9, label="Equal-weighted portfolio (18 assets)")
    ax.plot(bench.index, bench["qqq_index"], color="#546e7a", lw=1.3, ls="--", label="QQQ")
    ax.plot(bench.index, bench["spy_index"], color="#90a4ae", lw=1.3, ls=":", label="SPY")
    ax.set_title(f"Cumulative return — $100 invested, {span}")
    ax.set_ylabel("Index (start = 100)")
    ax.legend(loc="upper left")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=9))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))
    regime_bands(ax, eps)
    save(fig, "cumulative_returns.png")

    # ---- 2. Portfolio drawdown -------------------------------------------------- #
    fig, ax = plt.subplots(figsize=(11, 4.2))
    dd = port["port_drawdown"]
    ax.fill_between(dd.index, dd * 100, 0, color="#c62828", alpha=0.55, lw=0)
    ax.plot(dd.index, dd * 100, color="#b71c1c", lw=1.0)
    worst = dd.idxmin()
    ax.annotate(f"Max drawdown {dd.min():.1%}\n({worst:%b %d, %Y})",
                xy=(worst, dd.min() * 100), xytext=(12, -8), textcoords="offset points",
                fontsize=9, fontweight="bold", color="#b71c1c")
    ax.set_title(f"Portfolio drawdown from running peak, {span}")
    ax.set_ylabel("Drawdown (%)")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=9))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))
    regime_bands(ax, eps)
    regime_legend(ax, eps)
    save(fig, "drawdown.png")

    # ---- 3. Volatility + regime timeline ------------------------------------------ #
    fig, ax = plt.subplots(figsize=(11.5, 4.6))
    v = pm["port_vol30"]
    ax.plot(v.index, v * 100, color="#37474f", lw=1.8, label="Portfolio 30-day vol (annualized)")
    regime_bands(ax, eps, alpha=0.22)
    ax.set_title(f"Volatility regimes — 30-day portfolio volatility with K-Means regime bands, {span}")
    ax.set_ylabel("Annualized vol (%)")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=9))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))
    regime_legend(ax, eps, loc="upper right")
    save(fig, "vol_regime_timeline.png")

    # ---- 4. Rolling correlation ----------------------------------------------------- #
    fig, ax = plt.subplots(figsize=(11, 4.4))
    ax.plot(corr_roll.index, corr_roll["avg_pairwise_corr"], color="#37474f", lw=1.8,
            label="Avg pairwise correlation (90d, all 18 assets)")
    ax.plot(corr_roll.index, corr_roll["avg_corr_cross"], color="#f9a825", lw=1.5,
            label="Avg equity–crypto correlation (90d)")
    ax.set_ylim(-0.2, 1.0)
    ax.set_title(f"Diversification under stress — rolling correlations, {span}")
    ax.set_ylabel("Correlation")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=9))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))
    ax.legend(loc="upper left")
    regime_bands(ax, eps)
    save(fig, "rolling_correlation.png")

    # ---- 5. Full-sample correlation heatmap ------------------------------------------- #
    tickers = sorted(corr_full["asset1"].unique())
    matrix = corr_full.pivot(index="asset1", columns="asset2", values="correlation").reindex(index=tickers, columns=tickers)
    fig, ax = plt.subplots(figsize=(9.6, 8.2))
    im = ax.imshow(matrix.values, cmap="RdBu_r", vmin=-1, vmax=1)
    ax.set_xticks(range(len(tickers)), tickers, rotation=45, ha="right")
    ax.set_yticks(range(len(tickers)), tickers)
    for i in range(len(tickers)):
        for j in range(len(tickers)):
            val = matrix.values[i, j]
            ax.text(j, i, f"{val:.2f}", ha="center", va="center", fontsize=7.3,
                    color="white" if abs(val) > 0.6 else "#263238")
    ax.grid(False)
    ax.set_title("Full-sample correlation matrix — daily returns")
    fig.colorbar(im, ax=ax, shrink=0.75, label="Correlation")
    save(fig, "corr_heatmap.png")

    # ---- 6. Correlation by regime ------------------------------------------------------- #
    fig, ax = plt.subplots(figsize=(8.6, 4.4))
    # Order regimes by severity (avg portfolio vol): calm -> crisis
    summary = summary.sort_values("avg_port_vol30")
    names = summary["regime_name"].tolist()
    x = np.arange(len(names))
    w = 0.27
    reg_lab = regimes["regime_name_smoothed"].reindex(corr_roll.index)
    eq_corr = corr_roll.groupby(reg_lab)["avg_corr_equity"].mean().reindex(names)
    cross = corr_roll.groupby(reg_lab)["avg_corr_cross"].mean().reindex(names)
    ax.bar(x - w, summary["avg_pairwise_corr"], width=w, color="#37474f", label="Avg pairwise corr")
    ax.bar(x, eq_corr, width=w, color="#1e88e5", label="Avg equity–equity corr")
    ax.bar(x + w, cross, width=w, color="#f9a825", label="Avg equity–crypto corr")
    ax.set_xticks(x, [f"{n}\n({s:.0%} of days)" for n, s in zip(names, summary["pct_days"])])
    ax.set_ylim(0, 1)
    ax.set_title("Correlation by volatility regime — diversification decays in stress")
    ax.set_ylabel("Correlation")
    ax.legend(loc="upper left")
    save(fig, "corr_by_regime.png")

    # ---- 7. VaR backtest ------------------------------------------------------------------ #
    fig, ax = plt.subplots(figsize=(11, 4.6))
    ax.plot(port.index, port["port_index"], color=CLASS_COLORS["Portfolio"], lw=1.6, label="Portfolio (index, start=100)")
    br_dates = port.index[(port["port_return"] < -port["port_var95"]) & port["port_var95"].notna()]
    ax.scatter(br_dates, port.loc[br_dates, "port_index"], marker="v", s=26, color="#e53935",
               zorder=5, label=f"95% VaR breach ({len(br_dates)} days)")
    n_eligible = int(port["port_var95"].notna().sum())
    if n_eligible:
        ax.set_title(f"95% historical VaR backtest — {len(br_dates)} breaches in {n_eligible} days "
                     f"({len(br_dates)/n_eligible:.1%} vs 5% expected)")
    else:
        ax.set_title("95% historical VaR backtest")
    ax.set_ylabel("Index (start = 100)")
    ax.legend(loc="upper left")
    ax.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=9))
    ax.xaxis.set_major_formatter(mdates.ConciseDateFormatter(mdates.AutoDateLocator()))
    save(fig, "var_backtest.png")

    # ---- 8. Risk / return map ---------------------------------------------------------------- #
    r = risk[risk["asset_class"].isin(["Equity", "Crypto"])]
    fig, ax = plt.subplots(figsize=(9.6, 6.2))
    for cls, g in r.groupby("asset_class"):
        sizes = (g["max_drawdown"].abs() * 420 + 20)
        ax.scatter(g["ann_vol"] * 100, g["cagr"] * 100, s=sizes, alpha=0.75,
                   color=CLASS_COLORS[cls], edgecolor="white", lw=0.8, label=cls)
        for _, row in g.iterrows():
            ax.annotate(row["ticker"], (row["ann_vol"] * 100, row["cagr"] * 100),
                        xytext=(5, 4), textcoords="offset points", fontsize=8.5, fontweight="bold",
                        color="#263238")
    ax.axhline(0, color="#90a4ae", lw=0.8, ls="--")
    ax.set_xlabel("Annualized volatility (%)")
    ax.set_ylabel("CAGR (%)")
    ax.set_title("Risk / return map — bubble size = max drawdown")
    ax.legend(loc="best")
    save(fig, "risk_return_map.png")

    print("[charts] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
