#!/usr/bin/env python3
"""Chunk 2 runner: the reordering gate. First version of the experiment runner.

One *trial* = fresh network + fresh controller + one long iperf3 TCP flow
h1 -> h3, pinned to spine FROM, then at MOVE_AT seconds one OFPFC_MODIFY at
the ingress leaf sends it to spine TO. A control trial does the same with
TO == FROM (a sham move: a real flow-mod that changes nothing). Per trial:
  - kernel TCP counters (/proc/net/netstat + snmp, i.e. what nstat reads) of
    sender and receiver at start, MOVE_AT-2, just before the move, 2 s after
    it, and at the end
  - `ss -tin` of the data socket every 50 ms on the sender
  - tcpdump on the sender's and the receiver's NIC (pcaps stay local), then
    converted to compact packet traces that are kept in results/
  - iperf3 client and server JSON with 100 ms intervals
  - tc -s qdisc of every switch port, flow and port stats of all switches
  - the sysctls of the sender's network namespace, tcp_metrics flushed first
A *campaign* runs a block-randomised list of trials, each in a brand-new set
of namespaces, and appends one line per trial to <out>/trials.csv.

Usage (from the repo root):
  python3 experiments/gate.py campaign --out results/gate/<name> [--reps 10]
  python3 experiments/gate.py campaign --out ... --only R2,C1 --reps 1   # pilot
Root or not: as a normal user every trial runs under scripts/userns.sh
(private user/net/pid/mount namespaces and a private OVS; see that file);
under sudo it runs directly with `mn -c` before each trial.
"""

import argparse
import csv
import json
import os
import random
import signal
import socket
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

VENV_PY = os.path.join(REPO, ".venv", "bin", "python")
OF_PORT, CMD_PORT = 6653, 6700
SENDER, RECEIVER = "h1", "h3"
DATA_SPORT, DATA_DPORT = 40001, 5201
WINDOW_S = 2.0            # damage window after the move (pre-registered)

# Pre-registered plan (LOG.md, 2026-10-19 Chunk 2). Spine delays (one way,
# ms): s1 = s2 = 1, s3 = 5, s4 = 10.
CONDITIONS = {
    "R1": dict(kind="reroute", src=3, dst=1, label="slow->fast, gap 4 ms (s3->s1)"),
    "R2": dict(kind="reroute", src=4, dst=1, label="slow->fast, gap 9 ms (s4->s1)"),
    "R3": dict(kind="reroute", src=1, dst=3, label="fast->slow, gap 4 ms (s1->s3)"),
    "R4": dict(kind="reroute", src=1, dst=4, label="fast->slow, gap 9 ms (s1->s4)"),
    "R0": dict(kind="reroute", src=1, dst=2, label="fast->fast, gap 0 ms (s1->s2)"),
    "C1": dict(kind="control", src=1, dst=1, label="no reroute on s1 (1 ms)"),
    "C3": dict(kind="control", src=3, dst=3, label="no reroute on s3 (5 ms)"),
    "C4": dict(kind="control", src=4, dst=4, label="no reroute on s4 (10 ms)"),
}

SYSCTLS = ["net.ipv4.tcp_congestion_control", "net.ipv4.tcp_recovery",
           "net.ipv4.tcp_sack", "net.ipv4.tcp_dsack", "net.ipv4.tcp_timestamps",
           "net.ipv4.tcp_reordering", "net.ipv4.tcp_max_reordering",
           "net.ipv4.tcp_early_retrans", "net.ipv4.tcp_frto",
           "net.ipv4.tcp_no_metrics_save", "net.ipv4.tcp_limit_output_bytes",
           "net.ipv4.tcp_wmem", "net.ipv4.tcp_rmem", "net.ipv4.tcp_ecn",
           "net.ipv4.tcp_autocorking", "net.ipv4.tcp_comp_sack_nr",
           "net.ipv4.tcp_slow_start_after_idle", "net.core.default_qdisc"]


# ----------------------------------------------------------------- trial ----
def read_counters(host):
    """All TCP/IP MIB counters of a host's namespace, as nstat sees them."""
    out = host.cmd("cat /proc/net/snmp /proc/net/netstat")
    lines = [ln.split() for ln in out.splitlines() if ":" in ln]
    counters = {}
    for names, values in zip(lines[0::2], lines[1::2]):
        prefix = names[0].rstrip(":")
        if prefix != values[0].rstrip(":"):
            continue
        for n, v in zip(names[1:], values[1:]):
            try:
                counters[prefix + n] = int(v)
            except ValueError:
                pass
    return counters


