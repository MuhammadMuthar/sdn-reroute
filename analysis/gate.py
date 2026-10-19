#!/usr/bin/env python3
"""Chunk 2 analysis: the reordering gate (criterion pre-registered in LOG.md).

Reads results/gate/<campaign>/runs/*/ written by experiments/gate.py and writes
  results/gate/<campaign>/per_trial.csv    one row per trial, every metric
  results/gate/<campaign>/summary.csv      per condition: mean and 95 % CI
  results/gate/<campaign>/verdict.md       the table and the gate verdict
  figures/gate_timeseries.png              goodput and cwnd around the move
Usage: .venv/bin/python analysis/gate.py results/gate/gate-2026-10-19
"""

import csv
import glob
import json
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from experiments.gate import CONDITIONS  # noqa: E402

W = 2.0                                   # damage window, s (pre-registered)
CONTROL_OF = {"R1": "C1", "R2": "C1", "R0": "C1", "R3": "C3", "R4": "C4",
              "C1": "C1", "C3": "C3", "C4": "C4"}
EVENT = ["TcpExtTCPDSACKRecv", "TcpExtTCPSACKReorder", "TcpExtTCPTSReorder",
         "TcpExtTCPRenoReorder", "TcpExtTCPSpuriousRTOs"]
SND_COUNTERS = ["TcpRetransSegs", "TcpExtTCPFastRetrans", "TcpExtTCPDSACKRecv",
                "TcpExtTCPDSACKRecvSegs", "TcpExtTCPSACKReorder", "TcpExtTCPTSReorder",
                "TcpExtTCPRenoReorder", "TcpExtTCPSpuriousRTOs", "TcpExtTCPTimeouts",
                "TcpExtTCPLossUndo", "TcpExtTCPDSACKUndo", "TcpExtTCPFullUndo",
                "TcpExtTCPPartialUndo", "TcpExtTCPLostRetransmit"]
RCV_COUNTERS = ["TcpExtTCPOFOQueue", "TcpExtTCPDSACKOldSent", "TcpExtTCPDSACKOfoSent"]
BIN = 0.1
T_LO, T_HI = -3.0, 6.0                    # time-series range around the move, s
REROUTE_COLOR, CONTROL_COLOR = "#2a78d6", "#8a8a85"
INK, INK2 = "#1f1f1e", "#5f5e58"


def ack_at(t_s, ack_t, ack_v):
    """Cumulative ACK (bytes) at times t_s: last ACK sent at or before t."""
    idx = np.searchsorted(ack_t, t_s, side="right") - 1
    return np.where(idx >= 0, ack_v[np.maximum(idx, 0)], 0)


def ss_series(path, t0):
    """(t relative to client start, cwnd, retrans_total, reord_seen) from ss.log."""
    rows, t = [], None
    for line in open(path):
        if line.startswith("T "):
            t = float(line.split()[1]) - t0
        elif t is not None and "cwnd:" in line:
            cwnd = int(re.search(r"\bcwnd:(\d+)", line).group(1))
            m = re.search(r"\bretrans:\d+/(\d+)", line)
            r = re.search(r"\breord_seen:(\d+)", line)
            rows.append((t, cwnd, int(m.group(1)) if m else 0, int(r.group(1)) if r else 0))
    return np.array(rows) if rows else np.zeros((0, 4))


def trace_metrics(rd, tm):
    """Packet-trace counts in [tm, tm+W): sender retransmissions, receiver
    first arrivals that are out of order, receiver duplicate arrivals."""
    snd = pd.read_csv(os.path.join(rd, "send_trace.csv.gz"))
    snd = snd[(snd.dir == 0) & (snd.len > 0)]
    end = (snd.seq + snd.len).to_numpy()
    t = snd.t_us.to_numpy() / 1e6
    hi = np.maximum.accumulate(np.concatenate([[0], end[:-1]]))
    retx = end <= hi
    snd_retx = int(np.sum(retx & (t >= tm) & (t < tm + W)))

    rcv = pd.read_csv(os.path.join(rd, "recv_trace.csv.gz"))
    data = rcv[(rcv.dir == 0) & (rcv.len > 0)]
    seen, hi_end, ooo, dup = set(), 0, 0, 0
    for tu, seq, ln in zip(data.t_us.to_numpy(), data.seq.to_numpy(), data.len.to_numpy()):
        tt = tu / 1e6
        inwin = tm <= tt < tm + W
        if seq in seen:
            dup += inwin
        else:
            seen.add(seq)
            if seq + ln <= hi_end:
                ooo += inwin
        hi_end = max(hi_end, seq + ln)
    acks = rcv[rcv.dir == 1]
    return snd_retx, ooo, dup, acks.t_us.to_numpy() / 1e6, acks.ack.to_numpy()


