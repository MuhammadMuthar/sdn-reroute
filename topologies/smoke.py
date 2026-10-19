#!/usr/bin/env python3
"""Chunk 1 smoke topology: h1 - s1 - s2 - h2, 10 Mbps TCLinks.

Run by scripts/verify.sh with the learning-switch controller already
listening on 127.0.0.1:6653. Does pingall, then one idle iperf3 TCP flow
h1 -> h2, and prints the achieved rate as a fraction of the configured one.
Exit 0 when pingall has no loss and iperf3 reaches >= PASS_FRACTION of bw.

Needs root (Mininet). Options:
  --bw 10          link rate in Mbit/s (TCLink HTB on every link)
  --delay 1ms      optional netem delay per link (needs sch_netem)
  --datapath user  OVS userspace datapath (for kernels without the
                   openvswitch module, e.g. containers); default kernel
  --duration 10    iperf3 seconds
  --out FILE       append a one-line CSV result row to FILE
  --cli            drop into the Mininet CLI after the tests
"""

import argparse
import json
import sys
import time

from mininet.cli import CLI
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.topo import Topo

PASS_FRACTION = 0.90


class SmokeTopo(Topo):
    def build(self, bw=10, delay=None):
        opts = dict(bw=bw)
        if delay:
            opts["delay"] = delay
        h1, h2 = self.addHost("h1"), self.addHost("h2")
        s1, s2 = self.addSwitch("s1"), self.addSwitch("s2")
        self.addLink(h1, s1, **opts)
        self.addLink(s1, s2, **opts)
        self.addLink(s2, h2, **opts)


def disable_offloads(net):
    """Turn off checksum/segmentation offload on every host and switch port.

    Needed for correct captures later (CLAUDE.md pitfalls) and, with the OVS
    userspace datapath, for TCP to work at all: with tx checksum offload on,
    the SYN-ACK leaves h2 with an unfilled checksum and is dropped.
    """
    feats = "tx off rx off tso off gso off gro off"
    for node in list(net.hosts) + list(net.switches):
        for intf in node.intfList():
            if intf.name != "lo":
                node.cmd("ethtool -K %s %s >/dev/null 2>&1" % (intf.name, feats))


def iperf3_mbps(client, server, duration):
    server.cmd("pkill -x iperf3")
    srv = server.popen(["iperf3", "-s", "-1"])   # one client, then exits
    out = ""
    for attempt in range(10):          # server needs a moment to listen
        time.sleep(1)
        out = client.cmd("iperf3 -c %s -t %d -J" % (server.IP(), duration))
        try:
            data = json.loads(out)
        except ValueError:
            continue
        if "error" in data and "unable to connect" in data["error"]:
            continue
        try:
            mbps = data["end"]["sum_received"]["bits_per_second"] / 1e6
            srv.wait(timeout=5)
            return mbps, None
        except KeyError as exc:
            return None, "iperf3 output missing %s: %s" % (exc, out[:300])
    srv.kill()
    return None, "iperf3 could not connect after 10 tries: %s" % out[:300]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--bw", type=float, default=10)
    ap.add_argument("--delay", default=None)
    ap.add_argument("--datapath", choices=["kernel", "user"], default="kernel")
    ap.add_argument("--duration", type=int, default=10)
    ap.add_argument("--controller-ip", default="127.0.0.1")
    ap.add_argument("--controller-port", type=int, default=6653)
    ap.add_argument("--out", default=None)
    ap.add_argument("--cli", action="store_true")
    args = ap.parse_args()

    setLogLevel("info")
    switch = (lambda name, **kw: OVSSwitch(name, datapath=args.datapath,
                                           protocols="OpenFlow13", **kw))
    net = Mininet(topo=SmokeTopo(bw=args.bw, delay=args.delay), link=TCLink,
                  switch=switch, controller=None, autoSetMacs=True,
                  waitConnected=True)
    net.addController("c0", controller=RemoteController,
                      ip=args.controller_ip, port=args.controller_port)
    net.start()
    ok = True
    try:
        disable_offloads(net)
        loss = net.pingAll()
        print("RESULT pingall_loss_pct=%.0f" % loss)
        ok &= loss == 0
        h1, h2 = net.get("h1", "h2")
        mbps, err = iperf3_mbps(h1, h2, args.duration)
        if err:
            print("RESULT iperf3_error=%s" % err)
            ok = False
            frac = 0.0
        else:
            frac = mbps / args.bw
            print("RESULT iperf3_mbps=%.2f configured_mbps=%.0f fraction=%.3f"
                  % (mbps, args.bw, frac))
            ok &= frac >= PASS_FRACTION
        if args.out:
            with open(args.out, "a") as f:
                f.write("%s,%s,%.0f,%s,%.3f,%.3f,%s\n" % (
                    time.strftime("%Y-%m-%dT%H:%M:%S"), args.datapath,
                    loss, args.bw, mbps or 0.0, frac, "PASS" if ok else "FAIL"))
        if args.cli:
            CLI(net)
    finally:
        net.stop()
    print("SMOKE %s (pingall loss %.0f%%, iperf3 %.0f%% of %g Mbps, pass >= %.0f%%)"
          % ("PASS" if ok else "FAIL", loss, frac * 100, args.bw,
             PASS_FRACTION * 100))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
