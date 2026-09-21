# KPI catalog — Portfolio Risk & Regime Dashboard

This is the KPI dictionary for the three-page Power BI dashboard. The visual
preview is [`dashboard_mockup.html`](dashboard_mockup.html); the live report is
built from the CSV model described in [`BUILD_GUIDE.md`](BUILD_GUIDE.md).

The current snapshot used by the preview ends **2026-09-14**. Percentages in the
CSV model are stored as decimals: `0.207` is displayed as `20.7%`.

## At-a-glance KPI set

| Dashboard card | Current snapshot | Unit | Decision question |
|---|---:|---|---|
| Portfolio CAGR | 21.3% | annual return | What compounded return did the equal-weighted book produce? |
| 30-day volatility | 20.7% | annualized volatility | How much is the portfolio moving now? |
| 1-day historical VaR, 95% | 2.6% | loss magnitude | What loss is exceeded on roughly 5% of comparable days? |
| Current drawdown | −0.4% | loss from peak | How far below its running high is the portfolio today? |
| Maximum drawdown | −53.3% | peak-to-trough loss | What was the deepest historical loss? |
| Current regime | Low · 37 days | state / trading days | What volatility state is active, and how long has it lasted? |
| 95% VaR breach rate | 4.4% | realized rate | Is the VaR model behaving close to its 5% design level? |

The snapshot is data-driven and will change after the pipeline is refreshed;
these values are not hard-coded into the analytics layer.

---

## Page 1 — Portfolio overview

### Portfolio performance and risk cards

| KPI | Formula | Power BI implementation / source |
|---|---|---|
| **Range Return %** | `Last portfolio index / First portfolio index − 1` | `portfolio_daily[port_index]`; use the first and last visible dates. |
| **Portfolio CAGR** | `(Ending index / Beginning index) ^ (252 / trading days) − 1` | `risk_summary[cagr]` for the portfolio row. Python implementation: `scripts/03_risk_metrics.py::_summary_row`. |
| **QQQ Range Return %** | `Last QQQ index / First QQQ index − 1` | `portfolio_daily[qqq_index]`. |
| **Excess vs QQQ %** | `Portfolio range return − QQQ range return` | `[Range Return %] - [QQQ Range Return %]`. |
| **Annualized volatility** | `STDEV.S(daily returns) × √252` | `fact_returns[daily_return]` for an arbitrary asset filter, or `portfolio_daily[port_return]` for the portfolio. `vol30` and `vol90` use rolling windows of 30 and 90 trading days. |
| **Current 30D Vol %** | `STDEV.S(last 30 portfolio returns) × √252` | Latest non-blank `portfolio_daily[port_vol30]`; DAX measure: `[Current 30D Vol %]`. |
| **Current VaR 95%** | `−PERCENTILE.INC(last 250 portfolio returns, 5%)` | Latest `portfolio_daily[port_var95]`. The result is a **positive loss magnitude**, so 2.6% means a return below −2.6% is a breach. |
| **Current VaR 99%** | `−PERCENTILE.INC(last 250 portfolio returns, 1%)` | Latest `portfolio_daily[port_var99]`. |
| **Current drawdown** | `Current index / Running maximum index − 1` | Latest `portfolio_daily[port_drawdown]`. |
| **Maximum drawdown** | `MIN(all portfolio drawdowns)` | `MIN(portfolio_daily[port_drawdown])`; the associated peak/trough dates are in `risk_summary`. |
| **Worst day %** | `MIN(daily portfolio returns)` | `MIN(portfolio_daily[port_return])`. |
| **Best day %** | `MAX(daily portfolio returns)` | `MAX(portfolio_daily[port_return])`. |
| **Rolling 30D average daily return** | `AVERAGE(last 30 calendar/trading observations shown by the visual)` | `AVERAGE(portfolio_daily[port_return])` over the selected date window; use as a tooltip, not a replacement for CAGR. |

### VaR backtest KPIs

A breach is defined as a realized return strictly below the negative VaR
estimate:

```text
breach95 = portfolio return < −portfolio VaR 95%
breach99 = portfolio return < −portfolio VaR 99%
```

| KPI | Formula | Interpretation |
|---|---|---|
| **Portfolio VaR Breaches 95** | `SUM(port_breach95)` | Number of realized losses beyond the 95% VaR estimate. |
| **Portfolio VaR Eligible Days** | `COUNTROWS(rows where port_var95 is not blank)` | Excludes the initial warm-up before the 250-day VaR window exists. |
| **Portfolio VaR Breach Rate 95%** | `Breaches 95 / Eligible days` | Compare with the 5.0% design level; it is a model-monitoring KPI, not a forecast. |
| **VaR Breach Excess vs 5%** | `Realized breach rate − 0.05` | Positive values mean more breaches than the nominal design level. |
| **Asset VaR Breach Rate 95%** | `SUM(fact_returns[breach95]) / COUNTROWS(rows where fact_returns[var95] is not blank)` | Same test at the selected asset/sleeve level. |