def load_trial(rd):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    if meta.get("errors") or "t_move" not in meta:
        return None, None
    t0 = meta["t_client_start"]
    tm = meta["t_move"] - t0
    c = meta["counters"]
    row = dict(run_id=meta["run_id"], condition=meta["condition"],
               kind=CONDITIONS[meta["condition"]]["kind"], src=meta["src_spine"],
               dst=meta["dst_spine"], t_move_s=round(tm, 4),
               flowmod_ms=round((meta["move"]["t_barrier"] - meta["move"]["t_sent"]) * 1e3, 3),
               snap_post_lag_s=round(c["post"]["t"] - meta["t_move"] - W, 4))
    for k in SND_COUNTERS:
        row["d_" + k] = c["post"]["h1"].get(k, 0) - c["pre"]["h1"].get(k, 0)
        row["pre_" + k] = c["pre"]["h1"].get(k, 0) - c["pre_window"]["h1"].get(k, 0)
    for k in RCV_COUNTERS:
        row["d_" + k] = c["post"]["h3"].get(k, 0) - c["pre"]["h3"].get(k, 0)
        row["pre_" + k] = c["pre"]["h3"].get(k, 0) - c["pre_window"]["h3"].get(k, 0)
    row["events"] = sum(row["d_" + k] for k in EVENT)
    row["event"] = int(row["events"] >= 1)
    row["pre_events"] = sum(row["pre_" + k] for k in EVENT)
    row["trace_snd_retx"], row["trace_rcv_ooo"], row["trace_rcv_dup"], at, av = \
        trace_metrics(rd, tm)
    a = ack_at(np.array([tm - W, tm, tm + W]), at, av)
    row["goodput_pre_mbps"] = (a[1] - a[0]) * 8 / W / 1e6
    row["goodput_W_mbps"] = (a[2] - a[1]) * 8 / W / 1e6
    row["bytes_W"] = int(a[2] - a[1])
    row["d_goodput_mbps"] = row["goodput_W_mbps"] - row["goodput_pre_mbps"]
    a_all = ack_at(np.array([2.0, tm]), at, av)
    row["goodput_steady_mbps"] = (a_all[1] - a_all[0]) * 8 / (tm - 2.0) / 1e6
    ts = json.load(open(os.path.join(rd, "tshark.json")))
    for side in ("recv", "send"):
        for fl in ("out_of_order", "retransmission", "spurious_retransmission"):
            row["tshark_%s_%s" % (side, fl)] = ts[side]["post"][fl]
    ss = ss_series(os.path.join(rd, "ss.log"), t0)
    if len(ss):
        before = ss[(ss[:, 0] < tm) & (ss[:, 0] >= tm - 0.5), 1]
        inwin = ss[(ss[:, 0] >= tm) & (ss[:, 0] < tm + W), 1]
        row["cwnd_before"] = float(np.median(before)) if len(before) else np.nan
        row["cwnd_min_W"] = float(inwin.min()) if len(inwin) else np.nan
    edges = tm + np.arange(T_LO, T_HI + BIN / 2, BIN)
    rate = np.diff(ack_at(edges, at, av)) * 8 / BIN / 1e6
    grid = np.arange(T_LO, T_HI, 0.05)
    cwnd = np.interp(tm + grid, ss[:, 0], ss[:, 1]) if len(ss) else np.full(len(grid), np.nan)
    return row, dict(rate=rate, cwnd=cwnd)


