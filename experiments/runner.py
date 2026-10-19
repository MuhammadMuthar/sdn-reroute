#!/usr/bin/env python3
"""Reusable experiment runner (Chunk 3 onwards; Chunk 2's gate.py was its
first version and stays as it was so the gate can be rerun).

One *run* = fresh network + fresh controller + one seeded traffic schedule
(experiments/traffic.py): iperf3 elephants with fixed --cport, short flows
from experiments/mice.py, optional pins and deliberate moves through the
controller's command channel. Per run it records
  - schedule.json (and its sha256), meta.json (pins, moves with flow-mod
    timing, sysctls of h1, tc setup, errors), counters.json (TCP MIB counters
    of every host, i.e. what nstat reads, at start/end and around moves)
  - iperf3 client and server JSON per elephant (100 ms intervals)
  - mice_<host>.csv: one row per short flow with its completion time
  - ss -tin of the elephant sockets on every elephant sender (50-100 ms)
  - header captures on the four uplinks of l1 (path oracle, link
    utilisation; experiments/spinetrace.py) and on both NICs of each
    "tagged" elephant (packet traces, experiments/pcaptrace.py)
  - group stats, flow and port stats of every switch, tc -s qdisc
  - the controller's own log and its list of pins/moves (events)
Then experiments/metrics.py (venv) turns the run into CSV rows and rebuilds
the campaign CSVs.

Usage (repo root; as a normal user every run goes through scripts/userns.sh,
under sudo it runs on the system OVS with `mn -c` first):
  python3 experiments/runner.py campaign --controller ecmp --scenario ecmp_mixed \\
      --runs 10 --seed 1 --out results/ecmp/<name>
  python3 experiments/runner.py campaign --controller ecmp --scenario loaded_move \\
      --conditions IC1,IR2,LC1,LR2,LR0 --runs 10 --seed 1 --out results/loaded/<name>
  python3 experiments/runner.py campaign --controllers ecmp,ecmp_mon,hedera,cooldown \\
      --scenario ecmp_mixed --runs 10 --seed 1 --out results/compare/<name>
  python3 experiments/runner.py replay --spec <jobs.json> --out results/damage/<name>
Run r of a campaign uses seed + r - 1; with --conditions (and/or --controllers)
every condition and controller of rep r uses that same seed (paired), in a
block-randomised order. `replay` runs fully specified schedules (matched move /
no-move pairs built by experiments/replay.py).
"""

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

from experiments.gate import SYSCTLS, read_counters, step, wait_port  # noqa: E402

VENV_PY = os.path.join(REPO, ".venv", "bin", "python")
OF_PORT, CMD_PORT = 6653, 6700
SNAPLEN_UPLINK, SNAPLEN_HOST = 96, 128
LOCAL_PORT_RANGE = "41000 60999"


def ctl_cmd(req, timeout=10.0):
    with socket.create_connection(("127.0.0.1", CMD_PORT), timeout=timeout) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(1 << 20)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf)


def tcp_counters(host):
    return {k: v for k, v in read_counters(host).items() if k.startswith("Tcp")}


def udp9_frame():
    """Minimal Ethernet/IPv4/UDP frame to port 9: the packet that makes a
    `tcpdump -G/-W 1` capture on a switch port close its file and exit."""
    payload = b"end!"
    udp = struct.pack("!HHHH", 9, 9, 8 + len(payload), 0) + payload
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 0, 0, 1, 17, 0,
                     bytes([10, 0, 0, 254]), bytes([10, 0, 0, 253]))
    s = sum(struct.unpack("!10H", ip))
    s = (s & 0xFFFF) + (s >> 16)
    ip = ip[:10] + struct.pack("!H", ~s & 0xFFFF) + ip[12:]
    return b"\xff" * 6 + b"\x02\x00\x00\x00\x00\xfe" + b"\x08\x00" + ip + udp


def inject(ifname):
    with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as s:
        s.bind((ifname, 0))
        s.send(udp9_frame())


