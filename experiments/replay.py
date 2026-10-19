#!/usr/bin/env python3
"""Matched move / no-move pairs for the reroutes a controller made (Chunk 4).

The damage of a reroute is measured against its matched no-move control
(CHUNKS.md, amendment after Chunk 3): the same traffic with the moved flow
already on its destination path, and no move. A controller's own moves are not
scheduled in advance, so each one is re-run as a scripted pair. An *event* is
one policy round of a live run in which the controller moved at least one
elephant. For every event, two runs with the live run's own seed (same
elephants, same mice, same source ports, so the same ECMP paths within one
boot) and the plain ECMP controller:

  T (move)     every elephant pinned from the start to the spine it was on just
               before the event; at the event's time (the round's first
               flow-mod, relative to t0) the same flows are moved to the same
               spines, back to back as in the round (one batch).
  C (no move)  every elephant pinned from the start to the spine it was on just
               after the event; at the same time a sham flow-mod
               (MODIFY_STRICT to the spine it is already on) for every flow
               that T moves.

Neither arm has any other move, so each event is measured from the same state
in both arms and apart from the controller's other rounds. ACKs and mice
follow ECMP in both arms, as in the live run. Every elephant is tagged (host
captures on both NICs) and counters are snapshotted around the move, as in
the Chunk 3 loaded-move runs. analysis/stage2.py turns the pairs into the
three damage terms. Moves of flows that are not iperf3 elephants (mice that
crossed the elephant threshold) are counted in the live metrics but not
replayed; the spec lists them.

  python3 experiments/replay.py --live results/compare/<name> \\
      --controllers hedera,cooldown --out results/damage/<name>/jobs.json
  python3 experiments/runner.py replay --spec results/damage/<name>/jobs.json \\
      --out results/damage/<name>
"""

import argparse
import copy
import csv
import json
import os
import random
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from experiments.moves import policy_moves  # noqa: E402

SNAPSHOTS = [-2.0, 0.0, 2.0, 5.0, 10.0]
REPLAY_CONTROLLER = "ecmp"


def capture_spines(rd):
    """{elephant id: [spines whose uplink capture carried its data]} (run_flows.csv)."""
    out = {}
    with open(os.path.join(rd, "run_flows.csv")) as f:
        for r in csv.DictReader(f):
            if r["kind"] == "elephant" and r.get("bytes_by_spine"):
                out[r["id"]] = sorted(int(x.split(":")[0][1:]) for x in
                                      r["bytes_by_spine"].split(";") if x)
    return out


def events_of_run(rd):
    """(events, skipped, problems) for one live run."""
    meta = json.load(open(os.path.join(rd, "meta.json")))
    sched = json.load(open(os.path.join(rd, "schedule.json")))
    moves = policy_moves(meta, sched)
    caps = capture_spines(rd)
    first = {}
    for m in moves:
        if m["flow_id"]:
            first.setdefault(m["flow_id"], m["from_spine"])
    place, problems = {}, []
    for e in sched["elephants"]:
        fid = e["id"]
        if fid in first:
            place[fid] = first[fid]
        elif len(caps.get(fid, [])) == 1:
            place[fid] = caps[fid][0]
        else:
            problems.append("%s: no single capture spine (%s)" % (fid, caps.get(fid)))
    # the controller's view of the paths must match the captures
    for fid in first:
        want = sorted({first[fid]} | {m["to_spine"] for m in moves if m["flow_id"] == fid})
        if caps.get(fid) != want:
            problems.append("%s: captures %s, controller %s" % (fid, caps.get(fid), want))
    rounds = {}
    for m in moves:
        rounds.setdefault(m["round"], []).append(m)
    events, skipped = [], []
    for rnd in sorted(rounds, key=lambda r: rounds[r][0]["t"]):
        ms = rounds[rnd]
        el = [m for m in ms if m["flow_id"]]
        skipped += [dict(round=rnd, t=m["t"], flow=list(m["key"])) for m in ms if not m["flow_id"]]
        if not el:
            continue
        pre = dict(place)
        for m in el:
            if pre.get(m["flow_id"]) != m["from_spine"]:
                problems.append("round %s: %s believed on s%s, placement says s%s" % (
                    rnd, m["flow_id"], m["from_spine"], pre.get(m["flow_id"])))
            place[m["flow_id"]] = m["to_spine"]
        post = dict(place)
        events.append(dict(round=rnd, t_sent=min(m["t_sent"] for m in el),
                           t_barrier=min(m["t"] for m in el),
                           moved=[m["flow_id"] for m in el],
                           moves=[dict(flow=m["flow_id"], from_spine=m["from_spine"],
                                       to_spine=m["to_spine"]) for m in el],
                           pre=pre, post=post))
    return meta, sched, events, skipped, problems