def mean_ci(x):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if len(x) < 2:
        return (x.mean() if len(x) else np.nan), np.nan
    return x.mean(), stats.t.ppf(0.975, len(x) - 1) * x.std(ddof=1) / np.sqrt(len(x))


def welch(a, b):
    """mean(a) - mean(b) with its Welch 95 % CI."""
    a, b = np.asarray(a, float), np.asarray(b, float)
    va, vb = a.var(ddof=1) / len(a), b.var(ddof=1) / len(b)
    d = a.mean() - b.mean()
    se = np.sqrt(va + vb)
    if se == 0:
        return d, d, d
    df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    h = stats.t.ppf(0.975, df) * se
    return d, d - h, d + h


def main():
    camp = sys.argv[1] if len(sys.argv) > 1 else os.path.join(REPO, "results", "gate",
                                                              "gate-2026-10-19")
    rows, series, skipped = [], {}, []
    for rd in sorted(glob.glob(os.path.join(camp, "runs", "*"))):
        if not os.path.exists(os.path.join(rd, "tshark.json")):
            skipped.append(os.path.basename(rd))
            continue
        row, s = load_trial(rd)
        if row is None:
            skipped.append(os.path.basename(rd))
            continue
        rows.append(row)
        series.setdefault(row["condition"], []).append(s)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(camp, "per_trial.csv"), index=False, float_format="%.6g")

    show = ["goodput_W_mbps", "d_goodput_mbps", "events", "d_TcpRetransSegs",
            "d_TcpExtTCPDSACKRecv", "d_TcpExtTCPSACKReorder", "d_TcpExtTCPTSReorder",
            "d_TcpExtTCPSpuriousRTOs", "d_TcpExtTCPTimeouts", "d_TcpExtTCPDSACKUndo",
            "d_TcpExtTCPLossUndo", "d_TcpExtTCPOFOQueue", "trace_snd_retx",
            "trace_rcv_ooo", "trace_rcv_dup", "tshark_recv_out_of_order",
            "tshark_recv_spurious_retransmission", "cwnd_before", "cwnd_min_W",
            "goodput_steady_mbps", "flowmod_ms"]
    summ = []
    order = [c for c in ["R1", "R2", "R0", "R3", "R4", "C1", "C3", "C4"]
             if c in set(df.condition)]
    for c in order:
        g = df[df.condition == c]
        r = dict(condition=c, label=CONDITIONS[c]["label"], n=len(g),
                 trials_with_event=int(g.event.sum()))
        for k in show:
            m, h = mean_ci(g[k])
            r[k + "_mean"], r[k + "_ci95"] = m, h
        ctl = df[df.condition == CONTROL_OF[c]]
        if CONDITIONS[c]["kind"] == "reroute" and len(ctl) >= 2 and len(g) >= 2:
            d, lo, hi = welch(ctl.goodput_W_mbps, g.goodput_W_mbps)
            r.update(control=CONTROL_OF[c], deficit_mbps=d, deficit_lo=lo, deficit_hi=hi,
                     deficit_bytes=d * 1e6 / 8 * W,
                     deficit_pct=100 * d / ctl.goodput_W_mbps.mean())
            dd, dlo, dhi = welch(ctl.d_goodput_mbps, g.d_goodput_mbps)
            r.update(did_mbps=dd, did_lo=dlo, did_hi=dhi)
            r["meets_a"] = r["trials_with_event"] >= 6
            r["meets_b"] = lo > 0
        summ.append(r)
    sm = pd.DataFrame(summ)
    sm.to_csv(os.path.join(camp, "summary.csv"), index=False, float_format="%.6g")

    def get(c, k):
        return sm.set_index("condition").loc[c, k] if c in set(sm.condition) else None
    passed = all(bool(get(c, "meets_a")) and bool(get(c, "meets_b")) for c in ("R1", "R2"))
    verdict = "PASS" if passed else "FAIL"

    lines = ["# Reordering gate: %s" % os.path.basename(camp), "",
             "Generated by `analysis/gate.py`; criterion pre-registered in LOG.md "
             "(2026-10-19, Chunk 2). W = 2 s after the flow-mod's barrier reply.", ""]
    if skipped:
        lines += ["Trials skipped (incomplete or errors): %s" % ", ".join(skipped), ""]
    lines += ["## Verdict: %s" % verdict, "",
              "| cond | move | n | (a) trials with reorder/spurious event | goodput in W, Mbit/s "
              "| deficit vs control, Mbit/s [95% CI] | deficit, KB (% of 2 s) | (a) | (b) |",
              "|---|---|---|---|---|---|---|---|---|"]
    for _, r in sm.iterrows():
        gp = "%.3f ± %.3f" % (r.goodput_W_mbps_mean, r.goodput_W_mbps_ci95)
        if "deficit_mbps" in r and not pd.isna(r.get("deficit_mbps")):
            dfc = "%.3f [%.3f, %.3f] vs %s" % (r.deficit_mbps, r.deficit_lo, r.deficit_hi,
                                                r.control)
            kb = "%.1f (%.2f %%)" % (r.deficit_bytes / 1e3, r.deficit_pct)
            ma, mb = ("yes" if r.meets_a else "no"), ("yes" if r.meets_b else "no")
        else:
            dfc, kb, ma, mb = "control", "", "", ""
        lines.append("| %s | %s | %d | %d / %d | %s | %s | %s | %s | %s |" % (
            r.condition, r.label, r.n, r.trials_with_event, r.n, gp, dfc, kb, ma, mb))
    lines += ["", "## Damage per move (mean ± 95 % CI over trials, counts in W)", "",
              "| cond | retrans segs | DSACKs recv | SACK reorder | TS reorder | spurious RTO "
              "| RTO | DSACK undo | loss undo | rcv OFO queued | trace: rcv out-of-order "
              "| trace: rcv dup | tshark OOO (rcv) | tshark spurious (rcv) "
              "| cwnd before → min in W | goodput change vs 2 s before, Mbit/s |",
              "|" + "---|" * 16]
    for _, r in sm.iterrows():
        def f(k, fmt="%.1f"):
            m, h = r[k + "_mean"], r[k + "_ci95"]
            return (fmt + " ± " + fmt) % (m, h) if not pd.isna(h) else fmt % m
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s "
                     "| %.0f → %.0f | %s |" % (
                         r.condition, f("d_TcpRetransSegs"), f("d_TcpExtTCPDSACKRecv"),
                         f("d_TcpExtTCPSACKReorder"), f("d_TcpExtTCPTSReorder"),
                         f("d_TcpExtTCPSpuriousRTOs"), f("d_TcpExtTCPTimeouts"),
                         f("d_TcpExtTCPDSACKUndo"), f("d_TcpExtTCPLossUndo"),
                         f("d_TcpExtTCPOFOQueue"), f("trace_rcv_ooo"), f("trace_rcv_dup"),
                         f("tshark_recv_out_of_order"),
                         f("tshark_recv_spurious_retransmission"),
                         r.cwnd_before_mean, r.cwnd_min_W_mean,
                         f("d_goodput_mbps", "%.3f")))
    # Post-hoc (not pre-registered): how much of each deficit comes from trials
    # where the move ended in a retransmission timeout.
    lines += ["", "## Post-hoc: deficit with and without RTO trials (not part of the verdict)",
              "", "| cond | trials with an RTO in W | deficit, all trials, KB "
              "| deficit, trials without RTO, KB [95% CI] |", "|---|---|---|---|"]
    for _, r in sm.iterrows():
        if pd.isna(r.get("deficit_mbps")):
            continue
        g = df[df.condition == r.condition]
        ctl = df[df.condition == r.control]
        clean = g[g.d_TcpExtTCPTimeouts == 0]
        if len(clean) >= 2:
            d, lo, hi = welch(ctl.goodput_W_mbps, clean.goodput_W_mbps)
            kb = [x * 1e6 / 8 * W / 1e3 for x in (d, lo, hi)]
            nr = "%.1f [%.1f, %.1f] (n=%d)" % (kb[0], kb[1], kb[2], len(clean))
        else:
            nr = "n/a"
        lines.append("| %s | %d / %d | %.1f | %s |" % (
            r.condition, int((g.d_TcpExtTCPTimeouts > 0).sum()), len(g),
            r.deficit_bytes / 1e3, nr))
    lines += ["", "Steady goodput before the move (2 s to the move, all trials): "
              "%.3f ± %.3f Mbit/s of 10." % mean_ci(df.goodput_steady_mbps),
              "Flow-mod to barrier reply: median %.2f ms." % df.flowmod_ms.median(), ""]
    with open(os.path.join(camp, "verdict.md"), "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    plot(sm, series, os.path.join(REPO, "figures", "gate_timeseries.png"))


def plot(sm, series, out):
    reroutes = [c for c in ["R1", "R2", "R0", "R3", "R4"] if c in series]
    if not reroutes:
        return
    t_rate = np.arange(T_LO, T_HI, BIN) + BIN / 2
    t_cwnd = np.arange(T_LO, T_HI, 0.05)
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": "#c9c8c1",
                         "axes.labelcolor": INK2, "xtick.color": INK2, "ytick.color": INK2})
    fig, axes = plt.subplots(2, len(reroutes), figsize=(3.1 * len(reroutes), 5.2),
                             sharex=True, sharey="row", facecolor="#fcfcfb")
    axes = np.atleast_2d(axes).reshape(2, -1)

    def band(ax, t, arr, color, label, z):
        arr = np.array(arr)
        m = np.nanmean(arr, axis=0)
        h = stats.t.ppf(0.975, len(arr) - 1) * np.nanstd(arr, axis=0, ddof=1) / np.sqrt(len(arr))
        ax.fill_between(t, m - h, m + h, color=color, alpha=0.18, lw=0, zorder=z)
        ax.plot(t, m, color=color, lw=1.6 if z > 2 else 1.2, label=label, zorder=z + 1)

    for i, c in enumerate(reroutes):
        ctl = CONTROL_OF[c]
        for row, key, t in ((0, "rate", t_rate), (1, "cwnd", t_cwnd)):
            ax = axes[row, i]
            ax.set_facecolor("#fcfcfb")
            if ctl in series:
                band(ax, t, [s[key] for s in series[ctl]], CONTROL_COLOR,
                     "no reroute (%s, n=%d)" % (ctl, len(series[ctl])), 1)
            band(ax, t, [s[key] for s in series[c]], REROUTE_COLOR,
                 "reroute (%s, n=%d)" % (c, len(series[c])), 3)
            ax.axvline(0, color=INK2, lw=0.8, ls=(0, (3, 3)))
            ax.axvspan(0, W, color="#e8e7e1", alpha=0.5, lw=0, zorder=0)
            ax.grid(axis="y", color="#e8e7e1", lw=0.6)
            for sp in ("top", "right"):
                ax.spines[sp].set_visible(False)
            if row == 0:
                ax.set_title("%s: %s" % (c, CONDITIONS[c]["label"]), fontsize=8.5, color=INK,
                             loc="left")
                ax.legend(fontsize=7, frameon=False, loc="lower left", labelcolor=INK2)
            else:
                ax.set_xlabel("time from reroute (s)")
        axes[0, 0].set_ylabel("goodput, 100 ms bins (Mbit/s)")
        axes[1, 0].set_ylabel("sender cwnd (segments)")
    lo = min(7.5, np.floor(min(np.nanmin(np.nanmean([s["rate"] for s in series[c]], axis=0))
                               for c in reroutes)))
    axes[0, 0].set_ylim(lo, 10.5)      # line chart: axis starts near the data on purpose
    fig.suptitle("Single-flow reroute at t = 0: goodput and sender cwnd, mean with 95% CI "
                 "band\n(shaded: the 2 s damage window; the goodput axis does not start "
                 "at zero; 10 Mbit/s links)", fontsize=9, color=INK, x=0.01, ha="left")
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    fig.savefig(out, dpi=150, facecolor=fig.get_facecolor())
    print("figure:", out)


if __name__ == "__main__":
    main()