### Recommended DAX for Page 1

```DAX
Range Return % =
VAR FirstDate = MIN ( dim_date[date] )
VAR LastDate  = MAX ( dim_date[date] )
VAR FirstIdx =
    CALCULATE ( MIN ( portfolio_daily[port_index] ), portfolio_daily[date] = FirstDate )
VAR LastIdx =
    CALCULATE ( MAX ( portfolio_daily[port_index] ), portfolio_daily[date] = LastDate )
RETURN DIVIDE ( LastIdx, FirstIdx ) - 1

Current 30D Vol % =
VAR LastDate =
    CALCULATE ( MAX ( portfolio_daily[date] ), REMOVEFILTERS ( dim_date ) )
RETURN
    CALCULATE ( MAX ( portfolio_daily[port_vol30] ), portfolio_daily[date] = LastDate )

Current VaR 95 % =
VAR LastDate =
    CALCULATE ( MAX ( portfolio_daily[date] ), REMOVEFILTERS ( dim_date ) )
RETURN
    CALCULATE ( MAX ( portfolio_daily[port_var95] ), portfolio_daily[date] = LastDate )

Current Drawdown % =
VAR LastDate =
    CALCULATE ( MAX ( portfolio_daily[date] ), REMOVEFILTERS ( dim_date ) )
RETURN
    CALCULATE ( MAX ( portfolio_daily[port_drawdown] ), portfolio_daily[date] = LastDate )

Max Drawdown % = MIN ( portfolio_daily[port_drawdown] )

Portfolio VaR Breaches 95 = SUM ( portfolio_daily[port_breach95] )

Portfolio VaR Eligible Days =
CALCULATE (
    COUNTROWS ( portfolio_daily ),
    NOT ISBLANK ( portfolio_daily[port_var95] )
)

Portfolio VaR Breach Rate 95 % =
DIVIDE ( [Portfolio VaR Breaches 95], [Portfolio VaR Eligible Days] )
```

`Current` cards intentionally use the latest available portfolio date rather
than the last date selected in a date slicer. If the business requirement is a
historical as-of card, remove `REMOVEFILTERS(dim_date)` and let the slicer
control the date.

---

## Page 2 — Correlation and diversification

Correlation values are precomputed in Python because a full rolling matrix is
expensive and awkward to reproduce in a Power BI matrix. `corr_long.csv`
contains one row per scope and ordered asset pair. The scopes are `Full sample`,
`Low`, `Normal`, `Elevated`, and `Crisis`.

| KPI | Formula | Source / use |
|---|---|---|
| **Average pairwise correlation** | `mean(correlation[i,j])` for all off-diagonal asset pairs | `portfolio_daily[avg_pairwise_corr]` for the rolling line; `regime_summary[avg_pairwise_corr]` for regime cards. |
| **Average equity–crypto correlation** | `mean(correlation[equity, crypto])` | `portfolio_daily[avg_corr_cross]`; the crisis value is the stress co-movement reference. |
| **Average equity–equity correlation** | `mean(correlation[equity, equity])`, excluding the diagonal | `portfolio_daily[avg_corr_equity]`. |
| **Average crypto–crypto correlation** | `mean(correlation[crypto, crypto])`, excluding the diagonal | `portfolio_daily[avg_corr_crypto]`. |
| **Pairs above 0.70** | `COUNT(unique unordered pairs where correlation > 0.70)` | Stress-concentration flag; scope slicer filters `corr_long[scope]`. |
| **Corr now vs calm regime** | `Current average pairwise correlation − calm-regime average` | Positive values show diversification has deteriorated relative to the calm baseline. |

### Correlation matrix measure

```DAX
Correlation = MAX ( corr_long[correlation] )
```

Use `corr_long[asset1]` as matrix rows, `corr_long[asset2]` as columns, and
`[Correlation]` as the value. Apply a fixed conditional-formatting scale from
−1 (blue) to +1 (red). A regime slicer on `corr_long[scope]` changes the
matrix without recalculating the matrix in DAX.

The CSV stores both `(A, B)` and `(B, A)` because a matrix needs both axes. If
the KPI is intended to count **unique** pairs, filter to one triangle:

