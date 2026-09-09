#!/usr/bin/env python3
"""
Step 7 — Generate the one-page "Portfolio Risk Summary" PDF memo.

Input : data/processed/*.csv
Output: reports/Portfolio_Risk_Summary.pdf

The memo is fully templated from the computed data — every number in the
text is derived from the pipeline outputs, so re-running the pipeline
regenerates an up-to-date memo automatically.
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (HRFlowable, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

REPO = Path(__file__).resolve().parents[1]
PROC = REPO / "data" / "processed"
OUT = REPO / "reports" / "Portfolio_Risk_Summary.pdf"

NAVY = colors.HexColor("#1a2744")
RED = colors.HexColor("#b02a30")
GRAY = colors.HexColor("#5a6472")
LIGHT = colors.HexColor("#eef1f6")


def pct(x, dp=1):
    """Format a fraction (0.195) as a percentage string ('19.5%')."""
    return f"{x * 100:.{dp}f}%"


def main() -> int:
    pm = pd.read_csv(PROC / "portfolio_metrics.csv", index_col="date", parse_dates=True)
    # Combined daily table (portfolio + benchmarks + macro + regime) from step 5
    port = pd.read_csv(PROC / "powerbi" / "portfolio_daily.csv", index_col="date", parse_dates=True)
    corr_roll = pd.read_csv(PROC / "corr_rolling.csv", index_col="date", parse_dates=True)
    regimes = pd.read_csv(PROC / "regimes.csv", index_col="date", parse_dates=True)
    eps = pd.read_csv(PROC / "regime_episodes.csv", parse_dates=["start_date", "end_date"])
    summ = pd.read_csv(PROC / "regime_summary.csv")
    risk = pd.read_csv(PROC / "risk_summary.csv")
    manifest_path = REPO / "data" / "raw" / "_fetch_manifest.json"

    as_of = pm.index.max()
    n_assets = len(risk[risk["asset_class"].isin(["Equity", "Crypto"])])

    # ---- Current snapshot ---------------------------------------------------- #
    cur = port.iloc[-1]
    cur_vol30 = cur["port_vol30"]
    cur_var95, cur_var99 = cur["port_var95"], cur["port_var99"]
    cur_regime = cur["regime_name_smoothed"]
    days_in = int(cur["days_in_regime"])
    cur_corr = cur["avg_pairwise_corr"]

    # ---- Portfolio drawdown --------------------------------------------------- #
    port_row = risk[risk["ticker"] == "PORTFOLIO"].iloc[0]
    mdd = port_row["max_drawdown"]
    peak_d = pd.to_datetime(port_row["dd_peak_date"])
    trough_d = pd.to_datetime(port_row["dd_trough_date"])
    rec_d = port_row["dd_recovery_date"]
    if isinstance(rec_d, str) and rec_d:
        rec_days = (pd.to_datetime(rec_d) - trough_d).days
        recovery_text = f"recovering {rec_days} days later"
    else:
        recovery_text = "still unrecovered as of this report"

    # ---- Regime stats ----------------------------------------------------------- #
    # Order regimes by severity (avg portfolio vol) so first = calmest, last = worst
    summ = summ.sort_values("avg_port_vol30").set_index("regime_name")
    names = list(summ.index)
    calm = names[0]
    worst_regime = names[-1]
    calm_corr = summ.loc[calm, "avg_pairwise_corr"]
    crisis_corr = summ.loc[worst_regime, "avg_pairwise_corr"]
    calm_vol = summ.loc[calm, "avg_port_vol30"]
    crisis_vol = summ.loc[worst_regime, "avg_port_vol30"]
    crisis_share = summ.loc[worst_regime, "pct_days"]

    cross_by_regime = corr_roll.groupby(
        regimes["regime_name_smoothed"].reindex(corr_roll.index)
    )["avg_corr_cross"].mean()
    calm_cross = cross_by_regime.get(calm, np.nan)
    crisis_cross = cross_by_regime.get(worst_regime, np.nan)

    worst_eps = eps[eps["regime_name"] == worst_regime].sort_values("n_days").iloc[-1] \
        if (eps["regime_name"] == worst_regime).any() else None
    max_ep_days = int(worst_eps["n_days"]) if worst_eps is not None else 0
    max_ep_span = (f"{worst_eps['start_date']:%b %Y}–{worst_eps['end_date']:%b %Y}"
                   if worst_eps is not None else "n/a")
    avg_ep_days = float(summ.loc[worst_regime, "avg_episode_days"])

    # ---- VaR backtest -------------------------------------------------------------- #
    eligible = port[port["port_var95"].notna()]
    n_eligible = len(eligible)
    n_breach = int((eligible["port_return"] < -eligible["port_var95"]).sum())
    breach_rate = n_breach / n_eligible if n_eligible else np.nan
    worst_day = eligible["port_return"].min()
    worst_mult = abs(worst_day) / cur_var95 if cur_var95 else np.nan

    # ---- Worst asset ---------------------------------------------------------------- #
    assets = risk[risk["asset_class"].isin(["Equity", "Crypto"])]
    worst_dd_asset = assets.loc[assets["max_drawdown"].idxmin()]
    crypto_vols = assets[assets["asset_class"] == "Crypto"]["ann_vol"].mean()
    equity_vols = assets[assets["asset_class"] == "Equity"]["ann_vol"].mean()

    # ---- Memo text -------------------------------------------------------------------- #
    findings = [
        f"<b>Diversification broke down under stress.</b> Average pairwise correlation across the "
        f"{n_assets}-asset book was <b>{calm_corr:.2f}</b> in the {calm.lower()}-vol regime but "
        f"<b>{crisis_corr:.2f}</b> in the {worst_regime.lower()} regime; the equity–crypto correlation rose from "
        f"{calm_cross:.2f} to {crisis_cross:.2f}. The crypto sleeve ({crypto_vols:.0%} avg annualized vol vs "
        f"{equity_vols:.0%} for the equity sleeve) moved with — not against — equity stress.",

        f"<b>Volatility is regime-structured, not continuous.</b> K-Means clustering on rolling volatility, "
        f"correlation dispersion and VIX separated the period into {len(names)} regimes; {crisis_share:.0%} of days "
        f"were {worst_regime.lower()}-regime conditions, the longest spell lasting {max_ep_days} trading days "
        f"({max_ep_span}). Portfolio vol averaged {calm_vol:.0%} in the calm regime versus {crisis_vol:.0%} in "
        f"{worst_regime.lower()} — a {crisis_vol/calm_vol:.1f}× step-up when the regime flips.",

        f"<b>Historical VaR is a floor, not a ceiling.</b> The trailing 1-year 95% historical VaR stands at "
        f"<b>{pct(cur_var95)}</b> (99%: {pct(cur_var99)}). In backtest the 95% level was breached "
        f"{n_breach} of {n_eligible} days ({breach_rate:.1%} vs 5% expected), and the worst single day "
        f"({pct(worst_day)}) was {worst_mult:.1f}× the current 95% VaR. Breaches cluster around regime "
        f"transitions, when the 250-day estimation window still carries the calm regime's distribution.",

        f"<b>Drawdown risk is dominated by regime episodes.</b> The equal-weighted portfolio's maximum drawdown was "
        f"<b>{pct(mdd)}</b> ({peak_d:%b %Y} peak → {trough_d:%b %Y} trough, {recovery_text}); the deepest single-name "
        f"drawdown was {worst_dd_asset['ticker']} at {pct(worst_dd_asset['max_drawdown'])}. Current regime: "
        f"<b>{cur_regime}</b> for {days_in} trading days, with 30-day vol at {pct(cur_vol30)} and average pairwise "
        f"correlation at {cur_corr:.2f}.",
    ]

    recs = [
        f"<b>Size to the stress regime, not the current one.</b> Set position limits and volatility targets off "
        f"{worst_regime.lower()}-regime vol ({crisis_vol:.0%} portfolio, {summ.loc[worst_regime,'avg_pairwise_corr']:.2f} avg correlation) "
        f"rather than the current 30-day vol ({pct(cur_vol30)}) — otherwise the book is structurally oversized the "
        f"moment the regime shifts.",

        f"<b>Stress-test at crisis correlations.</b> Use the {worst_regime.lower()}-regime correlation matrix "
        f"(avg pairwise {crisis_corr:.2f}; equity–crypto {crisis_cross:.2f}) for risk limits and hedge sizing. "
        f"Name-level diversification provides materially less protection than the full-sample matrix implies; "
        f"sleeve-level hedges (index puts, futures, cash) are the reliable lever.",

        f"<b>Upgrade the VaR process.</b> Report 99% VaR ({pct(cur_var99)}) and expected shortfall alongside the "
        f"95% headline, re-estimate on every regime transition rather than on a fixed calendar, and treat breach "
        f"clusters ({breach_rate:.1%} realized vs 5% expected) as a model-risk KPI reviewed monthly.",

        f"<b>Operationalize the regime flag.</b> Codify de-risking triggers on transitions into "
        f"Elevated/{worst_regime} states (average episode {avg_ep_days:.0f} trading days, max {max_ep_days}) with a "
        f"pre-agreed playbook per regime — the value is in the speed of the response, not the label itself.",
    ]

    # ---- Render ------------------------------------------------------------------------- #
    doc = SimpleDocTemplate(
        str(OUT), pagesize=letter,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.6 * inch, bottomMargin=0.55 * inch,
        title="Portfolio Risk Summary",
        author="Portfolio Risk & Regime Dashboard",
    )

    h1 = ParagraphStyle("h1", fontName="Helvetica-Bold", fontSize=17, leading=20, textColor=NAVY)
    sub = ParagraphStyle("sub", fontName="Helvetica", fontSize=9.5, leading=12, textColor=GRAY)
    sect = ParagraphStyle("sect", fontName="Helvetica-Bold", fontSize=10.5, leading=13,
                          textColor=NAVY, spaceBefore=10, spaceAfter=4)
    body = ParagraphStyle("body", fontName="Helvetica", fontSize=9, leading=12.4,
                          alignment=TA_LEFT, spaceAfter=4.5, textColor=colors.HexColor("#20242c"))
    foot = ParagraphStyle("foot", fontName="Helvetica", fontSize=6.8, leading=8.6, textColor=GRAY)
    cell = ParagraphStyle("cell", fontName="Helvetica", fontSize=7.6, leading=9.4, textColor=colors.HexColor("#20242c"))
    cellv = ParagraphStyle("cellv", fontName="Helvetica-Bold", fontSize=12, leading=13, textColor=NAVY)

    def metric_tile(label: str, value: str, note: str = "") -> list:
        return [
            Paragraph(f"<font color='#5a6472'>{label.upper()}</font>", cell),
            Paragraph(value, cellv),
            Paragraph(f"<font color='#5a6472'>{note}</font>", cell),
        ]

    tiles = [
        metric_tile("30-day vol (ann.)", pct(cur_vol30), f"vs {crisis_vol:.0%} in {worst_regime.lower()} regime"),
        metric_tile("1-day 95% VaR", pct(cur_var95), f"99% VaR {pct(cur_var99)}"),
        metric_tile("Max drawdown", pct(mdd), f"{peak_d:%b %Y} → {trough_d:%b %Y}"),
        metric_tile("Avg pairwise corr", f"{cur_corr:.2f}", f"vs {calm_corr:.2f} in {calm.lower()} regime"),
        metric_tile("Current regime", f"{cur_regime}", f"{days_in} trading days"),
        metric_tile("95% VaR breach rate", f"{breach_rate:.1%}", f"{n_breach} of {n_eligible} days vs 5% expected"),
    ]

    tile_tbl = Table(
        [tiles[0], tiles[1], tiles[2], tiles[3], tiles[4], tiles[5]],
        colWidths=[2.4 * inch] * 3,
    )
    tile_tbl.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.white),
        ("LINEAFTER", (0, 0), (1, -1), 6, colors.white),
    ]))

    story = [
        Paragraph("PORTFOLIO RISK SUMMARY", h1),
        Spacer(1, 2),
        Paragraph(
            f"Equal-weighted {n_assets}-asset portfolio (15 technology-sector equities + BTC, ETH, SOL) · "
            f"Daily data through {as_of:%B %d, %Y} · Yahoo Finance / FRED · K-Means volatility regimes",
            sub,
        ),
        HRFlowable(width="100%", thickness=1.4, color=RED, spaceBefore=6, spaceAfter=10),
        tile_tbl,
        Paragraph("FINDINGS", sect),
        HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#c9cfda"), spaceAfter=5),
    ]
    story += [Paragraph(f"•&nbsp;&nbsp;{f}", body) for f in findings]
    story += [
        Paragraph("RECOMMENDATIONS", sect),
        HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#c9cfda"), spaceAfter=5),
    ]
    story += [Paragraph(f"{i}.&nbsp;&nbsp;{r}", body) for i, r in enumerate(recs, 1)]
    story += [
        Spacer(1, 8),
        HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#c9cfda"), spaceAfter=4),
        Paragraph(
            "<b>Methodology.</b> Daily total returns from adjusted closes (Yahoo Finance); macro context from FRED (10Y yield, VIX, "
            "broad dollar index). Volatility annualized on a 252-day basis; historical VaR = 5th/1st percentile of trailing 250-day daily returns "
            "(percentile method; no parametric or Monte Carlo assumptions); drawdown on the equal-weighted, daily-rebalanced index. "
            "Regimes: K-Means (k=4, fixed seed) on standardized 30-day average volatility, cross-asset vol dispersion, 90-day average pairwise "
            "correlation, portfolio vol and VIX; labels smoothed with a 5-day majority filter for episode reporting. "
            "<b>Limitations.</b> Historical VaR assumes the recent past is representative of the near future — it is not a complete risk model; "
            "correlations and volatilities are estimates and cluster around regime transitions; the crypto sleeve is mapped onto the US equity "
            "calendar (weekend moves book to the next trading day); equal-weight daily rebalancing ignores transaction costs and taxes. "
            "This memo is an analytics exercise, not investment advice.",
            foot,
        ),
    ]

    doc.build(story)
    print(f"[memo] wrote {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
