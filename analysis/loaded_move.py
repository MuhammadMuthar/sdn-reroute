#!/usr/bin/env python3
"""Chunk 3 analysis: what a slow->fast move costs when the new path is loaded.
Analysis plan pre-registered in LOG.md (2026-10-19, Chunk 3) before the
campaign ran.

Reads results/loaded/<campaign>/runs/*/ (experiments/runner.py, scenario
loaded_move) and writes
  <campaign>/per_run.csv     one row per run, every metric
  <campaign>/summary.csv     per condition: mean and 95 % CI
  <campaign>/comparisons.csv the pre-registered contrasts
  <campaign>/summary.md      tables
  figures/loaded_move_timeseries.png
Usage: .venv/bin/python analysis/loaded_move.py results/loaded/<campaign>

Goodput G of a flow in a window = in-order bytes the receiver acknowledged in
it (cumulative ACK in the receiver-side trace, as in the gate), Mbit/s.
Windows start at t_move = the move's barrier reply: W2, W5, W10 = 2, 5, 10 s.
"""

import glob
import gzip
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from scipy import stats  # noqa: E402

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from analysis.gate import EVENT, RCV_COUNTERS, SND_COUNTERS, ack_at, mean_ci, welch  # noqa: E402

WINDOWS = (2.0, 5.0, 10.0)
COND_ORDER = ["IC1", "IR2", "LC1", "LR0", "LR2"]
LABEL = {"IC1": "idle, E0 stays on s1 (sham move)", "IR2": "idle, E0 s4 -> s1",
         "LC1": "loaded, E0 and B on s1 (sham move)", "LR0": "loaded, E0 s2 -> s1 (no gap)",
         "LR2": "loaded, E0 s4 -> s1 (9 ms gap)"}
# Pre-registered contrasts: (name, reroute, reference). Deficit = ref - reroute.
CONTRASTS = [("P1 delay-gap cost under load", "LR2", "LR0"),
             ("S1 loaded slow->fast vs no move", "LR2", "LC1"),
             ("S1 loaded fast->fast vs no move", "LR0", "LC1"),
             ("S1 idle slow->fast vs no move (gate R2 replication)", "IR2", "IC1")]
BIN, T_LO, T_HI = 0.1, -3.0, 12.0
COLORS = {"IC1": "#b4b2a9", "IR2": "#7a786f", "LC1": "#8a8a85", "LR0": "#e08a2c",
          "LR2": "#2a78d6"}
INK, INK2 = "#1f1f1e", "#5f5e58"


def acks(rd, fid):
    p = os.path.join(rd, "recv_%s_trace.csv.gz" % fid)
    if not os.path.exists(p):
        return None
    t = pd.read_csv(p)
    a = t[t.dir == 1]
    return a.t_us.to_numpy() / 1e6, a.ack.to_numpy()


def ss_cwnd(path, sport):
    """(t epoch, cwnd) samples of the socket with local port `sport` from an
    ss log. Other sockets in the log's port filter (e.g. an iperf3 control
    connection whose ephemeral port happens to fall in it) are skipped."""
    rows, t, cur = [], None, None
    op = gzip.open if path.endswith(".gz") else open
    for line in op(path, "rt"):
        if line.startswith("T "):
            t, cur = float(line.split()[1]), None
        elif not line[:1].isspace() and line.strip():
            parts = line.split()
            cur = parts[3].rsplit(":", 1)[-1] if len(parts) >= 5 else None
        elif t is not None and "cwnd:" in line and cur == str(sport):
            rows.append((t, int(line.split("cwnd:")[1].split()[0])))
    return np.array(rows) if rows else np.zeros((0, 2))


