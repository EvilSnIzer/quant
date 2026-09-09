#!/usr/bin/env python3
"""
Step 2 — Build the aligned returns table.

Input : data/raw/prices_portfolio.csv, prices_benchmark.csv, macro.csv
Output: data/processed/
    prices_wide.csv     — aligned adjusted closes (portfolio assets)
    returns_wide.csv    — daily simple returns (assets as columns, dates as rows)
    portfolio_daily.csv — equal-weighted portfolio series (return, index, drawdown)
    benchmark_daily.csv — SPY / QQQ returns and indexed levels
    macro_daily.csv     — macro series aligned to the same calendar

Methodology
-----------
* Calendar: the portfolio is evaluated on the **US equity trading calendar**
  (any day on which at least one equity prints a price). Crypto trades 24/7,
  so crypto prices are simply sampled on those dates; a Friday→Monday move
  lands on Monday as one (3-day) return. This keeps every metric on a single
  252-day-per-year basis. Total variance over a window is preserved; weekend
  crypto volatility is not lost, it is booked on the next trading day.
  (Trade-off documented in README "Limitations".)
* Stray single-name gaps (e.g. a listing holiday) are forward-filled up to
  5 days; assets missing entirely are dropped with a loud warning.
* The portfolio is **equal-weighted and daily-rebalanced**: each day's
  portfolio return is the mean of that day's available asset returns.
  This is the standard convention for "equal-weighted" analytics and avoids
  drift-driven rebalancing effects polluting the risk numbers.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "raw"
OUT = REPO / "data" / "processed"

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
    portfolio_assets = equities + crypto

    prices = pd.read_csv(RAW / "prices_portfolio.csv", index_col="date", parse_dates=True)
    bench = pd.read_csv(RAW / "prices_benchmark.csv", index_col="date", parse_dates=True)

    # ---- Equity trading calendar ------------------------------------------- #
    equity_prices = prices[equities]
    traded = equity_prices.notna().any(axis=1)
    calendar = prices.index[traded]
    print(f"[returns] equity trading calendar: {len(calendar)} days "
          f"({calendar.min().date()} → {calendar.max().date()})", flush=True)

    aligned = prices.reindex(calendar)
    # Forward-fill stray gaps (never fill leading NaNs = pre-listing)
    aligned = aligned.ffill(limit=5)

    missing = [t for t in portfolio_assets if aligned[t].notna().sum() == 0]
    if missing:
        print(f"[returns] ERROR: assets with no data on calendar: {missing}", file=sys.stderr)
        return 1
    # Drop rows that are entirely NaN (defensive)
    aligned = aligned.dropna(how="all")

    # ---- Returns ------------------------------------------------------------ #
    returns = aligned.pct_change()
    returns = returns.dropna(how="all")
    # A 0 return on day one is meaningless; pct_change's first row is NaN already.
    # Infinite returns (price 0 glitches) -> NaN
    returns = returns.replace([np.inf, -np.inf], np.nan)

    gaps = returns.isna().sum().sort_values(ascending=False)
    if gaps.max() > 0:
        print(f"[returns] note: residual NaNs per asset (kept, excluded pairwise):\n{gaps[gaps>0]}", flush=True)

    # ---- Equal-weighted daily-rebalanced portfolio -------------------------- #
    port_ret = returns[portfolio_assets].mean(axis=1, skipna=True).rename("port_return")
    initial = float(cfg["portfolio"]["initial_capital"])
    port_index = (initial * (1 + port_ret).cumprod()).rename("port_index")
    port_dd = (port_index / port_index.cummax() - 1.0).rename("port_drawdown")
    portfolio = pd.concat([port_ret, port_index, port_dd], axis=1)

    # ---- Benchmarks ---------------------------------------------------------- #
    bench_aligned = bench.reindex(calendar).ffill(limit=5)
    bench_ret = bench_aligned.pct_change()
    bench_out = pd.DataFrame(index=calendar)
    for t in bench_ret.columns:
        bench_out[f"{t.lower()}_return"] = bench_ret[t]
        bench_out[f"{t.lower()}_index"] = initial * (1 + bench_ret[t].fillna(0)).cumprod()

    # ---- Macro aligned ------------------------------------------------------- #
    if (RAW / "macro.csv").exists():
        macro = pd.read_csv(RAW / "macro.csv", index_col="date", parse_dates=True)
        macro = macro.reindex(calendar).ffill(limit=10)
    else:
        macro = pd.DataFrame(index=calendar)

    # ---- Write ---------------------------------------------------------------- #
    OUT.mkdir(parents=True, exist_ok=True)
    aligned.index.name = "date"
    returns.index.name = "date"
    aligned.to_csv(OUT / "prices_wide.csv")
    returns.to_csv(OUT / "returns_wide.csv")
    portfolio.to_csv(OUT / "portfolio_daily.csv")
    bench_out.to_csv(OUT / "benchmark_daily.csv")
    macro.to_csv(OUT / "macro_daily.csv")

    print(f"[returns] wrote returns_wide.csv {returns.shape[0]}x{returns.shape[1]}", flush=True)
    ann = returns[portfolio_assets].mean() * 252
    print(f"[returns] full-sample annualized mean returns:\n{ann.round(3)}", flush=True)
    print("[returns] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
