"""Walkthrough tab: replay one decision at a time and show how the method thinks."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from dashboard import charts
from dashboard.data import (CONDITION_LABELS, LABELS, STRATEGIES, Run, block_label, decision_detail,
                            risk_history, round_overview)

RULES = {
    "proposed": "Scores every provider with **w_N x N + w_C x C - w_R x rho** and adapts the weights "
                "to the network condition.",
    "static_network": "Ranks providers by the live **network score N only**: no cost, no risk history, "
                      "no adaptation.",
    "cost_only": "Always tries the **cheapest** provider first (b2), whatever the network does.",
    "nearest": "Always tries the provider with the **lowest natural RTT** (fixed once at the start).",
    "random": "Tries the providers in a **random** (seeded) order.",
}


def _outcome(a) -> str:
    if a.timed_out:
        return "timed out"
    if not a.success:
        return "failed"
    if a.verified == 0:
        return "checksum mismatch"
    return "ok, verified"


def _highlights(run: Run) -> list[tuple[str, str, int]]:
    """(label, block_id, round_id) of moments worth showing in a demo (proposed strategy)."""
    df = run.df.sort_values("id")
    p = df[df["strategy"] == "proposed"]
    cond = {b["block_id"]: b["condition"] for b in run.schedule["blocks"]}
    out: list[tuple[str, str, int]] = []
    for b, g in p.groupby("block_id", sort=False):
        prev = None
        for r in g.itertuples():
            if prev == "normal" and r.weight_profile == "severe":
                note = " (false alarm: nothing is degraded)" if cond[b] == "natural" else ""
                out.append((f"{b} ({cond[b]}) R{r.round_id}: switches to the severe weight profile{note}",
                            b, r.round_id))
                break
            prev = r.weight_profile
    for r in p[p["n_failed_attempts"] > 0].itertuples():
        out.append((f"{r.block_id} ({cond[r.block_id]}) R{r.round_id}: first choice fails, fails over",
                    r.block_id, r.round_id))
    m = df[~df["is_warmup"] & df["condition"].isin(["mild", "severe"])]
    for (b, rid), g in m.groupby(["block_id", "round_id"], sort=False):
        s = g.set_index("strategy")
        if not s.loc["proposed", "chose_degraded"] and s.drop("proposed")["chose_degraded"].sum() >= 2:
            out.append((f"{b} ({cond[b]}) R{rid}: avoids the degraded provider while others pick it", b, rid))
    return out[:30]


def _set(block: str, rnd: int) -> None:
    st.session_state["wt_block"], st.session_state["wt_round"] = block, rnd


def _step(run: Run, delta: int) -> None:
    keys = list(run.df.sort_values("id")[["block_id", "round_id"]].drop_duplicates().itertuples(index=False))
    cur = (st.session_state["wt_block"], st.session_state["wt_round"])
    i = keys.index(cur) if cur in keys else 0
    _set(*keys[max(0, min(len(keys) - 1, i + delta))])


def _jump(run: Run) -> None:
    label = st.session_state.get("wt_highlight")
    for text, b, r in _highlights(run):
        if text == label:
            _set(b, r)


def render(run: Run, key: str = "wt") -> None:
    blocks = {b["block_id"]: b for b in run.schedule["blocks"]}
    if st.session_state.get("wt_block") not in blocks:
        _set(run.schedule["blocks"][0]["block_id"], 1)

    st.markdown("Pick a moment in the simulation and see each step of the decision: what was measured, "
                "how the providers were scored, and what happened during the upload.")
    hl = _highlights(run)
    if hl:
        st.selectbox("Jump to a highlight", [h[0] for h in hl], index=None, placeholder="Interesting moments...",
                     key="wt_highlight", on_change=_jump, args=(run,))

    c1, c2, c3, c4, c5 = st.columns([3, 2, 2, 1, 1], vertical_alignment="bottom")
    c1.selectbox("Block", list(blocks), format_func=lambda b: block_label(blocks[b]), key="wt_block")
    rounds = sorted(run.df[run.df["block_id"] == st.session_state["wt_block"]]["round_id"].unique())
    if st.session_state.get("wt_round") not in rounds:
        st.session_state["wt_round"] = rounds[0]
    c2.selectbox("Round", rounds, key="wt_round",
                 format_func=lambda r: f"{r}{' (warm-up)' if r <= run.cfg['experiment']['warmup_rounds_per_block'] else ''}")
    c3.selectbox("Strategy", STRATEGIES, format_func=LABELS.get, key="wt_strategy")
    c4.button("Previous", on_click=_step, args=(run, -1), width="stretch")
    c5.button("Next", on_click=_step, args=(run, 1), width="stretch")

    block_id, rnd, strategy = st.session_state["wt_block"], st.session_state["wt_round"], st.session_state["wt_strategy"]
    block = blocks[block_id]
    t = run.df[(run.df["block_id"] == block_id) & (run.df["round_id"] == rnd) & (run.df["strategy"] == strategy)]
    if t.empty:
        st.warning("No transfer recorded for this selection.")
        return
    d = decision_detail(run, int(t["id"].iloc[0]))
    tr = d["transfer"]

    deg = block["degraded_provider"]
    situation = (f"**{CONDITION_LABELS[block['condition']]}** network"
                 + (f": the path to **{deg}** is degraded "
                    f"(+{run.cfg['netem']['conditions'][block['condition']]['rtt_increase_ratio'] * 100:.0f} % RTT, "
                    f"+{run.cfg['netem']['conditions'][block['condition']]['loss_pct']:g} % loss)." if deg
                    else ": nothing is degraded."))
    st.info(f"{situation}  Strategy: **{LABELS[strategy]}**. {RULES[strategy]}")

    # ---- step 1: probe ---------------------------------------------------------------
    st.markdown("#### 1. Measure the network")
    if strategy in ("proposed", "static_network"):
        if d["probes_fresh"]:
            st.write(f"Probed all three providers, which took **{tr['probe_time_s']:.2f} s** "
                     "(counted in the end-to-end time).")
        else:
            st.write(f"Reused the probes measured in **round {d['probe_source_round']}** (younger than "
                     f"{run.cfg['probe']['max_age_s']} s), so probing cost **nothing** this time.")
        probes = d["probes"].copy()
        probes["provider"] = [p + ("  (degraded path)" if g else "") for p, g in
                              zip(probes["provider"], probes["degraded_path"])]
        st.dataframe(probes.drop(columns="degraded_path").rename(columns={
            "provider": "Provider", "rtt_ms": "RTT (ms)", "baseline_rtt_ms": "Natural RTT (ms)",
            "throughput_mbps": "Throughput (Mbit/s)", "loss_pct": "Loss (%)"}).round(1),
            hide_index=True, width="stretch")
        st.caption(f"Loss comes from {run.cfg['probe']['samples']} connection samples, so it moves in "
                   f"steps of {100 / run.cfg['probe']['samples']:.0f} %: one lost sample is enough to look "
                   "like heavy loss.")
    else:
        st.write("This strategy does not measure the network.")

    # ---- step 2: weights -------------------------------------------------------------
    if strategy == "proposed":
        st.markdown("#### 2. Choose the weights")
        w = d["weights"]
        cols = st.columns(4)
        cols[0].metric("Profile", d["profile"])
        cols[1].metric("w_N network", f"{w['network']:.2f}")
        cols[2].metric("w_C cost", f"{w['cost']:.2f}")
        cols[3].metric("w_R risk", f"{w['risk']:.2f}")
        ind = run.cfg["weights"]["degradation_indicator"]
        if d["trigger_reasons"]:
            st.write("The network looks degraded, so cost matters less and risk matters more:")
            for r in d["trigger_reasons"]:
                st.write(f"- {r}")
        else:
            st.write(f"No provider has RTT >= {1 + ind['rtt_increase_ratio']:g} x its natural RTT or loss >= "
                     f"{ind['loss_pct']:g} %, so the **normal** profile applies (cost still counts).")

    # ---- step 3: score -----------------------------------------------------------------
    n = 3 if strategy == "proposed" else 2
    if d["scores"] is not None:
        st.markdown(f"#### {n}. Score and rank the providers")
        st.plotly_chart(charts.score_breakdown(d["scores"]), width="stretch", key=f"{key}-score")
        cols = {"rank": "Rank", "provider": "Provider", "N": "N (network)", "C": "C (cost)",
                "rho": "rho (risk)", "score": "Score"}
        table = d["scores"][list(cols)].rename(columns=cols)
        if strategy == "static_network":
            table = table.drop(columns=["C (cost)", "rho (risk)"])
        st.dataframe(table.round(3), hide_index=True, width="stretch")
    else:
        st.markdown(f"#### {n}. Rank the providers")
    st.write("Ranking (failover order): " + " -> ".join(f"**{p}**" for p in d["ranking"]))

    # ---- step 4: upload ----------------------------------------------------------------
    st.markdown(f"#### {n + 1}. Upload (with failover)")
    att = d["attempts"]
    st.dataframe(pd.DataFrame({
        "Attempt": att["attempt_no"], "Provider": att["provider"],
        "Time (s)": att["transfer_time_s"].round(2), "Outcome": [_outcome(a) for a in att.itertuples()]}),
        hide_index=True, width="stretch")
    upload = tr["end_to_end_time_s"] - tr["probe_time_s"]
    st.success(f"End-to-end **{tr['end_to_end_time_s']:.2f} s** = probing {tr['probe_time_s']:.2f} s + "
               f"upload {upload:.2f} s. Stored on **{tr['provider_final']}**"
               + (f" after {int(tr['n_failed_attempts'])} failed attempt(s)." if tr["n_failed_attempts"] else "."))

    # ---- same round, all strategies ------------------------------------------------------
    st.markdown("#### The same round, every strategy")
    ov = round_overview(run, block_id, rnd)
    ov["strategy"] = ov["strategy"].map(LABELS)
    ov["Picked degraded"] = (ov["first_choice"] == deg) if deg else False
    ov["weight_profile"] = ov["weight_profile"].fillna("-")
    st.dataframe(ov.rename(columns={
        "order": "Ran", "strategy": "Strategy", "first_choice": "First choice", "provider_final": "Stored on",
        "probe_time_s": "Probing (s)", "end_to_end_time_s": "End-to-end (s)",
        "n_failed_attempts": "Failed attempts", "timed_out": "Timeout", "weight_profile": "Profile"}).round(2),
        hide_index=True, width="stretch")

    # ---- whole block ----------------------------------------------------------------------
    st.markdown(f"#### Across block {block_id}")
    bd = run.df[run.df["block_id"] == block_id]
    st.plotly_chart(charts.block_timeline(bd, rnd), width="stretch", key=f"{key}-timeline")
    hist = risk_history(run, block_id)
    if len(hist):
        st.caption("How the proposed method's risk estimate for each provider evolved in this block "
                   "(from its own upload history).")
        st.plotly_chart(charts.risk_timeline(hist, rnd), width="stretch", key=f"{key}-risk")