def trace_counts(rd, tm, w=2.0):
    snd = pd.read_csv(os.path.join(rd, "send_E0_trace.csv.gz"))
    snd = snd[(snd.dir == 0) & (snd.len > 0)]
    end = (snd.seq + snd.len).to_numpy()
    t = snd.t_us.to_numpy() / 1e6
    hi = np.maximum.accumulate(np.concatenate([[0], end[:-1]]))
    retx = int(np.sum((end <= hi) & (t >= tm) & (t < tm + w)))
    # sequence numbers the sender transmitted more than once: a late first
    # arrival of one of these at the receiver is a loss repair, not reordering
    resent = set(snd.seq[snd.seq.duplicated()].to_numpy().tolist())
    rcv = pd.read_csv(os.path.join(rd, "recv_E0_trace.csv.gz"))
    data = rcv[(rcv.dir == 0) & (rcv.len > 0)]
    seen, hi_end, ooo, dup = set(), 0, 0, 0
    for tu, seq, ln in zip(data.t_us.to_numpy(), data.seq.to_numpy(), data.len.to_numpy()):
        inwin = tm <= tu / 1e6 < tm + w
        if seq in seen:
            dup += inwin
        else:
            seen.add(seq)
            if seq + ln <= hi_end and seq not in resent:
                ooo += inwin
        hi_end = max(hi_end, seq + ln)
    return retx, ooo, dup


def load_run(rd):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    sched = json.load(open(os.path.join(rd, "schedule.json")))
    if meta.get("errors") or not meta.get("moves"):
        return None, None
    t0 = meta["t0"]
    mv = meta["moves"][0]["reply"]
    tm = mv["t_barrier"] - t0
    row = dict(run_id=meta["run_id"], condition=sched["condition"], seed=sched["seed"],
               t_move_s=tm, flowmod_ms=(mv["t_barrier"] - mv["t_sent"]) * 1e3,
               pre_snap_to_barrier_ms=(mv["t_barrier"] - json.load(open(os.path.join(
                   rd, "counters.json")))["m0_pre"]["t"]) * 1e3,
               loaded=int(any(e["id"] == "B" for e in sched["elephants"])))
    # paths actually used (uplink captures) must match the condition, or the
    # run is not what its label says (e.g. a pin the switch rejected)
    fl = pd.read_csv(os.path.join(rd, "run_flows.csv"))
    pins = {p["flow"]: p["spine"] for p in sched["pins"]}
    want = {"E0": [pins["E0"]] + ([meta["moves"][0]["spine"]]
                                  if meta["moves"][0]["spine"] != pins["E0"] else [])}
    if "B" in pins:
        want["B"] = [pins["B"]]
    bad = []
    for fid, spines in want.items():
        r = fl[(fl.kind == "elephant") & (fl.id == fid)].iloc[0]
        got = [int(x.split(":")[0][1:]) for x in str(r.bytes_by_spine).split(";") if x]
        if sorted(got) != sorted(spines) or r.ack_spine != sched["ack_spine"]:
            bad.append("%s data %s ack %s" % (fid, got, r.ack_spine))
    row["path_check"] = "; ".join(bad) if bad else "ok"
    series = {}
    for fid in ("E0", "B"):
        a = acks(rd, fid)
        if a is None:
            continue
        at, av = a
        pts = ack_at(np.array([tm - 2.0, tm] + [tm + w for w in WINDOWS]), at, av)
        row["%s_G_pre" % fid] = (pts[1] - pts[0]) * 8 / 2.0 / 1e6
        for w, p in zip(WINDOWS, pts[2:]):
            row["%s_G_W%g" % (fid, w)] = (p - pts[1]) * 8 / w / 1e6
            row["%s_bytes_W%g" % (fid, w)] = int(p - pts[1])
        edges = tm + np.arange(T_LO, T_HI + BIN / 2, BIN)
        series[fid] = np.diff(ack_at(edges, at, av)) * 8 / BIN / 1e6
    for w in WINDOWS:              # a loaded run without B's trace must not read as B = 0
        b = row.get("B_G_W%g" % w, np.nan if row["loaded"] else 0.0)
        row["sum_G_W%g" % w] = row["E0_G_W%g" % w] + b
    c = json.load(open(os.path.join(rd, "counters.json")))
    snd, rcv = "h1", "h5"
    for w in WINDOWS:
        post = c["m0_%+g" % w]
        for k in SND_COUNTERS:
            row["d%g_%s" % (w, k)] = post[snd].get(k, 0) - c["m0_pre"][snd].get(k, 0)
        for k in RCV_COUNTERS:
            row["d%g_%s" % (w, k)] = post[rcv].get(k, 0) - c["m0_pre"][rcv].get(k, 0)
    row["events_W2"] = sum(row["d2_" + k] for k in EVENT)
    row["event_W2"] = int(row["events_W2"] >= 1)
    row["trace_snd_retx_W2"], row["trace_rcv_ooo_W2"], row["trace_rcv_dup_W2"] = \
        trace_counts(rd, tm)
    for fid, host in (("E0", "h1"), ("B", "h2")):
        p = os.path.join(rd, "ss_%s.log.gz" % host)
        if not os.path.exists(p):
            continue
        ss = ss_cwnd(p, 40001 if fid == "E0" else 40002)
        if not len(ss):
            continue
        tt = ss[:, 0] - t0
        before = ss[(tt >= tm - 0.5) & (tt < tm), 1]
        w2 = ss[(tt >= tm) & (tt < tm + 2), 1]
        w10 = ss[(tt >= tm) & (tt < tm + 10), 1]
        row["%s_cwnd_before" % fid] = float(np.median(before)) if len(before) else np.nan
        row["%s_cwnd_min_W2" % fid] = float(w2.min()) if len(w2) else np.nan
        row["%s_cwnd_mean_W10" % fid] = float(w10.mean()) if len(w10) else np.nan
        grid = np.arange(T_LO, T_HI, 0.05)
        series["%s_cwnd" % fid] = np.interp(tm + grid, tt, ss[:, 1])
    return row, series


