#!/usr/bin/env python3
"""Chunk 5: a seeded campaign of deliberate reroutes under varied conditions.

Every trial is one reroute of E1 (h1 -> h5) from spine p to spine q at t_move,
run twice with the same traffic seed (the matched pair of the Chunk 4 replays,
CHUNKS.md amendment):

  T (move)     E1 pinned to p from the start (or to r, then moved r -> p at
               t_move - delta when the trial has a previous move), moved p -> q
               at t_move
  C (no move)  E1 pinned to q from the start; a sham MODIFY_STRICT to q at
               every time T has a flow-mod

E2..E4 are pinned from the start to the same spines in both arms: n_p of them
on p, n_q on q, the rest on the two other spines. Both arms run the
monitor-only controller (controllers/ecmp_mon.py), so its policy_log.jsonl
records, every second, the loads and flow rates the cost-aware controller
would see; analysis/damage_dataset.py rebuilds E1's controller-visible
features from the T arm's last round before t_move.

Factors, per trial (the design is a pre-registered seeded draw, LOG.md):
  (p, q)       all 12 ordered spine pairs, REPS times each (delay gaps -9 .. +9 ms)
  n_p, n_q     elephants already on the source / destination, uniform over the
               8 pairs with n_p + n_q <= 3
  cap          E1's iperf3 bitrate: none (1/2), 6 Mbit/s (1/4), 3 Mbit/s (1/4)
  mice         Poisson mice from h2..h4 on (1/2) or off
  previous     none (3/5), or one earlier move r -> p, r uniform among the
               spines other than p (r = q is a flap), delta = 2, 5 or 10 s before
  t_move       uniform in [12, 14] s after t0 (E1 has run about 12 s)

  python3 experiments/damage_design.py --trials-per-pair 5 --seed 5 \\
      --out results/dataset/<name>/jobs.json
  python3 experiments/runner.py replay --spec results/dataset/<name>/jobs.json \\
      --out results/dataset/<name>
writes jobs.json (the runner's replay spec, run order shuffled) and design.csv
(one row per trial).
"""

import argparse
import csv
import itertools
import json
import os
import random
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from experiments.traffic import make_schedule, resolve  # noqa: E402

SPINES = (1, 2, 3, 4)
NPQ = [(a, b) for a in range(3) for b in range(3) if a + b <= 3]
CAPS = [(None, 2), ("6M", 1), ("3M", 1)]          # (iperf3 -b, weight)
DELTAS = (2.0, 5.0, 10.0)
SNAPSHOTS = [-2.0, 0.0, 2.0, 5.0, 10.0]
CONTROLLER = "ecmp_mon"
SEED_BASE = 5000
CAP_MODE = dict(mode="b")     # how E1's cap is applied: iperf3 -b (as run first) or fq
# Chunk 7a (damage regime): per-namespace sysctls and spine delays written into
# every schedule (the runner applies and logs them); empty = the runner's defaults
SETTING = dict(sysctl={}, spine_delay_ms=None)
T_MOVE = [12.0, 14.0]         # t_move range (Chunk 7b: --t-move-range 6,14 for flow ages)


def apply_setting(s):
    if SETTING["sysctl"]:
        s["sysctl"] = dict(SETTING["sysctl"])
    if SETTING["spine_delay_ms"]:
        s["spine_delay_ms"] = list(SETTING["spine_delay_ms"])


def draw_trial(rng, p, q):
    n_p, n_q = rng.choice(NPQ)
    cap = rng.choices([c for c, _ in CAPS], weights=[w for _, w in CAPS])[0]
    mice = rng.random() < 0.5
    prev = rng.random() < 0.4
    r = rng.choice([s for s in SPINES if s != p]) if prev else None
    delta = rng.choice(DELTAS) if prev else None
    t_move = round(rng.uniform(*T_MOVE), 3)
    others = [s for s in SPINES if s not in (p, q)]
    comp = ["E2", "E3", "E4"]
    rng.shuffle(comp)
    place = {}
    for i, fid in enumerate(comp):
        place[fid] = p if i < n_p else q if i < n_p + n_q else rng.choice(others)
    return dict(p=p, q=q, n_p=n_p, n_q=n_q, cap=cap, mice=mice, n_prev=int(prev), r=r,
                delta=delta, t_move=t_move, t_prev=round(t_move - delta, 3) if prev else None,
                competitors=place)


