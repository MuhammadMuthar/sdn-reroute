#!/usr/bin/env python3
"""Reordering inside the ingress leaf, per flow, from the l1 uplink captures
(Chunk 4, post hoc: added after the comparison showed the monitoring
controllers' senders with about 1000 reorder events per run, ECMP with 3).

Linux gives each TCP packet of a connected socket the next IPv4 ID, so the
IDs of one flow give the order in which its sender transmitted it. A data or
ACK packet whose ID is below the highest ID already captured for that flow
was sent earlier but left l1 later: it was reordered between the sender and
the uplink (inside l1's Open vSwitch, since a host's NIC and the veth keep
order). A retransmission gets a new, higher ID, so loss repair is not
counted, unlike sequence-number tests at this capture point (the uplink
queue drops before the capture). Both directions of one flow are not mixed:
only packets from the left hosts (the senders) are used. Left out: packets
without payload and with ID 0, which the kernel sends for a socket already in
TIME_WAIT (e.g. the ACK of a retransmitted FIN), not from the socket's
counter (found by the independent recomputation).

Per run (runs/<id>/mice_reorder.json) and campaign (mice_reorder.csv,
mice_reorder.md, mean ± 95 % CI over runs per controller):
  mice / elephants with a reordered packet, reordered packets, and the
  time of each reordered packet after the flow's first packet on the uplink
  (its SYN), median over the run; and, pooled over a controller's runs, the
  share of mice with a reordered packet by the time from the mouse's SYN to
  its first non-SYN packet on the uplink (if the reordering comes from the
  rule a controller installs on each SYN, mice whose first packets pass
  after that rule is in place should not be reordered).

  .venv/bin/python analysis/mice_reorder.py results/compare/<campaign>

Needs the uplink captures (runs/<id>/uplink_s<k>.pcap.gz), which are kept on
the machine that ran the campaign and not committed (.gitignore), so this
reruns only there; its per-run JSON and the CSV are committed.
"""

import glob
import gzip
import json
import os
import struct
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from analysis.gate import mean_ci  # noqa: E402

CTRL_ORDER = ["ecmp", "ecmp_mon", "hedera", "cooldown"]
HS_EDGES = (0, 20, 50, 100, float("inf"))     # ms, SYN to first non-SYN packet


def hs_label(b):
    lo, hi = HS_EDGES[b], HS_EDGES[b + 1]
    return "%d_%s" % (lo, "inf" if hi == float("inf") else "%d" % hi)


def read_pcap_gz(path):
    """(timestamp, frame bytes) per record of a gzipped classic pcap."""
    with gzip.open(path, "rb") as f:
        hdr = f.read(24)
        if len(hdr) < 24:
            return
        magic = struct.unpack("<I", hdr[:4])[0]
        e = "<" if magic in (0xA1B2C3D4, 0xA1B23C4D) else ">"
        nano = magic in (0xA1B23C4D, 0x4D3CB2A1)
        while True:
            rec = f.read(16)
            if len(rec) < 16:
                return
            s, frac, incl, _orig = struct.unpack(e + "IIII", rec)
            yield s + frac * (1e-9 if nano else 1e-6), f.read(incl)


