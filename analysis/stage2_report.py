#!/usr/bin/env python3
"""Stage 2 milestone report: the one figure and the numbers table, recomputed
from the raw per-run files (no number is copied from another summary).

  .venv/bin/python analysis/stage2_report.py

reads  results/compare/compare-2026-10-26/runs.csv      (ECMP, Hedera-style, cooldown-only)
       results/compare/costaware-2026-10-26/runs.csv    (cost-aware, formula and ML)
       results/dataset/dataset-2026-10-26/dataset/dataset.csv   (per-move damage)
       results/model/model-2026-10-26/model_summary.csv (held-out model error)
writes figures/stage2_results.png and docs/stage2_numbers.md
"""

import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMPARE = "results/compare/compare-2026-10-26/runs.csv"
COSTAWARE = "results/compare/costaware-2026-10-26/runs.csv"
DATASET = "results/dataset/dataset-2026-10-26/dataset/dataset.csv"
MODEL = "results/model/model-2026-10-26/model_summary.csv"

CTRLS = ["ecmp", "hedera", "cooldown", "costaware_formula", "costaware_ml"]
NAME = {"ecmp": "ECMP", "hedera": "Hedera-\nstyle", "cooldown": "cooldown-\nonly",
        "costaware_formula": "cost-aware\n(formula)", "costaware_ml": "cost-aware\n(ML)"}
FLAT = {k: v.replace("-\n", "-").replace("\n", " ") for k, v in NAME.items()}
COLOR = {"ecmp": "#7a7974", "hedera": "#d98324", "cooldown": "#2f6fc4",
         "costaware_formula": "#2e8b57", "costaware_ml": "#7b4bb3"}
INK = "#222222"


def mean_ci(v):
    """Mean and half-width of the 95 % confidence interval (t, n - 1 df)."""
    v = np.asarray(v, dtype=float)
    n = len(v)
    return v.mean(), stats.t.ppf(0.975, n - 1) * v.std(ddof=1) / np.sqrt(n)


def load_runs():
    runs = pd.concat([pd.read_csv(os.path.join(REPO, p)) for p in (COMPARE, COSTAWARE)],
                     ignore_index=True)
    runs = runs[runs.controller.isin(CTRLS)]
    assert runs.errors.isna().all(), "a run recorded an error"
    assert (runs.groupby("controller").seed.apply(lambda s: sorted(s) == list(range(1, 11)))).all()
    return runs


def load_moves():
    """The 60 deliberate reroutes with a valid matched no-move control. The
    label is bytes lost on the destination path over the window; here Mbit/s."""
    d = pd.read_csv(os.path.join(REPO, DATASET))
    d = d[(d.source == "design") & (d.replay_ok == 1)].copy()
    for w in (2, 10):
        d["dest_mbps_W%d" % w] = d["dest_total_deficit_W%d" % w] * 8 / w / 1e6
        d["moved_mbps_W%d" % w] = d["moved_deficit_W%d" % w] * 8 / w / 1e6
    return d


def strip(ax, runs, col, ylabel, fmt="%.2f"):
    for i, c in enumerate(CTRLS):
        v = runs[runs.controller == c].sort_values("seed")[col].values
        jit = np.linspace(-0.16, 0.16, len(v))
        ax.scatter(i + jit, v, s=11, color=COLOR[c], alpha=0.45, lw=0)
        m, h = mean_ci(v)
        ax.errorbar(i, m, yerr=h, fmt="o", ms=5.5, color=COLOR[c], mec=INK, mew=0.7,
                    capsize=3, lw=1.3)
        ax.annotate(fmt % m, (i, m), xytext=(8, 0), textcoords="offset points",
                    fontsize=7, va="center", color=INK)
    ax.set_xticks(range(len(CTRLS)))
    ax.set_xticklabels([NAME[c] for c in CTRLS], fontsize=6.5)
    ax.set_xlim(-0.5, len(CTRLS) - 0.3)
    ax.set_ylim(bottom=0)
    ax.set_ylabel(ylabel, fontsize=8)


def style(ax, title):
    ax.set_title(title, fontsize=8.5, loc="left", color=INK)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.tick_params(labelsize=7)
    ax.yaxis.grid(True, color="#e6e5e0", lw=0.6)
    ax.set_axisbelow(True)


