"""Hedera-style greedy rerouting on top of ECMP (Chunk 4), and its flags.

This is Hedera-*style* greedy placement, not Hedera (CHUNK0_REVIEW 1.8). Kept
from Hedera (Al-Fares et al., NSDI 2010): ECMP places every flow first; the
controller polls the edge switches' flow counters, calls a flow an elephant
when it exceeds 10 % of the link rate, and reroutes only elephants, using a
global view of path load. Dropped: the demand estimator, Global First Fit
with per-link reservations (and simulated annealing), and the 5 s period; we
poll every 1 s (the spec's 1-2 s), use the measured rates instead of
estimated demands, and move greedily, one decision per elephant per round.

Monitoring (the same in every variant, including policy "none"):
  leaf, prio 20   tcp with SYN set and ACK clear (tcp_flags=+syn-ack)
                  -> [copy to controller (128 bytes), group 1]
                  The SYN keeps going through the ECMP group at once, so no
                  flow waits for the controller and the hash path is unchanged.
  leaf, prio 150  installed on that packet-in: exact 5-tuple -> group 1,
                  idle_timeout 5 s, cookie COOKIE_LEAF. Same group, so the same
                  dp_hash bucket: a per-flow byte counter, not a path change.
  spine, prio 150 installed once a flow first looks like an elephant: the
                  5-tuple -> the destination leaf's port on every spine,
                  cookie COOKIE_SPINE, no timeout. The spine whose counter
                  grows is the flow's path (OVS's dp_hash bucket cannot be
                  computed by the controller; it is seeded at boot).
  every POLL_S    flow stats of every switch, port stats of the leaves.
                  Rates are deltas of the cumulative byte counters over the
                  delta of the rule's own duration_sec/nsec (CLAUDE.md
                  pitfall); a flow's leaf bytes are its counting rule plus its
                  pin (prio 200) once the controller has moved it. Path load
                  U[leaf][k] = transmit rate of leaf uplink k (port stats),
                  the only shaped link on the path that depends on k (spine
                  egress ports carry delay only).

Policy, once per round, over elephants (rate >= ELEPHANT_MBPS in the last
interval) whose path is known, most loaded path first, then largest rate:
  q* = the least-loaded other uplink of the flow's ingress leaf
  gain = U_p - (U_q* + r_f)       (the load f would see on q* minus on p)
  "greedy"   (Hedera-style):  move if gain > 0
  "cooldown" (ablation):      move if gain > MARGIN_MBPS and the flow was not
                              moved in the last COOLDOWN_S (a flow never moved
                              is eligible at once)
  "none"     (monitor only):  never move
After a move, U_p -= r_f and U_q* += r_f for the rest of the round, so two
flows are not sent to the same idle path on the same stale reading; a flow
moves at most once per round. The move is a prio-200 pin on the ingress leaf
(fabric.set_path): ADD the first time, MODIFY_STRICT after. Only the data
direction moves; its ACKs stay on their ECMP spine (they are a small flow).

Logs: every policy move is in path_log (origin "policy", with the round and
the spine left) and, with the full pre-move features and every round's loads
and elephants, in $FABRIC_LOG_DIR/policy_log.jsonl. Path delays in the log
come from the topology configuration (layout.py), not from measurement.

Variants are thin subclasses so the runner can name them:
controllers/ecmp_mon.py (policy none), controllers/cooldown.py; or set
HEDERA_POLICY=none|greedy|cooldown for this module.
"""

import json
import os
import sys
import time

from os_ken.controller import ofp_event
from os_ken.controller.handler import MAIN_DISPATCHER, set_ev_cls
from os_ken.lib import hub
from os_ken.lib.packet import ipv4, packet, tcp

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controllers.ecmp import GROUP_ID, ECMPController  # noqa: E402
from controllers.fabric import PRIO_PIN  # noqa: E402
from topologies.layout import DEFAULT_SPINE_DELAY_MS  # noqa: E402

PRIO_SYN, PRIO_COUNT = 20, 150
COOKIE_LEAF, COOKIE_SPINE = 0xC401, 0xC402
TCP_SYN, TCP_ACK = 0x02, 0x10


