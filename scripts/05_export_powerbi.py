#!/usr/bin/env python3
"""
Step 5 — Shape tables for Power BI.

Reads the processed analytics tables and writes a tidy, import-ready set of
CSVs to data/processed/powerbi/ (one table per file, star-schema-ish):

  dim_date.csv          — calendar dimension
  dim_asset.csv         — asset dimension (name, class, sector)
  fact_returns.csv      — one row per (date, ticker): return, rolling vol,
                          drawdown, trailing VaR, breach flags
  portfolio_daily.csv   — daily portfolio series + benchmarks + macro +
                          regime label and days-in-regime
  corr_long.csv         — correlation matrices: "Full sample" + one per
                          regime (drives the heatmap with a regime slicer)
  regime_episodes.csv   — contiguous regime spells
  regime_summary.csv    — per-regime stats (days, episodes, avg vol/corr)
  risk_summary.csv      — per-asset summary (KPI cards / tables)

Design notes
------------
* Correlation matrices cannot be computed in DAX at matrix scale, so they
  are precomputed per regime here; the Power BI slicer just filters the
  `scope` column.
* `days_in_regime` is precomputed so "time in current regime" is a single
  measure instead of a window-function gymnastics in DAX.
* Dates are ISO yyyy-mm-dd; booleans are TRUE/FALSE literals (Power BI
  parses both natively).
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
PROC = REPO / "data" / "processed"
OUT = PROC / "powerbi"

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


def load_config() -> dict:
    with open(REPO / "config" / "portfolio.toml", "rb") as fh:
        return tomllib.load(fh)


def main() -> int:
    cfg = load_config()
    equities = list(cfg["universe"]["equities"])
    crypto = list(cfg["universe"]["crypto"])
    assets = equities + crypto
    benchmarks = list(cfg["universe"]["benchmarks"])
    meta = cfg["universe"]["asset_meta"]

    prices = pd.read_csv(PROC / "prices_wide.csv", index_col="date", parse_dates=True)
    returns = pd.read_csv(PROC / "returns_wide.csv", index_col="date", parse_dates=True)
    pm = pd.read_csv(PROC / "portfolio_metrics.csv", index_col="date", parse_dates=True)
    regimes = pd.read_csv(PROC / "regimes.csv", index_col="date", parse_dates=True)
    vol = pd.read_csv(PROC / "rolling_vol.csv", parse_dates=["date"])
    dd = pd.read_csv(PROC / "drawdown.csv", parse_dates=["date"])
    var = pd.read_csv(PROC / "var_daily.csv", parse_dates=["date"])
    bench = pd.read_csv(PROC / "benchmark_daily.csv", index_col="date", parse_dates=True)
    macro = pd.read_csv(PROC / "macro_daily.csv", index_col="date", parse_dates=True)
    episodes = pd.read_csv(PROC / "regime_episodes.csv", parse_dates=["start_date", "end_date"])

    OUT.mkdir(parents=True, exist_ok=True)

    # ---- dim_date ----------------------------------------------------------- #
    all_dates = pd.Series(pd.date_range(prices.index.min(), prices.index.max(), freq="D"))
    dim_date = pd.DataFrame({
        "date": all_dates,
        "year": all_dates.dt.year,
        "quarter": all_dates.dt.quarter,
        "month": all_dates.dt.month,
        "month_name": all_dates.dt.strftime("%b"),
        "day": all_dates.dt.day,
        "weekday": all_dates.dt.day_name(),
        "week_of_year": all_dates.dt.isocalendar().week.astype(int),
    })
    dim_date.to_csv(OUT / "dim_date.csv", index=False)

    # ---- dim_asset ----------------------------------------------------------- #
    rows = []
    for t in assets + benchmarks:
        m = meta.get(t, {})
        rows.append({
            "ticker": t,
            "name": m.get("name", t),
            "asset_class": "Crypto" if t in crypto else ("Benchmark" if t in benchmarks else "Equity"),
            "sector": m.get("sector", "n/a"),
            "industry": m.get("industry", "n/a"),
            "in_portfolio": t in assets,
        })
    pd.DataFrame(rows).to_csv(OUT / "dim_asset.csv", index=False)

    # ---- fact_returns --------------------------------------------------------- #
    fact = (
        var.merge(vol, on=["date", "ticker"], how="left")
        .merge(dd, on=["date", "ticker"], how="left")
    )
    fact = fact[["date", "ticker", "daily_return", "vol30", "vol90",
                 "drawdown", "var95", "var99", "breach95", "breach99"]]
    # Breach flags as 1/0 integers — unambiguous for Power BI and DAX-friendly
    for col in ("breach95", "breach99"):
        fact[col] = fact[col].fillna(False).astype(int)
    fact.to_csv(OUT / "fact_returns.csv", index=False)

    # ---- portfolio_daily -------------------------------------------------------- #
    port = pm.join(bench, how="left").join(macro, how="left")
    reg = regimes[["regime_id", "regime_name", "regime_name_smoothed"]].copy()
    port = port.join(reg, how="left")

    # days_in_regime: 1..n within each contiguous smoothed episode
    lab = port["regime_name_smoothed"]
    ep_id = (lab.ne(lab.shift())).cumsum()
    days_in = lab.groupby(ep_id).cumcount() + 1
    port["days_in_regime"] = days_in.where(lab.notna())
    # Portfolio-level VaR breach flags (1/0) for DAX breach counts
    for lvl in ("95", "99"):
        port[f"port_breach{lvl}"] = (
            (port["port_return"] < -port[f"port_var{lvl}"])
            & port[f"port_var{lvl}"].notna()
        ).astype(int)
    port.to_csv(OUT / "portfolio_daily.csv")

    # ---- corr_long: full sample + per regime -------------------------------------- #
    corr_frames = []
    full = returns.corr()
    long = full.stack(future_stack=True).rename("correlation").reset_index()
    long.columns = ["asset1", "asset2", "correlation"]
    long.insert(0, "scope", "Full sample")
    corr_frames.append(long)

    for name in episodes["regime_name"].unique():
        dates = regimes.index[regimes["regime_name_smoothed"] == name]
        sub = returns.loc[returns.index.isin(dates)]
        if len(sub) < 30:
            continue
        c = sub.corr()
        lo = c.stack(future_stack=True).rename("correlation").reset_index()
        lo.columns = ["asset1", "asset2", "correlation"]
        lo.insert(0, "scope", name)
        corr_frames.append(lo)
    corr_long = pd.concat(corr_frames, ignore_index=True)
    corr_long = corr_long[corr_long["asset1"] != corr_long["asset2"]]
    corr_long.to_csv(OUT / "corr_long.csv", index=False)

    # ---- episodes / summaries ------------------------------------------------------ #
    episodes.to_csv(OUT / "regime_episodes.csv", index=False)
    pd.read_csv(PROC / "regime_summary.csv").to_csv(OUT / "regime_summary.csv", index=False)
    pd.read_csv(PROC / "risk_summary.csv").to_csv(OUT / "risk_summary.csv", index=False)

    # ---- report ---------------------------------------------------------------------- #
    for f in sorted(OUT.glob("*.csv")):
        n = len(pd.read_csv(f))
        print(f"[powerbi] {f.name}: {n} rows", flush=True)
    print("[powerbi] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