def run(a):
    import faulthandler
    from mininet.log import setLogLevel
    from experiments.traffic import flow_tuple, reverse_tuple
    from topologies.layout import DEFAULT_SPINE_DELAY_MS
    from topologies.leafspine import build_net, start_net

    setLogLevel("warning")
    rd = os.path.abspath(a.run_dir)
    sched = json.load(open(os.path.join(rd, "schedule.json")))
    D = sched["duration"]
    faulthandler.dump_traceback_later(D + 150, exit=True)    # never hang a campaign
    hpl = len(sched["hosts"]) // 2
    meta = dict(run_id=os.path.basename(rd), controller=a.controller,
                scenario=sched["scenario"], condition=sched["condition"],
                seed=sched["seed"], duration_s=D, queue_pkts=a.queue, mbps=a.mbps,
                host_qdisc=a.host_qdisc,
                hosts_per_leaf=hpl, spine_delay_ms=list(DEFAULT_SPINE_DELAY_MS),
                uid=os.getuid(), userns=os.environ.get("SDN_USERNS_INNER") == "1",
                kernel=os.uname().release, t_wall_start=time.time(), errors=[],
                boot_id=open("/proc/sys/kernel/random/boot_id").read().strip())
    ctl_log = open(os.path.join(rd, "controller.log"), "w")
    ctl = subprocess.Popen([VENV_PY, os.path.join(REPO, "controllers", "run.py"),
                            "--ofp-tcp-listen-port", str(OF_PORT),
                            os.path.join(REPO, "controllers", a.controller + ".py")],
                           stdout=ctl_log, stderr=subprocess.STDOUT,
                           env=dict(os.environ, FABRIC_CMD_PORT=str(CMD_PORT),
                                    FABRIC_HOSTS_PER_LEAF=str(hpl), FABRIC_LOG_DIR=rd))
    bg, dumps, clients = [], [], []
    counters = {}
    net = None
    try:
        if not (wait_port(OF_PORT, 20) and wait_port(CMD_PORT, 5)):
            raise RuntimeError("controller did not start, see controller.log")
        step("controller up")
        net = build_net(hosts_per_leaf=hpl, controller_port=OF_PORT)
        meta["tc_setup"] = start_net(net, mbps=a.mbps, queue=a.queue,
                                     host_qdisc=a.host_qdisc)
        for _ in range(100):
            st = ctl_cmd({"cmd": "status"})
            if st.get("ready"):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("switches not configured: %s" % st)
        meta["controller_app"] = st.get("app")
        step("switches configured")
        H = {name: net.get(name) for name in sched["hosts"]}
        left = sorted(n for n in H if int(n[1:]) <= hpl)
        right = sorted(n for n in H if int(n[1:]) > hpl)
        # reachability: every left host to its mirror on the right
        meta["ping"] = {}
        for i, s in enumerate(left):
            d = right[i % len(right)]
            out = H[s].cmd("ping -c 2 -i 0.2 %s" % H[d].IP())
            meta["ping"]["%s-%s" % (s, d)] = " 0% packet loss" in out
            if " 0% packet loss" not in out:
                raise RuntimeError("ping %s -> %s failed:\n%s" % (s, d, out))
        for h in H.values():
            h.cmd("ip tcp_metrics flush all")
            # ephemeral ports (iperf3 control connections) stay out of the
            # elephant (40001-40999) and mice (20000-39999) source-port ranges
            h.cmd("sysctl -qw net.ipv4.ip_local_port_range='%s'" % LOCAL_PORT_RANGE)
        meta["ip_local_port_range"] = H["h1"].cmd(
            "sysctl -n net.ipv4.ip_local_port_range").strip()
        meta["sysctl"] = {k: H["h1"].cmd("sysctl -n %s 2>&1" % k).strip() for k in SYSCTLS}
        step("ping ok, metrics flushed")

        eleph = {e["id"]: e for e in sched["elephants"]}
        meta["pins"] = []
        for p in sched["pins"]:
            e = eleph[p["flow"]]
            r = ctl_cmd({"cmd": "pin", "flow": flow_tuple(e), "spine": p["spine"]})
            meta["pins"].append(dict(p, dir="data", reply=r))
            if sched.get("ack_spine"):
                r2 = ctl_cmd({"cmd": "pin", "flow": reverse_tuple(e),
                              "spine": sched["ack_spine"]})
                meta["pins"].append(dict(flow=p["flow"], spine=sched["ack_spine"],
                                         dir="ack", reply=r2))
            if not all(x["reply"].get("ok") for x in meta["pins"]):
                raise RuntimeError("pin failed: %s" % meta["pins"])
        for sw in ("l1", "l2"):
            meta["groups_" + sw] = net.get(sw).cmd(
                "ovs-ofctl -O OpenFlow13 dump-groups %s" % sw)

        # ---- captures (each stops itself, see udp9_frame) ------------------
        cap_s = D + 8
        t_dump = time.time()
        for k in range(1, 5):
            ifn = "l1-s%d" % k
            dumps.append(subprocess.Popen(
                ["tcpdump", "-i", ifn, "-s", str(SNAPLEN_UPLINK), "-B", "32768", "-U",
                 "-Z", "root", "-G", str(cap_s), "-W", "1",
                 "-w", os.path.join(rd, "uplink_s%d.pcap" % k), "tcp or udp port 9"],
                stdout=subprocess.DEVNULL,
                stderr=open(os.path.join(rd, "uplink_s%d.pcap.log" % k), "w")))
        host_caps = []
        for fid in sched["tagged"]:
            e = eleph[fid]
            for side, hn in (("send", e["src"]), ("recv", e["dst"])):
                name = "%s_%s.pcap" % (side, fid)
                dumps.append(H[hn].popen(
                    ["tcpdump", "-i", "%s-eth0" % hn, "-s", str(SNAPLEN_HOST), "-B",
                     "16384", "-U", "-Z", "root", "-G", str(cap_s), "-W", "1",
                     "-w", os.path.join(rd, name),
                     "tcp port %d or udp port 9" % e["sport"]],
                    stdout=subprocess.DEVNULL,
                    stderr=open(os.path.join(rd, name + ".log"), "w")))
                host_caps.append((e, hn))
        ss_period = 0.05 if sched["tagged"] else 0.1
        for hn in sorted({e["src"] for e in sched["elephants"]}):
            filt = "( %s )" % " or ".join("sport = :%d" % e["sport"]
                                          for e in sched["elephants"] if e["src"] == hn)
            bg.append(H[hn].popen(
                "while :; do echo T $(date +%%s.%%N); ss -tinmH '%s'; sleep %g; done"
                % (filt, ss_period), shell=True,
                stdout=open(os.path.join(rd, "ss_%s.log" % hn), "w"),
                stderr=subprocess.DEVNULL))

        # ---- servers ---------------------------------------------------------
        servers = []
        for e in sched["elephants"]:
            servers.append(H[e["dst"]].popen(
                ["iperf3", "-s", "-1", "-J", "-i", "0.1", "-p", str(e["dport"])],
                stdout=open(os.path.join(rd, "iperf_server_%s.json" % e["id"]), "w"),
                stderr=subprocess.STDOUT))
        mice_dsts = sorted({m["dst"] for m in sched["mice"]})
        mice_srcs = sorted({m["src"] for m in sched["mice"]})
        for hn in mice_dsts:
            bg.append(H[hn].popen(["python3", os.path.join(REPO, "experiments", "mice.py"),
                                   "serve", "--port", "8000"],
                                  stdout=subprocess.DEVNULL,
                                  stderr=open(os.path.join(rd, "mice_server_%s.log" % hn),
                                              "w")))
        time.sleep(1.0)
        counters["start"] = {"t": time.time(), **{n: tcp_counters(h) for n, h in H.items()}}
        t0 = time.time() + 1.0           # mice clients start their python before t0
        meta["t0"] = t0
        for hn in mice_srcs:
            clients.append(H[hn].popen(
                ["python3", os.path.join(REPO, "experiments", "mice.py"), "client",
                 "--schedule", os.path.join(rd, "schedule.json"), "--host", hn,
                 "--t0", "%.6f" % t0, "--out", os.path.join(rd, "mice_%s.csv" % hn)],
                stdout=subprocess.DEVNULL,
                stderr=open(os.path.join(rd, "mice_client_%s.log" % hn), "w")))
        step("captures and servers started")

        # ---- timeline: elephant starts, then moves with counter snapshots ----
        def snap(name):
            counters[name] = {"t": time.time(),
                              **{n: tcp_counters(h) for n, h in H.items()}}

        meta["elephant_start"] = {}
        for e in sorted(sched["elephants"], key=lambda e: e["start"]):
            time.sleep(max(0.0, t0 + e["start"] - time.time()))
            meta["elephant_start"][e["id"]] = time.time()
            clients.append(H[e["src"]].popen(
                ["iperf3", "-c", e["dst_ip"], "-p", str(e["dport"]), "-t", str(e["duration"]),
                 "--cport", str(e["sport"]), "-J", "-i", "0.1"],
                stdout=open(os.path.join(rd, "iperf_client_%s.json" % e["id"]), "w"),
                stderr=subprocess.STDOUT))
        step("elephants started")
        meta["moves"] = []
        # moves with the same `at` are one batch: their flow-mods go out back to
        # back (as a controller round does) and the snapshots bracket the batch
        batches = {}
        for mv in sched["moves"]:
            batches.setdefault(mv["at"], []).append(mv)
        for i, at in enumerate(sorted(batches)):
            for off in sorted(o for o in sched["snapshots"] if o < 0):
                time.sleep(max(0.0, t0 + at + off - time.time()))
                snap("m%d_%+g" % (i, off))
            time.sleep(max(0.0, t0 + at - time.time()))
            snap("m%d_pre" % i)
            tb = None
            for mv in batches[at]:
                e = eleph[mv["flow"]]
                r = ctl_cmd({"cmd": "move", "flow": flow_tuple(e), "spine": mv["spine"]})
                if not r.get("ok"):
                    meta["errors"].append("move failed: %s" % r)
                meta["moves"].append(dict(mv, reply=r, batch=i))
                tb = tb or r.get("t_barrier")
                step("moved %s -> s%d" % (mv["flow"], mv["spine"]))
            tb = tb or time.time()
            snap("m%d_post0" % i)                # counter windows can start here
            for off in sorted(o for o in sched["snapshots"] if o > 0):
                time.sleep(max(0.0, tb + off - time.time()))
                snap("m%d_%+g" % (i, off))
        for c in clients:
            c.wait(timeout=D + 45)
        for s in servers:
            try:
                s.wait(timeout=10)
            except subprocess.TimeoutExpired:
                meta["errors"].append("iperf3 server did not exit")
        snap("end")
        step("clients done")
        meta["t_clients_end"] = time.time()

        # ---- stop the captures ----------------------------------------------
        time.sleep(max(0.0, t_dump + cap_s + 0.3 - time.time()))
        for _ in range(40):
            for k in range(1, 5):
                inject("l1-s%d" % k)
            for e, hn in host_caps:
                H[e["src"]].cmd("echo end > /dev/udp/%s/9" % e["dst_ip"])
            if all(d.poll() is not None for d in dumps):
                break
            time.sleep(0.25)
        else:
            meta["errors"].append("tcpdump did not exit")

        step("captures stopped")
        meta["controller_events"] = ctl_cmd({"cmd": "events"}).get("events")
        with open(os.path.join(rd, "ovs_stats.txt"), "w") as f:
            for sw in net.switches:
                for what in ("dump-group-stats", "dump-flows", "dump-ports"):
                    if what == "dump-group-stats" and not sw.name.startswith("l"):
                        continue
                    f.write("== %s %s\n%s" % (sw.name, what, sw.cmd(
                        "ovs-ofctl -O OpenFlow13 %s %s" % (what, sw.name))))
        with open(os.path.join(rd, "tc_stats.txt"), "w") as f:
            for sw in net.switches:
                for intf in sw.intfList():
                    if intf.name != "lo":
                        f.write("== %s\n%s" % (intf.name, sw.cmd(
                            "tc -s qdisc show dev %s" % intf.name)))
    except Exception as exc:
        meta["errors"].append("%s: %s" % (type(exc).__name__, exc))
        raise
    finally:
        for p in bg + clients + [d for d in dumps if d.poll() is None]:
            try:
                if p.poll() is None:
                    p.send_signal(signal.SIGINT)
                    p.wait(timeout=5)
            except Exception:
                try:
                    p.kill()
                except OSError:
                    pass
        step("stats saved, stopping")
        if net is not None:
            net.stop()
        step("network stopped")
        ctl.terminate()
        try:
            ctl.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ctl.kill()
        step("controller stopped")
        ctl_log.close()
        meta["t_wall_end"] = time.time()
        with open(os.path.join(rd, "meta.json"), "w") as f:
            json.dump(meta, f, indent=1, sort_keys=True)
        with open(os.path.join(rd, "counters.json"), "w") as f:
            json.dump(counters, f, sort_keys=True)