def arm_schedule(trial, k, seed, arm):
    s = make_schedule(resolve("damage_moves"), seed)
    apply_setting(s)
    if not trial["mice"]:
        s["mice"] = []
    # --placed-only: only E1 and the competitors the trial places (on p or q)
    s["elephants"] = [e for e in s["elephants"] if e["id"] == "E1" or e["id"] in trial["competitors"]]
    for e in s["elephants"]:
        if e["id"] == "E1" and trial["cap"]:
            e["pacing" if CAP_MODE["mode"] == "fq" else "bitrate"] = trial["cap"]
    p, q = trial["p"], trial["q"]
    start = (trial["r"] if trial["n_prev"] else p) if arm == "T" else q
    s["condition"] = arm
    s["pins"] = [dict(flow="E1", spine=start)] + \
        [dict(flow=f, spine=trial["competitors"][f]) for f in sorted(trial["competitors"])]
    times = ([trial["t_prev"]] if trial["n_prev"] else []) + [trial["t_move"]]
    targets = ([p] if trial["n_prev"] else []) + [q]
    s["moves"] = [dict(at=t, flow="E1", spine=(dst if arm == "T" else q))
                  for t, dst in zip(times, targets)]
    s["tagged"] = sorted(e["id"] for e in s["elephants"])
    s["snapshots"] = list(SNAPSHOTS)
    s["snapshots_last_only"] = True
    s["ack_spine"] = None
    pre = dict(trial["competitors"], E1=p)
    post = dict(trial["competitors"], E1=q)
    s["design"] = dict(trial=k, arm=arm, moved=["E1"], pre=pre, post=post,
                       cap_mode=CAP_MODE["mode"] if trial["cap"] else "",
                       **{x: trial[x] for x in ("p", "q", "n_p", "n_q", "cap", "mice", "n_prev",
                                                "r", "delta", "t_move", "t_prev")})
    return s


def grid_trial(rng, p, q, n_p, n_q, placed_only=False):
    """A fixed-factor trial (--grid): no cap, no mice, no previous move.
    placed_only (Chunk 7a): the elephants on neither p nor q are left out."""
    t = draw_trial(rng, p, q)
    others = [s for s in SPINES if s not in (p, q)]
    comp = ["E2", "E3", "E4"]
    rng.shuffle(comp)
    place = {f: p if i < n_p else q if i < n_p + n_q else rng.choice(others)
             for i, f in enumerate(comp)}
    if placed_only:
        place = {f: x for f, x in place.items() if x in (p, q)}
    t.update(n_p=n_p, n_q=n_q, cap=None, mice=False, n_prev=0, r=None, delta=None,
             t_prev=None, competitors=place)
    return t


