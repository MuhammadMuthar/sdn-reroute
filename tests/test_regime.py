import os
import sys
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis import regime  # noqa: E402


class TestVerdict(unittest.TestCase):
    def test_rule(self):
        # 100 Mbit/s link: floor 1 Mbit/s
        self.assertEqual(regime.verdict(1.5, 0.4, 100), "DAMAGE")
        self.assertEqual(regime.verdict(0.5, 0.4, 100), "CI > 0, below 1 %")
        self.assertEqual(regime.verdict(1.5, 1.6, 100), "no damage")
        self.assertEqual(regime.verdict(-0.2, 0.1, 100), "no damage")
        # 10 Mbit/s link: floor 0.1 Mbit/s
        self.assertEqual(regime.verdict(0.1, 0.05, 10), "DAMAGE")
        self.assertEqual(regime.verdict(np.nan, 0.1, 10), "n/a")
        self.assertEqual(regime.verdict(0.3, np.nan, 10), "n/a")

    def test_sysctl_ok(self):
        want = {regime.CC_KEY: "bbr", regime.RACK_KEY: "0"}
        good = {"h%d" % i: {regime.CC_KEY: "bbr", regime.RACK_KEY: "0"} for i in range(1, 9)}
        self.assertTrue(regime.sysctl_ok(dict(sysctl_set=want, sysctl_hosts=good)))
        bad = dict(good, h5={regime.CC_KEY: "cubic", regime.RACK_KEY: "0"})
        self.assertFalse(regime.sysctl_ok(dict(sysctl_set=want, sysctl_hosts=bad)))
        self.assertFalse(regime.sysctl_ok(dict(sysctl_hosts=good)))

    def test_cells(self):
        # five trials losing exactly 2 Mbit/s over W10 on a 100 Mbit/s link, one excluded
        w10 = 2e6 / 8 * 10
        rows = []
        for k in range(6):
            rows.append(dict(rate=100.0, cc="cubic", rack=1, delay_set="D1", delays="1/1/5/10",
                             delay_gap_design_ms=9.0, n_q_design=0, trial=k,
                             replay_ok=int(k < 5), dest_total_deficit_W10=w10 + (k - 2) * 1e4,
                             dest_total_deficit_W2=0.0, moved_deficit_W10=w10,
                             moved_deficit_W2=0.0, ss_reord_seen_W2_T=k % 2,
                             ss_reord_seen_W2_C=0, ss_retrans_W2_T=3, ss_retrans_W2_C=1,
                             ss_dsack_dups_W2_T=0, ss_dsack_dups_W2_C=0))
        c = regime.cells(pd.DataFrame(rows))
        self.assertEqual(len(c), 1)
        r = c.iloc[0]
        self.assertEqual((r.n, r.excluded, r.direction), (5, 1, "fast->slow"))
        self.assertAlmostEqual(r.dest_total_W10_mbps, 2.0)
        self.assertAlmostEqual(r.dest_total_W10_pct, 2.0)
        self.assertAlmostEqual(r.dest_total_W10_bytes, w10)
        self.assertEqual(r.reorder_trials_T, 2)
        self.assertEqual(r.verdict, "DAMAGE")


if __name__ == "__main__":
    unittest.main()
