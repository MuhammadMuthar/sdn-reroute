"""The reroutes a controller's own policy made in one run (Chunk 4). Standard
library only: used by experiments/metrics.py (venv) and experiments/replay.py
(system python3).

A reroute is an entry of meta["controller_events"] (the controller's
path_log, fabric.set_path) with origin "policy", an ok flow-mod, and a
target spine different from the spine the flow left. A flap is a reroute
that sends a flow back to the spine its previous reroute took it off,
within FLAP_WINDOW_S of that previous reroute (A -> B, then B -> A).
"""

FLAP_WINDOW_S = 10.0


def flow_key(f):
    return (f["src"], f["dst"], int(f["sport"]), int(f["dport"]))


def policy_moves(meta, sched):
    """Reroutes oldest first: dict(t, t_sent, key, flow_id, from_spine,
    to_spine, round, flap); t and t_sent are seconds since the run's t0 (t =
    barrier reply). flow_id is the schedule's elephant id, or "" for any
    other flow (a mouse that crossed the elephant threshold)."""
    eleph = {(e["src_ip"], e["dst_ip"], int(e["sport"]), int(e["dport"])): e["id"]
             for e in sched["elephants"]}
    t0 = meta["t0"]
    out, last = [], {}
    evs = [e for e in (meta.get("controller_events") or []) if e.get("origin") == "policy"]
    for e in sorted(evs, key=lambda e: e["t_barrier"]):
        key = flow_key(e["flow"])
        rec = dict(t=e["t_barrier"] - t0, t_sent=e["t_sent"] - t0, key=key,
                   flow_id=eleph.get(key, ""), from_spine=e.get("from_spine"),
                   to_spine=int(e["spine"]), round=e.get("round"))
        if not e.get("ok") or rec["from_spine"] == rec["to_spine"]:
            continue
        prev = last.get(key)
        rec["flap"] = bool(prev and rec["to_spine"] == prev["from_spine"] and
                           rec["t"] - prev["t"] <= FLAP_WINDOW_S)
        last[key] = rec
        out.append(rec)
    return out
