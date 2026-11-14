#!/usr/bin/env python3
"""Chunk 7a: damage-regime campaign. How much goodput does one reroute cost
the destination path, per condition (rate, congestion control, RACK, spine
delays, move direction, load on the destination), and does any condition
have real damage by the pre-registered rule (LOG.md, 2026-11-13)?

Inputs: results/regime/regime-<rate>-<cc>-rack<r>-<D> (design campaigns from
scripts/chunk7_campaign.sh, labelled with analysis/damage_dataset.py's
design_pair: deficit = no-move minus move), results/regime/flap-* (repeated
moves), results/regime/feas-* (feasibility runs).

  .venv/bin/python analysis/regime.py --root results/regime \\
      --out results/regime/summary --fig figures/regime_damage.png
writes <out>/regime_cells.csv, regime_cells.md (with the flap and feasibility
tables), regime_rows.csv (one row per trial) and flap_rows.csv.
"""

import argparse
import glob
import json
import os
import sys

import numpy as np
import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
from analysis.damage_dataset import (_capture_spines, _design_runs, build_design,  # noqa: E402
                                     goodput_points, LATE_START_S)
from analysis.gate import mean_ci  # noqa: E402

CC_KEY, RACK_KEY = "net.ipv4.tcp_congestion_control", "net.ipv4.tcp_recovery"
DAMAGE_FLOOR = 0.01          # pre-registered: >= 1 % of the link rate over W10
FLAP_SPAN = 10.0


# ------------------------------------------------------------------ rule ----
def verdict(mean, half, link_mbps, floor=DAMAGE_FLOOR):
    """Pre-registered rule: damage if the 95 % CI lies above zero and the mean
    is at least `floor` of the link rate (both in Mbit/s)."""
    if mean is None or np.isnan(mean) or np.isnan(half):
        return "n/a"
    if mean - half > 0 and mean >= floor * link_mbps:
        return "DAMAGE"
    if mean - half > 0:
        return "CI > 0, below 1 %"
    return "no damage"


def to_mbps(bytes_, w):
    return np.asarray(bytes_, float) * 8 / w / 1e6


def sysctl_ok(meta):
    """Every host namespace read back the sysctls the schedule asked for."""
    want = meta.get("sysctl_set") or {}
    hosts = meta.get("sysctl_hosts") or {}
    if not want or not hosts:
        return False
    return all(v.get(k) == str(x) for v in hosts.values() for k, x in want.items()
               if k in (CC_KEY, RACK_KEY))


def setting_of(meta, mbps):
    s = meta.get("sysctl_set") or {}
    d = meta["spine_delay_ms"]
    return dict(rate=float(mbps), cc=s.get(CC_KEY, "?"), rack=int(s.get(RACK_KEY, -1)),
                delays="/".join("%g" % x for x in d), delay_set="D2" if max(d) > 10 else "D1")


# ---------------------------------------------------------------- design ----
def load_campaign(cdir):
    plan = json.load(open(os.path.join(cdir, "plan.json")))
    df = build_design(cdir)
    runs = _design_runs(cdir)
    st, bad = None, {}
    for k, arms in runs.items():
        for arm, (rd, _) in arms.items():
            m = json.load(open(os.path.join(rd, "meta.json")))
            st = st or setting_of(m, plan["mbps"])
            if not sysctl_ok(m):
                bad[k] = "%s: sysctls not read back as set (%s)" % (arm, m.get("sysctl_hosts"))
    for k, why in bad.items():                 # pre-registered exclusion, added in Chunk 7a
        i = df.index[df.trial == k]
        df.loc[i, "excluded"] = df.loc[i, "excluded"].fillna("").map(
            lambda x: (x + "; " if x else "") + why)
        df.loc[i, "replay_ok"] = 0
    for k, v in (st or {}).items():
        df[k] = v
    df["campaign"] = os.path.basename(os.path.abspath(cdir))
    return df


def direction(gap):
    return "slow->fast" if gap < 0 else "fast->slow" if gap > 0 else "same"


