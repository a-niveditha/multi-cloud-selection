"""What-if tab: change the settings, run the real simulation code, compare with the
reported run. Results here are exploratory; the reported results are the committed run."""
from __future__ import annotations

import copy
import json
import tempfile
from pathlib import Path

import streamlit as st

from dashboard import charts, results_tab
from dashboard.data import Run, load_run, mean_time_table
from rmc.simulation import simulate

MAX_CACHED = 6
# Same rounds as the reported run, so a what-if differs from it only in the settings
# that were changed. A 30-round run takes a few seconds.
DEFAULT_ROUNDS = 30


def _normalise(w: dict[str, float]) -> dict[str, float]:
    total = sum(w.values())
    if total <= 0:
        raise ValueError("at least one weight must be above 0")
    return {k: round(v / total, 6) for k, v in w.items()}


def build_config(base: dict, p: dict) -> dict:
    """The run configuration for a set of What-if parameters (also used by the tests)."""
    cfg = copy.deepcopy(base)
    cfg["experiment"]["rounds_per_condition"] = p["rounds"]
    cfg["weights"]["profiles"]["normal"] = _normalise(p["normal"])
    cfg["weights"]["profiles"]["severe"] = _normalise(p["severe"])
    cfg["weights"]["degradation_indicator"] = {"rtt_increase_ratio": p["trigger_rtt"],
                                               "loss_pct": p["trigger_loss"]}
    cfg["probe"].update(samples=p["samples"], throughput_bytes=p["probe_kib"] * 1024, max_age_s=p["max_age"])
    cfg["risk"]["window_k"] = p["window_k"]
    for cond in ("mild", "severe"):
        cfg["netem"]["conditions"][cond] = {"rtt_increase_ratio": p[f"{cond}_rtt"], "loss_pct": p[f"{cond}_loss"]}
    return cfg


def run_whatif(base: dict, p: dict, progress=None) -> Run:
    cfg = build_config(base, p)
    out = Path(tempfile.mkdtemp(prefix="rmc-whatif-"))
    simulate(cfg, p["seed"], out / "run", out / "backups",
             on_block=(lambda i, n, b, s: progress((i + 1) / n, f"Block {b['block_id']} done ({i + 1}/{n})"))
             if progress else None)
    return load_run(out / "run", name="what-if")


def defaults(base: dict) -> dict:
    w, ind, pr, nc = base["weights"], base["weights"]["degradation_indicator"], base["probe"], base["netem"]["conditions"]
    return {
        "seed": base["simulation"]["seed"], "rounds": DEFAULT_ROUNDS,
        "normal": dict(w["profiles"]["normal"]), "severe": dict(w["profiles"]["severe"]),
        "trigger_rtt": ind["rtt_increase_ratio"], "trigger_loss": ind["loss_pct"],
        "samples": pr["samples"], "probe_kib": pr["throughput_bytes"] // 1024, "max_age": pr["max_age_s"],
        "window_k": base["risk"]["window_k"],
        "mild_rtt": nc["mild"]["rtt_increase_ratio"], "mild_loss": nc["mild"]["loss_pct"],
        "severe_rtt": nc["severe"]["rtt_increase_ratio"], "severe_loss": nc["severe"]["loss_pct"],
    }


def _weights_inputs(label: str, d: dict, key: str) -> dict:
    st.markdown(f"**{label} profile**")
    c = st.columns(3)
    return {"network": c[0].slider("network", 0.0, 1.0, float(d["network"]), 0.05, key=f"{key}-n"),
            "cost": c[1].slider("cost", 0.0, 1.0, float(d["cost"]), 0.05, key=f"{key}-c"),
            "risk": c[2].slider("risk", 0.0, 1.0, float(d["risk"]), 0.05, key=f"{key}-r")}


