import math
import sqlite3

import numpy as np
import pandas as pd
import pytest

from rmc.analysis import plots, stats
from rmc.analysis.load import load_transfers, open_readonly
from tests.test_runner import Env

ACFG = {"metric": "end_to_end_time_s", "primary_test": "wilcoxon", "multiple_comparison": "holm",
        "alpha": 0.05, "failed_transfer_policy": "include"}


def synthetic(n_rounds=9, proposed_gain=1.0, seed=0):
    """Made-up transfers: every baseline is `proposed_gain` s slower than proposed, plus noise.
    In degraded conditions the static baseline picks the degraded provider in odd rounds."""
    rng = np.random.default_rng(seed)
    rows, tid = [], 0
    for cond, degraded in (("natural", None), ("mild", "gcs"), ("severe", "gcs")):
        for r in range(1, n_rounds + 1):
            base = 5 + rng.normal(0, 0.5)
            for s in [stats.PROPOSED] + stats.BASELINES:
                tid += 1
                chose = None if degraded is None else (s == "static_network" and r % 2 == 1)
                t = base + (0 if s == stats.PROPOSED else proposed_gain) + rng.normal(0, 0.05)
                rows.append({"id": tid, "block_id": f"B-{cond}", "round_id": r, "is_warmup": r <= 2,
                             "condition": cond, "degraded_provider": degraded, "strategy": s,
                             "first_choice": "gcs" if chose else "aws_s3", "end_to_end_time_s": t,
                             "probe_time_s": 0.5 if s in ("proposed", "static_network") else 0.0,
                             "success": True, "timed_out": False, "verified": True, "n_failed_attempts": 0,
                             "retry_time_s": None, "chose_degraded": chose})
    return pd.DataFrame(rows)


def test_paired_uses_measured_rounds_and_sign_convention():
    df = synthetic()
    p = stats.paired(df, "random", "end_to_end_time_s")
    assert len(p) == 3 * 7                       # 9 rounds - 2 warm-up, 3 conditions
    assert (p["diff"] > 0).all()                 # proposed faster -> positive
    assert p["diff"].mean() == pytest.approx(1.0, abs=0.1)


def test_exclude_pair_policy_drops_failed_rounds():
    df = synthetic()
    idx = df[(df.strategy == "cost_only") & (df.round_id == 5) & (df.condition == "mild")].index
    df.loc[idx, "success"] = False
    assert len(stats.paired(df, "cost_only", "end_to_end_time_s", "exclude_pair")) == 20
    assert len(stats.paired(df, "cost_only", "end_to_end_time_s", "include")) == 21
    with pytest.raises(ValueError):
        stats.paired(df, "cost_only", "end_to_end_time_s", "drop_everything")


def test_paired_tests_detect_a_consistent_difference():
    r = stats.paired_tests(pd.Series([1.0, 1.1, 0.9, 1.2, 1.0, 0.95, 1.05]))
    assert r["n"] == 7 and r["ttest_p"] < 0.001 and r["wilcoxon_p"] < 0.05


def test_paired_tests_no_difference_and_edge_cases():
    r = stats.paired_tests(pd.Series([0.3, -0.2, 0.1, -0.3, 0.2, -0.1, 0.05]))
    assert r["ttest_p"] > 0.5 and r["wilcoxon_p"] > 0.5
    zeros = stats.paired_tests(pd.Series([0.0, 0.0, 0.0]))
    assert math.isnan(zeros["ttest_p"]) and math.isnan(zeros["wilcoxon_p"])
    assert stats.paired_tests(pd.Series([], dtype=float))["n"] == 0


def test_holm():
    adj = stats.holm([0.01, 0.04, 0.03, math.nan])
    assert adj[:3] == pytest.approx([0.03, 0.06, 0.06]) and math.isnan(adj[3])


def test_compare_all_structure_and_correction():
    out = stats.compare_all(synthetic(), ACFG)
    prim = out[out.family == "primary"]
    assert len(prim) == 4 * 3
    assert (prim["median_diff"] > 0).all()
    assert (prim["primary_p_adj"] >= prim["primary_p"]).all()
    expl = out[out.family == "exploratory"]
    assert len(expl) == 4 * 2 * 2 and not expl["significant"].any()
    s = expl[(expl.baseline == "static_network") & (expl.condition == "mild")]
    assert sorted(s["n"]) == [3, 4]              # measured rounds 3..9: odd = chose degraded (3,5,7,9)


def test_reliability_and_degraded_choice_pairs():
    df = synthetic()
    rel = stats.reliability(df)
    static_mild = rel[(rel.condition == "mild") & (rel.strategy == "static_network")].iloc[0]
    assert static_mild["chose_degraded_rate"] == pytest.approx(4 / 7)
    assert static_mild["success_rate"] == 1.0
    pairs = stats.degraded_choice_pairs(df)
    row = pairs[(pairs.baseline == "static_network") & (pairs.condition == "severe")].iloc[0]
    assert (row["only_baseline"], row["only_proposed"], row["both"]) == (4, 0, 0)


def test_variability_probe_overhead_cold_start():
    df = synthetic()
    v = stats.variability(df, "end_to_end_time_s")
    assert set(v.columns) >= {"std", "iqr", "cv", "p90"} and len(v) == 15
    po = stats.probe_overhead(df).set_index("strategy")
    assert po.loc["cost_only", "mean_probe_time_s"] == 0 and po.loc["proposed", "probe_share"] > 0
    cs = stats.cold_start(df, "end_to_end_time_s")
    assert cs[cs.is_warmup]["round_id"].max() == 2


def test_plots_are_written(tmp_path):
    paths = plots.make_all(synthetic(), ACFG, tmp_path)
    assert len(paths) == 5 and all(p.exists() and p.stat().st_size > 1000 for p in paths)


def test_end_to_end_on_mock_runner_data(cfg, tmp_path, payload):
    env = Env(cfg, tmp_path, payload)
    for cond in ("natural", "mild", "severe"):
        env.runner(env.block_id(cond)).run()
    env.db.close()

    db = tmp_path / "trials.sqlite"
    df = load_transfers(db)
    assert len(df) == 3 * 5 * 5 and set(df["condition"]) == {"natural", "mild", "severe"}
    assert df[df.condition == "natural"]["chose_degraded"].isna().all()
    assert len(load_transfers(db, include_pilot=False)) == len(df)
    out = stats.compare_all(df, ACFG)
    assert (out[out.family == "primary"]["n"] == 3).all()
    assert len(plots.make_all(df, ACFG, tmp_path / "fig")) == 5

    with pytest.raises(sqlite3.OperationalError):           # analysis connection is read-only
        open_readonly(db).execute("DELETE FROM transfers")


def test_load_many_combines_machines_and_refuses_duplicate_blocks(cfg, tmp_path, payload):
    import shutil
    from rmc.analysis.load import load_many
    env = Env(cfg, tmp_path, payload)
    env.runner(env.block_id("natural")).run()
    env.db.close()
    db = tmp_path / "trials.sqlite"
    assert len(load_many([db])) == 25 and load_many([db])["id"].is_unique
    copy = tmp_path / "friend.sqlite"
    shutil.copy(db, copy)
    with pytest.raises(ValueError, match="more than one database"):
        load_many([db, copy])
