"""Dashboard for the risk-aware multi-cloud selection simulation.

    streamlit run streamlit_app.py

Tabs: Results (the committed 30-round run), Walkthrough (replay single decisions),
What-if (rerun the simulation with other settings). Deployable as-is on Streamlit
Community Cloud (main file: streamlit_app.py).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT)]

import streamlit as st  # noqa: E402

from dashboard import results_tab, walkthrough_tab, whatif_tab  # noqa: E402
from dashboard.data import load_run, probe_variant_table  # noqa: E402
from rmc.config import load_config  # noqa: E402

RESULTS = ROOT / "results"
REPORTED = RESULTS / "final_30rounds"
VARIANTS = {"A. original": "probe_variants/A_original", "B. light probes": "probe_variants/B_light",
            "C. reuse only": "probe_variants/C_reuse"}

st.set_page_config(page_title="Multi-cloud selection", page_icon=":cloud:", layout="wide")


@st.cache_resource
def reported_run():
    return load_run(REPORTED, name="final_30rounds")


@st.cache_resource
def variants():
    runs = {label: load_run(RESULTS / path) for label, path in VARIANTS.items() if (RESULTS / path).exists()}
    if not runs:
        return None
    runs["D. light + reuse (adopted)"] = reported_run()
    return probe_variant_table(runs)


st.title("Risk-aware adaptive multi-cloud selection")
st.caption("Choosing a cloud storage provider for each upload from the live network condition, cost and "
           "recent reliability. **All data on this page is simulated**: providers, network conditions and "
           "failures come from assumed profiles (config.yaml, `simulation:`), not real cloud measurements.")

run = reported_run()
results, walk, whatif = st.tabs(["Results", "Walkthrough", "What-if"])
with results:
    results_tab.render(run, variants())
with walk:
    source = run
    last = st.session_state.get("whatif_last")
    cache = st.session_state.get("whatif_cache", {})
    if last in cache:
        pick = st.radio("Data", ["Reported run", "Latest what-if run"], horizontal=True, key="wt_source")
        if pick == "Latest what-if run":
            source = cache[last]
    walkthrough_tab.render(source)
with whatif:
    whatif_tab.render(load_config(), run)
