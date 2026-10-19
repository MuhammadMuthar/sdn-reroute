#!/usr/bin/env python3
"""Methods check: does TCP Small Queues count packets waiting in the emulated
switch queue? (Chunk 3, LOG.md "TSQ artifact".)

Two elephants (h1 -> h5, h2 -> h6) share the l1-s1 uplink (10 Mbit/s, pfifo
20) for 10 s, once with each host qdisc setting of topologies/leafspine.py:
  default  hosts keep their default qdisc (Chunk 2 setup)
  netem    10 us netem on every host NIC, which frees ("orphans") each skb
           when it leaves the host, as TX completion does on a real NIC
Every 0.2 s from 3 s to 9 s it samples `ss -tim` on both senders and the
uplink's qdisc, and at the end iperf3's per-flow result. If skbs stay charged
to the sender while they sit in the switch queue, skmem t (sk_wmem_alloc) is
well above zero, packets in flight stay below cwnd and the uplink barely
drops; with the netem the charge is ~0, in flight == cwnd and the uplink
drops, i.e. normal loss-based congestion control.

Usage (repo root, no sudo needed; under sudo it runs on the system OVS):
  python3 experiments/tsq_check.py --out results/tsq/<name>
writes <out>/tsq_check.json (all samples) and <out>/tsq_check.md (table).
"""

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

FLOWS = [("h1", "h5", 40001, 5201), ("h2", "h6", 40002, 5202)]


def one(host_qdisc, rd):
    from mininet.log import setLogLevel
    from experiments.gate import wait_port
    from experiments.runner import CMD_PORT, OF_PORT, VENV_PY, ctl_cmd
    from topologies.leafspine import build_net, start_net
    setLogLevel("warning")
    log = open(os.path.join(rd, "controller_%s.log" % host_qdisc), "w")
    ctl = subprocess.Popen([VENV_PY, os.path.join(REPO, "controllers", "run.py"),
                            "--ofp-tcp-listen-port", str(OF_PORT),
                            os.path.join(REPO, "controllers", "ecmp.py")],
                           stdout=log, stderr=subprocess.STDOUT,
                           env=dict(os.environ, FABRIC_HOSTS_PER_LEAF="4",
                                    FABRIC_CMD_PORT=str(CMD_PORT)))
    net = None
    try:
        if not (wait_port(OF_PORT, 20) and wait_port(CMD_PORT, 5)):
            raise RuntimeError("controller did not start")
        net = build_net(hosts_per_leaf=4, controller_port=OF_PORT)
        start_net(net, host_qdisc=host_qdisc)
        for _ in range(100):
            if ctl_cmd({"cmd": "status"}).get("ready"):
                break
            time.sleep(0.1)
        H = {n: net.get(n) for n in ("h1", "h2", "h5", "h6")}
        for h in H.values():
            h.cmd("ip tcp_metrics flush all")
        srv, cli = [], []
        for s, d, sp, dp in FLOWS:
            f = dict(src=H[s].IP(), dst=H[d].IP(), sport=sp, dport=dp)
            r = dict(src=H[d].IP(), dst=H[s].IP(), sport=dp, dport=sp)
            assert ctl_cmd({"cmd": "pin", "flow": f, "spine": 1})["ok"]
            assert ctl_cmd({"cmd": "pin", "flow": r, "spine": 1})["ok"]
            srv.append(H[d].popen(["iperf3", "-s", "-1", "-J", "-p", str(dp)],
                                  stdout=subprocess.PIPE, text=True))
        time.sleep(1)
        t0 = time.time()
        for s, d, sp, dp in FLOWS:
            cli.append(H[s].popen(["iperf3", "-c", H[d].IP(), "-p", str(dp), "-t", "10",
                                   "--cport", str(sp), "-J"], stdout=subprocess.PIPE,
                                  text=True))
        l1 = net.get("l1")
        q0 = l1.cmd("tc -s qdisc show dev l1-s1")
        samples = []
        time.sleep(3)
        while time.time() < t0 + 9:
            smp = dict(t=time.time() - t0)
            for s, d, sp, dp in FLOWS:
                line = H[s].cmd("ss -timH '( sport = :%d )'" % sp)
                g = {k: re.search(r"\b%s:(\d+)" % k, line) for k in ("cwnd", "unacked")}
                t = re.search(r"skmem:\(r\d+,rb\d+,t(\d+),", line)
                smp[s] = dict(cwnd=int(g["cwnd"].group(1)) if g["cwnd"] else None,
                              unacked=int(g["unacked"].group(1)) if g["unacked"] else 0,
                              wmem_alloc=int(t.group(1)) if t else None)
            q = l1.cmd("tc -s qdisc show dev l1-s1")
            m = re.search(r"pfifo.*?backlog \d+b (\d+)p", q, re.S)
            smp["uplink_backlog_pkts"] = int(m.group(1)) if m else None
            samples.append(smp)
            time.sleep(0.2)
        res = {}
        for (s, d, sp, dp), c in zip(FLOWS, cli):
            j = json.loads(c.communicate(timeout=30)[0])
            res[s] = dict(goodput_mbps=j["end"]["sum_received"]["bits_per_second"] / 1e6,
                          retransmits=j["end"]["sum_sent"]["retransmits"])
        for p in srv:
            p.communicate(timeout=10)
        q1 = l1.cmd("tc -s qdisc show dev l1-s1")

        def drops(q):
            m = re.search(r"pfifo.*?dropped (\d+)", q, re.S)
            return int(m.group(1)) if m else None
        return dict(host_qdisc=host_qdisc, samples=samples, iperf=res,
                    uplink_drops=drops(q1) - drops(q0), uplink_qdisc_end=q1,
                    host_qdisc_shown=H["h1"].cmd("tc qdisc show dev h1-eth0"))
    finally:
        if net is not None:
            net.stop()
        ctl.terminate()
        ctl.wait(timeout=5)


