#!/usr/bin/env python3
"""CI smoke test: can this environment fetch from Yahoo Finance?

Writes data/.actions_smoke_test.csv on success. Used by the (temporary)
data-pipeline-test workflow to validate Actions-based data fetching.
"""

from __future__ import annotations

import sys
import time
import traceback
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / ".actions_smoke_test.csv"


def main() -> int:
    import yfinance as yf

    print(f"yfinance {yf.__version__}", flush=True)
    last_err: Exception | None = None
    for attempt in range(1, 4):
        try:
            df = yf.download("AAPL", period="5d", interval="1d",
                             progress=False, auto_adjust=True)
            print(f"attempt {attempt}: shape={getattr(df, 'shape', None)}", flush=True)
            if df is None or df.empty:
                raise RuntimeError("empty frame")
            print(df.tail(3), flush=True)
            OUT.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(OUT)
            print("FETCH_OK", flush=True)
            return 0
        except SystemExit:
            raise
        except Exception as err:  # noqa: BLE001
            last_err = err
            traceback.print_exc()
            if attempt < 3:
                time.sleep(10 * attempt)
    print(f"FETCH_FAILED: {last_err}", flush=True)
    return 1


if __name__ == "__main__":
    sys.exit(main())
