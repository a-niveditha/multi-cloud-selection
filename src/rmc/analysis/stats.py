"""Paired comparison of the proposed strategy against each baseline.

Pairing: within a round, every strategy sent the same file under the same network
condition, so (block_id, round_id) identifies a pair. Only measured rounds are
used (warm-up rounds are analysed separately as cold start).

Sign convention: diff = baseline - proposed. POSITIVE means the proposed strategy
was FASTER in that round. Tests are two-sided: we do not assume a direction.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
from scipy import stats

PROPOSED = "proposed"
BASELINES = ["static_network", "cost_only", "nearest", "random"]
CONDITIONS = ["natural", "mild", "severe"]
DEGRADED = ["mild", "severe"]


def measured(df: pd.DataFrame) -> pd.DataFrame:
    return df[~df["is_warmup"]]


def paired(df: pd.DataFrame, baseline: str, metric: str, policy: str = "include") -> pd.DataFrame:
    """One row per round: proposed vs baseline values and diff (= baseline - proposed)."""
    keys = ["block_id", "round_id"]
    m = measured(df)
    p = m[m["strategy"] == PROPOSED][keys + ["condition", "degraded_provider", metric, "success", "chose_degraded"]]
    b = m[m["strategy"] == baseline][keys + [metric, "success", "chose_degraded"]]
    out = p.merge(b, on=keys, suffixes=("_proposed", "_baseline"))
    out = out.rename(columns={f"{metric}_proposed": "proposed", f"{metric}_baseline": "baseline"})
    if policy == "exclude_pair":
        out = out[out["success_proposed"] & out["success_baseline"]]
    elif policy != "include":
        raise ValueError(f"unknown failed_transfer_policy {policy!r}")
    out["diff"] = out["baseline"] - out["proposed"]
    out["baseline_name"] = baseline
    return out.reset_index(drop=True)


def paired_tests(diff: pd.Series) -> dict:
    """Paired t-test and Wilcoxon signed-rank test on the differences (two-sided)."""
    d = diff.dropna().to_numpy(dtype=float)
    res = {"n": len(d), "mean_diff": np.mean(d) if len(d) else math.nan,
           "median_diff": np.median(d) if len(d) else math.nan,
           "shapiro_p": math.nan, "ttest_p": math.nan, "wilcoxon_p": math.nan}
    if len(d) >= 3:
        res["shapiro_p"] = stats.shapiro(d).pvalue if np.ptp(d) > 0 else math.nan
    if len(d) >= 2 and np.ptp(d) > 0:
        res["ttest_p"] = stats.ttest_1samp(d, 0.0).pvalue      # identical to the paired t-test
    if len(d) >= 1 and np.any(d != 0):
        res["wilcoxon_p"] = stats.wilcoxon(d).pvalue
    return res


def holm(pvalues: list[float]) -> list[float]:
    """Holm-Bonferroni adjusted p-values. NaN entries are ignored and stay NaN."""
    idx = [i for i, p in enumerate(pvalues) if not math.isnan(p)]
    order = sorted(idx, key=lambda i: pvalues[i])
    m = len(order)
    adjusted = [math.nan] * len(pvalues)
    running = 0.0
    for rank, i in enumerate(order):
        running = max(running, min(1.0, (m - rank) * pvalues[i]))
        adjusted[i] = running
    return adjusted


def compare_all(df: pd.DataFrame, acfg: dict) -> pd.DataFrame:
    """Proposed vs every baseline, per condition.

    family = "primary":     one row per (baseline, condition); the primary p-value is
                            corrected over this family (Holm if configured).
    family = "exploratory": degraded conditions split by whether the BASELINE's first
                            choice was the degraded provider; not corrected.
    """
    metric, policy = acfg["metric"], acfg["failed_transfer_policy"]
    rows = []
    for baseline in BASELINES:
        pairs = paired(df, baseline, metric, policy)
        for cond in CONDITIONS:
            sub = pairs[pairs["condition"] == cond]
            rows.append({"family": "primary", "baseline": baseline, "condition": cond, "subgroup": "all",
                         "proposed_mean": sub["proposed"].mean(), "baseline_mean": sub["baseline"].mean(),
                         **paired_tests(sub["diff"])})
            if cond in DEGRADED:
                for chose, label in ((True, "baseline chose degraded"), (False, "baseline avoided degraded")):
                    s = sub[sub["chose_degraded_baseline"] == chose]
                    rows.append({"family": "exploratory", "baseline": baseline, "condition": cond,
                                 "subgroup": label, "proposed_mean": s["proposed"].mean(),
                                 "baseline_mean": s["baseline"].mean(), **paired_tests(s["diff"])})
    out = pd.DataFrame(rows)
    out["primary_p"] = out[f"{acfg['primary_test']}_p"]
    out["primary_p_adj"] = out["primary_p"]
    prim = out["family"] == "primary"
    if acfg["multiple_comparison"] == "holm":
        out.loc[prim, "primary_p_adj"] = holm(out.loc[prim, "primary_p"].tolist())
    out["significant"] = prim & (out["primary_p_adj"] < acfg["alpha"])
    return out


def variability(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Stability (H1): spread of the metric per condition and strategy, measured rounds."""
    def iqr(x):
        return x.quantile(0.75) - x.quantile(0.25)
    g = measured(df).groupby(["condition", "strategy"])[metric]
    out = g.agg(n="count", mean="mean", median="median", std="std", p90=lambda x: x.quantile(0.9))
    out["iqr"] = g.apply(iqr)
    out["cv"] = out["std"] / out["mean"]
    return out.reset_index()


