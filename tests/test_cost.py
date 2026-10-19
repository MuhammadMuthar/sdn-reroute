"""Unit tests for controllers/cost.py (no Mininet, no os-ken)."""
import math
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controllers import cost  # noqa: E402

FEAT = dict(rate_mbps=4.0, U_p=9.0, U_q=2.0, gain_mbps=3.0, elephants_on_p=2,
            elephants_on_q=1, age_s=7.5, bytes_so_far=3_000_000, n_prev_moves=0,
            since_last_move_s=None, delay_ms_p_config=10, delay_ms_q_config=1)


class TestDerive(unittest.TestCase):
    def test_derived(self):
        d = cost.derive(FEAT)
        self.assertEqual(d["delay_gap_ms"], -9.0)
        self.assertAlmostEqual(d["rate_share_q"], 4.0 / 6.0)
        self.assertAlmostEqual(d["bytes_in_flight_est"], 4e6 / 8 * 0.020)

    def test_vector_order_and_none(self):
        v = cost.vector(FEAT)
        self.assertEqual(len(v), len(cost.FEATURES))
        self.assertTrue(math.isinf(v[cost.FEATURES.index("since_last_move_s")]))
        self.assertEqual(v[cost.FEATURES.index("rate_mbps")], 4.0)


class TestFormula(unittest.TestCase):
    def test_zero_by_default(self):
        self.assertEqual(cost.FormulaCost().predict(FEAT), 0.0)

    def test_terms_and_roundtrip(self):
        f = cost.FormulaCost(coef=dict(const=100.0, rate=10.0))
        self.assertAlmostEqual(f.predict(FEAT), 140.0)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "formula.json")
            f.save(p, fit=dict(n=3))
            g = cost.FormulaCost(path=p)
            self.assertEqual(g.coef, f.coef)
            self.assertEqual(g.fit_info, dict(n=3))

    def test_never_negative(self):
        self.assertEqual(cost.FormulaCost(coef=dict(const=-5.0)).predict(FEAT), 0.0)


class TestDecide(unittest.TestCase):
    def test_zero_cost_is_greedy_plus_cooldown(self):
        go, why, d = cost.decide_move(FEAT, cost.ZeroCost())
        self.assertTrue(go)
        self.assertEqual(d["gain_bytes"], 3.0 * 1e6 / 8 * cost.HORIZON_S)
        self.assertEqual(d["cost_bytes"], 0.0)
        cooling = dict(FEAT, since_last_move_s=1.0)
        self.assertFalse(cost.decide_move(cooling, cost.ZeroCost())[0])
        self.assertIn("cooling", cost.decide_move(cooling, cost.ZeroCost())[1])

    def test_cost_blocks_move(self):
        big = cost.FormulaCost(coef=dict(const=1e9))
        go, why, d = cost.decide_move(FEAT, big)
        self.assertFalse(go)
        self.assertEqual(why, "gain<=cost")
        self.assertGreaterEqual(d["decision_ms"], 0.0)

    def test_gain_clipped_to_link(self):
        f = dict(FEAT, rate_mbps=9.0, gain_mbps=5.0)
        go, why, d = cost.decide_move(f, cost.ZeroCost())
        self.assertAlmostEqual(d["gain_bytes"], cost.mbps_to_bytes(1.0))
        self.assertFalse(cost.decide_move(dict(FEAT, gain_mbps=-1.0), cost.ZeroCost())[0])


if __name__ == "__main__":
    unittest.main()
