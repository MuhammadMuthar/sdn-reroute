"""Scenarios and the seeded traffic schedule (Chunk 3 onwards).

A *scenario* is plain data: topology size, run length, elephant pairs, the
short-flow (mice) process, optional pins (start a flow on a given spine,
overriding the controller) and moves (deliberate reroutes at given times).
`make_schedule(scenario, seed)` turns it into the concrete list of flows with
every start time, size and source port fixed. It uses only its own
random.Random(seed), so the same scenario + seed always gives the same
schedule, byte for byte (schedule_sha256 in every result row checks it).

Scenarios
  ecmp_mixed     4 long elephants h1..h4 -> h5..h8 (iperf3, seeded --cport so the
                 ECMP hash, and therefore the collision pattern, depends on the
                 seed) plus Poisson mice from every left host to a random right
                 host, bounded-Pareto sizes. The Chunk 3 baseline and the
                 workload Chunk 4 compares controllers on.
  loaded_move    the Chunk 2 reroute again, but optionally onto a path that
                 already carries a competing elephant. Conditions (`--condition`):
                   IC1  E0 on s1, sham move                 (idle, control)
                   IR2  E0 s4 -> s1                         (idle, = gate R2)
                   LC1  E0 on s1, B on s1, sham move        (loaded, control)
                   LR2  E0 s4 -> s1, B on s1                (loaded, 9 ms gap)
                   LR0  E0 s2 -> s1, B on s1                (loaded, no gap)
                 E0 = h1 -> h5, B = h2 -> h6; ACKs of both pinned to s1, as in
                 the gate. No mice, so the sender h1 carries only E0 and its
                 nstat counters are E0's.
"""

import copy
import hashlib
import json
import math
import random

from topologies.layout import host_ip

MICE_PORT = 8000
ELEPHANT_DPORT0 = 5201

SCENARIOS = {
    "ecmp_mixed": dict(
        hosts_per_leaf=4, duration=30,
        elephants=dict(pairs=[[1, 5], [2, 6], [3, 7], [4, 8]], start_jitter=0.5,
                       cport_range=[40001, 40999]),
        mice=dict(srcs=[1, 2, 3, 4], dsts=[5, 6, 7, 8], rate_per_src=10.0,
                  t_start=2.0, t_end=28.0, sport_range=[20000, 39999],
                  size=dict(dist="bounded_pareto", alpha=1.2, lo=2000, hi=1000000)),
        pins=[], moves=[], tagged=[], snapshots=[],
    ),
    "loaded_move": dict(
        hosts_per_leaf=4, duration=25,
        elephants=dict(pairs=[[1, 5], [2, 6]], ids=["E0", "B"], start_jitter=0.0,
                       cports=[40001, 40002]),
        mice=None, pins=[], moves=[], tagged=["E0", "B"],
        # counter snapshots relative to each move: just before and after windows
        snapshots=[-2.0, 0.0, 2.0, 5.0, 10.0],
        conditions={
            "IC1": dict(pins={"E0": 1}, moves=[dict(at=10.0, flow="E0", spine=1)],
                        drop=["B"], kind="control"),
            "IR2": dict(pins={"E0": 4}, moves=[dict(at=10.0, flow="E0", spine=1)],
                        drop=["B"], kind="reroute"),
            "LC1": dict(pins={"E0": 1, "B": 1}, moves=[dict(at=10.0, flow="E0", spine=1)],
                        kind="control"),
            "LR2": dict(pins={"E0": 4, "B": 1}, moves=[dict(at=10.0, flow="E0", spine=1)],
                        kind="reroute"),
            "LR0": dict(pins={"E0": 2, "B": 1}, moves=[dict(at=10.0, flow="E0", spine=1)],
                        kind="reroute"),
        },
        ack_spine=1,
    ),
}


