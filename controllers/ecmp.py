"""ECMP baseline (Chunk 3): OpenFlow 1.3 select groups on the leaves.

Each leaf gets one select group (id 1) with one bucket per spine (equal
weights, output to uplink port k); traffic entering a leaf towards a remote
host goes to that group. Spines and the egress leaf forward by destination
(controllers/fabric.py), so the spine a flow uses is decided once, by the
ingress leaf's hash, and never changes.

Which hash: OF1.3 has no way to name a selection method, so OVS uses its
default. Open vSwitch 2.10+ (we run 3.7.1) defaults to `dp_hash` with the
symmetric L3/L4 hash computed in the kernel datapath (ovs-ofctl(8),
selection_method). Consequences, checked in Chunk 3 (LOG.md):
  - per 5-tuple, so a fixed iperf3 --cport and fixed mouse source ports give
    a fixed spine within one boot; the kernel seeds this hash randomly at
    boot, so the same 5-tuple can map to a different spine after a reboot.
    The runner therefore records the spine each flow actually used (spine
    captures) instead of predicting it.
  - symmetric: a flow's ACKs (hashed by l2's identical group) take the same
    spine as its data.
No per-flow upcall to the controller: mice never reach it.

The command channel's pin/move (fabric.py) still works on top of ECMP: a
prio-200 5-tuple rule overrides the group for one flow. The runner uses that
for deliberate moves (the loaded-move scenario); pure ECMP runs pin nothing.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controllers.fabric import FabricController  # noqa: E402

GROUP_ID = 1


class ECMPController(FabricController):
    APP_NAME = "ecmp"

    def leaf_setup(self, dp, leaf):
        ofp, parser = dp.ofproto, dp.ofproto_parser
        buckets = [parser.OFPBucket(weight=1, watch_port=ofp.OFPP_ANY,
                                    watch_group=ofp.OFPG_ANY,
                                    actions=[parser.OFPActionOutput(k)])
                   for k in range(1, self.lay["n_spines"] + 1)]
        dp.send_msg(parser.OFPGroupMod(dp, ofp.OFPGC_ADD, ofp.OFPGT_SELECT, GROUP_ID,
                                       buckets))

    def remote_actions(self, dp, leaf, host):
        return [dp.ofproto_parser.OFPActionGroup(GROUP_ID)]
