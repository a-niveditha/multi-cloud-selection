"""Static figures for the report (matplotlib, PNG).

Colours: one fixed colour per strategy, same in every figure (validated
categorical palette; colour is never the only cue - strategy names are always
on the axis or in a legend). Thin marks, recessive grid, no dual axes.
"""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from rmc.analysis.stats import BASELINES, CONDITIONS, DEGRADED, PROPOSED, measured, paired  # noqa: E402

STRATEGIES = [PROPOSED] + BASELINES
COLORS = {"proposed": "#2a78d6", "static_network": "#eb6834", "cost_only": "#1baf7a",
          "nearest": "#eda100", "random": "#e87ba4"}
LABELS = {"proposed": "Proposed", "static_network": "Static network", "cost_only": "Cost-only",
          "nearest": "Nearest region", "random": "Random"}
INK, MUTED, GRID = "#2b2b29", "#6b6a64", "#e4e3de"


def _style(ax) -> None:
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(GRID)
    ax.tick_params(colors=MUTED, labelsize=8)
    ax.yaxis.grid(True, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _save(fig, path: Path) -> Path:
    fig.tight_layout()
    fig.savefig(path, dpi=150, facecolor="white")
    plt.close(fig)
    return path


def plot_metric_by_condition(df: pd.DataFrame, metric: str, out: Path) -> Path:
    """Box plot of the metric per strategy, one panel per condition (measured rounds)."""
    m = measured(df)
    fig, axes = plt.subplots(1, len(CONDITIONS), figsize=(12, 4), sharey=True)
    for ax, cond in zip(axes, CONDITIONS):
        data = [m[(m["condition"] == cond) & (m["strategy"] == s)][metric].dropna() for s in STRATEGIES]
        bp = ax.boxplot(data, widths=0.5, patch_artist=True, showfliers=False,
                        medianprops={"color": INK, "linewidth": 1.5})
        for patch, s in zip(bp["boxes"], STRATEGIES):
            patch.set(facecolor=COLORS[s], alpha=0.35, edgecolor=COLORS[s], linewidth=1)
        rng = np.random.default_rng(0)
        for i, (s, d) in enumerate(zip(STRATEGIES, data), start=1):
            ax.scatter(i + rng.uniform(-0.12, 0.12, len(d)), d, s=10, color=COLORS[s], zorder=3, linewidths=0)
        ax.set_xticks(range(1, len(STRATEGIES) + 1), [LABELS[s] for s in STRATEGIES], rotation=30, ha="right")
        ax.set_title(cond.capitalize(), fontsize=10, color=INK)
        _style(ax)
    axes[0].set_ylabel("End-to-end time (s)" if metric == "end_to_end_time_s" else metric, color=INK)
    fig.suptitle("Transfer time by strategy and network condition (measured rounds)", fontsize=11, color=INK)
    return _save(fig, out)


def plot_paired_differences(df: pd.DataFrame, metric: str, policy: str, out: Path) -> Path:
    """Per-round difference (baseline - proposed). Above zero = proposed faster."""
    fig, axes = plt.subplots(1, len(CONDITIONS), figsize=(12, 4), sharey=True)
    for ax, cond in zip(axes, CONDITIONS):
        for i, b in enumerate(BASELINES, start=1):
            pairs = paired(df, b, metric, policy)
            d = pairs[pairs["condition"] == cond]["diff"].dropna()
            ax.scatter(np.full(len(d), i) + np.linspace(-0.12, 0.12, len(d)), d, s=12, color=COLORS[b],
                       zorder=3, linewidths=0)
            if len(d):
                ax.hlines(d.median(), i - 0.25, i + 0.25, color=INK, linewidth=1.5, zorder=4)
        ax.axhline(0, color=MUTED, linewidth=0.8)
        ax.set_xticks(range(1, len(BASELINES) + 1), [LABELS[b] for b in BASELINES], rotation=30, ha="right")
        ax.set_title(cond.capitalize(), fontsize=10, color=INK)
        _style(ax)
    axes[0].set_ylabel("Baseline − proposed (s)\nabove 0 = proposed faster", color=INK)
    fig.suptitle("Paired differences per round (bar = median)", fontsize=11, color=INK)
    return _save(fig, out)


def plot_subgroup_differences(df: pd.DataFrame, metric: str, policy: str, out: Path) -> Path:
    """Degraded conditions only: differences split by whether the baseline picked the degraded provider."""
    fig, axes = plt.subplots(1, len(DEGRADED), figsize=(11, 4), sharey=True)
    for ax, cond in zip(axes, DEGRADED):
        ticks, labels = [], []
        for i, b in enumerate(BASELINES):
            pairs = paired(df, b, metric, policy)
            sub = pairs[pairs["condition"] == cond]
            for j, (chose, tag) in enumerate(((True, "chose"), (False, "avoided"))):
                x = i * 3 + j
                d = sub[sub["chose_degraded_baseline"] == chose]["diff"].dropna()
                ax.scatter(np.full(len(d), x), d, s=12, color=COLORS[b], marker="o" if chose else "^",
                           zorder=3, linewidths=0)
                if len(d):
                    ax.hlines(d.median(), x - 0.3, x + 0.3, color=INK, linewidth=1.5, zorder=4)
                ticks.append(x)
                labels.append(f"{tag}\nn={len(d)}")
            ax.text(i * 3 + 0.5, -0.2, LABELS[b], transform=ax.get_xaxis_transform(), ha="center",
                    va="top", fontsize=8, color=INK)
        ax.axhline(0, color=MUTED, linewidth=0.8)
        ax.set_xticks(ticks, labels, fontsize=7)
        ax.set_title(f"{cond.capitalize()}: did the baseline pick the degraded provider?", fontsize=9, color=INK)
        _style(ax)
    axes[0].set_ylabel("Baseline − proposed (s)", color=INK)
    return _save(fig, out)


def plot_degraded_choice_rate(df: pd.DataFrame, out: Path) -> Path:
    """Share of measured rounds in which each strategy picked the degraded provider first."""
    m = measured(df)
    m = m[m["condition"].isin(DEGRADED)]
    fig, ax = plt.subplots(figsize=(8, 4))
    width = 0.8 / len(STRATEGIES)
    for i, s in enumerate(STRATEGIES):
        rates = [m[(m["condition"] == c) & (m["strategy"] == s)]["chose_degraded"].astype(float).mean() * 100
                 for c in DEGRADED]
        xs = np.arange(len(DEGRADED)) + (i - (len(STRATEGIES) - 1) / 2) * width
        bars = ax.bar(xs, rates, width=width * 0.9, color=COLORS[s], label=LABELS[s])
        ax.bar_label(bars, fmt="%.0f%%", fontsize=7, color=MUTED, padding=2)   # so 0 % is visible
    ax.set_xticks(range(len(DEGRADED)), [c.capitalize() for c in DEGRADED])
    ax.set_ylabel("Rounds choosing the degraded provider (%)", color=INK)
    ax.set_ylim(0, 100)
    ax.legend(frameon=False, fontsize=8, ncol=len(STRATEGIES), loc="upper center", bbox_to_anchor=(0.5, -0.1))
    ax.set_title("Poor first choices under degradation", fontsize=11, color=INK)
    _style(ax)
    return _save(fig, out)


def plot_failures(df: pd.DataFrame, out: Path) -> Path:
    """Failed attempts (timeouts + errors + checksum mismatches) per strategy and condition."""
    m = measured(df)
    fig, ax = plt.subplots(figsize=(8, 4))
    width = 0.8 / len(STRATEGIES)
    for i, s in enumerate(STRATEGIES):
        counts = [m[(m["condition"] == c) & (m["strategy"] == s)]["n_failed_attempts"].sum() for c in CONDITIONS]
        xs = np.arange(len(CONDITIONS)) + (i - (len(STRATEGIES) - 1) / 2) * width
        bars = ax.bar(xs, counts, width=width * 0.9, color=COLORS[s], label=LABELS[s])
        ax.bar_label(bars, fmt="%d", fontsize=7, color=MUTED, padding=2)
    ax.set_xticks(range(len(CONDITIONS)), [c.capitalize() for c in CONDITIONS])
    ax.set_ylabel("Failed upload attempts", color=INK)
    ax.yaxis.get_major_locator().set_params(integer=True)
    ax.legend(frameon=False, fontsize=8, ncol=len(STRATEGIES), loc="upper center", bbox_to_anchor=(0.5, -0.1))
    ax.set_title("Failure exposure (measured rounds)", fontsize=11, color=INK)
    _style(ax)
    return _save(fig, out)


def make_all(df: pd.DataFrame, acfg: dict, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    metric, policy = acfg["metric"], acfg["failed_transfer_policy"]
    return [
        plot_metric_by_condition(df, metric, out_dir / "metric_by_condition.png"),
        plot_paired_differences(df, metric, policy, out_dir / "paired_differences.png"),
        plot_subgroup_differences(df, metric, policy, out_dir / "subgroup_differences.png"),
        plot_degraded_choice_rate(df, out_dir / "degraded_choice_rate.png"),
        plot_failures(df, out_dir / "failures.png"),
    ]