def cells(df):
    """One row per (setting, direction, elephants on the destination)."""
    out = []
    df = df.copy()
    df["direction"] = df.delay_gap_design_ms.map(direction)
    keys = ["rate", "cc", "rack", "delay_set", "delays", "direction", "n_q_design"]
    for key, g in df.groupby(keys, sort=False):
        r = dict(zip(keys, key))
        ok = g[g.replay_ok == 1]
        link = r["rate"]
        r.update(n=len(ok), excluded=int((g.replay_ok != 1).sum()))
        for t in ("dest_total", "moved"):
            for w in (2, 10):
                b = pd.to_numeric(ok["%s_deficit_W%d" % (t, w)], errors="coerce").to_numpy()
                m, h = mean_ci(to_mbps(b, w))
                r["%s_W%d_mbps" % (t, w)], r["%s_W%d_ci" % (t, w)] = m, h
                r["%s_W%d_pct" % (t, w)] = 100 * m / link
                r["%s_W%d_pct_ci" % (t, w)] = 100 * h / link
                mb, hb = mean_ci(b)
                r["%s_W%d_bytes" % (t, w)], r["%s_W%d_bytes_ci" % (t, w)] = mb, hb
        for k in ("reord_seen", "retrans", "dsack_dups"):
            for arm in ("T", "C"):
                r["%s_W2_%s" % (k, arm)] = ok["ss_%s_W2_%s" % (k, arm)].mean() if len(ok) else np.nan
        r["reorder_trials_T"] = int((ok.ss_reord_seen_W2_T > 0).sum()) if len(ok) else 0
        r["verdict"] = verdict(r["dest_total_W10_mbps"], r["dest_total_W10_ci"], link)
        r["realistic"] = int(r["rack"] == 1)
        out.append(r)
    return pd.DataFrame(out)


# ------------------------------------------------------------------ flap ----
def flap_runs(fdir):
    out = {}
    for rd in sorted(glob.glob(os.path.join(fdir, "runs", "*"))):
        if not os.path.exists(os.path.join(rd, "run_flows.csv")):
            continue
        s = json.load(open(os.path.join(rd, "schedule.json")))
        out.setdefault(s["design"]["trial"], {})[s["design"]["arm"]] = rd
    return out


def flap_arm(rd):
    """(E1 bytes over FLAP_SPAN from the first move's barrier reply, retrans
    over it, reason or '')."""
    from analysis.chunk4 import ss_delta, ss_socket, start_lag
    m = json.load(open(os.path.join(rd, "meta.json")))
    s = json.load(open(os.path.join(rd, "schedule.json")))
    des = s["design"]
    if m.get("errors"):
        return None, None, "run error: %s" % m["errors"]
    if not sysctl_ok(m):
        return None, None, "sysctls not read back as set"
    mv = m.get("moves") or []
    if len(mv) != des["n_moves"] or not all(x["reply"].get("ok") for x in mv):
        return None, None, "%d of %d moves ok" % (sum(bool(x["reply"].get("ok")) for x in mv),
                                                  des["n_moves"])
    want = [des["p"]] if des["arm"] == "C" else sorted({des["p"], des["q"]})
    got = _capture_spines(rd).get("E1")
    if got != want:
        return None, None, "captures: E1 on %s, expected %s" % (got, want)
    t1 = mv[0]["reply"]["t_barrier"] - m["t0"]
    e1 = s["elephants"][0]
    if not e1["start"] + start_lag(rd, "E1", s) <= t1 - LATE_START_S:
        return None, None, "late start"
    pts = goodput_points(rd, "E1", t1, windows=(FLAP_SPAN,))
    if pts is None:
        return None, None, "no receiver trace"
    smp = ss_socket(rd, e1["src"], e1["sport"], m["t0"])
    rtx = ss_delta(smp, t1, t1 + FLAP_SPAN)["retrans"] if smp else np.nan
    return pts[FLAP_SPAN], rtx, ""


def flap_rows(fdir):
    plan = json.load(open(os.path.join(fdir, "plan.json")))
    rows = []
    for k, arms in sorted(flap_runs(fdir).items()):
        base = {a: flap_arm(rd) for a, rd in arms.items()}
        for a in sorted(x for x in arms if x != "C"):
            gT, rT, why = base[a]
            gC, rC, whyC = base.get("C", (None, None, "missing no-move run"))
            why = "; ".join(x for x in (why, whyC and "C: " + whyC) if x)
            r = dict(campaign=os.path.basename(os.path.abspath(fdir)), rate=float(plan["mbps"]),
                     trial=k, period_s=float(a[1:]), excluded=why, replay_ok=int(not why))
            if not why:
                r.update(G_move_mbps=gT * 8 / FLAP_SPAN / 1e6, G_nomove_mbps=gC * 8 / FLAP_SPAN / 1e6,
                         share=gT / gC, deficit_bytes=gC - gT,
                         deficit_mbps=(gC - gT) * 8 / FLAP_SPAN / 1e6, retrans_move=rT,
                         retrans_nomove=rC)
            rows.append(r)
    return pd.DataFrame(rows)


