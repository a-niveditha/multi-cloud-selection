"""Check a block after it finishes (read-only). Prints OK / WARN / FAIL lines.

    python scripts/check_block.py --block B01
    python scripts/check_block.py --block B01 --db data/mock.sqlite --schedule data/mock_schedule.json
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rmc.analysis.load import open_readonly  # noqa: E402
from rmc.config import REPO_ROOT, load_config  # noqa: E402
from rmc.runner.schedule import get_block, load_schedule  # noqa: E402


def check(db: Path, schedule: dict, block_id: str) -> list[tuple[str, str]]:
    block = get_block(schedule, block_id)
    expected_rounds = {r["round_id"] for r in block["rounds"]}
    n_strategies = len(block["rounds"][0]["strategy_order"])
    res = []
    with open_readonly(db) as c:
        complete = {r[0] for r in c.execute(
            "SELECT round_id FROM round_runs WHERE block_id = ? AND status = 'complete'", (block_id,))}
        missing = sorted(expected_rounds - complete)
        res.append(("OK", f"all {len(expected_rounds)} rounds complete") if not missing
                   else ("FAIL", f"rounds not complete: {missing} - rerun scripts/run_block.py --block {block_id}"))

        crashed = c.execute("SELECT COUNT(*) FROM round_runs WHERE block_id = ? AND status = 'started'",
                            (block_id,)).fetchone()[0]
        res.append(("WARN", f"{crashed} crashed round run(s) kept for audit (excluded from analysis)") if crashed
                   else ("OK", "no crashed round runs"))

        bad = c.execute("""SELECT r.round_id, COUNT(t.id) FROM round_runs r LEFT JOIN transfers t
                           ON t.round_run_id = r.id WHERE r.block_id = ? AND r.status = 'complete'
                           GROUP BY r.id HAVING COUNT(t.id) != ?""", (block_id, n_strategies)).fetchall()
        res.append(("FAIL", f"complete rounds with wrong transfer count: {bad}") if bad
                   else ("OK", f"{n_strategies} transfers in every complete round"))

        row = c.execute("""SELECT COUNT(*), SUM(1 - success), SUM(timed_out), SUM(n_failed_attempts),
                                  SUM(CASE WHEN success = 1 AND verified = 0 THEN 1 ELSE 0 END)
                           FROM transfers t JOIN round_runs r ON t.round_run_id = r.id
                           WHERE t.block_id = ? AND r.status = 'complete'""", (block_id,)).fetchone()
        n, failed, timeouts, failed_attempts, unverified = (x or 0 for x in row)
        res.append(("WARN" if failed else "OK", f"{n} transfers, {failed} finally failed, {timeouts} with a timeout, "
                                               f"{failed_attempts} failed attempts"))
        if unverified:
            res.append(("FAIL", f"{unverified} transfers marked success but not verified"))

        cond = {tuple(r) for r in c.execute(
            "SELECT DISTINCT condition, degraded_provider FROM round_runs WHERE block_id = ?", (block_id,))}
        want = (block["condition"], block["degraded_provider"])
        res.append(("OK", f"condition {want} as scheduled") if cond <= {want}
                   else ("FAIL", f"condition rows {cond} do not match the schedule {want}"))

        for col, label in (("machine_id", "machine"), ("code_version", "code version"), ("config_hash", "config")):
            vals = sorted(r[0] for r in c.execute(f"SELECT DISTINCT {col} FROM round_runs WHERE block_id = ?",
                                                  (block_id,)))
            if len(vals) > 1:
                res.append(("FAIL", f"more than one {label} in this block: {vals}"))
            elif any(str(v).endswith("-dirty") for v in vals):
                res.append(("WARN", f"{label} {vals[0]} has uncommitted changes (development run?)"))
            else:
                res.append(("OK", f"single {label}: {vals[0] if vals else '-'}"))
    return res


def main() -> None:
    cfg = load_config()
    ap = argparse.ArgumentParser()
    ap.add_argument("--block", required=True)
    ap.add_argument("--db", default=str(REPO_ROOT / cfg["storage"]["db_path"]))
    ap.add_argument("--schedule", default=str(REPO_ROOT / cfg["storage"]["schedule_path"]))
    args = ap.parse_args()
    results = check(Path(args.db), load_schedule(args.schedule), args.block)
    for level, msg in results:
        print(f"[{level:4s}] {msg}")
    print("\nAlso confirm by hand: `tc qdisc show dev <iface>` shows no netem (see docs/operator_checklist.md).")
    sys.exit(1 if any(level == "FAIL" for level, _ in results) else 0)


if __name__ == "__main__":
    main()