def resolve(name, condition=None):
    """Scenario dict with the condition applied (pins, moves, dropped flows)."""
    sc = copy.deepcopy(SCENARIOS[name])
    conds = sc.pop("conditions", None)
    sc["name"], sc["condition"] = name, condition or ""
    if conds:
        if condition not in conds:
            raise SystemExit("scenario %s needs --condition, one of %s"
                             % (name, ",".join(conds)))
        c = conds[condition]
        sc["pins"] = [dict(flow=f, spine=s) for f, s in c["pins"].items()]
        sc["moves"] = c["moves"]
        sc["drop"] = c.get("drop", [])
        sc["kind"] = c["kind"]
    elif condition:
        raise SystemExit("scenario %s has no conditions" % name)
    return sc


def bounded_pareto(rng, alpha, lo, hi):
    u = rng.random()
    la, ha = lo ** alpha, hi ** alpha
    return int(round((-(u * ha - u * la - ha) / (ha * la)) ** (-1.0 / alpha)))


def make_schedule(sc, seed):
    rng = random.Random(seed)
    n_hosts = 2 * sc["hosts_per_leaf"]
    sched = dict(scenario=sc["name"], condition=sc["condition"], seed=seed,
                 duration=sc["duration"],
                 hosts={"h%d" % n: host_ip(n) for n in range(1, n_hosts + 1)},
                 elephants=[], mice=[], pins=[], moves=copy.deepcopy(sc["moves"]),
                 tagged=list(sc["tagged"]), snapshots=list(sc["snapshots"]),
                 ack_spine=sc.get("ack_spine"))
    el = sc["elephants"]
    ids = el.get("ids") or ["E%d" % (i + 1) for i in range(len(el["pairs"]))]
    if "cports" in el:
        cports = list(el["cports"])
    else:
        cports = rng.sample(range(el["cport_range"][0], el["cport_range"][1] + 1),
                            len(el["pairs"]))
    for i, ((s, d), fid) in enumerate(zip(el["pairs"], ids)):
        start = round(rng.uniform(0, el["start_jitter"]), 4) if el["start_jitter"] else 0.0
        if fid in sc.get("drop", []):
            continue
        sched["elephants"].append(dict(
            id=fid, src="h%d" % s, dst="h%d" % d, src_ip=host_ip(s), dst_ip=host_ip(d),
            sport=cports[i], dport=ELEPHANT_DPORT0 + i, start=start,
            duration=sc["duration"]))
    present = {e["id"] for e in sched["elephants"]}
    sched["pins"] = [p for p in sc["pins"] if p["flow"] in present]
    sched["tagged"] = [t for t in sched["tagged"] if t in present]
    m = sc.get("mice")
    if m:
        lo, hi = m["sport_range"]
        events = []
        for s in m["srcs"]:
            t = m["t_start"]
            while True:
                t += rng.expovariate(m["rate_per_src"])
                if t >= m["t_end"]:
                    break
                d = rng.choice(m["dsts"])
                z = m["size"]
                size = bounded_pareto(rng, z["alpha"], z["lo"], z["hi"])
                events.append((round(t, 6), s, d, size))
        events.sort()
        sports = rng.sample(range(lo, hi + 1), len(events))
        for i, ((t, s, d, size), sp) in enumerate(zip(events, sports)):
            sched["mice"].append(dict(
                id="M%05d" % (i + 1), src="h%d" % s, dst="h%d" % d, src_ip=host_ip(s),
                dst_ip=host_ip(d), sport=sp, dport=MICE_PORT, start=t, size=size))
    return sched


def schedule_sha256(sched):
    return hashlib.sha256(json.dumps(sched, sort_keys=True).encode()).hexdigest()


def flow_tuple(f):
    return dict(src=f["src_ip"], dst=f["dst_ip"], sport=f["sport"], dport=f["dport"])


def reverse_tuple(f):
    return dict(src=f["dst_ip"], dst=f["src_ip"], sport=f["dport"], dport=f["sport"])


def bounded_pareto_mean(alpha, lo, hi):
    """Analytic mean, for the scenario description (offered load)."""
    if alpha == 1:
        return lo * hi / (hi - lo) * math.log(hi / lo)
    return (lo ** alpha / (1 - (lo / hi) ** alpha)) * (alpha / (alpha - 1)) * \
        (1 / lo ** (alpha - 1) - 1 / hi ** (alpha - 1))