def flap_cells(fr):
    out = []
    for (rate, per), g in fr.groupby(["rate", "period_s"]):
        ok = g[g.replay_ok == 1]
        r = dict(rate=rate, period_s=per, n=len(ok), excluded=int((g.replay_ok != 1).sum()))
        for c in ("share", "deficit_mbps", "deficit_bytes", "G_move_mbps", "G_nomove_mbps",
                  "retrans_move", "retrans_nomove"):
            r[c], r[c + "_ci"] = mean_ci(ok[c]) if len(ok) else (np.nan, np.nan)
        r["deficit_pct"] = 100 * r["deficit_mbps"] / rate
        r["deficit_pct_ci"] = 100 * r["deficit_mbps_ci"] / rate
        r["verdict"] = verdict(r["deficit_mbps"], r["deficit_mbps_ci"], rate)
        out.append(r)
    return pd.DataFrame(out)


# ----------------------------------------------------------- feasibility ----
def feasibility(root):
    rows = []
    for d in sorted(glob.glob(os.path.join(root, "feas-*"))):
        fp = os.path.join(d, "flows.csv")
        if not os.path.exists(fp):
            continue
        plan = json.load(open(os.path.join(d, "plan.json")))
        m = json.load(open(os.path.join(d, "runs", "alone", "meta.json")))
        fl = pd.read_csv(fp)
        st = setting_of(m, plan["mbps"])
        g = {r.id: r.goodput_steady_mbps for r in fl[fl.kind == "elephant"].itertuples()}
        rows.append(dict(st, setting=os.path.basename(d)[5:], slow_s4_mbps=g.get("E1"),
                         fast_s1_mbps=g.get("E2"), sysctl_ok=int(sysctl_ok(m)),
                         errors="; ".join(m["errors"])))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- output ----
def pm(m, h, f="%.3f"):
    if m is None or np.isnan(m):
        return "n/a"
    return (f % m) if np.isnan(h) else (f + " ± " + f) % (m, h)


