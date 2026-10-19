#!/usr/bin/env python3
"""Chunk 5: one row per rerouted elephant, pre-move features + measured damage.

Inputs (both written by experiments/runner.py):
  LIVE    results/compare/<name>   a multi-controller campaign; each run has
          policy_log.jsonl(.gz) (hedera.py: every move with its pre-move
          features), meta.json, schedule.json, ss_<host>.log.gz
  REPLAY  results/damage/<name>    `runner.py replay --spec jobs.json` of the
          pairs experiments/replay.py built: run_id <live>-e<round>-T (move)
          and -C (matched no-move control), each with recv_<E>_trace.csv.gz
          for every elephant, counters.json snapshots, schedule["replay"].

Row = (live run, policy round, moved flow). Features are the controller's own
feature record for that move (controllers/cost.FEATURES, controller-visible)
plus host-side columns from the live run's ss log (cwnd, unacked, rtt of the
moved flow just before the move, and the cwnd asymmetry vs the bystanders),
which the controller cannot see and which are kept for analysis only.

Labels, bytes over windows W2/W5/W10 after the event (C minus T, positive =
the move cost goodput), goodput G = cumulative ACK growth in the receiver
trace as in analysis/loaded_move.py:
  moved_deficit_W      G_C(f) - G_T(f) for the moved flow f
  bystander_deficit_W  sum over elephants already on f's destination spine
                       (post placement, not moved in this event)
  dest_total_deficit_W moved + bystander: the project's damage number
                       (CHUNKS.md amendment), the model target
Also nstat deltas of the moved flow's sender host over W (T minus C) for
retransmissions / reordering / undo counters, and a `group` column (live
seed) for leave-seed-out splits.

  .venv/bin/python analysis/damage_dataset.py --live results/compare/<n> \\
      --replay results/damage/<n> --out results/dataset/<n>
writes dataset.csv, data_dictionary.md, correlations.csv, and prints the row
count. figures/damage_correlations.png if --fig.
"""

import argparse
import glob
import gzip
import json
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from analysis.gate import ack_at  # noqa: E402
from analysis.loaded_move import ss_cwnd  # noqa: E402
from controllers.cost import CONTROLLER_FEATURES, DERIVED, derive  # noqa: E402

WINDOWS = (2.0, 5.0, 10.0)
SND_COUNTERS = ["TcpRetransSegs", "TcpExtTCPDSACKRecv", "TcpExtTCPSACKReorder",
                "TcpExtTCPTSReorder", "TcpExtTCPSpuriousRTOs", "TcpExtTCPTimeouts",
                "TcpExtTCPLossUndo", "TcpExtTCPDSACKUndo"]
TARGETS = ["moved_deficit_W%g", "bystander_deficit_W%g", "dest_total_deficit_W%g"]

