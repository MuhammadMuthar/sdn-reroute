#!/usr/bin/env python3
"""Chunk 7b: five controllers at 100 Mbit/s with spine delays 1/1/20/50 ms
(cubic, RACK on), seeds 1-10, every controller on every seed. Pre-registered
in LOG.md (2026-11-14): primary contrasts cost-aware (formula, ML) minus
Hedera-style and every controller minus ECMP on elephant goodput mean, worst
elephant and total elephant bytes; secondary reroutes, flaps, moves onto the
20 / 50 ms spines (and onto an empty one), mice FCT p50 / p99, retransmissions.
Mean ± 95 % CI per controller (t over runs); contrasts paired by seed (t CI on
the per-seed differences, analysis/chunk4.py). A run with an error or an
unfinished elephant is excluded, and with it that seed from every contrast
that involves its controller (chunk4.load_compare, chunk4.paired).

  .venv/bin/python analysis/chunk7b.py --camp results/compare7b/w1-longpath_stagger \\
      --camp results/compare7b/w2-ecmp_mixed --out results/compare7b/summary \\
      --fig figures/chunk7b_comparison.png
"""

import argparse
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from analysis import chunk4  # noqa: E402
from analysis.gate import mean_ci  # noqa: E402

CTRLS = ["ecmp", "hedera", "cooldown", "costaware_formula", "costaware_ml"]
LABEL = dict(chunk4.CTRL_LABEL)
COLOR = dict(chunk4.CTRL_COLOR, costaware_formula="#3a9a5b", costaware_ml="#8e5bb5")
PRIMARY = [("eleph_goodput_mean", "elephant goodput, mean of the run's elephants", "Mbit/s"),
           ("eleph_goodput_min", "elephant goodput, worst elephant", "Mbit/s"),
           ("eleph_bytes_total_MB", "elephant bytes delivered, total", "MB")]
SECONDARY = [("reroutes_per_min", "reroutes per minute", ""),
             ("flaps_per_min", "flaps per minute", ""),
             ("moves_to_s3", "moves onto s3 (20 ms) per run", ""),
             ("moves_to_s4", "moves onto s4 (50 ms) per run", ""),
             ("moves_to_slow_empty", "moves onto an empty s3 / s4 per run", ""),
             ("held_moves", "moves held back by the cost rule per run", ""),
             ("fct_p50_ms", "mice FCT p50", "ms"), ("fct_p99_ms", "mice FCT p99", "ms"),
             ("snd_TcpRetransSegs", "retransmitted segments (senders)", "")]


def policy_counts(rd):
    """Moves onto s3 / s4 (ok flow-mods, from the policy log) and the moves the
    cost rule held back (kind "held"); zeros for ECMP, which has no policy log."""
    out = dict(moves_to_s3=0, moves_to_s4=0, moves_to_slow_empty=0, held_moves=0)
    p = os.path.join(rd, "policy_log.jsonl.gz")
    if not os.path.exists(p):
        p = p[:-3]
    if not os.path.exists(p):
        return out
    op = gzip.open if p.endswith(".gz") else open
    with op(p, "rt") as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("kind") == "held":
                out["held_moves"] += len(r.get("held", []))
            if r.get("kind") != "round":
                continue
            for m in r.get("moves", []):
                if not m.get("reply", {}).get("ok") or m["to_spine"] == m["from_spine"]:
                    continue
                if m["to_spine"] in (3, 4):
                    out["moves_to_s%d" % m["to_spine"]] += 1
                    if m.get("elephants_on_q") == 0:
                        out["moves_to_slow_empty"] += 1
    return out


def load(camp):
    runs, flows, excl = chunk4.load_compare(camp)
    eb = flows[flows.kind == "elephant"].groupby("run_id").bytes.sum() / 1e6
    runs["eleph_bytes_total_MB"] = runs.run_id.map(eb)
    pc = pd.DataFrame([dict(run_id=r, **policy_counts(os.path.join(camp, "runs", r)))
                       for r in runs.run_id])
    runs = runs.merge(pc, on="run_id", how="left")
    plan = json.load(open(os.path.join(camp, "plan.json")))
    return runs, excl, plan


def fmt_ci(x, f="%.3g"):
    m, h = mean_ci(x)
    if np.isnan(m):
        return "n/a"
    return (f % m) if np.isnan(h) else ("%s ± %s" % (f % m, f % h))


def table(runs, ctrls):
    L = ["| metric | " + " | ".join(LABEL.get(c, c) for c in ctrls) + " |",
         "|---|" + "---|" * len(ctrls)]
    rows = []
    for k, lab, unit in PRIMARY + SECONDARY:
        cells = []
        for c in ctrls:
            x = runs[runs.controller == c][k].astype(float)
            m, h = mean_ci(x)
            rows.append(dict(controller=c, metric=k, n=int(x.notna().sum()), mean=m, ci95=h))
            cells.append(fmt_ci(x))
        L.append("| %s%s | %s |" % (lab, " (%s)" % unit if unit else "", " | ".join(cells)))
    return pd.DataFrame(rows), L


def contrast(runs, c, ref, k):
    d, m, h = chunk4.paired(runs, c, ref, k)
    return len(d), m, h


def verdict_sign(m, h, want=None):
    if np.isnan(m) or np.isnan(h):
        return "n/a"
    lo, hi = m - h, m + h
    s = "> 0" if lo > 0 else "< 0" if hi < 0 else "CI includes 0"
    if want is None:
        return s
    return ("supported" if s == want else "opposite sign" if s in ("> 0", "< 0") else
            "not supported")