def render(base_cfg: dict, reported: Run) -> None:
    st.markdown("Change the method or the environment and rerun the **whole experiment** with the real "
                "simulation code. Results here are exploratory: the reported results are the committed "
                "run on the Results tab.")
    d = defaults(base_cfg)
    with st.form("whatif"):
        a, b, c = st.columns(3)
        with a:
            st.markdown("##### Method: weights")
            normal = _weights_inputs("Normal", d["normal"], "wn")
            severe = _weights_inputs("Severe", d["severe"], "ws")
            st.caption("Each profile is rescaled to sum to 1.")
        with b:
            st.markdown("##### Method: trigger, risk, probing")
            trigger_rtt = st.slider("Severe profile if RTT is at least x natural", 1.1, 3.0,
                                    1 + float(d["trigger_rtt"]), 0.1) - 1
            trigger_loss = st.slider("... or measured loss at least (%)", 0.5, 50.0, float(d["trigger_loss"]), 0.5)
            window_k = st.slider("Risk history window (attempts)", 3, 30, int(d["window_k"]))
            samples = st.slider("Probe samples per provider", 1, 10, int(d["samples"]))
            probe_kib = st.select_slider("Throughput probe size (KiB)", [32, 64, 128, 256, 512, 1024],
                                         int(d["probe_kib"]))
            max_age = st.slider("Reuse probes for up to (s, 0 = never)", 0, 300, int(d["max_age"]), 10)
        with c:
            st.markdown("##### Environment and run")
            mild_rtt = st.slider("Mild: extra RTT (%)", 0, 200, int(d["mild_rtt"] * 100), 5) / 100
            mild_loss = st.slider("Mild: extra loss (%)", 0.0, 10.0, float(d["mild_loss"]), 0.5)
            severe_rtt = st.slider("Severe: extra RTT (%)", 0, 300, int(d["severe_rtt"] * 100), 5) / 100
            severe_loss = st.slider("Severe: extra loss (%)", 0.0, 20.0, float(d["severe_loss"]), 0.5)
            rounds = st.select_slider("Measured rounds per condition", [9, 15, 30], d["rounds"])
            seed = st.number_input("Seed", 0, 10_000, int(d["seed"]))
        submitted = st.form_submit_button("Run simulation", type="primary")

    params = {"seed": int(seed), "rounds": rounds, "normal": normal, "severe": severe,
              "trigger_rtt": round(trigger_rtt, 3), "trigger_loss": trigger_loss, "samples": samples,
              "probe_kib": probe_kib, "max_age": max_age, "window_k": window_k, "mild_rtt": mild_rtt,
              "mild_loss": mild_loss, "severe_rtt": severe_rtt, "severe_loss": severe_loss}
    cache: dict = st.session_state.setdefault("whatif_cache", {})
    if submitted:
        key = json.dumps(params, sort_keys=True)
        if key not in cache:
            try:
                bar = st.progress(0.0, "Starting simulation...")
                cache[key] = run_whatif(base_cfg, params, progress=bar.progress)
                bar.empty()
            except ValueError as e:
                st.error(str(e))
                return
            while len(cache) > MAX_CACHED:
                cache.pop(next(iter(cache)))
        if st.session_state.get("whatif_last") != key:
            st.session_state["whatif_last"] = key
            st.rerun()   # so the Walkthrough tab (rendered earlier) offers the new run too

    last = st.session_state.get("whatif_last")
    if not last or last not in cache:
        st.info("Set the parameters and press **Run simulation** (a 30-round run takes a few seconds). "
                "With the default settings it reproduces the reported run exactly.")
        return
    run: Run = cache[last]
    p = json.loads(last)
    prof = run.cfg["weights"]["profiles"]
    st.markdown(f"**What-if run**: seed {p['seed']}, {p['rounds']} rounds per condition. "
                "It is also available on the Walkthrough tab.  \n"
                "Weights used (network / cost / risk): normal "
                + " / ".join(f"{prof['normal'][k]:.2f}" for k in ("network", "cost", "risk"))
                + ", severe " + " / ".join(f"{prof['severe'][k]:.2f}" for k in ("network", "cost", "risk")))
    if p["rounds"] != reported.cfg["experiment"]["rounds_per_condition"] or p["seed"] != reported.schedule["seed"]:
        st.caption("Seed or rounds differ from the reported run, so part of any difference is just chance.")
    st.subheader("Mean end-to-end time: reported vs what-if")
    st.plotly_chart(charts.whatif_delta(mean_time_table(reported), mean_time_table(run)),
                    width="stretch", key="whatif-delta")
    results_tab.render_body(run, "whatif")