def diff_of_diffs(a1, b1, a2, b2):
    """(mean a1 - mean b1) - (mean a2 - mean b2), Welch-Satterthwaite 95 % CI."""
    g = [np.asarray(x, float) for x in (a1, b1, a2, b2)]
    d = g[0].mean() - g[1].mean() - g[2].mean() + g[3].mean()
    v = [x.var(ddof=1) / len(x) for x in g]
    se = np.sqrt(sum(v))
    df = sum(v) ** 2 / sum(vi ** 2 / (len(x) - 1) for vi, x in zip(v, g))
    h = stats.t.ppf(0.975, df) * se
    return d, d - h, d + h


def main():
    camp = sys.argv[1]
    rows, ser, skipped = [], {}, []
    for rd in sorted(glob.glob(os.path.join(camp, "runs", "*"))):
        if not os.path.exists(os.path.join(rd, "run_summary.json")):
            skipped.append(os.path.basename(rd))
            continue
        row, s = load_run(rd)
        if row is None:
            skipped.append(os.path.basename(rd))
            continue
        if row["path_check"] != "ok":
            skipped.append("%s (paths: %s)" % (os.path.basename(rd), row["path_check"]))
            continue
        rows.append(row)
        ser.setdefault(row["condition"], []).append(s)
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(camp, "per_run.csv"), index=False, float_format="%.6g")
    conds = [c for c in COND_ORDER if c in set(df.condition)]

    metrics = (["E0_G_pre"] + ["E0_G_W%g" % w for w in WINDOWS] +
               ["B_G_W%g" % w for w in WINDOWS] + ["sum_G_W%g" % w for w in WINDOWS] +
               ["events_W2", "d2_TcpRetransSegs", "d2_TcpExtTCPDSACKRecv",
                "d2_TcpExtTCPTimeouts", "d10_TcpRetransSegs", "d10_TcpExtTCPTimeouts",
                "d10_TcpExtTCPDSACKUndo", "d10_TcpExtTCPLossUndo", "trace_rcv_ooo_W2",
                "trace_rcv_dup_W2", "E0_cwnd_before", "E0_cwnd_min_W2", "E0_cwnd_mean_W10",
                "B_cwnd_before", "B_cwnd_mean_W10", "flowmod_ms"])
    summ = []
    for c in conds:
        g = df[df.condition == c]
        r = dict(condition=c, label=LABEL[c], n=len(g), runs_with_event_W2=int(g.event_W2.sum()),
                 runs_with_rto_W10=int((g.d10_TcpExtTCPTimeouts > 0).sum()))
        for k in metrics:
            if k in g:
                r[k + "_mean"], r[k + "_ci95"] = mean_ci(g[k])
        summ.append(r)
    sm = pd.DataFrame(summ)
    sm.to_csv(os.path.join(camp, "summary.csv"), index=False, float_format="%.6g")

    comp = []
    for name, a, b in CONTRASTS:
        if a not in conds or b not in conds:
            continue
        ga, gb = df[df.condition == a], df[df.condition == b]
        for w in WINDOWS:
            for who in ("E0", "B", "sum"):
                k = "%s_G_W%g" % (who, w)
                if k not in ga or ga[k].isna().all() or gb[k].isna().all():
                    continue
                d, lo, hi = welch(gb[k], ga[k])
                comp.append(dict(contrast=name, reroute=a, reference=b, flow=who, window_s=w,
                                 deficit_mbps=d, lo=lo, hi=hi, deficit_kB=d * 1e6 / 8 * w / 1e3,
                                 deficit_pct=100 * d / gb[k].mean(), n_a=len(ga), n_b=len(gb)))
    if all(c in conds for c in ("LR2", "LC1", "IR2", "IC1")):
        for w in WINDOWS:
            k = "E0_G_W%g" % w
            g = {c: df[df.condition == c][k] for c in ("LR2", "LC1", "IR2", "IC1")}
            d, lo, hi = diff_of_diffs(g["LC1"], g["LR2"], g["IC1"], g["IR2"])
            comp.append(dict(contrast="S3 loaded minus idle slow->fast deficit", reroute="LR2",
                             reference="LC1 / IR2 vs IC1", flow="E0", window_s=w,
                             deficit_mbps=d, lo=lo, hi=hi, deficit_kB=d * 1e6 / 8 * w / 1e3,
                             deficit_pct=np.nan, n_a=len(g["LR2"]), n_b=len(g["LC1"])))
    cp = pd.DataFrame(comp)
    cp.to_csv(os.path.join(camp, "comparisons.csv"), index=False, float_format="%.6g")

    L = ["# Loaded move: %s" % os.path.basename(camp), "",
         "Generated by `analysis/loaded_move.py`; plan pre-registered in LOG.md "
         "(2026-10-19, Chunk 3). E0 = h1 -> h5 (moved), B = h2 -> h6 (competing, "
         "on s1). Windows start at the move's barrier reply.", ""]
    if skipped:
        L += ["Runs skipped (incomplete or errors): %s" % ", ".join(skipped), ""]
    L += ["## Goodput of E0 and B (Mbit/s, mean ± 95 % CI over runs)", "",
          "| cond | what | n | E0 2 s before | E0 W2 | E0 W5 | E0 W10 | B W10 | E0+B W10 "
          "| runs with reorder event in W2 | runs with RTO in W10 |", "|" + "---|" * 11]

    def f(r, k, fmt="%.3f"):
        if k + "_mean" not in r or pd.isna(r[k + "_mean"]):
            return ""
        m, h = r[k + "_mean"], r[k + "_ci95"]
        return (fmt + " ± " + fmt) % (m, h) if not pd.isna(h) else fmt % m
    for _, r in sm.iterrows():
        L.append("| %s | %s | %d | %s | %s | %s | %s | %s | %s | %d / %d | %d / %d |" % (
            r.condition, r.label, r.n, f(r, "E0_G_pre"), f(r, "E0_G_W2"), f(r, "E0_G_W5"),
            f(r, "E0_G_W10"), f(r, "B_G_W10"), f(r, "sum_G_W10"), r.runs_with_event_W2, r.n,
            r.runs_with_rto_W10, r.n))
    L += ["", "## Pre-registered contrasts (deficit = reference minus reroute; "
          "Welch 95 % CI)", "",
          "| contrast | flow | window | deficit, Mbit/s [95% CI] | KB | % of reference |",
          "|---|---|---|---|---|---|"]
    for _, r in cp.iterrows():
        pct = "" if pd.isna(r.deficit_pct) else "%.2f %%" % r.deficit_pct
        L.append("| %s (%s vs %s) | %s | W%g | %.3f [%.3f, %.3f] | %.1f | %s |" % (
            r.contrast, r.reroute, r.reference, r.flow, r.window_s, r.deficit_mbps, r.lo,
            r.hi, r.deficit_kB, pct))
    L += ["", "## Damage counters of E0 (sender h1, receiver h5; mean ± 95 % CI)", "",
          "| cond | retrans W2 | DSACKs W2 | RTO W2 | retrans W10 | RTO W10 | DSACK undo W10 "
          "| loss undo W10 | rcv out-of-order W2 (trace) | rcv dup W2 (trace) "
          "| E0 cwnd before → min W2, mean W10 | B cwnd before → mean W10 |",
          "|" + "---|" * 12]
    for _, r in sm.iterrows():
        L.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %.0f → %.0f, %.1f | %s |" % (
            r.condition, f(r, "d2_TcpRetransSegs", "%.1f"), f(r, "d2_TcpExtTCPDSACKRecv", "%.1f"),
            f(r, "d2_TcpExtTCPTimeouts", "%.1f"), f(r, "d10_TcpRetransSegs", "%.1f"),
            f(r, "d10_TcpExtTCPTimeouts", "%.1f"), f(r, "d10_TcpExtTCPDSACKUndo", "%.1f"),
            f(r, "d10_TcpExtTCPLossUndo", "%.1f"), f(r, "trace_rcv_ooo_W2", "%.1f"),
            f(r, "trace_rcv_dup_W2", "%.1f"), r.E0_cwnd_before_mean, r.E0_cwnd_min_W2_mean,
            r.E0_cwnd_mean_W10_mean,
            ("%.0f → %.1f" % (r.B_cwnd_before_mean, r.B_cwnd_mean_W10_mean))
            if "B_cwnd_before_mean" in r and not pd.isna(r.B_cwnd_before_mean) else ""))
    L += ["", "Flow-mod to barrier reply: median %.2f ms. Counter windows start at the "
          "snapshot taken just before the flow-mod is sent, median %.0f ms (max %.0f ms) "
          "before the barrier reply; goodput and trace windows start at the barrier reply."
          % (df.flowmod_ms.median(), df.pre_snap_to_barrier_ms.median(),
             df.pre_snap_to_barrier_ms.max()),
          "Every analysed run passed the path check (uplink captures: E0 on the pinned and "
          "then the target spine, B on s1, ACKs on s1).",
          "Receiver out-of-order (trace) counts first arrivals below the highest byte seen, "
          "excluding sequence numbers the sender transmitted more than once (loss repairs)."]
    open(os.path.join(camp, "summary.md"), "w").write("\n".join(L) + "\n")

    # ---- figure ---------------------------------------------------------------
    plt.rcParams.update({"font.size": 9, "axes.edgecolor": INK2, "axes.labelcolor": INK,
                         "xtick.color": INK2, "ytick.color": INK2})
    fig, axes = plt.subplots(3, 1, figsize=(7.5, 7.2), sharex=True)
    tb = np.arange(T_LO, T_HI, BIN) + BIN / 2
    tg = np.arange(T_LO, T_HI, 0.05)
    for c in conds:
        ss = ser[c]
        ls = "--" if c.startswith("I") else "-"
        for ax, key, x in ((axes[0], "E0", tb), (axes[1], "B", tb), (axes[2], "E0_cwnd", tg)):
            arr = [s[key] for s in ss if key in s]
            if not arr:
                continue
            arr = np.array(arr)
            if key != "E0_cwnd":                 # 0.5 s moving average for readability
                k = np.ones(5) / 5
                arr = np.array([np.convolve(a, k, mode="same") for a in arr])
                arr[:, :2] = arr[:, -2:] = np.nan    # the average is incomplete there
            ax.plot(x, arr.mean(0), ls, color=COLORS[c], lw=1.4,
                    label="%s: %s (n=%d)" % (c, LABEL[c], len(arr)))
    axes[0].set_ylabel("E0 goodput, Mbit/s\n(0.5 s avg)")
    axes[1].set_ylabel("B goodput, Mbit/s\n(0.5 s avg)")
    axes[2].set_ylabel("E0 cwnd, segments")
    axes[2].set_xlabel("time since the move's barrier reply, s")
    for ax in axes:
        ax.axvline(0, color=INK2, lw=0.8, ls=":")
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", color="#e6e5df", lw=0.6)
    h, lab = axes[0].get_legend_handles_labels()
    fig.legend(h, lab, frameon=False, fontsize=7.5, loc="lower center", ncol=2)
    axes[0].set_title("Moving E0 onto s1, idle (dashed) or shared with elephant B (solid); "
                      "mean over runs", fontsize=9, color=INK, loc="left")
    fig.tight_layout(rect=(0, 0.07, 1, 1))
    os.makedirs(os.path.join(REPO, "figures"), exist_ok=True)
    name = "loaded_move_timeseries%s.png" % (
        "" if json.load(open(os.path.join(camp, "plan.json"))).get("host_qdisc", "netem")
        == "netem" else "_default_qdisc")
    fig.savefig(os.path.join(REPO, "figures", name), dpi=150)
    print("\n".join(L))


if __name__ == "__main__":
    main()