def ctl_cmd(req, timeout=10.0):
    with socket.create_connection(("127.0.0.1", CMD_PORT), timeout=timeout) as s:
        s.sendall((json.dumps(req) + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf)


def wait_port(port, timeout):
    t_end = time.time() + timeout
    while time.time() < t_end:
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def run_trial(a):
    from mininet.log import setLogLevel
    from topologies.leafspine import build_net, start_net
    from topologies.layout import DEFAULT_SPINE_DELAY_MS

    setLogLevel("warning")
    rd = os.path.abspath(a.run_dir)
    os.makedirs(rd, exist_ok=True)
    meta = dict(run_id=os.path.basename(rd), condition=a.condition,
                src_spine=a.src, dst_spine=a.dst, move_at_s=a.move_at,
                duration_s=a.duration, queue_pkts=a.queue, mbps=a.mbps,
                spine_delay_ms=list(DEFAULT_SPINE_DELAY_MS), window_s=WINDOW_S,
                sender=SENDER, receiver=RECEIVER, sport=DATA_SPORT, dport=DATA_DPORT,
                uid=os.getuid(), userns=os.environ.get("SDN_USERNS_INNER") == "1",
                t_wall_start=time.time(), errors=[])
    ctl_log = open(os.path.join(rd, "controller.log"), "w")
    ctl = subprocess.Popen([VENV_PY, os.path.join(REPO, "controllers", "run.py"),
                            "--ofp-tcp-listen-port", str(OF_PORT),
                            os.path.join(REPO, "controllers", "gate_ctl.py")],
                           stdout=ctl_log, stderr=subprocess.STDOUT,
                           env=dict(os.environ, GATE_CMD_PORT=str(CMD_PORT)))
    procs = []
    net = None
    try:
        if not (wait_port(OF_PORT, 20) and wait_port(CMD_PORT, 5)):
            raise RuntimeError("controller did not start, see controller.log")
        net = build_net(controller_port=OF_PORT)
        meta["tc_setup"] = start_net(net, mbps=a.mbps, queue=a.queue)
        for _ in range(100):
            if ctl_cmd({"cmd": "status"}).get("ready"):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("switches not configured: %s" % ctl_cmd({"cmd": "status"}))
        snd, rcv = net.get(SENDER), net.get(RECEIVER)
        meta["ping"] = snd.cmd("ping -c 3 -i 0.2 %s" % rcv.IP())
        if " 0% packet loss" not in meta["ping"]:
            raise RuntimeError("ping %s -> %s failed" % (SENDER, RECEIVER))
        for h in (snd, rcv):
            h.cmd("ip tcp_metrics flush all")
        meta["tcp_metrics_after_flush"] = snd.cmd("ip tcp_metrics show")
        meta["sysctl"] = {k: snd.cmd("sysctl -n %s 2>&1" % k).strip() for k in SYSCTLS}
        meta["kernel"] = os.uname().release

        flow = dict(src=snd.IP(), dst=rcv.IP(), sport=DATA_SPORT, dport=DATA_DPORT)
        meta["pin"] = ctl_cmd({"cmd": "pin", "flow": flow, "spine": a.src})
        if not meta["pin"].get("ok"):
            raise RuntimeError("pin failed: %s" % meta["pin"])

        filt = "tcp port %d" % DATA_SPORT
        for host, name in ((snd, "send.pcap"), (rcv, "recv.pcap")):
            procs.append(host.popen(["tcpdump", "-i", "%s-eth0" % host.name, "-s", "128",
                                     "-B", "16384", "-U", "-Z", "root", "-w",
                                     os.path.join(rd, name), filt],
                                    stderr=open(os.path.join(rd, name + ".log"), "w")))
        procs.append(snd.popen(
            "while :; do echo T $(date +%%s.%%N); ss -tinH '( sport = :%d )'; "
            "sleep 0.05; done" % DATA_SPORT, shell=True,
            stdout=open(os.path.join(rd, "ss.log"), "w")))
        srv = rcv.popen(["iperf3", "-s", "-1", "-J", "-i", "0.1", "-p", str(DATA_DPORT)],
                        stdout=open(os.path.join(rd, "iperf_server.json"), "w"))
        time.sleep(1.0)                               # tcpdump + iperf3 -s listening
        snaps = {"start": {"t": time.time(), SENDER: read_counters(snd),
                           RECEIVER: read_counters(rcv)}}
        t0 = time.time()
        cli = snd.popen(["iperf3", "-c", rcv.IP(), "-p", str(DATA_DPORT), "-t",
                         str(a.duration), "--cport", str(DATA_SPORT), "-J", "-i", "0.1"],
                        stdout=open(os.path.join(rd, "iperf_client.json"), "w"))
        meta["t_client_start"] = t0

        def snap(name):
            snaps[name] = {"t": time.time(), SENDER: read_counters(snd),
                           RECEIVER: read_counters(rcv)}

        time.sleep(max(0.0, t0 + a.move_at - WINDOW_S - time.time()))
        snap("pre_window")
        time.sleep(max(0.0, t0 + a.move_at - time.time()))
        snap("pre")
        meta["move"] = ctl_cmd({"cmd": "move", "flow": flow, "spine": a.dst})
        if not meta["move"].get("ok"):
            meta["errors"].append("move failed: %s" % meta["move"])
        t_move = meta["move"].get("t_barrier", time.time())
        meta["t_move"] = t_move
        time.sleep(max(0.0, t_move + WINDOW_S - time.time()))
        snap("post")
        cli.wait(timeout=a.duration + 30)
        srv.wait(timeout=10)
        snap("end")
        meta["counters"] = snaps
        meta["t_client_end"] = time.time()

        with open(os.path.join(rd, "tc_stats.txt"), "w") as f:
            for sw in net.switches:
                for intf in sw.intfList():
                    if intf.name != "lo":
                        f.write("== %s\n%s" % (intf.name, sw.cmd("tc -s qdisc show dev %s"
                                                                 % intf.name)))
            for h in (snd, rcv):
                f.write("== %s-eth0\n%s" % (h.name, h.cmd("tc -s qdisc show dev %s-eth0"
                                                          % h.name)))
        with open(os.path.join(rd, "ovs_stats.txt"), "w") as f:
            for sw in net.switches:
                f.write("== %s flows\n%s" % (sw.name, sw.cmd(
                    "ovs-ofctl -O OpenFlow13 dump-flows %s" % sw.name)))
                f.write("== %s ports\n%s" % (sw.name, sw.cmd(
                    "ovs-ofctl -O OpenFlow13 dump-ports %s" % sw.name)))
    except Exception as exc:
        meta["errors"].append("%s: %s" % (type(exc).__name__, exc))
        raise
    finally:
        for p in procs:
            try:
                p.send_signal(signal.SIGINT)
                p.wait(timeout=5)
            except Exception:
                p.kill()
        if net is not None:
            net.stop()
        ctl.terminate()
        try:
            ctl.wait(timeout=5)
        except subprocess.TimeoutExpired:
            ctl.kill()
        ctl_log.close()
        meta["t_wall_end"] = time.time()
        with open(os.path.join(rd, "meta.json"), "w") as f:
            json.dump(meta, f, indent=1, sort_keys=True)


# -------------------------------------------------------------- campaign ----
def tshark_flags(pcap, windows):
    """Counts of Wireshark's tcp.analysis flags per time window (cross-check)."""
    fields = ["tcp.analysis.out_of_order", "tcp.analysis.retransmission",
              "tcp.analysis.spurious_retransmission", "tcp.analysis.fast_retransmission",
              "tcp.analysis.lost_segment", "tcp.analysis.duplicate_ack"]
    cmd = ["tshark", "-r", pcap, "-Y", "tcp.analysis.flags", "-T", "fields",
           "-E", "separator=,", "-e", "frame.time_epoch"]
    for fl in fields:
        cmd += ["-e", fl]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    counts = {w: {fl.split(".")[-1]: 0 for fl in fields} for w in windows}
    for line in out.splitlines():
        parts = line.split(",")
        t = float(parts[0])
        for w, (lo, hi) in windows.items():
            if lo <= t < hi:
                for fl, v in zip(fields, parts[1:]):
                    if v != "":
                        counts[w][fl.split(".")[-1]] += 1
    return counts


def postprocess(rd):
    """As the normal user: packet traces + tshark cross-check, then gzip pcaps."""
    from experiments.pcaptrace import write_trace
    with open(os.path.join(rd, "meta.json")) as f:
        meta = json.load(f)
    t0, tm = meta["t_client_start"], meta["t_move"]
    out = {}
    for side in ("send", "recv"):
        pcap = os.path.join(rd, side + ".pcap")
        out[side + "_rows"] = write_trace(pcap, os.path.join(rd, side + "_trace.csv.gz"),
                                          DATA_SPORT, t0)
        out[side] = tshark_flags(pcap, {
            "pre": (tm - WINDOW_S, tm), "post": (tm, tm + WINDOW_S),
            "all": (t0, t0 + meta["duration_s"] + 5)})
        subprocess.run(["gzip", "-f", pcap], check=True)
    with open(os.path.join(rd, "tshark.json"), "w") as f:
        json.dump(out, f, indent=1, sort_keys=True)


def campaign(a):
    names = a.only.split(",") if a.only else list(CONDITIONS)
    rng = random.Random(a.seed)
    order = []
    for rep in range(1, a.reps + 1):          # block-randomised: each block has every condition once
        block = list(names)
        rng.shuffle(block)
        order += [(rep, c) for c in block]
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "plan.json"), "w") as f:
        json.dump(dict(conditions={c: CONDITIONS[c] for c in names}, reps=a.reps,
                       seed=a.seed, order=order, move_at_s=a.move_at,
                       duration_s=a.duration, queue_pkts=a.queue, mbps=a.mbps,
                       started=time.strftime("%Y-%m-%dT%H:%M:%S%z")), f, indent=1)
    index = os.path.join(a.out, "trials.csv")
    new = not os.path.exists(index)
    root = os.geteuid() == 0
    for i, (rep, c) in enumerate(order, 1):
        cond = CONDITIONS[c]
        run_id = "%s-r%02d" % (c, rep)
        rd = os.path.join(a.out, "runs", run_id)
        if os.path.exists(os.path.join(rd, "tshark.json")):
            print("[%d/%d] %s exists, skipping" % (i, len(order), run_id), flush=True)
            continue
        cmd = [sys.executable, os.path.abspath(__file__), "trial", "--run-dir", rd,
               "--condition", c, "--src", str(cond["src"]), "--dst", str(cond["dst"]),
               "--move-at", str(a.move_at), "--duration", str(a.duration),
               "--queue", str(a.queue), "--mbps", str(a.mbps)]
        if root:
            subprocess.run(["mn", "-c"], capture_output=True)
        else:
            cmd = [os.path.join(REPO, "scripts", "userns.sh")] + cmd
        t = time.time()
        print("[%d/%d] %s (%s) ..." % (i, len(order), run_id, cond["label"]),
              end=" ", flush=True)
        os.makedirs(rd, exist_ok=True)
        with open(os.path.join(rd, "trial.log"), "w") as log:
            rc = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT, cwd=REPO).returncode
        if root and os.environ.get("SUDO_UID"):
            subprocess.run(["chown", "-R", "%s:%s" % (os.environ["SUDO_UID"],
                                                      os.environ["SUDO_GID"]), a.out])
        status = "ok" if rc == 0 else "FAILED rc=%d (see %s/trial.log)" % (rc, rd)
        if rc == 0:
            try:
                postprocess(rd)
            except Exception as exc:
                status = "POSTPROCESS FAILED: %s" % exc
        print("%s, %.0f s" % (status, time.time() - t), flush=True)
        with open(index, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["seq", "run_id", "condition", "rep", "kind", "src_spine",
                            "dst_spine", "status", "seconds"])
                new = False
            w.writerow([i, run_id, c, rep, cond["kind"], cond["src"], cond["dst"],
                        status, "%.1f" % (time.time() - t)])
        if rc != 0 and a.stop_on_error:
            sys.exit("stopping: trial %s failed" % run_id)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="mode", required=True)
    for name in ("trial", "campaign"):
        p = sub.add_parser(name)
        p.add_argument("--move-at", type=float, default=10.0)
        p.add_argument("--duration", type=int, default=20)
        p.add_argument("--queue", type=int, default=20, help="pfifo packets per port")
        p.add_argument("--mbps", type=float, default=10.0)
        if name == "trial":
            p.add_argument("--run-dir", required=True)
            p.add_argument("--condition", default="adhoc")
            p.add_argument("--src", type=int, required=True)
            p.add_argument("--dst", type=int, required=True)
        else:
            p.add_argument("--out", required=True)
            p.add_argument("--reps", type=int, default=10)
            p.add_argument("--seed", type=int, default=20261019)
            p.add_argument("--only", default="", help="comma list of conditions")
            p.add_argument("--stop-on-error", action="store_true")
    a = ap.parse_args()
    if a.mode == "trial":
        run_trial(a)
    else:
        campaign(a)


if __name__ == "__main__":
    main()
