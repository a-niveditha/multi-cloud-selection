# Operator checklist

Tick every line. If anything is unexpected, **stop and write it in the lab log** rather than improvising.
One person runs one block at a time on one machine. Never split a block across machines.
Blocks may run in parallel only on different machines on different internet connections.

## A. Once, before any real trial (whole team)

- [ ] All `TODO` values in `config/config.yaml` decided in the pilot, including the `analysis:` section.
- [ ] Cost table checked on the providers' pricing pages; `cost.checked_on` filled in.
- [ ] `pytest` passes on the trial machine.
- [ ] Configuration frozen: changes committed, release tagged (`git tag trials-v1`), tag pushed.
- [ ] `pip freeze > requirements-lock.txt` committed with the tag.

## B. Once per trial machine (Ubuntu, native or VM)

- [ ] `.env` filled from `.env.example`; `RMC_MACHINE_ID` set; `.env` **not** in `git status`.
- [ ] `python scripts/make_payload.py`: note the payload md5 in the lab log (must match on every machine).
- [ ] Network interface found (`ip route get 8.8.8.8` → `dev ...`) and set as `netem.interface`.
- [ ] IPv6 does not reach the providers (netem only filters IPv4): `ip -6 route` is empty, or IPv6 is disabled for the trials.
- [ ] `netem.targets` filled for each provider; `python scripts/netem_ctl.py check-targets` prints `OK` for all three.
- [ ] Bucket checks: S3 bucket uses SSE-S3 (not SSE-KMS), so the ETag is the MD5.
- [ ] `python scripts/smoke_cloud.py`: all three upload, verify `True`, and delete.
- [ ] netem dry-run looks right: `python scripts/netem_ctl.py apply --provider gcs --condition mild --dry-run`.
- [ ] netem really works (pilot): `sudo -v`, apply with `--execute`, then `tc -s qdisc show dev <if>` and `tc filter show dev <if>`; the RTT probe to that provider rises by about the expected amount and the others do not; `netem_ctl.py clear --execute`.
- [ ] `netem.dry_run: false` in config (committed before tagging).
- [ ] `python scripts/calibrate.py` on the natural network → `data/calibration.json`. Record the printed RTTs and the nearest order.
- [ ] `python scripts/generate_schedule.py --seed <seed>` **once** for the whole study; the seed is in the lab log. Copy the same `data/schedule.json` to every trial machine.
- [ ] Pilot: `python scripts/run_block.py --block B01 --pilot` (goes to `data/pilot.sqlite`, never into the real data).

## C. Before each block

- [ ] Right machine, right block (next unstarted block in the schedule; any order of *who* runs it, but never two at once on one machine).
- [ ] `git status` is clean and `git describe --tags` shows the trial tag.
- [ ] Nothing else is using the network (downloads, updates, video calls, other VMs). Laptop on mains power, sleep disabled.
- [ ] `sudo -v` (so the runner can apply/clear netem without a password prompt).
- [ ] `python scripts/netem_ctl.py show` shows no `netem`.
- [ ] Lab log: date, time, machine, block id, network (Wi-Fi/wired), anything unusual.

## D. Run

- [ ] `python scripts/run_block.py --block BXX`
- [ ] Do not touch the machine or the network while it runs. Do not edit code, config or data.
- [ ] If it prints `REFUSED`, fix what it says. Do not use `--allow-dirty` for real blocks.

## E. After each block

- [ ] `python scripts/check_block.py --block BXX`: no `FAIL` lines.
- [ ] `tc qdisc show dev <if>` shows no `netem`.
- [ ] A backup `data/backups/trials_BXX_*.sqlite` exists.
- [ ] Lab log: finish time, warnings from `check_block`, anything unusual.

## F. If something crashes

- [ ] Do **not** delete or edit anything in `data/`.
- [ ] Check netem is cleared (`tc qdisc show`); if not, `python scripts/netem_ctl.py clear --execute`.
- [ ] Note the error in the lab log, fix the cause (network, credentials, power), then rerun the **same** command. Completed rounds are skipped; the crashed round is repeated and the partial one is kept for audit.
- [ ] A block must be finished on the machine that started it.

## G. After all blocks

- [ ] `python scripts/check_block.py` for every block: no `FAIL`.
- [ ] `python scripts/export_data.py` → read-only snapshot in `data/export/`.
- [ ] `python scripts/analyse.py` on the export (`--db data/export/<stamp>/trials.sqlite`).
- [ ] Optional: delete the uploaded trial objects from the buckets (prefix `rmc-trials/`) to stop storage charges, **after** the export.
- [ ] Report the limitations: single vantage point, coarse loss probe, netem is only an approximation, fixed trial count (no power calculation), placeholder weight rule, and anything the lab log recorded.