# -------------------------------------------------------------- campaign ----
def postprocess(rd):
    """As the normal user: summarise captures, keep traces, gzip pcaps."""
    from experiments.pcaptrace import write_trace
    from experiments.spinetrace import summarize
    meta = json.load(open(os.path.join(rd, "meta.json")))
    sched = json.load(open(os.path.join(rd, "schedule.json")))
    t0 = meta["t0"]
    left_ips = {ip for n, ip in sched["hosts"].items() if int(n[1:]) <= meta["hosts_per_leaf"]}
    pcaps = {k: os.path.join(rd, "uplink_s%d.pcap" % k) for k in range(1, 5)}
    summarize(pcaps, t0, left_ips, os.path.join(rd, "spine_flows.csv"),
              os.path.join(rd, "spine_bins.csv"))
    for e in sched["elephants"]:
        if e["id"] not in sched["tagged"]:
            continue
        for side in ("send", "recv"):
            p = os.path.join(rd, "%s_%s.pcap" % (side, e["id"]))
            write_trace(p, os.path.join(rd, "%s_%s_trace.csv.gz" % (side, e["id"])),
                        e["sport"], t0, dirs=(0,) if side == "send" else (0, 1))
            pcaps[side + e["id"]] = p
    for p in pcaps.values():
        subprocess.run(["gzip", "-f", p], check=True)
    subprocess.run([VENV_PY, os.path.join(REPO, "experiments", "metrics.py"), "run", rd],
                   check=True)
    big = [os.path.join(rd, f) for f in os.listdir(rd)
           if f.startswith(("ss_", "iperf_", "policy_log")) and
           f.endswith((".log", ".json", ".jsonl"))]
    if big:                                  # kept (committed), just compressed
        subprocess.run(["gzip", "-f"] + big, check=True)