class Flow:
    __slots__ = ("key", "f", "leaf", "dleaf", "t_syn", "rules", "spine_rules", "spine_bytes",
                 "rate", "bytes", "path", "path_src", "pinned", "n_moves", "t_last_move",
                 "moves", "last_seen", "elephant_since")

    def __init__(self, key, f, leaf, dleaf, t_syn):
        self.key, self.f, self.leaf, self.dleaf, self.t_syn = key, f, leaf, dleaf, t_syn
        self.rules = {}           # (dpid, prio) -> (byte_count, duration_ns), last poll
        self.spine_rules = False  # counting rules installed on the spines
        self.spine_bytes = {}     # spine k -> byte delta in the last interval
        self.rate = 0.0           # Mbit/s in the last interval
        self.bytes = 0            # bytes counted at the ingress leaf so far
        self.path, self.path_src = None, None
        self.pinned = None        # spine of our prio-200 rule, if any
        self.n_moves, self.t_last_move, self.moves = 0, None, []
        self.last_seen = t_syn
        self.elephant_since = None


class HederaController(ECMPController):
    APP_NAME = "hedera"
    POLICY = "greedy"
    POLICIES = ("none", "greedy", "cooldown")
    POLL_S = 1.0
    LINK_MBPS = 10.0
    ELEPHANT_MBPS = 1.0       # 10 % of the link rate (Hedera's threshold)
    COOLDOWN_S = 5.0          # "cooldown" only
    MARGIN_MBPS = 1.0         # "cooldown" only: hysteresis, 10 % of the link rate
    COUNT_IDLE_S = 5          # idle timeout of the per-flow leaf counting rules
    LOG_MIN_MBPS = 0.5        # flows logged in each round's record

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.policy = os.environ.get("HEDERA_POLICY", self.POLICY)
        if self.policy not in self.POLICIES:
            raise ValueError("HEDERA_POLICY must be one of %s" % (self.POLICIES,))
        self.flows = {}           # key -> Flow
        self.stats = {}           # xid -> [parts], for multipart replies
        self.waiting = {}         # xid -> hub.Event
        self.ports = {}           # (leaf, port) -> (tx_bytes, duration_ns, t_rx)
        self.U = {}               # leaf -> {k: Mbit/s}
        self.round = 0
        self.n_packet_in = 0
        d = os.environ.get("FABRIC_LOG_DIR")
        self.plog = open(os.path.join(d, "policy_log.jsonl"), "w") if d else None
        self._log(dict(kind="config", app=self.APP_NAME, policy=self.policy,
                       poll_s=self.POLL_S, elephant_mbps=self.ELEPHANT_MBPS,
                       cooldown_s=self.COOLDOWN_S if self.policy == "cooldown" else None,
                       margin_mbps=self.MARGIN_MBPS if self.policy == "cooldown" else None,
                       link_mbps=self.LINK_MBPS, count_idle_s=self.COUNT_IDLE_S,
                       spine_delay_ms_config=list(DEFAULT_SPINE_DELAY_MS)))
        self.threads.append(hub.spawn(self._poll_loop))

    def _log(self, rec):
        if self.plog:
            self.plog.write(json.dumps(rec, sort_keys=True) + "\n")
            self.plog.flush()

    # ---- monitoring rules --------------------------------------------------
    def leaf_setup(self, dp, leaf):
        super().leaf_setup(dp, leaf)             # the ECMP select group
        ofp, parser = dp.ofproto, dp.ofproto_parser
        match = parser.OFPMatch(eth_type=0x0800, ip_proto=6, tcp_flags=(TCP_SYN, TCP_SYN | TCP_ACK))
        self._flow(dp, PRIO_SYN, match, [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, 128),
                                         parser.OFPActionGroup(GROUP_ID)])

    def _match5(self, parser, f):
        return parser.OFPMatch(eth_type=0x0800, ip_proto=6, ipv4_src=f["src"],
                               ipv4_dst=f["dst"], tcp_src=f["sport"], tcp_dst=f["dport"])

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in_handler(self, ev):
        try:
            self._packet_in(ev.msg)
        except Exception:                 # os-ken swallows handler exceptions silently
            self.logger.exception("packet-in failed")

    def _packet_in(self, msg):
        dp = msg.datapath
        if dp.id not in self.leaf_of_dpid:
            return
        pkt = packet.Packet(msg.data)
        ip, tc = pkt.get_protocol(ipv4.ipv4), pkt.get_protocol(tcp.tcp)
        if ip is None or tc is None or ip.dst not in self.by_ip or ip.src not in self.by_ip:
            return
        self.n_packet_in += 1
        key = (ip.src, ip.dst, tc.src_port, tc.dst_port)
        if key in self.flows:             # a retransmitted SYN
            return
        leaf = self.leaf_of_dpid[dp.id]
        dleaf = self.by_ip[ip.dst]["leaf"]
        if dleaf == leaf:
            return
        f = dict(src=ip.src, dst=ip.dst, sport=tc.src_port, dport=tc.dst_port)
        self.flows[key] = Flow(key, f, leaf, dleaf, time.time())
        parser = dp.ofproto_parser
        self._flow(dp, PRIO_COUNT, self._match5(parser, f), [parser.OFPActionGroup(GROUP_ID)],
                   cookie=COOKIE_LEAF, idle_timeout=self.COUNT_IDLE_S)

    def set_path(self, f, spine, modify, origin="runner", extra=None):
        rep = super().set_path(f, spine, modify, origin=origin, extra=extra)
        if origin == "runner" and rep.get("ok"):
            self._track_runner_pin(f, int(spine))
        return rep

    def _track_runner_pin(self, f, spine):
        """A pin or move from the experiment runner (Chunk 5's deliberate
        moves). A flow pinned before it starts never reaches the SYN-copy
        rule (the prio-200 pin matches first), so the monitor registers it
        here, with the pin as its path; its rate then comes from the pin's
        counter like any moved flow's. Runs without runner pins (Chunk 4's
        live campaigns) never get here."""
        key = (f["src"], f["dst"], int(f["sport"]), int(f["dport"]))
        fl = self.flows.get(key)
        if fl is None:
            src, dst = self.by_ip.get(f["src"]), self.by_ip.get(f["dst"])
            if src is None or dst is None or src["leaf"] == dst["leaf"]:
                return
            fl = self.flows[key] = Flow(key, dict(src=f["src"], dst=f["dst"], sport=int(f["sport"]),
                                                  dport=int(f["dport"])),
                                        src["leaf"], dst["leaf"], time.time())
        fl.pinned, fl.path, fl.path_src = spine, spine, "pin"

    def _install_spine_rules(self, fl):
        for k, dpid in self.lay["spine_dpid"].items():
            dp = self.dps.get(dpid)
            if dp is None:
                continue
            parser = dp.ofproto_parser
            self._flow(dp, PRIO_COUNT, self._match5(parser, fl.f),
                       [parser.OFPActionOutput(fl.dleaf)], cookie=COOKIE_SPINE)
        fl.spine_rules = True

    # ---- stats collection ----------------------------------------------------
    def _request(self, dp, req):
        dp.set_xid(req)
        ev = hub.Event()
        self.waiting[req.xid] = ev
        self.stats[req.xid] = []
        dp.send_msg(req)
        return req.xid, ev

    @set_ev_cls(ofp_event.EventOFPFlowStatsReply, MAIN_DISPATCHER)
    def flow_stats_handler(self, ev):
        self._stats_part(ev.msg)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_handler(self, ev):
        self._stats_part(ev.msg)

    def _stats_part(self, msg):
        parts = self.stats.get(msg.xid)
        if parts is None:
            return
        parts.extend(msg.body)
        if not msg.flags & msg.datapath.ofproto.OFPMPF_REPLY_MORE:
            w = self.waiting.get(msg.xid)
            if w is not None:
                w.set()

    def _collect(self, timeout=0.6):
        """Flow stats of every switch and port stats of the leaves, one round."""
        reqs = []
        for dpid, dp in self.dps.items():
            parser, ofp = dp.ofproto_parser, dp.ofproto
            if dpid in self.leaf_of_dpid:
                reqs.append(("flow", dpid) + self._request(dp, parser.OFPFlowStatsRequest(dp)))
                reqs.append(("port", dpid) + self._request(
                    dp, parser.OFPPortStatsRequest(dp, 0, ofp.OFPP_ANY)))
            else:
                reqs.append(("flow", dpid) + self._request(dp, parser.OFPFlowStatsRequest(
                    dp, cookie=COOKIE_SPINE, cookie_mask=0xFFFFFFFFFFFFFFFF)))
        t_end = time.time() + timeout
        out, missing = [], 0
        for what, dpid, xid, ev in reqs:
            ok = ev.wait(timeout=max(0.0, t_end - time.time()))
            body = self.stats.pop(xid, [])
            self.waiting.pop(xid, None)
            if not ok:
                missing += 1
                continue
            out.append((what, dpid, body))
        return out, missing

    @staticmethod
    def _dur_ns(s):
        return s.duration_sec * 10 ** 9 + s.duration_nsec

    def _update(self, replies, now):
        """Rates and paths from one round of stats."""
        spine_seen = {}               # key -> {k: byte delta}
        for what, dpid, body in replies:
            if what == "port":
                leaf = self.leaf_of_dpid[dpid]
                u = self.U.setdefault(leaf, {})
                for s in body:
                    k = s.port_no
                    if not 1 <= k <= self.lay["n_spines"]:
                        continue
                    cur = (s.tx_bytes, self._dur_ns(s), now)
                    prev = self.ports.get((leaf, k))
                    if prev:
                        d_ns = cur[1] - prev[1]
                        if d_ns <= 0:                 # no port duration: wall clock
                            d_ns = (cur[2] - prev[2]) * 1e9
                        if d_ns > 0:
                            u[k] = (cur[0] - prev[0]) * 8e3 / d_ns   # Mbit/s
                    self.ports[(leaf, k)] = cur
                continue
            is_leaf = dpid in self.leaf_of_dpid
            seen = {}
            for s in body:
                if s.priority not in (PRIO_COUNT, PRIO_PIN):
                    continue
                m = s.match
                try:
                    key = (m["ipv4_src"], m["ipv4_dst"], m["tcp_src"], m["tcp_dst"])
                except KeyError:
                    continue
                fl = self.flows.get(key)
                if fl is None:
                    continue
                cur = (s.byte_count, self._dur_ns(s))
                rk = (dpid, s.priority)
                prev = fl.rules.get(rk)
                d_bytes = cur[0] - (prev[0] if prev else 0)
                d_ns = cur[1] - prev[1] if prev else cur[1]
                fl.rules[rk] = cur
                if is_leaf:
                    a = seen.setdefault(key, [0, 0])
                    a[0] += d_bytes
                    a[1] = max(a[1], d_ns)
                else:
                    spine_seen.setdefault(key, {})[self.spine_of_dpid[dpid]] = d_bytes
            for key, (d_bytes, d_ns) in seen.items():
                fl = self.flows[key]
                fl.bytes += d_bytes
                fl.rate = d_bytes * 8e3 / d_ns if d_ns > 0 else 0.0
                fl.last_seen = now
        for fl in list(self.flows.values()):
            if fl.last_seen < now:          # no leaf rule reported this round
                fl.rate = 0.0
            fl.spine_bytes = spine_seen.get(fl.key, {})
            if fl.pinned is not None:
                fl.path, fl.path_src = fl.pinned, "pin"
            elif fl.spine_bytes and max(fl.spine_bytes.values()) > 0:
                fl.path = max(sorted(fl.spine_bytes), key=lambda k: fl.spine_bytes[k])
                fl.path_src = "spine_counters"
            if fl.rate >= self.ELEPHANT_MBPS:
                if fl.elephant_since is None:
                    fl.elephant_since = now
                if not fl.spine_rules:
                    self._install_spine_rules(fl)
        # forget flows whose counting rule expired and that we never pinned
        for key in [k for k, fl in self.flows.items()
                    if fl.pinned is None and now - fl.last_seen > 3 * self.POLL_S]:
            del self.flows[key]

    # ---- policy --------------------------------------------------------------
    def _rule(self, feat):
        """(go, why) for one candidate move; feat is its pre-move feature record.
        Subclasses with another rule (controllers/costaware.py) override this."""
        gain, since = feat["gain_mbps"], feat["since_last_move_s"]
        if self.policy == "greedy":
            return gain > 0, "gain>0"
        cool = since is None or since >= self.COOLDOWN_S
        why = ("gain>margin" if gain > self.MARGIN_MBPS else "gain<=margin") + \
              ("" if cool else ",cooling")
        return gain > self.MARGIN_MBPS and cool, why

    def _decide(self, now):
        moves = []
        if self.policy == "none":
            return moves
        U = {leaf: dict(u) for leaf, u in self.U.items()}
        cands = [fl for fl in self.flows.values()
                 if fl.rate >= self.ELEPHANT_MBPS and fl.path is not None
                 and fl.leaf in U and len(U[fl.leaf]) == self.lay["n_spines"]]
        cands.sort(key=lambda fl: (-U[fl.leaf][fl.path], -fl.rate, fl.key))
        for fl in cands:
            u = U[fl.leaf]
            p = fl.path
            others = [k for k in sorted(u) if k != p]
            q = min(others, key=lambda k: (u[k], k))
            gain = u[p] - (u[q] + fl.rate)
            since = None if fl.t_last_move is None else now - fl.t_last_move
            n_p = sum(1 for g in cands if g.leaf == fl.leaf and g.path == p)
            n_q = sum(1 for g in cands if g.leaf == fl.leaf and g.path == q)
            feat = dict(round=self.round, t=now, flow=fl.f, leaf=fl.leaf, from_spine=p,
                        to_spine=q, rate_mbps=fl.rate, U_before={str(k): u[k] for k in sorted(u)},
                        U_p=u[p], U_q=u[q], gain_mbps=gain,
                        elephants_on_p=n_p, elephants_on_q=n_q, age_s=now - fl.t_syn,
                        bytes_so_far=fl.bytes, n_prev_moves=fl.n_moves,
                        since_last_move_s=since, path_src=fl.path_src,
                        delay_ms_p_config=DEFAULT_SPINE_DELAY_MS[p - 1],
                        delay_ms_q_config=DEFAULT_SPINE_DELAY_MS[q - 1])
            go, why = self._rule(feat)
            feat["why"] = why
            if not go:
                continue
            rep = self.set_path(fl.f, q, modify=fl.pinned is not None, origin="policy",
                                extra=dict(round=self.round, from_spine=p))
            feat["reply"] = rep
            moves.append(feat)
            if rep.get("ok"):
                fl.pinned, fl.path, fl.path_src = q, q, "pin"
                fl.n_moves += 1
                fl.t_last_move = now
                fl.moves.append((now, p, q))
                u[p] -= fl.rate
                u[q] += fl.rate
            self.logger.info("round %d: move %s s%d -> s%d (r=%.2f, gain=%.2f) ok=%s",
                             self.round, fl.key, p, q, fl.rate, gain, rep.get("ok"))
        return moves

    def _poll_loop(self):
        n_sw = len(self.leaf_of_dpid) + len(self.spine_of_dpid)
        while len(self.dps) < n_sw:
            hub.sleep(0.1)
        t_next = time.time() + self.POLL_S
        while True:
            hub.sleep(max(0.0, t_next - time.time()))
            t_next += self.POLL_S
            try:
                t_req = time.time()
                replies, missing = self._collect()
                now = time.time()
                self.round += 1
                self._update(replies, now)
                moves = self._decide(now)
                self._log(dict(
                    kind="round", round=self.round, t=now, t_req=t_req,
                    collect_ms=(now - t_req) * 1e3, missing_replies=missing,
                    U={str(leaf): {str(k): v for k, v in sorted(u.items())}
                       for leaf, u in self.U.items()},
                    n_tracked=len(self.flows), n_packet_in=self.n_packet_in,
                    flows=[dict(flow=fl.f, rate=fl.rate, path=fl.path, path_src=fl.path_src,
                                spine_bytes={str(k): v for k, v in fl.spine_bytes.items()},
                                bytes=fl.bytes, age_s=now - fl.t_syn, n_moves=fl.n_moves)
                           for fl in self.flows.values() if fl.rate >= self.LOG_MIN_MBPS],
                    moves=moves))
            except Exception:
                self.logger.exception("poll round %d failed", self.round)

    def handle_cmd(self, req):
        if req.get("cmd") == "policy":
            return dict(policy=self.policy, round=self.round, n_tracked=len(self.flows),
                        n_packet_in=self.n_packet_in)
        return None
