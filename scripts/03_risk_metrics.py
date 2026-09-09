#!/usr/bin/env python3
"""
Step 3 — Risk metrics.

Input : data/processed/{prices_wide,returns_wide,portfolio_daily,macro_daily}.csv
Output: data/processed/
    rolling_vol.csv       — long: date, ticker, vol30, vol90 (annualized)
    drawdown.csv          — long: date, ticker, drawdown
    var_daily.csv         — long: date, ticker, return, var95, var99, breach flags
    corr_full.csv         — full-sample correlation matrix (long format)
    corr_rolling.csv      — date, avg pairwise corr + block correlations
    portfolio_metrics.csv — portfolio-level daily risk series
    risk_summary.csv      — per-asset summary table (one row per asset)

Conventions
-----------
* Volatility: rolling std of daily returns x sqrt(252), annualized.
* Historical VaR (non-parametric, percentile method): the p-th percentile of
  the trailing `var_window` (250d ~ 1y) daily returns. Reported as a positive
  loss magnitude: var95 = 2.1% means "on 5% of days we expect to lose more
  than 2.1%". A breach is a daily return strictly below -var95.
  No parametric or Monte Carlo methods — per spec.
* Drawdown: price / running max - 1, on adjusted (total-return) prices.
* Sharpe ratio uses rf = 0 (excess-return-to-vol ratio); documented in README.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
PROC = REPO / "data" / "processed"

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


def load_config() -> dict:
    with open(REPO / "config" / "portfolio.toml", "rb") as fh:
        return tomllib.load(fh)


def max_drawdown_details(prices: pd.Series) -> dict:
    """Return max drawdown plus peak/trough/recovery dates for one asset."""
    running_max = prices.cummax()
    dd = prices / running_max - 1.0
    trough = dd.idxmin()
    mdd = dd.loc[trough]
    peak = prices.loc[:trough].idxmax()
    after = prices.loc[trough:]
    recovery = after[after >= prices.loc[peak]].index.min()
    return {
        "max_drawdown": float(mdd),
        "dd_peak_date": peak,
        "dd_trough_date": trough,
        "dd_recovery_date": recovery,  # NaT if still underwater
    }


def main() -> int:
    cfg = load_config()
    equities = list(cfg["universe"]["equities"])
    crypto = list(cfg["universe"]["crypto"])
    assets = equities + crypto
    vol_windows = list(cfg["risk"]["vol_windows"])
    corr_window = int(cfg["risk"]["corr_window"])
    var_levels = list(cfg["risk"]["var_levels"])
    var_window = int(cfg["risk"]["var_window"])
    td = int(cfg["risk"]["trading_days"])

    prices = pd.read_csv(PROC / "prices_wide.csv", index_col="date", parse_dates=True)[assets]
    returns = pd.read_csv(PROC / "returns_wide.csv", index_col="date", parse_dates=True)[assets]
    port = pd.read_csv(PROC / "portfolio_daily.csv", index_col="date", parse_dates=True)

    # ---- Rolling volatility (long) ----------------------------------------- #
    # One row per (date, ticker) with a vol30 / vol90 column per window
    per = []
    for w in vol_windows:
        v = (returns.rolling(w, min_periods=w).std() * np.sqrt(td)).stack(future_stack=True)
        per.append(v.rename(f"vol{w}"))
    vol_long_frame = pd.concat(per, axis=1).reset_index()
    vol_long_frame = vol_long_frame.rename(columns={vol_long_frame.columns[1]: "ticker"})
    vol_long_frame.to_csv(PROC / "rolling_vol.csv", index=False)

    # ---- Drawdown (long) ---------------------------------------------------- #
    dd = prices / prices.cummax() - 1.0
    dd_long = dd.stack(future_stack=True).rename("drawdown").reset_index()
    dd_long.columns = ["date", "ticker", "drawdown"]
    dd_long.to_csv(PROC / "drawdown.csv", index=False)

    # ---- Historical VaR + breaches (long) ----------------------------------- #
    var_frames = []
    for level in var_levels:
        q = returns.rolling(var_window, min_periods=var_window).quantile(1 - level)
        var_frames.append((-q).rename(f"var{int(level*100)}"))
    var_levels_frame = pd.concat(var_frames, axis=1)
    ret_long = returns.stack(future_stack=True).rename("daily_return")
    var_long = pd.concat([ret_long, var_levels_frame], axis=1).reset_index()
    var_long = var_long.rename(columns={"level_1": "ticker"})
    for level in var_levels:
        tag = int(level * 100)
        var_long[f"breach{tag}"] = var_long["daily_return"] < -var_long[f"var{tag}"]
    var_long.to_csv(PROC / "var_daily.csv", index=False)

    # ---- Correlations -------------------------------------------------------- #
    corr_full = returns.corr()
    corr_full.index.name = "asset1"
    corr_full_long = corr_full.stack(future_stack=True).rename("correlation").reset_index()
    corr_full_long.columns = ["asset1", "asset2", "correlation"]
    corr_full_long.to_csv(PROC / "corr_full.csv", index=False)

    # Rolling average pairwise correlation (diversification gauge)
    eq_mask = [a for a in assets if a in equities]
    cr_mask = [a for a in assets if a in crypto]

    def _pairwise_mean(cmat: pd.DataFrame, rows: list[str], cols: list[str]) -> float:
        sub = cmat.loc[rows, cols]
        if rows == cols:
            off = sub.values[~np.eye(len(rows), dtype=bool)]
            return float(np.nanmean(off))
        return float(sub.stack(future_stack=True).mean())

    roll_corr = returns.rolling(corr_window, min_periods=corr_window).corr()
    dates = roll_corr.index.get_level_values(0).unique()
    rows = []
    for d in dates:
        cmat = roll_corr.loc[d]
        if cmat.isna().all().all():
            continue
        rows.append({
            "date": d,
            "avg_pairwise_corr": _pairwise_mean(cmat, assets, assets),
            "avg_corr_equity": _pairwise_mean(cmat, eq_mask, eq_mask),
            "avg_corr_cross": _pairwise_mean(cmat, eq_mask, cr_mask),
            "avg_corr_crypto": _pairwise_mean(cmat, cr_mask, cr_mask),
        })
    corr_roll = pd.DataFrame(rows).set_index("date")
    corr_roll.to_csv(PROC / "corr_rolling.csv")

    # ---- Portfolio metrics ---------------------------------------------------- #
    port_ret = port["port_return"]
    pm = pd.DataFrame(index=port.index)
    pm["port_return"] = port_ret
    pm["port_index"] = port["port_index"]
    pm["port_drawdown"] = port["port_drawdown"]
    for w in vol_windows:
        pm[f"port_vol{w}"] = port_ret.rolling(w, min_periods=w).std() * np.sqrt(td)
    for level in var_levels:
        q = port_ret.rolling(var_window, min_periods=var_window).quantile(1 - level)
        pm[f"port_var{int(level*100)}"] = -q
    pm = pm.join(corr_roll, how="left")
    # Cross-sectional vol features (regime inputs)
    vol30_wide = returns.rolling(30, min_periods=30).std() * np.sqrt(td)
    pm["avg_vol30"] = vol30_wide.mean(axis=1, skipna=True)
    pm["vol30_dispersion"] = vol30_wide.std(axis=1, skipna=True)
    pm.to_csv(PROC / "portfolio_metrics.csv")

    # ---- Per-asset summary ------------------------------------------------------ #
    meta = cfg["universe"]["asset_meta"]
    summary_rows = []
    n_days = len(returns)
    for a in assets:
        r = returns[a].dropna()
        p = prices[a].dropna()
        cagr = (p.iloc[-1] / p.iloc[0]) ** (td / max(len(p) - 1, 1)) - 1.0
        vol = r.std() * np.sqrt(td)
        sharpe = (r.mean() * td) / vol if vol > 0 else np.nan
        mdd = max_drawdown_details(p)
        row = {
            "ticker": a,
            "name": meta.get(a, {}).get("name", a),
            "asset_class": "Crypto" if a in crypto else "Equity",
            "cagr": cagr,
            "ann_vol": vol,
            "sharpe_rf0": sharpe,
            "var95_current": float(-r.tail(var_window).quantile(0.05)),
            "var99_current": float(-r.tail(var_window).quantile(0.01)),
            "var95_full": float(-r.quantile(0.05)),
            "var99_full": float(-r.quantile(0.01)),
            "vol30_current": float(r.tail(30).std() * np.sqrt(td)),
            "worst_day": float(r.min()),
            "best_day": float(r.max()),
            **{k: (v.isoformat() if pd.notna(v) else None) for k, v in mdd.items()},
        }
        summary_rows.append(row)
    summary = pd.DataFrame(summary_rows)
    summary.to_csv(PROC / "risk_summary.csv", index=False)

    # Portfolio summary row appended for convenience tables
    pr = port_ret.dropna()
    port_row = {
        "ticker": "PORTFOLIO",
        "name": "Equal-Weighted Portfolio",
        "asset_class": "Portfolio",
        "cagr": (port["port_index"].iloc[-1] / port["port_index"].iloc[0]) ** (td / max(n_days - 1, 1)) - 1.0,
        "ann_vol": pr.std() * np.sqrt(td),
        "sharpe_rf0": pr.mean() * td / (pr.std() * np.sqrt(td)),
        "var95_current": float(-pr.tail(var_window).quantile(0.05)),
        "var99_current": float(-pr.tail(var_window).quantile(0.01)),
        "var95_full": float(-pr.quantile(0.05)),
        "var99_full": float(-pr.quantile(0.01)),
        "vol30_current": float(pr.tail(30).std() * np.sqrt(td)),
        "worst_day": float(pr.min()),
        "best_day": float(pr.max()),
        **{k: (v.isoformat() if pd.notna(v) else None) for k, v in max_drawdown_details(port["port_index"]).items()},
    }
    summary = pd.concat([summary, pd.DataFrame([port_row])], ignore_index=True)
    summary.to_csv(PROC / "risk_summary.csv", index=False)

    print("[risk] wrote rolling_vol, drawdown, var_daily, corr_full, corr_rolling,")
    print("[risk]       portfolio_metrics, risk_summary", flush=True)
    last = pm.iloc[-1]
    print(f"[risk] latest portfolio vol30={last['port_vol30']:.1%}  "
          f"var95={last['port_var95']:.2%}  avg_corr={last['avg_pairwise_corr']:.2f}", flush=True)
    print("[risk] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
