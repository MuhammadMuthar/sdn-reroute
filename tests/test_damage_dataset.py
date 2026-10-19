import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from analysis import damage_dataset as dd  # noqa: E402
from tests import fixture  # noqa: E402


class TestBuild(unittest.TestCase):
    def test_fixture_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            live, replay = fixture.make(tmp)
            df = dd.build(live, replay)
            self.assertEqual(len(df), 1)
            r = df.iloc[0]
            self.assertEqual(r.flow, "E1")
            self.assertEqual(r.replay_ok, 1)
            self.assertEqual(r.n_bystanders, 1)
            self.assertEqual(r.delay_gap_ms, -9.0)
            self.assertEqual(r.cwnd_before, 30.0)
            self.assertEqual(r.unacked_before, 25)
            self.assertAlmostEqual(r.cwnd_asymmetry, 3.0)
            # known synthetic damage: moved flow loses 1 Mbit/s, bystander gains 0.5
            for w in dd.WINDOWS:
                self.assertAlmostEqual(r["moved_deficit_W%g" % w], 1e6 / 8 * w, delta=0.03e6 * w)
                self.assertAlmostEqual(r["bystander_deficit_W%g" % w], -0.5e6 / 8 * w,
                                       delta=0.03e6 * w)
                self.assertAlmostEqual(r["dest_total_deficit_W%g" % w],
                                       r["moved_deficit_W%g" % w] + r["bystander_deficit_W%g" % w])
                self.assertEqual(r["d%g_TcpRetransSegs" % w], 5)
            corr = dd.correlations(df)
            self.assertIn("rate_mbps", set(corr.feature))
            dd.write_dictionary(os.path.join(tmp, "dict.md"), df)
            self.assertTrue(os.path.exists(os.path.join(tmp, "dict.md")))


if __name__ == "__main__":
    unittest.main()
