#!/usr/bin/env python3
"""Per-run metrics and the campaign CSVs (Chunk 3 onwards). Runs in the venv.

  .venv/bin/python experiments/metrics.py run <run_dir>      # after each run
  .venv/bin/python experiments/metrics.py collect <campaign> # rebuild CSVs

`run` reads only the raw files of one run (experiments/runner.py) and writes
  run_flows.csv    one row per flow: elephants, mice and any other TCP
                   connection seen on the uplinks (iperf3 control channels)
  run_links.csv    per uplink and direction: mean and peak utilisation
  run_summary.json the run's row in runs.csv
`collect` concatenates those into <campaign>/runs.csv, flows.csv, links.csv.
Nothing here edits raw data; rerunning gives the same CSVs.

Definitions
  spine of a flow   uplink of l1 that carried most of its data bytes (spine
                    captures); n_spines = uplinks that carried any of its data,
                    so n_spines > 1 means the flow changed path during the run
  ack_spine         uplink that carried most of the flow's reverse packets
  elephant goodput  bytes received by the iperf3 server / receiver-side seconds
  FCT               mice.py: just before connect() to the server's reply byte
  link utilisation  wire bytes in 100 ms bins / link rate, over the steady
                    window [3 s, duration - 1 s] after t0; max_link_util is the
                    largest per-uplink mean, peak_bin_util the largest bin
  fast / slow       spines 1-2 (1 ms) / spines 3-4 (5 and 10 ms one way)
"""

import glob
import gzip
import json
import os
import re
import sys

import numpy as np
import pandas as pd

FAST = {1, 2}
HOST_CTRS = ["TcpRetransSegs", "TcpExtTCPDSACKRecv", "TcpExtTCPSACKReorder",
             "TcpExtTCPTSReorder", "TcpExtTCPTimeouts", "TcpExtTCPSpuriousRTOs",
             "TcpExtTCPLossUndo", "TcpExtTCPDSACKUndo", "TcpExtTCPFastRetrans"]
RCV_CTRS = ["TcpExtTCPOFOQueue", "TcpExtTCPDSACKOldSent"]


def group_buckets(path):
    """{(switch, bucket): (packets, bytes)} from dump-group-stats."""
    out, sw = {}, None
    for line in open(path):
        m = re.match(r"== (\S+) dump-group-stats", line)
        if m:
            sw = m.group(1)
            continue
        if line.startswith("=="):
            sw = None
        if sw and "group_id=" in line:
            for b, p, by in re.findall(r"bucket(\d+):packet_count=(\d+),byte_count=(\d+)",
                                       line):
                out[(sw, int(b))] = (int(p), int(by))
    return out


def iperf_end(path):
    if not os.path.exists(path) and os.path.exists(path + ".gz"):
        path += ".gz"
    try:
        j = json.load(gzip.open(path, "rt") if path.endswith(".gz") else open(path))
    except (ValueError, OSError):
        return None
    return j.get("end") or None