def campaign(a):
    """Seeded campaign: every condition (and, with --controllers, every
    controller) of rep r uses seed + r - 1, in a block-randomised order."""
    from experiments.traffic import make_schedule, resolve, schedule_sha256
    conds = a.conditions.split(",") if a.conditions else [""]
    ctrls = a.controllers.split(",") if a.controllers else [a.controller]
    multi = bool(a.controllers)
    rng = random.Random(a.seed * 7919 + 17)
    order = []
    for rep in range(1, a.runs + 1):            # block-randomised, paired seeds
        block = [(c, k) for k in ctrls for c in conds]
        rng.shuffle(block)                      # one controller: same order as Chunk 3
        order += [(rep, c, k) for c, k in block]
    jobs = []
    for rep, c, k in order:
        run_id = "%s%s%sr%02d" % (k + "-" if multi else "", c, "-" if c else "", rep)
        jobs.append(dict(run_id=run_id, controller=k, condition=c, rep=rep,
                         seed=a.seed + rep - 1,
                         make=lambda c=c, rep=rep: make_schedule(resolve(a.scenario, c or None),
                                                                 a.seed + rep - 1)))
    plan = dict(controller=a.controller, controllers=ctrls, scenario=a.scenario,
                conditions=conds, runs=a.runs, seed=a.seed,
                order=[[rep, c, k] if multi else [rep, c] for rep, c, k in order])
    execute(a, plan, jobs, schedule_sha256)


