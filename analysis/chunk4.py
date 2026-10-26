#!/usr/bin/env python3
"""Chunk 4 analysis: ECMP vs monitor-only ECMP vs Hedera-style vs cooldown-only,
and the per-reroute damage from matched move / no-move replays. The analysis
plan is pre-registered in LOG.md (2026-10-19, Chunk 4 part 2) before the
campaign ran; this script implements that plan and nothing else.

Inputs
  --compare DIR   a campaign of experiments/runner.py (runs.csv, flows.csv,
                  links.csv from experiments/metrics.py; runs/<id>/ with
                  meta.json and, for the Hedera-style controllers,
                  policy_log.jsonl)
  --damage DIR    a replay campaign (runner.py replay --spec, jobs from
                  experiments/replay.py): runs/<id>/ with schedule.json
                  (block "replay"), meta.json, counters.json, run_flows.csv and
                  recv_<E>_trace.csv.gz for every elephant
Outputs (in --out, default the compare directory)
  chunk4_summary.md     every table, the exclusions and the checks
  chunk4_summary.csv    per controller and metric: n, mean, 95 % CI half-width
  chunk4_contrasts.csv  paired contrasts (difference per seed, mean, 95 % CI)
  chunk4_events.csv     one row per replayed reroute event: the three damage
                        terms per window, counters, the controller's pre-move
                        features (Chunk 5 input)
  figures/chunk4_comparison.png, chunk4_damage.png, chunk4_tradeoff.png
                        (--figures DIR; a SYNTHETIC campaign never writes there)

Checks without Chunk 4 data
  --loaded-check results/loaded/<campaign>
      runs the damage-term code on the Chunk 3 loaded-move runs, LR2 (E0
      s4 -> s1, B on s1) as the move arm and LC1 (both on s1, sham move) as
      its no-move control, paired by seed; must reproduce the Chunk 3 W10
      numbers (analysis/loaded_move.py: moved -0.88, bystander +0.86,
      total -0.03 Mbit/s)
  analysis/chunk4_fixture.py --out DIR, then --compare DIR/compare --damage
  DIR/damage: a synthetic campaign (SYNTHETIC marker file) that exercises the
  Hedera / cooldown columns and the replay pairs end to end

Definitions (LOG.md plan): goodput G of a flow in a window = in-order bytes the
receiver acknowledged in it (cumulative ACK of the receiver-side trace),
windows W2, W5, W10 from the barrier reply of the (sham) move. Deficit of a
flow = G_C - G_T (bytes; positive = the move cost it). Per event: moved =
sum over the moved flows; bystanders = sum over the elephants already on a
destination path and not moved; dest_total = moved + bystanders, the single
damage number. all_total = sum over every elephant.
"""

import argparse
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
from analysis.gate import EVENT, ack_at, mean_ci  # noqa: E402

WINDOWS = (2.0, 5.0, 10.0)
PRIMARY_W = 10.0
PRE_W = 2.0
BASELINE = "ecmp"
CTRL_ORDER = ["ecmp", "ecmp_mon", "hedera", "cooldown", "costaware_formula", "costaware_ml"]
CTRL_LABEL = {"ecmp": "ECMP", "ecmp_mon": "ECMP, monitor only",
              "hedera": "Hedera-style", "cooldown": "cooldown-only",
              "costaware_formula": "cost-aware, formula", "costaware_ml": "cost-aware, ML"}
# the repo's figure palette (analysis/ecmp.py, loaded_move.py): the static
# baselines stay grey, the two rerouting controllers get the two hues
CTRL_COLOR = {"ecmp": "#8a8a85", "ecmp_mon": "#b4b2a9", "hedera": "#e08a2c",
              "cooldown": "#2a78d6"}
INK, INK2 = "#1f1f1e", "#5f5e58"

