#!/usr/bin/env python3
"""Synthetic Chunk 4 campaign, so analysis/chunk4.py runs end to end without
Mininet (LOG.md, Chunk 4 part 2, "NOT done"). Nothing it writes is a result.

Writes, in the runner's layout and through the repo's own code wherever there
is one:
  <out>/compare/      ecmp, ecmp_mon, hedera, cooldown x seeds 1..N on the real
                      ecmp_mixed schedules (experiments/traffic.py): the raw
                      per-run files experiments/runner.py leaves (meta.json
                      with the controller's path_log, policy_log.jsonl.gz,
                      counters, iperf3 JSON, mice CSVs, uplink capture
                      summaries, group stats), then experiments/metrics.py
                      run + collect
  <out>/damage/       jobs.json from experiments/replay.py on that campaign
                      and one run per job (both arms of every event), again
                      through metrics.py; three pairs are broken on purpose
                      (BROKEN: an arm with a recorded error, an arm whose
                      captures show a flow on a wrong spine, a missing arm)
  <out>/planted.json  what the analysis must recover (tests/test_chunk4.py)
Both campaign directories hold a SYNTHETIC marker, so chunk4.py keeps its
figures next to the tables and labels every number as made up.

Model (made up; chosen only to exercise every code path): an elephant gets
ALONE_MBPS divided by the number of elephants on its uplink. The ECMP
placement of a seed cycles through PATTERNS (the same for every controller of
that seed, as within one boot), and SCRIPTS say which flows each rerouting
controller moves in which round: one move; two moves in one round after a
mouse that crossed the elephant threshold; a move onto an occupied uplink; a
flow moved in two consecutive rounds; two flaps. In the move arm of a replay
pair each moved flow loses D_MOVED Mbit/s and each bystander gains
G_BYSTANDER Mbit/s during the first TRANSIENT_S seconds after the barrier;
after the move nothing else differs from the no-move arm.

  .venv/bin/python analysis/chunk4_fixture.py --out scratch/chunk4-fixture
  .venv/bin/python analysis/chunk4.py --compare scratch/chunk4-fixture/compare \\
      --damage scratch/chunk4-fixture/damage
"""

import argparse
import csv
import gzip
import json
import math
import os
import random
import shutil
import subprocess
import sys

import numpy as np

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from experiments import metrics  # noqa: E402
from experiments.moves import FLAP_WINDOW_S  # noqa: E402
from experiments.traffic import make_schedule, resolve, schedule_sha256  # noqa: E402
from topologies.layout import DEFAULT_SPINE_DELAY_MS  # noqa: E402

CONTROLLERS = ["ecmp", "ecmp_mon", "hedera", "cooldown"]
POLICY = {"ecmp_mon": "none", "hedera": "greedy", "cooldown": "cooldown"}
ALONE_MBPS = 8.7            # an elephant alone on its uplink (Chunk 3: 8.70)
MICE_MBPS = 0.3             # what mice add to each uplink's port counter
D_MOVED = {"hedera": 1.5, "cooldown": 1.0}
G_BYSTANDER = 0.6
TRANSIENT_S = 2.0
GRID_S = 0.005              # ACK spacing in the receiver traces
MOVE_GRID_S = 0.125         # replay barriers land on this grid: with an integer
#                             t0 every window edge is an exact ACK time
T_ROUND = 0.55              # policy round r runs at t0 + r - 1 + T_ROUND
STALE_U_Q = 0.1             # the controller's (stale) load reading of an occupied
#                             destination, for the moves greedy makes by mistake
# ECMP uplink of E1..E4 for seed s: PATTERNS[(s - 1) % 5]
PATTERNS = [[1, 1, 2, 3], [2, 2, 2, 4], [3, 1, 3, 1], [4, 4, 1, 2], [1, 2, 3, 4]]
# per pattern: round -> moves (flow, from, to) in the order the controller sends
# them; "mouse" = a short flow that crossed the elephant threshold
SCRIPTS = {
    "hedera": [{3: [("E2", 1, 4)]},
               {3: [("mouse", 2, 1), ("E1", 2, 1), ("E2", 2, 3)]},
               {3: [("E3", 3, 2), ("E4", 1, 4)], 6: [("E3", 2, 3)], 7: [("E3", 3, 2)]},
               {3: [("E2", 4, 1)], 4: [("E2", 1, 3)]},
               {}],
    "cooldown": [{3: [("E2", 1, 4)]},
                 {3: [("E1", 2, 1), ("E2", 2, 3)]},
                 {3: [("E3", 3, 2), ("E4", 1, 4)]},
                 {3: [("E2", 4, 3)]},
                 {}],
}
# replay pairs broken on purpose: (live run, round) -> how
BROKEN = {("hedera-r01", 3): "error", ("hedera-r03", 7): "path", ("cooldown-r04", 3): "missing"}
SND_BASE = dict(TcpRetransSegs=300, TcpExtTCPFastRetrans=120, TcpExtTCPTimeouts=30)


