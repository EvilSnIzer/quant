#!/usr/bin/env python3
"""
Step 1 — Fetch raw market data.

Pulls:
  * Daily adjusted closes for the equity sleeve, crypto sleeve and benchmarks
    from Yahoo Finance (via yfinance — no API key required).
  * Macro context series (10Y yield, VIX, broad dollar index) from FRED's
    keyless public CSV endpoint.

Writes (data/raw/):
  prices_portfolio.csv   — adjusted close, portfolio assets as columns
  prices_benchmark.csv   — adjusted close, benchmarks as columns
  macro.csv              — date, DGS10, VIXCLS, DTWEXBGS
  _fetch_manifest.json   — provenance: when/how the data was fetched

Design notes
------------
* `auto_adjust=True` gives split/dividend-adjusted closes, so returns are
  total-return-ish for equities. Crypto has no adjustments.
* FRED's fredgraph.csv endpoint is public (no key); "." marks missing days
  (holidays) and is parsed as NaN.
* The script retries transient yfinance failures and fails loudly (non-zero
  exit) if any portfolio asset comes back empty, so CI catches data problems
  instead of silently producing a broken dashboard.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO / "config" / "portfolio.toml"
RAW_DIR = REPO / "data" / "raw"

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


def load_config() -> dict:
    with open(CONFIG_PATH, "rb") as fh:
        return tomllib.load(fh)


# --------------------------------------------------------------------------- #
# Yahoo Finance
# --------------------------------------------------------------------------- #
def fetch_yahoo_closes(tickers: list[str], years: int, retries: int = 3) -> pd.DataFrame:
    """Download adjusted closes for `tickers`, wide format (dates x tickers)."""
    import yfinance as yf

    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            data = yf.download(
                tickers,
                period=f"{years}y",
                interval="1d",
                auto_adjust=True,
                progress=False,
                group_by="column",
                threads=True,
            )
            if data is None or data.empty:
                raise RuntimeError("yfinance returned an empty frame")
            # group_by="column" -> MultiIndex (field, ticker); take Close
            if isinstance(data.columns, pd.MultiIndex):
                closes = data["Close"].copy()
            else:  # single ticker -> plain columns
                closes = data[["Close"]].rename(columns={"Close": tickers[0]})
            closes = closes[tickers]  # requested order; missing tickers -> NaN cols
            return closes
        except Exception as err:  # noqa: BLE001 - retry any transient failure
            last_err = err
            print(f"[fetch] yfinance attempt {attempt}/{retries} failed: {err}", flush=True)
            if attempt < retries:
                time.sleep(10 * attempt)
    raise RuntimeError(f"yfinance failed after {retries} attempts: {last_err}")


# --------------------------------------------------------------------------- #
# FRED (keyless public CSV endpoint)
# --------------------------------------------------------------------------- #
def fetch_fred_series(series_id: str) -> pd.Series:
    """Pull one series from FRED's public CSV endpoint (no API key)."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    df = pd.read_csv(url, na_values=["."])
    # fredgraph.csv: columns are literally "DATE" and "<series_id>"
    date_col = df.columns[0]
    df[date_col] = pd.to_datetime(df[date_col])
    df = df.set_index(date_col).sort_index()
    return df[series_id].astype(float)


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Fetch raw market data")
    parser.add_argument(
        "--years", type=int, default=None,
        help="Override the lookback window from config",
    )
    args = parser.parse_args()

    cfg = load_config()
    years = args.years or cfg["window"]["years"]
    equities = list(cfg["universe"]["equities"])
    crypto = list(cfg["universe"]["crypto"])
    benchmarks = list(cfg["universe"]["benchmarks"])
    portfolio_assets = equities + crypto
    macro_series = dict(cfg["macro"])

    RAW_DIR.mkdir(parents=True, exist_ok=True)

    # ---- Prices (single batched download) --------------------------------- #
    print(f"[fetch] Yahoo Finance: {len(portfolio_assets)} portfolio assets "
          f"+ {len(benchmarks)} benchmarks, {years}y daily", flush=True)
    all_closes = fetch_yahoo_closes(portfolio_assets + benchmarks, years)

    prices_portfolio = all_closes[portfolio_assets]
    prices_benchmark = all_closes[benchmarks]

    empty = [t for t in portfolio_assets if t not in prices_portfolio.columns]
    empty += [t for t in portfolio_assets + benchmarks
              if t in all_closes.columns and all_closes[t].notna().sum() == 0]
    if empty:
        print(f"[fetch] ERROR: no data returned for {empty}", file=sys.stderr)
        return 1

    for name, frame in [("prices_portfolio.csv", prices_portfolio),
                        ("prices_benchmark.csv", prices_benchmark)]:
        frame.index.name = "date"
        frame.to_csv(RAW_DIR / name)
        print(f"[fetch] wrote {name}: {frame.shape[0]} rows x {frame.shape[1]} cols, "
              f"{frame.index.min().date()} → {frame.index.max().date()}", flush=True)

    # ---- Macro ------------------------------------------------------------- #
    macro = {}
    for series_id in macro_series:
        try:
            macro[series_id] = fetch_fred_series(series_id)
            print(f"[fetch] FRED {series_id}: {macro[series_id].notna().sum()} observations", flush=True)
        except Exception as err:  # noqa: BLE001 - macro is optional context
            print(f"[fetch] WARN: FRED {series_id} failed ({err}); continuing without it", flush=True)
    if macro:
        macro_df = pd.DataFrame(macro)
        macro_df.index.name = "date"
        macro_df.to_csv(RAW_DIR / "macro.csv")
    else:
        print("[fetch] WARN: no macro series fetched; writing empty macro.csv", flush=True)
        pd.DataFrame(index=pd.DatetimeIndex([], name="date")).to_csv(RAW_DIR / "macro.csv")

    # ---- Provenance manifest ------------------------------------------------ #
    import yfinance
    manifest = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "Yahoo Finance (yfinance) + FRED fredgraph.csv",
        "yfinance_version": yfinance.__version__,
        "pandas_version": pd.__version__,
        "years": years,
        "portfolio_assets": portfolio_assets,
        "benchmarks": benchmarks,
        "macro_series": list(macro_series),
        "price_range": {
            "start": str(all_closes.index.min().date()),
            "end": str(all_closes.index.max().date()),
            "rows": int(all_closes.shape[0]),
        },
    }
    with open(RAW_DIR / "_fetch_manifest.json", "w") as fh:
        json.dump(manifest, fh, indent=2)
    print("[fetch] wrote _fetch_manifest.json", flush=True)
    print("[fetch] done", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
