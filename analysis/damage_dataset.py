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
    # only packets carrying an ACK: the receiver's closing RSTs have ack 0 in
    # the trace (analysis/chunk4.py, Chunk 4 part 4)
    a = t[(t["dir"] == 1) & ((t["flags"] & 0x10) != 0)]
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
                    # Chunk 4 sensitivity rules (Chunk 5 plan): no label when this row's
                    # moved flow or a bystander started > 0.5 s late in an arm (lost SYN),
                    # or when W10 runs past the end of the elephants
                    from analysis.chunk4 import LATE_START_S as C4_LATE, start_lag
                    sch = {d: json.load(open(os.path.join(d, "schedule.json"))) for d in (T, C)}
                    lags = [start_lag(d, f, sch[d]) for d in (T, C) for f in [fid] + bystanders]
                    row["max_start_lag_s"] = float(np.nanmax(lags))
                    end = min(e["start"] + e["duration"] for e in sched["elephants"])
                    if row["max_start_lag_s"] > C4_LATE:
                        row["excluded"] = "late start (Chunk 4 sensitivity rule)"
                    elif max(te.values()) + WINDOWS[-1] > end:
                        row["excluded"] = "W10 past the end of the elephants"
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
    return df[cols + [c for c in df.columns if c not in cols]]


# ---------------------------------------------------------------- Chunk 5 design
# Rows from the deliberate-reroute campaign (experiments/damage_design.py): one
# trial = a move arm T and a matched no-move arm C, both under the monitor-only
# controller, whose policy_log gives the controller-visible features.
ELEPHANT_MBPS = 1.0       # controllers/hedera.py ELEPHANT_MBPS: what the controller counts
LATE_START_S = 5.0        # pre-registered exclusion: an elephant not yet at its receiver
                          # 5 s before the move, in either arm (lost SYNs)
SS_W = (2.0, 10.0)
NSTAT_W = (2.0, 10.0)
DESIGN_DICTIONARY = {
    "source": "design (Chunk 5 deliberate reroute) or chunk4 (a controller's own move, replayed)",
    "scenario": "direction (slow->fast, fast->slow, same) | q<elephants already on the destination>",
    "pair": "source>destination spine; held-out group of the design rows (group column)",
    "trial": "design trial (experiments/damage_design.py), runs t<trial>-T and -C",
    "excluded": "why the row has no label (empty = analysed)",
    "n_p_design": "[design] elephants other than the moved one on the source",
    "n_q_design": "[design] elephants on the destination (bystanders)",
    "cap": "[design] iperf3 bitrate cap of the moved flow (empty = none)",
    "mice": "[design] 1 = Poisson mice from h2..h4", "flap": "[design] 1 = the previous move came from the destination",
    "ctl_round_lag_s": "[ctl] age of the monitor round the features come from (s before the move)",
    "ctl_path_ok": "1 if the monitor saw the moved flow on its source spine in that round",
    "t_move_T_s": "move arm: barrier reply of the move, s since t0 (window start)",
    "t_move_C_s": "no-move arm: barrier reply of the sham, s since t0 (window start)",
    "max_start_lag_s": "largest delay of an elephant's first packet at its receiver behind its scheduled start, either arm",
}
for w in WINDOWS:
    DESIGN_DICTIONARY["left_behind_deficit_W%g" % w] = "[label] bytes, sum over elephants left on the source"
    DESIGN_DICTIONARY["all_total_deficit_W%g" % w] = "[label] bytes, sum over all four elephants"
DESIGN_DICTIONARY["dest_total_deficit_pre"] = "bytes in the 2 s before the move, C - T (arms differ by construction)"
for w in SS_W:
    for k in ("retrans", "reord_seen", "dsack_dups"):
        for arm in ("T", "C"):
            DESIGN_DICTIONARY["ss_%s_W%g_%s" % (k, w, arm)] = (
                "[label] moved flow's own socket (ss): %s over %g s after the move, arm %s" % (k, w, arm))


def _design_runs(ddir):
    """{trial: {arm: (run dir, schedule)}} of a design campaign."""
    out = {}
    for rd in sorted(glob.glob(os.path.join(ddir, "runs", "*"))):
        sp = os.path.join(rd, "schedule.json")
        if not os.path.exists(sp):
            continue
        s = json.load(open(sp))
        des = s.get("design")
        if des:
            out.setdefault(des["trial"], {})[des["arm"]] = (rd, s)
    return out


