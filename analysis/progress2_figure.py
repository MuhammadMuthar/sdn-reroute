#!/usr/bin/env python3
"""Figure for the second progress report, drawn from the Chunk 7a cell table.

  .venv/bin/python analysis/progress2_figure.py
reads  results/regime/summary/regime_cells.csv (written by analysis/regime.py)
writes figures/progress2_regime.png
"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
c = pd.read_csv(os.path.join(REPO, "results/regime/summary/regime_cells.csv"))
SETTINGS = [(cc, rack, d) for cc in ("cubic", "bbr") for rack in (1, 0) for d in ("D1", "D2")]
MOVES = [("slow->fast", 0, "to a faster path, empty", "#2f6fc4", "o", "white"),
         ("slow->fast", 1, "to a faster path, busy", "#2f6fc4", "s", "#2f6fc4"),
         ("fast->slow", 0, "to a slower path, empty", "#c0392b", "o", "white"),
         ("fast->slow", 1, "to a slower path, busy", "#c0392b", "s", "#c0392b")]
DELAY = {"D1": "1 / 10 ms", "D2": "1 / 50 ms"}

fig, axs = plt.subplots(1, 2, figsize=(7.4, 3.5), dpi=220, sharey=True)
for ax, rate in zip(axs, (100, 10)):
    for i, (cc, rack, d) in enumerate(SETTINGS):
        if d == "D2":
            ax.axvspan(i - 0.5, i + 0.5, color="#f3f1ea", lw=0)
        for k, (direction, nq, lab, col, mk, face) in enumerate(MOVES):
            r = c[(c.rate == rate) & (c.cc == cc) & (c.rack == rack) & (c.delay_set == d)
                  & (c.direction == direction) & (c.n_q_design == nq)].iloc[0]
            ax.errorbar(i + (k - 1.5) * 0.17, r.dest_total_W10_pct, yerr=r.dest_total_W10_pct_ci,
                        fmt=mk, ms=4, color=col, mfc=face, capsize=2, lw=1,
                        label=lab if i == 0 else None)
    ax.axhline(0, color="#222", lw=0.7)
    ax.set_xticks(range(len(SETTINGS)))
    ax.set_xticklabels(["%s\nRACK %s\n%s" % (cc.upper() if cc == "bbr" else "Cubic",
                                               "on" if rack else "off", DELAY[d])
                        for cc, rack, d in SETTINGS], fontsize=5.6)
    ax.set_title("%d Mbit/s links" % rate, fontsize=8.5, loc="left")
    ax.tick_params(axis="y", labelsize=7)
    ax.yaxis.grid(True, color="#e6e5e0", lw=0.6)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
axs[0].set_ylabel("goodput lost on the destination path\nin the 10 s after one move, % of link",
                  fontsize=7.5)
axs[0].legend(fontsize=6.3, frameon=False, loc="upper left", title="the move goes",
              title_fontsize=6.3)
fig.tight_layout(w_pad=1.0)
out = os.path.join(REPO, "figures", "progress2_regime.png")
fig.savefig(out)
print(out)
