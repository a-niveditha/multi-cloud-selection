"""Run one block of the schedule.

    preflight(...)          refuses to start if anything is unsafe (see the checks below)
    BlockRunner(...).run()  clears netem, applies the block's condition, runs every
                            round not yet completed, clears netem and backs up the
                            database at the end - also when it crashes (try/finally).

Resume: rounds with a complete run in the database are skipped. A round that
crashed half-way stays in the database as an incomplete run (never overwritten
or deleted) and is repeated from scratch as run 2. The risk history is rebuilt
from complete runs only.
"""
from __future__ import annotations

from rmc.config import validate_config
from rmc.models import LocalFile, RunContext, Strategy
from rmc.probing.probes import probe_all
from rmc.runner.schedule import get_block
from rmc.selection.risk import HistoryStore
from rmc.selection.strategies import SelectionInput, build_strategies
from rmc.transfer import transfer_with_failover


class RunnerRefused(Exception):
    """A safety check failed; nothing was run."""


def preflight(cfg: dict, schedule: dict, block_id: str, db, ctx: RunContext, setup: dict, *,
              git_dirty: bool | None, allow_dirty: bool, netem_dry_run: bool) -> None:
    """git_dirty: True/False, or None if this is not a git repository."""
    block = get_block(schedule, block_id)
    problems = validate_config(cfg)
    if problems:
        raise RunnerRefused("config problems: " + "; ".join(problems))
    if git_dirty is None and not allow_dirty:
        raise RunnerRefused("not a git repository, so the code version cannot be recorded (use --allow-dirty for development)")
    if git_dirty and not allow_dirty:
        raise RunnerRefused("the git tree has uncommitted changes; commit them (or use --allow-dirty for development)")
    if schedule["config_hash"] != ctx.config_hash and not allow_dirty:
        raise RunnerRefused("config.yaml changed after the schedule was generated "
                            f"({ctx.config_hash} != {schedule['config_hash']})")
    others = db.block_machines(block_id) - {ctx.machine_id}
    if others:
        raise RunnerRefused(f"block {block_id} was started on another machine {sorted(others)}; "
                            "a block (and its paired rounds) must never be split across machines")
    missing = set(cfg["providers"]) - set(setup["baseline"])
    if missing:
        raise RunnerRefused(f"calibration baseline is missing providers {sorted(missing)}")
    if block["condition"] != "natural" and netem_dry_run and not ctx.is_pilot:
        raise RunnerRefused("netem.dry_run is true: a degraded block would run WITHOUT degradation")
    try:  # one database = one schedule
        db.set_meta("schedule_seed", str(schedule["seed"]))
        db.set_meta("schedule_created_at", schedule["created_at"])
    except ValueError as e:
        raise RunnerRefused(f"this database belongs to a different schedule: {e}") from e


