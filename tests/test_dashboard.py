"""Dashboard: data layer correctness, and the app itself running headless (AppTest)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dashboard.data import decision_detail, load_run, mean_time_table, probes_used  # noqa: E402
from dashboard.whatif_tab import build_config, defaults, run_whatif  # noqa: E402

REPORTED = ROOT / "results" / "final_30rounds"
pytestmark = pytest.mark.skipif(not (REPORTED / "config.json").exists(), reason="results not generated")


@pytest.fixture(scope="module")
def run():
    return load_run(REPORTED)


def test_walkthrough_recomputes_every_stored_score(run):
    """The walkthrough rebuilds N, C and the weighted terms from the stored probes;
    they must add up to exactly the score the strategy stored at decision time."""
    aware = run.df[run.df["strategy"].isin(["proposed", "static_network"])]
    for t in aware.itertuples():
        s = decision_detail(run, t.id)["scores"]
        expected = s["N"] if t.strategy == "static_network" else s["network_part"] + s["cost_part"] + s["risk_part"]
        assert (expected - s["score"]).abs().max() < 1e-9, t.id


def test_reused_probes_are_traced_back_to_their_source(run):
    proposed = run.df[run.df["strategy"] == "proposed"]
    reused = [t for t in proposed.itertuples() if t.id not in set(run.probes["transfer_id"])]
    assert reused, "the reported run reuses probes (max_age_s = 60)"
    rows, fresh, src = probes_used(run, run.df[run.df["id"] == reused[0].id].iloc[0])
    assert not fresh and src < reused[0].id and len(rows) == 3
    assert run.df.loc[run.df["id"] == src, "strategy"].item() == "proposed"


def test_build_config_rescales_weights_and_rejects_all_zero(cfg):
    p = defaults(cfg)
    p["normal"] = {"network": 2, "cost": 1, "risk": 1}
    out = build_config(cfg, p)
    assert out["weights"]["profiles"]["normal"] == {"network": 0.5, "cost": 0.25, "risk": 0.25}
    p["severe"] = {"network": 0, "cost": 0, "risk": 0}
    with pytest.raises(ValueError):
        build_config(cfg, p)


def test_whatif_with_reported_settings_reproduces_reported_run(cfg, run):
    p = defaults(cfg)
    p["rounds"] = 30
    new = run_whatif(cfg, p)
    assert (mean_time_table(new) - mean_time_table(run)).abs().max().max() < 1e-9


def test_app_runs_and_navigates():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=120).run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["Results", "Walkthrough", "What-if"]
    block = at.session_state["wt_block"]
    next(b for b in at.button if b.label == "Next").click().run()
    assert not at.exception
    assert (at.session_state["wt_block"], at.session_state["wt_round"]) != (block, 1)
    at.selectbox(key="wt_strategy").set_value("cost_only").run()
    assert not at.exception


def test_app_whatif_run():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(str(ROOT / "streamlit_app.py"), default_timeout=120).run()
    next(b for b in at.button if b.label == "Run simulation").click().run()
    assert not at.exception
    assert at.session_state["whatif_last"]
    at.radio(key="wt_source").set_value("Latest what-if run").run()
    assert not at.exception
