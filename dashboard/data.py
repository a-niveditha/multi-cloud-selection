"""Data for the dashboard. No Streamlit here, so everything can be unit-tested.

A Run is one simulation folder (results/<name>/ or a What-if temp folder):
simulation.sqlite + schedule.json + setup.json + config.json.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from rmc.analysis import stats
from rmc.analysis.load import load_transfers, open_readonly
from rmc.analysis.plots import COLORS, LABELS, STRATEGIES  # noqa: F401  (re-exported for the tabs)
from rmc.models import ProbeResult
from rmc.selection.normalise import cost_per_transfer_usd, cost_scores, network_scores

CONDITIONS = stats.CONDITIONS
CONDITION_LABELS = {"natural": "Natural", "mild": "Mild", "severe": "Severe"}
NETWORK_AWARE = {"proposed", "static_network"}


@dataclass
class Run:
    name: str
    df: pd.DataFrame          # one row per transfer (complete rounds, warm-up included)
    attempts: pd.DataFrame    # one row per upload attempt
    probes: pd.DataFrame      # one row per provider probed (fresh probes only)
    setup: dict               # natural baseline + nearest order
    schedule: dict
    cfg: dict                 # effective configuration of this run

    @property
    def providers(self) -> list[str]:
        return list(self.cfg["providers"])

    @property
    def measured(self) -> pd.DataFrame:
        return stats.measured(self.df)


def load_run(run_dir: str | Path, name: str | None = None, fallback_cfg: dict | None = None) -> Run:
    """Load a simulation folder. config.json is used when present (runs made by
    rmc.simulation); otherwise `fallback_cfg`."""
    run_dir = Path(run_dir)
    db = run_dir / "simulation.sqlite"
    df = load_transfers(db, include_pilot=True)
    with open_readonly(db) as conn:
        attempts = pd.read_sql_query("SELECT * FROM attempts ORDER BY id", conn)
        probes = pd.read_sql_query("SELECT * FROM probes ORDER BY id", conn)
    cfg_path = run_dir / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8")) if cfg_path.exists() else fallback_cfg
    if cfg is None:
        raise FileNotFoundError(f"{cfg_path} not found and no fallback config given")
    return Run(name=name or run_dir.name, df=df, attempts=attempts, probes=probes,
               setup=json.loads((run_dir / "setup.json").read_text(encoding="utf-8")),
               schedule=json.loads((run_dir / "schedule.json").read_text(encoding="utf-8")), cfg=cfg)


# ---- summary tables -------------------------------------------------------------------
def summary_tables(run: Run) -> dict[str, pd.DataFrame]:
    acfg = run.cfg["analysis"]
    comp = stats.compare_all(run.df, acfg)
    m = run.measured
    choice = (m.groupby(["condition", "strategy", "first_choice"]).size()
              .rename("rounds").reset_index())
    return {
        "comparisons": comp[comp["family"] == "primary"].reset_index(drop=True),
        "variability": stats.variability(run.df, acfg["metric"]),
        "reliability": stats.reliability(run.df),
        "degraded_choice_pairs": stats.degraded_choice_pairs(run.df),
        "probe_overhead": stats.probe_overhead(run.df),
        "provider_choice": choice,
    }


def mean_time_table(run: Run) -> pd.DataFrame:
    """condition x strategy mean end-to-end time (measured rounds)."""
    m = run.measured
    t = m.pivot_table(index="condition", columns="strategy", values="end_to_end_time_s", aggfunc="mean")
    return t.reindex(index=CONDITIONS, columns=STRATEGIES)


def cost_per_gb(run: Run) -> dict[str, float]:
    """Storage + egress USD per GB for each provider, as the cost model sees it."""
    return {p: cost_per_transfer_usd(p, run.cfg["cost"], 1024 ** 3) for p in run.providers}


# ---- walkthrough ----------------------------------------------------------------------
def block_label(block: dict) -> str:
    deg = f", {block['degraded_provider']} degraded" if block["degraded_provider"] else ""
    return f"{block['block_id']} - {CONDITION_LABELS[block['condition']]}{deg}"


def _probe_results(rows: pd.DataFrame) -> dict[str, ProbeResult]:
    return {r.provider: ProbeResult(r.provider, r.rtt_ms, r.throughput_mbps, r.loss_pct, r.probe_time_s,
                                    ok=bool(r.ok), error=r.error)
            for r in rows.itertuples()}


def probes_used(run: Run, transfer: pd.Series) -> tuple[pd.DataFrame, bool, int | None]:
    """(probe rows, fresh, source transfer id). Reused probes are not stored with the
    transfer that reused them, so look back to this strategy's last fresh probe set
    in the same block."""
    rows = run.probes[run.probes["transfer_id"] == transfer["id"]]
    if len(rows):
        return rows, True, int(transfer["id"])
    if transfer["strategy"] not in NETWORK_AWARE:
        return rows, False, None
    df = run.df
    earlier = df[(df["block_id"] == transfer["block_id"]) & (df["strategy"] == transfer["strategy"])
                 & (df["id"] < transfer["id"])]
    with_probes = earlier[earlier["id"].isin(run.probes["transfer_id"])]
    if with_probes.empty:
        return rows, False, None
    src = int(with_probes["id"].max())
    return run.probes[run.probes["transfer_id"] == src], False, src


def trigger_reasons(probes: dict[str, ProbeResult], baseline: dict, indicator: dict) -> list[str]:
    """Human-readable reasons why the degradation indicator fired (empty = it did not).
    Mirrors rmc.selection.weights.is_degraded."""
    reasons = []
    for name, p in probes.items():
        if not p.ok:
            reasons.append(f"{name}: probe failed")
            continue
        if p.loss_pct is not None and p.loss_pct >= indicator["loss_pct"]:
            reasons.append(f"{name}: measured loss {p.loss_pct:.0f} % >= {indicator['loss_pct']:g} %")
        base = baseline.get(name)
        limit = base["rtt_ms"] * (1 + indicator["rtt_increase_ratio"]) if base else None
        if limit is not None and p.rtt_ms is not None and p.rtt_ms >= limit:
            reasons.append(f"{name}: RTT {p.rtt_ms:.0f} ms >= {limit:.0f} ms "
                           f"({1 + indicator['rtt_increase_ratio']:g} x baseline {base['rtt_ms']:.0f} ms)")
    return reasons


def decision_detail(run: Run, transfer_id: int) -> dict:
    """Everything needed to explain one decision, recomputed from the stored probes
    with the same functions the strategy used."""
    t = run.df[run.df["id"] == transfer_id].iloc[0]
    providers = run.providers
    rows, fresh, source_id = probes_used(run, t)
    probes = _probe_results(rows)
    baseline = run.setup["baseline"]
    weights = json.loads(t["weights_json"])
    scores = json.loads(t["scores_json"])
    risk = json.loads(t["risk_json"])
    ranking = json.loads(t["ranking_json"])

    probe_table = pd.DataFrame([
        {"provider": p, "rtt_ms": probes[p].rtt_ms, "baseline_rtt_ms": baseline[p]["rtt_ms"],
         "throughput_mbps": probes[p].throughput_mbps, "loss_pct": probes[p].loss_pct,
         "degraded_path": p == t["degraded_provider"]}
        for p in providers if p in probes])

    score_table = None
    if t["strategy"] in NETWORK_AWARE and len(probes) == len(providers):
        n = network_scores(probes, run.cfg["network_score"])
        c = cost_scores(providers, run.cfg["cost"], run.cfg["experiment"]["payload_size_bytes"])
        rows_ = []
        for p in providers:
            row = {"provider": p, "N": n[p], "C": c[p], "rho": risk.get(p, 0.0),
                   "network_part": weights.get("network", 0) * n[p],
                   "cost_part": weights.get("cost", 0) * c[p],
                   "risk_part": -weights.get("risk", 0) * risk.get(p, 0.0),
                   "score": scores[p], "rank": ranking.index(p) + 1}
            rows_.append(row)
        score_table = pd.DataFrame(rows_).sort_values("rank").reset_index(drop=True)

    attempts = run.attempts[run.attempts["transfer_id"] == transfer_id].sort_values("attempt_no")
    indicator = run.cfg["weights"]["degradation_indicator"]
    return {
        "transfer": t,
        "probes": probe_table,
        "probes_fresh": fresh,
        "probe_source_id": source_id,
        "probe_source_round": (int(run.df.loc[run.df["id"] == source_id, "round_id"].iloc[0])
                               if source_id is not None else None),
        "trigger_reasons": trigger_reasons(probes, baseline, indicator) if probes else [],
        "weights": weights,
        "profile": t["weight_profile"],
        "scores": score_table,
        "ranking": ranking,
        "attempts": attempts,
    }


def round_overview(run: Run, block_id: str, round_id: int) -> pd.DataFrame:
    """What every strategy did in one round."""
    df = run.df
    r = df[(df["block_id"] == block_id) & (df["round_id"] == round_id)].copy()
    r["order"] = r["strategy_position"] + 1
    return r.sort_values("order")[["order", "strategy", "first_choice", "provider_final", "probe_time_s",
                                   "end_to_end_time_s", "n_failed_attempts", "timed_out", "weight_profile"]]


def risk_history(run: Run, block_id: str) -> pd.DataFrame:
    """rho per provider as the proposed strategy saw it, round by round, in one block."""
    p = run.df[(run.df["block_id"] == block_id) & (run.df["strategy"] == "proposed")].sort_values("round_id")
    rows = []
    for r in p.itertuples():
        for prov, rho in json.loads(r.risk_json).items():
            rows.append({"round_id": r.round_id, "provider": prov, "rho": rho, "profile": r.weight_profile,
                         "is_warmup": r.is_warmup})
    return pd.DataFrame(rows)


def probe_variant_table(runs: dict[str, Run]) -> pd.DataFrame:
    """One row per probing variant: the proposed method's probe time, end-to-end time,
    rate of choosing the degraded provider (mean of mild and severe) and failovers."""
    rows = []
    for label, run in runs.items():
        m = run.measured
        p = m[m["strategy"] == "proposed"]
        rel = stats.reliability(run.df)
        rp = rel[rel["strategy"] == "proposed"]
        rows.append({"variant": label, "probe_s": p["probe_time_s"].mean(), "e2e_s": p["end_to_end_time_s"].mean(),
                     "chose_degraded": rp["chose_degraded_rate"].mean(),
                     "failovers": int(rp["transfers_with_failover"].sum())})
    return pd.DataFrame(rows)
