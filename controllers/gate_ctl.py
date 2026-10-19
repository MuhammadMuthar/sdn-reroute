"""Static leaf-spine forwarding plus on-demand single-flow reroutes (Chunk 2).

Forwarding is proactive and installed when each switch connects (numbering
in topologies/layout.py):
  spine s<k>: eth_dst = host on leaf j          -> port j
  leaf  l<j>: eth_dst = local host              -> that host's port   (prio 100)
              eth_dst = remote host             -> spine DEFAULT_SPINE (prio 10)
Hosts use static ARP, so nothing is flooded and the table-miss (drop) never
sends packets to the controller.

A flow is "pinned" with an exact 5-tuple rule at its ingress leaf (prio 200),
and "moved" with one OFPFC_MODIFY_STRICT of that rule's output port. Because
spines and the egress leaf forward by destination, that single flow-mod on
one switch is the whole reroute: no install ordering, no transient loss
(Chunk 0 review 1.10).

Command channel for the experiment runner: JSON lines on TCP 127.0.0.1:6700.
  {"cmd": "status"}                        -> {"ready": true|false, "dpids": [...]}
  {"cmd": "pin",  "flow": F, "spine": k}   -> install (ADD) the 5-tuple rule
  {"cmd": "move", "flow": F, "spine": k}   -> MODIFY_STRICT its output port
  F = {"src": ip, "dst": ip, "sport": n, "dport": n}
pin/move reply after the switch's barrier reply, with wall-clock times
t_recv (command read), t_sent (flow-mod written), t_barrier (barrier reply),
so the runner can align the move with packet captures. A "move" to the spine
the flow is already on is still sent as a real flow-mod (the sham move used
by the control runs).
"""

import json
import os
import sys
import time

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER
from os_ken.controller.handler import set_ev_cls
from os_ken.lib import hub
from os_ken.ofproto import ofproto_v1_3

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from topologies.layout import layout  # noqa: E402

DEFAULT_SPINE = 1
CMD_ADDR = ("127.0.0.1", int(os.environ.get("GATE_CMD_PORT", "6700")))
PRIO_HOST, PRIO_REMOTE, PRIO_PIN = 100, 10, 200


class GateController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lay = layout(int(os.environ.get("GATE_HOSTS_PER_LEAF", "2")))
        self.by_ip = {h["ip"]: h for h in self.lay["hosts"]}
        self.leaf_of_dpid = {d: j for j, d in self.lay["leaf_dpid"].items()}
        self.spine_of_dpid = {d: k for k, d in self.lay["spine_dpid"].items()}
        self.dps = {}            # dpid -> datapath, once its rules are installed
        self.barriers = {}       # xid -> hub.Event
        self.server = hub.StreamServer(CMD_ADDR, self._serve)
        self.threads.append(hub.spawn(self.server.serve_forever))
        self.logger.info("gate_ctl: command channel on %s:%d", *CMD_ADDR)

    # ---- static forwarding -------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        dp = ev.msg.datapath
        parser = dp.ofproto_parser
        if dp.id in self.spine_of_dpid:
            for h in self.lay["hosts"]:
                self._flow(dp, PRIO_HOST, parser.OFPMatch(eth_dst=h["mac"]), h["leaf"])
        elif dp.id in self.leaf_of_dpid:
            j = self.leaf_of_dpid[dp.id]
            for h in self.lay["hosts"]:
                if h["leaf"] == j:
                    self._flow(dp, PRIO_HOST, parser.OFPMatch(eth_dst=h["mac"]), h["port"])
                else:
                    self._flow(dp, PRIO_REMOTE, parser.OFPMatch(eth_dst=h["mac"]),
                               DEFAULT_SPINE)
        else:
            self.logger.warning("unknown dpid %016x, no rules installed", dp.id)
            return
        # The barrier reply arrives through this app's own event loop, which is
        # busy running this handler, so wait for it in a separate green thread.
        hub.spawn(self._mark_ready, dp)

    def _mark_ready(self, dp):
        if not self._barrier(dp):
            self.logger.error("switch %016x: no barrier reply", dp.id)
            return
        self.dps[dp.id] = dp
        self.logger.info("switch %016x configured (%d/%d)", dp.id, len(self.dps),
                         len(self.leaf_of_dpid) + len(self.spine_of_dpid))

    def _flow(self, dp, prio, match, port, command=None):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS,
                                             [parser.OFPActionOutput(port)])]
        mod = parser.OFPFlowMod(datapath=dp, priority=prio, match=match,
                                instructions=inst,
                                command=ofp.OFPFC_ADD if command is None else command)
        dp.send_msg(mod)

    def _barrier(self, dp, timeout=5.0):
        req = dp.ofproto_parser.OFPBarrierRequest(dp)
        dp.set_xid(req)
        ev = hub.Event()
        self.barriers[req.xid] = ev
        dp.send_msg(req)
        ok = ev.wait(timeout=timeout)
        self.barriers.pop(req.xid, None)
        return bool(ok)

    @set_ev_cls(ofp_event.EventOFPBarrierReply, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def barrier_reply_handler(self, ev):
        waiter = self.barriers.get(ev.msg.xid)
        if waiter is not None:
            waiter.set()

    # ---- pinned flows ------------------------------------------------------
    def _pin_match(self, parser, f):
        return parser.OFPMatch(eth_type=0x0800, ip_proto=6, ipv4_src=f["src"],
                               ipv4_dst=f["dst"], tcp_src=int(f["sport"]),
                               tcp_dst=int(f["dport"]))

    def _set_path(self, f, spine, modify):
        leaf = self.by_ip[f["src"]]["leaf"]
        dp = self.dps[self.lay["leaf_dpid"][leaf]]
        ofp = dp.ofproto
        t_sent = time.time()
        self._flow(dp, PRIO_PIN, self._pin_match(dp.ofproto_parser, f), int(spine),
                   command=ofp.OFPFC_MODIFY_STRICT if modify else ofp.OFPFC_ADD)
        ok = self._barrier(dp)
        return dict(ok=ok, t_sent=t_sent, t_barrier=time.time(), leaf=leaf,
                    spine=int(spine))

    # ---- command channel ---------------------------------------------------
    def _serve(self, sock, addr):
        rfile = sock.makefile("r")
        try:
            for line in rfile:
                t_recv = time.time()
                try:
                    req = json.loads(line)
                    cmd = req.get("cmd")
                    if cmd == "status":
                        n = len(self.leaf_of_dpid) + len(self.spine_of_dpid)
                        rep = dict(ready=len(self.dps) == n,
                                   dpids=sorted("%016x" % d for d in self.dps))
                    elif cmd in ("pin", "move"):
                        rep = self._set_path(req["flow"], req["spine"], cmd == "move")
                        self.logger.info("%s %s -> spine %s ok=%s", cmd, req["flow"],
                                         req["spine"], rep["ok"])
                    else:
                        rep = dict(error="unknown cmd %r" % cmd)
                except Exception as exc:          # report, keep serving
                    rep = dict(error="%s: %s" % (type(exc).__name__, exc))
                rep["t_recv"] = t_recv
                sock.sendall((json.dumps(rep) + "\n").encode())
        finally:
            sock.close()
