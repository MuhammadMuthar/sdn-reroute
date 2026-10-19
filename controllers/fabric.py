"""Shared base for the leaf-spine controllers from Chunk 3 on (ECMP, Hedera-style,
cost-aware). Not an app by itself: os-ken only instantiates app classes that
are defined in the module it loads, so `controllers/ecmp.py` etc. subclass
FabricController and get everything below.

What the base installs when a switch connects (numbering: topologies/layout.py):
  spine s<k>: eth_dst = host on leaf j    -> port j                  (prio 100)
  leaf  l<j>: eth_dst = local host        -> that host's port        (prio 100)
              eth_dst = remote host       -> self.remote_actions()   (prio 10)
Only the ingress leaf chooses the spine; spines and the egress leaf forward
by destination, so moving a flow is one flow-mod on one switch (as in
controllers/gate_ctl.py, which stays unchanged so Chunk 2 can be rerun).

Per-flow overrides ("pins") are exact 5-tuple rules at prio 200 on the
flow's ingress leaf. They are what a reroute changes, whatever installed the
flow's original path (ECMP group, a pin, a later policy).

Command channel for the experiment runner (experiments/runner.py): JSON lines
on TCP 127.0.0.1:$FABRIC_CMD_PORT (default 6700).
  {"cmd": "status"}                        -> {"ready": bool, "dpids": [...], "app": name}
  {"cmd": "pin",  "flow": F, "spine": k}   -> ADD the 5-tuple rule
  {"cmd": "move", "flow": F, "spine": k}   -> MODIFY_STRICT its output port
  {"cmd": "events"}                        -> list of moves this controller made itself
  F = {"src": ip, "dst": ip, "sport": n, "dport": n}
pin/move reply after the switch's barrier reply, with wall-clock times
t_recv, t_sent and t_barrier (same clock as the packet captures). A move to
the spine the flow is already on is still a real flow-mod (sham move).
Every pin/move, from the runner or from the controller's own policy, is
appended to self.path_log with its timing, so later controllers can log their
reroutes the same way.
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

PRIO_HOST, PRIO_REMOTE, PRIO_PIN = 100, 10, 200


class FabricController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    APP_NAME = "fabric"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lay = layout(int(os.environ.get("FABRIC_HOSTS_PER_LEAF", "2")))
        self.by_ip = {h["ip"]: h for h in self.lay["hosts"]}
        self.leaf_of_dpid = {d: j for j, d in self.lay["leaf_dpid"].items()}
        self.spine_of_dpid = {d: k for k, d in self.lay["spine_dpid"].items()}
        self.dps = {}            # dpid -> datapath, once its rules are installed
        self.barriers = {}       # xid -> hub.Event
        self.of_errors = {}      # xid -> error text, from OFPT_ERROR replies
        self.path_log = []       # every pin/move with timing (not `events`: OSKenApp uses that name)
        addr = ("127.0.0.1", int(os.environ.get("FABRIC_CMD_PORT", "6700")))
        self.server = hub.StreamServer(addr, self._serve)
        self.threads.append(hub.spawn(self.server.serve_forever))
        self.logger.info("%s: command channel on %s:%d", self.APP_NAME, *addr)

    # ---- hooks for subclasses ---------------------------------------------
    def leaf_setup(self, dp, leaf):
        """Called before the leaf's host rules are installed (e.g. add groups)."""

    def remote_actions(self, dp, leaf, host):
        """Actions for traffic entering `leaf` towards remote `host`."""
        raise NotImplementedError

    # ---- static forwarding -------------------------------------------------
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        try:
            self._configure(ev.msg.datapath)
        except Exception:                 # os-ken swallows handler exceptions silently
            self.logger.exception("configuring switch %016x failed", ev.msg.datapath.id)

    def _configure(self, dp):
        parser = dp.ofproto_parser
        if dp.id in self.spine_of_dpid:
            for h in self.lay["hosts"]:
                self._flow(dp, PRIO_HOST, parser.OFPMatch(eth_dst=h["mac"]),
                           [parser.OFPActionOutput(h["leaf"])])
        elif dp.id in self.leaf_of_dpid:
            j = self.leaf_of_dpid[dp.id]
            self.leaf_setup(dp, j)
            for h in self.lay["hosts"]:
                if h["leaf"] == j:
                    acts = [parser.OFPActionOutput(h["port"])]
                    self._flow(dp, PRIO_HOST, parser.OFPMatch(eth_dst=h["mac"]), acts)
                else:
                    self._flow(dp, PRIO_REMOTE, parser.OFPMatch(eth_dst=h["mac"]),
                               self.remote_actions(dp, j, h))
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

    def _flow(self, dp, prio, match, actions, command=None):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        mod = parser.OFPFlowMod(datapath=dp, priority=prio, match=match,
                                instructions=inst,
                                command=ofp.OFPFC_ADD if command is None else command)
        dp.set_xid(mod)
        dp.send_msg(mod)
        return mod.xid

    def _barrier(self, dp, timeout=5.0):
        req = dp.ofproto_parser.OFPBarrierRequest(dp)
        dp.set_xid(req)
        ev = hub.Event()
        self.barriers[req.xid] = ev
        dp.send_msg(req)
        ok = ev.wait(timeout=timeout)
        self.barriers.pop(req.xid, None)
        return bool(ok)

    @set_ev_cls(ofp_event.EventOFPErrorMsg, [CONFIG_DISPATCHER, MAIN_DISPATCHER])
    def error_msg_handler(self, ev):
        m = ev.msg
        self.of_errors[m.xid] = "type=%d code=%d" % (m.type, m.code)
        self.logger.error("OFPT_ERROR from %016x xid=%d: %s", m.datapath.id, m.xid,
                          self.of_errors[m.xid])

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

    def set_path(self, f, spine, modify, origin="runner"):
        leaf = self.by_ip[f["src"]]["leaf"]
        dp = self.dps[self.lay["leaf_dpid"][leaf]]
        ofp, parser = dp.ofproto, dp.ofproto_parser
        t_sent = time.time()
        xid = self._flow(dp, PRIO_PIN, self._pin_match(parser, f),
                         [parser.OFPActionOutput(int(spine))],
                         command=ofp.OFPFC_MODIFY_STRICT if modify else ofp.OFPFC_ADD)
        ok = self._barrier(dp)      # an error reply precedes the barrier reply
        t_barrier = time.time()
        err = self.of_errors.pop(xid, None)
        rep = dict(ok=ok and err is None, t_sent=t_sent, t_barrier=t_barrier, leaf=leaf,
                   spine=int(spine))
        if err:
            rep["of_error"] = err
        self.path_log.append(dict(rep, cmd="move" if modify else "pin", flow=f,
                                origin=origin))
        return rep

    # ---- command channel ---------------------------------------------------
    def handle_cmd(self, req):
        """Subclasses may extend; return None for unknown commands."""
        return None

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
                        rep = dict(ready=len(self.dps) == n, app=self.APP_NAME,
                                   dpids=sorted("%016x" % d for d in self.dps))
                    elif cmd in ("pin", "move"):
                        rep = self.set_path(req["flow"], req["spine"], cmd == "move")
                        self.logger.info("%s %s -> spine %s ok=%s", cmd, req["flow"],
                                         req["spine"], rep["ok"])
                    elif cmd == "events":
                        rep = dict(events=self.path_log)
                    else:
                        rep = self.handle_cmd(req) or dict(error="unknown cmd %r" % cmd)
                except Exception as exc:          # report, keep serving
                    rep = dict(error="%s: %s" % (type(exc).__name__, exc))
                rep["t_recv"] = t_recv
                sock.sendall((json.dumps(rep) + "\n").encode())
        finally:
            sock.close()