def arm_schedule(sched, ev, arm, live_id, controller):
    s = copy.deepcopy(sched)
    s.pop("sha256", None)
    at = round(ev["t_sent"], 3)
    place = ev["pre"] if arm == "T" else ev["post"]
    s["condition"] = arm
    s["pins"] = [dict(flow=fid, spine=place[fid]) for fid in sorted(place)]
    s["moves"] = [dict(at=at, flow=fid, spine=ev["post"][fid]) for fid in ev["moved"]]
    s["tagged"] = sorted(e["id"] for e in s["elephants"])
    s["snapshots"] = list(SNAPSHOTS)
    s["ack_spine"] = None
    s["replay"] = dict(live_run=live_id, live_controller=controller, round=ev["round"],
                       arm=arm, t_event=at, moved=ev["moved"], moves=ev["moves"],
                       pre=ev["pre"], post=ev["post"])
    return s


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--live", required=True, help="live campaign directory")
    ap.add_argument("--controllers", default="hedera,cooldown")
    ap.add_argument("--out", required=True, help="jobs JSON to write")
    ap.add_argument("--seed", type=int, default=4004, help="run order shuffle")
    ap.add_argument("--max-events", type=int, default=0,
                    help="per controller; 0 = all (a seeded sample otherwise)")
    a = ap.parse_args()
    ctrls = a.controllers.split(",")
    by_ctrl, skipped_all, problems_all = {c: [] for c in ctrls}, [], []
    for name in sorted(os.listdir(os.path.join(a.live, "runs"))):
        rd = os.path.join(a.live, "runs", name)
        if not os.path.exists(os.path.join(rd, "run_summary.json")):
            continue
        meta = json.load(open(os.path.join(rd, "meta.json")))
        if meta["controller"] not in ctrls:
            continue
        if meta.get("errors"):
            problems_all.append("%s: run has errors, not replayed" % name)
            continue
        meta, sched, events, skipped, problems = events_of_run(rd)
        skipped_all += [dict(s, run=name) for s in skipped]
        problems_all += ["%s: %s" % (name, p) for p in problems]
        if problems:
            continue                     # never replay a run whose paths we cannot trust
        for ev in events:
            by_ctrl[meta["controller"]].append((name, meta, sched, ev))
    rng = random.Random(a.seed)
    chosen = []
    for c in ctrls:
        evs = by_ctrl[c]
        if a.max_events and len(evs) > a.max_events:
            evs = sorted(rng.sample(evs, a.max_events), key=lambda x: (x[0], x[3]["round"]))
        chosen += evs
    rng.shuffle(chosen)
    jobs = []
    for name, meta, sched, ev in chosen:
        arms = ["T", "C"]
        rng.shuffle(arms)
        for arm in arms:
            jobs.append(dict(run_id="%s-e%d-%s" % (name, ev["round"], arm),
                             controller=REPLAY_CONTROLLER, condition=arm,
                             schedule=arm_schedule(sched, ev, arm, name, meta["controller"])))
    spec = dict(source=os.path.abspath(a.live), controllers=ctrls, seed=a.seed,
                max_events=a.max_events,
                n_events={c: len(by_ctrl[c]) for c in ctrls},
                n_events_replayed=len(chosen), skipped_moves=skipped_all,
                problems=problems_all, jobs=jobs)
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    json.dump(spec, open(a.out, "w"), indent=1)
    print("%d events (%s), %d replay runs -> %s" % (
        len(chosen), ", ".join("%s %d" % (c, len(by_ctrl[c])) for c in ctrls), len(jobs), a.out))
    if skipped_all:
        print("moves of non-elephant flows, not replayed: %d" % len(skipped_all))
    for p in problems_all:
        print("PROBLEM:", p)


if __name__ == "__main__":
    main()