# (runs.csv column, label, unit) in the order of the plan
METRICS = [
    ("eleph_goodput_mean", "elephant goodput, mean of 4", "Mbit/s"),
    ("eleph_goodput_min", "elephant goodput, worst of 4", "Mbit/s"),
    ("eleph_goodput_sum", "elephant goodput, sum", "Mbit/s"),
    ("fct_p50_ms", "mice FCT p50", "ms"),
    ("fct_p99_ms", "mice FCT p99", "ms"),
    ("fct_p50_ms_lt10k", "mice FCT p50, < 10 KB", "ms"),
    ("fct_p99_ms_lt10k", "mice FCT p99, < 10 KB", "ms"),
    ("fct_p50_ms_10k_100k", "mice FCT p50, 10-100 KB", "ms"),
    ("fct_p99_ms_10k_100k", "mice FCT p99, 10-100 KB", "ms"),
    ("fct_p50_ms_ge100k", "mice FCT p50, >= 100 KB", "ms"),
    ("fct_p99_ms_ge100k", "mice FCT p99, >= 100 KB", "ms"),
    ("mice_failed", "mice that failed", ""),
    ("max_link_util", "max link utilisation", ""),
    ("n_reroutes", "reroutes per run", ""),
    ("reroutes_per_min", "reroutes per minute", "/min"),
    ("n_reroute_rounds", "reroute rounds per run", ""),
    ("flaps_per_min", "flaps per minute", "/min"),
    ("n_reroutes_other", "moves of non-elephant flows per run", ""),
    ("snd_TcpRetransSegs", "retransmitted segments, all senders", ""),
    ("snd_reorder_events", "reorder / spurious events, all senders", ""),
    ("snd_TcpExtTCPTimeouts", "RTOs, all senders", ""),
    ("ctl_collect_ms_p50", "controller stats collection per round, p50", "ms"),
    ("ctl_collect_ms_max", "controller stats collection per round, max", "ms"),
]
# pre-registered contrasts: (id, controller, reference, metric, prediction)
CONTRASTS = [
    ("P1", "hedera", "ecmp", "eleph_goodput_mean", "> 0"),
    ("P2", "hedera", "ecmp", "fct_p50_ms", "> 0 (worse)"),
    ("S1", "ecmp_mon", "ecmp", "eleph_goodput_mean", "CI includes 0"),
    ("S1", "ecmp_mon", "ecmp", "fct_p50_ms", "CI includes 0"),
    ("S1", "ecmp_mon", "ecmp", "fct_p99_ms", "CI includes 0"),
    ("S2", "cooldown", "hedera", "reroutes_per_min", "<= 0"),
    ("S2", "cooldown", "hedera", "flaps_per_min", "<= 0"),
    ("S2", "cooldown", "hedera", "snd_reorder_events", "<= 0"),
    ("S2", "cooldown", "hedera", "eleph_goodput_mean", "none (reported)"),
]
DESCRIPTIVE_CONTRAST_METRICS = ["eleph_goodput_mean", "eleph_goodput_min", "fct_p50_ms",
                                "fct_p99_ms", "fct_p99_ms_lt10k", "reroutes_per_min",
                                "snd_TcpRetransSegs", "snd_reorder_events"]
FEATURES = ["rate_mbps", "U_p", "U_q", "gain_mbps", "elephants_on_p", "elephants_on_q",
            "delay_ms_p_config", "delay_ms_q_config", "age_s", "n_prev_moves",
            "since_last_move_s", "bytes_so_far"]


# ---------------------------------------------------------------- helpers
def fmt(x, f="%.3g"):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f % x


def fmt_ci(x, f="%.3g"):
    m, h = mean_ci(x)
    if np.isnan(m):
        return "n/a"
    return (f + " ± " + f) % (m, h) if not np.isnan(h) else f % m


def paired_ci(d):
    """mean and 95 % t CI half-width of paired differences (n - 1 df)."""
    return mean_ci(d)


def read_jsonl(path):
    if not os.path.exists(path) and os.path.exists(path + ".gz"):
        path += ".gz"
    if not os.path.exists(path):
        return []
    op = gzip.open if path.endswith(".gz") else open
    return [json.loads(line) for line in op(path, "rt") if line.strip()]


def acks(rd, fid):
    p = os.path.join(rd, "recv_%s_trace.csv.gz" % fid)
    if not os.path.exists(p):
        return None
    t = pd.read_csv(p)
    a = t[t.dir == 1]
    return a.t_us.to_numpy() / 1e6, a.ack.to_numpy().astype(float)


def goodput_bytes(rd, fid, tm):
    """{W: bytes acked in [tm, tm + W)} and 'pre': bytes in [tm - PRE_W, tm)."""
    a = acks(rd, fid)
    if a is None:
        return None
    at, av = a
    pts = ack_at(np.array([tm - PRE_W, tm] + [tm + w for w in WINDOWS]), at, av)
    out = {"pre": float(pts[1] - pts[0])}
    for w, p in zip(WINDOWS, pts[2:]):
        out[w] = float(p - pts[1])
    return out


def capture_spines(rd, fid):
    fl = pd.read_csv(os.path.join(rd, "run_flows.csv"))
    r = fl[(fl.kind == "elephant") & (fl.id == fid)]
    if not len(r):
        return None
    s = str(r.iloc[0].bytes_by_spine)
    return sorted(int(x.split(":")[0][1:]) for x in s.split(";") if x and x != "nan")


def counter_delta(c, a, b, hosts, names):
    return {n: sum(c[b][h].get(n, 0) - c[a][h].get(n, 0) for h in hosts) for n in names}


# ---------------------------------------------------------------- comparison
def load_compare(camp):
    runs = pd.read_csv(os.path.join(camp, "runs.csv"))
    flows = pd.read_csv(os.path.join(camp, "flows.csv"))
    runs["errors"] = runs["errors"].fillna("").astype(str)
    bad = runs[(runs.errors.str.len() > 0) | (runs.elephants_ok < runs.n_elephants)]
    excl = ["%s (%s, seed %d): %s" % (r.run_id, r.controller, r.seed,
                                      r.errors or "elephant did not finish")
            for _, r in bad.iterrows()]
    runs = runs[~runs.run_id.isin(bad.run_id)].copy()
    flows = flows[flows.run_id.isin(runs.run_id)]
    for k, _, _ in METRICS:
        if k not in runs:
            runs[k] = np.nan
    return runs, flows, excl


def controllers_in(runs):
    present = list(runs.controller.unique())
    return [c for c in CTRL_ORDER if c in present] + sorted(c for c in present
                                                            if c not in CTRL_ORDER)


