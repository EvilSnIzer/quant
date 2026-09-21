#!/usr/bin/env python3
"""
Step 8 — Build a self-contained virtual dashboard preview.

Reads the import-ready Power BI tables and writes:
    powerbi/dashboard_mockup.html

The preview is intentionally dependency-free: it uses the repository's existing
PNG exhibits, embeds the current KPI snapshot, and provides three clickable
pages that mirror the Power BI build guide. It is a visual specification, not
an alternative calculation engine. Re-run after the pipeline refreshes data.
"""

from __future__ import annotations

import csv
import html
import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PB = REPO / "data" / "processed" / "powerbi"
OUT = REPO / "powerbi" / "dashboard_mockup.html"


# --------------------------------------------------------------------------- #
# Small standard-library helpers so the preview can be rebuilt on a clean
# Python install without pandas, a web framework, or a package download.
# --------------------------------------------------------------------------- #
def read_csv(name: str) -> list[dict[str, str]]:
    with open(PB / name, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def num(value: str | None, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def fmt_pct(value: float, decimals: int = 1, signed: bool = False) -> str:
    sign = "+" if signed and value > 0 else ""
    return f"{sign}{value * 100:.{decimals}f}%"


def fmt_num(value: float, decimals: int = 2) -> str:
    return f"{value:,.{decimals}f}"


def esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def json_for_script(value: object) -> str:
    # Prevent a data string from prematurely closing the script element.
    return json.dumps(value, separators=(",", ":")).replace("</", "<\\/")


def trend_svg(values: list[float], color: str = "#71d6aa") -> str:
    """Return a tiny inline sparkline for KPI cards."""
    if not values:
        return ""
    lo, hi = min(values), max(values)
    span = hi - lo or 1.0
    pts = []
    for i, value in enumerate(values):
        x = 2 + (i / max(len(values) - 1, 1)) * 94
        y = 19 - ((value - lo) / span) * 16
        pts.append(f"{x:.1f},{y:.1f}")
    return (
        f'<svg class="spark" viewBox="0 0 100 22" role="img" '
        f'aria-label="Recent trend"><polyline points="{" ".join(pts)}" '
        f'fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" '
        f'stroke-linejoin="round" /></svg>'
    )


def status_class(regime: str) -> str:
    return {"Low": "low", "Normal": "normal", "Elevated": "elevated", "Crisis": "crisis"}.get(regime, "muted")


# --------------------------------------------------------------------------- #
# Load current snapshot
# --------------------------------------------------------------------------- #
port = read_csv("portfolio_daily.csv")
regime_summary = read_csv("regime_summary.csv")
episodes = read_csv("regime_episodes.csv")
risk_rows = read_csv("risk_summary.csv")
asset_rows = read_csv("dim_asset.csv")
corr_rows = read_csv("corr_long.csv")
asset_class = {row["ticker"]: row["asset_class"] for row in asset_rows}

port = sorted(port, key=lambda row: row["date"])
latest = port[-1]
portfolio_risk = next(row for row in risk_rows if row["ticker"] == "PORTFOLIO")
summary_by_regime = {row["regime_name"]: row for row in regime_summary}

eligible = [row for row in port if row.get("port_var95") not in (None, "")]
breach_count = sum(int(num(row.get("port_breach95"))) for row in eligible)
breach_rate = breach_count / len(eligible) if eligible else 0.0
range_return = num(latest["port_index"]) / num(port[0]["port_index"]) - 1.0

# A compact trend sample keeps the HTML small while preserving visual context.
trend_rows = port[-120::8]
trend = {
    "index": [num(row["port_index"]) for row in trend_rows],
    "corr": [num(row.get("avg_pairwise_corr")) for row in trend_rows if row.get("avg_pairwise_corr")],
    "vol": [num(row.get("port_vol30")) for row in trend_rows if row.get("port_vol30")],
}

# corr_long stores both (asset A, asset B) and (asset B, asset A). Count unique
# pairs for the preview while the catalog explains the Power BI implementation.
pair_counts: dict[str, int] = defaultdict(int)
seen_pairs: set[tuple[str, str, str]] = set()
for row in corr_rows:
    a, b, scope = row["asset1"], row["asset2"], row["scope"]
    pair = (scope, *sorted((a, b)))
    if num(row["correlation"]) > 0.70 and pair not in seen_pairs:
        pair_counts[scope] += 1
        seen_pairs.add(pair)

def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0

full_values = [num(row["correlation"]) for row in corr_rows if row["scope"] == "Full sample"]
full_cross_values = [
    num(row["correlation"])
    for row in corr_rows
    if row["scope"] == "Full sample"
    and {asset_class.get(row["asset1"]), asset_class.get(row["asset2"])} == {"Equity", "Crypto"}
]
full_vol_values = [num(row["port_vol30"]) for row in port if row.get("port_vol30") not in (None, "")]

scope_metrics = {
    "Full sample": {
        "corr": mean(full_values),
        "cross": mean(full_cross_values),
        "vol": mean(full_vol_values),
        "pairs": pair_counts.get("Full sample", 0),
    }
}
for name, row in summary_by_regime.items():
    scope_metrics[name] = {
        "corr": num(row["avg_pairwise_corr"]),
        "cross": None,
        "vol": num(row["avg_port_vol30"]),
        "pairs": pair_counts.get(name, 0),
    }

# Use rolling cross-sleeve averages where available to annotate regime cards.
for row in port:
    regime = row.get("regime_name_smoothed")
    if regime in scope_metrics and row.get("avg_corr_cross") not in (None, ""):
        scope_metrics[regime].setdefault("cross_values", []).append(num(row["avg_corr_cross"]))
for name, metric in scope_metrics.items():
    values = metric.pop("cross_values", [])
    if metric.get("cross") is None:
        metric["cross"] = sum(values) / len(values) if values else 0.0

# Show the highest-volatility names in the mock risk table, then the portfolio.
assets = [row for row in risk_rows if row["asset_class"] in ("Equity", "Crypto")]
assets.sort(key=lambda row: num(row["ann_vol"]), reverse=True)
show_risk = assets[:6]

# The latest few rows of the episode table are the most useful in a preview.
show_episodes = list(reversed(episodes))[:6]

# Current regime color is used in the hero status and nav.
current_regime = latest.get("regime_name_smoothed", "n/a")
current_status = status_class(current_regime)
as_of = latest["date"]

# --------------------------------------------------------------------------- #
# Reusable HTML fragments
# --------------------------------------------------------------------------- #

def kpi(label: str, value: str, note: str, tone: str = "teal", spark: list[float] | None = None) -> str:
    spark_html = trend_svg(spark or [], {"red": "#f07878", "amber": "#f3bd68", "blue": "#7ea9ff"}.get(tone, "#71d6aa"))
    return (
        f'<article class="kpi-card {esc(tone)}">'
        f'<div class="kpi-label">{esc(label)}</div>'
        f'<div class="kpi-value">{value}</div>'
        f'<div class="kpi-note">{esc(note)}</div>{spark_html}</article>'
    )


def chart(title: str, subtitle: str, image: str, cls: str = "") -> str:
    return (
        f'<section class="panel chart-panel {esc(cls)}">'
        f'<div class="panel-heading"><div><h3>{esc(title)}</h3><p>{esc(subtitle)}</p></div>'
        f'<span class="panel-menu">•••</span></div>'
        f'<div class="chart-frame"><img src="../reports/charts/{esc(image)}" alt="{esc(title)}" loading="lazy"></div>'
        f'</section>'
    )


def risk_table() -> str:
    rows = []
    for row in show_risk:
        cls = "crypto" if row["asset_class"] == "Crypto" else "equity"
        rows.append(
            "<tr>"
            f'<td><span class="ticker-dot {cls}"></span><b>{esc(row["ticker"].replace("-USD", ""))}</b><small>{esc(row["name"])}</small></td>'
            f'<td>{fmt_pct(num(row["cagr"]))}</td>'
            f'<td class="risk-value">{fmt_pct(num(row["ann_vol"]))}</td>'
            f'<td>{fmt_pct(num(row["var95_current"]))}</td>'
            f'<td class="negative">{fmt_pct(num(row["max_drawdown"]))}</td>'
            "</tr>"
        )
    return (
        '<section class="panel table-panel"><div class="panel-heading"><div><h3>Asset risk snapshot</h3>'
        '<p>Current estimates; sort in the live Power BI table</p></div><span class="table-link">View all 18 →</span></div>'
        '<div class="table-scroll"><table><thead><tr><th>Asset</th><th>CAGR</th><th>Ann. vol</th><th>1D VaR 95%</th><th>Max DD</th></tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table></div></section>'
    )


def regime_cards() -> str:
    cards = []
    ordered = ["Low", "Normal", "Elevated", "Crisis"]
    for name in ordered:
        row = summary_by_regime.get(name)
        if not row:
            continue
        cards.append(
            f'<div class="regime-card {status_class(name)}"><div class="regime-card-top"><span class="regime-dot"></span>'
            f'<b>{esc(name)}</b><span>{fmt_pct(num(row["pct_days"]))} of days</span></div>'
            f'<strong>{fmt_pct(num(row["avg_port_vol30"]))}</strong><small>avg 30D vol · corr {num(row["avg_pairwise_corr"]):.2f}</small>'
            f'<div class="meter"><span style="width:{min(num(row["pct_days"]) * 100 * 2.1, 100):.1f}%"></span></div></div>'
        )
    return "".join(cards)


def episode_rows() -> str:
    rows = []
    for row in show_episodes:
        name = row["regime_name"]
        rows.append(
            f'<tr><td><span class="regime-pill {status_class(name)}">{esc(name)}</span></td>'
            f'<td>{esc(row["start_date"])}</td><td>{esc(row["end_date"])}</td><td class="align-right">{esc(row["n_days"])}d</td></tr>'
        )
    return "".join(rows)


# --------------------------------------------------------------------------- #
# The page itself. The data snapshot is embedded so opening the file directly
# works; no localhost API calls or external assets are required.
# --------------------------------------------------------------------------- #
html_doc = f'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Portfolio Risk &amp; Regime Dashboard — Virtual Preview</title>
<style>
:root {{
  --ink:#e7edf7; --muted:#91a0b7; --muted-2:#63728a; --bg:#0d1524; --sidebar:#101b2d;
  --panel:#142239; --panel-2:#172943; --line:#253956; --teal:#71d6aa; --blue:#7ea9ff;
  --red:#f07878; --amber:#f3bd68; --low:#71d6aa; --normal:#7ea9ff; --elevated:#f3bd68; --crisis:#f07878;
  --shadow:0 18px 48px rgba(0,0,0,.16);
}}
* {{ box-sizing:border-box; }}
html,body {{ margin:0; min-height:100%; background:var(--bg); color:var(--ink); font-family:Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
body {{ font-size:14px; }}
button,select {{ font:inherit; }}
.app {{ display:grid; grid-template-columns:238px minmax(0,1fr); min-height:100vh; }}
.sidebar {{ background:var(--sidebar); border-right:1px solid var(--line); padding:28px 16px 20px; display:flex; flex-direction:column; gap:28px; }}
.brand {{ padding:0 12px; }}
.brand-mark {{ width:34px; height:34px; display:grid; place-items:center; border-radius:10px; background:linear-gradient(135deg,#71d6aa,#438dba); color:#07131d; font-weight:900; margin-bottom:16px; box-shadow:0 8px 18px rgba(113,214,170,.18); }}
.brand h1 {{ font-size:16px; line-height:1.2; margin:0 0 6px; letter-spacing:-.03em; }}
.brand p {{ color:var(--muted); font-size:11px; line-height:1.5; margin:0; }}
.nav-label {{ color:var(--muted-2); text-transform:uppercase; letter-spacing:.14em; font-size:10px; font-weight:800; padding:0 12px 8px; }}
.nav {{ display:grid; gap:6px; }}
.nav button {{ cursor:pointer; border:0; border-radius:9px; background:transparent; color:var(--muted); width:100%; display:flex; align-items:center; gap:11px; text-align:left; padding:12px; font-weight:650; font-size:13px; }}
.nav button:hover,.nav button.active {{ background:#1b304c; color:var(--ink); }}
.nav button.active {{ box-shadow:inset 3px 0 var(--teal); }}
.nav-icon {{ width:19px; text-align:center; color:var(--muted-2); font-size:16px; }}
.nav button.active .nav-icon {{ color:var(--teal); }}
.sidebar-bottom {{ margin-top:auto; }}
.data-health {{ border:1px solid #28475a; background:rgba(113,214,170,.06); border-radius:12px; padding:13px; }}
.data-health .eyebrow {{ color:var(--teal); }}
.eyebrow {{ text-transform:uppercase; font-size:9px; letter-spacing:.14em; font-weight:850; color:var(--muted); }}
.data-health strong {{ display:block; font-size:12px; margin:6px 0 4px; }}
.data-health p {{ color:var(--muted); font-size:10px; line-height:1.45; margin:0; }}
.main {{ min-width:0; }}
.topbar {{ height:72px; display:flex; align-items:center; justify-content:space-between; padding:0 34px; border-bottom:1px solid var(--line); background:rgba(13,21,36,.8); }}
.breadcrumb {{ color:var(--muted); font-size:12px; }}
.breadcrumb b {{ color:var(--ink); font-weight:700; }}
.top-actions {{ display:flex; align-items:center; gap:10px; }}
.chip {{ border:1px solid var(--line); color:var(--muted); border-radius:999px; padding:8px 11px; font-size:11px; background:#111e31; }}
.chip.live {{ color:var(--teal); border-color:#2b5d5b; }}
.dot {{ display:inline-block; width:7px; height:7px; border-radius:50%; background:var(--teal); margin-right:6px; box-shadow:0 0 0 3px rgba(113,214,170,.12); }}
.content {{ max-width:1530px; margin:0 auto; padding:32px 34px 44px; }}
.page {{ display:none; animation:fade .18s ease-out; }}
.page.active {{ display:block; }}
@keyframes fade {{ from {{ opacity:.1; transform:translateY(3px); }} to {{ opacity:1; transform:none; }} }}
.hero {{ display:flex; justify-content:space-between; align-items:flex-end; gap:22px; margin-bottom:26px; }}
.hero h2 {{ font-size:28px; line-height:1.1; margin:8px 0 8px; letter-spacing:-.045em; }}
.hero p {{ color:var(--muted); margin:0; font-size:13px; }}
.hero-controls {{ display:flex; align-items:center; gap:8px; flex-wrap:wrap; justify-content:flex-end; }}
.control {{ display:flex; align-items:center; gap:8px; border:1px solid var(--line); border-radius:8px; padding:8px 10px; background:#111e31; color:var(--muted); font-size:11px; }}
.control select {{ color:var(--ink); border:0; outline:0; background:transparent; font-weight:700; cursor:pointer; }}
.status-badge {{ display:inline-flex; align-items:center; gap:7px; padding:7px 10px; border-radius:999px; font-size:11px; font-weight:750; }}
.status-badge.low,.regime-pill.low,.regime-card.low {{ color:var(--low); background:rgba(113,214,170,.11); }}
.status-badge.normal,.regime-pill.normal,.regime-card.normal {{ color:var(--normal); background:rgba(126,169,255,.11); }}
.status-badge.elevated,.regime-pill.elevated,.regime-card.elevated {{ color:var(--elevated); background:rgba(243,189,104,.11); }}
.status-badge.crisis,.regime-pill.crisis,.regime-card.crisis {{ color:var(--crisis); background:rgba(240,120,120,.11); }}
.status-badge .dot {{ background:currentColor; box-shadow:none; margin:0; }}
.kpi-grid {{ display:grid; grid-template-columns:repeat(6,minmax(130px,1fr)); gap:12px; margin-bottom:16px; }}
.kpi-card {{ min-height:137px; background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:16px; position:relative; overflow:hidden; box-shadow:var(--shadow); }}
.kpi-card::before {{ content:""; position:absolute; inset:0 auto 0 0; width:3px; background:var(--teal); }}
.kpi-card.red::before {{ background:var(--red); }} .kpi-card.amber::before {{ background:var(--amber); }} .kpi-card.blue::before {{ background:var(--blue); }}
.kpi-label {{ text-transform:uppercase; letter-spacing:.11em; font-size:9px; color:var(--muted); font-weight:850; margin-bottom:13px; }}
.kpi-value {{ font-size:24px; letter-spacing:-.04em; font-weight:780; white-space:nowrap; }}
.kpi-note {{ color:var(--muted); font-size:10px; margin-top:8px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.spark {{ position:absolute; width:73px; height:20px; right:12px; bottom:14px; opacity:.8; }}
.panel-grid {{ display:grid; grid-template-columns:minmax(0,1.6fr) minmax(320px,1fr); gap:16px; margin-bottom:16px; }}
.panel-grid.equal {{ grid-template-columns:repeat(2,minmax(0,1fr)); }}
.panel {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; overflow:hidden; box-shadow:var(--shadow); }}
.panel-heading {{ display:flex; align-items:flex-start; justify-content:space-between; gap:12px; padding:17px 18px 12px; }}
.panel-heading h3 {{ margin:0 0 5px; font-size:14px; letter-spacing:-.02em; }}
.panel-heading p {{ color:var(--muted); margin:0; font-size:10px; }}
.panel-menu,.table-link {{ color:var(--muted-2); font-size:12px; }} .table-link {{ color:var(--teal); font-size:11px; }}
.chart-frame {{ background:#fff; min-height:258px; padding:9px 10px 7px; display:flex; align-items:center; }}
.chart-frame img {{ width:100%; max-height:295px; display:block; object-fit:contain; }}
.chart-panel.tall .chart-frame {{ min-height:345px; }}
.table-scroll {{ overflow-x:auto; }}
table {{ border-collapse:collapse; width:100%; min-width:545px; }}
th {{ color:var(--muted-2); font-weight:800; text-transform:uppercase; letter-spacing:.1em; font-size:9px; text-align:right; padding:0 18px 10px; border-bottom:1px solid var(--line); }}
th:first-child,td:first-child {{ text-align:left; }}
td {{ padding:11px 18px; border-bottom:1px solid rgba(37,57,86,.66); text-align:right; font-size:11px; color:#cbd4e2; }}
tbody tr:last-child td {{ border-bottom:0; }}
td small {{ display:block; color:var(--muted-2); font-size:9px; margin-top:3px; }}
.ticker-dot {{ display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:8px; }} .ticker-dot.equity {{ background:var(--blue); }} .ticker-dot.crypto {{ background:var(--amber); }}
.negative {{ color:var(--red); }} .risk-value {{ color:var(--amber); font-weight:750; }}
.insight {{ padding:17px 18px; border-left:3px solid var(--amber); background:rgba(243,189,104,.05); margin:16px; border-radius:0 8px 8px 0; }}
.insight strong {{ display:block; font-size:12px; margin-bottom:6px; }} .insight p {{ color:var(--muted); font-size:11px; line-height:1.55; margin:0; }}
.corr-hero {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; margin-bottom:16px; }}
.metric-box {{ border:1px solid var(--line); background:var(--panel); border-radius:12px; padding:16px; }}
.metric-box .metric {{ font-size:24px; font-weight:780; letter-spacing:-.05em; margin:8px 0 4px; }} .metric-box p {{ color:var(--muted); margin:0; font-size:10px; }}
.scope-note {{ color:var(--muted); font-size:10px; margin:0 18px 14px; padding:9px 11px; border-radius:7px; background:#111e31; border:1px solid var(--line); }}
.regime-cards {{ display:grid; grid-template-columns:repeat(4,1fr); gap:10px; padding:0 18px 18px; }}
.regime-card {{ border:1px solid currentColor; border-color:color-mix(in srgb, currentColor 28%, transparent); border-radius:10px; padding:13px; background:rgba(255,255,255,.015); }}
.regime-card-top {{ display:flex; align-items:center; gap:6px; font-size:11px; margin-bottom:13px; }} .regime-card-top span:last-child {{ margin-left:auto; font-size:9px; color:var(--muted); }}
.regime-dot {{ width:8px; height:8px; border-radius:50%; background:currentColor; }} .regime-card strong {{ font-size:22px; display:block; letter-spacing:-.04em; }} .regime-card small {{ color:var(--muted); font-size:10px; }}
.meter {{ height:3px; background:#22334c; border-radius:3px; margin-top:13px; overflow:hidden; }} .meter span {{ display:block; height:100%; border-radius:3px; background:currentColor; }}
.regime-pill {{ display:inline-block; padding:4px 7px; border-radius:5px; font-size:10px; font-weight:800; }}
.align-right {{ text-align:right; }}
.timeline-kpis {{ display:grid; grid-template-columns:repeat(4,1fr); gap:12px; margin-bottom:16px; }}
.timeline-stat {{ background:var(--panel); border:1px solid var(--line); border-radius:12px; padding:14px 16px; }} .timeline-stat strong {{ font-size:21px; display:block; margin-top:8px; }} .timeline-stat span {{ color:var(--muted); font-size:10px; }}
.formula-strip {{ border:1px dashed #355173; border-radius:10px; margin:16px 0; padding:13px 15px; color:var(--muted); font-size:10px; line-height:1.55; }}
.formula-strip code {{ color:#c9d8f0; background:#0e1a2c; padding:2px 5px; border-radius:4px; }}
.footer {{ color:var(--muted-2); font-size:10px; margin-top:26px; display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; }}
@media (max-width:1150px) {{ .kpi-grid {{ grid-template-columns:repeat(3,1fr); }} .corr-hero {{ grid-template-columns:repeat(2,1fr); }} }}
@media (max-width:820px) {{ .app {{ grid-template-columns:1fr; }} .sidebar {{ display:none; }} .topbar {{ padding:0 18px; }} .content {{ padding:24px 18px 36px; }} .hero {{ align-items:flex-start; flex-direction:column; }} .hero-controls {{ justify-content:flex-start; }} .panel-grid,.panel-grid.equal {{ grid-template-columns:1fr; }} .regime-cards {{ grid-template-columns:repeat(2,1fr); }} .timeline-kpis {{ grid-template-columns:repeat(2,1fr); }} }}
@media (max-width:520px) {{ .kpi-grid {{ grid-template-columns:repeat(2,1fr); }} .kpi-card {{ min-height:125px; padding:13px; }} .kpi-value {{ font-size:20px; }} .top-actions .chip:first-child {{ display:none; }} .regime-cards {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand"><div class="brand-mark">Q</div><h1>Portfolio Risk<br>&amp; Regime</h1><p>Decision cockpit for an equal-weighted tech + crypto portfolio.</p></div>
    <div><div class="nav-label">Workspace</div><nav class="nav" aria-label="Dashboard pages">
      <button class="active" data-page-target="overview"><span class="nav-icon">◫</span>Portfolio overview</button>
      <button data-page-target="correlation"><span class="nav-icon">⊞</span>Correlation &amp; diversification</button>
      <button data-page-target="regimes"><span class="nav-icon">◒</span>Regime timeline</button>
    </nav></div>
    <div class="sidebar-bottom"><div class="data-health"><div class="eyebrow">Data health</div><strong><span class="dot"></span>Pipeline snapshot ready</strong><p>18 assets · 5 years daily<br>Yahoo Finance + FRED · through {esc(as_of)}</p></div></div>
  </aside>
  <main class="main">
    <header class="topbar"><div class="breadcrumb">Risk analytics / <b id="crumb">Portfolio overview</b></div><div class="top-actions"><span class="chip">18 assets · 5Y lookback</span><span class="chip live"><span class="dot"></span>Refreshed {esc(as_of)}</span></div></header>
    <div class="content">
      <section class="page active" id="overview">
        <div class="hero"><div><div class="eyebrow">Executive view · Page 1 of 3</div><h2>Portfolio overview</h2><p>Where are we, how bad has it been, and is risk elevated now?</p></div><div class="hero-controls"><div class="control">Asset class <select aria-label="Asset class"><option>All assets</option><option>Equity</option><option>Crypto</option></select></div><div class="control">Window <select aria-label="Window"><option>5 years</option><option>1 year</option><option>90 days</option></select></div><span class="status-badge {current_status}"><span class="dot"></span>{esc(current_regime)} regime · {esc(latest.get("days_in_regime", "—"))}d</span></div></div>
        <div class="kpi-grid">
          {kpi("Portfolio CAGR", fmt_pct(num(portfolio_risk["cagr"])), "Equal-weighted, rf = 0", "teal", trend["index"])}
          {kpi("30D volatility", fmt_pct(num(latest["port_vol30"])), "Annualized · √252", "amber", trend["vol"])}
          {kpi("1D VaR · 95%", fmt_pct(num(latest["port_var95"])), "Trailing 250d historical", "red")}
          {kpi("Current drawdown", fmt_pct(num(latest["port_drawdown"])), "From running peak", "red")}
          {kpi("Maximum drawdown", fmt_pct(num(portfolio_risk["max_drawdown"])), "Peak → trough", "red")}
          {kpi("VaR breach rate", fmt_pct(breach_rate), f"{breach_count} / {len(eligible)} days · 5% expected", "blue")}
        </div>
        <div class="panel-grid">{chart("Cumulative performance", "Indexed to 100 · portfolio versus QQQ and SPY", "cumulative_returns.png", "tall")}{chart("Portfolio drawdown", "Peak-to-trough loss with volatility-regime bands", "drawdown.png", "tall")}</div>
        <div class="panel-grid equal">{risk_table()}<section class="panel"><div class="panel-heading"><div><h3>Decision signal</h3><p>What the dashboard is telling the investment committee</p></div><span class="status-badge {current_status}">{esc(current_regime)}</span></div><div class="insight"><strong>Diversification is regime-dependent.</strong><p>Average pairwise correlation rises to <b>{num(scope_metrics.get("Crisis", dict()).get("corr", 0)):.2f}</b> in Crisis versus <b>{num(scope_metrics.get("Low", dict()).get("corr", 0)):.2f}</b> in Low. The risk budget should be sized to stress correlations, not just the current calm tape.</p></div><div class="formula-strip"><b>Headline formulas</b><br><code>Vol = STDEV(daily returns) × √252</code> &nbsp; <code>VaR95 = −PERCENTILE.INC(last 250 returns, 5%)</code><br><span>All values are precomputed in Python and exposed as Power BI-ready columns.</span></div></section></div>
      </section>

      <section class="page" id="correlation">
        <div class="hero"><div><div class="eyebrow">Diversification lens · Page 2 of 3</div><h2>Correlation &amp; diversification</h2><p>Flip the regime lens and watch diversification decay under stress.</p></div><div class="hero-controls"><div class="control">Matrix scope <select id="scopeFilter" aria-label="Correlation matrix scope"><option>Full sample</option><option>Low</option><option>Normal</option><option>Elevated</option><option>Crisis</option></select></div><span class="chip">Rows × columns · daily returns</span></div></div>
        <div class="corr-hero"><div class="metric-box"><div class="eyebrow">Avg pairwise corr</div><div class="metric" id="scopeCorr">—</div><p id="scopeCorrNote">Selected matrix scope</p></div><div class="metric-box"><div class="eyebrow">Equity–crypto corr</div><div class="metric" id="scopeCross">—</div><p>Cross-sleeve co-movement</p></div><div class="metric-box"><div class="eyebrow">Avg 30D vol</div><div class="metric" id="scopeVol">—</div><p>Portfolio, annualized</p></div><div class="metric-box"><div class="eyebrow">Pairs above 0.70</div><div class="metric" id="scopePairs">—</div><p>Unique off-diagonal pairs</p></div></div>
        <section class="panel"><div class="panel-heading"><div><h3>Correlation by regime</h3><p>Portfolio-wide and cross-sleeve relationships move together when volatility rises.</p></div><span class="panel-menu">Use slicer in Power BI</span></div><div class="chart-frame"><img src="../reports/charts/corr_by_regime.png" alt="Correlation by volatility regime" loading="lazy"></div><p class="scope-note">Preview behavior: the selector updates the KPI scope above. In the live Power BI build, the same scope filters the correlation matrix below through <b>corr_long[scope]</b>.</p><div class="regime-cards">{regime_cards()}</div></section>
        <div class="panel-grid equal" style="margin-top:16px">{chart("Correlation matrix", "Full-sample heatmap · fixed −1 to +1 color scale", "corr_heatmap.png", "tall")}{chart("Rolling correlation", "90-day average pairwise and equity–crypto correlation", "rolling_correlation.png", "tall")}</div>
      </section>

      <section class="page" id="regimes">
        <div class="hero"><div><div class="eyebrow">State detection · Page 3 of 3</div><h2>Regime timeline</h2><p>Make volatility states visible, measurable, and operational.</p></div><div class="hero-controls"><span class="chip">K-Means · k = 4</span><span class="status-badge {current_status}"><span class="dot"></span>Current: {esc(current_regime)}</span></div></div>
        <div class="timeline-kpis"><div class="timeline-stat"><div class="eyebrow">Current regime</div><strong>{esc(current_regime)}</strong><span>{esc(latest.get("days_in_regime", "—"))} trading days in spell</span></div><div class="timeline-stat"><div class="eyebrow">Current VIX</div><strong>{num(latest.get("VIXCLS")):.2f}</strong><span>CBOE volatility index</span></div><div class="timeline-stat"><div class="eyebrow">Episodes observed</div><strong>{len(episodes)}</strong><span>Contiguous regime spells</span></div><div class="timeline-stat"><div class="eyebrow">Avg episode length</div><strong>{sum(num(row["n_days"]) for row in episodes) / len(episodes):.0f}d</strong><span>Across all labeled states</span></div></div>
        <div class="panel-grid equal">{chart("Volatility regime timeline", "30-day portfolio volatility with K-Means bands", "vol_regime_timeline.png", "tall")}{chart("VaR backtest", "Observed breaches versus the 5% design level", "var_backtest.png", "tall")}</div>
        <div class="panel-grid equal"><section class="panel table-panel"><div class="panel-heading"><div><h3>Recent regime episodes</h3><p>Most recent spells first · click through in Power BI</p></div></div><div class="table-scroll"><table><thead><tr><th>Regime</th><th>Start</th><th>End</th><th>Length</th></tr></thead><tbody>{episode_rows()}</tbody></table></div></section><section class="panel"><div class="panel-heading"><div><h3>Regime playbook</h3><p>Operational interpretation of the model output</p></div></div><div class="insight" style="margin-top:2px"><strong><span class="regime-dot" style="background:var(--low);display:inline-block;margin-right:7px"></span>Low / Normal</strong><p>Monitor carry and concentration; do not let calm volatility mechanically increase risk limits.</p></div><div class="insight" style="border-color:var(--amber);margin-top:0"><strong><span class="regime-dot" style="background:var(--amber);display:inline-block;margin-right:7px"></span>Elevated</strong><p>Review exposures and hedge capacity; stress-test using the higher-correlation matrix.</p></div><div class="insight" style="border-color:var(--red);margin-top:0"><strong><span class="regime-dot" style="background:var(--red);display:inline-block;margin-right:7px"></span>Crisis</strong><p>Size to crisis volatility, activate the pre-agreed de-risking playbook, and monitor VaR breach clusters.</p></div></section></div>
      </section>
      <div class="footer"><span>Virtual representation · generated from data/processed/powerbi · as of {esc(as_of)}</span><span>Method: daily simple returns · 252 trading days · historical VaR · K-Means regimes</span></div>
    </div>
  </main>
</div>
<script>
const scopeMetrics = {json_for_script(scope_metrics)};
const navLabels = {{overview:'Portfolio overview', correlation:'Correlation & diversification', regimes:'Regime timeline'}};
const pages = [...document.querySelectorAll('.page')];
const navButtons = [...document.querySelectorAll('[data-page-target]')];
function showPage(name) {{
  pages.forEach(page => page.classList.toggle('active', page.id === name));
  navButtons.forEach(button => button.classList.toggle('active', button.dataset.pageTarget === name));
  document.getElementById('crumb').textContent = navLabels[name];
  window.scrollTo({{top:0, behavior:'smooth'}});
}}
navButtons.forEach(button => button.addEventListener('click', () => showPage(button.dataset.pageTarget)));
function updateScope() {{
  const name = document.getElementById('scopeFilter').value;
  const m = scopeMetrics[name] || scopeMetrics['Full sample'];
  document.getElementById('scopeCorr').textContent = Number(m.corr).toFixed(2);
  document.getElementById('scopeCross').textContent = Number(m.cross).toFixed(2);
  document.getElementById('scopeVol').textContent = (Number(m.vol) * 100).toFixed(1) + '%';
  document.getElementById('scopePairs').textContent = m.pairs;
  document.getElementById('scopeCorrNote').textContent = name + ' · off-diagonal average';
}}
document.getElementById('scopeFilter').addEventListener('change', updateScope);
updateScope();
</script>
</body>
</html>
'''

OUT.write_text(html_doc, encoding="utf-8")
print(f"[mockup] wrote {OUT} ({len(html_doc):,} characters)")