def markdown(cl, fc, fe, rows, settings_missing):
    L = ["# Chunk 7a: damage per reroute by condition", "",
         "Generated by `analysis/regime.py`. Deficit = no-move minus move (positive = the move "
         "cost goodput), mean ± 95 % CI (t) over matched pairs, from each arm's own barrier "
         "reply. Destination-path total = the moved flow plus the elephant already on the "
         "destination (if any). Every trial has one other elephant on the source. Verdict "
         "(pre-registered, LOG.md 2026-11-13): DAMAGE if the W10 total's CI is above zero "
         "and its mean is at least 1 % of the link rate. Realistic = RACK on (Linux default).",
         ""]
    if settings_missing:
        L += ["Not run: " + "; ".join(settings_missing), ""]
    head = ["rate", "cc", "RACK", "delays (ms)", "move", "q load", "n", "total W2 Mbit/s",
            "total W10 Mbit/s", "total W10 % link", "total W10 kB", "moved W2 Mbit/s",
            "moved W10 Mbit/s", "reord. trials", "reord_seen W2 T", "retrans W2 T / C",
            "verdict"]
    L += ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    for r in cl.itertuples():
        L.append("| %g | %s | %s | %s | %s | %s | %d%s | %s | %s | %s | %s | %s | %s | %d/%d | %.1f | %.1f / %.1f | %s |" % (
            r.rate, r.cc, "on" if r.rack == 1 else "off", r.delays, r.direction,
            "empty" if r.n_q_design == 0 else "%d el." % r.n_q_design, r.n,
            " (%d excl.)" % r.excluded if r.excluded else "",
            pm(r.dest_total_W2_mbps, r.dest_total_W2_ci), pm(r.dest_total_W10_mbps, r.dest_total_W10_ci),
            pm(r.dest_total_W10_pct, r.dest_total_W10_pct_ci, "%.2f"),
            pm(r.dest_total_W10_bytes / 1e3, r.dest_total_W10_bytes_ci / 1e3, "%.0f"),
            pm(r.moved_W2_mbps, r.moved_W2_ci), pm(r.moved_W10_mbps, r.moved_W10_ci),
            r.reorder_trials_T, r.n, r.reord_seen_W2_T, r.retrans_W2_T, r.retrans_W2_C,
            r.verdict))
    dmg = cl[cl.verdict == "DAMAGE"]
    real = dmg[dmg.realistic == 1]
    L += ["", "Cells with damage by the rule: %d of %d (%d realistic, RACK on)." % (
        len(dmg), len(cl), len(real))]
    for r in dmg.itertuples():
        L.append("- %g Mbit/s, %s, RACK %s, delays %s, %s, %s: %.2f %% ± %.2f of the link at W10" % (
            r.rate, r.cc, "on" if r.rack == 1 else "off", r.delays, r.direction,
            "empty destination" if r.n_q_design == 0 else "loaded destination",
            r.dest_total_W10_pct, r.dest_total_W10_pct_ci))
    ex = rows[rows.replay_ok != 1]
    if len(ex):
        L += ["", "Excluded trials:"] + ["- %s trial %d: %s" % (r.campaign, int(r.trial), r.excluded)
                                         for r in ex.itertuples()]
    if len(fc):
        L += ["", "## Repeated moves (E1 alone, s1 <-> s4, D1, cubic, RACK on)", "",
              "E1's goodput over the 10 s from the first move, against the no-move run of the "
              "same seed (sham flow-mods at the same times).", "",
              "| rate | move every | n | goodput moved / no-move Mbit/s | share of no-move | "
              "deficit Mbit/s | deficit % link | retrans move / no-move | verdict |",
              "|---|---|---|---|---|---|---|---|---|"]
        for r in fc.itertuples():
            L.append("| %g | %g s | %d%s | %.2f / %.2f | %s | %s | %s | %.0f / %.0f | %s |" % (
                r.rate, r.period_s, r.n, " (%d excl.)" % r.excluded if r.excluded else "",
                r.G_move_mbps, r.G_nomove_mbps, pm(r.share, r.share_ci, "%.4f"),
                pm(r.deficit_mbps, r.deficit_mbps_ci), pm(r.deficit_pct, r.deficit_pct_ci, "%.2f"),
                r.retrans_move, r.retrans_nomove, r.verdict))
    if len(fe):
        L += ["", "## Feasibility (one run per setting, each elephant alone, steady goodput)", "",
              "| setting | E1 alone on s4 (slowest) Mbit/s | E2 alone on s1 Mbit/s | sysctls read back |",
              "|---|---|---|---|"]
        for r in fe.itertuples():
            L.append("| %s | %.1f (%.1f %%) | %.1f (%.1f %%) | %s |" % (
                r.setting, r.slow_s4_mbps, 100 * r.slow_s4_mbps / r.rate, r.fast_s1_mbps,
                100 * r.fast_s1_mbps / r.rate, "yes" if r.sysctl_ok else "NO"))
    return "\n".join(L) + "\n"