def per_controller_table(runs, ctrls):
    rows, L = [], []
    L += ["| metric | " + " | ".join(CTRL_LABEL.get(c, c) for c in ctrls) + " |",
          "|---|" + "---|" * len(ctrls)]
    for k, lab, unit in METRICS:
        cells = []
        for c in ctrls:
            x = runs[runs.controller == c][k]
            m, h = mean_ci(x)
            rows.append(dict(controller=c, metric=k, label=lab, unit=unit,
                             n=int(x.notna().sum()), mean=m, ci95=h))
            cells.append(fmt_ci(x))
        L.append("| %s%s | %s |" % (lab, (" (%s)" % unit) if unit else "", " | ".join(cells)))
    return pd.DataFrame(rows), L


def paired(runs, c, ref, metric):
    a = runs[runs.controller == c].set_index("seed")[metric]
    b = runs[runs.controller == ref].set_index("seed")[metric]
    seeds = sorted(set(a.index) & set(b.index))
    d = (a.loc[seeds] - b.loc[seeds]).dropna()
    m, h = paired_ci(d)
    return d, m, h


def contrasts_table(runs, ctrls):
    rows, L = [], []
    L += ["| id | contrast | metric | n pairs | difference, mean [95 % CI] | prediction | outcome |",
          "|---|---|---|---|---|---|---|"]
    label = {k: lab for k, lab, _ in METRICS}
    seen = set()
    for cid, c, ref, metric, pred in CONTRASTS:
        if c not in ctrls or ref not in ctrls:
            continue
        d, m, h = paired(runs, c, ref, metric)
        lo, hi = m - h, m + h
        if np.isnan(m) or len(d) < 2:
            out = "n/a"
        elif pred.startswith("> 0"):
            out = "supported" if lo > 0 else ("opposite sign" if hi < 0 else "not supported")
        elif pred.startswith("<= 0"):
            out = "supported" if hi <= 0 else ("opposite sign" if lo > 0 else "not supported")
        elif pred.startswith("CI includes 0"):
            out = "as predicted" if lo <= 0 <= hi else "monitoring changes the result"
        else:
            out = "reported"
        rows.append(dict(id=cid, controller=c, reference=ref, metric=metric, n_pairs=len(d),
                         mean=m, ci95=h, lo=lo, hi=hi, prediction=pred, outcome=out,
                         preregistered=1))
        seen.add((c, ref, metric))
        L.append("| %s | %s minus %s | %s | %d | %s [%s, %s] | %s | %s |" % (
            cid, CTRL_LABEL.get(c, c), CTRL_LABEL.get(ref, ref), label.get(metric, metric),
            len(d), fmt(m), fmt(lo), fmt(hi), pred, out))
    L += ["", "Descriptive paired differences (not pre-registered as tests):", "",
          "| contrast | metric | n pairs | difference, mean [95 % CI] |", "|---|---|---|---|"]
    for c in ctrls:
        for ref in (BASELINE, "hedera"):
            if c == ref or ref not in ctrls or c not in ctrls:
                continue
            if ref == "hedera" and c != "cooldown":
                continue
            for metric in DESCRIPTIVE_CONTRAST_METRICS:
                if (c, ref, metric) in seen:
                    continue
                d, m, h = paired(runs, c, ref, metric)
                if np.isnan(m):
                    continue
                rows.append(dict(id="", controller=c, reference=ref, metric=metric,
                                 n_pairs=len(d), mean=m, ci95=h, lo=m - h, hi=m + h,
                                 prediction="", outcome="", preregistered=0))
                L.append("| %s minus %s | %s | %d | %s [%s, %s] |" % (
                    CTRL_LABEL.get(c, c), CTRL_LABEL.get(ref, ref), label.get(metric, metric),
                    len(d), fmt(m), fmt(m - h), fmt(m + h)))
    return pd.DataFrame(rows), L


def per_run_table(runs, ctrls):
    L = ["| run | controller | seed | elephant -> spine | elephant goodput mean / worst, "
         "Mbit/s | FCT p50 / p99, ms | reroutes | flaps | retrans | reorder events | RTOs |",
         "|" + "---|" * 11]
    for c in ctrls:
        for _, r in runs[runs.controller == c].sort_values("seed").iterrows():
            L.append("| %s | %s | %d | %s | %.2f / %.2f | %.1f / %.0f | %s | %s | %s | %s | %s |" % (
                r.run_id, c, r.seed, str(r.elephant_spines).replace(";", " "),
                r.eleph_goodput_mean, r.eleph_goodput_min, r.fct_p50_ms, r.fct_p99_ms,
                fmt(r.n_reroutes, "%.0f"), fmt(r.n_flaps, "%.0f") if "n_flaps" in r else "n/a",
                fmt(r.snd_TcpRetransSegs, "%.0f"), fmt(r.snd_reorder_events, "%.0f"),
                fmt(r.snd_TcpExtTCPTimeouts, "%.0f")))
    return L


