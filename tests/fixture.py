"""Synthetic live + replay directories in the runner's layout, with known
damage, for tests/test_damage_dataset.py. No Mininet. Stdlib + numpy only."""
import csv
import gzip
import json
import os

ELEPHANTS = [dict(id="E1", src="h1", dst="h5", src_ip="10.0.0.1", dst_ip="10.0.0.5",
                  sport=40001, dport=5201, start=0.0, duration=30),
             dict(id="E2", src="h2", dst="h6", src_ip="10.0.0.2", dst_ip="10.0.0.6",
                  sport=40002, dport=5201, start=0.0, duration=30)]
T0 = 1_700_000_000.0
RATE = {"E1": 4.0, "E2": 8.0}   # Mbit/s goodput in C; in T the moved flow loses DROP_T, bystander gains
DROP = {"E1": 1.0, "E2": -0.5}  # Mbit/s deficit (C - T) per flow after the move


def _ss(path, sport, cwnd, unacked=20, rtt=12.0, n=60):
    with gzip.open(path, "wt") as fh:
        for i in range(n):
            fh.write("T %.6f\n" % (T0 + 2 + i * 0.1))
            fh.write("ESTAB 0 1000 10.0.0.1:%d 10.0.0.5:5201\n" % sport)
            fh.write("\t cubic rtt:%.1f/1.0 mss:1448 cwnd:%d unacked:%d\n" % (rtt, cwnd, unacked))


def _trace(path, mbps, t_event, drop, dur=20.0):
    """Receiver trace: ACKs every 10 ms at `mbps`, minus `drop` after t_event."""
    with gzip.open(path, "wt", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["t_us", "dir", "seq", "ack", "len", "flags", "sack"])
        ack, t = 0, 0.0
        while t <= dur:
            r = mbps - (drop if t >= t_event else 0.0)
            ack += int(r * 1e6 / 8 * 0.01)
            w.writerow([int(t * 1e6), 1, 0, ack, 0, 16, ""])
            t += 0.01


def make(root):
    live = os.path.join(root, "live")
    replay = os.path.join(root, "replay")
    rd = os.path.join(live, "runs", "hedera-r01")
    os.makedirs(rd)
    sched = dict(scenario="ecmp_mixed", condition="", seed=1, duration=30,
                 hosts={"h1": "10.0.0.1", "h2": "10.0.0.2", "h5": "10.0.0.5", "h6": "10.0.0.6"},
                 elephants=ELEPHANTS, mice=[], pins=[], moves=[], tagged=[], snapshots=[])
    json.dump(sched, open(os.path.join(rd, "schedule.json"), "w"))
    t_ev = 5.0
    feat = dict(round=3, t=T0 + t_ev, flow=dict(src="10.0.0.1", dst="10.0.0.5", sport=40001,
                dport=5201), leaf=1, from_spine=4, to_spine=1, rate_mbps=4.0, U_p=4.1, U_q=8.0,
                gain_mbps=-7.9, why="gain>0", elephants_on_p=1, elephants_on_q=1, age_s=5.0,
                bytes_so_far=2_500_000, n_prev_moves=0, since_last_move_s=None,
                path_src="spine_counters", delay_ms_p_config=10, delay_ms_q_config=1,
                reply=dict(ok=True, t_sent=T0 + t_ev, t_barrier=T0 + t_ev + 0.002))
    with open(os.path.join(rd, "policy_log.jsonl"), "w") as fh:
        fh.write(json.dumps(dict(kind="config")) + "\n")
        fh.write(json.dumps(dict(kind="round", round=3, t=T0 + t_ev, moves=[feat])) + "\n")
    json.dump(dict(run_id="hedera-r01", controller="hedera", seed=1, t0=T0, errors=[]),
              open(os.path.join(rd, "meta.json"), "w"))
    _ss(os.path.join(rd, "ss_h1.log.gz"), 40001, cwnd=30, unacked=25, rtt=22.0)
    _ss(os.path.join(rd, "ss_h2.log.gz"), 40002, cwnd=10)
    pre, post = {"E1": 4, "E2": 1}, {"E1": 1, "E2": 1}
    for arm in ("T", "C"):
        d = os.path.join(replay, "runs", "hedera-r01-e3-%s" % arm)
        os.makedirs(d)
        s = dict(sched, condition=arm, replay=dict(live_run="hedera-r01", round=3, arm=arm,
                 moved=["E1"], pre=pre, post=post, t_event=t_ev))
        json.dump(s, open(os.path.join(d, "schedule.json"), "w"))
        json.dump(dict(run_id=os.path.basename(d), controller="ecmp", seed=1, t0=T0, errors=[],
                       moves=[dict(at=t_ev, flow="E1", spine=1,
                                   reply=dict(ok=True, t_barrier=T0 + t_ev))]),
                  open(os.path.join(d, "meta.json"), "w"))
        for e in ELEPHANTS:
            _trace(os.path.join(d, "recv_%s_trace.csv.gz" % e["id"]), RATE[e["id"]], t_ev,
                   DROP[e["id"]] if arm == "T" else 0.0)
        retx = 7 if arm == "T" else 2
        cnt = {}
        for name, k in (("m0_pre", 0), ("m0_+2", 1), ("m0_+5", 1), ("m0_+10", 1)):
            cnt[name] = dict(t=T0 + t_ev, **{h: {"TcpRetransSegs": 100 + k * retx} for h in
                                             ("h1", "h2", "h5", "h6")})
        json.dump(cnt, open(os.path.join(d, "counters.json"), "w"))
    return live, replay