DICTIONARY = {
    "live_run": "live run id (campaign runs/<id>)", "controller": "controller that made the move",
    "seed": "traffic seed of the live run", "group": "split group for leave-seed-out CV (= seed)",
    "round": "controller poll round of the event", "t_event_s": "event time, s since t0 (live)",
    "flow": "moved elephant id", "from_spine": "spine left", "to_spine": "destination spine",
    "n_moved_in_event": "elephants moved in the same round (one batch)",
    "n_bystanders": "elephants on the destination spine after the event, not moved in it",
    "rate_mbps": "[ctl] moved flow rate, last 1 s interval", "U_p": "[ctl] load of the source uplink, Mbit/s",
    "U_q": "[ctl] load of the destination uplink", "gain_mbps": "[ctl] U_p - (U_q + rate)",
    "elephants_on_p": "[ctl] elephants on the source path", "elephants_on_q": "[ctl] elephants on the destination path",
    "age_s": "[ctl] time since the flow's SYN", "bytes_so_far": "[ctl] bytes counted at the ingress leaf",
    "n_prev_moves": "[ctl] earlier moves of this flow", "since_last_move_s": "[ctl] s since its last move (empty = never)",
    "delay_ms_p_config": "[ctl] configured one-way delay of the source spine",
    "delay_ms_q_config": "[ctl] configured one-way delay of the destination spine",
    "delay_gap_ms": "[derived] q delay - p delay (negative = slow->fast)",
    "rate_share_q": "[derived] rate / (U_q + rate)", "bytes_in_flight_est": "[derived] rate x 2 x delay_p",
    "cwnd_before": "[host] moved flow cwnd (segments), median over 0.5 s before the move (ss)",
    "unacked_before": "[host] moved flow unacked segments before the move (bytes in flight)",
    "rtt_before_ms": "[host] moved flow srtt before the move",
    "bystander_cwnd_before": "[host] median cwnd of the bystanders before the move",
    "cwnd_asymmetry": "[host] cwnd_before / bystander_cwnd_before (cwnd-at-arrival asymmetry)",
    "replay_ok": "1 if both T and C runs exist without errors",
}
for w in WINDOWS:
    DICTIONARY["moved_deficit_W%g" % w] = "[label] bytes, G_C - G_T of the moved flow in %g s" % w
    DICTIONARY["bystander_deficit_W%g" % w] = "[label] bytes, sum over bystanders"
    DICTIONARY["dest_total_deficit_W%g" % w] = "[label] moved + bystander: the damage number"
    DICTIONARY["moved_G_T_W%g" % w] = "moved flow goodput in T, Mbit/s"
    DICTIONARY["moved_G_C_W%g" % w] = "moved flow goodput in C, Mbit/s"
    for k in SND_COUNTERS:
        DICTIONARY["d%g_%s" % (w, k)] = "[label] sender-host nstat delta over %g s, T minus C" % w


def _open(p):
    if not os.path.exists(p) and os.path.exists(p + ".gz"):
        p += ".gz"
    return gzip.open(p, "rt") if p.endswith(".gz") else open(p)


def live_moves(rd):
    """Feature records of every ok policy move in a live run, by (round, flow id)."""
    sched = json.load(open(os.path.join(rd, "schedule.json")))
    key_of = {(e["src_ip"], e["dst_ip"], e["sport"], e["dport"]): e["id"] for e in sched["elephants"]}
    out = {}
    with _open(os.path.join(rd, "policy_log.jsonl")) as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("kind") != "round":
                continue
            for m in r.get("moves", []):
                if not m.get("reply", {}).get("ok"):
                    continue
                f = m["flow"]
                fid = key_of.get((f["src"], f["dst"], f["sport"], f["dport"]))
                if fid:
                    out[(r["round"], fid)] = m
    return sched, out


def goodput_points(rd, fid, t_event, windows=WINDOWS):
    p = os.path.join(rd, "recv_%s_trace.csv.gz" % fid)
    if not os.path.exists(p):
        return None
    t = pd.read_csv(p)
    a = t[t.dir == 1]
    pts = ack_at(np.array([t_event] + [t_event + w for w in windows]),
                 a.t_us.to_numpy() / 1e6, a.ack.to_numpy())
    return {w: int(pts[i + 1] - pts[0]) for i, w in enumerate(windows)}