def _main_move(meta):
    """Reply of the last batch's first flow-mod (the move or sham under test)."""
    mv = meta.get("moves") or []
    if not mv:
        return None, None
    b = max(m.get("batch", 0) for m in mv)
    return b, [m for m in mv if m.get("batch", 0) == b][0]["reply"]


def round_features(rd, meta, sched, des, t_sent):
    """The cost-aware controller's feature record for the move, rebuilt from the
    monitor's last round at or before the move's flow-mod, with the definitions
    of controllers/hedera.py _decide: rate in the last 1 s interval, U = leaf
    uplink tx rate, elephants = flows of the ingress leaf at >= 1 Mbit/s with a
    known path. The flow's age counts from its scheduled start (the controller
    would see its SYN then; a pinned flow's SYN skips the SYN copy)."""
    e1 = {e["id"]: e for e in sched["elephants"]}[des["moved"][0]]
    key = (e1["src_ip"], e1["dst_ip"], int(e1["sport"]), int(e1["dport"]))
    last = None
    with _open(os.path.join(rd, "policy_log.jsonl")) as fh:
        for line in fh:
            r = json.loads(line)
            if r.get("kind") == "round" and r["t"] <= t_sent:
                last = r
    if last is None:
        return None
    hpl = meta["hosts_per_leaf"]
    left = {ip for n, ip in sched["hosts"].items() if int(n[1:]) <= hpl}
    U = {int(k): float(v) for k, v in last.get("U", {}).get("1", {}).items()}
    flows = last.get("flows", [])

    def k5(f):
        g = f["flow"]
        return (g["src"], g["dst"], int(g["sport"]), int(g["dport"]))
    me = [f for f in flows if k5(f) == key]
    rate = float(me[0]["rate"]) if me else 0.0
    p, q = des["p"], des["q"]

    def on(x):
        return sum(1 for f in flows if f["flow"]["src"] in left and f.get("path") == x and
                   float(f["rate"]) >= ELEPHANT_MBPS)
    t_rel = last["t"] - meta["t0"]
    feat = dict(rate_mbps=rate, U_p=U.get(p, np.nan), U_q=U.get(q, np.nan),
                elephants_on_p=on(p), elephants_on_q=on(q), age_s=t_rel - e1["start"],
                bytes_so_far=float(me[0]["bytes"]) if me else np.nan,
                n_prev_moves=des["n_prev"],
                since_last_move_s=(t_rel - des["t_prev"]) if des["n_prev"] else None,
                delay_ms_p_config=meta["spine_delay_ms"][p - 1],
                delay_ms_q_config=meta["spine_delay_ms"][q - 1])
    feat["gain_mbps"] = feat["U_p"] - (feat["U_q"] + rate)
    return dict(feat=feat, round=last["round"], lag=(t_sent - meta["t0"]) - t_rel,
                path=me[0].get("path") if me else None)


def _capture_spines(rd):
    fl = pd.read_csv(os.path.join(rd, "run_flows.csv"))
    out = {}
    for _, r in fl[fl.kind == "elephant"].iterrows():
        s = str(r.bytes_by_spine)
        out[r["id"]] = sorted(int(x.split(":")[0][1:]) for x in s.split(";") if x and x != "nan")
    return out