def flap_jobs(rng, seed0, reps, periods, span=10.0, p=1, q=4):
    """Chunk 7a, repeated moves (an eager controller, Carpa et al. 2017): E1
    alone, starting on p; arm P<period> moves it p -> q -> p ... every period
    for span s from t_move; arm C keeps it on p with a sham flow-mod (to p) at
    the same times. One C and one run per period for each seed."""
    jobs = []
    for k in range(reps):
        seed = seed0 + k
        t_move = round(rng.uniform(12.0, 14.0), 3)
        arms = ["C"] + ["P%g" % x for x in periods]
        rng.shuffle(arms)
        for arm in arms:
            s = make_schedule(resolve("damage_moves"), seed)
            apply_setting(s)
            s["mice"] = []
            s["elephants"] = [e for e in s["elephants"] if e["id"] == "E1"]
            s["condition"] = arm
            s["pins"] = [dict(flow="E1", spine=p)]
            per = None if arm == "C" else float(arm[1:])
            times = [round(t_move + i * (per or periods[0]), 3)
                     for i in range(int(round(span / (per or periods[0]))))]
            if arm == "C":         # a sham at every time any flapping arm moves
                times = sorted({round(t_move + i * x, 3) for x in periods
                                for i in range(int(round(span / x)))})
            s["moves"] = [dict(at=t, flow="E1", spine=(p if arm == "C" else (q if i % 2 == 0 else p)))
                          for i, t in enumerate(times)]
            s["tagged"] = ["E1"]
            s["snapshots"] = []
            s["snapshots_last_only"] = True
            s["ack_spine"] = None
            s["design"] = dict(kind="flap", trial=k, arm=arm, period=per, span=span, p=p, q=q,
                               t_move=t_move, n_moves=len(times), moved=["E1"])
            jobs.append(dict(run_id="f%03d-%s" % (k, arm), controller=CONTROLLER, condition=arm,
                             rep=k, schedule=s))
    return jobs