def host_features(live_rd, sched, meta, fid, t_event, bystanders):
    out = {}
    src = {e["id"]: e for e in sched["elephants"]}
    t0 = meta["t0"]

    def before(f):
        e = src[f]
        p = os.path.join(live_rd, "ss_%s.log" % e["src"])
        p = p if os.path.exists(p) else p + ".gz"
        if not os.path.exists(p):
            return None
        ss = ss_cwnd(p, e["sport"])
        if not len(ss):
            return None
        tt = ss[:, 0] - t0
        w = ss[(tt >= t_event - 0.5) & (tt < t_event), 1]
        return float(np.median(w)) if len(w) else None

    out["cwnd_before"] = before(fid)
    bc = [b for b in (before(f) for f in bystanders) if b is not None]
    out["bystander_cwnd_before"] = float(np.median(bc)) if bc else None
    out["cwnd_asymmetry"] = (out["cwnd_before"] / out["bystander_cwnd_before"]
                             if out["cwnd_before"] and out["bystander_cwnd_before"] else None)
    # unacked and rtt: last ss sample before the move
    e = src[fid]
    p = os.path.join(live_rd, "ss_%s.log" % e["src"])
    p = p if os.path.exists(p) else p + ".gz"
    out["unacked_before"] = out["rtt_before_ms"] = None
    if os.path.exists(p):
        t, cur = None, None
        for line in _open(p):
            if line.startswith("T "):
                t, cur = float(line.split()[1]) - t0, None
            elif not line[:1].isspace() and line.strip():
                parts = line.split()
                cur = parts[3].rsplit(":", 1)[-1] if len(parts) >= 5 else None
            elif t is not None and cur == str(e["sport"]) and t < t_event and "rtt:" in line:
                for tok in line.split():
                    if tok.startswith("unacked:"):
                        out["unacked_before"] = int(tok[8:])
                    elif tok.startswith("rtt:"):
                        out["rtt_before_ms"] = float(tok[4:].split("/")[0])
    return out


def build(live, replay):
    rows = []
    replay_runs = {os.path.basename(d): d for d in glob.glob(os.path.join(replay, "runs", "*"))}
    for rd in sorted(glob.glob(os.path.join(live, "runs", "*"))):
        name = os.path.basename(rd)
        if not os.path.exists(os.path.join(rd, "meta.json")):
            continue
        meta = json.load(open(os.path.join(rd, "meta.json")))
        if not os.path.exists(os.path.join(rd, "policy_log.jsonl")) and \
           not os.path.exists(os.path.join(rd, "policy_log.jsonl.gz")):
            continue
        sched, moves = live_moves(rd)
        rounds = sorted({r for r, _ in moves})
        for rnd in rounds:
            T, C = (replay_runs.get("%s-e%d-%s" % (name, rnd, arm)) for arm in ("T", "C"))
            info = None
            for d in (T, C):
                if d and os.path.exists(os.path.join(d, "schedule.json")):
                    info = json.load(open(os.path.join(d, "schedule.json"))).get("replay")
            moved = [fid for r, fid in moves if r == rnd]
            for fid in moved:
                m = moves[(rnd, fid)]
                row = dict(live_run=name, controller=meta["controller"], seed=meta["seed"],
                           group=meta["seed"], round=rnd, t_event_s=m["t"] - meta["t0"],
                           flow=fid, from_spine=m["from_spine"], to_spine=m["to_spine"],
                           n_moved_in_event=len(moved))
                for k in CONTROLLER_FEATURES:
                    row[k] = m.get(k)
                row.update(derive(m))
                post = info["post"] if info else {}
                bystanders = sorted(f for f, s in post.items()
                                    if s == m["to_spine"] and f not in moved)
                row["n_bystanders"] = len(bystanders) if info else None
                row.update(host_features(rd, sched, meta, fid, row["t_event_s"], bystanders))
                ok = bool(T and C and info)
                for d in (T, C):
                    if d and os.path.exists(os.path.join(d, "meta.json")):
                        ok = ok and not json.load(open(os.path.join(d, "meta.json"))).get("errors")
                    else:
                        ok = False
                row["replay_ok"] = int(ok)
                if ok:
                    mt = {d: json.load(open(os.path.join(d, "meta.json"))) for d in (T, C)}
                    te = {d: mt[d]["moves"][0]["reply"]["t_barrier"] - mt[d]["t0"] for d in (T, C)}
                    g = {d: {f: goodput_points(d, f, te[d]) for f in [fid] + bystanders}
                         for d in (T, C)}
                    cnt = {d: json.load(open(os.path.join(d, "counters.json"))) for d in (T, C)}
                    snd = {e["id"]: e["src"] for e in sched["elephants"]}[fid]
                    for w in WINDOWS:
                        gm = g[C][fid][w] - g[T][fid][w]
                        gb = sum(g[C][b][w] - g[T][b][w] for b in bystanders
                                 if g[C][b] and g[T][b])
                        row["moved_deficit_W%g" % w] = gm
                        row["bystander_deficit_W%g" % w] = gb
                        row["dest_total_deficit_W%g" % w] = gm + gb
                        row["moved_G_T_W%g" % w] = g[T][fid][w] * 8 / w / 1e6
                        row["moved_G_C_W%g" % w] = g[C][fid][w] * 8 / w / 1e6
                        for k in SND_COUNTERS:
                            dd = {}
                            for d in (T, C):
                                c = cnt[d]
                                dd[d] = c["m0_%+g" % w][snd].get(k, 0) - c["m0_pre"][snd].get(k, 0)
                            row["d%g_%s" % (w, k)] = dd[T] - dd[C]
                rows.append(row)
    cols = list(DICTIONARY)
    df = pd.DataFrame(rows)
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan
    return df[cols]