def design_pair(T, C):
    """(row, reason): one deliberate reroute from its two arms; reason is empty
    when the pair is analysed, else the pre-registered exclusion that hit."""
    from analysis.chunk4 import SS_KEYS, ss_delta, ss_socket, start_lag
    (rdT, sT), (rdC, sC) = T, C
    des = sT["design"]
    fid = des["moved"][0]
    p, q = des["p"], des["q"]
    pre, post = des["pre"], des["post"]
    elephants = sorted(e["id"] for e in sT["elephants"])
    src_of = {e["id"]: e for e in sT["elephants"]}
    bystanders = sorted(f for f in elephants if f != fid and post[f] == q)
    left_behind = sorted(f for f in elephants if f != fid and post[f] == p)
    row = dict(source="design", trial=des["trial"], live_run="t%03d" % des["trial"],
               controller="deliberate", seed=sT["seed"], flow=fid, from_spine=p, to_spine=q,
               n_moved_in_event=1, n_bystanders=len(bystanders), n_p_design=des["n_p"],
               n_q_design=des["n_q"], cap=des["cap"] or "", mice=int(bool(des["mice"])),
               flap=int(bool(des["n_prev"]) and des["r"] == q), pair="s%d>s%d" % (p, q))
    mT, mC = (json.load(open(os.path.join(d, "meta.json"))) for d in (rdT, rdC))
    row["delay_gap_design_ms"] = mT["spine_delay_ms"][q - 1] - mT["spine_delay_ms"][p - 1]
    gap = row["delay_gap_design_ms"]
    row["scenario"] = "%s|q%d" % ("slow->fast" if gap < 0 else "fast->slow" if gap > 0 else "same",
                                  des["n_q"])
    row["group"] = row["pair"]
    if mT.get("errors") or mC.get("errors"):
        return row, "run error: %s" % " | ".join((mT.get("errors") or []) + (mC.get("errors") or []))
    bT, repT = _main_move(mT)
    bC, repC = _main_move(mC)
    if repT is None or repC is None or not repT.get("ok") or not repC.get("ok"):
        return row, "no ok move record"
    tmT, tmC = repT["t_barrier"] - mT["t0"], repC["t_barrier"] - mC["t0"]
    row.update(t_event_s=tmT, t_move_T_s=tmT, t_move_C_s=tmC)
    # path check against the uplink captures
    start = (des["r"] if des["n_prev"] else p)
    want = {"T": {f: sorted({start, p, q}) if f == fid else [pre[f]] for f in elephants},
            "C": {f: [post[f]] for f in elephants}}
    for arm, rd in (("T", rdT), ("C", rdC)):
        got = _capture_spines(rd)
        bad = ["%s on %s, expected %s" % (f, got.get(f), want[arm][f]) for f in elephants
               if got.get(f) != want[arm][f]]
        if bad:
            return row, "captures, %s: %s" % (arm, "; ".join(bad))
    # late starts (lost SYNs)
    lags = {arm: {f: start_lag(rd, f, s) for f in elephants}
            for arm, rd, s in (("T", rdT, sT), ("C", rdC, sC))}
    row["max_start_lag_s"] = max(v for a in lags.values() for v in a.values())
    late = ["%s %s first packet %.2f s" % (arm, f, src_of[f]["start"] + lags[arm][f])
            for arm, tm in (("T", tmT), ("C", tmC)) for f in elephants
            if not src_of[f]["start"] + lags[arm][f] <= tm - LATE_START_S]
    if late:
        return row, "late start: %s" % "; ".join(late)
    # features, from the move arm's monitor
    rf = round_features(rdT, mT, sT, des, repT["t_sent"])
    if rf is None:
        return row, "no monitor round before the move"
    row.update(rf["feat"])
    row.update(derive(rf["feat"]))
    row["round"] = rf["round"]
    row["ctl_round_lag_s"] = rf["lag"]
    row["ctl_path_ok"] = int(rf["path"] == p)
    row.update(host_features(rdT, sT, mT, fid, tmT, bystanders))
    # labels
    g = {}
    for arm, rd, tm in (("T", rdT, tmT), ("C", rdC, tmC)):
        g[arm] = {}
        for f in elephants:
            pts = goodput_points(rd, f, tm - 2.0, windows=(2.0,) + tuple(2.0 + w for w in WINDOWS))
            if pts is None:
                return row, "%s: no receiver trace of %s" % (arm, f)
            g[arm][f] = {w: pts[2.0 + w] - pts[2.0] for w in WINDOWS}
            g[arm][f]["pre"] = pts[2.0]

    def deficit(fids, w):
        return sum(g["C"][f][w] - g["T"][f][w] for f in fids)
    for w in WINDOWS:
        row["moved_deficit_W%g" % w] = deficit([fid], w)
        row["bystander_deficit_W%g" % w] = deficit(bystanders, w)
        row["dest_total_deficit_W%g" % w] = deficit([fid] + bystanders, w)
        row["left_behind_deficit_W%g" % w] = deficit(left_behind, w)
        row["all_total_deficit_W%g" % w] = deficit(elephants, w)
        row["moved_G_T_W%g" % w] = g["T"][fid][w] * 8 / w / 1e6
        row["moved_G_C_W%g" % w] = g["C"][fid][w] * 8 / w / 1e6
    row["dest_total_deficit_pre"] = deficit([fid] + bystanders, "pre")
    # the moved flow's own socket, and its sender's nstat (h1 carries only it)
    e = src_of[fid]
    for arm, rd, tm, m in (("T", rdT, tmT, mT), ("C", rdC, tmC, mC)):
        smp = ss_socket(rd, e["src"], e["sport"], m["t0"])
        for w in SS_W:
            d = ss_delta(smp, tm, tm + w) if smp else {k: np.nan for k, _ in SS_KEYS}
            for k, _ in SS_KEYS:
                row["ss_%s_W%g_%s" % (k, w, arm)] = d[k]
    cnt = {arm: json.load(open(os.path.join(rd, "counters.json"))) for arm, rd in (("T", rdT), ("C", rdC))}
    bb = {"T": bT, "C": bC}
    for w in NSTAT_W:
        for k in SND_COUNTERS:
            dd = {}
            for arm in ("T", "C"):
                c, b = cnt[arm], bb[arm]
                dd[arm] = c["m%d_%+g" % (b, w)][e["src"]].get(k, 0) - c["m%d_pre" % b][e["src"]].get(k, 0)
            row["d%g_%s" % (w, k)] = dd["T"] - dd["C"]
    return row, ""