def gz_json(path, obj):
    with gzip.open(path, "wt") as f:
        f.write(json.dumps(obj))


def write_json(path, obj, **kw):
    with open(path, "w") as f:       # one write: json.dump writes chunk by chunk
        f.write(json.dumps(obj, **kw))


def spine_at(timeline, t):
    """timeline: [(t_from, spine)] in time order; the spine at time t."""
    k = timeline[0][1]
    for tf, s in timeline:
        if tf <= t:
            k = s
    return k


def count_flaps(moves):
    """moves: [(t, flow, from, to)] in time order. A flap sends a flow back to the
    spine its previous move took it off, within FLAP_WINDOW_S (experiments/moves.py)."""
    last, n = {}, 0
    for t, f, frm, to in moves:
        p = last.get(f)
        if p and to == p[1] and t - p[0] <= FLAP_WINDOW_S:
            n += 1
        last[f] = (t, frm)
    return n


def rate_grid(elephants, timelines, D, adjust=None):
    """({fid: Mbit/s in each interval [i, i + 1) * GRID_S}, midpoints, {fid: spine
    per interval}); adjust(fid, t) adds a transient."""
    n = int(round(D / GRID_S))
    tm = (np.arange(n) + 0.5) * GRID_S
    on = {}
    for e in elephants:
        tl = timelines[e["id"]]
        idx = np.searchsorted([t for t, _ in tl], tm, side="right") - 1
        on[e["id"]] = np.array([s for _, s in tl])[np.maximum(idx, 0)]
    started = {e["id"]: tm >= e["start"] for e in elephants}
    out = {}
    for e in elephants:
        f = e["id"]
        share = np.zeros(n)
        for g in elephants:
            share += (on[g["id"]] == on[f]) & started[g["id"]]
        r = np.where(started[f], ALONE_MBPS / np.maximum(share, 1), 0.0)
        if adjust is not None:
            r = r + np.where(started[f], [adjust(f, t) for t in tm], 0.0)
        out[f] = r
    return out, tm, on


def mouse_spine(m):
    """Stand-in for the boot's dp_hash: fixed per 5-tuple, symmetric."""
    return 1 + ((m["sport"] * 2654435761) >> 16) % 4