def figure(cl, fc, path):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    rates = sorted(cl.rate.unique(), reverse=True)
    n = len(rates) + (1 if len(fc) else 0)
    fig, axes = plt.subplots(n, 1, figsize=(11, 3.4 * n), squeeze=False)
    style = {("slow->fast", 0): ("#1f77b4", "o"), ("slow->fast", 1): ("#1f77b4", "s"),
             ("fast->slow", 0): ("#d62728", "o"), ("fast->slow", 1): ("#d62728", "s")}
    for ax, rate in zip(axes[:, 0], rates):
        g = cl[cl.rate == rate].reset_index(drop=True)
        sets = list(dict.fromkeys(zip(g.cc, g.rack, g.delay_set)))
        for i, (cc, rack, ds) in enumerate(sets):
            for j, (key, (c, mk)) in enumerate(style.items()):
                r = g[(g.cc == cc) & (g.rack == rack) & (g.delay_set == ds) &
                      (g.direction == key[0]) & (g.n_q_design == key[1])]
                if not len(r):
                    continue
                r = r.iloc[0]
                x = i + (j - 1.5) * 0.17
                ax.errorbar(x, r.dest_total_W10_pct, yerr=r.dest_total_W10_pct_ci, fmt=mk,
                            color=c, mfc=c if key[1] else "white", capsize=3,
                            label=("%s, %s destination" % (key[0], "loaded" if key[1] else "empty"))
                            if i == 0 else None)
        ax.axhline(0, color="k", lw=0.8)
        ax.axhline(100 * DAMAGE_FLOOR, color="grey", ls="--", lw=0.8)
        ax.set_xticks(range(len(sets)))
        ax.set_xticklabels(["%s\nRACK %s\n%s" % (cc, "on" if rk == 1 else "off",
                                                 "1/1/5/10 ms" if ds == "D1" else "1/1/20/50 ms")
                            for cc, rk, ds in sets], fontsize=8)
        ax.set_ylabel("destination-path deficit\nover 10 s, % of link")
        ax.set_title("%g Mbit/s: goodput one reroute costs the destination path "
                     "(mean, 95 %% CI; dashed: 1 %% damage floor)" % rate, fontsize=10)
        ax.legend(fontsize=7, loc="upper left")
    if len(fc):
        ax = axes[-1, 0]
        lab = ["%g Mbit/s, every %g s" % (r.rate, r.period_s) for r in fc.itertuples()]
        ax.errorbar(range(len(fc)), fc.deficit_pct, yerr=fc.deficit_pct_ci, fmt="o", color="k",
                    capsize=3)
        ax.axhline(0, color="k", lw=0.8)
        ax.axhline(100 * DAMAGE_FLOOR, color="grey", ls="--", lw=0.8)
        ax.set_xticks(range(len(fc)))
        ax.set_xticklabels(lab, fontsize=8)
        ax.set_ylabel("E1 deficit, 10 s,\n% of link")
        ax.set_title("Repeated moves s1 <-> s4 for 10 s vs no move (cubic, RACK on)", fontsize=10)
    fig.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    fig.savefig(path, dpi=150)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--root", default="results/regime")
    ap.add_argument("--out", default="results/regime/summary")
    ap.add_argument("--fig", default="figures/regime_damage.png")
    a = ap.parse_args()
    camps = sorted(d for d in glob.glob(os.path.join(a.root, "regime-*"))
                   if os.path.exists(os.path.join(d, "plan.json")))
    rows = pd.concat([load_campaign(d) for d in camps], ignore_index=True)
    order = {"cubic": 0, "bbr": 1}
    cl = cells(rows)
    cl = cl.sort_values(["rate", "cc", "rack", "delay_set", "direction", "n_q_design"],
                        ascending=[False, True, False, True, False, True],
                        key=lambda s: s.map(order) if s.name == "cc" else s).reset_index(drop=True)
    fdirs = sorted(d for d in glob.glob(os.path.join(a.root, "flap-*"))
                   if os.path.exists(os.path.join(d, "plan.json")))
    fr = pd.concat([flap_rows(d) for d in fdirs], ignore_index=True) if fdirs else pd.DataFrame()
    fc = flap_cells(fr) if len(fr) else pd.DataFrame()
    if len(fc):
        fc = fc.sort_values(["rate", "period_s"], ascending=[False, True]).reset_index(drop=True)
    fe = feasibility(a.root)
    have = {os.path.basename(d)[7:] for d in camps}
    missing = ["%s-%s-rack%s-%s" % (r, cc, rk, ds) for r in (100, 10) for cc in ("cubic", "bbr")
               for rk in (1, 0) for ds in ("D1", "D2")
               if "%s-%s-rack%s-%s" % (r, cc, rk, ds) not in have]
    os.makedirs(a.out, exist_ok=True)
    cl.to_csv(os.path.join(a.out, "regime_cells.csv"), index=False, float_format="%.6g")
    rows.to_csv(os.path.join(a.out, "regime_rows.csv"), index=False, float_format="%.10g")
    if len(fr):
        fr.to_csv(os.path.join(a.out, "flap_rows.csv"), index=False, float_format="%.10g")
        fc.to_csv(os.path.join(a.out, "flap_cells.csv"), index=False, float_format="%.6g")
    fe.to_csv(os.path.join(a.out, "feasibility.csv"), index=False, float_format="%.6g")
    md = markdown(cl, fc, fe, rows, missing)
    open(os.path.join(a.out, "regime_cells.md"), "w").write(md)
    figure(cl, fc, a.fig)
    print(md)


if __name__ == "__main__":
    main()
