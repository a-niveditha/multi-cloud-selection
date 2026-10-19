# Risk-Aware Adaptive Multi-Cloud Selection in Dynamic Networks

Computer Networks, Article Review project (2026).

> **Status: in development.** Experimental results have not been collected yet. This README describes the design and the planned structure; commands and file names below are the intended interface and may change as the code is written.

## What this project does

When a file must be uploaded to one of several cloud storage providers, can we choose the provider intelligently from the **current network condition**, **cost** and **recent observed reliability**, instead of always choosing by cost or location?

The proposed method is **adaptive, risk-aware and network-aware**:

- **Network-aware:** what is the network like right now (RTT, throughput, packet loss)?
- **Risk-aware:** how reliable has each provider been recently (timeouts, failures, instability)?
- **Adaptive:** how much should network, cost and risk matter under the current conditions?

For provider *i*, the selection score is:

```
Score_i = w_N * N_i + w_C * C_i - w_R * rho_i
```

where `N_i` is network performance, `C_i` is cost (higher for cheaper), `rho_i` is the observable risk penalty, and the weights `w_N`, `w_C`, `w_R` adapt to the current situation (for example, cost matters less and risk matters more under severe degradation). The provider with the highest score is selected. After every transfer, the outcome updates the provider history.

We are testing, not assuming, that this improves transfer performance and reliability.

## Hypotheses

- **H1 (primary):** under changing or severe degradation, the proposed method achieves lower or more stable end-to-end transfer time than static network-aware selection.
- **H2 (secondary):** the proposed method reduces poor provider choices and failure exposure; the effect is expected to be clearer under degraded conditions.

## Experiment overview

