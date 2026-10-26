#!/usr/bin/env python3
"""4-path leaf-spine for the reroute experiments (Chunk 2 onwards).

    h1 h2          h3 h4
     \\ /            \\ /
      l1 ---------- l2        each leaf has one link to each of s1..s4
       \\ \\ \\ \\  / / / /
        s1 s2 s3 s4

Fixed numbering, so the controller needs no discovery:
  leaf l<j>  dpid 0x10<j>, port k = uplink to spine s<k> (k = 1..4),
             port 4+i = i-th local host
  spine s<k> dpid 0x20<k>, port j = link to leaf l<j>
  host h<n>  10.0.0.<n>, MAC 00:00:00:00:00:<n>, on leaf 1 + (n-1)//hosts_per_leaf

Link shaping (Chunk 0 review 1.4, CLAUDE.md pitfalls). Every switch egress
towards a host or a spine is a 10 Mbit/s HTB class with a pfifo of `queue`
packets; that is where queues build. Spine egress ports carry only netem
delay (no rate), so delay and shaping never share an interface. Host NICs are
not shaped, so a single sender's queue sits in the network (on the leaf
uplink of the path it uses), not inside the sending host where TCP small
queues would hide it.

  data h1 -> h3 via s<k>:  l1-s<k> HTB+pfifo -> s<k>-l2 netem d_k -> l2-h3 HTB+pfifo

Routing is done by the controller (controllers/gate_ctl.py): spines and the
egress leaf forward by destination MAC; only the ingress leaf chooses the
spine, so a reroute is one flow-mod on one switch.

This module only builds and configures the network; experiments/gate.py
drives it. `python3 topologies/leafspine.py --cli` (as root, with a
controller on 6653) gives an interactive network for poking around.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mininet.link import Link
from mininet.net import Mininet
from mininet.node import OVSSwitch, RemoteController
from mininet.topo import Topo

from topologies.layout import (DEFAULT_SPINE_DELAY_MS, N_LEAVES, N_SPINES,  # noqa: E402
                               layout)

HTB_BURST = "15k"          # same burst Mininet's TCLink uses


class LeafSpineTopo(Topo):
    def build(self, hosts_per_leaf=2):
        lay = layout(hosts_per_leaf)
        leaves = {j: self.addSwitch("l%d" % j, dpid="%016x" % d)
                  for j, d in lay["leaf_dpid"].items()}
        spines = {k: self.addSwitch("s%d" % k, dpid="%016x" % d)
                  for k, d in lay["spine_dpid"].items()}
        for j, leaf in leaves.items():
            for k, spine in spines.items():
                self.addLink(leaf, spine, port1=k, port2=j,
                             intfName1="l%d-s%d" % (j, k),
                             intfName2="s%d-l%d" % (k, j))
        for h in lay["hosts"]:
            node = self.addHost(h["name"], ip=h["ip"] + "/24", mac=h["mac"])
            self.addLink(node, leaves[h["leaf"]], port1=0, port2=h["port"],
                         intfName1="%s-eth0" % h["name"],
                         intfName2="l%d-%s" % (h["leaf"], h["name"]))


def shape_cmds(intf, mbps, queue):
    # above 16 Mbit/s HTB's default quantum (rate / r2q) exceeds 200000 bytes and tc
    # warns (the kernel clamps it); set the clamped value so the warning goes away.
    # One class per qdisc, so the quantum does not change who is served; at 10
    # Mbit/s the commands are exactly the earlier chunks' ones.
    q = " quantum 200000" if mbps * 1e6 / 8 / 10 > 200000 else ""
    return ["tc qdisc del dev %s root" % intf,
            "tc qdisc add dev %s root handle 5: htb default 1" % intf,
            "tc class add dev %s parent 5: classid 5:1 htb rate %gmbit burst %s cburst %s%s"
            % (intf, mbps, HTB_BURST, HTB_BURST, q),
            "tc qdisc add dev %s parent 5:1 handle 10: pfifo limit %d" % (intf, queue)]


def delay_cmds(intf, delay_ms, limit):
    return ["tc qdisc del dev %s root" % intf,
            "tc qdisc add dev %s root netem delay %gms limit %d" % (intf, delay_ms, limit)]


def configure_links(net, mbps=10, queue=20, spine_delay_ms=DEFAULT_SPINE_DELAY_MS,
                    netem_limit=100):
    """Apply shaping and delay; returns the list of tc commands that ran."""
    ran = []
    for sw in net.switches:
        for intf in sw.intfList():
            name = intf.name
            if name == "lo":
                continue
            if name.startswith("l"):              # leaf egress: to spine or host
                cmds = shape_cmds(name, mbps, queue)
            else:                                 # spine egress: delay only
                k = int(name[1:name.index("-")])
                cmds = delay_cmds(name, spine_delay_ms[k - 1], netem_limit)
            for c in cmds:
                out = sw.cmd(c + " 2>&1")
                if out.strip() and " del " not in c:
                    raise RuntimeError("%s -> %s" % (c, out.strip()))
                ran.append(c)
    return ran


def disable_offloads(net):
    """Same as topologies/smoke.py: no TSO/GSO/GRO/checksum offload anywhere."""
    feats = "tx off rx off tso off gso off gro off"
    for node in list(net.hosts) + list(net.switches):
        for intf in node.intfList():
            if intf.name != "lo":
                node.cmd("ethtool -K %s %s >/dev/null 2>&1" % (intf.name, feats))


def build_net(hosts_per_leaf=2, controller_port=6653):
    switch = (lambda name, **kw: OVSSwitch(name, protocols="OpenFlow13",
                                           failMode="secure", **kw))
    net = Mininet(topo=LeafSpineTopo(hosts_per_leaf=hosts_per_leaf), link=Link,
                  switch=switch, controller=None, autoSetMacs=False,
                  waitConnected=True)
    net.addController("c0", controller=RemoteController, ip="127.0.0.1",
                      port=controller_port)
    return net


# Host egress qdisc (Chunk 3, LOG.md "TSQ artifact"). With the hosts' default
# qdisc a sender's skbs stay charged to its socket (sk_wmem_alloc) through the
# veth and OVS until the spine's netem orphans them, so TCP Small Queues counts
# packets waiting in the *switch* queue and caps every flow at a few packets
# there: the bottleneck never fills and cwnd stops mattering. Real NICs free
# the skb at TX completion, when it leaves the host. A 10 us netem on each host
# NIC does the same here (netem orphans the skb on enqueue), so the queue builds
# in the fabric and TCP is cwnd-limited, as on real hosts.
# "default" keeps the Chunk 2 behaviour (gate.py never passes this argument).
HOST_EGRESS_NETEM = "netem delay 0.01ms limit 10000"


def start_net(net, mbps=10, queue=20, spine_delay_ms=DEFAULT_SPINE_DELAY_MS,
              host_qdisc="default"):
    net.start()
    disable_offloads(net)
    # the spine netem holds rate x delay in flight (83 packets at 100 Mbit/s and
    # 10 ms): its limit scales with the rate, 100 packets at 10 Mbit/s as before
    tc = configure_links(net, mbps=mbps, queue=queue, spine_delay_ms=spine_delay_ms,
                         netem_limit=max(100, int(round(10 * mbps))))
    if host_qdisc == "netem":
        for h in net.hosts:
            c = "tc qdisc replace dev %s-eth0 root %s" % (h.name, HOST_EGRESS_NETEM)
            out = h.cmd(c + " 2>&1")
            if out.strip():
                raise RuntimeError("%s -> %s" % (c, out.strip()))
            tc.append("%s: %s" % (h.name, c))
    elif host_qdisc != "default":
        raise ValueError("host_qdisc must be 'default' or 'netem'")
    net.staticArp()            # no ARP at all: nothing is ever flooded
    for h in net.hosts:        # keep IPv6 ND/RS chatter off the wire
        h.cmd("sysctl -qw net.ipv6.conf.all.disable_ipv6=1")
    return tc


def main():
    from mininet.cli import CLI
    from mininet.log import setLogLevel
    ap = argparse.ArgumentParser()
    ap.add_argument("--queue", type=int, default=20)
    ap.add_argument("--cli", action="store_true")
    args = ap.parse_args()
    setLogLevel("info")
    net = build_net()
    try:
        start_net(net, queue=args.queue)
        if args.cli:
            CLI(net)
    finally:
        net.stop()


if __name__ == "__main__":
    main()