def build_design(ddir):
    rows = []
    for k, arms in sorted(_design_runs(ddir).items()):
        if "T" not in arms or "C" not in arms:
            rows.append(dict(source="design", trial=k, live_run="t%03d" % k,
                             excluded="missing arm (%s)" % ",".join(sorted(arms)), replay_ok=0))
            continue
        row, why = design_pair(arms["T"], arms["C"])
        row["excluded"] = why
        row["replay_ok"] = int(not why)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- outputs
CORR_TARGETS = ["dest_total_deficit_W10", "dest_total_deficit_W2", "moved_deficit_W10",
                "bystander_deficit_W10"]
HOST_FEATURES = ["cwnd_before", "unacked_before", "cwnd_asymmetry"]


def correlations(df, targets=None):
    """Spearman r of every controller, derived and host feature against the
    labels, over the rows with a label."""
    feats = CONTROLLER_FEATURES + DERIVED + HOST_FEATURES
    tgt = targets or [t % 10 for t in TARGETS]
    d = df[df.replay_ok == 1]
    out = []
    for f in feats:
        x = pd.to_numeric(d[f], errors="coerce") if f in d else pd.Series(np.nan, index=d.index)
        for t in tgt:
            y = pd.to_numeric(d[t], errors="coerce")
            ok = x.notna() & y.notna() & np.isfinite(x)
            r = x[ok].corr(y[ok], method="spearman") if ok.sum() >= 3 else np.nan
            out.append(dict(feature=f, target=t, n=int(ok.sum()), spearman=r))
    return pd.DataFrame(out)


def write_dictionary(path, df):
    with open(path, "w") as fh:
        fh.write("# Damage dataset: data dictionary\n\nRows: %d (%d with a label: "
                 "replay_ok = 1). One row per moved elephant and event: a deliberate reroute "
                 "of the Chunk 5 design (source = design) or a controller's own move in Chunk 4, "
                 "replayed (source = chunk4). [ctl] = controller-visible at decision time "
                 "(model input); [derived] = computed from [ctl] columns (controllers/cost.derive); "
                 "[host] = sender-side ss, analysis only; [design] = the trial's factors; [label] "
                 "= measured from the matched move / no-move pair (C minus T, positive = the move "
                 "cost goodput).\n\n| column | meaning |\n|---|---|\n" %
                 (len(df), int(df.replay_ok.sum())))
        for k, v in list(DICTIONARY.items()) + list(DESIGN_DICTIONARY.items()):
            fh.write("| %s | %s |\n" % (k, v))


def _mean_ci(x):
    from analysis.gate import mean_ci
    m, h = mean_ci(pd.to_numeric(x, errors="coerce"))
    return "n/a" if np.isnan(m) else ("%.3f ± %.3f" % (m, h) if not np.isnan(h) else "%.3f" % m)


