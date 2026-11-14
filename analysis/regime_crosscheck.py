#!/usr/bin/env python3
"""Independent cross-check of the Chunk 7a damage cells (written separately
from analysis/regime.py and sharing no code with it).

For every fast -> slow move onto an empty destination path (s1 -> s4, no other
elephant on s4) it recomputes the 10 s goodput deficit from iperf3's own
receiver interval reports: bytes E1's server received in the 10 s after the
controller's barrier reply, no-move arm (flow on s4 from the start) minus move
arm. It then prints the result beside results/regime/summary/regime_cells.csv.

  .venv/bin/python analysis/regime_crosscheck.py      # writes results/regime/summary/crosscheck.md
"""
import glob
import gzip
import json
import os

import numpy as np
import pandas as pd
from scipy import stats

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
W = 10.0


def received(run_dir, flow="E1"):
    """Bytes the iperf3 server received in [t_barrier, t_barrier + W]."""
    meta = json.load(open(os.path.join(run_dir, "meta.json")))
    t_move = meta["moves"][0]["reply"]["t_barrier"]
    j = json.load(gzip.open(os.path.join(run_dir, "iperf_server_%s.json.gz" % flow)))
    t_start = j["start"]["timestamp"]["timemillisecs"] / 1000.0
    total = 0.0
    for iv in j["intervals"]:
        s = iv["sum"]
        a, b = t_start + s["start"], t_start + s["end"]
        overlap = min(b, t_move + W) - max(a, t_move)
        if overlap > 0 and b > a:
            total += s["bytes"] * overlap / (b - a)
    return total


rows = []
for d in sorted(glob.glob(os.path.join(REPO, "results/regime/regime-*"))):
    _, rate, cc, rack, dset = os.path.basename(d).split("-")
    design = pd.read_csv(os.path.join(d, "design.csv"))
    if not os.path.isdir(os.path.join(d, "runs")):      # run directories not in this checkout
        print("skipped (no run directories):", os.path.basename(d))
        continue
    for _, t in design[(design.p == 1) & (design.q == 4) & (design.n_q == 0)].iterrows():
        arm = {a: received(os.path.join(d, "runs", "t%03d-%s" % (t.trial, a))) for a in "TC"}
        rows.append(dict(rate=int(rate), cc=cc, rack=int(rack[-1]), delay_set=dset, trial=t.trial,
                         deficit_pct=(arm["C"] - arm["T"]) * 8 / W / 1e6 / int(rate) * 100))
r = pd.DataFrame(rows)


def ci(v):
    return stats.t.ppf(0.975, len(v) - 1) * v.std(ddof=1) / np.sqrt(len(v))


g = r.groupby(["rate", "cc", "rack", "delay_set"]).deficit_pct.agg(["mean", ci, "size"]).reset_index()
cells = pd.read_csv(os.path.join(REPO, "results/regime/summary/regime_cells.csv"))
cells = cells[(cells.direction == "fast->slow") & (cells.n_q_design == 0)]
m = g.merge(cells[["rate", "cc", "rack", "delay_set", "dest_total_W10_pct", "dest_total_W10_pct_ci",
                   "verdict"]], on=["rate", "cc", "rack", "delay_set"])
L = ["# Chunk 7a cross-check: fast -> slow onto an empty path", "",
     "Written by `analysis/regime_crosscheck.py`. Deficit over 10 s, % of the link, mean ± 95 % CI. "
     "Left: recomputed here from iperf3 receiver reports. Right: analysis/regime.py (packet traces).",
     "", "| rate | cc | RACK | delays | n | iperf3 recomputation | regime.py | verdict there |",
     "|---|---|---|---|---|---|---|---|"]
for _, x in m.sort_values(["delay_set", "rate", "cc", "rack"], ascending=[False, False, False, False]).iterrows():
    L.append("| %d | %s | %s | %s | %d | %.1f ± %.1f | %.1f ± %.1f | %s |" % (
        x.rate, x.cc, "on" if x.rack else "off", x.delay_set, x["size"], x["mean"], x["ci"],
        x.dest_total_W10_pct, x.dest_total_W10_pct_ci, x.verdict))
L.append("")
L.append("Largest difference between the two cell means: %.2f points." % (
    (m["mean"] - m.dest_total_W10_pct).abs().max()))
out = os.path.join(REPO, "results/regime/summary/crosscheck.md")
open(out, "w").write("\n".join(L) + "\n")
print(open(out).read())