def correlations(df):
    feats = CONTROLLER_FEATURES + DERIVED + ["cwnd_before", "unacked_before", "cwnd_asymmetry"]
    tgt = [t % 10 for t in TARGETS]
    d = df[df.replay_ok == 1]
    out = []
    for f in feats:
        x = pd.to_numeric(d[f], errors="coerce")
        for t in tgt:
            y = d[t]
            ok = x.notna() & y.notna() & np.isfinite(x)
            r = x[ok].corr(y[ok], method="spearman") if ok.sum() >= 3 else np.nan
            out.append(dict(feature=f, target=t, n=int(ok.sum()), spearman=r))
    return pd.DataFrame(out)


def write_dictionary(path, df):
    with open(path, "w") as fh:
        fh.write("# Damage dataset: data dictionary\n\nRows: %d (%d with a complete replay "
                 "pair). One row per (live run, controller round, moved elephant). "
                 "[ctl] = controller-visible at decision time (model input); [derived] = "
                 "computed from [ctl] columns (controllers/cost.derive); [host] = sender-side "
                 "ss, analysis only; [label] = measured from the matched replay pair.\n\n"
                 "| column | meaning |\n|---|---|\n" % (len(df), int(df.replay_ok.sum())))
        for k, v in DICTIONARY.items():
            fh.write("| %s | %s |\n" % (k, v))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", required=True)
    ap.add_argument("--replay", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--fig", default=None, help="write the correlation heatmap PNG here")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    df = build(a.live, a.replay)
    df.to_csv(os.path.join(a.out, "dataset.csv"), index=False)
    write_dictionary(os.path.join(a.out, "data_dictionary.md"), df)
    corr = correlations(df)
    corr.to_csv(os.path.join(a.out, "correlations.csv"), index=False)
    print("rows: %d, with replay pair: %d -> %s" % (len(df), int(df.replay_ok.sum()), a.out))
    if a.fig and len(corr):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        piv = corr.pivot(index="feature", columns="target", values="spearman")
        fig, ax = plt.subplots(figsize=(6, 0.4 * len(piv) + 1.5))
        im = ax.imshow(piv.to_numpy(dtype=float), vmin=-1, vmax=1, cmap="RdBu_r", aspect="auto")
        ax.set_yticks(range(len(piv)), piv.index)
        ax.set_xticks(range(len(piv.columns)), piv.columns, rotation=30, ha="right")
        fig.colorbar(im, ax=ax, label="Spearman r")
        fig.tight_layout()
        os.makedirs(os.path.dirname(os.path.abspath(a.fig)), exist_ok=True)
        fig.savefig(a.fig, dpi=150)


if __name__ == "__main__":
    main()
