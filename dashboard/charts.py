"""Plotly figures for the dashboard. Same strategy colours and labels as the PNG
figures in rmc.analysis.plots. Colour is never the only cue: strategy and provider
names are always on an axis, in a legend or in the hover text."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from dashboard.data import CONDITION_LABELS, CONDITIONS, COLORS, LABELS, STRATEGIES

PROVIDER_COLORS = {"aws_s3": "#2a78d6", "gcs": "#1baf7a", "b2": "#eda100"}
PROVIDER_LABELS = {"aws_s3": "aws_s3 (near)", "gcs": "gcs (medium)", "b2": "b2 (far, cheapest)"}
PART_COLORS = {"network_part": "#2a78d6", "cost_part": "#1baf7a", "risk_part": "#d64545"}
PART_LABELS = {"network_part": "w_N x N (network)", "cost_part": "w_C x C (cost)", "risk_part": "- w_R x rho (risk)"}


def _layout(fig: go.Figure, height: int = 380, subplots: bool = False, **kw) -> go.Figure:
    """Shared layout. With subplot titles, the legend sits above them instead of on top."""
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=80 if subplots else 40, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.12 if subplots else 1.02, x=0,
                                  traceorder="normal"), **kw)
    return fig


def time_by_condition(m: pd.DataFrame, log_y: bool = False) -> go.Figure:
    """Box + points of end-to-end time, one panel per condition."""
    fig = make_subplots(rows=1, cols=3, shared_yaxes=True,
                        subplot_titles=[CONDITION_LABELS[c] for c in CONDITIONS], horizontal_spacing=0.03)
    for col, cond in enumerate(CONDITIONS, start=1):
        sub = m[m["condition"] == cond]
        for s in STRATEGIES:
            d = sub[sub["strategy"] == s]
            fig.add_trace(go.Box(
                y=d["end_to_end_time_s"], name=LABELS[s], marker_color=COLORS[s], boxpoints="all",
                jitter=0.4, pointpos=0, marker_size=4, line_width=1, showlegend=False,
                customdata=d[["block_id", "round_id", "first_choice", "n_failed_attempts"]],
                hovertemplate=("%{x}<br>%{y:.2f} s<br>%{customdata[0]} round %{customdata[1]}"
                               "<br>first choice %{customdata[2]}<br>failed attempts %{customdata[3]}"
                               "<extra></extra>")), row=1, col=col)
    fig.update_yaxes(title_text="End-to-end time (s)", row=1, col=1)
    if log_y:
        fig.update_yaxes(type="log")
    return _layout(fig, 430, subplots=True)


def paired_heatmap(comp: pd.DataFrame) -> go.Figure:
    """baseline x condition: mean(baseline - proposed); * = significant after correction."""
    baselines = [s for s in STRATEGIES if s != "proposed"]
    z, text = [], []
    for b in baselines:
        zr, tr = [], []
        for c in CONDITIONS:
            row = comp[(comp["baseline"] == b) & (comp["condition"] == c)].iloc[0]
            zr.append(row["mean_diff"])
            tr.append(f"{row['mean_diff']:+.2f} s{' *' if row['significant'] else ''}"
                      f"<br>p_adj={row['primary_p_adj']:.3f}")
        z.append(zr)
        text.append(tr)
    lim = max(abs(v) for r in z for v in r) or 1
    fig = go.Figure(go.Heatmap(
        z=z, x=[CONDITION_LABELS[c] for c in CONDITIONS], y=[LABELS[b] for b in baselines],
        text=text, texttemplate="%{text}", zmin=-lim, zmax=lim,
        colorscale=[[0, "#c2410c"], [0.5, "#f5f5f4"], [1, "#2563eb"]],
        colorbar=dict(title="s"), hovertemplate="%{y} vs proposed, %{x}<br>%{text}<extra></extra>"))
    fig.update_yaxes(autorange="reversed")
    return _layout(fig, 300)


def degraded_choice(rel: pd.DataFrame) -> go.Figure:
    d = rel[rel["condition"].isin(["mild", "severe"])]
    fig = go.Figure()
    for s in STRATEGIES:
        x = d[d["strategy"] == s]
        fig.add_bar(x=[CONDITION_LABELS[c] for c in x["condition"]], y=x["chose_degraded_rate"] * 100,
                    name=LABELS[s], marker_color=COLORS[s],
                    text=[f"{v:.0f}%" for v in x["chose_degraded_rate"] * 100], textposition="outside",
                    hovertemplate=f"{LABELS[s]}<br>%{{x}}: %{{y:.0f}} %<extra></extra>")
    fig.update_layout(barmode="group", yaxis=dict(title="Rounds choosing the degraded provider (%)",
                                                  range=[0, 100]))
    return _layout(fig, 360)


def failures(rel: pd.DataFrame) -> go.Figure:
    tot = rel.groupby("strategy")[["transfers_with_timeout", "transfers_with_failover"]].sum().reindex(STRATEGIES)
    fig = go.Figure()
    fig.add_bar(x=[LABELS[s] for s in tot.index], y=tot["transfers_with_timeout"], name="with a timeout",
                marker_color="#d64545")
    fig.add_bar(x=[LABELS[s] for s in tot.index], y=tot["transfers_with_failover"], name="needed failover",
                marker_color="#9ca3af")
    fig.update_layout(barmode="group", yaxis_title="Transfers (all conditions)")
    return _layout(fig, 320)


def provider_choice(choice: pd.DataFrame) -> go.Figure:
    """First choice per strategy, one panel per condition, stacked by provider."""
    fig = make_subplots(rows=1, cols=3, shared_yaxes=True,
                        subplot_titles=[CONDITION_LABELS[c] for c in CONDITIONS], horizontal_spacing=0.03)
    for col, cond in enumerate(CONDITIONS, start=1):
        sub = choice[choice["condition"] == cond]
        for prov, color in PROVIDER_COLORS.items():
            d = sub[sub["first_choice"] == prov].set_index("strategy").reindex(STRATEGIES).fillna(0)
            fig.add_bar(x=[LABELS[s] for s in STRATEGIES], y=d["rounds"], name=PROVIDER_LABELS[prov],
                        marker_color=color, showlegend=col == 1, legendgroup=prov,
                        hovertemplate=f"%{{x}}<br>{prov}: %{{y}} rounds<extra></extra>", row=1, col=col)
    fig.update_layout(barmode="stack")
    fig.update_yaxes(title_text="Rounds", row=1, col=1)
    return _layout(fig, 400, subplots=True)


def probe_variants(df: pd.DataFrame) -> go.Figure:
    """Stacked bar: probing vs the rest of the proposed method's end-to-end time."""
    fig = go.Figure()
    fig.add_bar(x=df["variant"], y=df["probe_s"], name="probing", marker_color="#eb6834",
                hovertemplate="%{x}<br>probing %{y:.2f} s<extra></extra>")
    fig.add_bar(x=df["variant"], y=df["e2e_s"] - df["probe_s"], name="upload (incl. failover)",
                marker_color="#2a78d6", hovertemplate="%{x}<br>upload %{y:.2f} s<extra></extra>")
    fig.update_layout(barmode="stack", yaxis_title="Proposed: mean end-to-end time (s)")
    return _layout(fig, 340)


