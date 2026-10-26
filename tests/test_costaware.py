"""The controllers' decision step without Mininet or a switch: a HederaController
(and its subclasses) built without __init__, with stubbed set_path, fed one
round of loads and flows. Needs os-ken importable (the project venv)."""
import json
import logging
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controllers import cost  # noqa: E402
from controllers.costaware import CostAwareController, make_estimator  # noqa: E402
from controllers.hedera import Flow, HederaController  # noqa: E402

NOW = 1000.0


def ctl(cls, policy, estimator=None):
    c = cls.__new__(cls)
    c.policy, c.round, c.plog = policy, 7, None
    c.lay = dict(n_spines=4)
    c.logger = logging.getLogger("test")
    c.calls = []
    c.held = []
    if estimator is not None:
        c.estimator = estimator
        c.COOLDOWN_S = cost.T_COOL_S

    def set_path(f, q, modify, origin, extra):
        c.calls.append((f["sport"], q))
        return dict(ok=True)
    c.set_path = set_path
    # leaf 1: spine 1 carries two elephants (8 Mbit/s), spine 2 idle
    c.U = {1: {1: 8.0, 2: 0.2, 3: 6.0, 4: 6.0}}
    c.flows = {}
    for sport, rate in ((40001, 4.0), (40002, 4.0)):
        f = dict(src="10.0.0.1", dst="10.0.0.5", sport=sport, dport=5201)
        fl = Flow(sport, f, 1, 2, NOW - 10)
        fl.rate, fl.path, fl.path_src, fl.bytes = rate, 1, "spine_counters", 5_000_000
        c.flows[sport] = fl
    return c


class TestDecide(unittest.TestCase):
    def test_greedy_unchanged(self):
        # first flow: gain = 8 - (0.2 + 4) = 3.8 > 0 -> spine 2; then U[1]=4, U[2]=4.2,
        # second flow: q = spine 2 (4.2 < 6), gain = 4 - 8.2 < 0 -> stays
        c = ctl(HederaController, "greedy")
        moves = c._decide(NOW)
        self.assertEqual(c.calls, [(40001, 2)])
        self.assertEqual(moves[0]["why"], "gain>0")
        self.assertAlmostEqual(moves[0]["gain_mbps"], 3.8)

    def test_cooldown_unchanged(self):
        c = ctl(HederaController, "cooldown")
        c.COOLDOWN_S, c.MARGIN_MBPS = 5.0, 1.0
        c.flows[40001].t_last_move = NOW - 2
        c._decide(NOW)
        # flow 1 is cooling; flow 2 moves instead (same gain, eligible)
        self.assertEqual(c.calls, [(40002, 2)])

    def test_costaware_zero_is_greedy_plus_cooldown(self):
        c = ctl(CostAwareController, "costaware", cost.ZeroCost())
        moves = c._decide(NOW)
        self.assertEqual(c.calls, [(40001, 2)])
        m = moves[0]
        self.assertAlmostEqual(m["gain_bytes"], cost.mbps_to_bytes(3.8))
        self.assertEqual(m["cost_bytes"], 0.0)
        self.assertEqual(m["estimator"], "zero")
        self.assertIn("decision_ms", m)

    def test_costaware_holds_costly_move(self):
        # cost above the 3.8 Mbit/s x 10 s gain: nothing moves, the candidate is held
        est = cost.FormulaCost(coef=dict(const=cost.mbps_to_bytes(5.0)))
        with tempfile.TemporaryDirectory() as d:
            c = ctl(CostAwareController, "costaware", est)
            c.plog = open(os.path.join(d, "p.jsonl"), "w")
            self.assertEqual(c._decide(NOW), [])
            c.plog.close()
            self.assertEqual(c.calls, [])
            recs = [json.loads(x) for x in open(os.path.join(d, "p.jsonl"))]
        self.assertEqual(recs[0]["kind"], "held")
        self.assertEqual(recs[0]["held"][0]["why"], "gain<=cost")

    def test_missing_model_fails_loudly(self):
        with self.assertRaises(FileNotFoundError):
            make_estimator("model", "/nonexistent/damage_model.joblib")
        self.assertEqual(make_estimator("zero").name, "zero")


if __name__ == "__main__":
    unittest.main()