def summary(df, corr, out_md, sources):
    d = df[df.replay_ok == 1].copy()
    L = ["# Damage dataset (Chunk 5)", "",
         "Generated by `analysis/damage_dataset.py` from %s. Labels: C minus T, bytes in "
         "dataset.csv, Mbit/s over the window here (positive = the move cost goodput). "
         "Mean ± 95 %% CI (t, n - 1 df)." % ", ".join(sources), "",
         "Rows: %d, with a label: %d (design %d, chunk4 %d)." % (
             len(df), len(d), int((d.source == "design").sum()), int((d.source == "chunk4").sum())), ""]
    ex = df[(df.replay_ok != 1)]
    if len(ex):
        def who(r):
            if r.source == "design":
                return "design trial %s (%s)" % (r.trial, r.get("pair", ""))
            return "chunk4 %s round %s %s" % (r.live_run, r["round"], r.flow)
        L += ["Excluded (pre-registered rules):"] + ["- %s: %s" % (who(r), r.excluded)
                                                     for _, r in ex.iterrows()] + [""]
    for col in ("moved_deficit", "bystander_deficit", "dest_total_deficit", "all_total_deficit"):
        for w in WINDOWS:
            k = "%s_W%g" % (col, w)
            if k in d:
                d[k.replace("deficit", "mbps")] = pd.to_numeric(d[k], errors="coerce") * 8 / w / 1e6
    dd = d[d.source == "design"]
    if len(dd):
        L += ["## Design rows by direction and by elephants on the destination", "",
              "| group | rows | moved W2 | moved W10 | bystanders W10 | destination-path total W2 | "
              "destination-path total W10 |", "|---|---|---|---|---|---|---|"]
        groups = [("all", dd)] + [("direction " + v, dd[dd.scenario.str.startswith(v + "|")])
                                  for v in ("slow->fast", "same", "fast->slow")] + \
                 [("n_q = %d" % n, dd[dd.n_q_design == n]) for n in (0, 1, 2)] + \
                 [("previous move", dd[dd.n_prev_moves == 1]), ("flap", dd[dd.flap == 1]),
                  ("mice on", dd[dd.mice == 1])]
        for lab, g in groups:
            L.append("| %s | %d | %s | %s | %s | %s | %s |" % (
                lab, len(g), _mean_ci(g.moved_mbps_W2), _mean_ci(g.moved_mbps_W10),
                _mean_ci(g.bystander_mbps_W10), _mean_ci(g.dest_total_mbps_W2),
                _mean_ci(g.dest_total_mbps_W10)))
        L += ["", "Moved flow's own socket (ss) in the 2 s after the move, mean over design rows, "
              "move arm / no-move arm: reord_seen %s / %s, dsack_dups %s / %s, retransmissions "
              "%s / %s." % tuple(_mean_ci(dd["ss_%s_W2_%s" % (k, a)]) for k in
                                ("reord_seen", "dsack_dups", "retrans") for a in ("T", "C")), ""]
    L += ["## Spearman correlation of features with the labels (design rows)", "",
          "| feature | " + " | ".join(CORR_TARGETS) + " |", "|---|" + "---|" * len(CORR_TARGETS)]
    piv = corr[corr.rows == "design"].pivot(index="feature", columns="target", values="spearman")
    nn = corr[corr.rows == "design"].pivot(index="feature", columns="target", values="n")
    for f in CONTROLLER_FEATURES + DERIVED + HOST_FEATURES:
        if f in piv.index:
            L.append("| %s | %s |" % (f, " | ".join(
                "n/a" if np.isnan(piv.loc[f, t]) else "%.2f (n=%d)" % (piv.loc[f, t], nn.loc[f, t])
                for t in CORR_TARGETS)))
    open(out_md, "w").write("\n".join(L) + "\n")
    return L