def write_run(rd, sched, meta, timelines, rates, tm, on, counters, rng, noise=False,
              mouse_moves=None, extra_spine=None):
    """Raw files of one run as runner.py and its postprocess leave them, then
    experiments/metrics.py run. extra_spine = {fid: spine} puts some of that
    elephant's data on a spine it was never pinned to."""
    D, t0 = sched["duration"], meta["t0"]
    write_json(os.path.join(rd, "schedule.json"), sched, indent=1)
    write_json(os.path.join(rd, "meta.json"), meta, indent=1, sort_keys=True)
    write_json(os.path.join(rd, "counters.json"), counters, sort_keys=True)
    flows, bins = [], {}             # spine_flows.csv rows; (spine, bin) -> wire bytes up
    byte_spine = {k: 0.0 for k in range(1, 5)}
    for e in sched["elephants"]:
        f = e["id"]
        b = rates[f] * GRID_S * 1e6 / 8
        per = {k: float(b[on[f] == k].sum()) for k in range(1, 5)}
        if extra_spine and f in extra_spine:
            per[extra_spine[f]] += 1448.0 * 20
        for k, by in per.items():
            if by <= 0:
                continue
            sel = (on[f] == k) & (b > 0)
            t_on = tm[sel] if sel.any() else np.array([e["start"], e["start"]])
            pk = int(math.ceil(by / 1448))
            flows.append([k, "up", e["src_ip"], e["dst_ip"], e["sport"], e["dport"], pk + 2, pk,
                          int(by), int(by) + 66 * (pk + 2), t_on.min(), t_on.max(), 1])
            byte_spine[k] += by
        pk = int(sum(per.values()) / 1448 / 2)             # ACKs stay on the ECMP spine
        flows.append([timelines[f][0][1], "down", e["dst_ip"], e["src_ip"], e["dport"],
                      e["sport"], pk + 2, 0, 0, 66 * (pk + 2), e["start"], D, 1])
        for i, k in enumerate(on[f]):
            key = (int(k), int(tm[i] // 0.1))
            bins[key] = bins.get(key, 0.0) + b[i] * 1.04
    by_host = {}
    for m in sched["mice"]:
        k = mouse_spine(m)
        n_el = sum(1 for e in sched["elephants"]
                   if spine_at(timelines[e["id"]], m["start"]) == k and m["start"] >= e["start"])
        syn_loss = rng.random() < 0.023
        ok = rng.random() >= 0.001
        connect = 0.002 * DEFAULT_SPINE_DELAY_MS[k - 1] + 0.004 + (1.0 if syn_loss else 0.0)
        fct = connect + 0.006 + m["size"] * 8 / (9e6 / (n_el + 1))
        t_sched = t0 + m["start"]
        by_host.setdefault(m["src"], []).append(dict(
            id=m["id"], t_sched="%.6f" % t_sched, t_start="%.6f" % (t_sched + 0.0004),
            fct_s=("%.6f" % fct) if ok else "", connect_s="%.6f" % connect, size=m["size"],
            ok=int(ok), error="" if ok else "timeout"))
        segs = (mouse_moves or {}).get(m["id"], [(m["start"], k)])
        for j, (ts, kk) in enumerate(segs):
            part = m["size"] / len(segs)
            pk = int(math.ceil(part / 1448))
            flows.append([kk, "up", m["src_ip"], m["dst_ip"], m["sport"], m["dport"], pk + 2, pk,
                          int(part), int(part) + 66 * (pk + 2), ts, ts + fct, int(j == 0)])
        flows.append([k, "down", m["dst_ip"], m["src_ip"], m["dport"], m["sport"], 4, 1, 1, 265,
                      m["start"], m["start"] + fct, 1])
    with open(os.path.join(rd, "spine_flows.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["spine", "dir", "src", "dst", "sport", "dport", "pkts", "data_pkts",
                    "payload_bytes", "wire_bytes", "t_first", "t_last", "syns"])
        for r in sorted(flows, key=lambda r: tuple(str(x) for x in r[:6])):
            w.writerow(r[:10] + ["%.6f" % r[10], "%.6f" % r[11], r[12]])
    cap = 10e6 * 0.1 / 8
    with open(os.path.join(rd, "spine_bins.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["spine", "dir", "t_bin", "wire_bytes"])
        for k in range(1, 5):
            for i in range(int(round((D + 1) / 0.1))):
                up = min(bins.get((k, i), 0.0) + MICE_MBPS * 0.1 * 1e6 / 8, cap)
                w.writerow([k, "down", "%.1f" % (i * 0.1), int(0.04 * up + 300)])
                w.writerow([k, "up", "%.1f" % (i * 0.1), int(up)])
    for host, rows in by_host.items():
        with open(os.path.join(rd, "mice_%s.csv" % host), "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=["id", "t_sched", "t_start", "fct_s", "connect_s",
                                               "size", "ok", "error"])
            w.writeheader()
            for r in sorted(rows, key=lambda r: r["id"]):
                w.writerow(r)
    for e in sched["elephants"]:
        f = e["id"]
        b = rates[f] * GRID_S * 1e6 / 8 * ((1.0 + rng.gauss(0, 0.015)) if noise else 1.0)
        total, secs = int(b.sum()), D - e["start"] + 0.01
        gz_json(os.path.join(rd, "iperf_client_%s.json.gz" % f), dict(end=dict(
            sum_received=dict(start=0, end=secs, seconds=secs, bytes=total,
                              bits_per_second=total * 8 / secs, sender=True),
            sum_sent=dict(start=0, end=secs, seconds=secs, bytes=total + 40000,
                          retransmits=40 + 10 * len(timelines[f]), sender=True))))
        n_iv = int(round((D - e["start"]) / 0.1))
        k = np.floor((tm - e["start"]) / 0.1).astype(int)
        ok = (k >= 0) & (k < n_iv)
        per_iv = np.bincount(k[ok], weights=b[ok], minlength=n_iv)
        iv = [dict(sum=dict(start=round(i * 0.1, 6), end=round((i + 1) * 0.1, 6), seconds=0.1,
                            bytes=int(per_iv[i]), omitted=False, sender=False))
              for i in range(n_iv)]
        gz_json(os.path.join(rd, "iperf_server_%s.json.gz" % f),
                dict(start={}, intervals=iv, end={}))
    tot = sum(byte_spine.values()) or 1.0
    with open(os.path.join(rd, "ovs_stats.txt"), "w") as fh:
        for sw, scale in (("l1", 1.0), ("l2", 0.05)):
            fh.write("== %s dump-group-stats\nOFPST_GROUP reply (OF1.3) (xid=0x2):\n" % sw)
            fh.write(" group_id=1,duration=%.3fs,ref_count=4,packet_count=%d,byte_count=%d,%s\n"
                     % (D + 10, tot * scale / 1448, tot * scale, ",".join(
                         "bucket%d:packet_count=%d,byte_count=%d" % (
                             k - 1, byte_spine[k] * scale / 1448, byte_spine[k] * scale)
                         for k in range(1, 5))))
            fh.write("== %s dump-flows\n== %s dump-ports\n" % (sw, sw))
    open(os.path.join(rd, "run.log"), "w").write("synthetic run (analysis/chunk4_fixture.py)\n")
    metrics.run_metrics(rd)


def host_counters(sched, inc):
    """{host: TCP counters}: zero, plus SND_BASE on the senders, plus inc[host]."""
    out = {}
    for h in sched["hosts"]:
        c = {n: 0 for n in metrics.HOST_CTRS + metrics.RCV_CTRS}
        if int(h[1:]) <= 4:
            c.update(SND_BASE)
        for n, v in inc.get(h, {}).items():
            c[n] = c.get(n, 0) + v
        out[h] = c
    return out


def policy_record(r, t_rel, f, frm, to, eleph, timelines, moved_before, stale, policy):
    """A move's feature record as controllers/hedera.py _decide logs it."""
    n_on = {k: 0 for k in range(1, 5)}
    for g in eleph:
        n_on[spine_at(timelines[g["id"]], t_rel)] += 1
    load = {k: MICE_MBPS + (ALONE_MBPS if n_on[k] else 0.0) for k in range(1, 5)}
    rate = ALONE_MBPS / max(n_on[frm], 1) if f["id"] != "mouse" else 1.4
    u_q = STALE_U_Q if stale else load[to]
    prev = moved_before.get(f["id"], [])
    return dict(round=r, flow=dict(src=f["src_ip"], dst=f["dst_ip"], sport=f["sport"],
                                   dport=f["dport"]),
                leaf=1, from_spine=frm, to_spine=to, rate_mbps=rate,
                U_before={str(k): load[k] for k in range(1, 5)}, U_p=load[frm], U_q=u_q,
                gain_mbps=load[frm] - (u_q + rate),
                why="gain>0" if policy == "greedy" else "gain>margin",
                elephants_on_p=n_on[frm], elephants_on_q=n_on[to],
                age_s=t_rel - f["start"], bytes_so_far=int(rate * (t_rel - f["start"]) * 125000),
                n_prev_moves=len(prev), since_last_move_s=(t_rel - prev[-1]) if prev else None,
                path_src="pin" if prev else "spine_counters",
                delay_ms_p_config=DEFAULT_SPINE_DELAY_MS[frm - 1],
                delay_ms_q_config=DEFAULT_SPINE_DELAY_MS[to - 1])


def live_run(camp, ctrl, rep, mice_step, planted):
    """One run of the comparison campaign (seed = rep, as runner.py --seed 1)."""
    seed = rep
    run_id = "%s-r%02d" % (ctrl, rep)
    rd = os.path.join(camp, "runs", run_id)
    os.makedirs(rd)
    sched = make_schedule(resolve("ecmp_mixed"), seed)
    sched["mice"] = sched["mice"][::mice_step]
    sched["sha256"] = schedule_sha256(sched)
    D = sched["duration"]
    t0 = 1_800_000_000.0 + 1000.0 * (seed * 10 + CONTROLLERS.index(ctrl))
    pat = PATTERNS[(seed - 1) % len(PATTERNS)]
    eleph = sched["elephants"]
    by_id = {e["id"]: e for e in eleph}
    timelines = {e["id"]: [(0.0, pat[i])] for i, e in enumerate(eleph)}
    script = SCRIPTS[ctrl][(seed - 1) % len(PATTERNS)] if ctrl in SCRIPTS else {}
    rng = random.Random(seed * 101 + CONTROLLERS.index(ctrl))
    path_log, rounds, moved_before, mouse_moves, done = [], [], {}, {}, []
    for r in range(1, D):
        t_rel = r - 1 + T_ROUND
        recs = []
        for i, (fid, frm, to) in enumerate(script.get(r, [])):
            if fid == "mouse":            # the latest mouse that started before the round
                cands = [m for m in sched["mice"] if m["start"] < t_rel - 0.05 and
                         mouse_spine(m) != to]
                if not cands:
                    continue
                m = max(cands, key=lambda m: (mouse_spine(m) == frm, m["start"]))
                f, frm = dict(m, id="mouse"), mouse_spine(m)
            else:
                f = by_id[fid]
            stale = fid != "mouse" and any(spine_at(timelines[g["id"]], t_rel) == to
                                           for g in eleph if g["id"] != fid)
            rec = policy_record(r, t_rel, f, frm, to, eleph, timelines, moved_before, stale,
                                POLICY[ctrl])
            rec["t"] = t0 + t_rel
            t_sent = t0 + t_rel + 0.0015 + 0.003 * i
            t_bar = t_sent + 0.0012
            rec["reply"] = dict(ok=True, t_sent=t_sent, t_barrier=t_bar, leaf=1, spine=to)
            recs.append(rec)
            key = fid if fid != "mouse" else m["id"]
            path_log.append(dict(ok=True, t_sent=t_sent, t_barrier=t_bar, leaf=1, spine=to,
                                 cmd="move" if key in moved_before or key in mouse_moves
                                 else "pin", flow=rec["flow"], origin="policy", round=r,
                                 from_spine=frm))
            done.append((t_bar - t0, key, frm, to))
            if fid == "mouse":
                mouse_moves[key] = [(m["start"], frm), (t_bar - t0, to)]
            else:
                timelines[fid].append((t_bar - t0, to))
                moved_before.setdefault(fid, []).append(t_rel)
        if ctrl != "ecmp":
            n_on = {k: sum(1 for g in eleph if spine_at(timelines[g["id"]], t_rel) == k)
                    for k in range(1, 5)}
            rounds.append(dict(kind="round", round=r, t=t0 + t_rel, t_req=t0 + t_rel - 0.004,
                               collect_ms=3.5 + rng.random(), missing_replies=0,
                               U={"1": {str(k): MICE_MBPS + ALONE_MBPS * bool(n_on[k])
                                        for k in range(1, 5)},
                                  "2": {str(k): 0.2 for k in range(1, 5)}},
                               n_tracked=4 + rng.randrange(8),
                               n_packet_in=4 + sum(1 for m in sched["mice"]
                                                   if m["start"] < t_rel),
                               flows=[], moves=recs))
    if ctrl != "ecmp":
        with gzip.open(os.path.join(rd, "policy_log.jsonl.gz"), "wt") as fh:
            fh.write(json.dumps(dict(kind="config", app=ctrl, policy=POLICY[ctrl], poll_s=1.0,
                                     elephant_mbps=1.0,
                                     cooldown_s=5.0 if ctrl == "cooldown" else None,
                                     margin_mbps=1.0 if ctrl == "cooldown" else None,
                                     link_mbps=10.0, count_idle_s=5,
                                     spine_delay_ms_config=list(DEFAULT_SPINE_DELAY_MS)),
                                sort_keys=True) + "\n")
            for rec in rounds:
                fh.write(json.dumps(rec, sort_keys=True) + "\n")
    inc = {h: dict(SND_BASE) for h in sched["hosts"] if int(h[1:]) <= 4}
    for fid, ts in moved_before.items():
        h, n = by_id[fid]["src"], len(ts)
        for name, v in (("TcpRetransSegs", 25 * n), ("TcpExtTCPDSACKRecv", 2 * n),
                        ("TcpExtTCPSACKReorder", n), ("TcpExtTCPDSACKUndo", n)):
            inc[h][name] = inc[h].get(name, 0) + v
    counters = dict(start=dict(t=t0 - 1.0, **host_counters(sched, {})),
                    end=dict(t=t0 + D + 1.0, **host_counters(sched, inc)))
    meta = dict(run_id=run_id, controller=ctrl, controller_app=ctrl, scenario="ecmp_mixed",
                condition="", seed=seed, duration_s=D, queue_pkts=20, mbps=10.0,
                host_qdisc="netem", hosts_per_leaf=4, spine_delay_ms=list(DEFAULT_SPINE_DELAY_MS),
                uid=1000, userns=True, kernel="synthetic", boot_id="synthetic-fixture",
                t_wall_start=t0 - 8.0, t0=t0, errors=[], pins=[], moves=[],
                elephant_start={e["id"]: t0 + e["start"] for e in eleph},
                controller_events=path_log, t_clients_end=t0 + D + 1.0, t_wall_end=t0 + D + 9.0)
    rates, tm, on = rate_grid(eleph, timelines, D)
    write_run(rd, sched, meta, timelines, rates, tm, on, counters, rng, noise=True,
              mouse_moves=mouse_moves)
    s = json.load(open(os.path.join(rd, "run_summary.json")))
    n_el = sum(len(ts) for ts in moved_before.values())
    planted["runs"][run_id] = dict(
        controller=ctrl, seed=seed, eleph_goodput_mean=s["eleph_goodput_mean"],
        n_reroutes=len(done), n_reroutes_other=len(mouse_moves), n_flaps=count_flaps(done),
        n_reroute_rounds=len({p["round"] for p in path_log}),
        snd_reorder_events=3 * n_el, snd_TcpRetransSegs=4 * 300 + 25 * n_el)


def replay_runs(camp, damage, planted):
    """jobs.json from experiments/replay.py, then one synthetic run per job."""
    jobs_path = os.path.join(damage, "jobs.json")
    subprocess.run([sys.executable, os.path.join(REPO, "experiments", "replay.py"), "--live",
                    camp, "--controllers", "hedera,cooldown", "--out", jobs_path], check=True,
                   stdout=subprocess.DEVNULL)
    spec = json.load(open(jobs_path))
    planted["replay_problems"] = spec["problems"]
    planted["replay_skipped_moves"] = len(spec["skipped_moves"])
    for k, job in enumerate(sorted(spec["jobs"], key=lambda j: j["run_id"])):
        s = dict(job["schedule"])
        rp = s["replay"]
        arm, broken = rp["arm"], BROKEN.get((rp["live_run"], rp["round"]))
        if broken == "missing" and arm == "C":
            continue
        s["sha256"] = schedule_sha256(s)
        rd = os.path.join(damage, "runs", job["run_id"])
        os.makedirs(rd)
        t0 = 1_900_000_000.0 + 1000.0 * k
        D = s["duration"]
        tm = math.ceil((s["moves"][0]["at"] + 0.0015) / MOVE_GRID_S) * MOVE_GRID_S
        pre, post, moved = rp["pre"], rp["post"], rp["moved"]
        dest = {post[f] for f in moved}
        bystanders = [e["id"] for e in s["elephants"] if e["id"] not in moved and
                      post[e["id"]] in dest]
        if arm == "T":
            timelines = {f: [(0.0, pre[f])] + ([(tm, post[f])] if f in moved else [])
                         for f in pre}
            d_moved = D_MOVED[rp["live_controller"]]

            def adjust(f, t, d_moved=d_moved, bystanders=bystanders, moved=moved, tm=tm):
                if not tm <= t < tm + TRANSIENT_S:
                    return 0.0
                return -d_moved if f in moved else (G_BYSTANDER if f in bystanders else 0.0)
        else:
            timelines = {f: [(0.0, post[f])] for f in post}
            adjust = None
        rates, tm_grid, on = rate_grid(s["elephants"], timelines, D, adjust)
        meta = dict(run_id=job["run_id"], controller=job["controller"], controller_app="ecmp",
                    scenario=s["scenario"], condition=arm, seed=s["seed"], duration_s=D,
                    queue_pkts=20, mbps=10.0, host_qdisc="netem", hosts_per_leaf=4,
                    spine_delay_ms=list(DEFAULT_SPINE_DELAY_MS), uid=1000, userns=True,
                    kernel="synthetic", boot_id="synthetic-fixture", t_wall_start=t0 - 8.0,
                    t0=t0, errors=[], elephant_start={e["id"]: t0 + e["start"]
                                                      for e in s["elephants"]})
        eflow = {e["id"]: dict(src=e["src_ip"], dst=e["dst_ip"], sport=e["sport"],
                               dport=e["dport"]) for e in s["elephants"]}
        meta["pins"], meta["moves"], log = [], [], []
        for p in s["pins"]:
            rep = dict(ok=True, leaf=1, spine=p["spine"], t_sent=t0 - 2.0, t_barrier=t0 - 1.999)
            meta["pins"].append(dict(p, dir="data", reply=rep))
            log.append(dict(rep, cmd="pin", flow=eflow[p["flow"]], origin="runner"))
        for i, mv in enumerate(s["moves"]):
            t_sent = t0 + tm - 0.0012 + 0.003 * i
            rep = dict(ok=True, leaf=1, spine=mv["spine"], t_sent=t_sent,
                       t_barrier=t0 + tm + 0.003 * i)
            meta["moves"].append(dict(mv, batch=0, reply=dict(rep, t_recv=t_sent - 0.0001)))
            log.append(dict(rep, cmd="move", flow=eflow[mv["flow"]], origin="runner"))
        meta["controller_events"] = log
        src = {e["id"]: e["src"] for e in s["elephants"]}
        counters = {}
        for name, off in (("start", -1.0 - tm), ("m0_-2", -2.0), ("m0_pre", 0.0),
                          ("m0_post0", 0.004), ("m0_+2", 2.0), ("m0_+5", 5.0),
                          ("m0_+10", 10.0), ("end", D + 1.0 - tm)):
            inc = {}
            if arm == "T" and off >= 2.0:     # the move's reordering, inside W2
                inc = {src[f]: dict(TcpExtTCPDSACKRecv=2, TcpExtTCPSACKReorder=1,
                                    TcpRetransSegs=6) for f in moved}
            counters[name] = dict(t=t0 + tm + off, **host_counters(s, inc))
        if broken == "error" and arm == "C":   # the runner keeps no data of a failed run
            meta["errors"] = ["RuntimeError: synthetic failure (analysis/chunk4_fixture.py)"]
            write_json(os.path.join(rd, "schedule.json"), s, indent=1)
            write_json(os.path.join(rd, "meta.json"), meta, indent=1, sort_keys=True)
            write_json(os.path.join(rd, "counters.json"), counters, sort_keys=True)
            continue
        extra = {moved[0]: pre[moved[0]]} if broken == "path" and arm == "C" else None
        write_run(rd, s, meta, timelines, rates, tm_grid, on, counters, random.Random(k),
                  extra_spine=extra)
        for e in s["elephants"]:                 # receiver traces: cumulative ACK
            f = e["id"]
            ack = np.round(np.cumsum(rates[f] * GRID_S * 1e6 / 8)).astype(int)
            t_us = np.round((np.arange(len(ack)) + 1) * GRID_S * 1e6).astype(int)
            keep = rates[f] > 0
            with gzip.open(os.path.join(rd, "recv_%s_trace.csv.gz" % f), "wt",
                           newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["t_us", "dir", "seq", "ack", "len", "flags", "sack"])
                for tu, a in zip(t_us[keep], ack[keep]):
                    w.writerow([tu, 1, 1, a, 0, 16, ""])
        if arm != "T":
            continue
        frac = {w: min(w, TRANSIENT_S) / w for w in (2.0, 5.0, 10.0)}
        d_m = len(moved) * D_MOVED[rp["live_controller"]]
        g_b = len(bystanders) * G_BYSTANDER
        ev = dict(controller=rp["live_controller"], live_run=rp["live_run"], round=rp["round"],
                  broken=broken or "", moved=moved, bystanders=bystanders,
                  n_left_behind=sum(1 for f in pre if f not in moved and
                                    post[f] in {pre[g] for g in moved}),
                  t_move_s=tm, pre_goodput_T_mbps=ALONE_MBPS * len(set(pre.values())),
                  pre_goodput_C_mbps=ALONE_MBPS * len(set(post.values())),
                  events_W2_T=3 * len(moved))
        for w, x in frac.items():
            ev.update({"moved_mbps_W%g" % w: d_m * x, "bystanders_mbps_W%g" % w: -g_b * x,
                       "dest_total_mbps_W%g" % w: (d_m - g_b) * x,
                       "all_total_mbps_W%g" % w: (d_m - g_b) * x})
        planted["events"]["%s round %d" % (rp["live_run"], rp["round"])] = ev


def first_move_features(camp, planted):
    """Pre-move features of each event's first moved elephant from the live run's
    policy log: what chunk4.py must attach to the event row."""
    for ev in planted["events"].values():
        rd = os.path.join(camp, "runs", ev["live_run"])
        sched = json.load(open(os.path.join(rd, "schedule.json")))
        e = {x["id"]: x for x in sched["elephants"]}[ev["moved"][0]]
        want = dict(src=e["src_ip"], dst=e["dst_ip"], sport=e["sport"], dport=e["dport"])
        for line in gzip.open(os.path.join(rd, "policy_log.jsonl.gz"), "rt"):
            r = json.loads(line)
            if r.get("kind") == "round" and r["round"] == ev["round"]:
                m = [m for m in r["moves"] if m["flow"] == want][0]
                ev["features"] = {k: m[k] for k in ("rate_mbps", "U_p", "U_q", "gain_mbps",
                                                    "elephants_on_p", "elephants_on_q",
                                                    "n_prev_moves", "since_last_move_s")}
                ev["mouse_moved_first_in_round"] = r["moves"][0]["flow"]["dport"] == 8000


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True, help="directory to create (an older fixture "
                    "there is replaced; anything else is left alone)")
    ap.add_argument("--seeds", type=int, default=5, help="seeds 1..N; at least 4, the "
                    "patterns that carry the code paths (seed 5 has no collision)")
    ap.add_argument("--mice-step", type=int, default=1,
                    help="keep every n-th mouse of each schedule (faster tests)")
    a = ap.parse_args()
    if a.seeds < 4:
        sys.exit("--seeds must be at least 4")
    if os.path.exists(a.out):
        if not os.path.exists(os.path.join(a.out, "compare", "SYNTHETIC")):
            sys.exit("%s exists and is not a fixture; not touching it" % a.out)
        shutil.rmtree(a.out)
    camp, damage = os.path.join(a.out, "compare"), os.path.join(a.out, "damage")
    planted = dict(note="SYNTHETIC: made up by analysis/chunk4_fixture.py, not a result",
                   seeds=a.seeds, mice_step=a.mice_step, d_moved=D_MOVED,
                   g_bystander=G_BYSTANDER, transient_s=TRANSIENT_S, broken=[
                       dict(live_run=k[0], round=k[1], how=v) for k, v in BROKEN.items()],
                   runs={}, events={})
    for d in (camp, damage):
        os.makedirs(os.path.join(d, "runs"))
        open(os.path.join(d, "SYNTHETIC"), "w").write(
            "made up by analysis/chunk4_fixture.py; not a result\n")
    rng, order = random.Random(17), []
    for rep in range(1, a.seeds + 1):            # block-randomised, as runner.py campaign
        block = list(CONTROLLERS)
        rng.shuffle(block)
        order += [(rep, c) for c in block]
    for rep, c in order:
        live_run(camp, c, rep, a.mice_step, planted)
    json.dump(dict(controllers=CONTROLLERS, scenario="ecmp_mixed", runs=a.seeds, seed=1,
                   order=[[rep, "", c] for rep, c in order], synthetic=True),
              open(os.path.join(camp, "plan.json"), "w"), indent=1)
    metrics.collect(camp)
    replay_runs(camp, damage, planted)
    first_move_features(camp, planted)
    json.dump(planted, open(os.path.join(a.out, "planted.json"), "w"), indent=1)
    print("fixture in %s: %d live runs, %d replay events (%d broken on purpose)" % (
        a.out, len(planted["runs"]), len(planted["events"]),
        sum(1 for e in planted["events"].values() if e["broken"])))


if __name__ == "__main__":
    main()