def score_breakdown(scores: pd.DataFrame) -> go.Figure:
    """One horizontal bar per provider, split into the three weighted terms; the
    diamond is the total score (network + cost - risk)."""
    order = scores.sort_values("rank", ascending=False)
    labels = [f"#{r} {p}" for p, r in zip(order["provider"], order["rank"])]
    fig = go.Figure()
    for part in ("network_part", "cost_part", "risk_part"):
        fig.add_bar(y=labels, x=order[part], orientation="h", name=PART_LABELS[part],
                    marker_color=PART_COLORS[part], hovertemplate="%{y}<br>%{x:.3f}<extra>" + PART_LABELS[part] + "</extra>")
    fig.add_scatter(y=labels, x=order["score"], mode="markers+text", name="score",
                    marker=dict(symbol="diamond", size=12, color="#111827"),
                    text=[f"{v:.3f}" for v in order["score"]], textposition="middle right",
                    hovertemplate="%{y}<br>score %{x:.3f}<extra></extra>")
    fig.update_layout(barmode="relative", xaxis_title="Contribution to the score")
    return _layout(fig, 260)


def block_timeline(block_df: pd.DataFrame, highlight_round: int | None = None) -> go.Figure:
    fig = go.Figure()
    for s in STRATEGIES:
        d = block_df[block_df["strategy"] == s].sort_values("round_id")
        fig.add_scatter(x=d["round_id"], y=d["end_to_end_time_s"], mode="lines+markers", name=LABELS[s],
                        line=dict(color=COLORS[s], width=1.5), marker=dict(size=7),
                        customdata=d[["first_choice", "n_failed_attempts"]],
                        hovertemplate=(f"{LABELS[s]}<br>round %{{x}}: %{{y:.2f}} s<br>first choice "
                                       "%{customdata[0]}<br>failed attempts %{customdata[1]}<extra></extra>"))
    warm = block_df[block_df["is_warmup"]]["round_id"]
    if len(warm):
        fig.add_vrect(x0=warm.min() - 0.5, x1=warm.max() + 0.5, fillcolor="#9ca3af", opacity=0.15,
                      line_width=0, annotation_text="warm-up", annotation_position="top left")
    if highlight_round is not None:
        fig.add_vline(x=highlight_round, line_dash="dot", line_color="#111827")
    fig.update_layout(xaxis=dict(title="Round", dtick=1), yaxis_title="End-to-end time (s)")
    return _layout(fig, 340)