def contrasts(runs, workload):
    rows = []
    pre = [("costaware_formula", "hedera"), ("costaware_ml", "hedera")] + \
          [(c, "ecmp") for c in CTRLS if c != "ecmp"]
    for c, ref in pre:
        for k, lab, unit in PRIMARY:
            n, m, h = contrast(runs, c, ref, k)
            want = "> 0" if (c, ref, k) == ("hedera", "ecmp", "eleph_goodput_mean") else None
            rows.append(dict(workload=workload, kind="primary", controller=c, reference=ref,
                             metric=k, label=lab, unit=unit, n_pairs=n, mean=m, ci95=h,
                             prediction="P1: > 0" if want else "no direction (P3)"
                             if ref == "hedera" else "reported",
                             outcome=verdict_sign(m, h, want)))
    for c in ("costaware_formula", "costaware_ml", "cooldown"):
        for k, lab, unit in SECONDARY:
            n, m, h = contrast(runs, c, "hedera", k)
            p2 = c.startswith("costaware") and k in ("moves_to_s3", "moves_to_s4",
                                                    "moves_to_slow_empty")
            rows.append(dict(workload=workload, kind="secondary", controller=c, reference="hedera",
                             metric=k, label=lab, unit=unit, n_pairs=n, mean=m, ci95=h,
                             prediction="P2: < 0" if p2 else "reported",
                             outcome=verdict_sign(m, h, "< 0" if p2 else None)))
    return pd.DataFrame(rows)


def ctable(df):
    L = ["| contrast | metric | n pairs | difference, mean [95 % CI] | prediction | outcome |",
         "|---|---|---|---|---|---|"]
    for r in df.itertuples():
        L.append("| %s minus %s | %s%s | %d | %s [%s, %s] | %s | %s |" % (
            LABEL.get(r.controller, r.controller), LABEL.get(r.reference, r.reference), r.label,
            " (%s)" % r.unit if r.unit else "", r.n_pairs, chunk4.fmt(r.mean),
            chunk4.fmt(r.mean - r.ci95), chunk4.fmt(r.mean + r.ci95), r.prediction, r.outcome))
    return L


def figure(parts, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    mets = PRIMARY + [("moves_to_slow_empty", "moves onto an empty\n20 / 50 ms spine per run", "")]
    fig, axes = plt.subplots(len(parts), len(mets), figsize=(3.4 * len(mets), 3.3 * len(parts)),
                             squeeze=False)
    for i, (wl, runs) in enumerate(parts):
        ctrls = [c for c in CTRLS if c in set(runs.controller)]
        for j, (k, lab, unit) in enumerate(mets):
            ax = axes[i, j]
            for x, c in enumerate(ctrls):
                v = runs[runs.controller == c][k].astype(float)
                m, h = mean_ci(v)
                ax.bar(x, m, color=COLOR.get(c, "#777"), width=0.7)
                ax.errorbar(x, m, yerr=0 if np.isnan(h) else h, color="k", capsize=3)
                ax.plot(np.full(len(v), x) + np.linspace(-0.2, 0.2, len(v)), v, ".",
                        color="k", ms=3, alpha=0.5)
            ax.set_xticks(range(len(ctrls)))
            ax.set_xticklabels([LABEL.get(c, c).replace(", ", ",\n") for c in ctrls],
                               fontsize=7, rotation=0)
            ax.set_title("%s%s" % (lab, " (%s)" % unit if unit else ""), fontsize=8)
            if j == 0:
                ax.set_ylabel(wl, fontsize=9)
    fig.suptitle("Five controllers, 100 Mbit/s, spine delays 1/1/20/50 ms, cubic, RACK on "
                 "(mean, 95 % CI, dots = seeds)", fontsize=10)
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, dpi=150)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--camp", action="append", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fig", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    L = ["# Chunk 7b: five controllers where a reroute costs something", "",
         "Generated by `analysis/chunk7b.py`. 100 Mbit/s, leaf queues 200 packets, spine one-way "
         "delays 1/1/20/50 ms, cubic, RACK on. Mean ± 95 % CI over runs; contrasts paired by "
         "seed (t CI on the differences). Pre-registration: LOG.md, 2026-11-14.", ""]
    parts, allc, allr = [], [], []
    for camp in a.camp:
        runs, excl, plan = load(camp)
        wl = plan.get("scenario", os.path.basename(camp))
        runs["workload"] = wl
        ctrls = [c for c in CTRLS if c in set(runs.controller)]
        _, T = table(runs, ctrls)
        C = contrasts(runs, wl)
        parts.append((wl, runs))
        allc.append(C)
        allr.append(runs)
        n_seeds = runs.groupby("controller").seed.nunique().to_dict()
        L += ["## %s (%s)" % (wl, os.path.relpath(camp, REPO)), "",
              "Runs per controller: " + ", ".join("%s %d" % (LABEL.get(c, c), n_seeds.get(c, 0))
                                                  for c in ctrls) + ".",
              ("Excluded runs: " + "; ".join(excl)) if excl else "No run excluded.", ""]
        L += T + ["", "### Contrasts, paired by seed", ""] + ctable(C) + [""]
    pd.concat(allc).to_csv(os.path.join(a.out, "chunk7b_contrasts.csv"), index=False,
                           float_format="%.6g")
    pd.concat(allr).to_csv(os.path.join(a.out, "chunk7b_runs.csv"), index=False,
                           float_format="%.6g")
    open(os.path.join(a.out, "chunk7b_summary.md"), "w").write("\n".join(L) + "\n")
    figure(parts, a.fig)
    print("\n".join(L))


if __name__ == "__main__":
    main()