def alone_jobs(seed, slow=4, fast=1):
    """Chunk 7a feasibility: E1 alone on the slowest spine and E2 alone on the
    fastest (disjoint paths), no move; each should reach about 95 % of the link."""
    s = make_schedule(resolve("damage_moves"), seed)
    apply_setting(s)
    s["mice"] = []
    s["elephants"] = [e for e in s["elephants"] if e["id"] in ("E1", "E2")]
    s["condition"] = "alone"
    s["pins"] = [dict(flow="E1", spine=slow), dict(flow="E2", spine=fast)]
    s["moves"], s["tagged"], s["snapshots"], s["ack_spine"] = [], [], [], None
    s["design"] = dict(kind="alone", E1=slow, E2=fast)
    return [dict(run_id="alone", controller=CONTROLLER, condition="alone", rep=0, schedule=s)]


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--trials-per-pair", type=int, default=5)
    ap.add_argument("--seed", type=int, default=5, help="design draw and run order")
    ap.add_argument("--first", type=int, default=0, help="first trial (pilots: a slice)")
    ap.add_argument("--count", type=int, default=0, help="number of trials, 0 = all")
    ap.add_argument("--only", default="", help="comma list of trial numbers (pilots)")
    ap.add_argument("--capped-only", action="store_true", help="only the trials with a rate cap")
    ap.add_argument("--cap-mode", choices=["b", "fq"], default="b",
                    help="E1's rate cap: iperf3 -b (an average since the start, which catches "
                    "up after a stall; the 2026-10-26 campaign) or --fq-rate (kernel pacing)")
    ap.add_argument("--grid", default="", help="fixed factors instead of the random draw: "
                    "'p>q,...;n_p,...;n_q,...;reps', e.g. '4>1,3>1,1>4,1>2;1;0,1;2' (the damage-"
                    "regime pilot); uncapped, no mice, no previous move")
    ap.add_argument("--placed-only", action="store_true", help="leave out the "
                    "elephants on neither p nor q (Chunk 7a grid, Chunk 7b draw)")
    ap.add_argument("--sysctl", default="", help="comma list k=v set in every host namespace, "
                    "e.g. net.ipv4.tcp_congestion_control=bbr,net.ipv4.tcp_recovery=0 (Chunk 7a)")
    ap.add_argument("--spine-delays", default="", help="one-way ms per spine, e.g. 1,1,20,50")
    ap.add_argument("--flap", default="", help="repeated-move runs instead of a design: periods "
                    "in s, e.g. 0.5,2 (Chunk 7a); --flap-reps seeds")
    ap.add_argument("--flap-reps", type=int, default=5)
    ap.add_argument("--alone", action="store_true", help="feasibility run: E1 alone on s4, E2 "
                    "alone on s1, no move")
    ap.add_argument("--cap-scale", type=float, default=1.0, help="multiply the caps (6M, 3M) "
                    "by this, e.g. 10 at 100 Mbit/s (Chunk 7b)")
    ap.add_argument("--t-move-range", default="12,14", help="t_move uniform in [a, b] s")
    ap.add_argument("--out", required=True, help="jobs JSON to write")
    a = ap.parse_args()
    T_MOVE[:] = [float(x) for x in a.t_move_range.split(",")]
    if a.cap_scale != 1.0:
        CAPS[:] = [(None if c is None else "%gM" % (float(c[:-1]) * a.cap_scale), w)
                   for c, w in CAPS]
    CAP_MODE["mode"] = a.cap_mode
    SETTING["sysctl"] = dict(kv.split("=", 1) for kv in a.sysctl.split(",") if kv)
    SETTING["spine_delay_ms"] = [float(x) for x in a.spine_delays.split(",")] if a.spine_delays else None
    rng = random.Random(a.seed)
    if a.flap or a.alone:
        jobs = (flap_jobs(rng, SEED_BASE + a.seed * 1000, a.flap_reps,
                          [float(x) for x in a.flap.split(",")]) if a.flap
                else alone_jobs(SEED_BASE + a.seed * 1000))
        os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
        json.dump(dict(source="flap" if a.flap else "alone", design_seed=a.seed,
                       setting=SETTING, n_jobs=len(jobs), jobs=jobs), open(a.out, "w"), indent=1)
        print("%d runs -> %s" % (len(jobs), a.out))
        return
    if a.grid:
        pairs, nps, nqs, reps = a.grid.split(";")
        pairs = [tuple(int(x) for x in pq.split(">")) for pq in pairs.split(",")]
        trials = [grid_trial(rng, p, q, int(n_p), int(n_q), a.placed_only) for p, q in pairs
                  for n_p in nps.split(",") for n_q in nqs.split(",") for _ in range(int(reps))]
    else:
        slots = [pq for pq in itertools.permutations(SPINES, 2)
                 for _ in range(a.trials_per_pair)]
        trials = [draw_trial(rng, p, q) for p, q in slots]
        if a.placed_only:              # Chunk 7b: drop elephants on neither p nor q
            for t in trials:
                t["competitors"] = {f: x for f, x in t["competitors"].items()
                                    if x in (t["p"], t["q"])}
    order = list(range(len(trials)))
    rng.shuffle(order)                            # run order of the trials
    if a.count:
        order = [k for k in order if a.first <= k < a.first + a.count]
    if a.only:
        order = [k for k in order if k in {int(x) for x in a.only.split(",")}]
    if a.capped_only:
        order = [k for k in order if trials[k]["cap"]]
    jobs = []
    for k in order:
        seed = SEED_BASE + a.seed * 1000 + k
        arms = ["T", "C"]
        rng.shuffle(arms)
        for arm in arms:
            jobs.append(dict(run_id="t%03d-%s" % (k, arm), controller=CONTROLLER, condition=arm,
                             rep=k, schedule=arm_schedule(trials[k], k, seed, arm)))
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    spec = dict(source="design", design_seed=a.seed, trials_per_pair=a.trials_per_pair,
                setting=SETTING,
                n_trials=len(trials), n_jobs=len(jobs), jobs=jobs)
    json.dump(spec, open(a.out, "w"), indent=1)
    with open(os.path.join(os.path.dirname(os.path.abspath(a.out)), "design.csv"), "w",
              newline="") as f:
        w = csv.writer(f)
        cols = ["trial", "seed", "p", "q", "n_p", "n_q", "cap", "mice", "n_prev", "r", "delta",
                "t_move", "t_prev", "competitors", "in_this_spec"]
        w.writerow(cols)
        sel = set(order)
        for k, t in enumerate(trials):
            w.writerow([k, SEED_BASE + a.seed * 1000 + k] + [t[c] for c in cols[2:13]] +
                       [";".join("%s:s%d" % kv for kv in sorted(t["competitors"].items())),
                        int(k in sel)])
    print("%d trials (%d in this spec), %d runs -> %s" % (len(trials), len(order), len(jobs), a.out))


if __name__ == "__main__":
    main()