def reliability(df: pd.DataFrame) -> pd.DataFrame:
    """Failure exposure (H2) per condition and strategy, measured rounds."""
    m = measured(df).copy()
    m["failed_over"] = m["n_failed_attempts"] > 0
    g = m.groupby(["condition", "strategy"])
    out = g.agg(transfers=("id", "count"), success_rate=("success", "mean"),
                transfers_with_timeout=("timed_out", "sum"), failed_attempts=("n_failed_attempts", "sum"),
                transfers_with_failover=("failed_over", "sum"), mean_retry_time_s=("retry_time_s", "mean"),
                unverified=("verified", lambda x: int((~x).sum())))
    deg = m[m["condition"].isin(DEGRADED)].groupby(["condition", "strategy"])["chose_degraded"]
    out["chose_degraded_rate"] = deg.apply(lambda x: x.astype(float).mean())
    return out.reset_index()


def degraded_choice_pairs(df: pd.DataFrame) -> pd.DataFrame:
    """Per baseline and degraded condition: in how many rounds did only the proposed /
    only the baseline pick the degraded provider first. Exact binomial (McNemar-style)
    test on the discordant rounds; two-sided."""
    rows = []
    for baseline in BASELINES:
        pairs = paired(df, baseline, "end_to_end_time_s")
        for cond in DEGRADED:
            s = pairs[pairs["condition"] == cond]
            p_, b_ = s["chose_degraded_proposed"].astype(bool), s["chose_degraded_baseline"].astype(bool)
            only_p, only_b = int((p_ & ~b_).sum()), int((~p_ & b_).sum())
            pval = stats.binomtest(only_p, only_p + only_b, 0.5).pvalue if only_p + only_b else math.nan
            rows.append({"baseline": baseline, "condition": cond, "rounds": len(s),
                         "both": int((p_ & b_).sum()), "only_proposed": only_p, "only_baseline": only_b,
                         "neither": int((~p_ & ~b_).sum()), "binomial_p": pval})
    return pd.DataFrame(rows)


def probe_overhead(df: pd.DataFrame) -> pd.DataFrame:
    m = measured(df)
    out = m.groupby("strategy").agg(mean_probe_time_s=("probe_time_s", "mean"),
                                     mean_end_to_end_s=("end_to_end_time_s", "mean"))
    out["probe_share"] = out["mean_probe_time_s"] / out["mean_end_to_end_s"]
    return out.reset_index()


def cold_start(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Mean metric and failures by round position in the block (warm-up rounds included),
    to show how the proposed strategy behaves before its risk history exists."""
    g = df.groupby(["round_id", "is_warmup", "strategy"])
    out = g.agg(n=(metric, "count"), mean=(metric, "mean"), failed_attempts=("n_failed_attempts", "sum"))
    return out.reset_index()
