# How to run the trials (for team members)

Read this once, top to bottom, before you start. It takes about 1 hour to set up
your machine the first time, then about 15–30 minutes per block.

**The golden rules**

1. Run only the blocks assigned to you (table below). Never run someone else's block.
2. Once you start a block, finish it on the **same machine**. If it crashes, rerun the same command.
3. Never edit code, `config/config.yaml` or anything in `data/`.
4. Never commit or send your `.env` file or keys to anyone or anywhere.
5. If something looks wrong, **stop and post in the group**. Don't improvise.
6. Running at the same time as someone else is fine **only** on a different machine
   **and** a different internet connection. Two people on the same Wi-Fi (same
   home, hostel or lab network) must take turns, or they slow each other down and
   spoil both blocks. Before you start, post "starting B04" in the group.

---

## Block assignment

The coordinator fills this in. One person per block.

| Block | Person | Done? |
|---|---|---|
| B01 | | |
| B02 | | |
| B03 | | |
| B04 | | |
| B05 | | |
| B06 | | |
| B07 | | |
| B08 | | |
| B09 | | |

---

## 1. What you need

- **Ubuntu** 22.04 or 24.04, native or in a VM. Windows/macOS cannot run `tc`/`netem`.
- Admin (`sudo`) rights on it.
- A stable internet connection, ideally wired. Run your blocks from the same place each time.
- The **credentials file** contents. The coordinator sends them to you privately; never via GitHub or the group chat.

## 2. One-time setup

Open a terminal and run these one at a time:

```bash
sudo apt update && sudo apt install -y git python3-venv iproute2
git clone https://github.com/a-niveditha/multi-cloud-selection.git
cd multi-cloud-selection
git checkout trials-v1
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

`trials-v1` is the frozen version the coordinator tags. Use exactly the name they give you.

Find your network interface name:

```bash
ip route get 8.8.8.8
```

It's the word after `dev`, e.g. `enp0s3`, `eth0` or `wlp2s0`.

Now open `.env` in a text editor (`nano .env`) and fill in:

- `RMC_MACHINE_ID` = your name and machine, e.g. `harini-laptop`
- `RMC_NETEM_INTERFACE` = the interface name from above
- the credential lines the coordinator sent you

Save and close (in nano: Ctrl+O, Enter, Ctrl+X).

**Every time you open a new terminal** later, first run:

```bash
cd multi-cloud-selection
source .venv/bin/activate
```

## 3. Check that everything works

Run these and compare with what is expected:

| Command | Expected |
|---|---|
| `pytest -q` | ends with `passed`, no `failed` |
| `python scripts/make_payload.py` | the first `md5=` must equal the value the coordinator posted |
| `python scripts/smoke_cloud.py` | three uploads, each with `verified=True`, then `deleted` |
| `python scripts/netem_ctl.py check-targets` | `OK` for all three providers |
| `ip -6 route` | prints nothing (IPv6 off); if not, tell the coordinator |

Post a screenshot of these in the group. **Don't continue until the coordinator says OK.**

## 4. Calibrate your machine (once)

On your normal network, with nothing else downloading:

```bash
python scripts/calibrate.py
```

Post the printed RTT lines and the "Nearest order" line in the group. This creates
`data/calibration.json`. Never delete it.

## 5. Running a block

Do this for each block assigned to you, e.g. `B04`.

**Before:**

- [ ] Close everything else that uses the internet (browser, updates, cloud sync, calls, other VMs).
- [ ] Plug in the charger and turn off sleep/screen lock.
- [ ] `git status` says *nothing to commit, working tree clean*.
- [ ] `sudo -v` (type your password; this lets the script control `netem`).
- [ ] `python scripts/netem_ctl.py show` does **not** contain the word `netem`.

**Run:**

```bash
python scripts/run_block.py --block B04
```

Leave the machine alone until it prints `Done:`. Each line shows a transfer; `FAIL`
lines can happen (that's data, not an error).

**After:**

```bash
python scripts/check_block.py --block B04
```

- [ ] No `[FAIL]` lines.
- [ ] `python scripts/netem_ctl.py show` does **not** contain `netem`.
- [ ] Post in the group: block id, date/time, `check_block` output, anything unusual.

## 6. If something goes wrong

| What you see | What to do |
|---|---|
| `REFUSED: ...` | Nothing ran. Read the message, fix that one thing, run again. If unsure, post it. |
| `sudo: a password is required` | Run `sudo -v`, then run the block again. |
| A crash / traceback / Ctrl+C / power cut | Run `python scripts/netem_ctl.py show`. If it shows `netem`, run `python scripts/netem_ctl.py clear --execute`. Then run **the same** `run_block` command again. Finished rounds are skipped automatically. |
| Internet went down mid-block | Same as a crash: wait until it's back, then rerun the same command. |
| Anything else | Stop and post the full message in the group. |

Never delete or edit files in `data/` to "fix" something.

## 7. Sending your data

When **all** your blocks are done and checked:

```bash
python scripts/export_data.py
```

It prints a folder like `data/export/20261020T101500Z/`. Send the coordinator the
`trials.sqlite` file from that folder, renamed to your name (e.g. `harini.sqlite`),
via the shared drive. Keep your own `data/` folder until the report is submitted.

---

## For the coordinator only

Before anyone runs a real block:

1. Decide every `TODO` in `config/config.yaml` from the pilot, including `netem.targets`, the `analysis:` section and `netem.dry_run: false`.
2. Generate the schedule once and commit it with the frozen config:
   `python scripts/generate_schedule.py --seed <seed>` → `config/schedule.json`.
3. Commit, then `git tag trials-v1 && git push origin main trials-v1`.
4. Post the payload `md5` (from `make_payload.py`) in the group.
5. Give each person their own cloud keys, limited to the trial buckets, privately.
   On the B2 bucket, set the lifecycle rule to "Keep only the last version", because
   each throughput probe overwrites its per-machine object and B2 keeps old versions.
6. Fill in the block table above. Spread natural, mild and severe blocks across people and days.
7. To analyse everyone's data together:
   `python scripts/analyse.py --db harini.sqlite siva.sqlite mathu.sqlite niveditha.sqlite`
   (it refuses if the same block appears in two files).

Note: each machine has its own calibration and "nearest" provider. If team members
are in different places, record that in the report (it is a limitation).
