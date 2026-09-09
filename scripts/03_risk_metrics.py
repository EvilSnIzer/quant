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
* VaR is **lagged** by `var_lag_days` (default 1): the VaR attributed to day t
  is estimated on returns up to day t-1 only. A risk limit must be knowable
  before the day it governs; if today's own return sits inside today's
  quantile window, a large loss drags the 5th percentile further down and the
  breach is (by construction) harder to record — the backtest is then
  in-sample and biased flattering. `var95_full`/`var99_full` stay un-lagged
  because they are full-sample descriptive statistics, not a forecast.
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


def historical_var(returns: pd.Series | pd.DataFrame, window: int, level: float,
                   lag: int = 1) -> pd.Series | pd.DataFrame:
    """Trailing-window historical VaR at `level`, as a positive loss magnitude.

    The quantile at index t is taken over [t-window-lag+1, t-lag], i.e. the
    `lag` most recent observations are excluded so that the value assigned to
    day t only uses information available at the start of day t.
    """
    q = returns.rolling(window, min_periods=window).quantile(1.0 - level)
    if lag:
        q = q.shift(lag)
    return -q


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
    # Days by which the VaR estimate is lagged (risk must be knowable pre-trade).
    var_lag = int(cfg["risk"].get("var_lag_days", 1))
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
    # Rolling quantiles are computed wide (date x ticker), one frame per level,
    # then stacked into a single long (date, ticker) table. Each value is lagged
    # by var_lag days: see historical_var().
    var_wide = pd.concat(
        {
            f"var{int(level * 100)}": historical_var(returns, var_window, level, var_lag)
            for level in var_levels
        },
        axis=1,
    )
    ret_long = returns.stack(future_stack=True).rename("daily_return")
    var_long = var_wide.stack(future_stack=True).join(ret_long).reset_index()
    var_long.columns = ["date", "ticker"] + list(var_long.columns[2:])
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
        pm[f"port_var{int(level*100)}"] = historical_var(port_ret, var_window, level, var_lag)
    pm = pm.join(corr_roll, how="left")
    # Cross-sectional vol features (regime inputs)
    vol30_wide = returns.rolling(30, min_periods=30).std() * np.sqrt(td)
    pm["avg_vol30"] = vol30_wide.mean(axis=1, skipna=True)
    pm["vol30_dispersion"] = vol30_wide.std(axis=1, skipna=True)
    pm.to_csv(PROC / "portfolio_metrics.csv")

    # ---- Per-asset summary ------------------------------------------------------ #
    meta = cfg["universe"]["asset_meta"]

    def _summary_row(ticker: str, name: str, asset_class: str,
                     r: pd.Series, p: pd.Series, n_days: int) -> dict:
        cagr = (p.iloc[-1] / p.iloc[0]) ** (td / max(len(p) - 1, 1)) - 1.0
        vol = r.std() * np.sqrt(td)
        sharpe = (r.mean() * td) / vol if vol > 0 else np.nan
        mdd = max_drawdown_details(p)
        # "current" = the trailing-window VaR that would govern the next session,
        # i.e. estimated through the previous trading day (same lag as the daily
        # series). "full" = descriptive percentile over the whole sample, un-lagged.
        cur = {
            f"var{int(level * 100)}_current": historical_var(r, var_window, level, var_lag).dropna()
            for level in var_levels
        }

        def _cur(level: float) -> float:
            s = cur[f"var{int(level * 100)}_current"]
            return float(s.iloc[-1]) if len(s) else float("nan")

        return {
            "ticker": ticker,
            "name": name,
            "asset_class": asset_class,
            "cagr": cagr,
            "ann_vol": vol,
            "sharpe_rf0": sharpe,
            "var95_current": _cur(0.95),
            "var99_current": _cur(0.99),
            "var95_full": float(-r.quantile(0.05)),
            "var99_full": float(-r.quantile(0.01)),
            "vol30_current": float(r.tail(30).std() * np.sqrt(td)),
            "worst_day": float(r.min()),
            "best_day": float(r.max()),
            "max_drawdown": mdd["max_drawdown"],
            "dd_peak_date": mdd["dd_peak_date"].isoformat() if pd.notna(mdd["dd_peak_date"]) else None,
            "dd_trough_date": mdd["dd_trough_date"].isoformat() if pd.notna(mdd["dd_trough_date"]) else None,
            "dd_recovery_date": mdd["dd_recovery_date"].isoformat() if pd.notna(mdd["dd_recovery_date"]) else None,
        }

    summary_rows = [
        _summary_row(
            a,
            meta.get(a, {}).get("name", a),
            "Crypto" if a in crypto else "Equity",
            returns[a].dropna(),
            prices[a].dropna(),
            len(returns),
        )
        for a in assets
    ]
    summary = pd.DataFrame(summary_rows)

    # Portfolio summary row appended for convenience tables
    pr = port_ret.dropna()
    port_row = _summary_row(
        "PORTFOLIO", "Equal-Weighted Portfolio", "Portfolio",
        pr, port["port_index"], len(returns),
    )
    summary = pd.concat([summary, pd.DataFrame([port_row])], ignore_index=True)
    summary.to_csv(PROC / "risk_summary.csv", index=False)

    print("[risk] wrote rolling_vol, drawdown, var_daily, corr_full, corr_rolling,")
    print("[risk]       portfolio_metrics, risk_summary", flush=True)
    last = pm.iloc[-1]
    print(f"[risk] latest portfolio vol30={last['port_vol30']:.1%}  "
          f"var95={last['port_var95']:.2%}  avg_corr={last['avg_pairwise_corr']:.2f}", flush=True)
    # Backtest of the VaR series actually shipped: every value is lagged, so
    # these breaches are out-of-sample by construction rather than self-referential.
    elig = pm[pm["port_var95"].notna()]
    if len(elig):
        nb = int((elig["port_return"] < -elig["port_var95"]).sum())
        print(f"[risk] 95% VaR backtest: {nb} breaches in {len(elig)} days "
              f"({nb / len(elig):.2%} vs 5% expected), VaR lag={var_lag}d", flush=True)
    print("[risk] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