class BlockRunner:
    def __init__(self, cfg: dict, schedule: dict, block_id: str, db, ctx: RunContext, providers: dict,
                 prober, netem, setup: dict, payload: LocalFile, clock, log=print):
        self.cfg, self.db, self.ctx = cfg, db, ctx
        self.block = get_block(schedule, block_id)
        self.block_id = block_id
        self.providers, self.prober, self.netem = providers, prober, netem
        self.baseline = setup["baseline"]
        self.payload, self.clock, self.log = payload, clock, log
        self.names = list(cfg["providers"])
        self.strategies = build_strategies(cfg, setup["nearest_order"])
        self.history = HistoryStore(cfg["risk"]["window_k"])

    # ---- block --------------------------------------------------------------
    def run(self) -> dict:
        b = self.block
        natural = b["condition"] == "natural"
        if natural and self.netem.has_rules():
            raise RunnerRefused("netem rules are present before a NATURAL block; "
                                "clear them (scripts/netem_ctl.py clear), check, and rerun")
        summary = {"block_id": self.block_id, "rounds_run": 0, "rounds_skipped": 0, "transfers": 0}
        finished = False
        try:
            self.netem.clear()
            if self.netem.has_rules():
                raise RunnerRefused("netem rules are still present after clearing")
            if not natural:
                c = self.cfg["netem"]["conditions"][b["condition"]]
                self.netem.apply(b["degraded_provider"], c["rtt_increase_ratio"], c["loss_pct"])
                if not getattr(self.netem, "dry_run", False) and not self.netem.has_rules():
                    raise RunnerRefused("netem apply did not leave a netem qdisc on the interface")
                self.log(f"{self.block_id}: {b['condition']} applied to {b['degraded_provider']}")

            self._rebuild_history()
            done = self.db.completed_rounds(self.block_id)
            for rnd in b["rounds"]:
                if rnd["round_id"] in done:
                    self.log(f"{self.block_id} R{rnd['round_id']}: already complete, skipping")
                    summary["rounds_skipped"] += 1
                    continue
                summary["transfers"] += self._run_round(rnd)
                summary["rounds_run"] += 1
            finished = True
        finally:
            self.netem.clear()
            label = self.block_id if finished else f"{self.block_id}-incomplete"
            try:
                backup = self.db.backup(self.cfg["storage"]["backup_dir"], label)
                self.log(f"{self.block_id}: netem cleared, database backed up to {backup}")
            except Exception as e:   # never hide the original error behind a backup error
                self.log(f"{self.block_id}: BACKUP FAILED: {e}")
        return summary

    # ---- round --------------------------------------------------------------
    def _run_round(self, rnd: dict) -> int:
        b, rid = self.block, rnd["round_id"]
        rr = self.db.start_round_run(self.ctx, rid, rnd["is_warmup"], b["condition"], b["degraded_provider"],
                                     rnd["strategy_order"])
        run_no = self.db.conn.execute("SELECT run_no FROM round_runs WHERE id = ?", (rr,)).fetchone()[0]
        tcfg = self.cfg["transfer"]
        for pos, sname in enumerate(rnd["strategy_order"]):
            strategy = self.strategies[Strategy(sname)]
            probes = probe_all(self.prober, self.names) if strategy.needs_probes else None
            probe_time = sum(p.probe_time_s for p in probes.values()) if probes else 0.0
            decision = strategy.select(SelectionInput(self.names, probes, self.history, self.baseline,
                                                      round_key=f"{self.block_id}:R{rid}"))
            key_base = f"{tcfg['object_prefix']}/{self.block_id}/R{rid:02d}/run{run_no}/{sname}"
            result = transfer_with_failover(decision, self.providers, self.payload, key_base, tcfg,
                                            probe_time_s=probe_time, clock=self.clock)
            self.db.insert_transfer(self.ctx, rr, pos, decision, result, list(probes.values()) if probes else [])
            self._update_history(sname, result, probes)
            self.log(f"{self.block_id} R{rid}{' (warm-up)' if rnd['is_warmup'] else ''} {sname:14s} -> "
                     f"{result.provider:7s} {'ok  ' if result.success else 'FAIL'} "
                     f"e2e={result.end_to_end_time_s:7.2f}s attempts={len(result.attempts)}")
            self.clock.sleep(self.cfg["experiment"]["cooldown_s"])
        self.db.complete_round_run(rr)
        return len(rnd["strategy_order"])

    # ---- risk history -------------------------------------------------------
    def _history_filters(self) -> tuple[str | None, str | None]:
        r = self.cfg["risk"]
        block = self.block_id if r["history_scope"] == "block" else None
        strategy = Strategy.PROPOSED.value if r["history_source"] == "own" else None
        return block, strategy

    def _rebuild_history(self) -> None:
        block, strategy = self._history_filters()
        self.history.reset()
        for a in self.db.history_attempts(block, strategy):
            self.history.add_attempt(a)
        for p in self.db.history_probes(block, strategy):
            self.history.add_probe(p)

    def _update_history(self, sname: str, result, probes) -> None:
        _, only = self._history_filters()
        if only is not None and sname != only:
            return
        for a in result.attempts:
            self.history.add_attempt(a)
        for p in (probes or {}).values():
            self.history.add_probe(p)