def figure(runs, moves, model):
    fig, axs = plt.subplots(2, 2, figsize=(7.4, 4.9), dpi=220)
    axs = axs.ravel()
    strip(axs[0], runs, "eleph_goodput_mean", "elephant goodput, mean of 4 (Mbit/s)")
    style(axs[0], "(a) Benefit: big-flow throughput")
    strip(axs[1], runs, "fct_p50_ms", "short-flow completion time, median (ms)", "%.1f")
    style(axs[1], "(b) Side effect on short flows")

    ax = axs[2]
    v = moves.dest_mbps_W10.values
    # one move lies outside the plotted range; it is drawn in the edge bin
    ax.hist(np.clip(v, -0.199, 0.199), bins=np.arange(-0.2, 0.2001, 0.02), color="#2f6fc4",
            alpha=0.8)
    ax.axvline(0, color=INK, lw=0.8)
    m, h = mean_ci(v)
    ax.set_xlabel("goodput lost on the destination path in the 10 s after a move,\n"
                  "Mbit/s on 10 Mbit/s links (edge bins hold anything beyond ±0.2)", fontsize=7.5)
    ax.set_ylabel("deliberate reroutes (of %d)" % len(v), fontsize=8)
    ax.text(0.97, 0.95, "mean %.3f ± %.3f\n%d of %d within ±0.1" % (
        m, h, (np.abs(v) < 0.1).sum(), len(v)), transform=ax.transAxes, fontsize=7,
        ha="right", va="top")
    style(ax, "(c) Damage per reroute")

    ax = axs[3]
    t = model[model.target == "dest_total_deficit_W10"].set_index("estimator")
    order = [("mean", "predict\nthe mean", "#7a7974"), ("formula", "hand-written\nformula", "#2e8b57"),
             ("tree", "decision\ntree", "#7b4bb3"), ("gbr", "gradient\nboosting", "#7b4bb3")]
    for i, (k, lab, col) in enumerate(order):
        ax.bar(i, t.loc[k, "mae_mean"] / 1e3, yerr=t.loc[k, "mae_ci95"] / 1e3, color=col,
               alpha=0.8, capsize=3, error_kw={"lw": 1.1})
        ax.text(i, 1.2, "%.1f" % (t.loc[k, "mae_mean"] / 1e3), ha="center", fontsize=7,
                color="white")
    ax.set_xticks(range(len(order)))
    ax.set_xticklabels([o[1] for o in order], fontsize=6.5)
    ax.set_ylabel("error on held-out moves, kB per move", fontsize=8)
    style(ax, "(d) Predicting the damage")

    fig.tight_layout(w_pad=1.4, h_pad=1.2)
    out = os.path.join(REPO, "figures", "stage2_results.png")
    fig.savefig(out)
    return out


def numbers(runs, moves, model):
    L = ["# Stage 2 report: numbers", "",
         "Written by `analysis/stage2_report.py` from the raw run files. Mean ± 95 % CI "
         "(t, n - 1 df), n = 10 seeded runs per controller, 30 s each, 4 elephants and "
         "about 1050 short flows per run.", "",
         "| controller | elephant goodput, mean of 4 (Mbit/s) | worst elephant (Mbit/s) | "
         "short-flow FCT p50 (ms) | short-flow FCT p99 (ms) | reroutes / min |",
         "|---|---|---|---|---|---|"]
    cols = [("eleph_goodput_mean", "%.2f ± %.2f"), ("eleph_goodput_min", "%.2f ± %.2f"),
            ("fct_p50_ms", "%.1f ± %.1f"), ("fct_p99_ms", "%.0f ± %.0f"),
            ("reroutes_per_min", "%.1f ± %.1f")]
    for c in CTRLS:
        r = runs[runs.controller == c]
        L.append("| %s | " % FLAT[c] + " | ".join(f % mean_ci(r[k]) for k, f in cols) + " |")

    L += ["", "Paired by seed (difference, mean [95 % CI]):", ""]
    piv = runs.pivot(index="seed", columns="controller")
    for a, b, k, unit in [("hedera", "ecmp", "eleph_goodput_mean", "Mbit/s"),
                          ("hedera", "ecmp", "fct_p50_ms", "ms"),
                          ("costaware_formula", "hedera", "eleph_goodput_mean", "Mbit/s"),
                          ("costaware_ml", "hedera", "eleph_goodput_mean", "Mbit/s"),
                          ("costaware_ml", "hedera", "reroutes_per_min", "/min")]:
        m, h = mean_ci(piv[k][a] - piv[k][b])
        L.append("- %s minus %s, %s: %+.2f [%.2f, %.2f] %s" % (FLAT[a], FLAT[b], k, m, m - h,
                                                               m + h, unit))

    L += ["", "Deliberate reroutes (%d move / no-move pairs; deficit = no-move minus move, "
          "Mbit/s, positive = the move cost goodput):" % len(moves), ""]
    for k, lab in [("moved_mbps_W2", "moved flow, 2 s after the move"),
                   ("dest_mbps_W2", "destination path total, 2 s"),
                   ("dest_mbps_W10", "destination path total, 10 s")]:
        L.append("- %s: %+.3f ± %.3f" % ((lab,) + mean_ci(moves[k])))
    L.append("- moves within ±0.1 Mbit/s over 10 s: %d of %d" % (
        (moves.dest_mbps_W10.abs() < 0.1).sum(), len(moves)))
    L.append("- moves where the moved flow's socket saw reordering in the 2 s after: %d of %d "
             "(no-move arms: %d)" % ((moves.ss_reord_seen_W2_T > 0).sum(), len(moves),
                                     (moves.ss_reord_seen_W2_C > 0).sum()))

    L += ["", "Damage models, error on held-out groups of moves (kB per move over 10 s, "
          "mean ± 95 % CI over 10 folds):", ""]
    t = model[model.target == "dest_total_deficit_W10"]
    for _, r in t.iterrows():
        L.append("- %s: %.1f ± %.1f" % (r.estimator, r.mae_mean / 1e3, r.mae_ci95 / 1e3))
    out = os.path.join(REPO, "docs", "stage2_numbers.md")
    with open(out, "w") as f:
        f.write("\n".join(L) + "\n")
    return out


def main():
    runs, moves = load_runs(), load_moves()
    model = pd.read_csv(os.path.join(REPO, MODEL))
    print(figure(runs, moves, model))
    out = numbers(runs, moves, model)
    print(open(out).read())


if __name__ == "__main__":
    main()