# ---------------------------------------------------------------- damage
def policy_features(compare, live_run):
    """{round: features of the round's first ok move} from policy_log.jsonl."""
    if not compare:
        return {}
    recs = read_jsonl(os.path.join(compare, "runs", live_run, "policy_log.jsonl"))
    out = {}
    for r in recs:
        if r.get("kind") != "round":
            continue
        ok = [m for m in r.get("moves", []) if (m.get("reply") or {}).get("ok")]
        if ok:
            f = {k: ok[0].get(k) for k in FEATURES}
            f["n_moves_in_round"] = len(ok)
            f["why"] = ok[0].get("why")
            out[r["round"]] = f
    return out


def arm_measure(rd, elephants, expected, moved_src_hosts):
    """One replay arm: per-elephant goodput per window, path check, counters.
    `expected` = {fid: sorted spines the captures must show}."""
    meta = json.load(open(os.path.join(rd, "meta.json")))
    sched = json.load(open(os.path.join(rd, "schedule.json")))
    res = dict(errors=" | ".join(meta.get("errors") or []), path_check="ok", G={})
    if res["errors"] or not meta.get("moves"):
        res["path_check"] = "run has errors or no move record"
        return res
    t0 = meta["t0"]
    rep = meta["moves"][0]["reply"]
    tm = rep["t_barrier"] - t0
    res["t_move_s"] = tm
    res["flowmod_ms"] = (rep["t_barrier"] - rep["t_sent"]) * 1e3
    bad = []
    for fid, want in expected.items():
        got = capture_spines(rd, fid)
        if got != sorted(want):
            bad.append("%s data on %s, expected %s" % (fid, got, sorted(want)))
    for fid in elephants:
        g = goodput_bytes(rd, fid, tm)
        if g is None:
            bad.append("%s: no receiver trace" % fid)
            continue
        res["G"][fid] = g
    res["path_check"] = "; ".join(bad) if bad else "ok"
    c = json.load(open(os.path.join(rd, "counters.json")))
    if "m0_pre" in c and "m0_+2" in c and moved_src_hosts:
        d = counter_delta(c, "m0_pre", "m0_+2", moved_src_hosts,
                          EVENT + ["TcpExtTCPTimeouts", "TcpRetransSegs"])
        res["events_W2"] = sum(d[k] for k in EVENT)
        res["rto_W2"] = d["TcpExtTCPTimeouts"]
        res["retrans_W2"] = d["TcpRetransSegs"]
    res["sched"] = sched
    return res


def event_row(T, C, ev, elephants, src_of):
    """The three damage terms of one event from its two arms."""
    moved = ev["moved"]
    post, pre = ev["post"], ev["pre"]
    dest = {post[f] for f in moved}
    src = {pre[f] for f in moved}
    bystanders = [f for f in elephants if f not in moved and post[f] in dest]
    left_behind = [f for f in elephants if f not in moved and post[f] in src]
    row = dict(n_moved=len(moved), moved=";".join(moved), n_bystanders=len(bystanders),
               bystanders=";".join(bystanders), dest_spines=";".join("s%d" % s for s in sorted(dest)),
               src_spines=";".join("s%d" % s for s in sorted(src)),
               n_left_behind=len(left_behind))
    for w in WINDOWS:
        def deficit(fids):
            return sum(C["G"][f][w] - T["G"][f][w] for f in fids)
        row["moved_W%g" % w] = deficit(moved)
        row["bystanders_W%g" % w] = deficit(bystanders)
        row["dest_total_W%g" % w] = row["moved_W%g" % w] + row["bystanders_W%g" % w]
        row["left_behind_W%g" % w] = deficit(left_behind)
        row["all_total_W%g" % w] = deficit(elephants)
        # the same in Mbit/s over the window, for the tables
        for term in ("moved", "bystanders", "dest_total", "all_total"):
            row["%s_mbps_W%g" % (term, w)] = row["%s_W%g" % (term, w)] * 8 / w / 1e6
    pre_T = sum(T["G"][f]["pre"] for f in elephants) * 8 / PRE_W / 1e6
    pre_C = sum(C["G"][f]["pre"] for f in elephants) * 8 / PRE_W / 1e6
    row.update(pre_goodput_T_mbps=pre_T, pre_goodput_C_mbps=pre_C,
               pre_mismatch_flag=int(abs(pre_T - pre_C) > 1.0))
    for arm, R in (("T", T), ("C", C)):
        for k in ("events_W2", "rto_W2", "retrans_W2", "t_move_s", "flowmod_ms"):
            row["%s_%s" % (k, arm)] = R.get(k, np.nan)
    row["event_W2"] = int(row.get("events_W2_T", 0) >= 1)
    return row