- **Providers:** AWS S3, Google Cloud Storage, Backblaze B2 (geographically distinct regions).
- **Strategies compared:** proposed risk-aware adaptive, static network-aware, cost-only, fixed-nearest-region, random.
- **Conditions** (applied with `tc`/`netem`, one provider's path at a time):
  - Natural: no injected impairment.
  - Mild: about +25% RTT, about 1% packet loss.
  - Severe: about +100% RTT, about 5% packet loss.
- **Trials:** at least 8 paired rounds per condition; the same 10 MB file under every strategy within a round; randomised strategy order; cooldown between transfers; degraded provider and severity rotated across blocks; block order randomised.
- **Metrics:** end-to-end transfer time, probing overhead, failures and timeouts, provider selected, failure-to-retry time, checksum/ETag verification.
- **Analysis:** paired t-test or Wilcoxon signed-rank test; results reported per condition and by whether the degraded provider was the one the baseline selected; cold-start behaviour reported separately.

## Planned repository structure

```
.
├── README.md
├── requirements.txt
├── .env.example            # credential variable names only, no secrets
├── .gitignore              # includes .env and data/
├── config/
│   └── config.yaml         # all tunable settings (weights, risk window, timeout, ...)
├── src/rmc/
│   ├── config.py
│   ├── db.py               # SQLite schema and helpers
│   ├── models.py           # probe/upload result data classes
│   ├── providers/          # base, aws_s3, gcs, b2, mock (also used for simulation)
│   ├── probing/            # RTT, throughput, packet-loss probes
│   ├── selection/          # normalisation, risk, adaptive weights, strategies
│   ├── netem/              # apply/clear tc/netem rules (with dry-run)
│   ├── runner/             # schedule, block runner, verification
│   └── analysis/           # statistics and plots
├── scripts/                # generate_schedule, run_block, check_block, export_data, run_simulation
├── tests/                  # pytest
├── data/                   # trial databases and logs (not committed)
└── docs/
    └── operator_checklist.md
```

## Requirements

**Software:** Python 3, boto3, google-cloud-storage, b2sdk, ping3 and/or custom HTTP probes, SQLite, Pandas, NumPy, SciPy, Matplotlib/Plotly, pytest, Git. An optional Flask/FastAPI REST layer is low priority. TLS is used for transport.

**Hardware (minimum):** Intel i5 / AMD Ryzen 5 or equivalent, 8 GB RAM, 50 GB free storage, stable broadband, native Ubuntu or an Ubuntu VM (needed for `tc`/`netem`), and accounts for AWS S3, Google Cloud Storage and Backblaze B2.
None of the hardware/account requirements beyond Python apply to simulation mode (below).

## Setup

```bash
git clone <repo-url>
cd <repo>
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # then fill in your credentials; never commit .env
```

Credentials are read from environment variables only. Do not put keys in code, config files or commits.

## Configuration

All tunable values live in `config/config.yaml`. Items still to be decided are marked `TODO`:

- weight-adaptation rule and weight values (currently a placeholder)
- risk history window and how risk components are combined
- timeout, cooldown and probe duration
- cost table and how the nearest region is determined
- number of rounds and warm-up rounds

**Freeze the configuration and tag the release in Git before the real trials. Do not change it afterwards.**

## Simulation mode (no cloud accounts, no Ubuntu)

The whole experiment can be run in a **simulated multi-cloud environment** on any OS
(Windows included), with no cloud accounts, credentials, buckets, VM or `tc`/`netem`:

```bash
pip install -r requirements.txt
python scripts/run_simulation.py                              # seed from config
python scripts/run_simulation.py --seed 7 --rounds-per-condition 30
```

It replaces only the cloud uploads and the network: the three providers are simulated
from the profiles under `simulation:` in `config/config.yaml` (RTT, throughput, loss,
jitter, failure/timeout rates, loss-driven failures and random congestion episodes),
and the mild/severe conditions are applied to the simulated paths. The selection
strategies, risk history, adaptive weights, failover, database and analysis are the
same code as in the real trials. Time is simulated, so a full run takes seconds.

Output goes to `data/simulation/seed<N>/` (database, schedule, and `analysis/` with
`summary.txt`, CSV tables and figures). The provider profiles are **assumptions, not
measurements**: report these as simulation results, state the profiles and the seed,
and do not tune the profiles after looking at which strategy wins.

## Running real trials

Team members running trials: follow **[docs/RUNNING_TRIALS.md](docs/RUNNING_TRIALS.md)** step by step.
The full checklist is in [docs/operator_checklist.md](docs/operator_checklist.md).

```bash
# 1. Run the tests
pytest

# 2. Measure the natural baseline and nearest region once per machine
python scripts/calibrate.py

# 3. Generate the trial schedule once, with a recorded random seed
python scripts/generate_schedule.py --seed 42

# 3. Pilot run (small, not part of the real data)
python scripts/run_block.py --block 1 --pilot

# 4. Real trials: the schedule decides everything, operators choose nothing
python scripts/run_block.py --block 1

# 5. Check the block after it finishes
python scripts/check_block.py --block 1

# 6. Export a read-only copy of the data for analysis
python scripts/export_data.py
```

The runner clears netem rules at start and end, refuses to run if the Git tree has uncommitted changes, skips rounds already completed so a crashed block can be resumed safely, and backs up the database after each block.

## Rules for running trials

1. Only **one person runs at a time** on a given machine. Never run two blocks simultaneously.
2. **Never split a paired round.** All five strategies in a round run on the same machine and network.
3. Use the same code tag, configuration, file, timeout and cooldown for every block.
4. Do not edit code or data during or between real blocks. If something crashes, note it and rerun the script.
5. Confirm netem rules are cleared after every block.
6. Every row records machine id, code version, block id and timestamp.
7. Do not tune parameters after seeing real trial results.

## Team

| Area | Owner |
|---|---|
| Cloud and transfer layer (uploads, verification, retry/failover) | TBD |
| Network layer (probes, netem, calibration) | TBD |
| Selection engine (risk, adaptive weights, strategies) | TBD |
| Data, runner and analysis | TBD |

Authors: Harini Murugesh Kumar, Thondepu Siva Sethamanyu, Mathu Metha, Niveditha.

## Limitations (summary)

Single client vantage point; free-tier limits and available regions may not match paid-tier performance; `tc`/`netem` approximates but does not reproduce every real congestion or routing failure; timeout-based failover can miss slowdowns that do not cause a timeout; a limited trial count reduces statistical power; weights and risk parameters are lightweight and manually configured; risk depends on recent history, so cold start is reported separately.

## Results

*Pending. Results will be added after the trials are completed and analysed.*

## License

TBD.