def report(out, results):
    L = ["# TSQ check", "", "Generated by `experiments/tsq_check.py`. Two elephants share "
         "the l1-s1 uplink (10 Mbit/s, pfifo 20) for 10 s; samples every 0.2 s from 3 s "
         "to 9 s; medians over samples.", "",
         "| host qdisc | flow | sk_wmem_alloc, bytes | packets in flight | cwnd | in flight / cwnd "
         "| goodput, Mbit/s | retransmits | uplink backlog, pkts | uplink drops |",
         "|" + "---|" * 10]
    for r in results:
        bl = statistics.median(s["uplink_backlog_pkts"] for s in r["samples"])
        for h in ("h1", "h2"):
            ss = [s[h] for s in r["samples"] if s[h]["cwnd"]]
            w = statistics.median(x["wmem_alloc"] for x in ss)
            u = statistics.median(x["unacked"] for x in ss)
            c = statistics.median(x["cwnd"] for x in ss)
            L.append("| %s | %s | %.0f | %.0f | %.0f | %.2f | %.2f | %d | %.0f | %d |" % (
                r["host_qdisc"], h, w, u, c, statistics.median(x["unacked"] / x["cwnd"]
                                                               for x in ss),
                r["iperf"][h]["goodput_mbps"], r["iperf"][h]["retransmits"], bl,
                r["uplink_drops"]))
    open(os.path.join(out, "tsq_check.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--inner", default="", help=argparse.SUPPRESS)
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    os.makedirs(out, exist_ok=True)
    if a.inner:                       # one variant, inside the namespaces
        json.dump(one(a.inner, out), open(os.path.join(out, "raw_%s.json" % a.inner), "w"),
                  indent=1)
        return
    results = []
    for hq in ("default", "netem"):
        cmd = [sys.executable, os.path.abspath(__file__), "--out", out, "--inner", hq]
        if os.geteuid() == 0:
            subprocess.run(["mn", "-c"], capture_output=True)
        else:
            cmd = [os.path.join(REPO, "scripts", "userns.sh")] + cmd
        with open(os.path.join(out, "run_%s.log" % hq), "w") as log:
            subprocess.run(cmd, check=True, stdout=log, stderr=subprocess.STDOUT, cwd=REPO)
        p = os.path.join(out, "raw_%s.json" % hq)
        results.append(json.load(open(p)))
        os.remove(p)
    json.dump(dict(results=results, kernel=os.uname().release,
                   time=time.strftime("%Y-%m-%dT%H:%M:%S%z")),
              open(os.path.join(out, "tsq_check.json"), "w"), indent=1)
    report(out, results)
    if os.geteuid() == 0 and os.environ.get("SUDO_UID"):
        subprocess.run(["chown", "-R", "%s:%s" % (os.environ["SUDO_UID"],
                                                  os.environ["SUDO_GID"]), out])


if __name__ == "__main__":
    main()