def load_damage(damage, compare):
    """events DataFrame (one row per analysed pair) and the list of excluded pairs."""
    arms = {}
    for rd in sorted(glob.glob(os.path.join(damage, "runs", "*"))):
        sp = os.path.join(rd, "schedule.json")
        if not os.path.exists(sp):
            continue
        sched = json.load(open(sp))
        rp = sched.get("replay")
        if not rp:
            continue
        key = (rp["live_controller"], rp["live_run"], rp["round"])
        arms.setdefault(key, {})[rp["arm"]] = (rd, sched)
    rows, excl = [], []
    feats = {}
    for key in sorted(arms):
        ctrl, live, rnd = key
        if "T" not in arms[key] or "C" not in arms[key]:
            excl.append("%s %s round %s: only arm %s present" % (ctrl, live, rnd,
                                                                  list(arms[key])))
            continue
        rdT, sT = arms[key]["T"]
        rdC, sC = arms[key]["C"]
        rp = sT["replay"]
        elephants = sorted(e["id"] for e in sT["elephants"])
        src_of = {e["id"]: e["src"] for e in sT["elephants"]}
        pre, post = {k: int(v) for k, v in rp["pre"].items()}, {k: int(v) for k, v in rp["post"].items()}
        ev = dict(moved=rp["moved"], pre=pre, post=post)
        expT = {f: sorted({pre[f], post[f]}) for f in elephants}
        expC = {f: [post[f]] for f in elephants}
        hosts = sorted({src_of[f] for f in rp["moved"]})
        T = arm_measure(rdT, elephants, expT, hosts)
        C = arm_measure(rdC, elephants, expC, hosts)
        probs = [a + ": " + R["path_check"] for a, R in (("T", T), ("C", C))
                 if R["path_check"] != "ok"]
        if probs:
            excl.append("%s %s round %s: %s" % (ctrl, live, rnd, "; ".join(probs)))
            continue
        row = dict(controller=ctrl, live_run=live, round=rnd, seed=sT["seed"],
                   run_T=os.path.basename(rdT), run_C=os.path.basename(rdC),
                   t_event_s=rp.get("t_event"))
        row.update(event_row(T, C, ev, elephants, src_of))
        if (ctrl, live) not in feats:
            feats[(ctrl, live)] = policy_features(compare, live)
        f = feats[(ctrl, live)].get(rnd, {})
        for k in FEATURES + ["n_moves_in_round", "why"]:
            row["feat_" + k] = f.get(k, np.nan)
        rows.append(row)
    return pd.DataFrame(rows), excl


def damage_tables(events, ctrls):
    L = []
    present = [c for c in ctrls if c in set(events.controller)] + \
              sorted(set(events.controller) - set(ctrls))
    L += ["Per event, deficit = no-move control minus move, Mbit/s over the window "
          "(positive = the reroute cost that goodput). Mean ± 95 % CI over events.", "",
          "| controller | events | pairs flagged (pre-move arms differ > 1 Mbit/s) | "
          "window | (1) moved flow | (2) bystanders | (3) destination-path total = (1)+(2) "
          "| all four elephants | reorder event in W2 (move arm) |",
          "|" + "---|" * 9]
    for c in present:
        e = events[events.controller == c]
        for w in WINDOWS:
            L.append("| %s | %d | %d | W%g | %s | %s | **%s** | %s | %s |" % (
                CTRL_LABEL.get(c, c), len(e), int(e.pre_mismatch_flag.sum()), w,
                fmt_ci(e["moved_mbps_W%g" % w]), fmt_ci(e["bystanders_mbps_W%g" % w]),
                fmt_ci(e["dest_total_mbps_W%g" % w]), fmt_ci(e["all_total_mbps_W%g" % w]),
                "%d / %d" % (int(e.event_W2.sum()), len(e)) if w == PRIMARY_W else ""))
    L += ["", "Damage per run: sum of term (3) at W%g over the run's replayed events, "
          "MB (0 for a run without a reroute of an elephant is *not* assumed; only "
          "runs with at least one replayed event appear).  " % PRIMARY_W, "",
          "| controller | runs with events | events per run | term (3) per run, MB | "
          "moved-flow term per run, MB |", "|---|---|---|---|---|"]
    for c in present:
        e = events[events.controller == c]
        per = e.groupby("live_run").agg(n=("round", "size"),
                                        d=("dest_total_W%g" % PRIMARY_W, "sum"),
                                        m=("moved_W%g" % PRIMARY_W, "sum"))
        L.append("| %s | %d | %s | %s | %s |" % (CTRL_LABEL.get(c, c), len(per),
                                                 fmt_ci(per.n, "%.1f"),
                                                 fmt_ci(per.d / 1e6, "%.2f"),
                                                 fmt_ci(per.m / 1e6, "%.2f")))
    L += ["", "Reported, not tested (W%g, term (3) in Mbit/s against the controller's "
          "pre-move features; Pearson r over events, all controllers pooled):" % PRIMARY_W, ""]
    y = events["dest_total_mbps_W%g" % PRIMARY_W]
    for k, lab in (("feat_gain_mbps", "predicted gain U_p - U_q - r_f"),
                   ("feat_rate_mbps", "flow rate before the move"),
                   ("feat_elephants_on_q", "elephants already on the destination"),
                   ("delay_gap", "delay gap, destination minus source (ms)")):
        if k == "delay_gap":
            x = events.feat_delay_ms_q_config - events.feat_delay_ms_p_config
        else:
            x = events[k]
        ok = x.notna() & y.notna()
        if ok.sum() >= 3 and x[ok].std() > 0:
            r, p = stats.pearsonr(x[ok].astype(float), y[ok].astype(float))
            L.append("- %s: r = %.2f (n = %d, p = %.2g)" % (lab, r, ok.sum(), p))
        else:
            L.append("- %s: n/a" % lab)
    return L