def replay(a):
    """Runs whose schedules are given in full (experiments/replay.py writes
    them: matched move / no-move pairs for the moves a controller made)."""
    from experiments.traffic import schedule_sha256
    spec = json.load(open(a.spec))
    jobs = [dict(run_id=j["run_id"], controller=j["controller"], condition=j["condition"],
                 rep=j.get("rep", 0), seed=j["schedule"]["seed"],
                 make=lambda j=j: j["schedule"]) for j in spec["jobs"]]
    plan = dict(controller=None, controllers=sorted({j["controller"] for j in jobs}),
                scenario="replay", spec=os.path.abspath(a.spec),
                spec_sha256=hashlib.sha256(open(a.spec, "rb").read()).hexdigest(),
                source=spec.get("source"), runs=len(jobs), seed=None,
                order=[j["run_id"] for j in jobs])
    execute(a, plan, jobs, schedule_sha256)


def execute(a, plan, jobs, schedule_sha256):
    os.makedirs(a.out, exist_ok=True)
    plan.update(queue_pkts=a.queue, mbps=a.mbps, host_qdisc=a.host_qdisc,
                started=time.strftime("%Y-%m-%dT%H:%M:%S%z"), host=os.uname().nodename,
                kernel=os.uname().release, euid=os.geteuid(), argv=sys.argv,
                boot_id=open("/proc/sys/kernel/random/boot_id").read().strip(),
                host_default_qdisc=open("/proc/sys/net/core/default_qdisc").read().strip())
    plan_path = os.path.join(a.out, "plan.json")
    if not os.path.exists(plan_path):
        json.dump(plan, open(plan_path, "w"), indent=1)
    root = os.geteuid() == 0
    index = os.path.join(a.out, "trials.csv")
    for i, j in enumerate(jobs, 1):
        run_id = j["run_id"]
        rd = os.path.join(a.out, "runs", run_id)
        if os.path.exists(os.path.join(rd, "run_flows.csv")):
            print("[%d/%d] %s exists, skipping" % (i, len(jobs), run_id), flush=True)
            continue
        if os.path.exists(rd):                 # a failed earlier attempt: start clean
            shutil.rmtree(rd)
        os.makedirs(rd, exist_ok=True)
        sched = j["make"]()
        sched["sha256"] = schedule_sha256(sched)
        json.dump(sched, open(os.path.join(rd, "schedule.json"), "w"), indent=1)
        cmd = [sys.executable, os.path.abspath(__file__), "run", "--run-dir", rd,
               "--controller", j["controller"], "--queue", str(a.queue), "--mbps", str(a.mbps),
               "--host-qdisc", a.host_qdisc]
        if root:
            subprocess.run(["mn", "-c"], capture_output=True)
        else:
            cmd = [os.path.join(REPO, "scripts", "userns.sh")] + cmd
        t = time.time()
        print("[%d/%d] %s seed %d ..." % (i, len(jobs), run_id, j["seed"]), end=" ", flush=True)
        with open(os.path.join(rd, "run.log"), "w") as log:
            rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=REPO).returncode
        if root and os.environ.get("SUDO_UID"):      # hand results back to the sudo user
            subprocess.run(["chown", "-R", "%s:%s" % (os.environ["SUDO_UID"],
                                                      os.environ["SUDO_GID"]), a.out])
        status = "ok" if rc == 0 else "FAILED rc=%d (see %s/run.log)" % (rc, rd)
        if rc == 0:
            try:
                postprocess(rd)
            except Exception as exc:
                status = "POSTPROCESS FAILED: %s" % exc
        print("%s, %.0f s" % (status, time.time() - t), flush=True)
        new = not os.path.exists(index)
        with open(index, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["seq", "run_id", "condition", "rep", "seed", "status", "seconds",
                            "controller"])
            w.writerow([i, run_id, j["condition"], j["rep"], j["seed"], status,
                        "%.1f" % (time.time() - t), j["controller"]])
        if rc != 0 and a.stop_on_error:
            sys.exit("stopping: run %s failed" % run_id)
    subprocess.run([VENV_PY, os.path.join(REPO, "experiments", "metrics.py"), "collect",
                    a.out], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    for name in ("run", "campaign", "replay"):
        p = sub.add_parser(name)
        p.add_argument("--controller", default="ecmp",
                       help="controllers/<name>.py, a FabricController app")
        p.add_argument("--queue", type=int, default=20, help="pfifo packets per port")
        p.add_argument("--mbps", type=float, default=10.0)
        p.add_argument("--host-qdisc", choices=["netem", "default"], default="netem",
                       help="host NIC qdisc; netem (10 us) frees each skb when it leaves "
                       "the host, see topologies/leafspine.py")
        if name == "run":
            p.add_argument("--run-dir", required=True, help="holds schedule.json")
        else:
            p.add_argument("--out", required=True)
            p.add_argument("--stop-on-error", action="store_true")
        if name == "campaign":
            p.add_argument("--scenario", required=True)
            p.add_argument("--conditions", default="", help="comma list, if the scenario has any")
            p.add_argument("--controllers", default="",
                           help="comma list: every controller runs every seed, block-"
                           "randomised per seed (run ids <controller>-r<rep>)")
            p.add_argument("--runs", type=int, default=10)
            p.add_argument("--seed", type=int, default=1)
        if name == "replay":
            p.add_argument("--spec", required=True, help="jobs JSON from experiments/replay.py")
    a = ap.parse_args()
    if a.mode == "run":
        run(a)
    elif a.mode == "campaign":
        campaign(a)
    else:
        replay(a)


if __name__ == "__main__":
    main()