def figures(df, corr, path_heat, path_scatter):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    piv = corr[corr.rows == "design"].pivot(index="feature", columns="target",
                                             values="spearman").reindex(
        [f for f in CONTROLLER_FEATURES + DERIVED + HOST_FEATURES])[CORR_TARGETS]
    fig, ax = plt.subplots(figsize=(7, 0.36 * len(piv) + 1.8))
    im = ax.imshow(piv.to_numpy(dtype=float), vmin=-1, vmax=1, cmap="RdBu_r", aspect="auto")
    ax.set_yticks(range(len(piv)), piv.index, fontsize=8)
    ax.set_xticks(range(len(piv.columns)), [c.replace("_deficit", "") for c in piv.columns],
                  rotation=25, ha="right", fontsize=8)
    for i in range(piv.shape[0]):
        for j in range(piv.shape[1]):
            v = piv.iat[i, j]
            if np.isfinite(v):
                ax.text(j, i, "%.2f" % v, ha="center", va="center", fontsize=7,
                        color="white" if abs(v) > 0.5 else "#1f1f1e")
    fig.colorbar(im, ax=ax, label="Spearman r (design rows)")
    ax.set_title("Chunk 5: features vs damage", fontsize=9)
    fig.tight_layout()
    fig.savefig(path_heat, dpi=150)
    plt.close(fig)
    d = df[(df.replay_ok == 1) & (df.source == "design")].copy()
    y = pd.to_numeric(d.dest_total_deficit_W10, errors="coerce") * 8 / 10 / 1e6
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.6))
    colors = {0: "#2a78d6", 1: "#e08a2c", 2: "#c0392b"}
    for ax, (x, lab) in zip(axes, (("delay_gap_ms", "delay gap, destination minus source (ms)"),
                                   ("rate_mbps", "moved flow rate before the move (Mbit/s)"),
                                   ("U_q", "destination load before the move (Mbit/s)"))):
        xv = pd.to_numeric(d[x], errors="coerce")
        jit = np.random.default_rng(0).uniform(-0.3, 0.3, len(d)) if x == "delay_gap_ms" else 0
        for n in (0, 1, 2):
            m = (d.n_q_design == n).to_numpy()
            ax.scatter(xv[m] + (jit[m] if np.ndim(jit) else 0), y[m], s=16, alpha=0.75, lw=0,
                       color=colors[n], label="%d elephant(s) on the destination" % n)
        ax.axhline(0, color="#5f5e58", lw=0.8)
        ax.set_xlabel(lab, fontsize=8)
        ax.tick_params(labelsize=8)
    axes[0].set_ylabel("destination-path deficit, W10 (Mbit/s)\n(positive = the move cost goodput)",
                       fontsize=8)
    axes[0].legend(fontsize=7, frameon=False)
    fig.suptitle("Chunk 5: damage of deliberate reroutes, one point per trial", fontsize=9)
    fig.tight_layout()
    fig.savefig(path_scatter, dpi=150)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--design", action="append", default=[],
                    help="a Chunk 5 design campaign (experiments/damage_design.py); repeatable")
    ap.add_argument("--live", help="a controller campaign (Chunk 4), with --replay")
    ap.add_argument("--replay", help="its replay campaign")
    ap.add_argument("--out", required=True)
    ap.add_argument("--figures", default=None, help="directory for the two figures")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    parts, sources = [], []
    for d in a.design:
        parts.append(build_design(d))
        sources.append(os.path.relpath(d, REPO))
    if a.live and a.replay:
        c4 = build(a.live, a.replay)
        c4["source"] = "chunk4"
        c4["scenario"] = "ecmp_mixed"
        c4["group"] = "chunk4"
        if "excluded" not in c4:
            c4["excluded"] = np.nan
        c4["excluded"] = np.where(c4.replay_ok != 1, "no complete replay pair",
                                  c4["excluded"].fillna(""))
        c4.loc[c4.excluded != "", "replay_ok"] = 0
        parts.append(c4)
        sources.append("%s + %s" % (os.path.relpath(a.live, REPO), os.path.relpath(a.replay, REPO)))
    df = pd.concat(parts, ignore_index=True, sort=False)
    cols = ["source", "scenario", "group", "pair", "trial"] + list(DICTIONARY) + \
        [k for k in DESIGN_DICTIONARY if k not in ("source", "scenario", "pair", "trial")]
    for c in cols:
        if c not in df.columns:
            df[c] = np.nan
    df = df[cols + [c for c in df.columns if c not in cols]]
    df.to_csv(os.path.join(a.out, "dataset.csv"), index=False, float_format="%.6g")
    write_dictionary(os.path.join(a.out, "data_dictionary.md"), df)
    corr = pd.concat([correlations(df[df.source == "design"], CORR_TARGETS).assign(rows="design"),
                      correlations(df, CORR_TARGETS).assign(rows="all")], ignore_index=True)
    corr.to_csv(os.path.join(a.out, "correlations.csv"), index=False, float_format="%.4g")
    L = summary(df, corr, os.path.join(a.out, "summary.md"), sources)
    if a.figures and (df.source == "design").any():
        os.makedirs(a.figures, exist_ok=True)
        figures(df, corr, os.path.join(a.figures, "damage_correlations.png"),
                os.path.join(a.figures, "damage_vs_features.png"))
    print("\n".join(L[:20]))
    print("rows: %d, with a label: %d -> %s" % (len(df), int(df.replay_ok.sum()), a.out))


if __name__ == "__main__":
    main()