# ---------------------------------------------------------------- figures
def style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color(INK2)
    ax.tick_params(colors=INK2, labelsize=8)
    ax.yaxis.grid(True, color="#e6e5e0", lw=0.6)
    ax.set_axisbelow(True)


def fig_comparison(runs, ctrls, path):
    panels = [("eleph_goodput_mean", "elephant goodput\nmean of 4, Mbit/s"),
              ("eleph_goodput_min", "elephant goodput\nworst of 4, Mbit/s"),
              ("fct_p50_ms", "mice FCT p50, ms"), ("fct_p99_ms", "mice FCT p99, ms"),
              ("reroutes_per_min", "reroutes per minute"),
              ("snd_reorder_events", "reorder / spurious\nevents per run")]
    fig, axes = plt.subplots(2, 3, figsize=(10, 6))
    rng = np.random.default_rng(0)
    for ax, (k, lab) in zip(axes.flat, panels):
        style(ax)
        for i, c in enumerate(ctrls):
            x = runs[runs.controller == c][k].dropna()
            if not len(x):
                ax.text(i, 0, "n/a", ha="center", va="bottom", color=INK2, fontsize=8)
                continue
            jit = rng.uniform(-0.18, 0.18, len(x))
            ax.scatter(i + jit, x, s=14, color=CTRL_COLOR.get(c, INK2), alpha=0.45, lw=0)
            m, h = mean_ci(x)
            ax.errorbar([i], [m], yerr=[0 if np.isnan(h) else h], fmt="o", ms=6,
                        color=CTRL_COLOR.get(c, INK2), mec=INK, mew=0.8, capsize=3, lw=1.4)
            ax.annotate(fmt(m), (i, m), xytext=(8, 0), textcoords="offset points",
                        fontsize=7, color=INK, va="center")
        ax.set_xticks(range(len(ctrls)))
        ax.set_xticklabels([CTRL_LABEL.get(c, c).replace(", ", ",\n") for c in ctrls], fontsize=7)
        ax.set_xlim(-0.6, len(ctrls) - 0.4)
        ax.set_ylabel(lab, fontsize=8, color=INK)
        ax.set_ylim(bottom=0)
    fig.suptitle("Chunk 4: controllers on ecmp_mixed, one point per seed, "
                 "mean ± 95 %% CI (n = %d runs per controller)" %
                 runs.groupby("controller").size().max(), fontsize=9, color=INK)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_damage(events, ctrls, path):
    present = [c for c in ctrls if c in set(events.controller)]
    terms = [("moved_mbps_W%g" % PRIMARY_W, "(1) moved flow"),
             ("bystanders_mbps_W%g" % PRIMARY_W, "(2) bystanders"),
             ("dest_total_mbps_W%g" % PRIMARY_W, "(3) destination-path total"),
             ("all_total_mbps_W%g" % PRIMARY_W, "all four elephants")]
    fig, (ax, ax2) = plt.subplots(1, 2, figsize=(10, 4), gridspec_kw=dict(width_ratios=[3, 2]))
    style(ax)
    style(ax2)
    rng = np.random.default_rng(1)
    width = 0.8 / max(len(present), 1)
    for j, c in enumerate(present):
        e = events[events.controller == c]
        for i, (k, lab) in enumerate(terms):
            x = i + (j - (len(present) - 1) / 2) * width
            ax.scatter(x + rng.uniform(-width / 4, width / 4, len(e)), e[k], s=12,
                       color=CTRL_COLOR.get(c, INK2), alpha=0.4, lw=0)
            m, h = mean_ci(e[k])
            ax.errorbar([x], [m], yerr=[0 if np.isnan(h) else h], fmt="o", ms=6,
                        color=CTRL_COLOR.get(c, INK2), mec=INK, mew=0.8, capsize=3, lw=1.4,
                        label="%s (%d events)" % (CTRL_LABEL.get(c, c), len(e)) if i == 0 else None)
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_xticks(range(len(terms)))
    ax.set_xticklabels([t[1] for t in terms], fontsize=7)
    ax.set_ylabel("deficit vs matched no-move control, W%g, Mbit/s\n(positive = the move cost "
                  "goodput)" % PRIMARY_W, fontsize=8, color=INK)
    ax.legend(fontsize=7, frameon=False, loc="best")
    ax.set_title("Per-reroute damage terms (one point per event)", fontsize=9, color=INK)
    for c in present:
        e = events[events.controller == c]
        ax2.scatter(e["moved_mbps_W%g" % PRIMARY_W], e["dest_total_mbps_W%g" % PRIMARY_W],
                    s=16, color=CTRL_COLOR.get(c, INK2), alpha=0.7, lw=0,
                    label=CTRL_LABEL.get(c, c))
    lim = np.nanmax(np.abs(events[["moved_mbps_W%g" % PRIMARY_W,
                                    "dest_total_mbps_W%g" % PRIMARY_W]].to_numpy())) * 1.1 or 1
    ax2.plot([-lim, lim], [-lim, lim], color="#c9c8c2", lw=0.8, ls="--")
    ax2.axhline(0, color=INK2, lw=0.6)
    ax2.axvline(0, color=INK2, lw=0.6)
    ax2.set_xlabel("(1) moved-flow deficit, Mbit/s", fontsize=8, color=INK)
    ax2.set_ylabel("(3) destination-path total, Mbit/s", fontsize=8, color=INK)
    ax2.set_title("Moved flow alone vs the single damage number", fontsize=9, color=INK)
    ax2.legend(fontsize=7, frameon=False)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def fig_tradeoff(runs, events, ctrls, path):
    """Benefit over ECMP (paired per seed) against what it cost."""
    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    xs = [("reroutes_per_min", "reroutes per minute (mean ± 95 % CI)")]
    for ax, (xk, xlab) in zip(axes[:1], xs):
        style(ax)
        for c in ctrls:
            if c == BASELINE:
                continue
            d, m, h = paired(runs, c, BASELINE, "eleph_goodput_mean")
            x = runs[runs.controller == c][xk]
            xm, xh = mean_ci(x)
            if np.isnan(m) or np.isnan(xm):
                continue
            ax.errorbar([xm], [m], xerr=[0 if np.isnan(xh) else xh],
                        yerr=[0 if np.isnan(h) else h], fmt="o", ms=7,
                        color=CTRL_COLOR.get(c, INK2), mec=INK, mew=0.8, capsize=3, lw=1.2)
            ax.annotate(CTRL_LABEL.get(c, c), (xm, m), xytext=(6, 6),
                        textcoords="offset points", fontsize=8, color=INK)
        ax.axhline(0, color=INK2, lw=0.8)
        ax.set_xlabel(xlab, fontsize=8, color=INK)
        ax.set_ylabel("elephant goodput gain over ECMP, Mbit/s\n(paired by seed, mean ± 95 % CI)",
                      fontsize=8, color=INK)
        ax.set_title("Benefit against how often it moved flows", fontsize=9, color=INK)
    ax = axes[1]
    style(ax)
    if events is not None and len(events):
        for c in ctrls:
            if c == BASELINE or c not in set(events.controller):
                continue
            d, m, h = paired(runs, c, BASELINE, "eleph_goodput_mean")
            per = events[events.controller == c].groupby("live_run")[
                "dest_total_W%g" % PRIMARY_W].sum() / 1e6
            xm, xh = mean_ci(per)
            if np.isnan(m) or np.isnan(xm):
                continue
            ax.errorbar([xm], [m], xerr=[0 if np.isnan(xh) else xh],
                        yerr=[0 if np.isnan(h) else h], fmt="o", ms=7,
                        color=CTRL_COLOR.get(c, INK2), mec=INK, mew=0.8, capsize=3, lw=1.2)
            ax.annotate(CTRL_LABEL.get(c, c), (xm, m), xytext=(6, 6),
                        textcoords="offset points", fontsize=8, color=INK)
        ax.set_xlabel("damage per run: sum of destination-path deficits (W%g), MB" % PRIMARY_W,
                      fontsize=8, color=INK)
    else:
        ax.text(0.5, 0.5, "no replay pairs yet", ha="center", va="center", color=INK2,
                transform=ax.transAxes)
    ax.axhline(0, color=INK2, lw=0.8)
    ax.set_ylabel("elephant goodput gain over ECMP, Mbit/s", fontsize=8, color=INK)
    ax.set_title("Benefit against measured damage", fontsize=9, color=INK)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ---------------------------------------------------------------- loaded check
