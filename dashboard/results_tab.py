"""Results tab: the committed 30-round simulation (also reused for What-if runs)."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import charts
from dashboard.data import CONDITION_LABELS, CONDITIONS, LABELS, STRATEGIES, Run, mean_time_table, summary_tables


def _pct(x: float) -> str:
    return "-" if pd.isna(x) else f"{x * 100:.0f} %"


def headline(run: Run, tables: dict) -> None:
    rel, comp = tables["reliability"], tables["comparisons"]
    sev = rel[rel["condition"] == "severe"].set_index("strategy")
    means = mean_time_table(run)
    vs_static = comp[(comp["baseline"] == "static_network") & (comp["condition"] == "severe")].iloc[0]
    timeouts = rel.groupby("strategy")["transfers_with_timeout"].sum()
    worst_other = sev.drop("proposed")["chose_degraded_rate"].max()

    c = st.columns(4)
    diff = vs_static["mean_diff"]   # baseline - proposed: positive = proposed faster
    c[0].metric("Proposed, severe: mean time", f"{means.loc['severe', 'proposed']:.2f} s",
                f"{'' if diff > 0 else '-'}{abs(diff):.2f} s {'faster' if diff > 0 else 'slower'} than static "
                f"network ({'significant' if vs_static['significant'] else 'not significant'})",
                delta_color="normal")
    c[1].metric("Picked the degraded provider (severe)", _pct(sev.loc["proposed", "chose_degraded_rate"]),
                f"baselines up to {_pct(worst_other)}", delta_color="off")
    c[2].metric("Transfers with a timeout", int(timeouts.get("proposed", 0)),
                f"baselines: {', '.join(str(int(timeouts[s])) for s in STRATEGIES if s != 'proposed')}",
                delta_color="off")
    c[3].metric("Transfers simulated", f"{len(run.df)}",
                f"{run.measured['round_id'].count()} measured", delta_color="off")


def render_body(run: Run, key: str, tables: dict | None = None) -> None:
    tables = tables or summary_tables(run)
    headline(run, tables)

    st.subheader("End-to-end transfer time")
    st.caption("Every dot is one transfer (probing + upload, including failovers). "
               "The 60 s dots are transfers whose first attempt timed out.")
    log_y = st.toggle("Log scale", key=f"{key}-log")
    st.plotly_chart(charts.time_by_condition(run.measured, log_y), width="stretch", key=f"{key}-box")

    var = tables["variability"].set_index(["condition", "strategy"])
    rows = []
    for cond in CONDITIONS:
        row = {"Condition": CONDITION_LABELS[cond]}
        for s in STRATEGIES:
            v = var.loc[(cond, s)]
            row[LABELS[s]] = f"{v['mean']:.2f} ({v['p90']:.1f})"
        rows.append(row)
    st.caption("Mean seconds, 90th percentile in brackets")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    st.subheader("Is the proposed method faster? (paired comparison)")
    st.caption("Each cell: mean of (baseline - proposed) over the paired rounds. Blue = proposed faster, "
               "orange = proposed slower. * = significant after Holm correction "
               f"({run.cfg['analysis']['primary_test']}, alpha {run.cfg['analysis']['alpha']}).")
    st.plotly_chart(charts.paired_heatmap(tables["comparisons"]), width="stretch", key=f"{key}-heat")

    left, right = st.columns(2)
    with left:
        st.subheader("Poor first choices")
        st.caption("How often a strategy's first choice was the provider whose path was degraded.")
        st.plotly_chart(charts.degraded_choice(tables["reliability"]), width="stretch",
                        key=f"{key}-deg")
    with right:
        st.subheader("Failures")
        st.caption("Transfers that hit a timeout or needed failover (all transfers still succeeded).")
        st.plotly_chart(charts.failures(tables["reliability"]), width="stretch", key=f"{key}-fail")

    st.subheader("Which provider each strategy chose first")
    st.plotly_chart(charts.provider_choice(tables["provider_choice"]), width="stretch",
                    key=f"{key}-choice")

    with st.expander("All tables"):
        for name in ("comparisons", "variability", "reliability", "degraded_choice_pairs", "probe_overhead"):
            st.markdown(f"**{name}**")
            st.dataframe(tables[name].round(4), hide_index=True, width="stretch")


def render(run: Run, variants: pd.DataFrame | None) -> None:
    st.markdown(
        f"**Run `{run.name}`**: seed {run.schedule['seed']}, "
        f"{run.cfg['experiment']['rounds_per_condition']} measured rounds per condition, "
        f"{len(run.schedule['blocks'])} blocks, code version `{run.schedule['code_version']}`.")
    tables = summary_tables(run)
    render_body(run, "results", tables)

    if variants is not None and len(variants):
        st.subheader("Effect of the probing change")
        st.caption("Same seed and rounds; only the probe settings differ. D is the adopted setting "
                   "(the run above).")
        st.plotly_chart(charts.probe_variants(variants), width="stretch", key="results-variants")
        show = variants.rename(columns={"variant": "Variant", "probe_s": "Probe time (s)",
                                        "e2e_s": "End-to-end (s)", "chose_degraded": "Chose degraded",
                                        "failovers": "Failovers"})
        show["Chose degraded"] = show["Chose degraded"].map(_pct)
        st.dataframe(show.round(2), hide_index=True, width="stretch")
