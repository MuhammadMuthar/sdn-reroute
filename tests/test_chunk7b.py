import math
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from controllers import cost  # noqa: E402
from experiments.traffic import make_schedule, resolve  # noqa: E402


def feat(**kw):
    f = dict(rate_mbps=40.0, U_p=95.0, U_q=0.0, gain_mbps=55.0, elephants_on_p=2,
             elephants_on_q=0, age_s=10.0, bytes_so_far=1e7, n_prev_moves=0,
             since_last_move_s=None, delay_ms_p_config=1.0, delay_ms_q_config=50.0,
             link_mbps=100.0)
    f.update(kw)
    return f


class TestNewFeatures(unittest.TestCase):
    def test_derive(self):
        d = cost.derive(feat())
        self.assertAlmostEqual(d["delay_ratio_qp"], 50.0)
        self.assertAlmostEqual(d["window_ratio_q"], 40.0 * 1.0 / (100.0 * 50.0))
        self.assertEqual(d["q_empty"], 1.0)
        self.assertEqual(cost.derive(feat(elephants_on_q=1))["q_empty"], 0.0)

    def test_old_records_default_link(self):
        f = feat()
        del f["link_mbps"]
        self.assertEqual(cost.link_of(f), cost.LINK_MBPS)
        self.assertEqual(cost.link_of(feat(link_mbps=float("nan"))), cost.LINK_MBPS)
        self.assertEqual(len(cost.vector(f)), len(cost.FEATURES))

    def test_ramp_term(self):
        # empty q: link minus the rate the current window gives on q
        t = cost.FormulaCost.terms(feat())
        self.assertAlmostEqual(t["ramp"], 100.0 - 40.0 * 1.0 / 50.0)
        self.assertEqual(cost.FormulaCost.terms(feat(elephants_on_q=1))["ramp"], 0.0)
        # moving to a shorter path: the window is more than enough, ramp clipped at 0
        t2 = cost.FormulaCost.terms(feat(delay_ms_p_config=50.0, delay_ms_q_config=1.0))
        self.assertEqual(t2["ramp"], 0.0)
        est = cost.FormulaCost(coef=dict(ramp=1e5))
        self.assertAlmostEqual(est.predict(feat()), 1e5 * t["ramp"])

    def test_old_formula_has_no_ramp(self):
        est = cost.FormulaCost(coef=dict(const=5.0))     # a Chunk 6 formula.json
        self.assertEqual(est.coef["ramp"], 0.0)
        self.assertEqual(est.predict(feat()), 5.0)

    def test_gain_uses_link(self):
        go, why, det = cost.decide_move(feat(), cost.ZeroCost(), link_mbps=100.0)
        self.assertTrue(go)
        self.assertAlmostEqual(det["gain_bytes"], cost.mbps_to_bytes(55.0))


class TestStagger(unittest.TestCase):
    def test_schedule(self):
        s = make_schedule(resolve("longpath_stagger"), 3)
        el = s["elephants"]
        self.assertEqual(len(el), 8)
        self.assertEqual(len({e["src"] for e in el}), 8)
        self.assertEqual(len({e["dst"] for e in el}), 8)
        for e in el:
            self.assertTrue(0 <= e["start"] <= 20 and 10 <= e["duration"] <= 20)
            self.assertLessEqual(e["start"] + e["duration"], s["duration"])
        self.assertEqual(s, make_schedule(resolve("longpath_stagger"), 3))


if __name__ == "__main__":
    unittest.main()