def loaded_check(camp):
    """Chunk 3 LR2 (move) vs LC1 (no move) by seed, through event_row."""
    runs = {}
    for rd in sorted(glob.glob(os.path.join(camp, "runs", "*"))):
        sched = json.load(open(os.path.join(rd, "schedule.json")))
        if sched["condition"] in ("LR2", "LC1"):
            runs[(sched["condition"], sched["seed"])] = rd
    seeds = sorted({s for c, s in runs if ("LR2", s) in runs and ("LC1", s) in runs})
    elephants, src_of = ["E0", "B"], {"E0": "h1", "B": "h2"}
    ev = dict(moved=["E0"], pre={"E0": 4, "B": 1}, post={"E0": 1, "B": 1})
    rows, excl = [], []
    for s in seeds:
        T = arm_measure(runs[("LR2", s)], elephants, {"E0": [1, 4], "B": [1]}, ["h1"])
        C = arm_measure(runs[("LC1", s)], elephants, {"E0": [1], "B": [1]}, ["h1"])
        probs = [a + ": " + R["path_check"] for a, R in (("T", T), ("C", C))
                 if R["path_check"] != "ok"]
        if probs:
            excl.append("seed %d: %s" % (s, "; ".join(probs)))
            continue
        rows.append(dict(seed=s, **event_row(T, C, ev, elephants, src_of)))
    e = pd.DataFrame(rows)
    print("loaded-move check: %d seeds paired (LR2 move arm, LC1 no-move arm), %d excluded"
          % (len(e), len(excl)))
    for x in excl:
        print("  excluded:", x)
    print("expected from analysis/loaded_move.py (Chunk 3, W10, Mbit/s): moved E0 -0.88 "
          "[-1.25, -0.52], bystander B +0.86 [0.50, 1.22], total -0.03 [-0.04, -0.01]")
    for w in WINDOWS:
        print("W%-3g moved %s   bystanders %s   dest_total %s   all %s   reorder event in W2: %d/%d"
              % (w, fmt_ci(e["moved_mbps_W%g" % w]), fmt_ci(e["bystanders_mbps_W%g" % w]),
                 fmt_ci(e["dest_total_mbps_W%g" % w]), fmt_ci(e["all_total_mbps_W%g" % w]),
                 int(e.event_W2.sum()), len(e)))
    return e


# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--compare", help="comparison campaign directory")
    ap.add_argument("--damage", help="replay campaign directory")
    ap.add_argument("--out", help="output directory (default: the compare directory)")
    ap.add_argument("--figures", default=os.path.join(REPO, "figures"))
    ap.add_argument("--loaded-check", help="results/loaded/<campaign>: check the damage "
                    "terms on the Chunk 3 LR2 / LC1 pairs")
    a = ap.parse_args()
    if a.loaded_check:
        loaded_check(a.loaded_check)
        if not a.compare:
            return
    if not a.compare:
        sys.exit("--compare is required (or --loaded-check alone)")
    out = a.out or a.compare
    os.makedirs(out, exist_ok=True)
    synthetic = any(os.path.exists(os.path.join(d, "SYNTHETIC")) for d in
                    (a.compare, a.damage or "") if d)
    figdir = out if synthetic else a.figures
    os.makedirs(figdir, exist_ok=True)

    runs, flows, excl = load_compare(a.compare)
    ctrls = controllers_in(runs)
    L = ["# Chunk 4: controller comparison, %s" % os.path.basename(os.path.abspath(a.compare)), ""]
    if synthetic:
        L += ["**SYNTHETIC FIXTURE (analysis/chunk4_fixture.py): every number below is made "
              "up to test the script. Not a result. Figures stay in this directory.**", ""]
    L += ["Generated by `analysis/chunk4.py` from runs.csv / flows.csv / links.csv "
          "(experiments/metrics.py)%s. Plan: LOG.md, 2026-10-19 Chunk 4 part 2. "
          "Mean ± 95 %% CI (t, n - 1 df) over runs; contrasts paired by seed." %
          (" and the replay pairs in %s" % a.damage if a.damage else ""), ""]
    L += ["Controllers: " + ", ".join("%s (%s, n = %d)" % (CTRL_LABEL.get(c, c), c,
                                                          (runs.controller == c).sum())
                                     for c in ctrls) + ".", ""]
    if excl:
        L += ["Excluded runs (recorded error or an elephant that did not finish):"] + \
             ["- " + x for x in excl] + [""]
    else:
        L += ["No run excluded.", ""]
    missing = [k for k, _, _ in METRICS if runs[k].isna().all()]
    if missing:
        L += ["Columns absent from this runs.csv (older metrics.py): " +
              ", ".join(missing) + ".", ""]
    seeds = runs.groupby("controller").seed.apply(lambda s: sorted(s))
    if len(ctrls) > 1 and len(set(map(tuple, seeds))) > 1:
        L += ["Seeds differ between controllers; paired contrasts use the common seeds only.", ""]

    L += ["## Across runs", ""]
    summ, T1 = per_controller_table(runs, ctrls)
    L += T1
    summ.to_csv(os.path.join(out, "chunk4_summary.csv"), index=False, float_format="%.6g")
    L += ["", "## Pre-registered contrasts (paired by seed)", ""]
    con, T2 = contrasts_table(runs, ctrls)
    L += T2
    con.to_csv(os.path.join(out, "chunk4_contrasts.csv"), index=False, float_format="%.6g")

    events = None
    if a.damage:
        events, dex = load_damage(a.damage, a.compare)
        L += ["", "## Per-reroute damage (matched move / no-move replays, %s)" %
              os.path.basename(os.path.abspath(a.damage)), ""]
        if dex:
            L += ["Pairs excluded (missing arm, error, or captures not on the intended "
                  "spines):"] + ["- " + x for x in dex] + [""]
        if len(events):
            L += damage_tables(events, ctrls)
            events.to_csv(os.path.join(out, "chunk4_events.csv"), index=False,
                          float_format="%.6g")
        else:
            L += ["No analysable pair.", ""]

    L += ["", "## Per run", ""] + per_run_table(runs, ctrls)
    figs = []
    p = os.path.join(figdir, "chunk4_comparison.png")
    fig_comparison(runs, ctrls, p)
    figs.append(p)
    if events is not None and len(events):
        p = os.path.join(figdir, "chunk4_damage.png")
        fig_damage(events, ctrls, p)
        figs.append(p)
    p = os.path.join(figdir, "chunk4_tradeoff.png")
    fig_tradeoff(runs, events, ctrls, p)
    figs.append(p)
    L += ["", "Figures: " + ", ".join(os.path.relpath(f, REPO) for f in figs), ""]
    open(os.path.join(out, "chunk4_summary.md"), "w").write("\n".join(L))
    print("\n".join(L[:len(T1) + 12]))
    print("...\nwrote %s and %s" % (os.path.join(out, "chunk4_summary.md"), ", ".join(figs)))


if __name__ == "__main__":
    main()