def run_metrics(rd):
    meta = json.load(open(os.path.join(rd, "meta.json")))
    sched = json.load(open(os.path.join(rd, "schedule.json")))
    D, mbps = sched["duration"], meta["mbps"]
    sf = pd.read_csv(os.path.join(rd, "spine_flows.csv"))
    up = sf[(sf.dir == "up") & (sf.payload_bytes > 0)]
    down = sf[sf.dir == "down"]
    key = ["src", "dst", "sport", "dport"]
    by_flow = {k: g for k, g in up.groupby(key)}
    by_rev = {k: g for k, g in down.groupby(key)}

    def paths(src, dst, sport, dport):
        g = by_flow.get((src, dst, sport, dport))
        r = by_rev.get((dst, src, dport, sport))
        res = dict(spine=np.nan, n_spines=0, ack_spine=np.nan, bytes_by_spine="")
        if g is not None:
            g = g.sort_values("payload_bytes", ascending=False)
            res.update(spine=int(g.spine.iloc[0]), n_spines=len(g),
                       bytes_by_spine=";".join("s%d:%d" % (s, b) for s, b in
                                               zip(g.spine, g.payload_bytes)))
        if r is not None:
            res["ack_spine"] = int(r.sort_values("pkts", ascending=False).spine.iloc[0])
        return res

    rows, known = [], set()
    for e in sched["elephants"]:
        end = iperf_end(os.path.join(rd, "iperf_client_%s.json" % e["id"]))
        row = dict(kind="elephant", id=e["id"], src=e["src"], dst=e["dst"],
                   src_ip=e["src_ip"], dst_ip=e["dst_ip"], sport=e["sport"],
                   dport=e["dport"], start=e["start"], size=np.nan, ok=int(end is not None))
        if end is not None:
            rcv = end["sum_received"]
            row.update(goodput_mbps=rcv["bytes"] * 8 / rcv["seconds"] / 1e6,
                       bytes=rcv["bytes"], retrans=end["sum_sent"].get("retransmits"))
        row.update(paths(e["src_ip"], e["dst_ip"], e["sport"], e["dport"]))
        rows.append(row)
        known.add((e["src_ip"], e["dst_ip"], e["sport"], e["dport"]))
    mice = {m["id"]: m for m in sched["mice"]}
    res = {}
    for p in glob.glob(os.path.join(rd, "mice_h*.csv")):
        for r in pd.read_csv(p, keep_default_na=False).to_dict("records"):
            res[r["id"]] = r
    for mid, m in mice.items():
        r = res.get(mid, {})
        row = dict(kind="mouse", id=mid, src=m["src"], dst=m["dst"], src_ip=m["src_ip"],
                   dst_ip=m["dst_ip"], sport=m["sport"], dport=m["dport"], start=m["start"],
                   size=m["size"], ok=int(r.get("ok", 0) == 1),
                   fct_s=float(r["fct_s"]) if r.get("fct_s") not in (None, "") else np.nan,
                   connect_s=float(r["connect_s"]) if r.get("connect_s") not in (None, "")
                   else np.nan,
                   start_lag_s=float(r["t_start"]) - float(r["t_sched"])
                   if r.get("t_start") not in (None, "") else np.nan,
                   error=r.get("error", "missing" if not r else ""))
        row.update(paths(m["src_ip"], m["dst_ip"], m["sport"], m["dport"]))
        rows.append(row)
        known.add((m["src_ip"], m["dst_ip"], m["sport"], m["dport"]))
    for k in by_flow:
        if k not in known:
            row = dict(kind="other", id="", src_ip=k[0], dst_ip=k[1], sport=k[2], dport=k[3])
            row.update(paths(*k))
            rows.append(row)
    flows = pd.DataFrame(rows)
    for c, v in (("run_id", meta["run_id"]), ("controller", meta["controller"]),
                 ("scenario", sched["scenario"]), ("condition", sched["condition"]),
                 ("seed", sched["seed"])):
        flows.insert(0, c, v)
    flows.to_csv(os.path.join(rd, "run_flows.csv"), index=False, float_format="%.6g")

    bins = pd.read_csv(os.path.join(rd, "spine_bins.csv"))
    nb = int(round((D - 1 - 3) / 0.1))
    lrows = []
    for k in range(1, 5):
        for d in ("up", "down"):
            b = bins[(bins.spine == k) & (bins.dir == d) & (bins.t_bin >= 3) &
                     (bins.t_bin < D - 1)]
            u = np.zeros(nb)
            idx = np.round((b.t_bin.to_numpy() - 3) / 0.1).astype(int)
            ok = (idx >= 0) & (idx < nb)
            u[idx[ok]] = b.wire_bytes.to_numpy()[ok] * 8 / 0.1 / (mbps * 1e6)
            lrows.append(dict(run_id=meta["run_id"], controller=meta["controller"],
                              scenario=sched["scenario"], condition=sched["condition"],
                              seed=sched["seed"], spine=k, dir=d, util_mean=u.mean(),
                              util_p99_bin=np.percentile(u, 99), util_max_bin=u.max()))
    links = pd.DataFrame(lrows)
    links.to_csv(os.path.join(rd, "run_links.csv"), index=False, float_format="%.6g")

    el = flows[flows.kind == "elephant"]
    mi = flows[flows.kind == "mouse"]
    ok = mi[mi.ok == 1]
    s = dict(run_id=meta["run_id"], controller=meta["controller"],
             scenario=sched["scenario"], condition=sched["condition"], seed=sched["seed"],
             schedule_sha256=sched["sha256"], errors=" | ".join(meta.get("errors", [])),
             n_elephants=len(el), elephants_ok=int(el.ok.sum()))
    if len(el) and "goodput_mbps" in el:
        g = el.goodput_mbps
        s.update(eleph_goodput_mean=g.mean(), eleph_goodput_min=g.min(),
                 eleph_goodput_max=g.max(), eleph_goodput_sum=g.sum(),
                 eleph_retrans_sum=el.retrans.sum())
        per = el.spine.value_counts()
        s.update(elephant_spines=";".join("%s:s%d" % (i, sp) for i, sp in
                                          zip(el.id, el.spine.fillna(0).astype(int))),
                 uplinks_used_by_elephants=int(el.spine.nunique()),
                 max_elephants_per_uplink=int(per.max()) if len(per) else 0)
    s["flows_with_path_change"] = int((flows.n_spines > 1).sum())
    s["elephants_with_path_change"] = int((el.n_spines > 1).sum())
    s["flows_ack_spine_ne_data_spine"] = int(((flows.ack_spine != flows.spine) &
                                              flows.ack_spine.notna() &
                                              flows.spine.notna()).sum())
    s.update(n_mice=len(mi), mice_ok=int(mi.ok.sum()), mice_failed=int((mi.ok == 0).sum()),
             mice_no_spine=int(mi.spine.isna().sum()) if len(mi) else 0,
             mice_start_lag_p99_ms=float(np.nanpercentile(mi.start_lag_s, 99) * 1e3)
             if len(mi) and mi.start_lag_s.notna().any() else np.nan)
    for lab, sub in (("", ok), ("_fast", ok[ok.spine.isin(FAST)]),
                     ("_slow", ok[ok.spine.isin({3, 4})])):
        s["n_mice_ok" + lab] = len(sub)
        if len(sub):
            s["fct_p50_ms" + lab] = float(np.percentile(sub.fct_s, 50) * 1e3)
            s["fct_p99_ms" + lab] = float(np.percentile(sub.fct_s, 99) * 1e3)
            s["fct_mean_ms" + lab] = float(sub.fct_s.mean() * 1e3)
    for k in range(1, 5):
        n = int((mi.spine == k).sum()) if len(mi) else 0
        s["mice_on_s%d" % k] = n
    lu = links[links.dir == "up"].set_index("spine")
    for k in range(1, 5):
        s["util_up_s%d" % k] = lu.loc[k, "util_mean"]
    s["max_link_util"] = lu.util_mean.max()
    s["peak_bin_util"] = lu.util_max_bin.max()
    c = json.load(open(os.path.join(rd, "counters.json")))
    hpl = meta["hosts_per_leaf"]
    for name in HOST_CTRS:
        s["snd_" + name] = sum(c["end"][h].get(name, 0) - c["start"][h].get(name, 0)
                               for h in sched["hosts"] if int(h[1:]) <= hpl)
    for name in RCV_CTRS:
        s["rcv_" + name] = sum(c["end"][h].get(name, 0) - c["start"][h].get(name, 0)
                               for h in sched["hosts"] if int(h[1:]) > hpl)
    gb = group_buckets(os.path.join(rd, "ovs_stats.txt"))
    for (sw, b), (p, by) in sorted(gb.items()):
        s["group_%s_bucket%d_bytes" % (sw, b)] = by
    tot = sum(by for (sw, b), (p, by) in gb.items() if sw == "l1")
    s["group_l1_max_bucket_share"] = (max(by for (sw, b), (p, by) in gb.items()
                                          if sw == "l1") / tot) if tot else np.nan
    json.dump({k: (None if isinstance(v, float) and np.isnan(v) else
                   (v.item() if hasattr(v, "item") else v)) for k, v in s.items()},
              open(os.path.join(rd, "run_summary.json"), "w"), indent=1)
    return s


def collect(camp):
    runs, flows, links = [], [], []
    for rd in sorted(glob.glob(os.path.join(camp, "runs", "*"))):
        p = os.path.join(rd, "run_summary.json")
        if not os.path.exists(p):
            continue
        runs.append(json.load(open(p)))
        flows.append(pd.read_csv(os.path.join(rd, "run_flows.csv")))
        links.append(pd.read_csv(os.path.join(rd, "run_links.csv")))
    if not runs:
        sys.exit("no completed runs in %s" % camp)
    pd.DataFrame(runs).to_csv(os.path.join(camp, "runs.csv"), index=False,
                              float_format="%.6g")
    pd.concat(flows).to_csv(os.path.join(camp, "flows.csv"), index=False,
                            float_format="%.6g")
    pd.concat(links).to_csv(os.path.join(camp, "links.csv"), index=False,
                            float_format="%.6g")
    print("%s: %d runs -> runs.csv, flows.csv, links.csv" % (camp, len(runs)))


def main():
    if len(sys.argv) != 3 or sys.argv[1] not in ("run", "collect"):
        sys.exit(__doc__)
    if sys.argv[1] == "run":
        run_metrics(sys.argv[2])
    else:
        collect(sys.argv[2])


if __name__ == "__main__":
    main()