def risk_timeline(hist: pd.DataFrame, highlight_round: int | None = None) -> go.Figure:
    fig = go.Figure()
    for prov, color in PROVIDER_COLORS.items():
        d = hist[hist["provider"] == prov]
        fig.add_scatter(x=d["round_id"], y=d["rho"], mode="lines+markers", name=PROVIDER_LABELS[prov],
                        line=dict(color=color), hovertemplate=f"{prov}<br>round %{{x}}: rho %{{y:.3f}}<extra></extra>")
    severe = hist[hist["profile"] == "severe"]["round_id"].unique()
    for r in severe:
        fig.add_vrect(x0=r - 0.5, x1=r + 0.5, fillcolor="#d64545", opacity=0.08, line_width=0)
    if highlight_round is not None:
        fig.add_vline(x=highlight_round, line_dash="dot", line_color="#111827")
    fig.update_layout(xaxis=dict(title="Round (red band = severe weight profile)", dtick=1),
                      yaxis=dict(title="Risk rho (proposed's view)", rangemode="tozero"))
    return _layout(fig, 300)


def whatif_delta(ref: pd.DataFrame, new: pd.DataFrame) -> go.Figure:
    """Mean end-to-end time per strategy and condition: reported run vs what-if run."""
    fig = make_subplots(rows=1, cols=3, shared_yaxes=True,
                        subplot_titles=[CONDITION_LABELS[c] for c in CONDITIONS], horizontal_spacing=0.03)
    for col, cond in enumerate(CONDITIONS, start=1):
        x = [LABELS[s] for s in STRATEGIES]
        fig.add_bar(x=x, y=ref.loc[cond, STRATEGIES], name="reported run", marker_color="#cbd5e1",
                    showlegend=col == 1, legendgroup="ref", row=1, col=col,
                    hovertemplate="%{x}<br>reported %{y:.2f} s<extra></extra>")
        fig.add_bar(x=x, y=new.loc[cond, STRATEGIES], name="what-if run",
                    marker_color=[COLORS[s] for s in STRATEGIES], showlegend=col == 1, legendgroup="new",
                    row=1, col=col, hovertemplate="%{x}<br>what-if %{y:.2f} s<extra></extra>")
    fig.update_layout(barmode="group")
    fig.update_yaxes(title_text="Mean end-to-end time (s)", row=1, col=1)
    return _layout(fig, 400, subplots=True)