def run_reorder(rd):
    sched = json.load(open(os.path.join(rd, "schedule.json")))
    meta = json.load(open(os.path.join(rd, "meta.json")))
    hpl = meta["hosts_per_leaf"]
    left = {ip for n, ip in sched["hosts"].items() if int(n[1:]) <= hpl}
    eleph = {(e["src_ip"], e["sport"]) for e in sched["elephants"]}
    pk = {}
    for k in range(1, 5):
        for ts, fr in read_pcap_gz(os.path.join(rd, "uplink_s%d.pcap.gz" % k)):
            if len(fr) < 54 or fr[12:14] != b"\x08\x00" or fr[23] != 6:
                continue
            src = "%d.%d.%d.%d" % tuple(fr[26:30])
            if src not in left:
                continue
            tot, ipid = struct.unpack("!HH", fr[16:20])
            ihl = (fr[14] & 0x0F) * 4
            t = 14 + ihl
            sport, dport = struct.unpack("!HH", fr[t:t + 4])
            if ipid == 0 and tot - ihl - (fr[t + 12] >> 4) * 4 == 0:
                continue                     # the kernel's reply for a TIME_WAIT socket
            syn = bool(fr[t + 13] & 0x02)
            pk.setdefault((src, sport, dport), []).append((ts, ipid, syn))
    out = dict(run_id=meta["run_id"], controller=meta["controller"], seed=sched["seed"])
    hs_n, hs_re = [0] * (len(HS_EDGES) - 1), [0] * (len(HS_EDGES) - 1)
    for kind in ("mice", "elephants"):
        n_flows = n_re = n_pk = n_late = 0
        delays = []
        for (src, sport, _), L in pk.items():
            if ((src, sport) in eleph) != (kind == "elephants"):
                continue
            L.sort()                         # capture time across the four uplinks
            n_flows += 1
            n_pk += len(L)
            hi, base, prev, late = None, 0, None, 0
            for ts, ipid, _syn in L:
                if prev is not None and prev - ipid > 32768:
                    base += 65536            # 16-bit wrap
                elif prev is not None and ipid - prev > 32768:
                    base -= 65536            # a late packet from before a wrap
                prev = ipid
                u = base + ipid
                if hi is not None and u < hi:
                    late += 1
                    delays.append((ts - L[0][0]) * 1e3)
                hi = u if hi is None else max(hi, u)
            n_late += late
            n_re += late > 0
            if kind == "mice" and L[0][2]:   # first captured packet is its SYN
                rest = [x for x in L if not x[2]]
                if rest:
                    hs = (rest[0][0] - L[0][0]) * 1e3
                    b = next(i for i, edge in enumerate(HS_EDGES[1:]) if hs < edge)
                    hs_n[b] += 1
                    hs_re[b] += late > 0
        out.update({"%s_flows" % kind: n_flows, "%s_packets" % kind: n_pk,
                    "%s_with_reordering" % kind: n_re, "%s_reordered_packets" % kind: n_late,
                    "%s_reorder_ms_after_first_p50" % kind:
                        float(np.median(delays)) if delays else None})
    for b in range(len(HS_EDGES) - 1):
        out["mice_hs_%s_n" % hs_label(b)] = hs_n[b]
        out["mice_hs_%s_reordered" % hs_label(b)] = hs_re[b]
    return out


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    camp = sys.argv[1]
    rows = []
    for rd in sorted(glob.glob(os.path.join(camp, "runs", "*"))):
        if not glob.glob(os.path.join(rd, "uplink_s1.pcap.gz")):
            print("skipped (no uplink capture):", rd)
            continue
        r = run_reorder(rd)
        json.dump(r, open(os.path.join(rd, "mice_reorder.json"), "w"), indent=1)
        rows.append(r)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(camp, "mice_reorder.csv"), index=False, float_format="%.6g")
    ctrls = [c for c in CTRL_ORDER if c in set(df.controller)] + \
        sorted(set(df.controller) - set(CTRL_ORDER))
    cols = [("mice_with_reordering", "mice with a reordered packet", "%.0f"),
            ("mice_reordered_packets", "reordered mouse packets", "%.0f"),
            ("mice_reorder_ms_after_first_p50", "median time of a reordered mouse packet "
             "after the mouse's SYN, ms", "%.1f"),
            ("elephants_reordered_packets", "reordered elephant packets", "%.0f"),
            ("mice_flows", "mice on the uplinks", "%.0f")]
    L = ["# Reordering inside l1 per flow (IPv4 ID order), %s" % os.path.basename(
            os.path.abspath(camp)), "",
         "Generated by `analysis/mice_reorder.py` from the l1 uplink captures. Post hoc. "
         "Mean ± 95 % CI over runs (t, n - 1 df); n = runs per controller.", "",
         "| per run | " + " | ".join("%s (n = %d)" % (c, (df.controller == c).sum())
                                     for c in ctrls) + " |", "|---|" + "---|" * len(ctrls)]
    for k, lab, f in cols:
        cells = []
        for c in ctrls:
            m, h = mean_ci(df[df.controller == c][k].astype(float))
            cells.append("n/a" if np.isnan(m) else ((f + " ± " + f) % (m, h) if not np.isnan(h)
                                                    else f % m))
        L.append("| %s | %s |" % (lab, " | ".join(cells)))
    if "ecmp" in ctrls:
        L += ["", "Paired by seed, minus ECMP, reordered mouse packets per run:", ""]
        b = df[df.controller == "ecmp"].set_index("seed").mice_reordered_packets
        for c in ctrls:
            if c == "ecmp":
                continue
            a = df[df.controller == c].set_index("seed").mice_reordered_packets
            s = sorted(set(a.index) & set(b.index))
            m, h = mean_ci((a.loc[s] - b.loc[s]).astype(float))
            L.append("- %s minus ECMP: %.0f [%.0f, %.0f] (n = %d)" % (c, m, m - h, m + h, len(s)))
    L += ["", "Pooled over each controller's runs: mice with a reordered packet, by the time "
          "from the mouse's SYN to its first non-SYN packet on the uplink:", "",
          "| SYN to first non-SYN packet | " + " | ".join(ctrls) + " |",
          "|---|" + "---|" * len(ctrls)]
    for b in range(len(HS_EDGES) - 1):
        lab = hs_label(b)
        cells = []
        for c in ctrls:
            d = df[df.controller == c]
            n, r = int(d["mice_hs_%s_n" % lab].sum()), int(d["mice_hs_%s_reordered" % lab].sum())
            cells.append("%d of %d (%.0f %%)" % (r, n, 100.0 * r / n) if n else "0 of 0")
        hi = HS_EDGES[b + 1]
        L.append("| %d-%s ms | %s |" % (HS_EDGES[b], "" if hi == float("inf") else "%d" % hi,
                                        " | ".join(cells)))
    open(os.path.join(camp, "mice_reorder.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