```DAX
Pairs Above 0.70 =
CALCULATE (
    COUNTROWS ( corr_long ),
    corr_long[asset1] < corr_long[asset2],
    corr_long[correlation] > 0.70
)
```

This avoids double-counting the two directional cells. If the reporting team
wants the number of hot matrix cells instead, omit the `asset1 < asset2`
filter and label the card **cells above 0.70**.

---

## Page 3 — Regime timeline

The regime model is K-Means on five standardized daily features:

1. Cross-asset average 30-day volatility
2. Cross-asset 30-day volatility dispersion
3. 90-day average pairwise correlation
4. Portfolio 30-day volatility
5. VIX

Clusters are ordered by volatility into `Low`, `Normal`, `Elevated`, and
`Crisis`; labels are smoothed with a five-day majority filter for episode
reporting.

| KPI | Formula | Source |
|---|---|---|
| **Current regime** | `regime_name_smoothed` on the latest portfolio date | `portfolio_daily[regime_name_smoothed]`. |
| **Days in current regime** | Sequential count from the start of the current contiguous regime spell | `portfolio_daily[days_in_regime]` on the latest date. |
| **% of days in regime** | `Trading days in selected regime / all trading days` | `regime_summary[pct_days]` or a DAX ratio in the selected filter context. |
| **Episode count** | `COUNTROWS(regime_episodes)` | Slice by `regime_episodes[regime_name]` for a per-regime count. |
| **Average episode duration** | `AVERAGE(regime_episodes[n_days])` | Trading days, not calendar days. |
| **Maximum episode duration** | `MAX(regime_episodes[n_days])` | Longest contiguous spell in the selected regime. |
| **Average 30D vol in regime** | `AVERAGE(portfolio_daily[port_vol30])` | Compare the volatility step-up between Low and Crisis. |
| **Average daily return in regime** | `AVERAGE(portfolio_daily[port_return])` | Descriptive only; regime labels are not a return forecast. |
| **Worst day in regime** | `MIN(portfolio_daily[port_return])` | Useful for stress communication and scenario design. |
| **VIX current / average** | Latest or average `portfolio_daily[VIXCLS]` | Macro context; VIX is a feature of the clustering model. |
| **10Y yield current** | Latest `portfolio_daily[DGS10]` | Macro context; not a portfolio risk calculation. |

### Recommended DAX for Page 3

```DAX
Current Regime =
VAR LastDate =
    CALCULATE ( MAX ( portfolio_daily[date] ), REMOVEFILTERS ( dim_date ) )
RETURN
    CALCULATE (
        MAX ( portfolio_daily[regime_name_smoothed] ),
        portfolio_daily[date] = LastDate
    )

Days in Current Regime =
VAR LastDate =
    CALCULATE ( MAX ( portfolio_daily[date] ), REMOVEFILTERS ( dim_date ) )
RETURN
    CALCULATE (
        MAX ( portfolio_daily[days_in_regime] ),
        portfolio_daily[date] = LastDate
    )

Avg Episode Duration (days) = AVERAGE ( regime_episodes[n_days] )
Max Episode Duration (days) = MAX ( regime_episodes[n_days] )
Episode Count = COUNTROWS ( regime_episodes )

% of Days in Regime =
DIVIDE (
    COUNTROWS ( portfolio_daily ),
    CALCULATE (
        COUNTROWS ( portfolio_daily ),
        ALL ( portfolio_daily[regime_name_smoothed] )
    )
)

Avg 30D Vol in Regime % = AVERAGE ( portfolio_daily[port_vol30] )
Worst Day in Regime % = MIN ( portfolio_daily[port_return] )
VIX Current =
VAR LastDate =
    CALCULATE ( MAX ( portfolio_daily[date] ), REMOVEFILTERS ( dim_date ) )
RETURN
    CALCULATE ( MAX ( portfolio_daily[VIXCLS] ), portfolio_daily[date] = LastDate )
```

---

## Formatting and interpretation rules

- Format return, volatility, VaR, drawdown, breach rate and correlation as
  percentages where appropriate. Correlation is often clearer with two decimal
  places (`0.58`) rather than `58.0%` in a matrix.
- Keep VaR positive as a loss magnitude. Keep realized returns and drawdowns
  signed; a negative drawdown is a loss.
- Use 252 trading days for annualization throughout. Crypto is sampled onto the
  US equity calendar, so a Friday-to-Monday crypto move is booked on Monday.
- VaR is historical and non-parametric. It is not an expected loss, a maximum
  loss, or a guarantee; pair it with the breach rate and, ideally, expected
  shortfall.
- Regimes are descriptive clusters, not a predictive signal. Use the current
  regime to trigger review and stress testing, not as a standalone trading
  instruction.
